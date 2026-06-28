from __future__ import annotations

from pathlib import Path

from patchguard.models import (
    ChangedFile,
    ChangedFunction,
    PullRequestInfo,
    RiskLevel,
    RiskReason,
    RiskReport,
    SecurityFinding,
)
from patchguard.services.memory_service import MemoryService


def test_memory_indexes_report_and_retrieves_similar_evidence(tmp_path: Path) -> None:
    db_path = tmp_path / "memory.db"
    old_report_path = tmp_path / "old-report.json"
    old_report = sample_report(
        pr_number=1,
        title="Tighten auth token parsing",
        file_name="patchguard/auth/token_parser.py",
    )
    old_report_path.write_text(old_report.model_dump_json(), encoding="utf-8")

    service = MemoryService(db_path)
    documents_indexed = service.index_report(old_report_path)
    hits = service.search_for_report(
        sample_report(
            pr_number=2,
            title="Fix token parser edge case",
            file_name="patchguard/auth/token_parser.py",
        )
    )

    assert documents_indexed >= 3
    assert hits
    assert hits[0].source_type in {"changed_file", "changed_function", "security_finding"}
    assert hits[0].repository == "owner/repo"


def test_memory_index_path_finds_reports_under_patchguard_directory(tmp_path: Path) -> None:
    reports_dir = tmp_path / ".patchguard" / "app_reports"
    reports_dir.mkdir(parents=True)
    report_path = reports_dir / "report.json"
    report_path.write_text(sample_report().model_dump_json(), encoding="utf-8")

    result = MemoryService(tmp_path / "memory.db").index_path(tmp_path)

    assert result.reports_seen == 1
    assert result.documents_indexed >= 1


def sample_report(
    *,
    pr_number: int = 1,
    title: str = "Fix parser",
    file_name: str = "patchguard/parser.py",
) -> RiskReport:
    return RiskReport(
        pr=PullRequestInfo(
            owner="owner",
            repo="repo",
            number=pr_number,
            url=f"https://github.com/owner/repo/pull/{pr_number}",
            title=title,
        ),
        changed_files=[
            ChangedFile(
                filename=file_name,
                status="modified",
                additions=6,
                deletions=2,
                changes=8,
                classification="security_sensitive",
            )
        ],
        changed_functions=[
            ChangedFunction(
                file_path=file_name,
                qualified_name="parse_token",
                symbol_type="function",
                start_line=10,
                end_line=18,
                source_code="def parse_token(value):\n    return value.strip()\n",
                changed_lines=[12],
            )
        ],
        security_findings=[
            SecurityFinding(
                tool="bandit",
                severity="medium",
                confidence="high",
                filename=file_name,
                line_number=12,
                message="Possible hardcoded token handling.",
            )
        ],
        risk_score=55,
        risk_level=RiskLevel.MEDIUM,
        risk_reasons=[
            RiskReason(
                category="security",
                score_impact=20,
                reason="Security-sensitive token parser changed.",
            )
        ],
    )
