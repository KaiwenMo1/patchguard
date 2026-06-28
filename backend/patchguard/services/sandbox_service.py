"""Docker sandbox execution service."""

from __future__ import annotations

import shlex
from pathlib import Path

from patchguard.config import (
    DEFAULT_COMMAND_TIMEOUT_SECONDS,
    DEFAULT_DOCKER_BUILD_TIMEOUT_SECONDS,
    DEFAULT_DOCKER_IMAGE,
    SandboxLimits,
)
from patchguard.models import RunStatus, ToolRun
from patchguard.utils.command_runner import CommandRunner

DEPENDENCY_INSTALL_SCRIPT = """
set -o pipefail
if [ -f requirements.txt ]; then
  python -m pip install -r requirements.txt
elif [ -f pyproject.toml ] || [ -f setup.py ]; then
  python -m pip install -e .
else
  echo "No requirements.txt, pyproject.toml, or setup.py found; skipping dependency install"
fi
""".strip()

EXISTING_TEST_SCRIPT = "python -m pytest -q"
GENERATED_TEST_COMPILE_SCRIPT = "python -m py_compile .patchguard/generated_tests/test_patchguard_generated_*.py"
GENERATED_TEST_SCRIPT = "python -m pytest -q .patchguard/generated_tests"


class SandboxService:
    """Build and run commands in the PatchGuard Python Docker sandbox."""

    def __init__(
        self,
        *,
        command_runner: CommandRunner | None = None,
        docker_image: str = DEFAULT_DOCKER_IMAGE,
        limits: SandboxLimits | None = None,
        project_root: str | Path | None = None,
    ) -> None:
        self.command_runner = command_runner or CommandRunner()
        self.docker_image = docker_image
        self.limits = limits or SandboxLimits()
        self.project_root = Path(project_root) if project_root else Path(__file__).resolve().parents[3]

    def ensure_image(self, timeout_seconds: int = DEFAULT_DOCKER_BUILD_TIMEOUT_SECONDS) -> ToolRun:
        inspect_command = ["docker", "image", "inspect", self.docker_image]
        inspect_result = self.command_runner.run(inspect_command, timeout_seconds=30)
        if inspect_result.succeeded:
            return ToolRun(
                name="ensure python sandbox image",
                kind="docker_build",
                status=RunStatus.PASSED,
                summary="Docker sandbox image already exists",
                command=inspect_result,
            )
        return self.build_image(timeout_seconds)

    def build_image(self, timeout_seconds: int = DEFAULT_DOCKER_BUILD_TIMEOUT_SECONDS) -> ToolRun:
        dockerfile = self.project_root / "sandbox" / "python" / "Dockerfile"
        command = [
            "docker",
            "build",
            "-t",
            self.docker_image,
            "-f",
            str(dockerfile),
            str(dockerfile.parent),
        ]
        result = self.command_runner.run(command, timeout_seconds=timeout_seconds)
        return self._tool_run(
            name="build python sandbox image",
            kind="docker_build",
            result=result,
            success_summary="Docker sandbox image built",
            failure_summary="Docker sandbox image build failed",
        )

    def run_in_repo(
        self,
        *,
        repo_dir: str | Path,
        name: str,
        kind: str,
        script: str,
        timeout_seconds: int = DEFAULT_COMMAND_TIMEOUT_SECONDS,
    ) -> ToolRun:
        command = [
            "docker",
            "run",
            "--rm",
            "--network",
            self.limits.network,
            "--cpus",
            self.limits.cpus,
            "--memory",
            self.limits.memory,
            "-v",
            f"{Path(repo_dir).resolve()}:/app",
            "-w",
            "/app",
            self.docker_image,
            "bash",
            "-lc",
            script,
        ]
        result = self.command_runner.run(command, timeout_seconds=timeout_seconds)
        return self._tool_run(
            name=name,
            kind=kind,
            result=result,
            success_summary="command completed successfully",
            failure_summary="command failed in Docker sandbox",
        )

    def run_dependency_install(
        self,
        *,
        repo_dir: str | Path,
        timeout_seconds: int = DEFAULT_COMMAND_TIMEOUT_SECONDS,
    ) -> ToolRun:
        run = self.run_in_repo(
            repo_dir=repo_dir,
            name="install repository dependencies",
            kind="dependency_install",
            script=DEPENDENCY_INSTALL_SCRIPT,
            timeout_seconds=timeout_seconds,
        )
        stdout = run.command.stdout_tail if run.command else ""
        if "skipping dependency install" in stdout.lower() and run.status == RunStatus.PASSED:
            run.status = RunStatus.SKIPPED
            run.summary = "No dependency manifest found; dependency install skipped"
        return run

    def run_existing_tests(
        self,
        *,
        repo_dir: str | Path,
        timeout_seconds: int = 180,
    ) -> ToolRun:
        run = self.run_in_repo(
            repo_dir=repo_dir,
            name="run existing pytest suite",
            kind="existing_tests",
            script=EXISTING_TEST_SCRIPT,
            timeout_seconds=timeout_seconds,
        )
        self._parse_pytest_status(run)
        return run

    def run_generated_test_compile(
        self,
        *,
        repo_dir: str | Path,
        timeout_seconds: int = DEFAULT_COMMAND_TIMEOUT_SECONDS,
    ) -> ToolRun:
        return self.run_in_repo(
            repo_dir=repo_dir,
            name="compile generated PatchGuard tests",
            kind="generated_tests",
            script=GENERATED_TEST_COMPILE_SCRIPT,
            timeout_seconds=timeout_seconds,
        )

    def run_generated_tests(
        self,
        *,
        repo_dir: str | Path,
        timeout_seconds: int = DEFAULT_COMMAND_TIMEOUT_SECONDS,
    ) -> ToolRun:
        run = self.run_in_repo(
            repo_dir=repo_dir,
            name="run generated PatchGuard tests",
            kind="generated_tests",
            script=GENERATED_TEST_SCRIPT,
            timeout_seconds=timeout_seconds,
        )
        self._parse_pytest_status(run)
        return run

    def run_existing_tests_at_ref(
        self,
        *,
        repo_dir: str | Path,
        git_ref: str,
        name: str,
        timeout_seconds: int = 180,
    ) -> ToolRun:
        quoted_ref = shlex.quote(git_ref)
        script = f"""
        set -o pipefail
        git checkout --force {quoted_ref}
        if [ -f requirements.txt ]; then
          python -m pip install -r requirements.txt
        elif [ -f pyproject.toml ] || [ -f setup.py ]; then
          python -m pip install -e .
        else
          echo "No requirements.txt, pyproject.toml, or setup.py found; skipping dependency install"
        fi
        if [ -d tests ] || find . -maxdepth 3 -name 'test_*.py' -not -path './.patchguard/*' -print -quit | grep -q .; then
          python -m pytest -q
        else
          echo "No pytest tests discovered; skipping existing test run"
        fi
        """.strip()
        run = self.run_in_repo(
            repo_dir=repo_dir,
            name=name,
            kind="existing_tests",
            script=script,
            timeout_seconds=timeout_seconds,
        )
        stdout = run.command.stdout_tail if run.command else ""
        if "No pytest tests discovered" in stdout and run.status == RunStatus.PASSED:
            run.status = RunStatus.SKIPPED
            run.summary = "No pytest tests discovered; existing test run skipped"
        else:
            self._parse_pytest_status(run)
        return run

    def skipped(self, *, name: str, kind: str, reason: str, command: list[str] | None = None) -> ToolRun:
        command_result = self.command_runner.skipped(command or [], reason)
        return ToolRun(
            name=name,
            kind=kind,
            status=RunStatus.SKIPPED,
            summary=reason,
            command=command_result,
        )

    @staticmethod
    def _tool_run(
        *,
        name: str,
        kind: str,
        result,
        success_summary: str,
        failure_summary: str,
    ) -> ToolRun:
        if result.timed_out:
            status = RunStatus.ERROR
            summary = "command timed out"
        elif result.succeeded:
            status = RunStatus.PASSED
            summary = success_summary
        else:
            status = RunStatus.FAILED
            summary = failure_summary
        return ToolRun(
            name=name,
            kind=kind,
            status=status,
            summary=summary,
            command=result,
        )

    @staticmethod
    def _parse_pytest_status(run: ToolRun) -> None:
        if run.command is None:
            run.status = RunStatus.ERROR
            run.summary = "pytest did not produce a command result"
            return
        output = f"{run.command.stdout_tail}\n{run.command.stderr_tail}".lower()
        if run.command.timed_out:
            run.status = RunStatus.ERROR
            run.summary = "pytest timed out"
        elif run.command.exit_code == 0:
            run.status = RunStatus.PASSED
            run.summary = "pytest passed"
        elif run.command.exit_code == 5 and ("no tests ran" in output or "no tests collected" in output):
            run.status = RunStatus.SKIPPED
            run.summary = "pytest found no tests"
        elif run.command.exit_code == 1:
            run.status = RunStatus.FAILED
            run.summary = "pytest tests failed"
        else:
            run.status = RunStatus.ERROR
            run.summary = "pytest errored"
