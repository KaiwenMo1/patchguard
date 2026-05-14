"""Evidence-grounded AI review summaries for PatchGuard reports."""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from typing import Any, Protocol

import requests

from patchguard.models import (
    EvidenceBasedReview,
    EvidenceRisk,
    PatchGuardReport,
    RiskReport,
    RunStatus,
    ToolRun,
)
from patchguard.services.test_generation_service import (
    DEFAULT_OPENAI_BASE_URL,
    DEFAULT_OPENAI_MODEL,
)
from patchguard.utils.command_runner import CommandRunner


class AIReviewProvider(Protocol):
    provider_name: str
    model: str

    def generate_review(self, prompt: str) -> str:
        """Return raw JSON text describing an evidence-based PR review."""


@dataclass(frozen=True)
class AIReviewResult:
    review: EvidenceBasedReview
    tool_run: ToolRun


class OpenAIReviewProvider:
    """Minimal OpenAI Responses API client for evidence-based review text."""

    provider_name = "openai"

    def __init__(
        self,
        *,
        api_key: str,
        model: str = DEFAULT_OPENAI_MODEL,
        base_url: str = DEFAULT_OPENAI_BASE_URL,
        timeout_seconds: int = 60,
    ) -> None:
        self.api_key = api_key
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds

    def generate_review(self, prompt: str) -> str:
        response = requests.post(
            f"{self.base_url}/responses",
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": self.model,
                "instructions": (
                    "You write concise pull request reviews using only evidence supplied by "
                    "PatchGuard. Return only valid JSON with the requested schema."
                ),
                "input": prompt,
                "temperature": 0,
                "max_output_tokens": 2200,
            },
            timeout=self.timeout_seconds,
        )
        response.raise_for_status()
        return self._extract_text(response.json())

    @staticmethod
    def _extract_text(payload: dict[str, Any]) -> str:
        output_text = payload.get("output_text")
        if isinstance(output_text, str):
            return output_text

        parts: list[str] = []
        for item in payload.get("output", []):
            if not isinstance(item, dict):
                continue
            for content in item.get("content", []):
                if not isinstance(content, dict):
                    continue
                text = content.get("text")
                if isinstance(text, str):
                    parts.append(text)
        return "\n".join(parts)


class AIReviewService:
    """Create optional AI summaries grounded only in collected PatchGuard evidence."""

    def __init__(
        self,
        *,
        api_key: str | None = None,
        model: str | None = None,
        base_url: str | None = None,
        provider: AIReviewProvider | None = None,
        enabled: bool = True,
    ) -> None:
        self.api_key = api_key if api_key is not None else os.getenv("OPENAI_API_KEY")
        self.model = model or os.getenv("OPENAI_MODEL", DEFAULT_OPENAI_MODEL)
        self.base_url = base_url or os.getenv("OPENAI_BASE_URL", DEFAULT_OPENAI_BASE_URL)
        self.provider = provider
        self.enabled = enabled

    def review(self, report: RiskReport | PatchGuardReport) -> AIReviewResult:
        if not self.enabled:
            return self._skipped(report, "Evidence-based AI review disabled by --skip-llm")
        if not self.api_key:
            return self._skipped(report, "OPENAI_API_KEY is not set; evidence-based AI review skipped")

        provider = self.provider or OpenAIReviewProvider(
            api_key=self.api_key,
            model=self.model,
            base_url=self.base_url,
        )
        prompt = self._prompt(report)
        try:
            raw = provider.generate_review(prompt)
            review = self._parse_review(raw)
        except Exception as exc:  # noqa: BLE001 - review failures are report evidence.
            reason = f"Evidence-based AI review failed: {exc}"
            return AIReviewResult(
                review=self._fallback_review(report, reason),
                tool_run=ToolRun(
                    name="generate evidence-based AI review",
                    kind="ai_review",
                    status=RunStatus.ERROR,
                    summary=reason,
                ),
            )

        return AIReviewResult(
            review=review,
            tool_run=ToolRun(
                name="generate evidence-based AI review",
                kind="ai_review",
                status=RunStatus.PASSED,
                summary="Generated evidence-based AI review from collected PatchGuard evidence",
                findings_count=len(review.top_risks),
            ),
        )

    @staticmethod
    def _prompt(report: RiskReport | PatchGuardReport) -> str:
        payload = AIReviewService._evidence_payload(report)
        return f"""Write an evidence-based PR review from this PatchGuard report.

Return only JSON with exactly these keys:
{{
  "merge_recommendation": "merge | merge_with_caution | do_not_merge | needs_human_review",
  "executive_summary": "...",
  "pr_change_summary": ["..."],
  "correctness_notes": ["..."],
  "efficiency_notes": ["..."],
  "top_risks": [
    {{
      "title": "...",
      "severity": "low | medium | high | critical",
      "evidence": ["..."],
      "files": ["..."],
      "suggested_fix": "..."
    }}
  ],
  "files_to_review_first": ["..."],
  "suggested_followup_tests": ["..."],
  "suggested_fixes": ["..."],
  "limitations": ["..."]
}}

Rules:
- Use only the evidence in the JSON payload below.
- Do not invent test failures, vulnerabilities, timings, benchmark results, or scanner findings.
- Do not claim the PR is correct. Say what evidence passed, failed, or is missing.
- For efficiency notes, only mention concrete diff evidence or say no performance evidence was collected.
- Every top risk must include at least one evidence string from the payload.
- If Docker, generated tests, OpenAI, or scans were skipped, explain the limitation.
- Keep the summary practical and concise.

PatchGuard evidence payload:
{json.dumps(payload, indent=2)}
"""

    @staticmethod
    def _evidence_payload(report: RiskReport | PatchGuardReport) -> dict[str, Any]:
        pr = report.pr
        return {
            "status": report.status,
            "errors": report.errors[:8],
            "pr": {
                "title": getattr(pr, "title", None),
                "repository": (
                    f"{getattr(pr, 'owner', 'unknown')}/{getattr(pr, 'repo', 'unknown')}"
                    if pr is not None
                    else "unknown"
                ),
                "number": getattr(pr, "number", None),
                "url": getattr(pr, "url", None) or getattr(pr, "html_url", None),
                "additions": getattr(pr, "additions", 0),
                "deletions": getattr(pr, "deletions", 0),
                "changed_files": getattr(pr, "changed_files_count", len(report.changed_files)),
            },
            "changed_files": [
                {
                    "filename": file.filename,
                    "status": file.status,
                    "classification": file.classification,
                    "additions": file.additions,
                    "deletions": file.deletions,
                    "patch": AIReviewService._limit(file.patch or "", 1600),
                }
                for file in report.changed_files[:12]
            ],
            "changed_functions": [
                {
                    "file_path": function.file_path,
                    "qualified_name": function.qualified_name,
                    "symbol_type": function.symbol_type,
                    "changed_lines": function.changed_lines,
                    "source_excerpt": AIReviewService._limit(function.source_code, 900),
                }
                for function in report.changed_functions[:12]
            ],
            "risk": {
                "score": report.risk_score,
                "level": AIReviewService._value(report.risk_level),
                "breakdown": (
                    report.risk_breakdown.model_dump(mode="json")
                    if report.risk_breakdown
                    else None
                ),
                "reasons": [
                    reason.model_dump(mode="json")
                    for reason in report.risk_reasons[:10]
                ],
                "merge_decision": AIReviewService._value(report.merge_decision),
                "recommendation": AIReviewService._value(report.recommendation),
            },
            "policy": report.policy_decision.model_dump(mode="json"),
            "behavioral_contract": report.behavioral_contract.model_dump(mode="json"),
            "test_runs": AIReviewService._test_runs(report),
            "failure_mappings": [
                mapping.model_dump(mode="json") for mapping in report.failure_mappings[:8]
            ],
            "security_findings": [
                {
                    "tool": finding.tool,
                    "severity": finding.severity,
                    "confidence": finding.confidence,
                    "filename": finding.filename or finding.file,
                    "line": finding.line_number or finding.line,
                    "message": finding.message or finding.issue_text,
                    "issue_code": finding.issue_code,
                }
                for finding in report.security_findings[:10]
            ],
            "static_findings": [
                finding.model_dump(mode="json") for finding in report.static_findings[:10]
            ],
        }

    @staticmethod
    def _test_runs(report: RiskReport | PatchGuardReport) -> list[dict[str, Any]]:
        if isinstance(report, RiskReport):
            existing_runs = [report.existing_tests] if report.existing_tests else []
        else:
            existing_runs = report.existing_test_results
        runs = [
            *existing_runs,
            report.contract_extraction,
            report.test_generation,
            *report.generated_test_results,
            *report.static_analysis_results,
        ]
        return [
            {
                "name": run.name,
                "kind": run.kind,
                "status": AIReviewService._value(run.status),
                "summary": run.summary,
                "stdout_tail": AIReviewService._limit(
                    run.command.stdout_tail if run.command else "",
                    900,
                ),
                "stderr_tail": AIReviewService._limit(
                    run.command.stderr_tail if run.command else "",
                    900,
                ),
            }
            for run in runs
            if run is not None
        ]

    @staticmethod
    def _parse_review(raw_text: str) -> EvidenceBasedReview:
        text = AIReviewService._strip_markdown_fences(raw_text).strip()
        if not text:
            raise ValueError("LLM returned empty review JSON")
        data = json.loads(text)
        if not isinstance(data, dict):
            raise ValueError("Review response must be a JSON object")
        normalized = {
            "merge_recommendation": AIReviewService._merge_recommendation(
                data.get("merge_recommendation")
            ),
            "executive_summary": str(data.get("executive_summary") or "").strip(),
            "pr_change_summary": AIReviewService._string_list(data.get("pr_change_summary")),
            "correctness_notes": AIReviewService._string_list(data.get("correctness_notes")),
            "efficiency_notes": AIReviewService._string_list(data.get("efficiency_notes")),
            "top_risks": AIReviewService._risks(data.get("top_risks")),
            "files_to_review_first": AIReviewService._string_list(
                data.get("files_to_review_first")
            ),
            "suggested_followup_tests": AIReviewService._string_list(
                data.get("suggested_followup_tests")
            ),
            "suggested_fixes": AIReviewService._string_list(data.get("suggested_fixes")),
            "limitations": AIReviewService._string_list(data.get("limitations")),
        }
        return EvidenceBasedReview.model_validate(normalized)

    @staticmethod
    def _risks(value: Any) -> list[EvidenceRisk]:
        if not isinstance(value, list):
            return []
        risks: list[EvidenceRisk] = []
        for item in value[:6]:
            if not isinstance(item, dict):
                continue
            evidence = AIReviewService._string_list(item.get("evidence"))
            if not evidence:
                evidence = ["PatchGuard evidence was referenced but not specified by the AI response."]
            risks.append(
                EvidenceRisk(
                    title=str(item.get("title") or "Evidence-backed risk").strip(),
                    severity=AIReviewService._severity(item.get("severity")),
                    evidence=evidence,
                    files=AIReviewService._string_list(item.get("files")),
                    suggested_fix=str(item.get("suggested_fix") or "").strip(),
                )
            )
        return risks

    @staticmethod
    def _merge_recommendation(value: Any) -> str:
        normalized = str(value or "").strip().lower()
        if normalized in {"merge", "merge_with_caution", "do_not_merge", "needs_human_review"}:
            return normalized
        return "needs_human_review"

    @staticmethod
    def _severity(value: Any) -> str:
        normalized = str(value or "").strip().lower()
        if normalized in {"low", "medium", "high", "critical"}:
            return normalized
        return "medium"

    @staticmethod
    def _string_list(value: Any) -> list[str]:
        if not isinstance(value, list):
            return []
        return [str(item).strip() for item in value if str(item).strip()][:10]

    @staticmethod
    def _strip_markdown_fences(raw_text: str) -> str:
        text = raw_text.strip()
        match = re.fullmatch(r"```(?:json)?\s*(.*?)```", text, flags=re.DOTALL | re.IGNORECASE)
        if match:
            return match.group(1).strip()
        return text

    @staticmethod
    def _fallback_review(
        report: RiskReport | PatchGuardReport,
        reason: str,
    ) -> EvidenceBasedReview:
        return EvidenceBasedReview(
            merge_recommendation=AIReviewService._decision_to_review_recommendation(report),
            executive_summary=reason,
            pr_change_summary=AIReviewService._fallback_change_summary(report),
            correctness_notes=[
                "No AI review was generated; rely on the structured test, scan, policy, and risk evidence.",
            ],
            efficiency_notes=["No performance or efficiency evidence was generated by PatchGuard."],
            limitations=[reason],
        )

    @staticmethod
    def _fallback_change_summary(report: RiskReport | PatchGuardReport) -> list[str]:
        changed = ", ".join(file.filename for file in report.changed_files[:5])
        if not changed:
            return ["No changed files were available in the report."]
        return [f"PatchGuard analyzed changed files: {changed}."]

    @staticmethod
    def _decision_to_review_recommendation(report: RiskReport | PatchGuardReport) -> str:
        decision = AIReviewService._value(report.merge_decision)
        if decision == "merge":
            return "merge"
        if decision == "merge_with_caution":
            return "merge_with_caution"
        if decision == "do_not_merge":
            return "do_not_merge"
        return "needs_human_review"

    @staticmethod
    def _skipped(report: RiskReport | PatchGuardReport, reason: str) -> AIReviewResult:
        runner = CommandRunner()
        return AIReviewResult(
            review=AIReviewService._fallback_review(report, reason),
            tool_run=ToolRun(
                name="generate evidence-based AI review",
                kind="ai_review",
                status=RunStatus.SKIPPED,
                summary=reason,
                command=runner.skipped(["openai", "responses", "create"], reason),
            ),
        )

    @staticmethod
    def _limit(value: str, max_chars: int) -> str:
        if len(value) <= max_chars:
            return value
        return value[:max_chars].rstrip() + "\n... [truncated]"

    @staticmethod
    def _value(value: Any) -> str:
        return str(getattr(value, "value", value))
