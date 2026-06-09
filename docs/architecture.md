# PatchGuard Architecture

PatchGuard turns a GitHub pull request into an evidence-backed merge-risk report. The main product path is the `patchguard analyze` CLI command; the FastAPI API, dashboard, and GitHub Action are adapters around that same analysis pipeline.

## System Flow

```mermaid
flowchart LR
    A[GitHub PR URL] --> B[GitHub service]
    B --> C[PR metadata and changed files]
    C --> D[Clone and checkout PR head]
    D --> E[Changed-function extraction]
    D --> F[Docker sandbox]
    F --> G[Dependency install]
    G --> H[Existing pytest suite]
    G --> I[Generated pytest suite]
    F --> J[Ruff and Bandit]
    C --> K[Deterministic risk scoring]
    E --> K
    H --> K
    I --> K
    J --> K
    K --> L[Policy gate]
    L --> M[JSON or Markdown report]
    M --> N[CLI, API, dashboard, PR comment]
```

## Core Modules

| Area | Responsibility |
| --- | --- |
| `services/github_service.py` | Parse PR URLs and fetch GitHub metadata and changed files. |
| `services/clone_service.py` | Create an isolated workspace and check out the PR head SHA. |
| `services/function_extractor.py` | Match changed Python diff lines to functions and classes using AST ranges. |
| `services/sandbox_service.py` | Run repository commands in Docker with time, CPU, memory, and network limits. |
| `services/security_scan_service.py` | Run Ruff and Bandit and retain findings on changed lines. |
| `services/risk_score_service.py` | Compute deterministic risk dimensions, reasons, level, and recommendation. |
| `services/policy_service.py` | Apply repository-configurable blocking and warning rules. |
| `services/report_service.py` | Orchestrate the analysis pipeline and write structured evidence. |
| `api_app.py` | Submit and poll analyses handled by a simple local in-process worker. |
| `action.yml` | Package the CLI pipeline as a reusable GitHub Action. |

## Trust Boundaries

- GitHub metadata and PR code are untrusted inputs.
- Repository tests, dependency installers, Ruff, and Bandit execute inside Docker.
- Docker execution has time, CPU, memory, and disabled-network limits.
- LLM features are optional and disabled with `--skip-llm`.
- Risk scoring and policy decisions are deterministic; LLM output does not determine the score.
- Failures and skipped steps remain visible as partial evidence instead of being reported as passes.

## Current Scope And Limitations

- Python repositories and public GitHub pull requests are the supported MVP path.
- PatchGuard currently tests the PR head; base-versus-head regression comparison is planned.
- Docker provides process isolation but is not presented as a hardened multi-tenant security boundary.
- The FastAPI worker and JSON store are intended for local demos, not public multi-tenant hosting.
- Generated tests and AI review require an OpenAI API key and should receive human review.

## Design Decisions Recruiters May Ask About

**Why deterministic scoring?**  
Reviewers can trace every score contribution to collected evidence and tune policy independently from LLM behavior.

**Why Docker?**  
PatchGuard must execute code from repositories it does not control. Docker provides a practical local and CI isolation boundary while preserving reproducibility.

**Why partial reports?**  
Dependency installation and repository tests often fail for environmental reasons. Preserving those failures as evidence is more useful than crashing or inventing a result.

**Why a static dashboard demo?**  
It lets users inspect real CLI-generated reports without granting a hosted service permission to execute arbitrary code.
