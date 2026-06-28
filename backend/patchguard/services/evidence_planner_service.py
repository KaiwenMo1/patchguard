"""Deterministic evidence planning for PatchGuard reports."""

from __future__ import annotations

from patchguard.models import (
    BaseComparisonResult,
    EvidencePlan,
    EvidencePlanStep,
    PatchGuardReport,
    RiskReport,
    RunStatus,
    ToolRun,
)
from patchguard.services.diff_service import DiffService


class EvidencePlannerService:
    """Create an explainable plan from diff shape and collected evidence."""

    def __init__(self, diff_service: DiffService | None = None) -> None:
        self.diff_service = diff_service or DiffService()

    def plan(
        self,
        report: RiskReport | PatchGuardReport,
        *,
        memory_enabled: bool = False,
        base_comparison_enabled: bool = False,
    ) -> EvidencePlan:
        summary = "PatchGuard selected evidence steps from changed files, risk signals, and enabled options."
        diff_summary = self.diff_service.summarize(report.changed_files)
        source_files = [file.filename for file in diff_summary.source_files]
        test_files = [file.filename for file in diff_summary.test_files]
        security_files = [file.filename for file in diff_summary.security_sensitive_files]
        dependency_files = [file.filename for file in diff_summary.dependency_files]
        config_files = [file.filename for file in diff_summary.config_files]
        steps = [
            EvidencePlanStep(
                step_id="diff",
                title="Fetch and classify pull request diff",
                reason="PatchGuard needs changed-file evidence before choosing deeper checks.",
                target_files=[file.filename for file in report.changed_files],
                status="completed" if report.changed_files else "skipped",
                evidence=[f"{len(report.changed_files)} changed files"],
            ),
            EvidencePlanStep(
                step_id="changed-functions",
                title="Extract changed Python functions",
                reason="Function-level context lets generated tests and review focus on affected behavior.",
                target_files=source_files,
                target_functions=[function.qualified_name for function in report.changed_functions],
                status="completed" if report.changed_functions else "skipped",
                evidence=[f"{len(report.changed_functions)} changed functions/classes"],
            ),
            EvidencePlanStep(
                step_id="existing-tests",
                title="Run existing pytest suite in Docker",
                reason="Existing project tests are the strongest baseline signal available locally.",
                target_files=source_files + test_files,
                commands=["python -m pytest -q"],
                status=status_from_runs(existing_test_runs(report)),
                evidence=summaries(existing_test_runs(report)),
            ),
            EvidencePlanStep(
                step_id="generated-tests",
                title="Generate and run targeted regression tests",
                reason="Changed functions and behavioral contracts can expose edge cases missing from the existing suite.",
                target_files=source_files,
                target_functions=[function.qualified_name for function in report.changed_functions],
                commands=["python -m pytest -q .patchguard/generated_tests"],
                status=status_from_runs(report.generated_test_results),
                evidence=summaries(report.generated_test_results),
            ),
            EvidencePlanStep(
                step_id="static-security",
                title="Run Ruff and Bandit scans",
                reason="Static and security findings provide cheap evidence before merge.",
                target_files=security_files + dependency_files + config_files + source_files,
                commands=["ruff check .", "bandit -r . -f json"],
                status=status_from_runs(report.static_analysis_results),
                evidence=summaries(report.static_analysis_results)
                + [f"{len(report.security_findings)} security findings"],
            ),
        ]
        if base_comparison_enabled or report.base_comparison.enabled:
            steps.append(base_comparison_step(report.base_comparison))
        if memory_enabled or report.memory_hits:
            steps.append(
                EvidencePlanStep(
                    step_id="memory-retrieval",
                    title="Retrieve similar historical evidence",
                    reason="Prior PatchGuard reports can reveal repeated risky files, functions, and failure patterns.",
                    target_files=source_files,
                    target_functions=[function.qualified_name for function in report.changed_functions],
                    status="completed" if report.memory_hits else "skipped",
                    evidence=[f"{len(report.memory_hits)} similar evidence hits"],
                )
            )
        return EvidencePlan(summary=summary, steps=steps)


def base_comparison_step(base_comparison: BaseComparisonResult) -> EvidencePlanStep:
    evidence = [base_comparison.summary] if base_comparison.summary else []
    if base_comparison.base_tests:
        evidence.append(f"base: {base_comparison.base_tests.status.value}")
    if base_comparison.head_tests:
        evidence.append(f"head: {base_comparison.head_tests.status.value}")
    return EvidencePlanStep(
        step_id="base-vs-head-tests",
        title="Compare base and PR-head pytest results",
        reason="A failing head with a passing base is stronger regression evidence than a head-only failure.",
        commands=["git checkout <base_sha> && pytest", "git checkout <head_sha> && pytest"],
        status=status_from_base_comparison(base_comparison),
        evidence=evidence,
    )


def existing_test_runs(report: RiskReport | PatchGuardReport) -> list[ToolRun]:
    if isinstance(report, RiskReport):
        return [report.existing_tests] if report.existing_tests else []
    return report.existing_test_results


def status_from_base_comparison(base_comparison: BaseComparisonResult) -> str:
    if not base_comparison.enabled:
        return "skipped"
    if base_comparison.status == "regression":
        return "failed"
    if base_comparison.status in {"passed", "base_failed"}:
        return "completed"
    if base_comparison.status in {"error"}:
        return "error"
    return "skipped"


def status_from_runs(runs: list[ToolRun]) -> str:
    if not runs:
        return "skipped"
    if any(run.status == RunStatus.FAILED for run in runs):
        return "failed"
    if any(run.status == RunStatus.ERROR for run in runs):
        return "error"
    if all(run.status == RunStatus.SKIPPED for run in runs):
        return "skipped"
    return "completed"


def summaries(runs: list[ToolRun]) -> list[str]:
    return [f"{run.name}: {run.status.value} ({run.summary})" for run in runs]
