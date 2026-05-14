"""Configurable merge policy gate."""

from __future__ import annotations

from fnmatch import fnmatch
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from patchguard.models import (
    ChangedFile,
    PatchGuardReport,
    PolicyConfig,
    PolicyDecision,
    PolicyGateDecision,
    RiskReport,
    RunStatus,
    SecurityFinding,
    ToolRun,
)
from patchguard.services.diff_service import DiffService


class PolicyService:
    """Evaluate deterministic CI-style policy rules from report evidence."""

    def __init__(self, diff_service: DiffService | None = None) -> None:
        self.diff_service = diff_service or DiffService()

    def evaluate(
        self,
        report: RiskReport | PatchGuardReport,
        *,
        repo_dir: str | Path | None = None,
    ) -> PolicyDecision:
        config, config_path, config_error = self.load_config(repo_dir)
        reasons: list[str] = []
        triggered_rules: list[str] = []
        blocked = False

        def block(rule: str, reason: str) -> None:
            nonlocal blocked
            triggered_rules.append(rule)
            reasons.append(reason)
            blocked = True

        def warn(rule: str, reason: str) -> None:
            triggered_rules.append(rule)
            reasons.append(reason)

        if report.risk_score >= config.risk_threshold:
            block(
                "risk_threshold",
                f"Risk score {report.risk_score} is at or above threshold {config.risk_threshold}.",
            )

        existing_status = self._combined_status(self._existing_runs(report))
        generated_failed = self._generated_tests_failed(self._generated_runs(report))
        high_security = self._has_high_security(report.security_findings)
        secret_detected = self._secrets_detected(report.security_findings)
        auth_without_tests = self._sensitive_source_without_tests(
            report.changed_files,
            config.sensitive_paths,
        )

        if existing_status in {RunStatus.FAILED, RunStatus.ERROR}:
            self._trigger_configured_rule(
                "existing_test_failure",
                "Existing tests failed or errored.",
                config,
                block,
                warn,
            )
        if generated_failed:
            self._trigger_configured_rule(
                "generated_test_failure",
                "Generated regression tests failed.",
                config,
                block,
                warn,
            )
        if high_security:
            self._trigger_configured_rule(
                "high_security_finding",
                "High or critical security finding exists on changed code.",
                config,
                block,
                warn,
            )
        if secret_detected:
            self._trigger_configured_rule(
                "secret_detected",
                "Potential secret or credential was detected.",
                config,
                block,
                warn,
            )
        if auth_without_tests:
            self._trigger_configured_rule(
                "auth_code_without_tests",
                "Sensitive source path changed without tests changing.",
                config,
                block,
                warn,
            )

        if config_error:
            warn("policy_config_error", config_error)

        if self._has_partial_evidence(report):
            warn(
                "partial_evidence",
                "One or more evidence steps were skipped or incomplete.",
            )

        if not blocked and report.risk_score >= config.allow_merge_with_caution_below:
            warn(
                "risk_warning_threshold",
                (
                    f"Risk score {report.risk_score} is at or above caution threshold "
                    f"{config.allow_merge_with_caution_below}."
                ),
            )

        decision = (
            PolicyGateDecision.BLOCK
            if blocked
            else PolicyGateDecision.WARN
            if reasons
            else PolicyGateDecision.PASS
        )
        return PolicyDecision(
            decision=decision,
            reasons=reasons,
            triggered_rules=triggered_rules,
            config_path=str(config_path) if config_path else None,
        )

    def apply(
        self,
        report: RiskReport | PatchGuardReport,
        *,
        repo_dir: str | Path | None = None,
    ) -> RiskReport | PatchGuardReport:
        report.policy_decision = self.evaluate(report, repo_dir=repo_dir)
        return report

    def load_config(
        self,
        repo_dir: str | Path | None,
    ) -> tuple[PolicyConfig, Path | None, str | None]:
        config_path = self._find_config(repo_dir)
        if config_path is None:
            return PolicyConfig(), None, None
        try:
            parsed = _parse_simple_yaml(config_path.read_text(encoding="utf-8"))
            return PolicyConfig(**parsed), config_path, None
        except (OSError, ValueError, ValidationError) as exc:
            return PolicyConfig(), config_path, f"Could not load {config_path.name}; defaults used: {exc}"

    @staticmethod
    def _find_config(repo_dir: str | Path | None) -> Path | None:
        if repo_dir is None:
            return None
        root = Path(repo_dir)
        for name in ("patchguard.yml", ".patchguard.yml"):
            path = root / name
            if path.exists():
                return path
        return None

    @staticmethod
    def _trigger_configured_rule(
        rule: str,
        reason: str,
        config: PolicyConfig,
        block,
        warn,
    ) -> None:  # noqa: ANN001
        if rule in config.block_on:
            block(rule, reason)
        else:
            warn(rule, reason)

    @staticmethod
    def _existing_runs(report: RiskReport | PatchGuardReport) -> list[ToolRun]:
        if isinstance(report, RiskReport):
            return [report.existing_tests] if report.existing_tests else []
        return report.existing_test_results

    @staticmethod
    def _generated_runs(report: RiskReport | PatchGuardReport) -> list[ToolRun]:
        return report.generated_test_results

    @staticmethod
    def _all_runs(report: RiskReport | PatchGuardReport) -> list[ToolRun]:
        runs: list[ToolRun] = []
        if isinstance(report, RiskReport):
            runs.extend(report.sandbox_results)
            if report.dependency_install:
                runs.append(report.dependency_install)
            if report.existing_tests:
                runs.append(report.existing_tests)
            runs.extend(report.generated_test_results)
            runs.extend(report.static_analysis_results)
            if report.test_generation:
                runs.append(report.test_generation)
        else:
            runs.extend(report.sandbox_results)
            runs.extend(report.existing_test_results)
            runs.extend(report.generated_test_results)
            runs.extend(report.static_analysis_results)
            if report.test_generation:
                runs.append(report.test_generation)
        return runs

    def _has_partial_evidence(self, report: RiskReport | PatchGuardReport) -> bool:
        if report.errors:
            return True
        return any(run.status in {RunStatus.SKIPPED, RunStatus.ERROR} for run in self._all_runs(report))

    @staticmethod
    def _combined_status(runs: list[ToolRun]) -> RunStatus | None:
        if not runs:
            return None
        statuses = [run.status for run in runs]
        if RunStatus.FAILED in statuses:
            return RunStatus.FAILED
        if RunStatus.ERROR in statuses:
            return RunStatus.ERROR
        if all(status == RunStatus.SKIPPED for status in statuses):
            return RunStatus.SKIPPED
        if RunStatus.PASSED in statuses:
            return RunStatus.PASSED
        return None

    @staticmethod
    def _generated_tests_failed(runs: list[ToolRun]) -> bool:
        return any(
            run.name == "run generated PatchGuard tests" and run.status == RunStatus.FAILED
            for run in runs
        )

    @staticmethod
    def _has_high_security(findings: list[SecurityFinding]) -> bool:
        return any(finding.severity.upper() in {"HIGH", "CRITICAL"} for finding in findings)

    @staticmethod
    def _secrets_detected(findings: list[SecurityFinding]) -> bool:
        secret_codes = {"B105", "B106", "B107"}
        secret_words = ("secret", "password", "token", "credential", "private key")
        for finding in findings:
            haystack = f"{finding.issue_code or ''} {finding.message} {finding.issue_text}".lower()
            if finding.issue_code in secret_codes or any(word in haystack for word in secret_words):
                return True
        return False

    def _sensitive_source_without_tests(
        self,
        changed_files: list[ChangedFile],
        sensitive_paths: list[str],
    ) -> bool:
        summary = self.diff_service.summarize(changed_files)
        if not summary.source_changed_without_tests:
            return False
        for changed_file in summary.source_files:
            if changed_file.classification == "security_sensitive":
                return True
            if _matches_any_path(changed_file.filename, sensitive_paths):
                return True
        return False


def _matches_any_path(filename: str, patterns: list[str]) -> bool:
    normalized = filename.replace("\\", "/").lower()
    for pattern in patterns:
        normalized_pattern = pattern.strip().strip("\"'").replace("\\", "/").lower()
        if not normalized_pattern:
            continue
        if any(char in normalized_pattern for char in "*?[]") and fnmatch(
            normalized,
            normalized_pattern,
        ):
            return True
        if normalized.startswith(normalized_pattern):
            return True
        if f"/{normalized_pattern}" in normalized:
            return True
    return False


def _parse_simple_yaml(text: str) -> dict[str, Any]:
    """Parse the tiny top-level YAML subset PatchGuard needs for config."""

    parsed: dict[str, Any] = {}
    current_list_key: str | None = None
    for raw_line in text.splitlines():
        line = raw_line.split("#", 1)[0].rstrip()
        if not line.strip():
            continue
        stripped = line.strip()
        if stripped.startswith("- "):
            if current_list_key is None:
                raise ValueError("list item found before a list key")
            parsed.setdefault(current_list_key, []).append(_clean_scalar(stripped[2:]))
            continue
        if ":" not in stripped:
            raise ValueError(f"unsupported line: {stripped}")
        key, value = stripped.split(":", 1)
        key = key.strip()
        value = value.strip()
        if not key:
            raise ValueError("empty config key")
        if value == "":
            parsed[key] = []
            current_list_key = key
        else:
            parsed[key] = _parse_scalar_or_inline_list(value)
            current_list_key = None
    return parsed


def _parse_scalar_or_inline_list(value: str) -> Any:
    if value.startswith("[") and value.endswith("]"):
        inner = value[1:-1].strip()
        if not inner:
            return []
        return [_clean_scalar(part) for part in inner.split(",")]
    cleaned = _clean_scalar(value)
    if cleaned.isdigit():
        return int(cleaned)
    return cleaned


def _clean_scalar(value: str) -> str:
    return value.strip().strip("\"'")
