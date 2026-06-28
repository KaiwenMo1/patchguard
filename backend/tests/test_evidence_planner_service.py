from __future__ import annotations

from patchguard.models import (
    BaseComparisonResult,
    ChangedFile,
    ChangedFunction,
    CommandResult,
    PullRequestInfo,
    RiskReport,
    RunStatus,
    ToolRun,
)
from patchguard.services.evidence_planner_service import EvidencePlannerService
from patchguard.services.report_service import (
    classify_base_comparison,
    summary_for_base_comparison,
)


def test_evidence_planner_adds_optional_memory_and_base_comparison_steps() -> None:
    report = RiskReport(
        pr=PullRequestInfo(
            owner="owner",
            repo="repo",
            number=42,
            url="https://github.com/owner/repo/pull/42",
        ),
        changed_files=[
            ChangedFile(
                filename="src/auth/login.py",
                status="modified",
                classification="security_sensitive",
            )
        ],
        changed_functions=[
            ChangedFunction(
                file_path="src/auth/login.py",
                qualified_name="LoginService.authenticate",
                symbol_type="method",
                start_line=8,
                end_line=20,
                source_code="def authenticate(self):\n    return True\n",
                changed_lines=[12],
            )
        ],
        existing_tests=tool_run("existing", RunStatus.PASSED, kind="existing_tests"),
        generated_test_results=[tool_run("generated", RunStatus.FAILED, kind="generated_tests")],
        static_analysis_results=[tool_run("bandit", RunStatus.PASSED, kind="security_scan")],
        base_comparison=BaseComparisonResult(
            enabled=True,
            status="regression",
            summary="Base passed and head failed.",
            base_tests=tool_run("base", RunStatus.PASSED, kind="existing_tests"),
            head_tests=tool_run("head", RunStatus.FAILED, kind="existing_tests"),
        ),
    )

    plan = EvidencePlannerService().plan(
        report,
        memory_enabled=True,
        base_comparison_enabled=True,
    )

    statuses = {step.step_id: step.status for step in plan.steps}
    assert statuses["diff"] == "completed"
    assert statuses["changed-functions"] == "completed"
    assert statuses["existing-tests"] == "completed"
    assert statuses["generated-tests"] == "failed"
    assert statuses["base-vs-head-tests"] == "failed"
    assert statuses["memory-retrieval"] == "skipped"


def test_base_comparison_classifies_head_only_failure_as_regression() -> None:
    base = tool_run("base", RunStatus.PASSED, kind="existing_tests")
    head = tool_run("head", RunStatus.FAILED, kind="existing_tests")
    comparison = BaseComparisonResult(
        enabled=True,
        base_tests=base,
        head_tests=head,
        status=classify_base_comparison(base, head),
    )
    comparison.summary = summary_for_base_comparison(comparison)

    assert comparison.status == "regression"
    assert "regression evidence" in comparison.summary


def tool_run(
    name: str,
    status: RunStatus,
    *,
    kind: str,
) -> ToolRun:
    return ToolRun(
        name=name,
        kind="existing_tests" if name in {"existing", "base", "head"} else "generated_tests",
        status=status,
        summary=f"{name} {status.value}",
        command=CommandResult(command=["python", "-m", "pytest", "-q"], exit_code=0),
    )
