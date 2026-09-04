# Research Framework

This is shared infrastructure for live, production-isolated research studies.
It is not imported by the chatbot runtime.

## What it standardizes

- Direct invocation of the real compiled `app_graph`—not mocked nodes.
- Unique graph thread/session IDs, structured request context, and optional
  correlated system-log extraction.
- No chat-history or memory archival in the default graph-validation mode.
- JSONL fixtures and result artifacts.
- Rate-limit pacing, per-minute retry, daily-quota stop, and bounded retries.
- Stable graph-state telemetry and basic declarative assertions.
- A generated mechanical run report; source/semantic conclusions remain the
  study report's responsibility.

## Create and run a study

```bash
python3 -m research._framework.create_study 2026-09-05_example_study
python3 -m research._framework.run_study research/2026-09-05_example_study/study.json --dry-run
python3 -m research._framework.run_study research/2026-09-05_example_study/study.json
```

Run from the repository root with the required provider keys available in the
shell. The runner creates timestamped result/progress files and a generated
report under the study directory.

## Boundaries

`graph_validation` is intentionally the only implemented adapter. It is the
same isolated direct-graph pattern used by the existing September live studies.
An HTTP/API adapter must be added explicitly for a study that needs endpoint,
persistence, checkout, or frontend verification; it must use namespaced
evaluation users and must not be silently substituted for graph validation.

See `docs/AUTOMATED_METRICS_HARNESS_GUIDELINE_v1.0.md` for the mandatory
methodology, corpus, safety, and reporting rules.
