# PatchGuard GitHub Action

Run PatchGuard automatically on pull requests and upload a Markdown report artifact.

## Basic Usage

Create `.github/workflows/patchguard.yml` in the repository you want to analyze:

```yaml
name: PatchGuard

on:
  pull_request:
    types: [opened, synchronize, reopened, ready_for_review]

permissions:
  contents: read
  pull-requests: read

jobs:
  patchguard:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v5

      - uses: KaiwenMo1/patchguard@v1
        with:
          skip-llm: "true"
```

This runs with no OpenAI cost. PatchGuard still fetches the PR diff, clones the PR code, runs Docker-based pytest/Ruff/Bandit evidence, computes risk, and uploads `patchguard-report.md`.

## Comment On The PR

To post or update one concise PatchGuard comment, grant write permission and set `comment: "true"`:

```yaml
permissions:
  contents: read
  pull-requests: read
  issues: write

jobs:
  patchguard:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v5

      - uses: KaiwenMo1/patchguard@v1
        with:
          skip-llm: "true"
          comment: "true"
```

The comment includes a marker, so repeated runs update the existing PatchGuard comment instead of posting duplicates.

## Enable LLM Evidence

Add an `OPENAI_API_KEY` repository secret and disable `skip-llm`:

```yaml
      - uses: KaiwenMo1/patchguard@v1
        env:
          OPENAI_API_KEY: ${{ secrets.OPENAI_API_KEY }}
        with:
          skip-llm: "false"
```

This enables behavioral contract extraction, generated pytest tests, and evidence-based AI review. Risk scoring remains deterministic.

## Inputs

| Input | Default | Description |
| --- | --- | --- |
| `pr-url` | current PR URL | GitHub pull request URL. Usually leave blank. |
| `output` | `patchguard-report.md` | Report path. |
| `format` | `markdown` | `json` or `markdown`. |
| `skip-llm` | `true` | Disable OpenAI-powered steps. |
| `comment` | `false` | Post/update one PR summary comment. |
| `timeout` | `180` | Sandbox command timeout in seconds. |
| `docker-image` | `patchguard-python-sandbox:latest` | Docker image tag. |
| `keep-workspace` | `true` | Keep cloned workspaces after the run. |
| `upload-artifact` | `true` | Upload the report as an artifact. |
| `artifact-name` | `patchguard-report` | Artifact name. |

## Notes

- The action requires Docker, which is available on `ubuntu-latest` GitHub-hosted runners.
- The target PR repository should be Python for the MVP.
- Private repositories need normal GitHub token permissions for the workflow context.
- Dependency install, test, scan, and LLM failures become report evidence instead of crashing the whole workflow when possible.
