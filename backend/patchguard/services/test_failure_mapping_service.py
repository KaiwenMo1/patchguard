"""Map generated pytest failures back to target functions and behaviors."""

from __future__ import annotations

import re

from patchguard.models import FailureMapping, GeneratedTestMetadata, RunStatus, ToolRun

FAILED_TEST_PATTERN = re.compile(
    r"^FAILED\s+(?P<nodeid>\S+?::(?P<test>[A-Za-z_][A-Za-z0-9_]*(?:\[[^\]]+\])?))"
    r"(?:\s+-\s+(?P<summary>.*))?$",
    re.MULTILINE,
)


class TestFailureMappingService:
    """Create structured failure mappings from pytest output and generated-test metadata."""

    def map_failures(
        self,
        generated_test_runs: list[ToolRun],
        metadata: list[GeneratedTestMetadata],
    ) -> list[FailureMapping]:
        failing_run = self._failing_generated_test_run(generated_test_runs)
        if failing_run is None:
            return []
        failures = parse_failed_pytest_tests(failing_run)
        if not failures:
            failures = [
                ParsedPytestFailure(
                    failed_test=item.test_name,
                    failure_summary=failing_run.summary,
                )
                for item in metadata
            ]
        return [self._map_failure(failure, metadata) for failure in failures]

    @staticmethod
    def _failing_generated_test_run(runs: list[ToolRun]) -> ToolRun | None:
        for run in runs:
            if run.name == "run generated PatchGuard tests" and run.status == RunStatus.FAILED:
                return run
        return None

    @staticmethod
    def _map_failure(
        failure: ParsedPytestFailure,
        metadata: list[GeneratedTestMetadata],
    ) -> FailureMapping:
        normalized_name = strip_pytest_params(failure.failed_test)
        match = next(
            (
                item
                for item in metadata
                if item.test_name == failure.failed_test or item.test_name == normalized_name
            ),
            None,
        )
        if match is None:
            return FailureMapping(
                failed_test=failure.failed_test,
                failure_summary=failure.failure_summary,
                risk_message=(
                    f"Generated test {failure.failed_test} failed, but PatchGuard could not map "
                    "it to generated-test metadata."
                ),
                suggested_next_step=(
                    "Inspect the generated test output and metadata.json to decide whether the "
                    "failure is a real regression or a bad generated test."
                ),
            )
        suggested_next_step = suggested_next_step_for_mapping(match)
        return FailureMapping(
            failed_test=failure.failed_test,
            target_file=match.target_file,
            target_function=match.target_function,
            behavior_checked=match.behavior_checked,
            failure_summary=failure.failure_summary,
            risk_message=(
                f"Generated test {failure.failed_test} failed while checking "
                f"{match.behavior_checked} in {match.target_file}::{match.target_function}."
            ),
            suggested_next_step=suggested_next_step,
        )


class ParsedPytestFailure:
    def __init__(self, *, failed_test: str, failure_summary: str) -> None:
        self.failed_test = failed_test
        self.failure_summary = failure_summary


def parse_failed_pytest_tests(run: ToolRun) -> list[ParsedPytestFailure]:
    if run.command is None:
        return []
    output = f"{run.command.stdout_tail}\n{run.command.stderr_tail}"
    failures: list[ParsedPytestFailure] = []
    seen: set[str] = set()
    for match in FAILED_TEST_PATTERN.finditer(output):
        test_name = match.group("test")
        if test_name in seen:
            continue
        seen.add(test_name)
        summary = (match.group("summary") or "").strip() or run.summary
        failures.append(ParsedPytestFailure(failed_test=test_name, failure_summary=summary))
    return failures


def strip_pytest_params(test_name: str) -> str:
    return test_name.split("[", 1)[0]


def suggested_next_step_for_mapping(metadata: GeneratedTestMetadata) -> str:
    target = f"{metadata.target_file}::{metadata.target_function}"
    if metadata.test_type == "new_behavior":
        return (
            f"Confirm whether the PR is expected to implement this new behavior, then inspect {target} "
            "or update the generated test if the contract was misunderstood."
        )
    if metadata.test_type == "edge_case":
        return (
            f"Review the edge case in {target}; add a human-written regression test if the generated "
            "case represents a real boundary condition."
        )
    if metadata.test_type == "security":
        return (
            f"Review {target} for the security-sensitive behavior and do not merge until the failing "
            "case is explained."
        )
    return (
        f"Check whether {target} regressed the behavior under test, then either fix the code or mark "
        "the generated test as invalid with a reason."
    )
