# Research Index

This directory contains isolated experiments, external evaluation data,
benchmark fixtures, reports, and outputs. Nothing here is imported by the
production chatbot at runtime.

## Studies

| Study | Purpose | Status |
|---|---|---|
| [2026-08-19 Retrieval Query Refinement](2026-08-19_retrieval_query_refinement/RESEARCH_SUMMARY.md) | Child-vector representation, query refinement, RRF, and narrative-angle retrieval experiments | Research complete; no production retrieval change |

Production specifications and active ETL/runtime assets remain in `docs/` and
`backend/`.

## Shared automated-study framework

New live metrics or validation studies should use
[`_framework/README.md`](_framework/README.md), rather than copying a prior
study runner. The framework invokes the real compiled graph in an isolated
process, applies the established rate-limit discipline, captures a stable
graph-state telemetry record, and writes timestamped JSONL/progress/report
artifacts without invoking chat-history or memory persistence.

```bash
python3 -m research._framework.create_study 2026-09-05_example_study
python3 -m research._framework.run_study research/2026-09-05_example_study/study.json --dry-run
python3 -m research._framework.run_study research/2026-09-05_example_study/study.json
```

Each new study keeps its hypothesis, fixtures, rubric, source review, and
conclusions in its own directory. The shared framework handles only recurring
execution and telemetry mechanics. The mandatory methodology is documented in
[`docs/AUTOMATED_METRICS_HARNESS_GUIDELINE_v1.0.md`](../docs/AUTOMATED_METRICS_HARNESS_GUIDELINE_v1.0.md).
