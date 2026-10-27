# JSON integrity: malformed-reply scan and schema injection check (16 Oct 2026)

## Scan (`../2026-10-16_json_malform_scan/scan.py`, deterministic, no model calls)
Every Gemini reply in five recent runs (8,178) was parsed with the bot's own steps (fence strip, json.loads, BUG-065 trailing-brace repair).

| | Replies | Malformed |
|---|---|---|
| Calls with a response_schema (evidence check, tool planner, compliance screen, triage, formulator, consistency check) | 5,122 | 0 |
| Calls without one (outbound fact-check, reply writer and statement writer, reviser) | 2,104 | 19 (0.9%) |

Failure shapes were varied (missing comma/bracket, unquoted key, invalid escape, stray "- " list marker, extra content, prose), so a deterministic repair cannot cover them safely. Cause: the combined fact-check prompt title no longer matched the schema router's marker; the writer, statement writer and reviser never had a schema.

## Change
New schemas in `backend/app/core/llm_service.py` (`NormalCombinedAudit`, `NonFinancialCombinedAudit`, `FlexibleResponseGeneration`, `PreDecidedStatementComposition`), passed explicitly at the call sites in `nodes.py`. They mirror each prompt's output shape and include `generation_guidance` on violations (the old `GroundingViolation` lacked it). The reviser is deliberately left without a schema (below).

## Checks
- `collect.py` + `replay.py`: 53 real prompts from the 15 Oct release run (all 9 whose original reply was malformed, plus a seeded random sample) replayed with the schemas: 53/53 parse, 9/9 formerly malformed now parse, no missing field.
- `paired.py`: each prompt rerun with and without the schema (the no-schema rerun measures ordinary model variation). Fact-check verdicts vs original: schema 18/18, no schema 17/18. Writer content similarity to original: schema 0.69, no schema 0.51. Statement writer 0.96 vs 1.00.
- Reviser: with a schema it kept a flagged sentence in 7 of 10 repeated runs of one case (field order made no difference) against 0 of 5 without; it had 0 malformed replies in 97 observed calls. Schema not applied.
- R5 planted-error replay with the fact-check schema (`../2026-10-04_v51_holdout/scripts/r5_replay_schema.py`): 30/30 planted errors caught (unchanged); false alarms 4/30 (was 2/30), all four on the adjacent-scope framing rule.
- Offline regression suites: all pass.
