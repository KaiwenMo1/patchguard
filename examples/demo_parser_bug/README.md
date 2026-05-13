# Demo Parser Bug

This demo mimics a PR that breaks empty input handling in a small parser.

The intended behavior is:

```python
parse_csv_line("") == []
```

The PR state in `repo/` now returns `[""]` for empty input. The existing tests intentionally miss that edge case, so this demo shows why changed-function context and generated regression tests are useful.

Run from the project root:

```bash
env -u OPENAI_API_KEY python -m patchguard.cli analyze-demo examples/demo_parser_bug --out examples/sample_reports/demo_parser_bug.json
```

To turn this into a real GitHub PR later, initialize `repo/` as a repository, commit the fixed base behavior from `patch.diff`, apply the changed line, and open a PR from `break-empty-input` to `main`.
