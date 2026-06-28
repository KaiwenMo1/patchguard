from __future__ import annotations

from patchguard.models import CommandResult, PRMetadata
from patchguard.services.clone_service import CloneService


def test_clone_service_uses_askpass_env_without_putting_token_in_command(tmp_path) -> None:
    runner = RecordingRunner()
    token = "github-app-installation-token"
    metadata = PRMetadata(
        owner="owner",
        repo="repo",
        number=123,
        title="Private repo PR",
        author="alice",
        state="open",
        is_draft=False,
        html_url="https://github.com/owner/repo/pull/123",
        base_ref="main",
        base_sha="base-sha",
        base_repo_full_name="owner/repo",
        base_clone_url="https://github.com/owner/repo.git",
        head_ref="feature",
        head_sha="head-sha",
        head_repo_full_name="owner/repo",
        head_clone_url="https://github.com/owner/repo.git",
        changed_files_count=1,
        additions=1,
        deletions=0,
    )

    outcome = CloneService(
        command_runner=runner,
        timeout_seconds=10,
        git_token=token,
    ).checkout_pull_request(metadata, tmp_path)

    assert outcome.success is True
    assert len(runner.calls) == 4
    for command, env in runner.calls:
        assert token not in command
        assert env is not None
        assert env["PATCHGUARD_GIT_TOKEN"] == token
        assert env["GIT_TERMINAL_PROMPT"] == "0"
        assert env["GIT_ASKPASS"].endswith(".patchguard-git-askpass.sh")


class RecordingRunner:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, str] | None]] = []

    def run(self, command, *, cwd=None, timeout_seconds=300, env=None):  # noqa: ANN001, ARG002
        rendered_command = " ".join(str(part) for part in command)
        self.calls.append((rendered_command, dict(env) if env else None))
        return CommandResult(command=[str(part) for part in command], exit_code=0)
