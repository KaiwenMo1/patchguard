"""Markdown rendering for PatchGuard reports."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from patchguard.models import PatchGuardReport, RiskReport, ToolRun
from patchguard.utils.file_utils import ensure_dir


def write_markdown_report(report: RiskReport | PatchGuardReport, path: str | Path) -> Path:
    output_path = Path(path)
    ensure_dir(output_path.parent)
    report.report_path = str(output_path)
    output_path.write_text(render_markdown_report(report) + "\n", encoding="utf-8")
    return output_path


def render_markdown_report(report: RiskReport | PatchGuardReport) -> str:
    pr = report.pr
    lines: list[str] = [
        "# PatchGuard Report",
        "",
        f"**Status:** `{_value(report.status)}`",
        f"**Risk:** `{report.risk_score}/100` (`{_value(report.risk_level)}`)",
        f"**Decision:** `{_value(report.merge_decision)}`",
        f"**Recommendation:** {escape_markdown(_value(report.recommendation))}",
        "",
    ]

    if pr is not None:
        lines.extend(_pr_section(pr))
    if report.errors:
        lines.extend(_list_section("Pipeline Errors", report.errors))
    lines.extend(_policy_section(report))
    lines.extend(_behavioral_contract_section(report))
    lines.extend(_changed_files_section(report.changed_files))
    lines.extend(_risk_reasons_section(report.risk_reasons))
    lines.extend(_run_section("Existing Tests", _existing_test_runs(report)))
    lines.extend(_run_section("Generated Tests", report.generated_test_results))
    lines.extend(_failure_mappings_section(report.failure_mappings))
    lines.extend(_run_section("Static Analysis", report.static_analysis_results))
    lines.extend(_security_section(report.security_findings))
    lines.extend(_generated_tests_section(report.generated_tests))
    return "\n".join(lines).rstrip()


def _pr_section(pr: Any) -> list[str]:
    owner = getattr(pr, "owner", "")
    repo = getattr(pr, "repo", "")
    number = getattr(pr, "number", "")
    url = getattr(pr, "url", None) or getattr(pr, "html_url", "")
    title = getattr(pr, "title", None) or "Untitled PR"
    additions = getattr(pr, "additions", 0)
    deletions = getattr(pr, "deletions", 0)
    changed_files = getattr(pr, "changed_files_count", 0)
    base_ref = getattr(pr, "base_ref", None) or "unknown"
    head_ref = getattr(pr, "head_ref", None) or "unknown"
    author = getattr(pr, "author", None) or "unknown"
    state = getattr(pr, "state", None) or "unknown"
    return [
        "## Pull Request",
        "",
        f"- **Title:** {escape_markdown(title)}",
        f"- **Repository:** `{owner}/{repo}`",
        f"- **PR:** [#{number}]({url})",
        f"- **Author:** `{author}`",
        f"- **State:** `{state}`",
        f"- **Base / Head:** `{base_ref}` -> `{head_ref}`",
        f"- **Changed files:** `{changed_files}`",
        f"- **Line delta:** `+{additions} / -{deletions}`",
        "",
    ]


def _changed_files_section(files: list[Any]) -> list[str]:
    lines = [
        "## Changed Files",
        "",
        "| File | Type | Status | + | - |",
        "| --- | --- | --- | ---: | ---: |",
    ]
    if not files:
        return ["## Changed Files", "", "No changed files were reported.", ""]
    for file in files:
        lines.append(
            "| "
            f"`{escape_markdown(getattr(file, 'filename', 'unknown'))}` | "
            f"{escape_markdown(str(getattr(file, 'classification', None) or 'unknown'))} | "
            f"{escape_markdown(str(getattr(file, 'status', 'unknown')))} | "
            f"{getattr(file, 'additions', 0)} | "
            f"{getattr(file, 'deletions', 0)} |"
        )
    lines.append("")
    return lines


def _risk_reasons_section(reasons: list[Any]) -> list[str]:
    if not reasons:
        return ["## Risk Reasons", "", "No risk reasons were recorded.", ""]
    lines = ["## Risk Reasons", ""]
    for reason in reasons:
        lines.append(
            f"- `+{reason.score_impact}` **{escape_markdown(reason.category)}:** "
            f"{escape_markdown(reason.reason)}"
        )
    lines.append("")
    return lines


def _policy_section(report: RiskReport | PatchGuardReport) -> list[str]:
    decision = report.policy_decision
    lines = [
        "## Policy Gate",
        "",
        f"- **Decision:** `{_value(decision.decision)}`",
        f"- **Triggered rules:** `{', '.join(decision.triggered_rules) or 'none'}`",
    ]
    if decision.config_path:
        lines.append(f"- **Config:** `{escape_markdown(decision.config_path)}`")
    if decision.reasons:
        lines.append("")
        lines.extend(f"- {escape_markdown(reason)}" for reason in decision.reasons)
    lines.append("")
    return lines


def _behavioral_contract_section(report: RiskReport | PatchGuardReport) -> list[str]:
    contract = report.behavioral_contract
    run = report.contract_extraction
    lines = [
        "## Behavioral Contract",
        "",
        f"- **Extraction:** `{_value(run.status) if run else 'not_run'}`",
        f"- **Confidence:** `{contract.confidence:.2f}`",
    ]
    if run:
        lines.append(f"- **Summary:** {escape_markdown(run.summary)}")
    sections = [
        ("Intended new behavior", contract.intended_new_behaviors),
        ("Behavior to preserve", contract.existing_behaviors_to_preserve),
        ("Edge cases", contract.edge_cases_to_test),
        ("Invalid inputs", contract.invalid_inputs_to_test),
        ("Uncertainties", contract.contract_uncertainties),
    ]
    for title, values in sections:
        lines.extend(["", f"**{title}:**"])
        if values:
            lines.extend(f"- {escape_markdown(value)}" for value in values)
        else:
            lines.append("- none recorded")
    lines.append("")
    return lines


def _failure_mappings_section(mappings: list[Any]) -> list[str]:
    if not mappings:
        return ["## Failed Generated Test Mappings", "", "No generated test failures were mapped.", ""]
    lines = [
        "## Failed Generated Test Mappings",
        "",
        "| Failed test | Target | Behavior | Failure | Next step |",
        "| --- | --- | --- | --- | --- |",
    ]
    for mapping in mappings:
        target = (
            f"{mapping.target_file or 'unknown'}::{mapping.target_function}"
            if mapping.target_function
            else mapping.target_file or "unknown"
        )
        lines.append(
            "| "
            f"`{escape_markdown(mapping.failed_test)}` | "
            f"`{escape_markdown(target)}` | "
            f"{escape_markdown(mapping.behavior_checked or 'unknown')} | "
            f"{escape_markdown(mapping.failure_summary)} | "
            f"{escape_markdown(mapping.suggested_next_step)} |"
        )
    lines.append("")
    return lines


def _run_section(title: str, runs: list[ToolRun]) -> list[str]:
    if not runs:
        return [f"## {title}", "", "No run evidence was recorded.", ""]
    lines = [
        f"## {title}",
        "",
        "| Step | Status | Summary |",
        "| --- | --- | --- |",
    ]
    for run in runs:
        lines.append(
            "| "
            f"{escape_markdown(run.name)} | "
            f"`{_value(run.status)}` | "
            f"{escape_markdown(run.summary)} |"
        )
    lines.append("")
    return lines


def _security_section(findings: list[Any]) -> list[str]:
    if not findings:
        return ["## Security Findings", "", "No security findings were recorded.", ""]
    lines = [
        "## Security Findings",
        "",
        "| Tool | Severity | Confidence | Location | Message |",
        "| --- | --- | --- | --- | --- |",
    ]
    for finding in findings:
        filename = getattr(finding, "filename", None) or getattr(finding, "file", None) or "unknown"
        line_number = getattr(finding, "line_number", None) or getattr(finding, "line", None) or "?"
        message = getattr(finding, "message", None) or getattr(finding, "issue_text", None) or ""
        lines.append(
            "| "
            f"{escape_markdown(getattr(finding, 'tool', 'unknown'))} | "
            f"`{escape_markdown(getattr(finding, 'severity', 'unknown'))}` | "
            f"{escape_markdown(str(getattr(finding, 'confidence', None) or 'n/a'))} | "
            f"`{escape_markdown(filename)}:{line_number}` | "
            f"{escape_markdown(message)} |"
        )
    lines.append("")
    return lines


def _generated_tests_section(generated_tests: list[Any]) -> list[str]:
    if not generated_tests:
        return ["## Generated Test Code", "", "No generated test code is attached.", ""]
    lines = ["## Generated Test Code", ""]
    for test in generated_tests:
        lines.extend(
            [
                f"### `{escape_markdown(test.path)}`",
                "",
                "```python",
                test.code.rstrip(),
                "```",
                "",
            ]
        )
    return lines


def _list_section(title: str, values: list[str]) -> list[str]:
    lines = [f"## {title}", ""]
    lines.extend(f"- {escape_markdown(value)}" for value in values)
    lines.append("")
    return lines


def _existing_test_runs(report: RiskReport | PatchGuardReport) -> list[ToolRun]:
    if isinstance(report, RiskReport):
        return [report.existing_tests] if report.existing_tests else []
    return report.existing_test_results


def escape_markdown(value: str) -> str:
    return str(value).replace("|", "\\|")


def _value(value: Any) -> str:
    return str(getattr(value, "value", value))
