# Hosted GitHub App Deployment

PatchGuard can run as an installable GitHub App with a hosted FastAPI webhook endpoint, a local SQLite store, a background worker, and GitHub Check Runs.

There are two realistic hosted modes:

| Mode | Best for | Docker evidence |
| --- | --- | --- |
| Render web service | Public demo, webhook delivery, Check Runs, dashboard history | Disabled by default; reports are partial |
| Docker-capable VPS | Full PatchGuard evidence with pytest/Ruff/Bandit sandbox execution | Enabled |

Render is the fastest way to prove the GitHub App flow. A VPS is the better path when you want the core security property: untrusted repository commands run inside Docker.

## 1. Create The GitHub App

In GitHub:

```text
Settings -> Developer settings -> GitHub Apps -> New GitHub App
```

Use these values:

| Setting | Value |
| --- | --- |
| Homepage URL | Your hosted PatchGuard URL |
| Webhook URL | `https://YOUR_HOST/github/webhook` |
| Webhook secret | Generate a long random string |
| Repository permissions | Contents: read, Pull requests: read, Metadata: read, Checks: read/write |
| Events | Pull request, Installation, Installation repositories |

Generate a private key and copy the PEM contents for your host secret store.

## 2. Deploy The Easy Hosted Mode On Render

This repository includes `render.yaml`. In Render:

1. Create a new Blueprint from your PatchGuard GitHub repository.
2. Let Render detect `render.yaml`.
3. Set these secret environment variables:

```bash
PATCHGUARD_PUBLIC_BASE_URL=https://YOUR-RENDER-SERVICE.onrender.com
PATCHGUARD_GITHUB_APP_ID=YOUR_APP_ID
PATCHGUARD_GITHUB_APP_PRIVATE_KEY="-----BEGIN RSA PRIVATE KEY-----
...
-----END RSA PRIVATE KEY-----"
PATCHGUARD_GITHUB_WEBHOOK_SECRET=YOUR_WEBHOOK_SECRET
```

The blueprint also sets:

```bash
PATCHGUARD_SKIP_DOCKER=true
PATCHGUARD_USE_MEMORY=true
PATCHGUARD_ENABLE_LLM=false
PATCHGUARD_PUBLISH_CHECKS=true
```

This means GitHub App webhooks, job queueing, report history, PatchGuard memory, and GitHub Checks work, but Docker-only evidence is marked skipped/partial.

Set the GitHub App webhook URL to:

```text
https://YOUR-RENDER-SERVICE.onrender.com/github/webhook
```

Install the GitHub App on selected repositories, then open or update a PR. PatchGuard should publish a Check Run. The Check Run `Details` link points to:

```text
https://YOUR-RENDER-SERVICE.onrender.com/api/app/jobs/{job_id}/report
```

## 3. Full Evidence Hosted Mode On A Docker-Capable Host

Use a small VPS or server where Docker is available.

```bash
git clone https://github.com/YOUR_ACCOUNT/patchguard.git
cd patchguard

python3 -m venv .venv
. .venv/bin/activate
python -m pip install -e ".[dev]"
make sandbox

export PATCHGUARD_PUBLIC_BASE_URL="https://patchguard.yourdomain.com"
export PATCHGUARD_APP_DB_PATH=".patchguard/github_app/patchguard-app.db"
export PATCHGUARD_MEMORY_DB=".patchguard/memory/patchguard-memory.db"
export PATCHGUARD_GITHUB_APP_ID="YOUR_APP_ID"
export PATCHGUARD_GITHUB_APP_PRIVATE_KEY_PATH="$PWD/.patchguard/github_app/private-key.pem"
export PATCHGUARD_GITHUB_WEBHOOK_SECRET="YOUR_WEBHOOK_SECRET"
export PATCHGUARD_SKIP_DOCKER=false
export PATCHGUARD_USE_MEMORY=true
export PATCHGUARD_COMPARE_BASE=true
export PATCHGUARD_ENABLE_LLM=false

bash scripts/start_github_app_hosted.sh
```

Put a reverse proxy such as Caddy, Nginx, or Cloudflare Tunnel in front of port `8000`, then set the GitHub App webhook URL to:

```text
https://patchguard.yourdomain.com/github/webhook
```

## 4. Backfill Existing PR History

After installing the app, enqueue recent PRs:

```bash
patchguard app-backfill --installation-id YOUR_INSTALLATION_ID --limit 10
```

The worker will process queued jobs:

```bash
patchguard app-worker --publish-checks --poll --interval 10 --use-memory
```

For full regression comparison on a Docker-capable host:

```bash
patchguard app-worker --publish-checks --poll --interval 10 --use-memory --compare-base
```

## 5. Safety Notes

- Do not run Docker analysis on a public multi-tenant host without hardening the execution environment.
- Do not commit GitHub App private keys, webhook secrets, or OpenAI keys.
- Render mode is useful for product demos, repository audit history, and Check Run plumbing, but its reports are partial when Docker is skipped.
- Full PatchGuard evidence requires Docker because repository tests and scanners execute untrusted code.
