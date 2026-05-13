"""Deterministic merge-risk scoring."""

from __future__ import annotations

from patchguard.models import (
    MergeDecision,
    MergeRecommendation,
    PatchGuardReport,
    RiskLevel,
    RiskReason,
    RiskReport,
    RunStatus,
)
from patchguard.services.diff_service import DiffService


class RiskScoreService:
    """Compute an explainable deterministic score from collected evidence."""

    def __init__(self, diff_service: DiffService | None = None) -> None:
        self.diff_service = diff_service or DiffService()

    def score_risk_report(self, report: RiskReport) -> RiskReport:
        score, level, reasons = self.compute_file_risk(report.changed_files)
        if report.existing_tests:
            if report.existing_tests.status == RunStatus.FAILED:
                score += 40
                reasons.append(
                    RiskReason(
                        category="existing_tests",
                        score_impact=40,
                        reason="Existing pytest suite failed",
                    )
                )
            elif report.existing_tests.status == RunStatus.ERROR:
                score += 40
                reasons.append(
                    RiskReason(
                        category="existing_tests",
                        score_impact=40,
                        reason="Existing pytest suite errored or timed out",
                    )
                )
        if report.dependency_install and report.dependency_install.status == RunStatus.FAILED:
            score += 10
            reasons.append(
                RiskReason(
                    category="dependencies",
                    score_impact=10,
                    reason="Dependency installation failed in sandbox",
                )
            )
        elif report.dependency_install and report.dependency_install.status == RunStatus.ERROR:
            score += 10
            reasons.append(
                RiskReason(
                    category="dependencies",
                    score_impact=10,
                    reason="Dependency installation errored or timed out in sandbox",
                )
            )
        for run in report.generated_test_results:
            generated_impact = self._generated_test_impact(run)
            if generated_impact:
                score += generated_impact
                reasons.append(
                    RiskReason(
                        category="generated_tests",
                        score_impact=generated_impact,
                        reason=self._generated_test_reason(run),
                    )
                )
        security_score = self._security_score(report.security_findings)
        if security_score:
            score += security_score
            reasons.append(
                RiskReason(
                    category="security",
                    score_impact=security_score,
                    reason=f"Bandit reported {len(report.security_findings)} security finding(s)",
                )
            )
        score = max(0, min(100, score))
        level = self._level(score)
        report.risk_score = score
        report.risk_level = level
        report.risk_reasons = reasons
        report.merge_decision = self._decision_for_score(score)
        report.recommendation = self._recommendation_for_risk_report(report)
        return report

    def compute_file_risk(self, changed_files) -> tuple[int, RiskLevel, list[RiskReason]]:
        reasons: list[RiskReason] = []
        score = 0
        diff_summary = self.diff_service.summarize(changed_files)

        def add(category: str, impact: int, reason: str) -> None:
            nonlocal score
            score += impact
            reasons.append(RiskReason(category=category, score_impact=impact, reason=reason))

        changed_count = len(changed_files)
        if changed_count > 10:
            add("change_size", 15, f"More than 10 files changed ({changed_count})")

        if diff_summary.total_changes > 500:
            add(
                "change_size",
                15,
                f"More than 500 total lines changed ({diff_summary.total_changes})",
            )

        if diff_summary.source_changed_without_tests:
            add("test_coverage", 20, "Source files changed without test files changing")

        if diff_summary.security_sensitive_files:
            names = ", ".join(file.filename for file in diff_summary.security_sensitive_files[:3])
            add("security_sensitive", 20, f"Security-sensitive file changed: {names}")

        dependency_or_config = [*diff_summary.dependency_files, *diff_summary.config_files]
        if dependency_or_config:
            names = ", ".join(file.filename for file in dependency_or_config[:3])
            add("dependency_config", 10, f"Dependency or config file changed: {names}")

        score = max(0, min(100, score))
        return score, self._level(score), reasons

    def score(self, report: PatchGuardReport) -> PatchGuardReport:
        reasons: list[RiskReason] = []
        score = 0

        def add(category: str, impact: int, reason: str) -> None:
            nonlocal score
            score += impact
            reasons.append(RiskReason(category=category, score_impact=impact, reason=reason))

        file_score, _, file_reasons = self.compute_file_risk(report.changed_files)
        score += file_score
        reasons.extend(file_reasons)

        removed_files = [file for file in report.changed_files if file.status == "removed"]
        if removed_files:
            add("deletions", min(15, 5 + len(removed_files)), f"{len(removed_files)} file(s) removed")

        for run in report.existing_test_results:
            if run.status == RunStatus.FAILED:
                add("existing_tests", 25, f"Existing test command failed: {run.name}")
            elif run.status == RunStatus.ERROR:
                add("existing_tests", 20, f"Existing test command errored: {run.name}")
            elif run.status == RunStatus.SKIPPED:
                add("existing_tests", 8, f"Existing tests skipped: {run.summary}")

        for run in report.generated_test_results:
            generated_impact = self._generated_test_impact(run)
            if generated_impact:
                add("generated_tests", generated_impact, self._generated_test_reason(run))

        for run in report.sandbox_results:
            if run.kind == "dependency_install" and run.status == RunStatus.FAILED:
                add("dependencies", 10, "Dependency installation failed in sandbox")
            elif run.kind == "dependency_install" and run.status == RunStatus.SKIPPED:
                add("dependencies", 5, f"Dependency installation skipped: {run.summary}")
            elif run.kind == "docker_build" and run.status == RunStatus.FAILED:
                add("sandbox", 15, "Docker sandbox image build failed")
            elif run.status == RunStatus.ERROR:
                add("sandbox", 15, f"Sandbox step errored: {run.name}")

        for run in report.static_analysis_results:
            if run.status == RunStatus.SKIPPED:
                add("static_analysis", 5, f"Static analysis skipped: {run.name}")

        if report.static_findings:
            impact = min(12, 3 + len(report.static_findings))
            add("static_analysis", impact, f"ruff reported {len(report.static_findings)} finding(s)")

        security_score = self._security_score(report.security_findings)
        if security_score:
            add(
                "security",
                security_score,
                f"Bandit reported {len(report.security_findings)} security finding(s)",
            )

        if report.errors:
            add("pipeline", min(20, 5 * len(report.errors)), "Pipeline produced partial evidence")

        report.risk_score = max(0, min(100, score))
        report.risk_reasons = reasons
        report.risk_level = self._level(report.risk_score)
        report.merge_decision = self._decision(report)
        report.recommendation = self._recommendation(report)
        return report

    @staticmethod
    def _level(score: int) -> RiskLevel:
        if score >= 80:
            return RiskLevel.CRITICAL
        if score >= 60:
            return RiskLevel.HIGH
        if score >= 30:
            return RiskLevel.MEDIUM
        return RiskLevel.LOW

    @staticmethod
    def _decision_for_score(score: int) -> MergeDecision:
        if score >= 80:
            return MergeDecision.DO_NOT_MERGE
        if score >= 60:
            return MergeDecision.MANUAL_REVIEW
        if score >= 30:
            return MergeDecision.MERGE_WITH_CAUTION
        return MergeDecision.MERGE

    @staticmethod
    def _security_score(findings) -> int:
        score = 0
        for finding in findings:
            severity = finding.severity.upper()
            if severity == "HIGH":
                score += 20
            elif severity == "MEDIUM":
                score += 10
            else:
                score += 5
        return min(score, 25)

    @staticmethod
    def _generated_test_impact(run) -> int:
        if run.name == "run generated PatchGuard tests":
            if run.status == RunStatus.FAILED:
                return 30
            if run.status == RunStatus.ERROR:
                return 15
        if run.name == "compile generated PatchGuard tests" and run.status in {
            RunStatus.FAILED,
            RunStatus.ERROR,
        }:
            return 15
        return 0

    @staticmethod
    def _generated_test_reason(run) -> str:
        if run.name == "run generated PatchGuard tests" and run.status == RunStatus.FAILED:
            return "Generated tests failed"
        return f"Generated tests errored or timed out: {run.name}"

    @staticmethod
    def _decision(report: PatchGuardReport) -> MergeDecision:
        has_failed_existing_tests = any(
            run.status in {RunStatus.FAILED, RunStatus.ERROR}
            for run in report.existing_test_results
        )
        has_failed_generated_tests = any(
            run.name == "run generated PatchGuard tests" and run.status == RunStatus.FAILED
            for run in report.generated_test_results
        )
        has_high_security = any(
            finding.severity.upper() == "HIGH" for finding in report.security_findings
        )
        has_skipped_evidence = any(
            run.status == RunStatus.SKIPPED
            for run in [
                *report.sandbox_results,
                *report.existing_test_results,
                *report.generated_test_results,
                *report.static_analysis_results,
            ]
        )
        if report.risk_score >= 80 or has_failed_existing_tests or has_high_security:
            return MergeDecision.DO_NOT_MERGE
        if has_failed_generated_tests:
            return MergeDecision.MANUAL_REVIEW
        if report.risk_score >= 55 or report.errors or has_skipped_evidence:
            return MergeDecision.MANUAL_REVIEW
        if report.risk_score >= 30:
            return MergeDecision.MERGE_WITH_CAUTION
        return MergeDecision.MERGE

    @staticmethod
    def _recommendation_for_risk_report(report: RiskReport) -> MergeRecommendation:
        if report.existing_tests and report.existing_tests.status in {RunStatus.FAILED, RunStatus.ERROR}:
            return MergeRecommendation.DO_NOT_MERGE_EXISTING_TESTS
        if any(
            run.name == "run generated PatchGuard tests" and run.status == RunStatus.FAILED
            for run in report.generated_test_results
        ):
            return MergeRecommendation.REVIEW_GENERATED_FAILURES
        if any(finding.severity.upper() == "HIGH" for finding in report.security_findings):
            return MergeRecommendation.DO_NOT_MERGE_SECURITY
        if report.risk_level in {RiskLevel.HIGH, RiskLevel.CRITICAL}:
            return MergeRecommendation.HUMAN_REVIEW
        return MergeRecommendation.LIKELY_SAFE

    @staticmethod
    def _recommendation(report: PatchGuardReport) -> MergeRecommendation:
        if any(run.status in {RunStatus.FAILED, RunStatus.ERROR} for run in report.existing_test_results):
            return MergeRecommendation.DO_NOT_MERGE_EXISTING_TESTS
        if any(
            run.name == "run generated PatchGuard tests" and run.status == RunStatus.FAILED
            for run in report.generated_test_results
        ):
            return MergeRecommendation.REVIEW_GENERATED_FAILURES
        if any(finding.severity.upper() == "HIGH" for finding in report.security_findings):
            return MergeRecommendation.DO_NOT_MERGE_SECURITY
        if report.risk_level in {RiskLevel.HIGH, RiskLevel.CRITICAL}:
            return MergeRecommendation.HUMAN_REVIEW
        return MergeRecommendation.LIKELY_SAFE
