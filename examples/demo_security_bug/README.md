# Demo Security Bug

This demo mimics a PR that changes a safe lookup helper into an `eval`-based evaluator.

The existing tests pass because they only cover the happy path, but Bandit should flag the unsafe `eval` usage.

Run from the project root:

```bash
env -u OPENAI_API_KEY python -m patchguard.cli analyze-demo examples/demo_security_bug --out examples/sample_reports/demo_security_bug.json
```
