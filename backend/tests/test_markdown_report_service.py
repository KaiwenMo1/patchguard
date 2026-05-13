from __future__ import annotations

from patchguard.models import (
    ChangedFile,
    PullRequestInfo,
    RiskReason,
    RiskReport,
    RunStatus,
    SecurityFinding,
    ToolRun,
)
from patchguard.services.markdown_report_service import render_markdown_report


def test_markdown_report_is_readable() -> None:
    report = RiskReport(
        status="partial",
        pr=PullRequestInfo(
            owner="owner",
            repo="repo",
            number=123,
            url="https://github.com/owner/repo/pull/123",
            title="Tighten parser behavior",
            author="octo-dev",
            state="open",
            base_ref="main",
            head_ref="fix-parser",
            additions=3,
            deletions=1,
            changed_files_count=1,
        ),
        changed_files=[
            ChangedFile(
                filename="parser_demo/parser.py",
                status="modified",
                additions=3,
                deletions=1,
                changes=4,
                classification="source",
            )
        ],
        risk_score=30,
        risk_reasons=[
            RiskReason(
                category="source_without_tests",
                score_impact=20,
                reason="Source changed without tests.",
            )
        ],
        security_findings=[
            SecurityFinding(
                tool="bandit",
                severity="HIGH",
                confidence="HIGH",
                filename="parser_demo/parser.py",
                line_number=12,
                message="Use of eval detected.",
            )
        ],
        existing_tests=ToolRun(
            name="run existing pytest suite",
            kind="existing_tests",
            status=RunStatus.PASSED,
            summary="pytest passed",
        ),
    )

    markdown = render_markdown_report(report)

    assert "# PatchGuard Report" in markdown
    assert "**Risk:** `30/100` (`low`)" in markdown
    assert "Tighten parser behavior" in markdown
    assert "`parser_demo/parser.py`" in markdown
    assert "Use of eval detected." in markdown
    assert "## Existing Tests" in markdown
