# Local GitHub App Development

This guide shows how to create a private GitHub App, point GitHub webhooks at your local PatchGuard API, enqueue a pull request job, process it locally, and publish a GitHub Check Run.

Use this for development and demos only. Do not expose this local server as a public multi-tenant service.

## Official References

- [Registering a GitHub App](https://docs.github.com/en/apps/creating-github-apps/registering-a-github-app/registering-a-github-app)
- [Managing private keys for GitHub Apps](https://docs.github.com/en/apps/creating-github-apps/authenticating-with-a-github-app/managing-private-keys-for-github-apps)
- [Validating webhook deliveries](https://docs.github.com/en/webhooks/using-webhooks/validating-webhook-deliveries)
- [Installing your own GitHub App](https://docs.github.com/en/apps/using-github-apps/installing-your-own-github-app)
- [GitHub Check Runs API](https://docs.github.com/en/rest/checks/runs?apiVersion=2022-11-28)
- [ngrok HTTP tunnels](https://ngrok.com/docs/http/)
- [Cloudflare quick tunnels](https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/do-more-with-tunnels/trycloudflare/)

## 1. Install PatchGuard Locally

From the repository root:

```bash
./scripts/bootstrap.sh --no-docker
. .venv/bin/activate
```

Build the Docker sandbox if you want real test and scan evidence:

```bash
./scripts/bootstrap.sh --with-docker
```

OpenAI is optional. To avoid spending credits during app testing, do not set `OPENAI_API_KEY`. The worker skips LLM features by default; pass `--enable-llm` only when you intentionally want generated tests and AI review.

## 2. Start A Local Tunnel

PatchGuard listens locally on port `8000`. GitHub needs a public HTTPS webhook URL that forwards to that port.

With ngrok:

```bash
ngrok http 8000
```

With cloudflared:

```bash
cloudflared tunnel --url http://127.0.0.1:8000
```

Copy the generated HTTPS forwarding URL. Your webhook URL will be:

```text
https://YOUR-TUNNEL-HOST/github/webhook
```

## 3. Create A Private GitHub App

Go to GitHub:

```text
Settings -> Developer settings -> GitHub Apps -> New GitHub App
```

Use these local-development values:

| Field | Value |
| --- | --- |
| GitHub App name | `PatchGuard Local YOUR_NAME` |
| Homepage URL | `http://127.0.0.1:5173` or your repository URL |
| Webhook URL | `https://YOUR-TUNNEL-HOST/github/webhook` |
| Webhook secret | Use the command below |
| Expire user authorization tokens | Leave default |
| Request user authorization during installation | Off |

Generate a webhook secret:

```bash
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

Save that value somewhere local. You will use it as `PATCHGUARD_GITHUB_WEBHOOK_SECRET`.

## 4. Configure GitHub App Permissions And Events

Use minimum practical permissions for the MVP:

Repository permissions:

| Permission | Access | Why |
| --- | --- | --- |
| Contents | Read-only | Clone and inspect repository contents |
| Pull requests | Read-only | Read PR metadata and diffs |
| Checks | Read and write | Create and update PatchGuard Check Runs |
| Metadata | Read-only | Required by GitHub |

Subscribe to events:

- `Pull request`
- `Installation repositories`

The app also receives installation lifecycle payloads used by PatchGuard when available.

Save the app.

## 5. Generate And Store The Private Key

In the GitHub App settings page, generate a private key and download the `.pem` file.

Store it under `.patchguard/`, which is already ignored by git:

```bash
mkdir -p .patchguard/github_app
mv ~/Downloads/*.private-key.pem .patchguard/github_app/private-key.pem
chmod 600 .patchguard/github_app/private-key.pem
```

Do not commit the private key.

## 6. Export Environment Variables

Find the App ID on the GitHub App settings page, then export:

```bash
export PATCHGUARD_GITHUB_APP_ID="YOUR_APP_ID"
export PATCHGUARD_GITHUB_APP_PRIVATE_KEY_PATH="$PWD/.patchguard/github_app/private-key.pem"
export PATCHGUARD_GITHUB_WEBHOOK_SECRET="YOUR_WEBHOOK_SECRET"
```

Optional: allow draft PRs to enqueue jobs.

```bash
export PATCHGUARD_ANALYZE_DRAFT_PRS=true
```

Do not commit `.env` files. This repository ignores `.env` and `.patchguard/`, but still check `git status` before committing.

## 7. Install The App On A Test Repository

From the GitHub App settings page:

```text
Install App -> Only select repositories -> choose one test repository
```

Pick a repository you control. For the safest first test, use a small public or private Python repository where running tests in Docker is acceptable.

## 8. Start FastAPI

Keep the tunnel running in one terminal. In a second terminal:

```bash
. .venv/bin/activate
python -m uvicorn patchguard.api_app:app --reload --host 127.0.0.1 --port 8000
```

Health-check the local dashboard API:

```bash
curl http://127.0.0.1:8000/api/app/installations
curl http://127.0.0.1:8000/api/app/repositories
```

If the app was installed while the server was offline, reinstall the app or use the backfill command after the installation has been recorded.

## 9. Trigger A Pull Request Event

In the selected test repository, open a pull request or push a new commit to an existing pull request.

PatchGuard handles these `pull_request` actions:

- `opened`
- `synchronize`
- `reopened`
- `ready_for_review`

Confirm GitHub delivered the webhook:

```text
GitHub App settings -> Advanced -> Recent Deliveries
```

The delivery should return HTTP `202`.

Confirm PatchGuard queued a job:

```bash
curl http://127.0.0.1:8000/api/app/repositories/OWNER/REPO/jobs
```

Replace `OWNER` and `REPO` with the selected repository name.

## 10. Run One Worker Job

Run one queued job and publish a Check Run:

```bash
patchguard app-worker --publish-checks
```

If Docker is not ready yet but you want to test the webhook, queue, report storage, and Check Run flow:

```bash
patchguard app-worker --publish-checks --skip-docker
```

The Docker-skipped run should be treated as partial evidence, not a real verification result.

To keep processing jobs while FastAPI is running:

```bash
patchguard app-worker --publish-checks --poll --interval 10
```

To include local memory retrieval and base-vs-head regression comparison:

```bash
patchguard app-worker --publish-checks --poll --interval 10 --use-memory --compare-base
```

If your FastAPI server is reachable through a public tunnel and you want the Check Run `Details`
link to open the hosted report JSON, set:

```bash
export PATCHGUARD_PUBLIC_BASE_URL="https://YOUR-TUNNEL-HOST"
```

## 11. Confirm The Check Run

Open the pull request on GitHub.

You should see a `PatchGuard` check. It starts as `in_progress`, then updates to one of:

- `success` for pass or low/medium risk.
- `neutral` for partial/manual-review results.
- `failure` for do-not-merge or blocked policy results.

The Check Run summary should include risk score, policy decision, test and scan summaries, top risk reasons, and the local report artifact path.

The full local JSON report is stored under:

```text
.patchguard/app_reports/
```

The SQLite app database is stored under:

```text
.patchguard/github_app/patchguard-app.db
```

Both paths are ignored by git.

## 12. View The App Dashboard

Start the React dashboard in live mode:

```bash
cd frontend
npm install
VITE_PATCHGUARD_API_URL=http://127.0.0.1:8000 npm run dev
```

Open:

```text
http://127.0.0.1:5173
```

Use `App audit` to inspect installed repositories, recent jobs, risk trend, and stored reports.

## Troubleshooting

Webhook returns `401`:

- Confirm the webhook secret in GitHub exactly matches `PATCHGUARD_GITHUB_WEBHOOK_SECRET`.
- Confirm the tunnel forwards to `http://127.0.0.1:8000`.

Webhook returns `500`:

- Confirm the FastAPI process has the three `PATCHGUARD_GITHUB_*` environment variables.
- Check the FastAPI terminal for the exact exception.

No repositories appear:

- Reinstall the app while FastAPI is running.
- Confirm the app was installed on selected repositories.
- Check `GitHub App settings -> Advanced -> Recent Deliveries`.

No job appears:

- Confirm the PR is not a draft, or set `PATCHGUARD_ANALYZE_DRAFT_PRS=true`.
- Push a new commit to the PR to trigger `synchronize`.

Check Run does not appear:

- Confirm the app has `Checks: Read and write`.
- Confirm the worker was run with `check_service_factory=GitHubAppCheckService`.
- Confirm the job has a `head_sha`.

GitHub rejects authentication:

- Confirm `PATCHGUARD_GITHUB_APP_ID` is the App ID, not the client ID.
- Confirm the private key path points to the downloaded `.pem` file.
- Generate a new private key if the old one was deleted in GitHub.
