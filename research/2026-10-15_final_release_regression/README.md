# Final release regression

This is the release-candidate validation run started on 15 October 2026.
It reruns the complete frozen v5.1 fixture set against the current code and
adds an isolated 60-turn C4 cache-parent recall run.

## Live scope

| Suite | Conversations | Customer turns |
|---|---:|---:|
| H3 safety | 100 | 114 |
| H1 Banking77 | 234 | 234 |
| H2b journeys | 15 | 40 |
| H4 controls | 105 | 105 |
| C4 isolated cache run | 60 | 60 |
| **Total** | **514** | **553** |

Removed or out-of-MVP metrics (B4, B7, C5, C8, R6 and self-harm) do not
gate release. Their existing fixture rows may still run as diagnostic
regression coverage. Provider unavailability makes the run incomplete, not a
bot failure.

## Resilience

The application router tries every key in `research/.research_api_keys` for
Gemini 3.5 Flash-Lite. If all keys are exhausted, it resets to key 1 and
switches to Gemini 3.1 Flash-Lite. Only after every key/model pair is
unavailable does the research wrapper wait and retry.

Each completed case is flushed immediately to JSONL. Re-running
`run_final.sh` skips prior `OK` cases with the same study ID. `nohup` keeps the
launcher independent of the terminal; macOS sleep suspends it and it resumes
after wake. A killed process can be restarted with the same command.

## Commands

```bash
nohup ./research/2026-10-15_final_release_regression/run_final.sh \
  > ./research/2026-10-15_final_release_regression/results/launcher.out 2>&1 &

./research/2026-10-15_final_release_regression/status.sh
```

The normal suites share one isolated copy of the release data, matching the
historical suite ordering. C4 uses a second isolated data copy with an empty
`subquery_answer_cache` collection.
