"""Repository checkout service."""

from __future__ import annotations

import os
import shutil
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from patchguard.models import PRMetadata, RunStatus, ToolRun
from patchguard.utils.command_runner import CommandRunner
from patchguard.utils.file_utils import ensure_dir


@dataclass(frozen=True)
class CheckoutOutcome:
    repo_dir: Path | None
    tool_runs: list[ToolRun]

    @property
    def success(self) -> bool:
        return self.repo_dir is not None and all(run.status == RunStatus.PASSED for run in self.tool_runs)


class CloneService:
    def __init__(
        self,
        command_runner: CommandRunner | None = None,
        timeout_seconds: int = 300,
        git_token: str | None = None,
    ) -> None:
        self.command_runner = command_runner or CommandRunner()
        self.timeout_seconds = timeout_seconds
        self.git_token = git_token

    def create_workspace(self, workspaces_dir: str | Path, metadata: PRMetadata) -> Path:
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
        name = f"{metadata.owner}-{metadata.repo}-pr-{metadata.number}-{stamp}"
        return ensure_dir(Path(workspaces_dir) / name)

    def cleanup_workspace(self, workspace_path: str | Path) -> None:
        shutil.rmtree(workspace_path, ignore_errors=True)

    def checkout_pull_request(self, metadata: PRMetadata, run_dir: str | Path) -> CheckoutOutcome:
        run_path = ensure_dir(run_dir)
        repo_dir = run_path / "repo"
        tool_runs: list[ToolRun] = []
        git_env = self._git_auth_env(run_path)

        clone_command = [
            "git",
            "clone",
            "--no-tags",
            "--depth",
            "1",
            metadata.base_clone_url,
            str(repo_dir),
        ]
        clone_result = self.command_runner.run(
            clone_command,
            timeout_seconds=self.timeout_seconds,
            env=git_env,
        )
        tool_runs.append(self._tool_run("clone repository", clone_result))
        if not clone_result.succeeded:
            return CheckoutOutcome(repo_dir=None, tool_runs=tool_runs)

        fetch_base = [
            "git",
            "-C",
            str(repo_dir),
            "fetch",
            "--no-tags",
            "--depth",
            "1",
            "origin",
            metadata.base_sha,
        ]
        base_result = self.command_runner.run(
            fetch_base,
            timeout_seconds=self.timeout_seconds,
            env=git_env,
        )
        tool_runs.append(self._tool_run("fetch base commit", base_result))

        fetch_remote = metadata.head_clone_url or metadata.base_clone_url
        fetch_head = [
            "git",
            "-C",
            str(repo_dir),
            "fetch",
            "--no-tags",
            "--depth",
            "1",
            fetch_remote,
            metadata.head_sha,
        ]
        head_result = self.command_runner.run(
            fetch_head,
            timeout_seconds=self.timeout_seconds,
            env=git_env,
        )
        tool_runs.append(self._tool_run("fetch PR head commit", head_result))
        if not head_result.succeeded:
            return CheckoutOutcome(repo_dir=None, tool_runs=tool_runs)

        checkout = [
            "git",
            "-C",
            str(repo_dir),
            "checkout",
            "--force",
            metadata.head_sha,
        ]
        checkout_result = self.command_runner.run(
            checkout,
            timeout_seconds=self.timeout_seconds,
            env=git_env,
        )
        tool_runs.append(self._tool_run("checkout PR head", checkout_result))
        if not checkout_result.succeeded:
            return CheckoutOutcome(repo_dir=None, tool_runs=tool_runs)

        return CheckoutOutcome(repo_dir=repo_dir, tool_runs=tool_runs)

    def _git_auth_env(self, workspace: Path) -> Mapping[str, str] | None:
        if not self.git_token:
            return None
        askpass_path = workspace / ".patchguard-git-askpass.sh"
        askpass_path.write_text(
            "#!/bin/sh\n"
            "case \"$1\" in\n"
            "  *Username*) echo \"x-access-token\" ;;\n"
            "  *) echo \"$PATCHGUARD_GIT_TOKEN\" ;;\n"
            "esac\n",
            encoding="utf-8",
        )
        askpass_path.chmod(0o700)
        env = dict(os.environ)
        env["GIT_ASKPASS"] = str(askpass_path)
        env["GIT_TERMINAL_PROMPT"] = "0"
        env["PATCHGUARD_GIT_TOKEN"] = self.git_token
        return env

    @staticmethod
    def _tool_run(name: str, command_result) -> ToolRun:
        status = RunStatus.PASSED if command_result.succeeded else RunStatus.FAILED
        summary = "command completed successfully" if status == RunStatus.PASSED else "command failed"
        if command_result.timed_out:
            summary = "command timed out"
        return ToolRun(
            name=name,
            kind="clone",
            status=status,
            summary=summary,
            command=command_result,
        )
