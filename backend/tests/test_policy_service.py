from __future__ import annotations

from patchguard.models import (
    BaseComparisonResult,
    ChangedFile,
    CommandResult,
    PatchGuardReport,
    PolicyGateDecision,
    RunStatus,
    SecurityFinding,
    ToolRun,
)
from patchguard.services.policy_service import PolicyService


def test_policy_passes_low_risk_report() -> None:
    report = PatchGuardReport(input_pr_url="https://github.com/o/r/pull/1")
    report.risk_score = 12

    decision = PolicyService().evaluate(report)

    assert decision.decision == PolicyGateDecision.PASS
    assert decision.triggered_rules == []


def test_policy_warns_near_caution_threshold() -> None:
    report = PatchGuardReport(input_pr_url="https://github.com/o/r/pull/1")
    report.risk_score = 62

    decision = PolicyService().evaluate(report)

    assert decision.decision == PolicyGateDecision.WARN
    assert decision.triggered_rules == ["risk_warning_threshold"]


def test_policy_warns_on_partial_evidence() -> None:
    report = PatchGuardReport(input_pr_url="https://github.com/o/r/pull/1")
    report.risk_score = 10
    report.generated_test_results = [
        ToolRun(
            name="run generated PatchGuard tests",
            kind="generated_tests",
            status=RunStatus.SKIPPED,
            summary="OpenAI disabled",
        )
    ]

    decision = PolicyService().evaluate(report)

    assert decision.decision == PolicyGateDecision.WARN
    assert decision.triggered_rules == ["partial_evidence"]


def test_policy_blocks_existing_test_failure() -> None:
    report = PatchGuardReport(input_pr_url="https://github.com/o/r/pull/1")
    report.risk_score = 20
    report.existing_test_results = [
        ToolRun(
            name="run existing pytest suite",
            kind="existing_tests",
            status=RunStatus.FAILED,
            summary="1 failed",
            command=CommandResult(command=["pytest"], exit_code=1, stdout_tail="1 failed"),
        )
    ]

    decision = PolicyService().evaluate(report)

    assert decision.decision == PolicyGateDecision.BLOCK
    assert "existing_test_failure" in decision.triggered_rules


def test_policy_blocks_base_vs_head_regression() -> None:
    report = PatchGuardReport(input_pr_url="https://github.com/o/r/pull/1")
    report.risk_score = 35
    report.base_comparison = BaseComparisonResult(
        enabled=True,
        status="regression",
        summary="Base passed but head failed.",
        base_tests=ToolRun(
            name="run base pytest suite",
            kind="existing_tests",
            status=RunStatus.PASSED,
            summary="base passed",
        ),
        head_tests=ToolRun(
            name="run head pytest suite",
            kind="existing_tests",
            status=RunStatus.FAILED,
            summary="head failed",
        ),
    )

    decision = PolicyService().evaluate(report)

    assert decision.decision == PolicyGateDecision.BLOCK
    assert "base_head_regression" in decision.triggered_rules


def test_policy_blocks_high_security_finding() -> None:
    report = PatchGuardReport(input_pr_url="https://github.com/o/r/pull/1")
    report.risk_score = 20
    report.security_findings = [
        SecurityFinding(
            tool="bandit",
            severity="HIGH",
            confidence="HIGH",
            filename="src/app.py",
            line_number=10,
            message="danger",
        )
    ]

    decision = PolicyService().evaluate(report)

    assert decision.decision == PolicyGateDecision.BLOCK
    assert "high_security_finding" in decision.triggered_rules


def test_policy_blocks_sensitive_source_without_tests() -> None:
    report = PatchGuardReport(input_pr_url="https://github.com/o/r/pull/1")
    report.risk_score = 20
    report.changed_files = [
        ChangedFile(
            filename="auth/session.py",
            status="modified",
            changes=5,
        )
    ]

    decision = PolicyService().evaluate(report)

    assert decision.decision == PolicyGateDecision.BLOCK
    assert "auth_code_without_tests" in decision.triggered_rules


def test_policy_loads_patchguard_yml(tmp_path) -> None:
    (tmp_path / "patchguard.yml").write_text(
        "\n".join(
            [
                "risk_threshold: 90",
                "allow_merge_with_caution_below: 50",
                "block_on:",
                "- existing_test_failure",
                "sensitive_paths:",
                "- custom_sensitive/",
            ]
        ),
        encoding="utf-8",
    )
    report = PatchGuardReport(input_pr_url="https://github.com/o/r/pull/1")
    report.risk_score = 75

    decision = PolicyService().evaluate(report, repo_dir=tmp_path)

    assert decision.decision == PolicyGateDecision.WARN
    assert decision.config_path == str(tmp_path / "patchguard.yml")
    assert decision.triggered_rules == ["risk_warning_threshold"]
