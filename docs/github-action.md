# PatchGuard GitHub Action

Run PatchGuard automatically on pull requests, publish GitHub-native annotations, add a job summary, and upload a Markdown report artifact.

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

This runs with no OpenAI cost. PatchGuard still fetches the PR diff, clones the PR code, runs Docker-based pytest/Ruff/Bandit evidence, computes risk, adds a GitHub Actions job summary, emits annotations, and uploads `patchguard-report.md`.

## Enforce The Policy Gate

By default PatchGuard reports evidence without failing the workflow. To make it a merge gate, set `fail-on-do-not-merge: "true"`:

```yaml
      - uses: KaiwenMo1/patchguard@v1
        with:
          skip-llm: "true"
          fail-on-do-not-merge: "true"
```

PatchGuard exits with code `2` only when the deterministic recommendation is `do_not_merge`. The report artifact is still uploaded.

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

## On-Demand Slash Command

You can also run PatchGuard only when someone comments `/patchguard` on a PR:

```yaml
name: PatchGuard Command

on:
  issue_comment:
    types: [created]

permissions:
  contents: read
  pull-requests: read
  issues: write

jobs:
  patchguard:
    if: ${{ github.event.issue.pull_request && startsWith(github.event.comment.body, '/patchguard') }}
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v5

      - uses: KaiwenMo1/patchguard@v1
        with:
          pr-url: ${{ github.event.issue.html_url }}
          skip-llm: "true"
          comment: "true"
```

This is the lightweight "agent" mode: install the workflow once, then trigger PatchGuard from the PR conversation.

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
| `annotations` | `true` | Emit GitHub Actions annotations for policy, test, and security evidence. |
| `step-summary` | `true` | Add a concise PatchGuard summary to the workflow job summary. |
| `fail-on-do-not-merge` | `false` | Fail the workflow when PatchGuard recommends `do_not_merge`. |
| `timeout` | `180` | Sandbox command timeout in seconds. |
| `docker-image` | `patchguard-python-sandbox:latest` | Docker image tag. |
| `keep-workspace` | `true` | Keep cloned workspaces after the run. |
| `upload-artifact` | `true` | Upload the report as an artifact. |
| `artifact-name` | `patchguard-report` | Artifact name. |

## Notes

- The action requires Docker, which is available on `ubuntu-latest` GitHub-hosted runners.
- The target PR repository should be Python for the MVP.
- Private repositories need normal GitHub token permissions for the workflow context.
- Add `patchguard.yml` or `.patchguard.yml` to the target repository to tune thresholds and blocking rules.
- Dependency install, test, scan, and LLM failures become report evidence instead of crashing the whole workflow when possible.

## Release The Action

After committing changes in the PatchGuard repository, publish a version tag:

```bash
git tag v1
git push origin v1
```

When you make future breaking changes, create `v2`. For non-breaking updates, move a version tag deliberately:

```bash
git tag -f v1
git push origin v1 --force
```

Test the action from a separate repository before publishing it to the GitHub Marketplace.
