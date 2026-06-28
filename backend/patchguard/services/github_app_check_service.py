"""Publish PatchGuard results as GitHub Check Runs."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal

import requests

from patchguard.app_models import GitHubAppAnalysisJob
from patchguard.models import (
    MergeDecision,
    PatchGuardReport,
    PolicyGateDecision,
    RiskReport,
    SecurityFinding,
    ToolRun,
)
from patchguard.services.github_app_auth_service import GitHubAppAuthService

GITHUB_API_BASE_URL = "https://api.github.com"
CHECK_NAME = "PatchGuard"
MAX_CHECK_TEXT_CHARS = 6000

CheckConclusion = Literal["success", "neutral", "failure"]


@dataclass(frozen=True)
class GitHubCheckRunResult:
    id: int
    html_url: str | None = None
    details_url: str | None = None


class GitHubCheckRunPublishError(RuntimeError):
    """Raised when GitHub rejects a Check Run request."""


class GitHubAppCheckService:
    """Create and update GitHub Check Runs for PatchGuard analysis jobs."""

    def __init__(
        self,
        *,
        token: str | None = None,
        auth_service: GitHubAppAuthService | None = None,
        api_base_url: str = GITHUB_API_BASE_URL,
        timeout_seconds: int = 20,
        now: Callable[[], datetime] | None = None,
        details_url: str | None = None,
    ) -> None:
        self.token = token
        self.auth_service = auth_service
        self.api_base_url = api_base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self.now = now or (lambda: datetime.now(UTC))
        self.details_url = details_url

    def create_in_progress(
        self,
        job: GitHubAppAnalysisJob,
        *,
        github_installation_id: int,
    ) -> GitHubCheckRunResult:
        owner, repo = split_repo_full_name(job.repository_full_name)
        if not job.head_sha:
            raise GitHubCheckRunPublishError(f"Job {job.id} is missing head_sha.")
        payload = create_in_progress_payload(
            job,
            started_at=self.now(),
            details_url=self._details_url_for_job(job),
        )
        response = requests.post(
            f"{self.api_base_url}/repos/{owner}/{repo}/check-runs",
            headers=self._headers(github_installation_id),
            json=payload,
            timeout=self.timeout_seconds,
        )
        return check_run_result_from_response(response, action="create")

    def update_from_report(
        self,
        job: GitHubAppAnalysisJob,
        report: RiskReport | PatchGuardReport,
        *,
        github_installation_id: int,
        check_run_id: int | None = None,
    ) -> GitHubCheckRunResult:
        owner, repo = split_repo_full_name(job.repository_full_name)
        resolved_check_run_id = check_run_id or job.check_run_id
        if resolved_check_run_id is None:
            raise GitHubCheckRunPublishError(f"Job {job.id} is missing check_run_id.")
        payload = completed_payload_from_report(
            job,
            report,
            completed_at=self.now(),
            details_url=self._details_url_for_job(job),
        )
        response = requests.patch(
            f"{self.api_base_url}/repos/{owner}/{repo}/check-runs/{resolved_check_run_id}",
            headers=self._headers(github_installation_id),
            json=payload,
            timeout=self.timeout_seconds,
        )
        return check_run_result_from_response(response, action="update")

    def update_for_failure(
        self,
        job: GitHubAppAnalysisJob,
        *,
        github_installation_id: int,
        error: str,
        check_run_id: int | None = None,
    ) -> GitHubCheckRunResult:
        owner, repo = split_repo_full_name(job.repository_full_name)
        resolved_check_run_id = check_run_id or job.check_run_id
        if resolved_check_run_id is None:
            raise GitHubCheckRunPublishError(f"Job {job.id} is missing check_run_id.")
        payload = completed_payload_for_error(
            job,
            error=error,
            completed_at=self.now(),
            details_url=self._details_url_for_job(job),
        )
        response = requests.patch(
            f"{self.api_base_url}/repos/{owner}/{repo}/check-runs/{resolved_check_run_id}",
            headers=self._headers(github_installation_id),
            json=payload,
            timeout=self.timeout_seconds,
        )
        return check_run_result_from_response(response, action="update")

    def _headers(self, github_installation_id: int) -> dict[str, str]:
        return {
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {self._token_for_installation(github_installation_id)}",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "PatchGuard-GitHub-App",
        }

    def _token_for_installation(self, github_installation_id: int) -> str:
        if self.token:
            return self.token
        auth_service = self.auth_service or GitHubAppAuthService()
        return auth_service.fetch_installation_token(github_installation_id).token

    def _details_url_for_job(self, job: GitHubAppAnalysisJob) -> str | None:
        if not self.details_url:
            return None
        owner, repo = split_repo_full_name(job.repository_full_name)
        return self.details_url.format(
            job_id=job.id or "",
            owner=owner,
            repo=repo,
            pr_number=job.pr_number or "",
        )


def create_in_progress_payload(
    job: GitHubAppAnalysisJob,
    *,
    started_at: datetime,
    details_url: str | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "name": CHECK_NAME,
        "head_sha": job.head_sha,
        "status": "in_progress",
        "started_at": github_timestamp(started_at),
        "external_id": external_id_for_job(job),
        "output": {
            "title": "PatchGuard analysis running",
            "summary": "PatchGuard is generating an evidence-backed merge-risk report.",
        },
    }
    if details_url:
        payload["details_url"] = details_url
    return payload


def completed_payload_from_report(
    job: GitHubAppAnalysisJob,
    report: RiskReport | PatchGuardReport,
    *,
    completed_at: datetime,
    details_url: str | None = None,
) -> dict[str, Any]:
    conclusion = conclusion_for_report(report)
    output = render_check_output(job, report)
    payload: dict[str, Any] = {
        "name": CHECK_NAME,
        "status": "completed",
        "conclusion": conclusion,
        "completed_at": github_timestamp(completed_at),
        "external_id": external_id_for_job(job),
        "output": {
            "title": output.title,
            "summary": output.summary,
            "text": output.text,
        },
    }
    if details_url:
        payload["details_url"] = details_url
    return payload


def completed_payload_for_error(
    job: GitHubAppAnalysisJob,
    *,
    error: str,
    completed_at: datetime,
    details_url: str | None = None,
) -> dict[str, Any]:
    safe_error = trim_text(error, 1000)
    payload: dict[str, Any] = {
        "name": CHECK_NAME,
        "status": "completed",
        "conclusion": "failure",
        "completed_at": github_timestamp(completed_at),
        "external_id": external_id_for_job(job),
        "output": {
            "title": "PatchGuard analysis failed",
            "summary": "PatchGuard could not complete analysis for this PR.",
            "text": f"Worker error:\n\n```text\n{safe_error}\n```",
        },
    }
    if details_url:
        payload["details_url"] = details_url
    return payload


@dataclass(frozen=True)
class CheckOutput:
    title: str
    summary: str
    text: str


def render_check_output(
    job: GitHubAppAnalysisJob,
    report: RiskReport | PatchGuardReport,
) -> CheckOutput:
    title = f"PatchGuard: {report.risk_score}/100 {value(report.risk_level)} risk"
    summary = (
        f"Decision: {value(report.merge_decision)}. "
        f"Policy: {value(report.policy_decision.decision)}. "
        f"Recommendation: {value(report.recommendation)}"
    )
    lines = [
        "## PatchGuard Evidence",
        "",
        f"- **Repository:** `{job.repository_full_name}`",
        f"- **PR:** `{job.pr_number or 'unknown'}`",
        f"- **Risk:** `{report.risk_score}/100` (`{value(report.risk_level)}`)",
        f"- **Decision:** `{value(report.merge_decision)}`",
        f"- **Policy:** `{value(report.policy_decision.decision)}`",
        f"- **Recommendation:** {value(report.recommendation)}",
        "",
        "### Test And Scan Evidence",
        f"- Existing tests: {run_group_summary(existing_runs(report))}",
        f"- Generated tests: {run_group_summary(report.generated_test_results)}",
        f"- Static/security scans: {run_group_summary(report.static_analysis_results)}",
        f"- Security findings: `{len(report.security_findings)}`",
    ]
    if report.report_path:
        lines.append(f"- Report artifact path: `{report.report_path}`")

    if report.risk_reasons:
        lines.extend(["", "### Top Risk Reasons"])
        for reason in report.risk_reasons[:8]:
            lines.append(f"- `+{reason.score_impact}` **{reason.category}:** {reason.reason}")

    if report.policy_decision.reasons:
        lines.extend(["", "### Policy Reasons"])
        lines.extend(f"- {reason}" for reason in report.policy_decision.reasons[:8])

    if report.security_findings:
        lines.extend(["", "### Security Findings"])
        for finding in report.security_findings[:8]:
            lines.append(f"- {security_finding_line(finding)}")

    if report.errors:
        lines.extend(["", "### Pipeline Errors"])
        lines.extend(f"- {error}" for error in report.errors[:8])

    text = trim_text("\n".join(lines).rstrip(), MAX_CHECK_TEXT_CHARS)
    return CheckOutput(title=title, summary=summary, text=text)


def conclusion_for_report(report: RiskReport | PatchGuardReport) -> CheckConclusion:
    if (
        report.policy_decision.decision == PolicyGateDecision.BLOCK
        or report.merge_decision == MergeDecision.DO_NOT_MERGE
    ):
        return "failure"
    if report.status == "partial" or report.merge_decision == MergeDecision.MANUAL_REVIEW:
        return "neutral"
    return "success"


def run_group_summary(runs: list[ToolRun]) -> str:
    if not runs:
        return "`not recorded`"
    return ", ".join(
        f"`{value(run.status)}` {run.summary}"
        for run in runs[:3]
    )


def existing_runs(report: RiskReport | PatchGuardReport) -> list[ToolRun]:
    if isinstance(report, RiskReport):
        return [report.existing_tests] if report.existing_tests else []
    return report.existing_test_results


def security_finding_line(finding: SecurityFinding) -> str:
    location = finding.filename or finding.file or "unknown"
    line = finding.line_number or finding.line
    if line:
        location = f"{location}:{line}"
    message = finding.message or finding.issue_text or "security finding"
    return f"`{finding.severity}` `{location}`: {message}"


def check_run_result_from_response(
    response: requests.Response,
    *,
    action: str,
) -> GitHubCheckRunResult:
    if response.status_code >= 400:
        raise GitHubCheckRunPublishError(
            f"GitHub Check Run {action} failed with HTTP {response.status_code}: "
            f"{response_message(response)}"
        )
    payload = response.json()
    return GitHubCheckRunResult(
        id=int(payload["id"]),
        html_url=payload.get("html_url"),
        details_url=payload.get("details_url"),
    )


def split_repo_full_name(full_name: str) -> tuple[str, str]:
    parts = full_name.split("/", 1)
    if len(parts) != 2 or not parts[0] or not parts[1]:
        raise GitHubCheckRunPublishError(f"Invalid repository full name: {full_name}")
    return parts[0], parts[1]


def external_id_for_job(job: GitHubAppAnalysisJob) -> str:
    return f"patchguard-job-{job.id or 'unknown'}"


def github_timestamp(value_to_format: datetime) -> str:
    if value_to_format.tzinfo is None:
        value_to_format = value_to_format.replace(tzinfo=UTC)
    return value_to_format.astimezone(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def trim_text(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    suffix = "\n\n[PatchGuard output truncated.]"
    return text[: max_chars - len(suffix)].rstrip() + suffix


def response_message(response: requests.Response) -> str:
    try:
        payload: Any = response.json()
    except ValueError:
        return response.text.strip() or response.reason
    if isinstance(payload, dict):
        return str(payload.get("message") or response.reason)
    return response.reason


def value(item: Any) -> str:
    return str(getattr(item, "value", item))
