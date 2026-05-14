"""Static and security scan parsing."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from patchguard.models import ChangedFile, RunStatus, SecurityFinding, StaticFinding, ToolRun
from patchguard.services.function_extractor import FunctionExtractor
from patchguard.services.sandbox_service import SandboxService


class SecurityScanService:
    def __init__(self, sandbox_service: SandboxService) -> None:
        self.sandbox_service = sandbox_service

    def run_ruff(
        self,
        repo_dir: str | Path,
        changed_files: list[ChangedFile] | None = None,
    ) -> tuple[ToolRun, list[StaticFinding]]:
        run = self.sandbox_service.run_in_repo(
            repo_dir=repo_dir,
            name="ruff check",
            kind="static_analysis",
            script="python -m ruff check --output-format=json .",
            timeout_seconds=300,
        )
        raw_findings = self._parse_ruff_findings(run)
        findings = self._filter_static_findings(raw_findings, changed_files)
        if findings and run.status == RunStatus.FAILED:
            run.summary = f"ruff reported {len(findings)} finding(s)"
            run.findings_count = len(findings)
        elif changed_files is not None and raw_findings and not findings and run.status == RunStatus.FAILED:
            run.status = RunStatus.PASSED
            run.summary = f"ruff reported {len(raw_findings)} finding(s), none in changed files"
            run.findings_count = 0
        return run, findings

    def run_bandit(
        self,
        repo_dir: str | Path,
        changed_files: list[ChangedFile] | None = None,
    ) -> tuple[ToolRun, list[SecurityFinding]]:
        run = self.sandbox_service.run_in_repo(
            repo_dir=repo_dir,
            name="bandit security scan",
            kind="security_scan",
            script="python -m bandit -r . -f json -q",
            timeout_seconds=300,
        )
        raw_findings = self._parse_bandit_findings(run)
        findings = self._filter_security_findings(raw_findings, changed_files)
        if findings:
            run.summary = f"bandit reported {len(findings)} changed-file security finding(s)"
            run.findings_count = len(findings)
        elif changed_files is not None and raw_findings and not findings and run.status == RunStatus.FAILED:
            run.status = RunStatus.PASSED
            run.summary = f"bandit reported {len(raw_findings)} finding(s), none in changed files"
            run.findings_count = 0
        return run, findings

    @staticmethod
    def _parse_ruff_findings(run: ToolRun) -> list[StaticFinding]:
        output = (run.command.stdout_tail if run.command else "").strip()
        if not output:
            return []
        try:
            payload = json.loads(output)
        except json.JSONDecodeError:
            return []
        findings: list[StaticFinding] = []
        for item in payload if isinstance(payload, list) else []:
            location = item.get("location") or {}
            filename = normalize_repo_path(item.get("filename"))
            findings.append(
                StaticFinding(
                    tool="ruff",
                    code=item.get("code"),
                    message=item.get("message") or "",
                    file=filename,
                    line=location.get("row"),
                    raw=item,
                )
            )
        return findings

    @staticmethod
    def _parse_bandit_findings(run: ToolRun) -> list[SecurityFinding]:
        output = (run.command.stdout_tail if run.command else "").strip()
        if not output:
            return []
        try:
            payload: dict[str, Any] = json.loads(output)
        except json.JSONDecodeError:
            return []
        findings: list[SecurityFinding] = []
        for item in payload.get("results", []):
            filename = normalize_repo_path(item.get("filename"))
            line_number = item.get("line_number")
            message = item.get("issue_text") or ""
            findings.append(
                SecurityFinding(
                    tool="bandit",
                    severity=item.get("issue_severity") or "UNKNOWN",
                    confidence=item.get("issue_confidence"),
                    filename=filename,
                    line_number=line_number,
                    message=message,
                    file=filename,
                    line=line_number,
                    issue_text=message,
                    issue_code=item.get("test_id"),
                    more_info=item.get("more_info"),
                    raw=item,
                )
            )
        return findings

    @staticmethod
    def _filter_static_findings(
        findings: list[StaticFinding],
        changed_files: list[ChangedFile] | None,
    ) -> list[StaticFinding]:
        if changed_files is None:
            return findings
        changed_lines = changed_python_line_map(changed_files)
        return [
            finding
            for finding in findings
            if finding_is_on_changed_line(
                normalize_repo_path(finding.file),
                finding.line,
                changed_lines,
            )
        ]

    @staticmethod
    def _filter_security_findings(
        findings: list[SecurityFinding],
        changed_files: list[ChangedFile] | None,
    ) -> list[SecurityFinding]:
        if changed_files is None:
            return findings
        changed_lines = changed_python_line_map(changed_files)
        return [
            finding
            for finding in findings
            if finding_is_on_changed_line(
                normalize_repo_path(finding.filename),
                finding.line_number,
                changed_lines,
            )
        ]


def changed_python_line_map(changed_files: list[ChangedFile]) -> dict[str, set[int]]:
    return {
        normalize_repo_path(file.filename): set(FunctionExtractor.parse_changed_lines(file.patch))
        for file in changed_files
        if file.is_python and file.status != "removed"
    }


def finding_is_on_changed_line(
    filename: str,
    line_number: int | None,
    changed_lines_by_file: dict[str, set[int]],
) -> bool:
    if filename not in changed_lines_by_file:
        return False
    changed_lines = changed_lines_by_file[filename]
    if not changed_lines or line_number is None:
        return True
    return line_number in changed_lines


def normalize_repo_path(path: str | None) -> str:
    if not path:
        return ""
    normalized = str(path).replace("\\", "/")
    while normalized.startswith("./"):
        normalized = normalized[2:]
    if normalized.startswith("/app/"):
        normalized = normalized[5:]
    return normalized
