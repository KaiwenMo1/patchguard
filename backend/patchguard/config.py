"""Configuration defaults for the PatchGuard CLI pipeline."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

DEFAULT_COMMAND_TIMEOUT_SECONDS = 300
DEFAULT_DOCKER_BUILD_TIMEOUT_SECONDS = 600
DEFAULT_DOCKER_IMAGE = "patchguard-python-sandbox:latest"
DEFAULT_REPORT_DIR = Path("reports")
DEFAULT_RUNS_DIR = Path(".patchguard") / "runs"
DEFAULT_WORKSPACES_DIR = Path(".patchguard_workspaces")


@dataclass(frozen=True)
class SandboxLimits:
    """Resource limits applied to untrusted repository execution."""

    cpus: str = "2"
    memory: str = "1g"
    network: str = "none"


@dataclass(frozen=True)
class PatchGuardSettings:
    """Runtime settings for a PatchGuard run."""

    command_timeout_seconds: int = DEFAULT_COMMAND_TIMEOUT_SECONDS
    docker_build_timeout_seconds: int = DEFAULT_DOCKER_BUILD_TIMEOUT_SECONDS
    docker_image: str = DEFAULT_DOCKER_IMAGE
    runs_dir: Path = DEFAULT_RUNS_DIR
    report_dir: Path = DEFAULT_REPORT_DIR
    workspaces_dir: Path = DEFAULT_WORKSPACES_DIR
    sandbox_limits: SandboxLimits = SandboxLimits()
