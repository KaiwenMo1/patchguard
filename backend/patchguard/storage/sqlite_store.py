"""SQLite storage for the PatchGuard GitHub App MVP."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from patchguard.app_models import (
    GitHubAppAnalysisJob,
    GitHubAppAnalysisReport,
    GitHubAppInstallation,
    GitHubAppJobStatus,
    GitHubAppRepository,
    GitHubWebhookDelivery,
    WebhookDeliveryResult,
)
from patchguard.models import MergeDecision, PolicyGateDecision, RiskLevel

SCHEMA_VERSION = 3


class GitHubAppSQLiteStore:
    """Small SQLite repository for GitHub App installations and analysis history."""

    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path)

    def initialize(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.executescript(SCHEMA_SQL)
            self._apply_migrations(connection)
            connection.execute(
                """
                INSERT INTO schema_migrations(version, applied_at)
                VALUES (?, ?)
                ON CONFLICT(version) DO NOTHING
                """,
                (SCHEMA_VERSION, utc_now_iso()),
            )

    def upsert_installation(
        self,
        installation: GitHubAppInstallation,
    ) -> GitHubAppInstallation:
        now = utc_now_iso()
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO installations(
                    github_installation_id,
                    account_login,
                    account_type,
                    active,
                    created_at,
                    updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(github_installation_id) DO UPDATE SET
                    account_login = excluded.account_login,
                    account_type = excluded.account_type,
                    active = excluded.active,
                    updated_at = excluded.updated_at
                """,
                (
                    installation.github_installation_id,
                    installation.account_login,
                    installation.account_type,
                    bool_to_int(installation.active),
                    now,
                    now,
                ),
            )
            return self.get_installation_by_github_id(
                installation.github_installation_id,
                connection=connection,
            )

    def get_installation_by_github_id(
        self,
        github_installation_id: int,
        *,
        connection: sqlite3.Connection | None = None,
    ) -> GitHubAppInstallation:
        row = self._fetch_one(
            """
            SELECT *
            FROM installations
            WHERE github_installation_id = ?
            """,
            (github_installation_id,),
            connection=connection,
        )
        if row is None:
            raise KeyError(f"GitHub installation not found: {github_installation_id}")
        return installation_from_row(row)

    def get_installation(
        self,
        installation_id: int,
        *,
        connection: sqlite3.Connection | None = None,
    ) -> GitHubAppInstallation:
        row = self._fetch_one(
            """
            SELECT *
            FROM installations
            WHERE id = ?
            """,
            (installation_id,),
            connection=connection,
        )
        if row is None:
            raise KeyError(f"Installation not found: {installation_id}")
        return installation_from_row(row)

    def list_installations(self, *, active_only: bool = False) -> list[GitHubAppInstallation]:
        query = """
            SELECT *
            FROM installations
        """
        params: tuple[Any, ...] = ()
        if active_only:
            query += " WHERE active = ?"
            params = (1,)
        query += " ORDER BY updated_at DESC, id DESC"
        with self._connect() as connection:
            rows = connection.execute(query, params).fetchall()
            return [installation_from_row(row) for row in rows]

    def upsert_repository(self, repository: GitHubAppRepository) -> GitHubAppRepository:
        now = utc_now_iso()
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO repositories(
                    installation_id,
                    github_repo_id,
                    full_name,
                    private,
                    default_branch,
                    selected,
                    active,
                    created_at,
                    updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(github_repo_id) DO UPDATE SET
                    installation_id = excluded.installation_id,
                    full_name = excluded.full_name,
                    private = excluded.private,
                    default_branch = excluded.default_branch,
                    selected = excluded.selected,
                    active = excluded.active,
                    updated_at = excluded.updated_at
                """,
                (
                    repository.installation_id,
                    repository.github_repo_id,
                    repository.full_name,
                    bool_to_int(repository.private),
                    repository.default_branch,
                    bool_to_int(repository.selected),
                    bool_to_int(repository.active),
                    now,
                    now,
                ),
            )
            return self.get_repository_by_github_id(
                repository.github_repo_id,
                connection=connection,
            )

    def get_repository_by_github_id(
        self,
        github_repo_id: int,
        *,
        connection: sqlite3.Connection | None = None,
    ) -> GitHubAppRepository:
        row = self._fetch_one(
            """
            SELECT *
            FROM repositories
            WHERE github_repo_id = ?
            """,
            (github_repo_id,),
            connection=connection,
        )
        if row is None:
            raise KeyError(f"GitHub repository not found: {github_repo_id}")
        return repository_from_row(row)

    def get_repository_by_full_name(
        self,
        full_name: str,
        *,
        connection: sqlite3.Connection | None = None,
    ) -> GitHubAppRepository:
        row = self._fetch_one(
            """
            SELECT *
            FROM repositories
            WHERE full_name = ? COLLATE NOCASE
            ORDER BY active DESC, selected DESC, id DESC
            LIMIT 1
            """,
            (full_name,),
            connection=connection,
        )
        if row is None:
            raise KeyError(f"GitHub repository not found: {full_name}")
        return repository_from_row(row)

    def list_repositories(
        self,
        *,
        active_only: bool = False,
        selected_only: bool = False,
    ) -> list[GitHubAppRepository]:
        conditions: list[str] = []
        params: list[Any] = []
        if active_only:
            conditions.append("active = ?")
            params.append(1)
        if selected_only:
            conditions.append("selected = ?")
            params.append(1)

        query = """
            SELECT *
            FROM repositories
        """
        if conditions:
            query += " WHERE " + " AND ".join(conditions)
        query += " ORDER BY active DESC, selected DESC, full_name COLLATE NOCASE"
        with self._connect() as connection:
            rows = connection.execute(query, tuple(params)).fetchall()
            return [repository_from_row(row) for row in rows]

    def list_repositories_for_installation(
        self,
        installation_id: int,
    ) -> list[GitHubAppRepository]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT *
                FROM repositories
                WHERE installation_id = ?
                ORDER BY full_name
                """,
                (installation_id,),
            ).fetchall()
            return [repository_from_row(row) for row in rows]

    def list_active_selected_repositories_for_installation(
        self,
        installation_id: int,
    ) -> list[GitHubAppRepository]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT *
                FROM repositories
                WHERE installation_id = ?
                  AND selected = 1
                  AND active = 1
                ORDER BY full_name
                """,
                (installation_id,),
            ).fetchall()
            return [repository_from_row(row) for row in rows]

    def mark_installation_repositories_inactive(self, installation_id: int) -> int:
        with self._connect() as connection:
            cursor = connection.execute(
                """
                UPDATE repositories
                SET selected = 0,
                    active = 0,
                    updated_at = ?
                WHERE installation_id = ?
                """,
                (utc_now_iso(), installation_id),
            )
            return cursor.rowcount

    def record_webhook_delivery(
        self,
        delivery: GitHubWebhookDelivery,
    ) -> WebhookDeliveryResult:
        now = utc_now_iso()
        with self._connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO webhook_deliveries(
                    delivery_id,
                    event_name,
                    action,
                    github_installation_id,
                    repository_full_name,
                    payload_sha256,
                    received_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(delivery_id) DO NOTHING
                """,
                (
                    delivery.delivery_id,
                    delivery.event_name,
                    delivery.action,
                    delivery.github_installation_id,
                    delivery.repository_full_name,
                    delivery.payload_sha256,
                    now,
                ),
            )
            row = self._fetch_one(
                """
                SELECT *
                FROM webhook_deliveries
                WHERE delivery_id = ?
                """,
                (delivery.delivery_id,),
                connection=connection,
            )
            if row is None:
                raise RuntimeError(f"Webhook delivery was not stored: {delivery.delivery_id}")
            return WebhookDeliveryResult(
                delivery=webhook_delivery_from_row(row),
                created=cursor.rowcount == 1,
            )

    def create_analysis_job(self, job: GitHubAppAnalysisJob) -> GitHubAppAnalysisJob:
        now = utc_now_iso()
        with self._connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO analysis_jobs(
                    installation_id,
                    repository_id,
                    repository_full_name,
                    event_type,
                    status,
                    pr_number,
                    pr_url,
                    head_sha,
                    base_sha,
                    check_run_id,
                    check_run_url,
                    report_path,
                    error,
                    created_at,
                    updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    job.installation_id,
                    job.repository_id,
                    job.repository_full_name,
                    job.event_type,
                    job.status.value,
                    job.pr_number,
                    job.pr_url,
                    job.head_sha,
                    job.base_sha,
                    job.check_run_id,
                    job.check_run_url,
                    job.report_path,
                    job.error,
                    now,
                    now,
                ),
            )
            return self.get_analysis_job(cursor.lastrowid, connection=connection)

    def create_analysis_job_if_absent(
        self,
        job: GitHubAppAnalysisJob,
    ) -> tuple[GitHubAppAnalysisJob, bool]:
        if job.pr_number is None or not job.head_sha:
            created = self.create_analysis_job(job)
            return created, True
        with self._connect() as connection:
            existing = self._get_analysis_job_for_pr_head(
                repository_id=job.repository_id,
                pr_number=job.pr_number,
                head_sha=job.head_sha,
                connection=connection,
            )
            if existing is not None:
                return existing, False
            now = utc_now_iso()
            cursor = connection.execute(
                """
                INSERT INTO analysis_jobs(
                    installation_id,
                    repository_id,
                    repository_full_name,
                    event_type,
                    status,
                    pr_number,
                    pr_url,
                    head_sha,
                    base_sha,
                    check_run_id,
                    check_run_url,
                    report_path,
                    error,
                    created_at,
                    updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    job.installation_id,
                    job.repository_id,
                    job.repository_full_name,
                    job.event_type,
                    job.status.value,
                    job.pr_number,
                    job.pr_url,
                    job.head_sha,
                    job.base_sha,
                    job.check_run_id,
                    job.check_run_url,
                    job.report_path,
                    job.error,
                    now,
                    now,
                ),
            )
            return self.get_analysis_job(cursor.lastrowid, connection=connection), True

    def get_analysis_job(
        self,
        job_id: int,
        *,
        connection: sqlite3.Connection | None = None,
    ) -> GitHubAppAnalysisJob:
        row = self._fetch_one(
            """
            SELECT *
            FROM analysis_jobs
            WHERE id = ?
            """,
            (job_id,),
            connection=connection,
        )
        if row is None:
            raise KeyError(f"Analysis job not found: {job_id}")
        return analysis_job_from_row(row)

    def get_next_queued_job(self) -> GitHubAppAnalysisJob | None:
        row = self._fetch_one(
            """
            SELECT *
            FROM analysis_jobs
            WHERE status = ?
            ORDER BY id
            LIMIT 1
            """,
            (GitHubAppJobStatus.QUEUED.value,),
        )
        if row is None:
            return None
        return analysis_job_from_row(row)

    def claim_next_queued_job(self) -> GitHubAppAnalysisJob | None:
        """Atomically move the oldest queued job to running and return it."""

        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """
                SELECT id
                FROM analysis_jobs
                WHERE status = ?
                ORDER BY id
                LIMIT 1
                """,
                (GitHubAppJobStatus.QUEUED.value,),
            ).fetchone()
            if row is None:
                return None
            job_id = int(row["id"])
            connection.execute(
                """
                UPDATE analysis_jobs
                SET status = ?,
                    updated_at = ?
                WHERE id = ?
                  AND status = ?
                """,
                (
                    GitHubAppJobStatus.RUNNING.value,
                    utc_now_iso(),
                    job_id,
                    GitHubAppJobStatus.QUEUED.value,
                ),
            )
            return self.get_analysis_job(job_id, connection=connection)

    def list_analysis_jobs_for_repository(
        self,
        repository_id: int,
        *,
        limit: int = 50,
    ) -> list[GitHubAppAnalysisJob]:
        bounded_limit = max(1, min(limit, 200))
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT *
                FROM analysis_jobs
                WHERE repository_id = ?
                ORDER BY id DESC
                LIMIT ?
                """,
                (repository_id, bounded_limit),
            ).fetchall()
            return [analysis_job_from_row(row) for row in rows]

    def get_analysis_job_for_pr_head(
        self,
        *,
        repository_id: int,
        pr_number: int,
        head_sha: str,
    ) -> GitHubAppAnalysisJob | None:
        with self._connect() as connection:
            return self._get_analysis_job_for_pr_head(
                repository_id=repository_id,
                pr_number=pr_number,
                head_sha=head_sha,
                connection=connection,
            )

    def attach_check_run_to_job(
        self,
        job_id: int,
        *,
        check_run_id: int,
        check_run_url: str | None = None,
    ) -> GitHubAppAnalysisJob:
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE analysis_jobs
                SET check_run_id = ?,
                    check_run_url = ?,
                    updated_at = ?
                WHERE id = ?
                """,
                (check_run_id, check_run_url, utc_now_iso(), job_id),
            )
            return self.get_analysis_job(job_id, connection=connection)

    def update_job_status(
        self,
        job_id: int,
        status: GitHubAppJobStatus,
        *,
        report_path: str | None = None,
        error: str | None = None,
    ) -> GitHubAppAnalysisJob:
        with self._connect() as connection:
            existing = self.get_analysis_job(job_id, connection=connection)
            connection.execute(
                """
                UPDATE analysis_jobs
                SET status = ?,
                    report_path = ?,
                    error = ?,
                    updated_at = ?
                WHERE id = ?
                """,
                (
                    status.value,
                    report_path if report_path is not None else existing.report_path,
                    error if error is not None else existing.error,
                    utc_now_iso(),
                    job_id,
                ),
            )
            return self.get_analysis_job(job_id, connection=connection)

    def attach_report_summary(
        self,
        report: GitHubAppAnalysisReport,
    ) -> GitHubAppAnalysisReport:
        now = utc_now_iso()
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO analysis_reports(
                    job_id,
                    risk_score,
                    risk_level,
                    merge_decision,
                    policy_decision,
                    report_json_path,
                    created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(job_id) DO UPDATE SET
                    risk_score = excluded.risk_score,
                    risk_level = excluded.risk_level,
                    merge_decision = excluded.merge_decision,
                    policy_decision = excluded.policy_decision,
                    report_json_path = excluded.report_json_path,
                    created_at = excluded.created_at
                """,
                (
                    report.job_id,
                    report.risk_score,
                    report.risk_level.value,
                    report.merge_decision.value,
                    report.policy_decision.value,
                    report.report_json_path,
                    now,
                ),
            )
            return self.get_report_summary_by_job_id(report.job_id, connection=connection)

    def get_report_summary_by_job_id(
        self,
        job_id: int,
        *,
        connection: sqlite3.Connection | None = None,
    ) -> GitHubAppAnalysisReport:
        row = self._fetch_one(
            """
            SELECT *
            FROM analysis_reports
            WHERE job_id = ?
            """,
            (job_id,),
            connection=connection,
        )
        if row is None:
            raise KeyError(f"Analysis report summary not found for job: {job_id}")
        return analysis_report_from_row(row)

    def count_rows(self, table_name: str) -> int:
        allowed_tables = {
            "installations",
            "repositories",
            "webhook_deliveries",
            "analysis_jobs",
            "analysis_reports",
        }
        if table_name not in allowed_tables:
            raise ValueError(f"Unsupported table name: {table_name}")
        with self._connect() as connection:
            return int(connection.execute(f"SELECT COUNT(*) FROM {table_name}").fetchone()[0])

    def _apply_migrations(self, connection: sqlite3.Connection) -> None:
        if not column_exists(connection, "analysis_jobs", "repository_full_name"):
            connection.execute(
                """
                ALTER TABLE analysis_jobs
                ADD COLUMN repository_full_name TEXT NOT NULL DEFAULT ''
                """
            )
        if not column_exists(connection, "analysis_jobs", "check_run_id"):
            connection.execute(
                """
                ALTER TABLE analysis_jobs
                ADD COLUMN check_run_id INTEGER
                """
            )
        if not column_exists(connection, "analysis_jobs", "check_run_url"):
            connection.execute(
                """
                ALTER TABLE analysis_jobs
                ADD COLUMN check_run_url TEXT
                """
            )

    def _get_analysis_job_for_pr_head(
        self,
        *,
        repository_id: int,
        pr_number: int,
        head_sha: str,
        connection: sqlite3.Connection,
    ) -> GitHubAppAnalysisJob | None:
        row = connection.execute(
            """
            SELECT *
            FROM analysis_jobs
            WHERE repository_id = ?
              AND pr_number = ?
              AND head_sha = ?
            ORDER BY id
            LIMIT 1
            """,
            (repository_id, pr_number, head_sha),
        ).fetchone()
        if row is None:
            return None
        return analysis_job_from_row(row)


    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.db_path)
        connection.row_factory = sqlite3.Row
        try:
            connection.execute("PRAGMA foreign_keys = ON")
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _fetch_one(
        self,
        query: str,
        params: tuple[Any, ...],
        *,
        connection: sqlite3.Connection | None = None,
    ) -> sqlite3.Row | None:
        if connection is not None:
            return connection.execute(query, params).fetchone()
        with self._connect() as own_connection:
            return own_connection.execute(query, params).fetchone()


def installation_from_row(row: sqlite3.Row) -> GitHubAppInstallation:
    return GitHubAppInstallation(
        id=row["id"],
        github_installation_id=row["github_installation_id"],
        account_login=row["account_login"],
        account_type=row["account_type"],
        active=int_to_bool(row["active"]),
        created_at=parse_datetime(row["created_at"]),
        updated_at=parse_datetime(row["updated_at"]),
    )


def repository_from_row(row: sqlite3.Row) -> GitHubAppRepository:
    return GitHubAppRepository(
        id=row["id"],
        installation_id=row["installation_id"],
        github_repo_id=row["github_repo_id"],
        full_name=row["full_name"],
        private=int_to_bool(row["private"]),
        default_branch=row["default_branch"],
        selected=int_to_bool(row["selected"]),
        active=int_to_bool(row["active"]),
        created_at=parse_datetime(row["created_at"]),
        updated_at=parse_datetime(row["updated_at"]),
    )


def webhook_delivery_from_row(row: sqlite3.Row) -> GitHubWebhookDelivery:
    return GitHubWebhookDelivery(
        id=row["id"],
        delivery_id=row["delivery_id"],
        event_name=row["event_name"],
        action=row["action"],
        github_installation_id=row["github_installation_id"],
        repository_full_name=row["repository_full_name"],
        payload_sha256=row["payload_sha256"],
        received_at=parse_datetime(row["received_at"]),
    )


def analysis_job_from_row(row: sqlite3.Row) -> GitHubAppAnalysisJob:
    return GitHubAppAnalysisJob(
        id=row["id"],
        installation_id=row["installation_id"],
        repository_id=row["repository_id"],
        repository_full_name=row["repository_full_name"],
        event_type=row["event_type"],
        status=GitHubAppJobStatus(row["status"]),
        pr_number=row["pr_number"],
        pr_url=row["pr_url"],
        head_sha=row["head_sha"],
        base_sha=row["base_sha"],
        check_run_id=row["check_run_id"],
        check_run_url=row["check_run_url"],
        report_path=row["report_path"],
        error=row["error"],
        created_at=parse_datetime(row["created_at"]),
        updated_at=parse_datetime(row["updated_at"]),
    )


def analysis_report_from_row(row: sqlite3.Row) -> GitHubAppAnalysisReport:
    return GitHubAppAnalysisReport(
        id=row["id"],
        job_id=row["job_id"],
        risk_score=row["risk_score"],
        risk_level=RiskLevel(row["risk_level"]),
        merge_decision=MergeDecision(row["merge_decision"]),
        policy_decision=PolicyGateDecision(row["policy_decision"]),
        report_json_path=row["report_json_path"],
        created_at=parse_datetime(row["created_at"]),
    )


def bool_to_int(value: bool) -> int:
    return 1 if value else 0


def int_to_bool(value: int) -> bool:
    return bool(value)


def utc_now_iso() -> str:
    return datetime.now(UTC).isoformat()


def parse_datetime(value: str | None) -> datetime | None:
    if value is None:
        return None
    return datetime.fromisoformat(value)


def column_exists(connection: sqlite3.Connection, table_name: str, column_name: str) -> bool:
    rows = connection.execute(f"PRAGMA table_info({table_name})").fetchall()
    return any(row["name"] == column_name for row in rows)


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    version INTEGER PRIMARY KEY,
    applied_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS installations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    github_installation_id INTEGER NOT NULL UNIQUE,
    account_login TEXT NOT NULL,
    account_type TEXT NOT NULL,
    active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS repositories (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    installation_id INTEGER NOT NULL,
    github_repo_id INTEGER NOT NULL UNIQUE,
    full_name TEXT NOT NULL,
    private INTEGER NOT NULL DEFAULT 0,
    default_branch TEXT NOT NULL,
    selected INTEGER NOT NULL DEFAULT 1,
    active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (installation_id) REFERENCES installations(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_repositories_installation_id
ON repositories(installation_id);

CREATE INDEX IF NOT EXISTS idx_repositories_full_name_nocase
ON repositories(full_name COLLATE NOCASE);

CREATE TABLE IF NOT EXISTS webhook_deliveries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    delivery_id TEXT NOT NULL UNIQUE,
    event_name TEXT NOT NULL,
    action TEXT,
    github_installation_id INTEGER,
    repository_full_name TEXT,
    payload_sha256 TEXT,
    received_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS analysis_jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    installation_id INTEGER NOT NULL,
    repository_id INTEGER NOT NULL,
    repository_full_name TEXT NOT NULL DEFAULT '',
    event_type TEXT NOT NULL,
    status TEXT NOT NULL,
    pr_number INTEGER,
    pr_url TEXT,
    head_sha TEXT,
    base_sha TEXT,
    check_run_id INTEGER,
    check_run_url TEXT,
    report_path TEXT,
    error TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (installation_id) REFERENCES installations(id) ON DELETE CASCADE,
    FOREIGN KEY (repository_id) REFERENCES repositories(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_analysis_jobs_status
ON analysis_jobs(status);

CREATE INDEX IF NOT EXISTS idx_analysis_jobs_repository_id
ON analysis_jobs(repository_id);

CREATE TABLE IF NOT EXISTS analysis_reports (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id INTEGER NOT NULL UNIQUE,
    risk_score INTEGER NOT NULL,
    risk_level TEXT NOT NULL,
    merge_decision TEXT NOT NULL,
    policy_decision TEXT NOT NULL,
    report_json_path TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY (job_id) REFERENCES analysis_jobs(id) ON DELETE CASCADE
);
"""
