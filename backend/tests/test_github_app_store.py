from __future__ import annotations

from patchguard.app_models import (
    GitHubAppAnalysisJob,
    GitHubAppAnalysisReport,
    GitHubAppInstallation,
    GitHubAppJobStatus,
    GitHubAppRepository,
    GitHubWebhookDelivery,
)
from patchguard.models import MergeDecision, PolicyGateDecision, RiskLevel
from patchguard.storage.sqlite_store import GitHubAppSQLiteStore


def test_store_initializes_schema(tmp_path) -> None:
    store = GitHubAppSQLiteStore(tmp_path / "patchguard-app.db")

    store.initialize()

    assert store.count_rows("installations") == 0
    assert store.count_rows("repositories") == 0
    assert store.count_rows("webhook_deliveries") == 0
    assert store.count_rows("analysis_jobs") == 0
    assert store.count_rows("analysis_reports") == 0


def test_upsert_installation_updates_existing_row(tmp_path) -> None:
    store = initialized_store(tmp_path)

    created = store.upsert_installation(
        GitHubAppInstallation(
            github_installation_id=123,
            account_login="kai",
            account_type="User",
        )
    )
    updated = store.upsert_installation(
        GitHubAppInstallation(
            github_installation_id=123,
            account_login="kai-renamed",
            account_type="User",
            active=False,
        )
    )

    assert created.id == updated.id
    assert updated.account_login == "kai-renamed"
    assert updated.active is False
    assert store.count_rows("installations") == 1


def test_upsert_repository_updates_existing_row(tmp_path) -> None:
    store = initialized_store(tmp_path)
    installation = sample_installation(store)

    created = store.upsert_repository(
        GitHubAppRepository(
            installation_id=installation.id,
            github_repo_id=456,
            full_name="KaiwenMo1/patchguard",
            private=False,
            default_branch="main",
        )
    )
    updated = store.upsert_repository(
        GitHubAppRepository(
            installation_id=installation.id,
            github_repo_id=456,
            full_name="KaiwenMo1/patchguard-renamed",
            private=True,
            default_branch="trunk",
            active=False,
        )
    )

    assert created.id == updated.id
    assert updated.full_name == "KaiwenMo1/patchguard-renamed"
    assert updated.private is True
    assert updated.default_branch == "trunk"
    assert updated.active is False
    assert store.count_rows("repositories") == 1


def test_record_webhook_delivery_is_idempotent(tmp_path) -> None:
    store = initialized_store(tmp_path)

    first = store.record_webhook_delivery(
        GitHubWebhookDelivery(
            delivery_id="delivery-1",
            event_name="pull_request",
            action="opened",
            github_installation_id=123,
            repository_full_name="KaiwenMo1/patchguard",
            payload_sha256="abc",
        )
    )
    duplicate = store.record_webhook_delivery(
        GitHubWebhookDelivery(
            delivery_id="delivery-1",
            event_name="pull_request",
            action="synchronize",
            github_installation_id=123,
            repository_full_name="KaiwenMo1/patchguard",
            payload_sha256="different",
        )
    )

    assert first.created is True
    assert duplicate.created is False
    assert first.delivery.id == duplicate.delivery.id
    assert duplicate.delivery.action == "opened"
    assert duplicate.delivery.payload_sha256 == "abc"
    assert store.count_rows("webhook_deliveries") == 1


def test_create_and_update_analysis_job(tmp_path) -> None:
    store = initialized_store(tmp_path)
    installation = sample_installation(store)
    repository = sample_repository(store, installation.id)

    job = store.create_analysis_job(
        GitHubAppAnalysisJob(
            installation_id=installation.id,
            repository_id=repository.id,
            repository_full_name=repository.full_name,
            event_type="pull_request",
            pr_number=42,
            pr_url="https://github.com/KaiwenMo1/patchguard/pull/42",
            head_sha="head",
            base_sha="base",
        )
    )
    updated = store.update_job_status(
        job.id,
        GitHubAppJobStatus.PARTIAL,
        report_path=".patchguard/app_reports/report.json",
        error="Docker unavailable",
    )

    assert job.id is not None
    assert job.status == GitHubAppJobStatus.QUEUED
    assert updated.status == GitHubAppJobStatus.PARTIAL
    assert updated.report_path == ".patchguard/app_reports/report.json"
    assert updated.error == "Docker unavailable"
    assert store.count_rows("analysis_jobs") == 1


def test_claim_next_queued_job_marks_oldest_job_running_once(tmp_path) -> None:
    store = initialized_store(tmp_path)
    installation = sample_installation(store)
    repository = sample_repository(store, installation.id)
    first = store.create_analysis_job(
        GitHubAppAnalysisJob(
            installation_id=installation.id,
            repository_id=repository.id,
            repository_full_name=repository.full_name,
            event_type="pull_request.opened",
            pr_number=1,
            head_sha="head-1",
        )
    )
    store.create_analysis_job(
        GitHubAppAnalysisJob(
            installation_id=installation.id,
            repository_id=repository.id,
            repository_full_name=repository.full_name,
            event_type="pull_request.opened",
            pr_number=2,
            head_sha="head-2",
        )
    )

    claimed = store.claim_next_queued_job()
    next_claimed = store.claim_next_queued_job()
    empty_claim = store.claim_next_queued_job()

    assert claimed is not None
    assert claimed.id == first.id
    assert claimed.status == GitHubAppJobStatus.RUNNING
    assert next_claimed is not None
    assert next_claimed.id != first.id
    assert next_claimed.status == GitHubAppJobStatus.RUNNING
    assert empty_claim is None
    assert store.get_analysis_job(first.id).status == GitHubAppJobStatus.RUNNING


def test_attach_report_summary_is_upserted_by_job(tmp_path) -> None:
    store = initialized_store(tmp_path)
    installation = sample_installation(store)
    repository = sample_repository(store, installation.id)
    job = store.create_analysis_job(
        GitHubAppAnalysisJob(
            installation_id=installation.id,
            repository_id=repository.id,
            repository_full_name=repository.full_name,
            event_type="pull_request",
        )
    )

    first = store.attach_report_summary(
        GitHubAppAnalysisReport(
            job_id=job.id,
            risk_score=44,
            risk_level=RiskLevel.MEDIUM,
            merge_decision=MergeDecision.MERGE_WITH_CAUTION,
            policy_decision=PolicyGateDecision.WARN,
            report_json_path="first.json",
        )
    )
    updated = store.attach_report_summary(
        GitHubAppAnalysisReport(
            job_id=job.id,
            risk_score=80,
            risk_level=RiskLevel.CRITICAL,
            merge_decision=MergeDecision.DO_NOT_MERGE,
            policy_decision=PolicyGateDecision.BLOCK,
            report_json_path="updated.json",
        )
    )

    assert first.id == updated.id
    assert updated.risk_score == 80
    assert updated.risk_level == RiskLevel.CRITICAL
    assert updated.merge_decision == MergeDecision.DO_NOT_MERGE
    assert updated.policy_decision == PolicyGateDecision.BLOCK
    assert updated.report_json_path == "updated.json"
    assert store.count_rows("analysis_reports") == 1


def initialized_store(tmp_path) -> GitHubAppSQLiteStore:  # noqa: ANN001
    store = GitHubAppSQLiteStore(tmp_path / "patchguard-app.db")
    store.initialize()
    return store


def sample_installation(store: GitHubAppSQLiteStore) -> GitHubAppInstallation:
    installation = store.upsert_installation(
        GitHubAppInstallation(
            github_installation_id=123,
            account_login="KaiwenMo1",
            account_type="User",
        )
    )
    assert installation.id is not None
    return installation


def sample_repository(
    store: GitHubAppSQLiteStore,
    installation_id: int,
) -> GitHubAppRepository:
    repository = store.upsert_repository(
        GitHubAppRepository(
            installation_id=installation_id,
            github_repo_id=456,
            full_name="KaiwenMo1/patchguard",
            default_branch="main",
        )
    )
    assert repository.id is not None
    return repository
