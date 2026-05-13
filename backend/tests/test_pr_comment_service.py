from __future__ import annotations

from patchguard.models import (
    CommandResult,
    PullRequestInfo,
    RiskReason,
    RiskReport,
    RunStatus,
    SecurityFinding,
    ToolRun,
)
from patchguard.services.pr_comment_service import (
    COMMENT_MARKER,
    GitHubPRCommentService,
    render_pr_comment,
)


def test_pr_comment_skips_without_token(monkeypatch) -> None:
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)

    result = GitHubPRCommentService().post_or_update(_report())

    assert result.status == "skipped"
    assert "GITHUB_TOKEN is not set" in result.summary


def test_render_pr_comment_is_concise_and_marked() -> None:
    body = render_pr_comment(_report())

    assert body.startswith(COMMENT_MARKER)
    assert "**Risk:** `45/100` (`medium`)" in body
    assert "**Recommendation:** Merge only after human review." in body
    assert "- Existing tests: `passed` pytest passed" in body
    assert "- Generated tests: `failed` generated regression failed" in body
    assert "1 security finding(s): 1 high." in body
    assert "Report artifact: `/tmp/patchguard-report.json`" in body
    assert "stdout tail" not in body


def test_posts_new_comment_when_marker_is_absent(monkeypatch) -> None:
    calls: list[tuple[str, str, dict | None]] = []

    def fake_get(url, headers, params, timeout):  # noqa: ANN001, ARG001
        calls.append(("GET", url, None))
        return FakeResponse([])

    def fake_post(url, headers, json, timeout):  # noqa: ANN001, ARG001
        calls.append(("POST", url, json))
        assert headers["Authorization"] == "Bearer test-token"
        assert COMMENT_MARKER in json["body"]
        return FakeResponse({"html_url": "https://github.com/owner/repo/pull/123#issuecomment-1"})

    monkeypatch.setattr("patchguard.services.pr_comment_service.requests.get", fake_get)
    monkeypatch.setattr("patchguard.services.pr_comment_service.requests.post", fake_post)

    result = GitHubPRCommentService(token="test-token").post_or_update(_report())

    assert result.status == "posted"
    assert result.comment_url == "https://github.com/owner/repo/pull/123#issuecomment-1"
    assert [call[0] for call in calls] == ["GET", "POST"]


def test_updates_existing_marker_comment(monkeypatch) -> None:
    calls: list[tuple[str, str, dict | None]] = []

    def fake_get(url, headers, params, timeout):  # noqa: ANN001, ARG001
        calls.append(("GET", url, None))
        return FakeResponse(
            [
                {"id": 42, "body": "old comment", "html_url": "https://example.invalid/old"},
                {
                    "id": 99,
                    "body": f"{COMMENT_MARKER}\nold PatchGuard report",
                    "html_url": "https://example.invalid/existing",
                },
            ]
        )

    def fake_patch(url, headers, json, timeout):  # noqa: ANN001, ARG001
        calls.append(("PATCH", url, json))
        assert url.endswith("/issues/comments/99")
        assert COMMENT_MARKER in json["body"]
        return FakeResponse({"html_url": "https://example.invalid/updated"})

    def fake_post(url, headers, json, timeout):  # noqa: ANN001, ARG001
        raise AssertionError("should update instead of posting a duplicate comment")

    monkeypatch.setattr("patchguard.services.pr_comment_service.requests.get", fake_get)
    monkeypatch.setattr("patchguard.services.pr_comment_service.requests.patch", fake_patch)
    monkeypatch.setattr("patchguard.services.pr_comment_service.requests.post", fake_post)

    result = GitHubPRCommentService(token="test-token").post_or_update(_report())

    assert result.status == "updated"
    assert result.comment_url == "https://example.invalid/updated"
    assert [call[0] for call in calls] == ["GET", "PATCH"]


def _report() -> RiskReport:
    return RiskReport(
        pr=PullRequestInfo(
            owner="owner",
            repo="repo",
            number=123,
            url="https://github.com/owner/repo/pull/123",
            title="Improve parser",
            author="octo-dev",
            state="open",
            additions=2,
            deletions=1,
            changed_files_count=1,
        ),
        existing_tests=ToolRun(
            name="run existing pytest suite",
            kind="existing_tests",
            status=RunStatus.PASSED,
            summary="pytest passed",
            command=CommandResult(
                command=["python", "-m", "pytest", "-q"],
                exit_code=0,
                stdout_tail="stdout tail that should stay out of the comment",
            ),
        ),
        generated_test_results=[
            ToolRun(
                name="run generated tests",
                kind="generated_tests",
                status=RunStatus.FAILED,
                summary="generated regression failed",
            )
        ],
        security_findings=[
            SecurityFinding(
                tool="bandit",
                severity="HIGH",
                confidence="HIGH",
                filename="src/app.py",
                line_number=12,
                message="Use of eval detected.",
            )
        ],
        risk_score=45,
        risk_level="medium",
        risk_reasons=[
            RiskReason(
                category="security",
                score_impact=20,
                reason="High security finding detected.",
            )
        ],
        report_path="/tmp/patchguard-report.json",
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
