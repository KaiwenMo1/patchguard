# PatchGuard — CI for AI-generated code

Most AI PR review bots generate comments. PatchGuard generates evidence.

PatchGuard analyzes a public GitHub pull request, checks out the PR code, runs targeted verification in a Docker sandbox, scans for static/security issues, applies a configurable merge policy, and produces an explainable merge-risk report. The MVP focuses on Python repositories.

[![CI](https://github.com/KaiwenMo1/patchguard/actions/workflows/ci.yml/badge.svg)](https://github.com/KaiwenMo1/patchguard/actions/workflows/ci.yml)
[![Static demo](https://github.com/KaiwenMo1/patchguard/actions/workflows/pages.yml/badge.svg)](https://kaiwenmo1.github.io/patchguard/)

![PatchGuard dashboard preview](docs/screenshots/patchguard-dashboard.svg)

## Start Here

Choose the path that matches what you want to evaluate:

| Goal | Start here | Requirements |
| --- | --- | --- |
| See the product | [Open the zero-install dashboard](https://kaiwenmo1.github.io/patchguard/) | Browser only |
| Verify the CLI quickly | Run the controlled demo below | Python 3.11+ and Docker |
| Analyze a real PR | Use `patchguard analyze <PR_URL>` | Python 3.11+, Docker, optional GitHub token |
| Add PatchGuard to a repo | Use `KaiwenMo1/patchguard@v1` | GitHub Actions |
| Understand the design | Read [Architecture](docs/architecture.md) | None |

The static dashboard displays reports produced by the real CLI. It does not execute Docker, FastAPI, GitHub requests, or OpenAI calls in the browser.

## Run A Verified Demo

Requirements:

- Python 3.11 or newer and Git.
- Docker Desktop or Docker Engine for real test and scan evidence.
- OpenAI API key only when intentionally enabling generated tests and AI review.

```bash
git clone https://github.com/KaiwenMo1/patchguard.git
cd patchguard

python -m venv .venv
# Linux, macOS, or WSL:
. .venv/bin/activate
# Windows PowerShell instead: .venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"

docker build -t patchguard-python-sandbox:latest -f sandbox/python/Dockerfile sandbox/python

patchguard analyze-demo examples/demo_security_bug \
  --out report.json \
  --skip-llm
```

Expected evidence: existing pytest passes, Ruff passes, Bandit identifies the intentionally unsafe `eval`, and PatchGuard writes `report.json`. No OpenAI credits are used.

Without Docker, verify installation and analyze metadata/diffs only:

```bash
patchguard analyze https://github.com/psf/requests/pull/7431 \
  --out report.json \
  --skip-docker \
  --skip-llm
```

Verify the project itself:

```bash
python -m pytest -q
python -m ruff check .
cd frontend && npm ci && npm run build
```

## Why PatchGuard?

AI-generated code often looks plausible while quietly changing behavior, weakening validation, or missing tests. A review comment is useful, but it is not evidence.

PatchGuard is built around a stricter loop:

1. Fetch the real pull request metadata and diff.
2. Classify changed files and affected Python functions.
3. Run existing tests and generated tests in a Docker sandbox.
4. Run Ruff and Bandit for static/security evidence.
5. Compute a deterministic, explainable risk score.
6. Emit a JSON or Markdown report that a developer can inspect, archive, or post back to GitHub.

It does not claim a PR is correct. It gives reviewers concrete signals before merge.

## Features

- **PR diff analysis** for public GitHub pull requests.
- **Changed-function extraction** for Python files using `ast`.
- **Behavioral contract extraction** that turns the diff into intended behavior, preserved behavior, edge cases, invalid inputs, and uncertainties.
- **Generated regression tests** for changed functions when an OpenAI API key is configured, guided by the extracted contract.
- **Evidence-based AI review** that summarizes what changed, correctness notes, efficiency notes, top risks, and next actions using only collected evidence.
- **Docker sandbox execution** with timeouts and disabled container networking.
- **Existing and generated pytest results** captured as structured evidence.
- **Generated-test failure mapping** from failed pytest names to target files, functions, and behavior checked.
- **Ruff and Bandit scans** with parsed security findings.
- **Multi-dimensional risk score** with deterministic sub-scores for change size, tests, behavior, security, and uncertainty.
- **Configurable policy gate** via `patchguard.yml`.
- **FastAPI backend** for submitting and polling analyses.
- **React + TypeScript dashboard** for a recruiter-friendly demo UI.
- **Static GitHub Pages demo mode** with checked-in sample reports.
- **Reusable GitHub Action** for running PatchGuard automatically on pull requests.
- **Optional GitHub PR comment** that updates one PatchGuard summary comment instead of spamming.
- **Partial reports** when clone, dependency install, Docker, tests, or scans fail.

## What Uses OpenAI?

OpenAI is optional. PatchGuard only uses OpenAI credits for:

- Behavioral contract extraction from changed Python code.
- LLM-generated pytest tests guided by that contract.
- Evidence-based AI review summaries grounded in collected PatchGuard evidence.

No credits are used when you run:

```bash
patchguard analyze <PR_URL> --out report.json --skip-llm
```

Without OpenAI, PatchGuard still fetches PR metadata, checks out code, analyzes diffs, runs Docker tests and scans, computes risk, writes reports, serves the dashboard, and can comment on PRs.

To intentionally enable behavioral contracts and generated tests:

```bash
export OPENAI_API_KEY=sk_your_key_here
patchguard analyze <PR_URL> --out report.json
```

## Evidence-Based AI Review

When OpenAI is enabled, PatchGuard adds a review summary that answers:

- What did this PR appear to change?
- What correctness evidence passed, failed, or is missing?
- Did PatchGuard collect any performance or efficiency evidence?
- Which files should a reviewer inspect first?
- What follow-up tests or fixes are suggested by the evidence?

The AI review is constrained by the report. It must not invent failures, vulnerabilities, benchmark results, or claim that a PR is correct. It can say:

```text
Existing tests passed, but generated regression tests were skipped and no tests changed with the parser behavior.
```

It should not say:

```text
This PR is definitely correct.
```

## Quickstart: Local CLI

Clone the repository:

```bash
git clone https://github.com/KaiwenMo1/patchguard.git
cd patchguard
```

Create an environment and install the CLI:

```bash
python -m venv .venv
. .venv/bin/activate
python -m pip install -e ".[dev]"
```

Build the Python sandbox image for real evidence runs:

```bash
docker build -t patchguard-python-sandbox:latest -f sandbox/python/Dockerfile sandbox/python
```

First smoke-test GitHub fetch, diff analysis, checkout, risk scoring, and policy without Docker or OpenAI:

```bash
patchguard analyze https://github.com/psf/requests/pull/7431 \
  --out report.json \
  --skip-docker \
  --skip-llm
```

Then run PatchGuard with Docker evidence but no OpenAI cost:

```bash
patchguard analyze https://github.com/psf/requests/pull/7431 \
  --out report.json \
  --skip-llm \
  --timeout 180 \
  --keep-workspace
```

Write a Markdown report:

```bash
patchguard analyze https://github.com/psf/requests/pull/7431 \
  --out patchguard-report.md \
  --format markdown \
  --skip-llm \
  --timeout 180
```

If dependencies fail to install, tests fail, Docker is unavailable, or scans cannot complete, PatchGuard still writes a partial report. It does not fake pass/fail evidence.

## Policy Gate

PatchGuard looks for `patchguard.yml` or `.patchguard.yml` in the checked-out repository. If no file exists, safe defaults are used.

Example:

```yaml
risk_threshold: 70
allow_merge_with_caution_below: 60
block_on:
- generated_test_failure
- existing_test_failure
- high_security_finding
- secret_detected
- auth_code_without_tests
sensitive_paths:
- "auth/"
- "security/"
- "payments/"
- "api/routes/"
```

This repository includes [.patchguard.yml](.patchguard.yml) as a starting policy. Copy it into repositories where you want PatchGuard to run, then tune thresholds and blocking rules for that codebase.

The final report includes:

```json
{
  "policy_decision": {
    "decision": "warn",
    "triggered_rules": ["partial_evidence"]
  }
}
```

Policy decisions are separate from the raw risk score. A PR can have medium risk but still warn because evidence was skipped.

## Generated Test Failure Mapping

When generated tests are enabled and a generated pytest fails, PatchGuard maps the failed test back to the changed function it was meant to check:

```json
{
  "failure_mappings": [
    {
      "failed_test": "test_parse_empty_input",
      "target_file": "src/parser.py",
      "target_function": "parse_config",
      "behavior_checked": "empty input should not crash",
      "failure_summary": "AssertionError",
      "risk_message": "Generated test test_parse_empty_input failed while checking empty input should not crash in src/parser.py::parse_config.",
      "suggested_next_step": "Check whether src/parser.py::parse_config regressed the behavior under test, then either fix the code or mark the generated test as invalid with a reason."
    }
  ]
}
```

PatchGuard also writes generated-test metadata to:

```text
.patchguard/generated_tests/metadata.json
```

This is the part that makes generated-test evidence reviewable instead of mysterious: every failing generated test gets tied back to the changed function, behavior checked, failure summary, risk message, and next step.

## Behavioral Contract Extraction

When OpenAI is enabled, PatchGuard extracts a compact contract before test generation:

```json
{
  "behavioral_contract": {
    "intended_new_behaviors": ["empty parser input returns an empty result"],
    "existing_behaviors_to_preserve": ["valid key/value input still parses successfully"],
    "edge_cases_to_test": ["blank lines and surrounding whitespace"],
    "invalid_inputs_to_test": ["malformed lines without a separator"],
    "contract_uncertainties": ["diff does not show the full caller contract"],
    "confidence": 0.72
  }
}
```

Generated tests use this contract as targeting guidance, and failed generated tests are mapped back to the behavior they were meant to check. With `--skip-llm`, this step is explicitly marked skipped and no OpenAI credits are used.

## GitHub Tokens

Public PRs work without a GitHub token until you hit lower unauthenticated rate limits.

For higher limits:

```bash
export GITHUB_TOKEN=ghp_your_token_here
```

To post or update a concise PatchGuard comment on the PR:

```bash
patchguard analyze https://github.com/owner/repo/pull/123 \
  --out report.json \
  --skip-llm \
  --comment
```

The comment includes `<!-- patchguard-report -->`, so repeated runs update the previous PatchGuard comment instead of posting duplicates. Raw logs are not posted.

## Example Report

CLI summary:

```text
PatchGuard report: report.json
Status: partial
PR: https://github.com/psf/requests/pull/7431
Title: Fix mutability issues with headers input types
Existing tests: skipped (Docker execution disabled by --skip-docker)
Static scans: ruff check=skipped, bandit security scan=skipped
Behavioral contract: skipped (Behavioral contract extraction disabled by --skip-llm)
Test generation: skipped (LLM test generation disabled by --skip-llm)
Changed files: 3 (+6/-6)
Changed functions: 4
Risk: 44/100 (medium)
Risk breakdown: change=0, tests=100, behavior=30, security=0, uncertainty=65
Policy: warn (rules: partial_evidence)
Decision: merge_with_caution
Recommendation: Likely safe to merge after normal review.
Top risk reasons:
  - [existing_tests] +35: Existing tests did not produce pass/fail evidence
  - [test_coverage] +80: Source files changed without test files changing
```

Report snippet:

```json
{
  "status": "partial",
  "risk_score": 44,
  "risk_level": "medium",
  "risk_breakdown": {
    "change_size_risk": 0,
    "test_coverage_risk": 100,
    "behavioral_risk": 30,
    "security_risk": 0,
    "uncertainty_risk": 65
  },
  "policy_decision": {
    "decision": "warn",
    "triggered_rules": ["partial_evidence"]
  },
  "merge_decision": "merge_with_caution",
  "recommendation": "Likely safe to merge after normal review.",
  "risk_reasons": [
    {
      "category": "test_coverage",
      "score_impact": 80,
      "reason": "Source files changed without test files changing"
    }
  ]
}
```

## Dashboard

Static dashboard demo, no backend required:

```bash
cd frontend
npm install
VITE_PATCHGUARD_STATIC_DEMO=true npm run dev
```

Open:

```text
http://127.0.0.1:5173
```

Live analyzer dashboard:

Start the API:

```bash
. .venv/bin/activate
env -u OPENAI_API_KEY uvicorn patchguard.api_app:app --reload --host 127.0.0.1 --port 8000
```

Start the frontend:

```bash
cd frontend
npm install
npm run dev
```

Open:

```text
http://127.0.0.1:5173
```

If the backend is on a different port:

```bash
VITE_PATCHGUARD_API_URL=http://127.0.0.1:8011 npm run dev
```

## Deploying The Frontend

You can deploy the React dashboard as a static site on GitHub Pages. The included workflow at `.github/workflows/pages.yml` builds the dashboard in static demo mode and serves the checked-in reports from `frontend/public/sample_reports/`.

GitHub Pages cannot run the analyzer itself. It cannot run FastAPI, Docker, git clone, pytest, Ruff, or Bandit.

Good deployment options:

- **Static portfolio demo:** GitHub Pages hosts the frontend and sample reports.
- **Live analyzer:** frontend on GitHub Pages, backend on Render/Fly/Railway/VPS.
- **Local-only tool:** CLI and dashboard run on your machine.

For a live hosted frontend, point it at your backend:

```bash
VITE_PATCHGUARD_API_URL=https://your-backend.example.com npm run build
```

For GitHub Pages under a repository path, set Vite `base` to the repo name, for example `/patchguard/`.

The Pages workflow already sets:

```bash
VITE_PATCHGUARD_STATIC_DEMO=true
VITE_BASE_PATH=/${{ github.event.repository.name }}/
```

After pushing to GitHub, enable Pages with **Settings → Pages → Source: GitHub Actions**.

## GitHub Action

PatchGuard can run as a reusable GitHub Action in other repositories:

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

This runs with no OpenAI cost. It uploads a Markdown report artifact, writes a GitHub Actions job summary, and emits annotations for policy, test, and security evidence.

To make PatchGuard a real merge gate, fail the workflow when the deterministic recommendation is `do_not_merge`:

```yaml
      - uses: KaiwenMo1/patchguard@v1
        with:
          skip-llm: "true"
          fail-on-do-not-merge: "true"
```

To comment on the PR, add `issues: write` and `comment: "true"`:

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

For on-demand "agent" mode, run PatchGuard when someone comments `/patchguard` on a PR:

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

Full action docs live at `docs/github-action.md`.

An example workflow for this repository lives at `.github/workflows/patchguard.yml`.

It installs PatchGuard, builds the Docker sandbox, runs `patchguard analyze` on pull requests, and uploads a Markdown report artifact. The workflow uses `--skip-llm` by default, so it does not spend OpenAI credits unless you intentionally change it and add an `OPENAI_API_KEY` secret.

## Local Demo Repositories

Controlled examples live under `examples/`:

- `examples/demo_parser_bug`
- `examples/demo_security_bug`
- `examples/demo_no_tests_changed`

Run a no-cost demo:

```bash
env -u OPENAI_API_KEY patchguard analyze-demo examples/demo_security_bug \
  --out examples/sample_reports/demo_security_bug.json \
  --skip-llm
```

Refresh every sample report and copy it into the static dashboard:

```bash
env -u OPENAI_API_KEY patchguard analyze-demo examples/demo_parser_bug \
  --out examples/sample_reports/demo_parser_bug.json \
  --skip-llm \
  --cleanup-workspace

env -u OPENAI_API_KEY patchguard analyze-demo examples/demo_security_bug \
  --out examples/sample_reports/demo_security_bug.json \
  --skip-llm \
  --cleanup-workspace

env -u OPENAI_API_KEY patchguard analyze-demo examples/demo_no_tests_changed \
  --out examples/sample_reports/demo_no_tests_changed.json \
  --skip-llm \
  --cleanup-workspace

mkdir -p frontend/public/sample_reports
cp examples/sample_reports/*.json frontend/public/sample_reports/
```

For a real GIF, open the static demo, switch between the three sample reports, and record the dashboard. Save it as `docs/screenshots/patchguard-demo.gif`, then replace the SVG image near the top of this README.

## Architecture

See [docs/architecture.md](docs/architecture.md) for module responsibilities, trust boundaries, limitations, and the engineering decisions behind the project.

```mermaid
flowchart TD
    A[GitHub PR URL] --> B[GitHub Service]
    B --> C[Changed Files + PR Metadata]
    C --> D[Clone Service]
    D --> E[Function Extractor]
    E --> R[Behavioral Contract Extraction]
    R --> F[Test Generation Service]
    D --> G[Docker Sandbox]
    G --> H[Existing Pytest Results]
    G --> I[Generated Test Results]
    G --> J[Ruff + Bandit Scans]
    I --> P[Test-to-Risk Mapping]
    C --> K[Risk Score Service]
    H --> K
    I --> K
    J --> K
    R --> K
    F --> K
    K --> Q[Policy Gate]
    Q --> S[Evidence-Based AI Review]
    P --> L[JSON / Markdown Report]
    Q --> L
    S --> L
    L --> M[FastAPI]
    M --> N[React Dashboard]
    L --> O[Optional PR Comment]
```

## Current Scope

PatchGuard is an MVP, not a hosted product.

Supported today:

- Public GitHub pull requests.
- Python repositories.
- Local CLI execution.
- Docker-based test/static/security evidence.
- Local FastAPI + React dashboard.
- Optional GitHub PR comments.
- Reusable GitHub Action.
- GitHub Actions annotations and job summaries.
- On-demand `/patchguard` command workflow.
- Configurable policy gate.
- Generated-test failure mappings.
- Behavioral contract extraction when OpenAI is enabled.
- Evidence-based AI review when OpenAI is enabled.

Known limitations:

- Behavioral contracts, generated tests, and AI review need an OpenAI API key and may need human review.
- Dependency installation can fail for some repositories; PatchGuard captures this as partial evidence.
- GitHub Pages can only host the static frontend, not the Docker-backed analyzer.
- Semgrep, TypeScript, hosted queueing, and report history are not implemented yet.

## Roadmap

- TypeScript repository support.
- Semgrep rules and richer security policies.
- Coverage-guided test generation.
- Mutation testing for generated regression tests.
- SWE-bench mini evaluation mode.
- GitHub App installation flow.
- GitHub Checks API integration with hosted reports.
- Report history with SQLite-backed API storage.

## Development

Run backend tests:

```bash
. .venv/bin/activate
python -m pytest -q
python -m ruff check .
```

Build the frontend:

```bash
cd frontend
npm install
npm run build
```

Package install checks:

```bash
python -m pip install -e .
patchguard analyze --help
```

## Project Summary For Resume Review

Use this factual summary when asking a recruiter, mentor, or ChatGPT to help write resume bullets:

> PatchGuard is an open-source Python developer tool that converts GitHub pull requests into evidence-backed merge-risk reports. It fetches PR metadata and diffs, checks out the PR, identifies changed Python functions with AST analysis, executes pytest/Ruff/Bandit in Docker with resource limits, and computes a deterministic risk score and configurable policy decision. It includes a package-friendly CLI, FastAPI adapter, React dashboard, reusable GitHub Action, GitHub annotations, and optional LLM-generated regression tests. The repository includes controlled demo cases, checked-in reports produced by the real pipeline, and CI for backend tests, linting, frontend builds, and Action smoke testing.

Engineering themes demonstrated:

- Designed a modular evidence pipeline around explicit Pydantic report models.
- Executed untrusted repository commands inside Docker with time, CPU, memory, and network limits.
- Preserved failed and skipped analysis steps as structured partial evidence instead of inventing results.
- Kept merge-risk scoring deterministic while using optional LLMs only for contract extraction, test generation, and evidence summaries.
- Packaged one analysis workflow across CLI, FastAPI, React, and GitHub Actions surfaces.

Honest scope: the current MVP supports public Python PRs, tests the PR head rather than comparing base-versus-head results, and treats Docker as practical isolation rather than a hardened multi-tenant sandbox.

## License
