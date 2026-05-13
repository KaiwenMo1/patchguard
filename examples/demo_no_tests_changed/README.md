# Demo No Tests Changed

This demo mimics a PR that changes source behavior without adding or updating tests.

The changed code now truncates the discounted price to an integer instead of rounding to cents. There is intentionally no `tests/` directory in this demo, so PatchGuard should call out that source changed without tests.

Run from the project root:

```bash
env -u OPENAI_API_KEY python -m patchguard.cli analyze-demo examples/demo_no_tests_changed --out examples/sample_reports/demo_no_tests_changed.json
```
