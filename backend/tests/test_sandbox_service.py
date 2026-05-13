from __future__ import annotations

from patchguard.models import CommandResult, RunStatus, ToolRun
from patchguard.services.sandbox_service import SandboxService


def test_parse_pytest_passed() -> None:
    run = _pytest_run(exit_code=0, stdout="1 passed")

    SandboxService._parse_pytest_status(run)

    assert run.status == RunStatus.PASSED
    assert run.summary == "pytest passed"


def test_parse_pytest_failed() -> None:
    run = _pytest_run(exit_code=1, stdout="1 failed")

    SandboxService._parse_pytest_status(run)

    assert run.status == RunStatus.FAILED
    assert run.summary == "pytest tests failed"


def test_parse_pytest_no_tests() -> None:
    run = _pytest_run(exit_code=5, stdout="no tests ran")

    SandboxService._parse_pytest_status(run)

    assert run.status == RunStatus.SKIPPED
    assert run.summary == "pytest found no tests"


def test_parse_pytest_timeout() -> None:
    run = _pytest_run(exit_code=None, stdout="", timed_out=True)

    SandboxService._parse_pytest_status(run)

    assert run.status == RunStatus.ERROR
    assert run.summary == "pytest timed out"


def _pytest_run(exit_code: int | None, stdout: str, *, timed_out: bool = False) -> ToolRun:
    return ToolRun(
        name="run existing pytest suite",
        kind="existing_tests",
        status=RunStatus.FAILED,
        summary="before parse",
        command=CommandResult(
            command=["python", "-m", "pytest", "-q"],
            exit_code=exit_code,
            stdout_tail=stdout,
            timed_out=timed_out,
        ),
    )
