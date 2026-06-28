from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest
from patchguard.app_models import GitHubAppAnalysisJob
from patchguard.models import (
    CommandResult,
    MergeDecision,
    PolicyDecision,
    PolicyGateDecision,
    PullRequestInfo,
    RiskLevel,
    RiskReason,
    RiskReport,
    RunStatus,
    SecurityFinding,
    ToolRun,
)
from patchguard.services.github_app_check_service import (
    MAX_CHECK_TEXT_CHARS,
    GitHubAppCheckService,
    GitHubCheckRunPublishError,
    completed_payload_from_report,
    conclusion_for_report,
    create_in_progress_payload,
    render_check_output,
)

FIXED_NOW = datetime(2026, 6, 25, 12, 0, 0, tzinfo=UTC)


def test_create_in_progress_check_run_payload_is_deterministic(monkeypatch) -> None:
    calls: list[dict[str, Any]] = []

    def fake_post(url, headers, json, timeout):  # noqa: ANN001
        calls.append(
            {
                "url": url,
                "headers": headers,
                "json": json,
                "timeout": timeout,
            }
        )
        return FakeResponse(
            {
                "id": 777,
                "html_url": "https://github.com/KaiwenMo1/patchguard/runs/777",
                "details_url": "https://patchguard.local/reports/job-12",
            }
        )

    monkeypatch.setattr("patchguard.services.github_app_check_service.requests.post", fake_post)
    service = GitHubAppCheckService(
        token="installation-token",
        now=lambda: FIXED_NOW,
        timeout_seconds=9,
        details_url="https://patchguard.local/reports/job-12",
    )

    result = service.create_in_progress(sample_job(), github_installation_id=98765)

    assert result.id == 777
    assert result.html_url == "https://github.com/KaiwenMo1/patchguard/runs/777"
    assert calls == [
        {
            "url": "https://api.github.com/repos/KaiwenMo1/patchguard/check-runs",
            "headers": {
                "Accept": "application/vnd.github+json",
                "Authorization": "Bearer installation-token",
                "X-GitHub-Api-Version": "2022-11-28",
                "User-Agent": "PatchGuard-GitHub-App",
            },
            "json": {
                "name": "PatchGuard",
                "head_sha": "head-sha-456",
                "status": "in_progress",
                "started_at": "2026-06-25T12:00:00Z",
                "external_id": "patchguard-job-12",
                "output": {
                    "title": "PatchGuard analysis running",
                    "summary": "PatchGuard is generating an evidence-backed merge-risk report.",
                },
                "details_url": "https://patchguard.local/reports/job-12",
            },
            "timeout": 9,
        }
    ]


@pytest.mark.parametrize(
    ("status", "risk_level", "merge_decision", "policy_decision", "expected_conclusion"),
    [
        (
            "complete",
            RiskLevel.MEDIUM,
            MergeDecision.MERGE_WITH_CAUTION,
            PolicyGateDecision.PASS,
            "success",
        ),
        (
            "partial",
            RiskLevel.HIGH,
            MergeDecision.MANUAL_REVIEW,
            PolicyGateDecision.WARN,
            "neutral",
        ),
        (
            "complete",
            RiskLevel.CRITICAL,
            MergeDecision.DO_NOT_MERGE,
            PolicyGateDecision.BLOCK,
            "failure",
        ),
    ],
)
def test_update_check_run_maps_patchguard_decisions(
    monkeypatch,
    status: str,
    risk_level: RiskLevel,
    merge_decision: MergeDecision,
    policy_decision: PolicyGateDecision,
    expected_conclusion: str,
) -> None:
    calls: list[dict[str, Any]] = []

    def fake_patch(url, headers, json, timeout):  # noqa: ANN001
        calls.append({"url": url, "headers": headers, "json": json, "timeout": timeout})
        return FakeResponse({"id": 777, "html_url": "https://github.com/checks/777"})

    monkeypatch.setattr("patchguard.services.github_app_check_service.requests.patch", fake_patch)
    service = GitHubAppCheckService(token="installation-token", now=lambda: FIXED_NOW)
    report = sample_report(
        status=status,
        risk_level=risk_level,
        merge_decision=merge_decision,
        policy_decision=policy_decision,
    )

    result = service.update_from_report(
        sample_job(),
        report,
        github_installation_id=98765,
    )

    assert result.id == 777
    assert calls[0]["url"] == "https://api.github.com/repos/KaiwenMo1/patchguard/check-runs/777"
    payload = calls[0]["json"]
    assert payload["status"] == "completed"
    assert payload["conclusion"] == expected_conclusion
    assert payload["completed_at"] == "2026-06-25T12:00:00Z"
    assert payload["external_id"] == "patchguard-job-12"
    assert payload["output"]["title"].startswith("PatchGuard:")
    assert "Report artifact path: `.patchguard/app_reports/job-12.json`" in payload["output"]["text"]
    assert "raw stdout that should never be posted" not in payload["output"]["text"]
    assert len(payload["output"]["text"]) <= MAX_CHECK_TEXT_CHARS


def test_error_update_uses_failure_conclusion_and_truncates_message(monkeypatch) -> None:
    calls: list[dict[str, Any]] = []

    def fake_patch(url, headers, json, timeout):  # noqa: ANN001, ARG001
        calls.append(json)
        return FakeResponse({"id": 777})

    monkeypatch.setattr("patchguard.services.github_app_check_service.requests.patch", fake_patch)
    service = GitHubAppCheckService(token="installation-token", now=lambda: FIXED_NOW)

    service.update_for_failure(
        sample_job(),
        github_installation_id=98765,
        error="x" * 2000,
    )

    payload = calls[0]
    assert payload["conclusion"] == "failure"
    assert payload["output"]["title"] == "PatchGuard analysis failed"
    assert "[PatchGuard output truncated.]" in payload["output"]["text"]
    assert len(payload["output"]["text"]) < 1200


def test_details_url_template_is_resolved_per_job(monkeypatch) -> None:
    calls: list[dict[str, Any]] = []

    def fake_post(url, headers, json, timeout):  # noqa: ANN001, ARG001
        calls.append(json)
        return FakeResponse({"id": 777})

    monkeypatch.setattr("patchguard.services.github_app_check_service.requests.post", fake_post)
    service = GitHubAppCheckService(
        token="installation-token",
        now=lambda: FIXED_NOW,
        details_url="https://patchguard.example.com/api/app/jobs/{job_id}/report",
    )

    service.create_in_progress(sample_job(), github_installation_id=98765)

    assert calls[0]["details_url"] == "https://patchguard.example.com/api/app/jobs/12/report"


def test_http_failure_raises_clean_error(monkeypatch) -> None:
    def fake_post(url, headers, json, timeout):  # noqa: ANN001, ARG001
        return FakeResponse({"message": "Resource not accessible by integration"}, status_code=403)

    monkeypatch.setattr("patchguard.services.github_app_check_service.requests.post", fake_post)
    service = GitHubAppCheckService(token="installation-token", now=lambda: FIXED_NOW)

    with pytest.raises(GitHubCheckRunPublishError, match="HTTP 403"):
        service.create_in_progress(sample_job(), github_installation_id=98765)


def test_payload_helpers_are_deterministic() -> None:
    job = sample_job()
    report = sample_report()

    assert create_in_progress_payload(job, started_at=FIXED_NOW) == {
        "name": "PatchGuard",
        "head_sha": "head-sha-456",
        "status": "in_progress",
        "started_at": "2026-06-25T12:00:00Z",
        "external_id": "patchguard-job-12",
        "output": {
            "title": "PatchGuard analysis running",
            "summary": "PatchGuard is generating an evidence-backed merge-risk report.",
        },
    }
    completed = completed_payload_from_report(job, report, completed_at=FIXED_NOW)
    assert completed["name"] == "PatchGuard"
    assert completed["status"] == "completed"
    assert completed["conclusion"] == "success"
    assert completed["completed_at"] == "2026-06-25T12:00:00Z"
    assert conclusion_for_report(report) == "success"
    assert render_check_output(job, report).title == "PatchGuard: 35/100 medium risk"


def sample_job() -> GitHubAppAnalysisJob:
    return GitHubAppAnalysisJob(
        id=12,
        installation_id=1,
        repository_id=2,
        repository_full_name="KaiwenMo1/patchguard",
        event_type="pull_request.opened",
        status="running",
        pr_number=42,
        pr_url="https://github.com/KaiwenMo1/patchguard/pull/42",
        base_sha="base-sha-123",
        head_sha="head-sha-456",
        check_run_id=777,
        check_run_url="https://github.com/KaiwenMo1/patchguard/runs/777",
    )


def sample_report(
    *,
    status: str = "complete",
    risk_level: RiskLevel = RiskLevel.MEDIUM,
    merge_decision: MergeDecision = MergeDecision.MERGE_WITH_CAUTION,
    policy_decision: PolicyGateDecision = PolicyGateDecision.PASS,
) -> RiskReport:
    return RiskReport(
        status=status,
        pr=PullRequestInfo(
            owner="KaiwenMo1",
            repo="patchguard",
            number=42,
            url="https://github.com/KaiwenMo1/patchguard/pull/42",
            title="Improve parser behavior",
        ),
        existing_tests=ToolRun(
            name="run existing pytest suite",
            kind="existing_tests",
            status=RunStatus.PASSED,
            summary="pytest passed",
            command=CommandResult(
                command=["python", "-m", "pytest", "-q"],
                exit_code=0,
                stdout_tail="raw stdout that should never be posted",
                stderr_tail="raw stderr that should never be posted",
            ),
        ),
        generated_test_results=[
            ToolRun(
                name="run generated tests",
                kind="generated_tests",
                status=RunStatus.SKIPPED,
                summary="OPENAI_API_KEY is not set; generation skipped",
            )
        ],
        static_analysis_results=[
            ToolRun(
                name="ruff check",
                kind="static_analysis",
                status=RunStatus.PASSED,
                summary="ruff passed",
            )
        ],
        security_findings=[
            SecurityFinding(
                tool="bandit",
                severity="LOW",
                confidence="HIGH",
                filename="src/app.py",
                line_number=12,
                message="Low severity issue",
            )
        ],
        risk_score=35 if risk_level == RiskLevel.MEDIUM else 75,
        risk_level=risk_level,
        risk_reasons=[
            RiskReason(
                category="test_coverage",
                score_impact=20,
                reason="Source changed without matching tests.",
            )
        ],
        policy_decision=PolicyDecision(
            decision=policy_decision,
            reasons=["Policy reason"] if policy_decision != PolicyGateDecision.PASS else [],
        ),
        merge_decision=merge_decision,
        report_path=".patchguard/app_reports/job-12.json",
    )


class FakeResponse:
    def __init__(
        self,
        payload,
        *,
        status_code: int = 200,
        text: str = "",
        reason: str = "OK",
    ) -> None:
        self._payload = payload
        self.status_code = status_code
        self.text = text
        self.reason = reason

    def json(self):
        return self._payload
