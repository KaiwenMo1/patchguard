# PatchGuard GitHub App Plan

This folder defines the plan for turning PatchGuard from a CLI/GitHub Action into an installable GitHub App.

The product goal is:

> Install PatchGuard once on a personal GitHub account or organization, choose repositories, and get continuous evidence-based audits for PRs, pushes, and recent repository history.

Start with [implementation-guide.md](implementation-guide.md) to understand the target architecture and scope. Use [prompt-order.md](prompt-order.md) as the phase-by-phase build sequence.

To run the MVP as a private local GitHub App, follow [local-development.md](local-development.md).
To deploy the GitHub App webhook, worker, dashboard history, and hosted Check Run links, follow [hosted-deployment.md](hosted-deployment.md).

## North Star

PatchGuard should become a GitHub-native repository audit system, not just a one-off PR analyzer.

After installation, it should:

- Discover installed repositories.
- Backfill a small recent-history audit.
- Automatically analyze new pull requests.
- Optionally analyze pushes to default branches.
- Publish GitHub Checks or concise PR comments.
- Store report history.
- Show a cross-repository dashboard.

## First MVP

The first GitHub App MVP should be intentionally small:

1. Receive GitHub App webhooks.
2. Verify webhook signatures.
3. Store installations, repositories, and analysis jobs in SQLite.
4. On `pull_request` events, enqueue one PatchGuard analysis.
5. Run the existing PatchGuard pipeline in a worker.
6. Store the report.
7. Create or update a GitHub Check Run with the result.

Do not start with billing, marketplace publishing, multi-tenant hardening, or full historical analysis. Those come after the install-and-run loop works.
