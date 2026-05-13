"""Timeout-aware subprocess wrapper."""

from __future__ import annotations

import subprocess
import time
from collections.abc import Mapping, Sequence
from pathlib import Path

from patchguard.models import CommandResult


class CommandRunner:
    """Run commands with timeouts and bounded captured output."""

    def __init__(self, output_tail_chars: int = 5_000_000) -> None:
        self.output_tail_chars = output_tail_chars

    def run(
        self,
        command: Sequence[str],
        *,
        cwd: str | Path | None = None,
        timeout_seconds: int = 300,
        env: Mapping[str, str] | None = None,
    ) -> CommandResult:
        started = time.monotonic()
        command_list = [str(part) for part in command]
        try:
            completed = subprocess.run(
                command_list,
                cwd=str(cwd) if cwd else None,
                env=dict(env) if env else None,
                text=True,
                capture_output=True,
                timeout=timeout_seconds,
                check=False,
            )
            duration = time.monotonic() - started
            return CommandResult(
                command=command_list,
                exit_code=completed.returncode,
                stdout_tail=self._tail(completed.stdout),
                stderr_tail=self._tail(completed.stderr),
                duration_seconds=round(duration, 3),
            )
        except subprocess.TimeoutExpired as exc:
            duration = time.monotonic() - started
            return CommandResult(
                command=command_list,
                exit_code=None,
                stdout_tail=self._tail(self._coerce_output(exc.stdout)),
                stderr_tail=self._tail(self._coerce_output(exc.stderr)),
                duration_seconds=round(duration, 3),
                timed_out=True,
            )
        except FileNotFoundError as exc:
            duration = time.monotonic() - started
            return CommandResult(
                command=command_list,
                exit_code=127,
                stdout_tail="",
                stderr_tail=str(exc),
                duration_seconds=round(duration, 3),
            )
        except OSError as exc:
            duration = time.monotonic() - started
            return CommandResult(
                command=command_list,
                exit_code=126,
                stdout_tail="",
                stderr_tail=str(exc),
                duration_seconds=round(duration, 3),
            )

    def skipped(self, command: Sequence[str], reason: str) -> CommandResult:
        return CommandResult(
            command=[str(part) for part in command],
            skipped=True,
            skip_reason=reason,
        )

    def _tail(self, value: str | bytes | None) -> str:
        text = self._coerce_output(value)
        if len(text) <= self.output_tail_chars:
            return text
        return text[-self.output_tail_chars :]

    @staticmethod
    def _coerce_output(value: str | bytes | None) -> str:
        if value is None:
            return ""
        if isinstance(value, bytes):
            return value.decode("utf-8", errors="replace")
        return value
