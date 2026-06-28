"""GitHub App webhook signature verification and event routing."""

from __future__ import annotations

import hashlib
import hmac
from typing import Any

from patchguard.app_models import (
    GitHubAppAnalysisJob,
    GitHubAppInstallation,
    GitHubAppRepository,
    GitHubWebhookDelivery,
)
from patchguard.storage.sqlite_store import GitHubAppSQLiteStore

SUPPORTED_EVENTS = {"installation", "installation_repositories", "pull_request"}
ANALYZABLE_PULL_REQUEST_ACTIONS = {"opened", "reopened", "synchronize", "ready_for_review"}


class WebhookSignatureError(RuntimeError):
    """Raised when a webhook signature is missing or invalid."""


class GitHubAppWebhookRouter:
    """Verify, deduplicate, and route GitHub App webhook deliveries."""

    def __init__(
        self,
        *,
        store: GitHubAppSQLiteStore,
        webhook_secret: str,
        analyze_draft_prs: bool = False,
    ) -> None:
        self.store = store
        self.webhook_secret = webhook_secret
        self.analyze_draft_prs = analyze_draft_prs

    def handle(
        self,
        *,
        body: bytes,
        signature_header: str | None,
        event_name: str,
        delivery_id: str,
        payload: dict[str, Any],
    ) -> dict[str, object]:
        verify_signature(
            body=body,
            signature_header=signature_header,
            webhook_secret=self.webhook_secret,
        )
        delivery_result = self.store.record_webhook_delivery(
            GitHubWebhookDelivery(
                delivery_id=delivery_id,
                event_name=event_name,
                action=string_or_none(payload.get("action")),
                github_installation_id=installation_id_from_payload(payload),
                repository_full_name=repository_full_name_from_payload(payload),
                payload_sha256=hashlib.sha256(body).hexdigest(),
            )
        )
        if not delivery_result.created:
            return {
                "status": "duplicate",
                "event": event_name,
                "delivery_id": delivery_id,
                "jobs_created": 0,
            }
        if event_name not in SUPPORTED_EVENTS:
            return {
                "status": "ignored",
                "event": event_name,
                "delivery_id": delivery_id,
                "jobs_created": 0,
                "reason": "unsupported_event",
            }
        if event_name == "installation":
            self._route_installation(payload)
            return accepted_response(event_name, delivery_id, jobs_created=0)
        if event_name == "installation_repositories":
            self._route_installation_repositories(payload)
            return accepted_response(event_name, delivery_id, jobs_created=0)
        job_result = self._route_pull_request(payload)
        job, created = job_result if job_result is not None else (None, False)
        return accepted_response(
            event_name,
            delivery_id,
            jobs_created=1 if created else 0,
            job_id=job.id if job is not None else None,
        )

    def _route_installation(self, payload: dict[str, Any]) -> GitHubAppInstallation:
        installation = upsert_installation_from_payload(self.store, payload)
        action = string_or_none(payload.get("action"))
        if action == "deleted":
            self.store.mark_installation_repositories_inactive(
                required_model_id(installation.id, "installation")
            )
        for repository_payload in payload.get("repositories") or []:
            upsert_repository_from_payload(
                self.store,
                repository_payload,
                installation.id,
                selected=action != "deleted",
                active=action != "deleted",
            )
        return installation

    def _route_installation_repositories(self, payload: dict[str, Any]) -> None:
        installation = upsert_installation_from_payload(self.store, payload)
        for repository_payload in payload.get("repositories_added") or []:
            upsert_repository_from_payload(
                self.store,
                repository_payload,
                installation.id,
                selected=True,
                active=True,
            )
        for repository_payload in payload.get("repositories_removed") or []:
            upsert_repository_from_payload(
                self.store,
                repository_payload,
                installation.id,
                selected=False,
                active=False,
            )

    def _route_pull_request(
        self,
        payload: dict[str, Any],
    ) -> tuple[GitHubAppAnalysisJob, bool] | None:
        action = string_or_none(payload.get("action"))
        if action not in ANALYZABLE_PULL_REQUEST_ACTIONS:
            return None
        installation = upsert_installation_from_payload(self.store, payload)
        repository = upsert_repository_from_payload(
            self.store,
            required_mapping(payload, "repository"),
            installation.id,
            selected=True,
            active=True,
        )
        pull_request = required_mapping(payload, "pull_request")
        if bool(pull_request.get("draft", False)) and not self.analyze_draft_prs:
            return None
        head = mapping_or_empty(pull_request.get("head"))
        base = mapping_or_empty(pull_request.get("base"))
        return self.store.create_analysis_job_if_absent(
            GitHubAppAnalysisJob(
                installation_id=required_model_id(installation.id, "installation"),
                repository_id=required_model_id(repository.id, "repository"),
                repository_full_name=repository.full_name,
                event_type=f"pull_request.{action}",
                pr_number=int(pull_request.get("number") or payload.get("number") or 0),
                pr_url=string_or_none(pull_request.get("html_url")),
                head_sha=string_or_none(head.get("sha")),
                base_sha=string_or_none(base.get("sha")),
            )
        )


def verify_signature(
    *,
    body: bytes,
    signature_header: str | None,
    webhook_secret: str,
) -> None:
    if not signature_header:
        raise WebhookSignatureError("Missing X-Hub-Signature-256 header.")
    if not signature_header.startswith("sha256="):
        raise WebhookSignatureError("Invalid webhook signature format.")
    expected = "sha256=" + hmac.new(
        webhook_secret.encode("utf-8"),
        body,
        hashlib.sha256,
    ).hexdigest()
    if not hmac.compare_digest(expected, signature_header):
        raise WebhookSignatureError("Invalid webhook signature.")


def upsert_installation_from_payload(
    store: GitHubAppSQLiteStore,
    payload: dict[str, Any],
) -> GitHubAppInstallation:
    installation_payload = required_mapping(payload, "installation")
    account = mapping_or_empty(installation_payload.get("account"))
    repository = mapping_or_empty(payload.get("repository"))
    owner = mapping_or_empty(repository.get("owner"))
    return store.upsert_installation(
        GitHubAppInstallation(
            github_installation_id=int(required_value(installation_payload, "id")),
            account_login=string_or_none(account.get("login"))
            or string_or_none(owner.get("login"))
            or "unknown",
            account_type=string_or_none(account.get("type"))
            or string_or_none(owner.get("type"))
            or "unknown",
            active=payload.get("action") != "deleted",
        )
    )


def upsert_repository_from_payload(
    store: GitHubAppSQLiteStore,
    repository_payload: dict[str, Any],
    installation_id: int | None,
    *,
    selected: bool,
    active: bool,
) -> GitHubAppRepository:
    return store.upsert_repository(
        GitHubAppRepository(
            installation_id=required_model_id(installation_id, "installation"),
            github_repo_id=int(required_value(repository_payload, "id")),
            full_name=str(required_value(repository_payload, "full_name")),
            private=bool(repository_payload.get("private", False)),
            default_branch=string_or_none(repository_payload.get("default_branch")) or "main",
            selected=selected,
            active=active,
        )
    )


def accepted_response(
    event_name: str,
    delivery_id: str,
    *,
    jobs_created: int,
    job_id: int | None = None,
) -> dict[str, object]:
    response: dict[str, object] = {
        "status": "accepted",
        "event": event_name,
        "delivery_id": delivery_id,
        "jobs_created": jobs_created,
    }
    if job_id is not None:
        response["job_id"] = job_id
    return response


def installation_id_from_payload(payload: dict[str, Any]) -> int | None:
    installation = mapping_or_empty(payload.get("installation"))
    value = installation.get("id")
    if value is None:
        return None
    return int(value)


def repository_full_name_from_payload(payload: dict[str, Any]) -> str | None:
    repository = mapping_or_empty(payload.get("repository"))
    return string_or_none(repository.get("full_name"))


def required_mapping(payload: dict[str, Any], key: str) -> dict[str, Any]:
    value = payload.get(key)
    if not isinstance(value, dict):
        raise ValueError(f"Webhook payload is missing object field: {key}.")
    return value


def mapping_or_empty(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def required_value(payload: dict[str, Any], key: str) -> Any:
    value = payload.get(key)
    if value is None:
        raise ValueError(f"Webhook payload is missing field: {key}.")
    return value


def required_model_id(value: int | None, model_name: str) -> int:
    if value is None:
        raise ValueError(f"Stored {model_name} did not include an id.")
    return value


def string_or_none(value: Any) -> str | None:
    if value is None:
        return None
    return str(value)
