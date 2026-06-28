# GitHub App Implementation Guide

This guide is the build spec for PatchGuard as an installable GitHub App.

## Product Positioning

PatchGuard as a GitHub App should answer:

> Across the repositories I installed this on, which PRs and commits have evidence-backed merge risk?

Current PatchGuard answers one question:

> Is this PR risky?

The GitHub App version adds:

- Install once.
- Monitor selected repositories.
- Keep report history.
- Run automatically on PR activity.
- Publish GitHub-native checks.
- Provide a personal or organization audit dashboard.

## GitHub Action vs GitHub App

| Capability | GitHub Action | GitHub App |
| --- | --- | --- |
| Setup | Add workflow file per repo | Install once on selected repos |
| Triggering | Workflow events | Webhooks from GitHub |
| History | Mostly workflow artifacts | App-owned database |
| Dashboard | External/manual | Cross-repo app dashboard |
| Checks API | Possible from workflow token | Native app identity |
| Best use | CI integration | Continuous repository audit |

PatchGuard should keep the GitHub Action, but the GitHub App should become the more polished product experience.

## Target Architecture

```text
GitHub App installation
  -> GitHub webhook receiver
  -> signature verification
  -> SQLite records installation/repository/event
  -> analysis job queue
  -> worker checks out PR head
  -> existing PatchGuard pipeline runs in Docker
  -> report stored in database/filesystem
  -> GitHub Check Run updated
  -> dashboard shows repo and PR risk history
```

## Backend Modules To Add

Recommended package layout:

```text
backend/patchguard/
  github_app.py
  app_models.py
  services/
    github_app_auth_service.py
    github_app_webhook_service.py
    github_app_installation_service.py
    github_app_job_service.py
    github_app_check_service.py
    github_app_audit_service.py
  storage/
    sqlite_store.py
```

Keep the existing `SkeletonReportService` / `patchguard analyze` path working. The GitHub App should call into the current report pipeline rather than duplicate it.

## Data Model

Minimum tables:

```text
installations
  id
  github_installation_id
  account_login
  account_type
  created_at
  updated_at

repositories
  id
  installation_id
  github_repo_id
  full_name
  private
  default_branch
  selected
  created_at
  updated_at

analysis_jobs
  id
  installation_id
  repository_id
  event_type
  status
  pr_number
  head_sha
  base_sha
  report_path
  error
  created_at
  updated_at

analysis_reports
  id
  job_id
  risk_score
  risk_level
  merge_decision
  policy_decision
  report_json_path
  created_at
```

Keep report JSON on disk first. Store searchable summary columns in SQLite.

## GitHub App Settings

Initial app settings:

```text
Homepage URL: GitHub repo or deployed dashboard
Webhook URL: https://<backend-domain>/github/webhook
Webhook secret: required
Install on: selected repositories or all repositories
```

Minimum permissions:

```text
Metadata: read
Contents: read
Pull requests: read
Checks: read/write
Issues: write only if PR comments are enabled
Actions: read optional later
```

Webhook events:

```text
installation
installation_repositories
pull_request
push later
check_suite/check_run optional later
```

## Webhook Handling

Endpoint:

```text
POST /github/webhook
```

Required behavior:

1. Read raw request body.
2. Verify `X-Hub-Signature-256` with the app webhook secret.
3. Read `X-GitHub-Event`.
4. Deduplicate delivery IDs from `X-GitHub-Delivery`.
5. Persist the event or enqueue a job.
6. Return quickly.

Never run a full PatchGuard analysis inside the webhook request path.

## Authentication Model

GitHub Apps authenticate differently from personal access tokens.

Flow:

```text
private key -> app JWT -> installation access token -> GitHub REST API calls
```

Use the installation token to:

- List installation repositories.
- Fetch PR metadata.
- Create Check Runs.
- Post comments if enabled.

Do not store installation tokens long term. They are short-lived and should be generated when needed.

## Worker Flow

For a `pull_request` event:

```text
pull_request webhook
  -> validate event action
  -> create analysis_job
  -> worker starts job
  -> create GitHub Check Run: in_progress
  -> run PatchGuard analysis
  -> store report
  -> update Check Run: completed/success/failure/neutral
```

Recommended check conclusions:

```text
success: low/medium risk, policy pass/warn
neutral: partial evidence or manual review needed
failure: policy block or do_not_merge
```

## Backfill Audit

Do not audit every commit in the first GitHub App MVP.

Initial install should backfill:

```text
last 10-20 PRs per installed repository
default branch latest commit metadata
repository test/config/security-sensitive file summary
```

This creates useful dashboard content without making install painfully slow.

## Dashboard Changes

New dashboard views:

```text
/app/installations
/app/repos
/app/repos/:owner/:repo
/app/jobs/:job_id
```

Top-level metrics:

```text
repositories monitored
PRs analyzed
high-risk PRs
policy blocks
security findings
source-without-tests count
partial evidence count
```

## Security Requirements

Must-have:

- Verify webhook signatures.
- Use minimum GitHub App permissions.
- Never expose private keys in logs.
- Do not store installation tokens long term.
- Do not run analysis in the webhook request.
- Keep Docker execution isolated from the API process if deploying publicly.
- Keep `OPENAI_API_KEY` optional and explicit.

Production hardening later:

- Queue worker process separate from API.
- Non-root containers.
- Read-only mounts where possible.
- Stronger sandbox runtime.
- Rate limits per installation.
- Job cancellation.
- Retention policy for cloned repos and reports.

## Definition Of Done For GitHub App MVP

- Local webhook endpoint verifies a valid signature and rejects invalid signatures.
- Installation events are stored.
- Pull request events enqueue jobs.
- Worker runs the current PatchGuard pipeline for a PR.
- Report is persisted.
- GitHub Check Run is created and updated.
- Tests cover signature verification, event routing, job creation, and check-run payload generation.
- README explains how to create and install the GitHub App locally.

