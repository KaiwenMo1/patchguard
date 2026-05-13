from __future__ import annotations

import subprocess

from patchguard.utils.command_runner import CommandRunner


def test_command_runner_captures_os_errors(monkeypatch) -> None:
    def fail_to_start(*_args, **_kwargs):
        raise OSError(5, "Input/output error", "docker")

    monkeypatch.setattr(subprocess, "run", fail_to_start)

    result = CommandRunner().run(["docker", "image", "inspect", "patchguard-python-sandbox"])

    assert result.exit_code == 126
    assert result.stdout_tail == ""
    assert "Input/output error" in result.stderr_tail
