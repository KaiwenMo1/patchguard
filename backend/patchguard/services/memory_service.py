"""Local evidence memory for retrieving similar prior PatchGuard findings."""

from __future__ import annotations

import json
import re
import sqlite3
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from patchguard.models import (
    EvidenceMemoryHit,
    PatchGuardReport,
    RiskReport,
)
from patchguard.utils.file_utils import ensure_dir

DEFAULT_MEMORY_DB = Path(".patchguard") / "memory" / "patchguard-memory.db"
MAX_QUERY_TERMS = 18


@dataclass(frozen=True)
class MemoryIndexResult:
    reports_seen: int
    documents_indexed: int
    db_path: Path


class MemoryService:
    """SQLite-backed retrieval over prior PatchGuard reports.

    This is intentionally local and deterministic. It gives PatchGuard a RAG-like
    memory without requiring embeddings or paid API calls.
    """

    def __init__(self, db_path: str | Path = DEFAULT_MEMORY_DB) -> None:
        self.db_path = Path(db_path)
        ensure_dir(self.db_path.parent)
        self._fts_enabled: bool | None = None

    def initialize(self) -> None:
        with self._connect() as connection:
            connection.executescript(SCHEMA_SQL)
            self._ensure_fts(connection)

    def index_path(self, path: str | Path) -> MemoryIndexResult:
        self.initialize()
        root = Path(path)
        report_paths = list(iter_report_paths(root))
        indexed = 0
        for report_path in report_paths:
            indexed += self.index_report(report_path)
        return MemoryIndexResult(
            reports_seen=len(report_paths),
            documents_indexed=indexed,
            db_path=self.db_path,
        )

    def index_report(self, path: str | Path) -> int:
        self.initialize()
        report_path = Path(path)
        report = load_report(report_path)
        documents = documents_from_report(report, report_path)
        with self._connect() as connection:
            self._ensure_fts(connection)
            for document in documents:
                self._upsert_document(connection, document)
        return len(documents)

    def search_for_report(
        self,
        report: RiskReport | PatchGuardReport,
        *,
        limit: int = 5,
    ) -> list[EvidenceMemoryHit]:
        self.initialize()
        terms = query_terms_for_report(report)
        if not terms:
            return []
        repository = repository_for_report(report)
        pr_url = pr_url_for_report(report)
        return self.search(
            terms,
            repository=repository,
            exclude_pr_url=pr_url,
            limit=limit,
        )

    def search(
        self,
        terms: Iterable[str],
        *,
        repository: str | None = None,
        exclude_pr_url: str | None = None,
        limit: int = 5,
    ) -> list[EvidenceMemoryHit]:
        self.initialize()
        cleaned_terms = normalize_terms(terms)
        if not cleaned_terms:
            return []
        bounded_limit = max(1, min(limit, 25))
        with self._connect() as connection:
            self._ensure_fts(connection)
            if self._fts_enabled:
                rows = self._search_fts(
                    connection,
                    cleaned_terms,
                    repository=repository,
                    exclude_pr_url=exclude_pr_url,
                    limit=bounded_limit,
                )
            else:
                rows = self._search_like(
                    connection,
                    cleaned_terms,
                    repository=repository,
                    exclude_pr_url=exclude_pr_url,
                    limit=bounded_limit,
                )
        return [hit_from_row(row, rank=index) for index, row in enumerate(rows)]

    def _upsert_document(self, connection: sqlite3.Connection, document: dict[str, Any]) -> None:
        existing = connection.execute(
            "SELECT id FROM memory_documents WHERE source_id = ?",
            (document["source_id"],),
        ).fetchone()
        if existing is not None:
            row_id = int(existing["id"])
            if self._fts_enabled:
                connection.execute("DELETE FROM memory_fts WHERE rowid = ?", (row_id,))
            connection.execute(
                """
                UPDATE memory_documents
                SET repository = ?,
                    source_type = ?,
                    pr_url = ?,
                    report_path = ?,
                    title = ?,
                    summary = ?,
                    file_path = ?,
                    function_name = ?,
                    risk_score = ?,
                    risk_level = ?,
                    reasons_json = ?,
                    metadata_json = ?,
                    updated_at = datetime('now')
                WHERE id = ?
                """,
                document_values(document) + (row_id,),
            )
        else:
            cursor = connection.execute(
                """
                INSERT INTO memory_documents(
                    source_id,
                    repository,
                    source_type,
                    pr_url,
                    report_path,
                    title,
                    summary,
                    file_path,
                    function_name,
                    risk_score,
                    risk_level,
                    reasons_json,
                    metadata_json,
                    created_at,
                    updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'), datetime('now'))
                """,
                (document["source_id"],) + document_values(document),
            )
            row_id = int(cursor.lastrowid)
        if self._fts_enabled:
            connection.execute(
                """
                INSERT INTO memory_fts(rowid, title, summary, file_path, function_name)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    row_id,
                    document["title"],
                    document["summary"],
                    document.get("file_path") or "",
                    document.get("function_name") or "",
                ),
            )

    def _search_fts(
        self,
        connection: sqlite3.Connection,
        terms: list[str],
        *,
        repository: str | None,
        exclude_pr_url: str | None,
        limit: int,
    ) -> list[sqlite3.Row]:
        query = " OR ".join(quote_fts_term(term) for term in terms[:MAX_QUERY_TERMS])
        conditions = ["memory_fts MATCH ?"]
        params: list[Any] = [query]
        if repository:
            conditions.append("(repository = ? OR repository IS NULL)")
            params.append(repository)
        if exclude_pr_url:
            conditions.append("(pr_url IS NULL OR pr_url != ?)")
            params.append(exclude_pr_url)
        params.append(limit)
        return connection.execute(
            f"""
            SELECT memory_documents.*, bm25(memory_fts) AS rank
            FROM memory_fts
            JOIN memory_documents ON memory_documents.id = memory_fts.rowid
            WHERE {' AND '.join(conditions)}
            ORDER BY rank, updated_at DESC
            LIMIT ?
            """,
            tuple(params),
        ).fetchall()

    def _search_like(
        self,
        connection: sqlite3.Connection,
        terms: list[str],
        *,
        repository: str | None,
        exclude_pr_url: str | None,
        limit: int,
    ) -> list[sqlite3.Row]:
        clauses = []
        params: list[Any] = []
        for term in terms[:MAX_QUERY_TERMS]:
            like = f"%{term}%"
            clauses.append(
                "(title LIKE ? OR summary LIKE ? OR file_path LIKE ? OR function_name LIKE ?)"
            )
            params.extend([like, like, like, like])
        conditions = ["(" + " OR ".join(clauses) + ")"]
        if repository:
            conditions.append("(repository = ? OR repository IS NULL)")
            params.append(repository)
        if exclude_pr_url:
            conditions.append("(pr_url IS NULL OR pr_url != ?)")
            params.append(exclude_pr_url)
        params.append(limit)
        return connection.execute(
            f"""
            SELECT *, 0.0 AS rank
            FROM memory_documents
            WHERE {' AND '.join(conditions)}
            ORDER BY updated_at DESC
            LIMIT ?
            """,
            tuple(params),
        ).fetchall()

    def _ensure_fts(self, connection: sqlite3.Connection) -> None:
        if self._fts_enabled is not None:
            return
        try:
            connection.execute(
                """
                CREATE VIRTUAL TABLE IF NOT EXISTS memory_fts
                USING fts5(title, summary, file_path, function_name)
                """
            )
        except sqlite3.OperationalError:
            self._fts_enabled = False
        else:
            self._fts_enabled = True

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path)
        connection.row_factory = sqlite3.Row
        return connection


def iter_report_paths(path: Path) -> list[Path]:
    if path.is_file():
        return [path]
    return sorted(
        item
        for item in path.rglob("*.json")
        if item.is_file()
    )


def load_report(path: Path) -> RiskReport | PatchGuardReport:
    raw = path.read_text(encoding="utf-8")
    payload = json.loads(raw)
    if "input_pr_url" in payload:
        return PatchGuardReport.model_validate(payload)
    return RiskReport.model_validate(payload)


def documents_from_report(
    report: RiskReport | PatchGuardReport,
    report_path: Path,
) -> list[dict[str, Any]]:
    repository = repository_for_report(report)
    pr_url = pr_url_for_report(report)
    base = {
        "repository": repository,
        "pr_url": pr_url,
        "report_path": str(report_path),
        "risk_score": report.risk_score,
        "risk_level": value(report.risk_level),
        "reasons": [reason.reason for reason in report.risk_reasons[:8]],
    }
    documents: list[dict[str, Any]] = []
    report_key = safe_key(str(report_path.resolve()))
    for changed_file in report.changed_files:
        summary = " ".join(
            [
                f"Changed file {changed_file.filename}",
                f"classification {changed_file.classification or 'unknown'}",
                f"status {changed_file.status}",
                f"risk {report.risk_score} {value(report.risk_level)}",
                *base["reasons"],
            ]
        )
        documents.append(
            {
                **base,
                "source_id": f"{report_key}:file:{safe_key(changed_file.filename)}",
                "source_type": "changed_file",
                "title": f"Changed file: {changed_file.filename}",
                "summary": summary,
                "file_path": changed_file.filename,
                "function_name": None,
                "metadata": {"changes": changed_file.changes, "status": changed_file.status},
            }
        )
    for function in report.changed_functions:
        documents.append(
            {
                **base,
                "source_id": (
                    f"{report_key}:function:"
                    f"{safe_key(function.file_path)}:{safe_key(function.qualified_name)}"
                ),
                "source_type": "changed_function",
                "title": f"Changed function: {function.qualified_name}",
                "summary": " ".join(
                    [
                        f"{function.qualified_name} in {function.file_path}",
                        function.symbol_type,
                        *base["reasons"],
                    ]
                ),
                "file_path": function.file_path,
                "function_name": function.qualified_name,
                "metadata": {"changed_lines": function.changed_lines},
            }
        )
    for index, finding in enumerate(report.security_findings):
        file_path = finding.filename or finding.file
        documents.append(
            {
                **base,
                "source_id": f"{report_key}:security:{index}",
                "source_type": "security_finding",
                "title": f"{finding.tool} {finding.severity}: {file_path or 'unknown file'}",
                "summary": finding.message or finding.issue_text,
                "file_path": file_path,
                "function_name": None,
                "metadata": {"severity": finding.severity, "confidence": finding.confidence},
            }
        )
    for index, mapping in enumerate(report.failure_mappings):
        documents.append(
            {
                **base,
                "source_id": f"{report_key}:failure:{index}",
                "source_type": "generated_test_failure",
                "title": f"Generated test failed: {mapping.failed_test}",
                "summary": " ".join(
                    [
                        mapping.failure_summary,
                        mapping.risk_message,
                        mapping.behavior_checked or "",
                    ]
                ),
                "file_path": mapping.target_file,
                "function_name": mapping.target_function,
                "metadata": {"next_step": mapping.suggested_next_step},
            }
        )
    return documents


def query_terms_for_report(report: RiskReport | PatchGuardReport) -> list[str]:
    terms: list[str] = []
    terms.extend(file.filename for file in report.changed_files)
    terms.extend(Path(file.filename).name for file in report.changed_files)
    terms.extend(function.qualified_name for function in report.changed_functions)
    terms.extend(function.file_path for function in report.changed_functions)
    terms.extend(reason.category for reason in report.risk_reasons)
    terms.extend(reason.reason for reason in report.risk_reasons[:5])
    title = getattr(report.pr, "title", None) if report.pr else None
    if title:
        terms.append(title)
    return normalize_terms(terms)


def normalize_terms(terms: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    normalized: list[str] = []
    for term in terms:
        for part in re.split(r"[^A-Za-z0-9_./-]+", str(term)):
            cleaned = part.strip(" ./-").lower()
            if len(cleaned) < 3 or cleaned in seen:
                continue
            seen.add(cleaned)
            normalized.append(cleaned)
    normalized.sort(key=lambda item: (-len(item), item))
    return normalized[:MAX_QUERY_TERMS]


def quote_fts_term(term: str) -> str:
    return '"' + term.replace('"', '""') + '"'


def document_values(document: dict[str, Any]) -> tuple[Any, ...]:
    return (
        document.get("repository"),
        document["source_type"],
        document.get("pr_url"),
        document.get("report_path"),
        document["title"],
        document["summary"],
        document.get("file_path"),
        document.get("function_name"),
        document.get("risk_score"),
        document.get("risk_level"),
        json.dumps(document.get("reasons") or [], sort_keys=True),
        json.dumps(document.get("metadata") or {}, sort_keys=True),
    )


def hit_from_row(row: sqlite3.Row, *, rank: int) -> EvidenceMemoryHit:
    reasons = json.loads(row["reasons_json"] or "[]")
    raw_rank = row["rank"] if "rank" in row.keys() else rank
    score = 100.0 - min(95.0, abs(float(raw_rank)) if raw_rank is not None else rank * 10.0)
    return EvidenceMemoryHit(
        source_id=row["source_id"],
        source_type=row["source_type"],
        title=row["title"],
        summary=row["summary"],
        score=round(score, 3),
        repository=row["repository"],
        pr_url=row["pr_url"],
        report_path=row["report_path"],
        file_path=row["file_path"],
        function_name=row["function_name"],
        risk_score=row["risk_score"],
        risk_level=row["risk_level"],
        reasons=reasons,
    )


def repository_for_report(report: RiskReport | PatchGuardReport) -> str | None:
    pr = report.pr
    if pr is None:
        return None
    if isinstance(report, RiskReport):
        return f"{pr.owner}/{pr.repo}"
    return pr.base_repo_full_name or f"{pr.owner}/{pr.repo}"


def pr_url_for_report(report: RiskReport | PatchGuardReport) -> str | None:
    pr = report.pr
    if pr is None:
        return None
    return getattr(pr, "url", None) or getattr(pr, "html_url", None)


def safe_key(value_: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "-", value_).strip("-")[:180]


def value(value_: Any) -> str:
    return getattr(value_, "value", str(value_))


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS memory_documents (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_id TEXT NOT NULL UNIQUE,
    repository TEXT,
    source_type TEXT NOT NULL,
    pr_url TEXT,
    report_path TEXT,
    title TEXT NOT NULL,
    summary TEXT NOT NULL,
    file_path TEXT,
    function_name TEXT,
    risk_score INTEGER,
    risk_level TEXT,
    reasons_json TEXT NOT NULL DEFAULT '[]',
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_memory_repository
ON memory_documents(repository);

CREATE INDEX IF NOT EXISTS idx_memory_pr_url
ON memory_documents(pr_url);
"""
