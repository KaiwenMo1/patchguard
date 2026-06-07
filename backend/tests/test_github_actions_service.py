from __future__ import annotations

from io import StringIO

from patchguard.models import (
    ChangedFunction,
    FailureMapping,
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
from patchguard.services.github_actions_service import (
    build_annotations,
    emit_github_actions_output,
    render_step_summary,
)


def test_build_annotations_includes_policy_security_and_failed_generated_test() -> None:
    annotations = build_annotations(_report())

    assert annotations[0].level == "error"
    assert annotations[0].title == "PatchGuard policy blocked this PR"
    assert any(annotation.path == "src/app.py" and annotation.line == 12 for annotation in annotations)
    assert any(annotation.title == "Generated regression test failed" for annotation in annotations)


def test_emit_annotations_escapes_workflow_command_values() -> None:
    report = _report()
    report.security_findings[0].message = "bad value: a,b\nnext line"
    stream = StringIO()

    result = emit_github_actions_output(report, annotations=True, stream=stream)

    assert result.annotations_emitted > 0
    output = stream.getvalue()
    assert "bad value: a,b%0Anext line" in output
    assert "file=src/app.py" in output


def test_step_summary_is_written_when_path_is_provided(tmp_path) -> None:
    summary_path = tmp_path / "summary.md"

    result = emit_github_actions_output(
        _report(),
        step_summary=True,
        summary_path=summary_path,
    )

    assert result.step_summary_written is True
    assert result.step_summary_path == str(summary_path)
    text = summary_path.read_text(encoding="utf-8")
    assert "# PatchGuard" in text
    assert "**Risk:** `85/100` (`critical`)" in text
    assert "Generated regression failed" in text


def test_render_step_summary_handles_missing_evidence() -> None:
    report = RiskReport(
        pr=PullRequestInfo(
            owner="owner",
            repo="repo",
            number=1,
            url="https://github.com/owner/repo/pull/1",
        )
    )

    text = render_step_summary(report)

    assert "**Existing tests:** `not_run` no evidence recorded" in text
    assert "**Generated tests:** `not_run` no evidence recorded" in text


def _report() -> RiskReport:
    return RiskReport(
        pr=PullRequestInfo(
            owner="owner",
            repo="repo",
            number=123,
            url="https://github.com/owner/repo/pull/123",
            title="Improve parser",
            additions=8,
            deletions=2,
            changed_files_count=1,
        ),
        changed_functions=[
            ChangedFunction(
                file_path="src/app.py",
                qualified_name="parse_config",
                symbol_type="function",
                start_line=10,
                end_line=20,
                source_code="def parse_config(value):\n    return value",
            )
        ],
        existing_tests=ToolRun(
            name="run existing pytest suite",
            kind="existing_tests",
            status=RunStatus.PASSED,
            summary="pytest passed",
        ),
        generated_test_results=[
            ToolRun(
                name="run generated PatchGuard tests",
                kind="generated_tests",
                status=RunStatus.FAILED,
                summary="Generated regression failed",
            )
        ],
        failure_mappings=[
            FailureMapping(
                failed_test="test_parse_empty_input",
                target_file="src/app.py",
                target_function="parse_config",
                behavior_checked="empty input",
                failure_summary="empty input raised ValueError",
                risk_message="Generated test failed for changed parser behavior.",
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
        risk_score=85,
        risk_level=RiskLevel.CRITICAL,
        risk_reasons=[
            RiskReason(
                category="security",
                score_impact=20,
                reason="High security finding detected.",
            )
        ],
        policy_decision=PolicyDecision(
            decision=PolicyGateDecision.BLOCK,
            reasons=["High security finding exists."],
            triggered_rules=["high_security_finding"],
        ),
    )
