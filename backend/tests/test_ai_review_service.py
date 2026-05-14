from __future__ import annotations

from patchguard.models import (
    ChangedFile,
    PullRequestInfo,
    RiskReason,
    RiskReport,
    RunStatus,
    SecurityFinding,
)
from patchguard.services.ai_review_service import AIReviewService


def test_ai_review_skips_without_api_key(monkeypatch) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    result = AIReviewService().review(_report())

    assert result.tool_run.status == RunStatus.SKIPPED
    assert "OPENAI_API_KEY is not set" in result.tool_run.summary
    assert result.review.limitations
    assert result.review.executive_summary.startswith("OPENAI_API_KEY")


def test_ai_review_parses_provider_json() -> None:
    provider = FakeReviewProvider(
        """
        {
          "merge_recommendation": "needs_human_review",
          "executive_summary": "The PR changes parser behavior and has missing test evidence.",
          "pr_change_summary": ["Parser code changed in src/parser.py"],
          "correctness_notes": ["Existing tests passed but generated tests were skipped."],
          "efficiency_notes": ["No performance evidence was collected."],
          "top_risks": [
            {
              "title": "Parser behavior changed without tests",
              "severity": "high",
              "evidence": ["Source files changed without test files changing"],
              "files": ["src/parser.py"],
              "suggested_fix": "Add an empty-input regression test."
            }
          ],
          "files_to_review_first": ["src/parser.py"],
          "suggested_followup_tests": ["test empty parser input"],
          "suggested_fixes": ["Add regression coverage"],
          "limitations": ["Generated tests were skipped"]
        }
        """
    )

    result = AIReviewService(api_key="test-key", provider=provider).review(_report())

    assert result.tool_run.status == RunStatus.PASSED
    assert result.review.executive_summary.startswith("The PR changes parser")
    assert result.review.top_risks[0].title == "Parser behavior changed without tests"
    assert result.review.top_risks[0].evidence == [
        "Source files changed without test files changing"
    ]
    assert "PatchGuard evidence payload" in provider.prompt
    assert "src/parser.py" in provider.prompt


def test_ai_review_invalid_json_records_error() -> None:
    result = AIReviewService(
        api_key="test-key",
        provider=FakeReviewProvider("not json"),
    ).review(_report())

    assert result.tool_run.status == RunStatus.ERROR
    assert "failed" in result.tool_run.summary.lower()
    assert result.review.limitations


def _report() -> RiskReport:
    return RiskReport(
        status="partial",
        pr=PullRequestInfo(
            owner="owner",
            repo="repo",
            number=1,
            url="https://github.com/owner/repo/pull/1",
            title="Tighten parser behavior",
            additions=1,
            deletions=1,
            changed_files_count=1,
        ),
        changed_files=[
            ChangedFile(
                filename="src/parser.py",
                status="modified",
                additions=1,
                deletions=1,
                changes=2,
                patch="@@ -1,1 +1,1 @@\n-return []\n+return ['']",
            )
        ],
        risk_score=70,
        risk_reasons=[
            RiskReason(
                category="test_coverage",
                score_impact=80,
                reason="Source files changed without test files changing",
                severity="high",
            )
        ],
        security_findings=[
            SecurityFinding(
                tool="bandit",
                severity="MEDIUM",
                confidence="HIGH",
                filename="src/parser.py",
                line_number=2,
                message="Example finding",
            )
        ],
    )


class FakeReviewProvider:
    provider_name = "fake-llm"
    model = "fake-model"

    def __init__(self, output: str) -> None:
        self.output = output
        self.prompt = ""

    def generate_review(self, prompt: str) -> str:
        self.prompt = prompt
        return self.output
