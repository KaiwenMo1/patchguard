"""GitHub PR comment support for PatchGuard reports."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Literal

import requests

from patchguard.models import PatchGuardReport, RiskReport, ToolRun

COMMENT_MARKER = "<!-- patchguard-report -->"


@dataclass(frozen=True)
class PRCommentResult:
    status: Literal["posted", "updated", "skipped", "failed"]
    summary: str
    comment_url: str | None = None


class GitHubPRCommentService:
    """Post or update one PatchGuard summary comment on a GitHub pull request."""

    def __init__(self, *, token: str | None = None, timeout_seconds: int = 20) -> None:
        self.token = token if token is not None else os.getenv("GITHUB_TOKEN")
        self.timeout_seconds = timeout_seconds

    def post_or_update(self, report: RiskReport | PatchGuardReport) -> PRCommentResult:
        if not self.token:
            return PRCommentResult(
                status="skipped",
                summary="GITHUB_TOKEN is not set; PR comment skipped.",
            )
        pr = report.pr
        if pr is None:
            return PRCommentResult(
                status="skipped",
                summary="Report does not include PR metadata; PR comment skipped.",
            )

        owner = getattr(pr, "owner", "")
        repo = getattr(pr, "repo", "")
        number = getattr(pr, "number", None)
        if not owner or not repo or number is None:
            return PRCommentResult(
                status="skipped",
                summary="Report PR metadata is incomplete; PR comment skipped.",
            )

        body = render_pr_comment(report)
        try:
            existing = self._find_existing_comment(owner, repo, int(number))
            if existing:
                return self._update_comment(owner, repo, existing, body)
            return self._create_comment(owner, repo, int(number), body)
        except requests.RequestException as exc:
            return PRCommentResult(
                status="failed",
                summary=f"GitHub PR comment request failed: {exc}",
            )

    def _find_existing_comment(self, owner: str, repo: str, pr_number: int) -> dict[str, Any] | None:
        page = 1
        while True:
            response = requests.get(
                f"https://api.github.com/repos/{owner}/{repo}/issues/{pr_number}/comments",
                headers=self._headers(),
                params={"per_page": 100, "page": page},
                timeout=self.timeout_seconds,
            )
            if response.status_code >= 400:
                raise GitHubCommentHTTPError(response)
            comments = response.json()
            for comment in comments:
                if isinstance(comment, dict) and COMMENT_MARKER in str(comment.get("body", "")):
                    return comment
            if not comments or len(comments) < 100:
                return None
            page += 1

    def _create_comment(self, owner: str, repo: str, pr_number: int, body: str) -> PRCommentResult:
        response = requests.post(
            f"https://api.github.com/repos/{owner}/{repo}/issues/{pr_number}/comments",
            headers=self._headers(),
            json={"body": body},
            timeout=self.timeout_seconds,
        )
        if response.status_code >= 400:
            return self._failed_response("create", response)
        payload = response.json()
        return PRCommentResult(
            status="posted",
            summary="Posted PatchGuard PR comment.",
            comment_url=payload.get("html_url"),
        )

    def _update_comment(
        self,
        owner: str,
        repo: str,
        comment: dict[str, Any],
        body: str,
    ) -> PRCommentResult:
        comment_id = comment.get("id")
        if comment_id is None:
            return PRCommentResult(
                status="failed",
                summary="Existing PatchGuard comment did not include an id; could not update.",
            )
        response = requests.patch(
            f"https://api.github.com/repos/{owner}/{repo}/issues/comments/{comment_id}",
            headers=self._headers(),
            json={"body": body},
            timeout=self.timeout_seconds,
        )
        if response.status_code >= 400:
            return self._failed_response("update", response)
        payload = response.json()
        return PRCommentResult(
            status="updated",
            summary="Updated existing PatchGuard PR comment.",
            comment_url=payload.get("html_url") or comment.get("html_url"),
        )

    def _headers(self) -> dict[str, str]:
        return {
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {self.token}",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "PatchGuard-MVP",
        }

    @staticmethod
    def _failed_response(action: str, response: requests.Response) -> PRCommentResult:
        message = _response_message(response)
        return PRCommentResult(
            status="failed",
            summary=f"GitHub PR comment {action} failed with HTTP {response.status_code}: {message}",
        )


class GitHubCommentHTTPError(requests.RequestException):
    def __init__(self, response: requests.Response) -> None:
        super().__init__(
            f"GitHub returned HTTP {response.status_code}: {_response_message(response)}"
        )
        self.response = response


def render_pr_comment(report: RiskReport | PatchGuardReport) -> str:
    lines = [
        COMMENT_MARKER,
        "## PatchGuard Report",
        "",
        f"**Risk:** `{report.risk_score}/100` (`{_value(report.risk_level)}`)",
        f"**Recommendation:** {escape_markdown(_value(report.recommendation))}",
        "",
        "### Test Results",
        *_test_result_lines(report),
        "",
        "### Security",
        _security_summary(report),
        "",
        "### Top Risk Reasons",
        *_risk_reason_lines(report),
    ]
    if report.report_path:
        lines.extend(["", f"Report artifact: `{escape_markdown(report.report_path)}`"])
    return "\n".join(lines).rstrip() + "\n"


def _test_result_lines(report: RiskReport | PatchGuardReport) -> list[str]:
    existing_runs = _existing_test_runs(report)
    generated_runs = report.generated_test_results
    return [
        f"- Existing tests: {_run_summary(existing_runs)}",
        f"- Generated tests: {_run_summary(generated_runs)}",
    ]


def _existing_test_runs(report: RiskReport | PatchGuardReport) -> list[ToolRun]:
    if isinstance(report, RiskReport):
        return [report.existing_tests] if report.existing_tests else []
    return report.existing_test_results


def _run_summary(runs: list[ToolRun]) -> str:
    if not runs:
        return "`not recorded`"
    return ", ".join(
        f"`{_value(run.status)}` {escape_markdown(run.summary)}"
        for run in runs[:3]
    )


def _security_summary(report: RiskReport | PatchGuardReport) -> str:
    findings = report.security_findings
    if not findings:
        return "No security findings recorded."
    severities: dict[str, int] = {}
    for finding in findings:
        severity = str(finding.severity or "unknown").lower()
        severities[severity] = severities.get(severity, 0) + 1
    severity_text = ", ".join(
        f"{count} {severity}" for severity, count in sorted(severities.items())
    )
    return f"{len(findings)} security finding(s): {severity_text}."


def _risk_reason_lines(report: RiskReport | PatchGuardReport) -> list[str]:
    if not report.risk_reasons:
        return ["- No risk reasons recorded."]
    return [
        f"- `+{reason.score_impact}` **{escape_markdown(reason.category)}:** "
        f"{escape_markdown(reason.reason)}"
        for reason in report.risk_reasons[:5]
    ]


def _response_message(response: requests.Response) -> str:
    try:
        payload = response.json()
    except ValueError:
        return response.text.strip() or response.reason
    if isinstance(payload, dict):
        return str(payload.get("message") or response.reason)
    return response.reason


def escape_markdown(value: str) -> str:
    return str(value).replace("|", "\\|")


def _value(value: Any) -> str:
    return str(getattr(value, "value", value))
