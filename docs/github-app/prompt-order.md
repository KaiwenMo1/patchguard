# GitHub App Prompt Order

Use these prompts in order. Do not skip verification commands. Do not start a later phase until the previous phase works.

## Prompt 0: GitHub App North Star

You are helping me evolve PatchGuard from a local CLI/GitHub Action into an installable GitHub App.

PatchGuard's product promise stays the same:

- Input: GitHub pull request or repository event.
- Output: evidence-backed merge-risk report.
- Evidence means: diff analysis, changed-function extraction, Docker-based test execution, generated tests when enabled, static/security scans, deterministic risk score, and policy decision.

The GitHub App goal:

- Install once on selected repositories.
- Automatically analyze new PRs.
- Store report history.
- Publish GitHub Checks.
- Show a cross-repository dashboard.

Constraints:

- Keep Python-only analysis for now.
- Keep the existing CLI and GitHub Action working.
- Do not fake GitHub App events, test results, or check results.
- Verify webhook signatures.
- Use minimum GitHub App permissions.
- Do not run full analysis inside the webhook request.
- Use SQLite first.
- Use Docker for untrusted repository execution.
- Preserve partial reports when failures happen.

Before each phase, inspect the current code. After each phase, run tests and fix failures before moving on.

## Prompt 1: GitHub App Data Models And SQLite Store

Implement the storage foundation for the GitHub App MVP.

Goal:
Persist installations, repositories, webhook deliveries, analysis jobs, and report summaries locally.

Implement:

1. `backend/patchguard/storage/sqlite_store.py`
2. Pydantic models in `backend/patchguard/app_models.py`
3. Tables:
   - installations
   - repositories
   - webhook_deliveries
   - analysis_jobs
   - analysis_reports
4. Store initialization/migration function.
5. Basic CRUD functions:
   - upsert installation
   - upsert repository
   - record webhook delivery
   - create analysis job
   - update job status
   - attach report summary
6. Tests using a temporary SQLite database.

Acceptance criteria:

- Tests create a temp database and pass.
- Store handles duplicate webhook delivery IDs idempotently.
- No existing CLI behavior breaks.

Run:

```bash
python -m pytest backend/tests/test_github_app_store.py -q
python -m pytest -q
```

## Prompt 2: GitHub App Auth Service

Implement GitHub App authentication helpers.

Goal:
Generate app JWTs and installation access tokens.

Implement:

1. `github_app_auth_service.py`
2. Load config from env:
   - `PATCHGUARD_GITHUB_APP_ID`
   - `PATCHGUARD_GITHUB_APP_PRIVATE_KEY_PATH`
   - `PATCHGUARD_GITHUB_WEBHOOK_SECRET`
3. Generate JWT signed with the app private key.
4. Exchange JWT for installation token.
5. Token response model with expiration.
6. Unit tests with mocked HTTP calls.

Acceptance criteria:

- Missing config gives clear errors.
- JWT creation is tested without hitting GitHub.
- Installation token request is mocked.

Run:

```bash
python -m pytest backend/tests/test_github_app_auth_service.py -q
python -m pytest -q
```

## Prompt 3: Webhook Signature Verification And Event Routing

Implement the webhook endpoint foundation.

Goal:
FastAPI can receive GitHub App webhooks safely.

Implement:

1. `POST /github/webhook` in `api_app.py` or a router module.
2. Verify `X-Hub-Signature-256`.
3. Read `X-GitHub-Event`.
4. Read `X-GitHub-Delivery`.
5. Reject invalid signatures.
6. Deduplicate delivery IDs in SQLite.
7. Route supported events:
   - installation
   - installation_repositories
   - pull_request
8. Ignore unsupported events safely.

Acceptance criteria:

- Invalid signature returns 401/403.
- Valid signature records delivery.
- Duplicate delivery does not create duplicate jobs.
- Unit tests cover valid, invalid, duplicate, unsupported.

Run:

```bash
python -m pytest backend/tests/test_github_app_webhook.py -q
python -m pytest -q
```

## Prompt 4: Installation And Repository Sync

Handle installation lifecycle events.

Goal:
When the app is installed or repos are added/removed, PatchGuard stores the installation state.

Implement:

1. Handle `installation.created`.
2. Handle `installation.deleted`.
3. Handle `installation_repositories.added`.
4. Handle `installation_repositories.removed`.
5. Store selected repositories.
6. Mark removed repos inactive instead of deleting history.

Acceptance criteria:

- Installation event updates SQLite.
- Added repos are stored.
- Removed repos are marked inactive.
- Tests use realistic GitHub webhook payload fixtures.

Run:

```bash
python -m pytest backend/tests/test_github_app_installations.py -q
python -m pytest -q
```

## Prompt 5: Pull Request Event To Analysis Job

Turn PR webhooks into queued analysis jobs.

Goal:
New or updated PRs create analysis jobs without running analysis inside the request.

Implement:

1. Handle PR actions:
   - opened
   - synchronize
   - reopened
   - ready_for_review
2. Ignore draft PRs unless configured otherwise.
3. Create analysis job with:
   - installation id
   - repo full name
   - PR number
   - PR URL
   - base SHA
   - head SHA
4. Add job status values:
   - queued
   - running
   - completed
   - failed
   - partial
5. Tests for event-to-job behavior.

Acceptance criteria:

- PR event returns quickly.
- Job is created exactly once per delivery.
- Duplicate delivery does not duplicate job.

Run:

```bash
python -m pytest backend/tests/test_github_app_pr_jobs.py -q
python -m pytest -q
```

## Prompt 6: Local Analysis Worker

Implement a local worker that processes queued GitHub App jobs.

Goal:
The worker runs the existing PatchGuard pipeline for queued PR jobs.

Implement:

1. `github_app_job_service.py`
2. Function:
   - `process_next_job()`
   - `process_job(job_id)`
3. Reuse existing `SkeletonReportService`.
4. Store report JSON under `.patchguard/app_reports/`.
5. Store report summary in SQLite.
6. Capture errors and mark job failed/partial.
7. Tests with mocked report service.

Acceptance criteria:

- Worker marks job running then completed/partial/failed.
- Report path is stored.
- Existing tests still pass.

Run:

```bash
python -m pytest backend/tests/test_github_app_job_service.py -q
python -m pytest -q
```

## Prompt 7: GitHub Check Run Publishing

Publish analysis results as GitHub Checks.

Goal:
PRs analyzed by the GitHub App show a PatchGuard check on GitHub.

Implement:

1. `github_app_check_service.py`
2. Create Check Run with status `in_progress`.
3. Update Check Run with:
   - conclusion
   - title
   - summary
   - text
   - report link/path if available
4. Map PatchGuard decisions:
   - pass/low/medium -> success
   - partial/manual review -> neutral
   - block/do_not_merge -> failure
5. Mock GitHub HTTP calls in tests.

Acceptance criteria:

- Check payloads are deterministic.
- No huge logs are posted.
- Tests cover success, neutral, failure.

Run:

```bash
python -m pytest backend/tests/test_github_app_check_service.py -q
python -m pytest -q
```

## Prompt 8: Backfill Recent PR Audit

Add initial repository history audit.

Goal:
After installation, PatchGuard can enqueue recent PRs for selected repositories.

Implement:

1. Function to list recent PRs for a repository.
2. Configurable limit, default 10 PRs per repo.
3. Create backfill jobs.
4. Avoid duplicate jobs for same repo/pr/head SHA.
5. Add CLI/dev command:
   - `patchguard app-backfill --installation-id <id> --limit 10`

Acceptance criteria:

- Backfill is bounded.
- Duplicate jobs are avoided.
- Tests mock GitHub API responses.

Run:

```bash
python -m pytest backend/tests/test_github_app_backfill.py -q
python -m pytest -q
```

## Prompt 9: App Dashboard API

Expose GitHub App audit history through FastAPI.

Goal:
Frontend can show installed repos, jobs, and reports.

Implement endpoints:

```text
GET /api/app/installations
GET /api/app/repositories
GET /api/app/repositories/{owner}/{repo}/jobs
GET /api/app/jobs/{job_id}
GET /api/app/jobs/{job_id}/report
```

Acceptance criteria:

- Endpoints read SQLite.
- Missing records return 404.
- Tests cover happy and error paths.

Run:

```bash
python -m pytest backend/tests/test_github_app_dashboard_api.py -q
python -m pytest -q
```

## Prompt 10: Dashboard UI For GitHub App Mode

Add cross-repository audit views to the React dashboard.

Goal:
Users can inspect monitored repositories and recent PatchGuard jobs.

Implement:

1. Repositories list.
2. Repo detail page.
3. Recent jobs table.
4. Risk trend summary.
5. Link from job to existing report view.
6. Graceful empty states.

Acceptance criteria:

- Existing static demo still works.
- Live app mode reads API endpoints.
- Frontend build passes.

Run:

```bash
cd frontend
npm run build
```

## Prompt 11: Local Development Setup Docs

Document how to create and test the GitHub App locally.

Goal:
A developer can create a private GitHub App and point it at a local tunnel.

Document:

1. Create GitHub App in Developer Settings.
2. Set webhook URL using ngrok/cloudflared.
3. Generate private key.
4. Set environment variables.
5. Install app on selected test repository.
6. Run FastAPI server.
7. Trigger PR event.
8. Run worker.
9. Confirm Check Run appears.

Acceptance criteria:

- README links to GitHub App docs.
- No secrets are committed.
- Setup commands are copy-pasteable.

Run:

```bash
python -m pytest -q
```

## Prompt 12: Production Readiness Review

Review the GitHub App implementation for production risks.

Goal:
Make limitations explicit before public launch.

Check:

- Webhook verification.
- Token handling.
- Private key handling.
- Worker isolation.
- Docker hardening.
- Rate limits.
- Job retry behavior.
- Queue durability.
- Data retention.
- Multi-user access control.

Acceptance criteria:

- Create `docs/github-app/production-readiness.md`.
- Add a clear launch checklist.
- Separate MVP-safe from production-required items.

