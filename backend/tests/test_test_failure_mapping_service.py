from __future__ import annotations

from patchguard.models import CommandResult, GeneratedTestMetadata, RunStatus, ToolRun
from patchguard.services.test_failure_mapping_service import (
    TestFailureMappingService,
    parse_failed_pytest_tests,
)


def test_parses_failed_pytest_nodeids() -> None:
    run = _generated_run(
        """FAILED .patchguard/generated_tests/test_patchguard_generated_src_app_py.py::test_empty_input - AssertionError: boom
FAILED .patchguard/generated_tests/test_patchguard_generated_src_app_py.py::test_param_case[None] - ValueError
2 failed, 1 passed
"""
    )

    failures = parse_failed_pytest_tests(run)

    assert [failure.failed_test for failure in failures] == [
        "test_empty_input",
        "test_param_case[None]",
    ]
    assert failures[0].failure_summary == "AssertionError: boom"


def test_maps_failure_to_generated_test_metadata() -> None:
    run = _generated_run(
        "FAILED .patchguard/generated_tests/test_patchguard_generated_src_app_py.py::test_empty_input - AssertionError\n"
    )
    metadata = [
        GeneratedTestMetadata(
            test_name="test_empty_input",
            target_file="src/app.py",
            target_function="parse_config",
            behavior_checked="empty input should not crash",
            test_type="regression",
        )
    ]

    mappings = TestFailureMappingService().map_failures([run], metadata)

    assert len(mappings) == 1
    assert mappings[0].failed_test == "test_empty_input"
    assert mappings[0].target_file == "src/app.py"
    assert mappings[0].target_function == "parse_config"
    assert mappings[0].behavior_checked == "empty input should not crash"
    assert "parse_config" in mappings[0].risk_message
    assert "src/app.py::parse_config" in mappings[0].suggested_next_step


def test_falls_back_to_metadata_when_pytest_output_has_no_nodeids() -> None:
    run = _generated_run("1 failed")
    metadata = [
        GeneratedTestMetadata(
            test_name="test_generated_behavior",
            target_file="src/app.py",
            target_function="greet",
            behavior_checked="generated behavior",
        )
    ]

    mappings = TestFailureMappingService().map_failures([run], metadata)

    assert [mapping.failed_test for mapping in mappings] == ["test_generated_behavior"]


def test_unmapped_failure_has_actionable_next_step() -> None:
    run = _generated_run(
        "FAILED .patchguard/generated_tests/test_patchguard_generated_src_app_py.py::test_unknown - AssertionError\n"
    )

    mappings = TestFailureMappingService().map_failures([run], [])

    assert mappings[0].target_file is None
    assert "metadata.json" in mappings[0].suggested_next_step


def _generated_run(stdout: str) -> ToolRun:
    return ToolRun(
        name="run generated PatchGuard tests",
        kind="generated_tests",
        status=RunStatus.FAILED,
        summary="pytest tests failed",
        command=CommandResult(
            command=["python", "-m", "pytest", "-q", ".patchguard/generated_tests"],
            exit_code=1,
            stdout_tail=stdout,
        ),
    )
