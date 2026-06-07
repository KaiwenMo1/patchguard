"""GitHub Actions annotations and job summary rendering."""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import TextIO

from patchguard.models import (
    FailureMapping,
    PatchGuardReport,
    PolicyGateDecision,
    RiskReport,
    RunStatus,
    SecurityFinding,
    ToolRun,
)


@dataclass(frozen=True)
class GitHubActionsResult:
    annotations_emitted: int = 0
    step_summary_written: bool = False
    step_summary_path: str | None = None


@dataclass(frozen=True)
class Annotation:
    level: str
    title: str
    message: str
    path: str | None = None
    line: int | None = None


def emit_github_actions_output(
    report: RiskReport | PatchGuardReport,
    *,
    annotations: bool = False,
    step_summary: bool = False,
    max_annotations: int = 20,
    stream: TextIO | None = None,
    summary_path: str | Path | None = None,
) -> GitHubActionsResult:
    """Emit optional GitHub Actions-native output for a finished report."""

    emitted = 0
    output_stream = stream or sys.stdout
    if annotations:
        emitted = emit_annotations(
            report,
            stream=output_stream,
            max_annotations=max_annotations,
        )

    resolved_summary_path = Path(summary_path) if summary_path else _summary_path_from_env()
    written = False
    if step_summary and resolved_summary_path:
        resolved_summary_path.parent.mkdir(parents=True, exist_ok=True)
        with resolved_summary_path.open("a", encoding="utf-8") as summary_file:
            summary_file.write(render_step_summary(report))
            summary_file.write("\n")
        written = True

    return GitHubActionsResult(
        annotations_emitted=emitted,
        step_summary_written=written,
        step_summary_path=str(resolved_summary_path) if resolved_summary_path else None,
    )


def emit_annotations(
    report: RiskReport | PatchGuardReport,
    *,
    stream: TextIO | None = None,
    max_annotations: int = 20,
) -> int:
    output_stream = stream or sys.stdout
    count = 0
    for annotation in build_annotations(report):
        if count >= max_annotations:
            break
        output_stream.write(_workflow_command(annotation))
        output_stream.write("\n")
        count += 1
    return count


def build_annotations(report: RiskReport | PatchGuardReport) -> list[Annotation]:
    annotations: list[Annotation] = []
    annotations.extend(_policy_annotations(report))
    annotations.extend(_security_annotations(report.security_findings))
    annotations.extend(_generated_failure_annotations(report))
    annotations.extend(_test_run_annotations(report))
    annotations.extend(_risk_annotations(report))
    return annotations


def render_step_summary(report: RiskReport | PatchGuardReport) -> str:
    lines = [
        "# PatchGuard",
        "",
        f"**Risk:** `{report.risk_score}/100` (`{_value(report.risk_level)}`)",
        f"**Decision:** `{_value(report.merge_decision)}`",
        f"**Recommendation:** {_value(report.recommendation)}",
        f"**Policy:** `{_value(report.policy_decision.decision)}`",
        "",
    ]
    pr = report.pr
    if pr:
        url = getattr(pr, "url", None) or getattr(pr, "html_url", "")
        owner = getattr(pr, "owner", "")
        repo = getattr(pr, "repo", "")
        number = getattr(pr, "number", "")
        title = getattr(pr, "title", None) or "Untitled PR"
        additions = getattr(pr, "additions", 0)
        deletions = getattr(pr, "deletions", 0)
        lines.extend(
            [
                "## Pull Request",
                "",
                f"- **PR:** [{owner}/{repo}#{number}]({url})",
                f"- **Title:** {title}",
                f"- **Changed files:** `{len(report.changed_files)}`",
                f"- **Line delta:** `+{additions} / -{deletions}`",
                "",
            ]
        )

    lines.extend(
        [
            "## Evidence",
            "",
            f"- **Existing tests:** {_run_group_summary(_existing_runs(report))}",
            f"- **Generated tests:** {_run_group_summary(report.generated_test_results)}",
            f"- **Static/security scans:** {_run_group_summary(report.static_analysis_results)}",
            f"- **Security findings:** `{len(report.security_findings)}`",
        ]
    )
    if report.ai_review_run:
        lines.append(f"- **AI review:** `{_value(report.ai_review_run.status)}` {report.ai_review_run.summary}")
    if report.report_path:
        lines.append(f"- **Report artifact path:** `{report.report_path}`")
    lines.append("")

    if report.policy_decision.reasons:
        lines.extend(["## Policy Reasons", ""])
        lines.extend(f"- {reason}" for reason in report.policy_decision.reasons[:8])
        lines.append("")

    if report.risk_reasons:
        lines.extend(["## Top Risk Reasons", ""])
        for reason in report.risk_reasons[:8]:
            lines.append(f"- `+{reason.score_impact}` **{reason.category}:** {reason.reason}")
        lines.append("")

    if report.failure_mappings:
        lines.extend(["## Failed Generated Tests", ""])
        for mapping in report.failure_mappings[:5]:
            target = mapping.target_file or "unknown target"
            if mapping.target_function:
                target = f"{target}::{mapping.target_function}"
            lines.append(f"- `{mapping.failed_test}` -> `{target}`: {mapping.failure_summary}")
        lines.append("")

    if report.security_findings:
        lines.extend(["## Security Findings", ""])
        for finding in report.security_findings[:8]:
            location = finding.filename or finding.file or "unknown"
            if finding.line_number or finding.line:
                location = f"{location}:{finding.line_number or finding.line}"
            lines.append(
                f"- `{finding.severity}` `{location}`: "
                f"{finding.message or finding.issue_text or 'security finding'}"
            )
        lines.append("")

    if report.errors:
        lines.extend(["## Pipeline Errors", ""])
        lines.extend(f"- {error}" for error in report.errors[:8])
        lines.append("")

    return "\n".join(lines).rstrip()


def _policy_annotations(report: RiskReport | PatchGuardReport) -> list[Annotation]:
    decision = report.policy_decision
    if decision.decision == PolicyGateDecision.PASS:
        return []
    level = "error" if decision.decision == PolicyGateDecision.BLOCK else "warning"
    title = (
        "PatchGuard policy blocked this PR"
        if decision.decision == PolicyGateDecision.BLOCK
        else "PatchGuard policy warning"
    )
    message = "; ".join(decision.reasons) or f"Policy decision: {decision.decision}"
    return [Annotation(level=level, title=title, message=message)]


def _security_annotations(findings: list[SecurityFinding]) -> list[Annotation]:
    annotations: list[Annotation] = []
    for finding in findings:
        severity = finding.severity.upper()
        level = "error" if severity in {"HIGH", "CRITICAL"} else "warning" if severity == "MEDIUM" else "notice"
        path = finding.filename or finding.file
        line = finding.line_number or finding.line
        message = finding.message or finding.issue_text or "Security finding"
        annotations.append(
            Annotation(
                level=level,
                title=f"{finding.tool}: {severity} security finding",
                message=message,
                path=path,
                line=line,
            )
        )
    return annotations


def _generated_failure_annotations(report: RiskReport | PatchGuardReport) -> list[Annotation]:
    annotations: list[Annotation] = []
    for mapping in report.failure_mappings:
        annotations.append(
            Annotation(
                level="error",
                title="Generated regression test failed",
                message=f"{mapping.failure_summary} {mapping.risk_message}",
                path=mapping.target_file,
                line=_line_for_mapping(report, mapping),
            )
        )
    return annotations


def _test_run_annotations(report: RiskReport | PatchGuardReport) -> list[Annotation]:
    annotations: list[Annotation] = []
    for run in [*_existing_runs(report), *report.generated_test_results]:
        if run.status not in {RunStatus.FAILED, RunStatus.ERROR}:
            continue
        annotations.append(
            Annotation(
                level="error",
                title=f"{run.name} {run.status}",
                message=run.summary,
            )
        )
    return annotations


def _risk_annotations(report: RiskReport | PatchGuardReport) -> list[Annotation]:
    if report.risk_score < 60:
        return []
    level = "error" if report.risk_score >= 80 else "warning"
    message = "; ".join(reason.reason for reason in report.risk_reasons[:5])
    return [
        Annotation(
            level=level,
            title=f"PatchGuard risk score {report.risk_score}/100",
            message=message or "High merge risk detected.",
        )
    ]


def _line_for_mapping(
    report: RiskReport | PatchGuardReport,
    mapping: FailureMapping,
) -> int | None:
    if not mapping.target_file:
        return None
    for changed_function in report.changed_functions:
        if changed_function.file_path != mapping.target_file:
            continue
        if mapping.target_function and changed_function.qualified_name != mapping.target_function:
            continue
        return changed_function.start_line
    return None


def _existing_runs(report: RiskReport | PatchGuardReport) -> list[ToolRun]:
    if isinstance(report, RiskReport):
        return [report.existing_tests] if report.existing_tests else []
    return report.existing_test_results


def _run_group_summary(runs: list[ToolRun]) -> str:
    if not runs:
        return "`not_run` no evidence recorded"
    status = _combined_status(runs)
    summary = "; ".join(f"{run.name}: {run.summary}" for run in runs[:3])
    return f"`{_value(status)}` {summary}"


def _combined_status(runs: list[ToolRun]) -> RunStatus:
    statuses = [run.status for run in runs]
    if RunStatus.FAILED in statuses:
        return RunStatus.FAILED
    if RunStatus.ERROR in statuses:
        return RunStatus.ERROR
    if all(status == RunStatus.SKIPPED for status in statuses):
        return RunStatus.SKIPPED
    return RunStatus.PASSED


def _workflow_command(annotation: Annotation) -> str:
    properties = [f"title={_escape_property(annotation.title)}"]
    if annotation.path:
        properties.append(f"file={_escape_property(annotation.path)}")
    if annotation.line:
        properties.append(f"line={annotation.line}")
    return (
        f"::{annotation.level} {','.join(properties)}::"
        f"{_escape_data(annotation.message)}"
    )


def _summary_path_from_env() -> Path | None:
    value = os.getenv("GITHUB_STEP_SUMMARY")
    return Path(value) if value else None


def _escape_data(value: str) -> str:
    return str(value).replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")


def _escape_property(value: str) -> str:
    return (
        _escape_data(value)
        .replace(":", "%3A")
        .replace(",", "%2C")
    )


def _value(value: object) -> str:
    return str(getattr(value, "value", value))
