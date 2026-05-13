"""Filesystem helpers."""

from __future__ import annotations

from pathlib import Path

from patchguard.models import PatchGuardReport


def ensure_dir(path: str | Path) -> Path:
    directory = Path(path)
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def write_json_report(report: PatchGuardReport, path: str | Path) -> Path:
    output_path = Path(path)
    ensure_dir(output_path.parent)
    report.report_path = str(output_path)
    output_path.write_text(report.model_dump_json(indent=2) + "\n", encoding="utf-8")
    return output_path


def safe_repo_path(repo_dir: str | Path, relative_path: str) -> Path:
    root = Path(repo_dir).resolve()
    candidate = (root / relative_path).resolve()
    if root != candidate and root not in candidate.parents:
        raise ValueError(f"Path escapes repository root: {relative_path}")
    return candidate
