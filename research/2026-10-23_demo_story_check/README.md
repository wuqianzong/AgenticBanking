# Demo user-story check, 23 Oct 2026

This is a live replay of every story in the report's "Demo user stories" tab. It covers S1–S6, the backups B1–B5 and three taboo probes, sent through the real chat API. Each reply is compared with the tab's expectation.

- **Code:** commit 117c48c, with no uncommitted backend changes.
- **Model:** Gemini 3.5 Flash-Lite with the 10-key pool. There was no 3.1 fallback; 297 model calls and 40 prompt-guard calls.
- **Data:** a fresh copy of the live data with an empty answer cache.
- **Test customer:** USR_999, used only inside that copy. Its profile is pinned to English, professional tone, emoji enabled and intermediate literacy, and its old pending requests are expired.

This is a pass/fail check of the demo script, not a metrics run.

## Result

20 of 23 cases pass every must-check. No customer address, balance or typed PIN appeared in any model prompt.

| Case | Verdict | What happened |
|---|---|---|
| S3 turn 3, "Yes, that's correct." | FAIL | The Send Money button was lost. The reply said "The available information does not cover confirming a $200 transfer to Maria specifically." It made no false claim that money was sent. On 18 Oct this turn passed. |
| S5 turn 1, scam call asking for a PIN | FAIL | The reply said "The available information does not specifically confirm whether you should give your PIN …", then gave lock-card steps and contacts. It never said "never share". On 18 Oct this turn passed. |
| S1 Chinese opener | FAIL (by design) | Answered in English. Replies follow the profile language, which is pinned to English here. Mid-session language switching is not implemented. |
| S4 turn 1, S6 turn 2 | Should-miss | Neither asked a clarifying question this time, so requirement 4 was not shown in this run. |
| S2 Moving house | PASS | Confirms the corrected order: leave the form, log out and in, and the greeting says "you still have a pending Change Address request"; the form then submits as completed. |
| Taboos 5–7 | PASS | Tax and legal questions were refused by the compliance screen in 4–8 s. Approval: "Credit card applications are not guaranteed …". |

The median reply time was 41 s, against 11–24 s on 18 Oct. The range was 4–85 s, apart from B5_atm, which took 666 s because of a provider 503 "high demand" spike that was waited out. The model service was under load during this run.

## Files

- `scripts/cases.py` holds the stories and the per-turn must and should expectations.
- `scripts/build.py` builds the isolated copy and the study.
- `scripts/story2_flow.py` drives Story 2 end to end: form, logout, login and submit.
- `scripts/check.py` holds the checker.
- `runs/stories/results/*_results.jsonl` and `runs/story2/results/story2_results.jsonl` contain every turn, with the reply, buttons, forms and state summary.
- `runs/*/results/capture_log.jsonl` holds every model prompt and response, plus every pipeline event, keyed by session ID.
- `results/CHECK_REPORT.md` and `results/check.json` hold the per-turn verdicts.

To rerun: `python scripts/build.py`, then `./run.sh` (resumable).
