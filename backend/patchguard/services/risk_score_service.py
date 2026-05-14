"""Deterministic merge-risk scoring."""

from __future__ import annotations

import re
from collections.abc import Iterable

from patchguard.models import (
    ChangedFile,
    MergeDecision,
    MergeRecommendation,
    PatchGuardReport,
    RiskBreakdown,
    RiskInput,
    RiskLevel,
    RiskReason,
    RiskReport,
    RunStatus,
    SecurityFinding,
    SecurityFindingCounts,
    ToolRun,
)
from patchguard.services.diff_service import DiffService


class RiskScoreService:
    """Compute an explainable deterministic score from collected evidence."""

    def __init__(self, diff_service: DiffService | None = None) -> None:
        self.diff_service = diff_service or DiffService()

    def score_risk_report(self, report: RiskReport) -> RiskReport:
        risk_input = self.input_from_risk_report(report)
        breakdown = self.compute_breakdown(risk_input)
        self._apply_breakdown(report, breakdown)
        report.merge_decision = self._decision_for_risk_input(risk_input, breakdown.overall_score)
        report.recommendation = self._recommendation_for_risk_report(report)
        return report

    def score(self, report: PatchGuardReport) -> PatchGuardReport:
        risk_input = self.input_from_patch_guard_report(report)
        breakdown = self.compute_breakdown(risk_input)
        self._apply_breakdown(report, breakdown)
        report.merge_decision = self._decision_for_risk_input(risk_input, breakdown.overall_score)
        report.recommendation = self._recommendation(report)
        return report

    def input_from_risk_report(self, report: RiskReport) -> RiskInput:
        diff_summary = self.diff_service.summarize(report.changed_files)
        existing_runs = [report.existing_tests] if report.existing_tests else []
        dependency_runs = [report.dependency_install] if report.dependency_install else []
        return self._build_input(
            changed_files=report.changed_files,
            changed_functions_count=len(report.changed_functions),
            source_changed=bool(diff_summary.source_files),
            tests_changed=bool(diff_summary.test_files),
            dependency_files_changed=bool(diff_summary.dependency_files),
            config_files_changed=bool(diff_summary.config_files),
            security_sensitive_files_changed=bool(diff_summary.security_sensitive_files),
            existing_test_runs=existing_runs,
            generated_test_runs=report.generated_test_results,
            dependency_runs=dependency_runs,
            security_findings=report.security_findings,
            behavior_confidence=self._contract_confidence(report),
        )

    def input_from_patch_guard_report(self, report: PatchGuardReport) -> RiskInput:
        diff_summary = self.diff_service.summarize(report.changed_files)
        dependency_runs = [
            run for run in report.sandbox_results if run.kind == "dependency_install"
        ]
        return self._build_input(
            changed_files=report.changed_files,
            changed_functions_count=len(report.changed_functions),
            source_changed=bool(diff_summary.source_files),
            tests_changed=bool(diff_summary.test_files),
            dependency_files_changed=bool(diff_summary.dependency_files),
            config_files_changed=bool(diff_summary.config_files),
            security_sensitive_files_changed=bool(diff_summary.security_sensitive_files),
            existing_test_runs=report.existing_test_results,
            generated_test_runs=report.generated_test_results,
            dependency_runs=dependency_runs,
            security_findings=report.security_findings,
            behavior_confidence=self._contract_confidence(report),
        )

    def compute_breakdown(self, risk_input: RiskInput) -> RiskBreakdown:
        reasons: list[RiskReason] = []
        change_size = self._change_size_risk(risk_input, reasons)
        test_coverage = self._test_coverage_risk(risk_input, reasons)
        behavioral = self._behavioral_risk(risk_input, reasons)
        security = self._security_risk(risk_input, reasons)
        uncertainty = self._uncertainty_risk(risk_input, reasons)
        weighted_score = round(
            0.15 * change_size
            + 0.30 * test_coverage
            + 0.25 * behavioral
            + 0.20 * security
            + 0.10 * uncertainty
        )
        floor_score, floor_reason = self._minimum_score_floor(risk_input)
        overall_score = max(weighted_score, floor_score)
        if floor_reason and floor_score > weighted_score:
            reasons.append(
                RiskReason(
                    category="risk_floor",
                    score_impact=floor_score - weighted_score,
                    reason=floor_reason,
                    severity=self._severity_for_score(floor_score),
                    evidence=[f"weighted_score={weighted_score}", f"floor_score={floor_score}"],
                )
            )
        overall_score = self._clamp(overall_score)
        return RiskBreakdown(
            overall_score=overall_score,
            risk_level=self._level(overall_score),
            change_size_risk=change_size,
            test_coverage_risk=test_coverage,
            behavioral_risk=behavioral,
            security_risk=security,
            uncertainty_risk=uncertainty,
            reasons=reasons,
        )

    def _build_input(
        self,
        *,
        changed_files: list[ChangedFile],
        changed_functions_count: int,
        source_changed: bool,
        tests_changed: bool,
        dependency_files_changed: bool,
        config_files_changed: bool,
        security_sensitive_files_changed: bool,
        existing_test_runs: list[ToolRun],
        generated_test_runs: list[ToolRun],
        dependency_runs: list[ToolRun],
        security_findings: list[SecurityFinding],
        behavior_confidence: float | None = None,
    ) -> RiskInput:
        existing_status = self._combined_status(existing_test_runs)
        generated_status = self._combined_status(
            [
                run
                for run in generated_test_runs
                if run.name == "run generated PatchGuard tests"
                or run.status in {RunStatus.FAILED, RunStatus.ERROR}
            ]
        )
        return RiskInput(
            changed_files_count=len(changed_files),
            total_lines_changed=sum(file.changes for file in changed_files),
            changed_functions_count=changed_functions_count,
            source_changed=source_changed,
            tests_changed=tests_changed,
            dependency_files_changed=dependency_files_changed,
            config_files_changed=config_files_changed,
            security_sensitive_files_changed=security_sensitive_files_changed,
            existing_tests_status=existing_status,
            generated_tests_status=generated_status,
            existing_tests_failed_count=self._failed_count(existing_test_runs),
            generated_tests_failed_count=self._failed_count(generated_test_runs),
            security_findings_by_severity=self._security_counts(security_findings),
            secrets_detected=self._secrets_detected(security_findings),
            dependency_install_failed=any(
                run.status in {RunStatus.FAILED, RunStatus.ERROR}
                for run in dependency_runs
            ),
            no_existing_tests_found=self._no_existing_tests_found(existing_test_runs),
            diff_too_large_for_full_analysis=self._diff_too_large(changed_files),
            behavior_changed=source_changed,
            behavior_confidence=behavior_confidence,
        )

    def _change_size_risk(self, risk_input: RiskInput, reasons: list[RiskReason]) -> int:
        score = 0
        if risk_input.changed_files_count > 20:
            score += 70
            self._reason(
                reasons,
                "change_size",
                70,
                f"Large PR changed {risk_input.changed_files_count} files",
                "high",
            )
        elif risk_input.changed_files_count > 10:
            score += 45
            self._reason(
                reasons,
                "change_size",
                45,
                f"More than 10 files changed ({risk_input.changed_files_count})",
                "medium",
            )
        if risk_input.total_lines_changed > 1000:
            score += 60
            self._reason(
                reasons,
                "change_size",
                60,
                f"More than 1000 total lines changed ({risk_input.total_lines_changed})",
                "high",
            )
        elif risk_input.total_lines_changed > 500:
            score += 45
            self._reason(
                reasons,
                "change_size",
                45,
                f"More than 500 total lines changed ({risk_input.total_lines_changed})",
                "medium",
            )
        if risk_input.changed_functions_count > 20:
            score += 35
            self._reason(
                reasons,
                "change_size",
                35,
                f"{risk_input.changed_functions_count} changed functions/classes",
                "medium",
            )
        elif risk_input.changed_functions_count > 10:
            score += 20
            self._reason(
                reasons,
                "change_size",
                20,
                f"{risk_input.changed_functions_count} changed functions/classes",
                "low",
            )
        return self._clamp(score)

    def _test_coverage_risk(self, risk_input: RiskInput, reasons: list[RiskReason]) -> int:
        score = 0
        if risk_input.existing_tests_status == "failed":
            score += 100
            self._reason(
                reasons,
                "existing_tests",
                100,
                "Existing pytest suite failed",
                "critical",
                [f"failed_count={risk_input.existing_tests_failed_count}"],
            )
        elif risk_input.existing_tests_status == "error":
            score += 85
            self._reason(
                reasons,
                "existing_tests",
                85,
                "Existing pytest suite errored or timed out",
                "high",
            )
        elif risk_input.existing_tests_status in {"skipped", "not_run"} and risk_input.source_changed:
            score += 35
            self._reason(
                reasons,
                "existing_tests",
                35,
                "Existing tests did not produce pass/fail evidence",
                "medium",
                [f"status={risk_input.existing_tests_status}"],
            )

        if risk_input.generated_tests_status == "failed":
            score += 100
            self._reason(
                reasons,
                "generated_tests",
                100,
                "Generated regression tests failed",
                "critical",
                [f"failed_count={risk_input.generated_tests_failed_count}"],
            )
        elif risk_input.generated_tests_status == "error":
            score += 65
            self._reason(
                reasons,
                "generated_tests",
                65,
                "Generated regression tests errored or timed out",
                "high",
            )

        if risk_input.source_changed and not risk_input.tests_changed:
            score += 80
            self._reason(
                reasons,
                "test_coverage",
                80,
                "Source files changed without test files changing",
                "high",
            )
        if risk_input.no_existing_tests_found:
            score += 45
            self._reason(
                reasons,
                "test_coverage",
                45,
                "No existing pytest tests were discovered",
                "medium",
            )
        return self._clamp(score)

    def _behavioral_risk(self, risk_input: RiskInput, reasons: list[RiskReason]) -> int:
        score = 0
        if risk_input.generated_tests_status == "failed":
            score += 100
            self._reason(
                reasons,
                "behavioral",
                100,
                "Generated tests found behavior that does not match expectations",
                "critical",
            )
        elif risk_input.existing_tests_status == "failed":
            score += 90
            self._reason(
                reasons,
                "behavioral",
                90,
                "Existing tests indicate behavior changed unexpectedly",
                "critical",
            )
        elif risk_input.source_changed:
            score += 30
            self._reason(
                reasons,
                "behavioral",
                30,
                "Python source behavior changed",
                "low",
            )
        if risk_input.security_sensitive_files_changed:
            score += 85
            self._reason(
                reasons,
                "behavioral",
                85,
                "Security-sensitive code path changed",
                "high",
            )
        if risk_input.dependency_files_changed:
            score += 35
            self._reason(
                reasons,
                "behavioral",
                35,
                "Dependency files changed",
                "medium",
            )
        if risk_input.config_files_changed:
            score += 25
            self._reason(
                reasons,
                "behavioral",
                25,
                "Configuration files changed",
                "low",
            )
        for category in risk_input.behavior_risky_categories:
            impact = self._behavior_category_impact(category)
            if impact:
                score += impact
                self._reason(
                    reasons,
                    "behavioral",
                    impact,
                    f"Behavior analysis flagged {category}",
                    self._severity_for_score(impact),
                )
        return self._clamp(score)

    def _security_risk(self, risk_input: RiskInput, reasons: list[RiskReason]) -> int:
        counts = risk_input.security_findings_by_severity
        score = 0
        if counts.critical:
            score += 100
            self._reason(reasons, "security", 100, f"{counts.critical} critical security finding(s)", "critical")
        if counts.high:
            impact = min(100, counts.high * 80)
            score += impact
            self._reason(reasons, "security", impact, f"{counts.high} high severity security finding(s)", "critical")
        if counts.medium:
            impact = min(80, counts.medium * 45)
            score += impact
            self._reason(reasons, "security", impact, f"{counts.medium} medium severity security finding(s)", "high")
        if counts.low:
            impact = min(50, counts.low * 15)
            score += impact
            self._reason(reasons, "security", impact, f"{counts.low} low severity security finding(s)", "medium")
        if risk_input.security_sensitive_files_changed:
            score += 35
            self._reason(reasons, "security", 35, "Security-sensitive filename changed", "medium")
        if risk_input.secrets_detected:
            score += 100
            self._reason(reasons, "security", 100, "Potential secret detected by security scan", "critical")
        return self._clamp(score)

    def _uncertainty_risk(self, risk_input: RiskInput, reasons: list[RiskReason]) -> int:
        score = 0
        if risk_input.dependency_install_failed:
            score += 65
            self._reason(
                reasons,
                "dependencies",
                65,
                "Dependency installation failed, limiting test evidence",
                "high",
            )
        if risk_input.existing_tests_status in {"skipped", "not_run"} and risk_input.source_changed:
            score += 40
            self._reason(
                reasons,
                "uncertainty",
                40,
                "Existing test evidence is missing",
                "medium",
                [f"status={risk_input.existing_tests_status}"],
            )
        if risk_input.generated_tests_status in {"skipped", "not_run"} and risk_input.source_changed:
            score += 25
            self._reason(
                reasons,
                "uncertainty",
                25,
                "Generated regression tests did not run",
                "low",
                [f"status={risk_input.generated_tests_status}"],
            )
        if risk_input.no_existing_tests_found:
            score += 45
            self._reason(reasons, "uncertainty", 45, "No test suite was discovered", "medium")
        if risk_input.pr_description_missing:
            score += 15
            self._reason(reasons, "uncertainty", 15, "PR description is missing", "low")
        if risk_input.diff_too_large_for_full_analysis:
            score += 60
            self._reason(reasons, "uncertainty", 60, "Diff was too large for complete line-level analysis", "high")
        if risk_input.behavior_confidence is not None and risk_input.behavior_confidence < 0.4:
            score += 20
            self._reason(reasons, "uncertainty", 20, "Behavior analysis confidence was low", "low")
        return self._clamp(score)

    def _minimum_score_floor(self, risk_input: RiskInput) -> tuple[int, str | None]:
        counts = risk_input.security_findings_by_severity
        if risk_input.secrets_detected:
            return 90, "Secret-like security finding requires critical risk floor"
        if counts.critical or counts.high:
            return 75, "High-severity security evidence requires high risk floor"
        if risk_input.existing_tests_status == "failed":
            return 80, "Failing existing tests require critical risk floor"
        if risk_input.generated_tests_status == "failed":
            return 70, "Failing generated regression tests require high risk floor"
        if (
            risk_input.security_sensitive_files_changed
            and risk_input.source_changed
            and not risk_input.tests_changed
        ):
            return 70, "Security-sensitive source changed without test changes"
        return 0, None

    @staticmethod
    def _apply_breakdown(
        report: RiskReport | PatchGuardReport,
        breakdown: RiskBreakdown,
    ) -> None:
        report.risk_score = breakdown.overall_score
        report.risk_level = breakdown.risk_level
        report.risk_breakdown = breakdown
        report.risk_reasons = breakdown.reasons

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
    def _decision_for_risk_input(risk_input: RiskInput, score: int) -> MergeDecision:
        counts = risk_input.security_findings_by_severity
        if (
            score >= 80
            or risk_input.existing_tests_status in {"failed", "error"}
            or counts.critical
            or counts.high
            or risk_input.secrets_detected
        ):
            return MergeDecision.DO_NOT_MERGE
        if risk_input.generated_tests_status == "failed" or score >= 60:
            return MergeDecision.MANUAL_REVIEW
        if score >= 30:
            return MergeDecision.MERGE_WITH_CAUTION
        return MergeDecision.MERGE

    @staticmethod
    def _combined_status(runs: Iterable[ToolRun]) -> str:
        statuses = [run.status for run in runs if run is not None]
        if not statuses:
            return "not_run"
        if any(status == RunStatus.FAILED for status in statuses):
            return "failed"
        if any(status == RunStatus.ERROR for status in statuses):
            return "error"
        if all(status == RunStatus.SKIPPED for status in statuses):
            return "skipped"
        if any(status == RunStatus.PASSED for status in statuses):
            return "passed"
        return "not_run"

    @staticmethod
    def _failed_count(runs: Iterable[ToolRun]) -> int:
        total = 0
        for run in runs:
            if run.status != RunStatus.FAILED:
                continue
            output = ""
            if run.command:
                output = f"{run.command.stdout_tail}\n{run.command.stderr_tail}"
            match = re.search(r"(\d+)\s+failed", output)
            total += int(match.group(1)) if match else 1
        return total

    @staticmethod
    def _security_counts(findings: list[SecurityFinding]) -> SecurityFindingCounts:
        counts = SecurityFindingCounts()
        for finding in findings:
            severity = finding.severity.upper()
            if severity == "CRITICAL":
                counts.critical += 1
            elif severity == "HIGH":
                counts.high += 1
            elif severity == "MEDIUM":
                counts.medium += 1
            else:
                counts.low += 1
        return counts

    @staticmethod
    def _secrets_detected(findings: list[SecurityFinding]) -> bool:
        secret_codes = {"B105", "B106", "B107"}
        secret_words = ("secret", "password", "token", "credential", "private key")
        for finding in findings:
            haystack = f"{finding.issue_code or ''} {finding.message} {finding.issue_text}".lower()
            if finding.issue_code in secret_codes or any(word in haystack for word in secret_words):
                return True
        return False

    @staticmethod
    def _no_existing_tests_found(runs: Iterable[ToolRun]) -> bool:
        return any(
            run.status == RunStatus.SKIPPED
            and "no pytest tests discovered" in run.summary.lower()
            for run in runs
        )

    @staticmethod
    def _diff_too_large(changed_files: list[ChangedFile]) -> bool:
        total_lines = sum(file.changes for file in changed_files)
        missing_python_patches = any(
            file.is_python
            and file.status != "removed"
            and file.patch is None
            and file.changes > 500
            for file in changed_files
        )
        return total_lines > 2500 or missing_python_patches

    @staticmethod
    def _contract_confidence(report: RiskReport | PatchGuardReport) -> float | None:
        run = getattr(report, "contract_extraction", None)
        if run is None:
            return None
        if run.status == RunStatus.PASSED:
            return report.behavioral_contract.confidence
        if run.status == RunStatus.ERROR:
            return 0.0
        return None

    @staticmethod
    def _reason(
        reasons: list[RiskReason],
        category: str,
        impact: int,
        reason: str,
        severity: str,
        evidence: list[str] | None = None,
    ) -> None:
        reasons.append(
            RiskReason(
                category=category,
                score_impact=impact,
                reason=reason,
                severity=severity,
                evidence=evidence or [],
            )
        )

    @staticmethod
    def _clamp(score: int) -> int:
        return max(0, min(100, score))

    @staticmethod
    def _severity_for_score(score: int) -> str:
        if score >= 80:
            return "critical"
        if score >= 60:
            return "high"
        if score >= 30:
            return "medium"
        return "low"

    @staticmethod
    def _behavior_category_impact(category: str) -> int:
        normalized = category.lower()
        if normalized in {"auth", "security", "input_validation", "api_contract"}:
            return 50
        if normalized in {"database", "data_model", "dependency", "concurrency"}:
            return 35
        if normalized in {"parser", "error_handling", "performance"}:
            return 25
        if normalized == "unknown":
            return 15
        return 10

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
