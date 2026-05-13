from __future__ import annotations

from patchguard.models import (
    ChangedFile,
    CommandResult,
    MergeRecommendation,
    PatchGuardReport,
    PullRequestInfo,
    RiskReport,
    RunStatus,
    SecurityFinding,
    ToolRun,
)
from patchguard.services.diff_service import DiffService, FileClassification
from patchguard.services.risk_score_service import RiskScoreService


def test_classifies_changed_files() -> None:
    diff_service = DiffService()

    assert diff_service.classify_file("src/app.py") == FileClassification.SOURCE
    assert diff_service.classify_file("tests/test_app.py") == FileClassification.TEST
    assert diff_service.classify_file("README.md") == FileClassification.DOCS
    assert diff_service.classify_file("pyproject.toml") == FileClassification.DEPENDENCY
    assert diff_service.classify_file(".github/workflows/ci.yml") == FileClassification.CONFIG
    assert diff_service.classify_file("src/auth/token_store.py") == (
        FileClassification.SECURITY_SENSITIVE
    )


def test_detects_source_changed_without_tests() -> None:
    summary = DiffService().summarize(
        [ChangedFile(filename="src/app.py", status="modified", changes=12)]
    )

    assert summary.source_changed_without_tests is True


def test_tests_prevent_source_without_tests_reason() -> None:
    report = _risk_report(
        [
            ChangedFile(filename="src/app.py", status="modified", changes=12),
            ChangedFile(filename="tests/test_app.py", status="modified", changes=8),
        ]
    )

    RiskScoreService().score_risk_report(report)

    assert report.risk_score == 0
    assert report.risk_level.value == "low"
    assert report.risk_reasons == []


def test_scores_source_change_without_tests() -> None:
    report = _risk_report([ChangedFile(filename="src/app.py", status="modified", changes=60)])

    RiskScoreService().score_risk_report(report)

    assert report.risk_score == 20
    assert report.risk_level.value == "low"
    assert [reason.category for reason in report.risk_reasons] == ["test_coverage"]


def test_scores_all_prompt_three_file_rules_deterministically() -> None:
    changed_files = [
        *[
            ChangedFile(filename=f"src/module_{index}.py", status="modified", changes=1)
            for index in range(11)
        ],
        ChangedFile(filename="src/auth/token_store.py", status="modified", changes=550),
        ChangedFile(filename="pyproject.toml", status="modified", changes=5),
    ]
    report = _risk_report(changed_files)

    RiskScoreService().score_risk_report(report)

    assert report.risk_score == 80
    assert report.risk_level.value == "critical"
    assert report.merge_decision.value == "do_not_merge"
    assert [reason.score_impact for reason in report.risk_reasons] == [15, 15, 20, 20, 10]
    assert {reason.category for reason in report.risk_reasons} == {
        "change_size",
        "test_coverage",
        "security_sensitive",
        "dependency_config",
    }


def test_security_findings_add_capped_risk() -> None:
    report = _risk_report(
        [ChangedFile(filename="tests/test_app.py", status="modified", changes=1)]
    )
    report.security_findings = [
        SecurityFinding(
            tool="bandit",
            severity="LOW",
            confidence="HIGH",
            filename="src/a.py",
            line_number=1,
            message="low",
            issue_text="low",
        ),
        SecurityFinding(
            tool="bandit",
            severity="MEDIUM",
            confidence="HIGH",
            filename="src/b.py",
            line_number=2,
            message="medium",
            issue_text="medium",
        ),
        SecurityFinding(
            tool="bandit",
            severity="HIGH",
            confidence="HIGH",
            filename="src/c.py",
            line_number=3,
            message="high",
            issue_text="high",
        ),
    ]

    RiskScoreService().score_risk_report(report)

    assert report.risk_score == 25
    assert report.risk_level.value == "low"
    assert report.risk_reasons[-1].category == "security"
    assert report.risk_reasons[-1].score_impact == 25


def test_failed_generated_tests_force_do_not_merge() -> None:
    report = PatchGuardReport(input_pr_url="https://github.com/o/r/pull/1")
    report.generated_test_results = [
        ToolRun(
            name="run generated PatchGuard tests",
            kind="generated_tests",
            status=RunStatus.FAILED,
            summary="pytest failed",
        )
    ]

    RiskScoreService().score(report)

    assert report.risk_score == 30
    assert report.merge_decision.value == "manual_review"
    assert report.recommendation == MergeRecommendation.REVIEW_GENERATED_FAILURES


def test_risk_report_scores_generated_test_failure_as_thirty() -> None:
    report = _risk_report([ChangedFile(filename="tests/test_app.py", status="modified", changes=1)])
    report.generated_test_results = [
        _generated_run(RunStatus.FAILED, exit_code=1, stdout="1 failed")
    ]

    RiskScoreService().score_risk_report(report)

    assert report.risk_score == 30
    assert report.risk_reasons[-1].category == "generated_tests"
    assert report.risk_reasons[-1].score_impact == 30
    assert report.recommendation == MergeRecommendation.REVIEW_GENERATED_FAILURES


def test_risk_report_scores_generated_test_error_as_fifteen() -> None:
    report = _risk_report([ChangedFile(filename="tests/test_app.py", status="modified", changes=1)])
    report.generated_test_results = [
        _generated_run(RunStatus.ERROR, exit_code=None, stdout="", timed_out=True)
    ]

    RiskScoreService().score_risk_report(report)

    assert report.risk_score == 15
    assert report.risk_reasons[-1].category == "generated_tests"
    assert report.risk_reasons[-1].score_impact == 15
    assert report.recommendation == MergeRecommendation.LIKELY_SAFE


def test_risk_report_scores_generated_test_pass_as_zero() -> None:
    report = _risk_report([ChangedFile(filename="tests/test_app.py", status="modified", changes=1)])
    report.generated_test_results = [
        _generated_run(RunStatus.PASSED, exit_code=0, stdout="1 passed")
    ]

    RiskScoreService().score_risk_report(report)

    assert report.risk_score == 0
    assert report.risk_reasons == []
    assert report.recommendation == MergeRecommendation.LIKELY_SAFE


def test_recommendation_prioritizes_existing_test_failures() -> None:
    report = _risk_report([ChangedFile(filename="tests/test_app.py", status="modified", changes=1)])
    report.existing_tests = ToolRun(
        name="run existing pytest suite",
        kind="existing_tests",
        status=RunStatus.FAILED,
        summary="pytest tests failed",
    )

    RiskScoreService().score_risk_report(report)

    assert report.recommendation == MergeRecommendation.DO_NOT_MERGE_EXISTING_TESTS


def test_recommendation_blocks_high_security_findings() -> None:
    report = _risk_report([ChangedFile(filename="tests/test_app.py", status="modified", changes=1)])
    report.security_findings = [
        SecurityFinding(
            tool="bandit",
            severity="HIGH",
            confidence="HIGH",
            filename="src/a.py",
            line_number=1,
            message="high",
        )
    ]

    RiskScoreService().score_risk_report(report)

    assert report.recommendation == MergeRecommendation.DO_NOT_MERGE_SECURITY


def _risk_report(changed_files: list[ChangedFile]) -> RiskReport:
    return RiskReport(
        pr=PullRequestInfo(
            owner="owner",
            repo="repo",
            number=1,
            url="https://github.com/owner/repo/pull/1",
        ),
        changed_files=changed_files,
    )


def _generated_run(
    status: RunStatus,
    *,
    exit_code: int | None,
    stdout: str,
    timed_out: bool = False,
) -> ToolRun:
    return ToolRun(
        name="run generated PatchGuard tests",
        kind="generated_tests",
        status=status,
        summary="generated pytest evidence",
        command=CommandResult(
            command=["python", "-m", "pytest", "-q", ".patchguard/generated_tests"],
            exit_code=exit_code,
            stdout_tail=stdout,
            timed_out=timed_out,
        ),
    )
