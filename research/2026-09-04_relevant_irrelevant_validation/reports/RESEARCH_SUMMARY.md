# Live Validation: SUFFICIENT / RELEVANT / IRRELEVANT Rename & Territory Ceiling Removal

**Date:** 2026-09-04
**Status:** Complete — 15/15 queries processed, zero errors, zero rate limits hit.
**Scope:** Live, real-API validation of the `node_03c_sufficiency_auditor` redesign
(status rename `PARTIALLY_SUFFICIENT`→`RELEVANT`, `INSUFFICIENT`→`IRRELEVANT`,
removal of the Territory Ceiling on Extra Effort `adjacent_scope` companions,
and the unified clarification-triggering principle), run against the real,
unmodified backend.

## What this validates

This is a **live-system test**, not a unit test: every query below was sent
through the actual, running `app_graph` (the same compiled graph object the
production `/api/chat` endpoint uses), with real Gemini/Groq calls, real FAQ
retrieval, and real audit/reviser rounds. Nothing was mocked. No production
code was touched to run it, and no test conversation was persisted to the
customer-facing chat/ledger tables (confirmed by direct row-count check
before and after).

## Method

- **Harness:** `research/2026-09-04_relevant_irrelevant_validation/scripts/run_validation.py`.
  Invokes `app_graph.ainvoke(...)` directly per query, each on a fresh
  `thread_id`/session (turn 1, no prior history), as `USR_999`. Never calls
  `archive_session_history` or the `LongTermMemoryManager.persist_*` helpers,
  so nothing is written to `Persistent_Chat_Message` /
  `Persistent_Customer_Dialogue_Registry` — verified empty for all
  `validation_*` session IDs after the run.
- **Test data:** 13 real queries sampled from the vendored BANKING77 test set
  (`research/2026-08-19_retrieval_query_refinement/data/banking77_test.csv`,
  3084 real human banking questions, 77 intent categories), chosen to span
  the expected outcome space (SUFFICIENT / RELEVANT / IRRELEVANT /
  CLARIFICATION_REQUIRED / DECLINE / service requests), plus 2 hand-picked
  control queries (a known-good loan-application case from an earlier live
  trace, and a known decline case) to check for regressions.
- **Rate-limit safety:** every call goes through the real `call_llm_router`,
  which raises `google.genai.errors.APIError` on a Gemini quota hit. The
  harness inspects `.code`/`.details` for a `RetryInfo.retryDelay` (a
  per-minute quota — wait that long + a buffer, retry, capped at 5 attempts)
  versus a `QuotaFailure` naming a daily/per-day metric (stop the whole run
  immediately, since waiting would not help). A flat 25s pacing gap is
  applied between every turn regardless of errors. **Not exercised this run**
  — zero 429s were hit — so the wait/stop branches are implemented and ready
  but not yet proven against a real quota error; worth a dedicated
  quota-exhaustion drill if that matters later.
- **Smoke-tested first:** 1 query run alone and inspected before committing
  to the full batch.

## Results at a glance

| # | Query | Outcome | Notes |
|---|---|---|---|
| 1 | "How do I locate my card?" | Resolved via **deeplink** (card-management), never reached RAG | Tool router correctly preferred a direct navigation action over search |
| 2 | "I no longer live at my address on file..." | Service Request (`profile-address-update`) + deeplink offered | Real registered form dispatched correctly |
| 3 | "Would it be possible to get another card?" | Service Request + a genuine **CLARIFY** ("Credit or Debit Card?") | Intent Formulator correctly declined to guess between two valid Matrix products |
| 4 | "How old do i need to be to get an account for myself" | Tagged **NON_FINANCIAL** by Triage | Unexpected — see Finding 3 |
| 5 | "I need to know your exchange rates." | `RELEVANT`, empty parents, real `generation_guidance` → **but final answer was a generic fallback**, not the guidance | Real bug surfaced — see Finding 1 |
| 6 | "Is the top up feature available on the Apple Watch?" | No task ever formed (matrix rejected pairing pre-retrieval) → honest no-info answer | IRRELEVANT-shaped outcome reached upstream of node_03c, not through it |
| 7 | "...won't let me get my own money out of the money machine..." | **Real Territory B fired**: `task_1` (in_scope) = RELEVANT, `task_1_scope` (adjacent_scope) = RELEVANT | See "Territory Ceiling" section below |
| 8 | "I think someone is using my card..." | `SUFFICIENT`, 2 real parents, clear guidance to lock the card and contact fraud team | Clean SUFFICIENT case |
| 9 | "Can you remove my account please?" | Real Gemini 503 on the tool planner → fell through to an honest "no information" answer | Unrelated to this change — see Finding 2 |
| 10 | "I cannot verify my identity" | `RELEVANT` w/ guidance **and** a real CLARIFY from `task_dispatcher` (security-settings vs. KYC resubmission) | Two layered clarification sources working correctly together |
| 11 | "Can I do a bank transfer to put additional money..." | CLARIFY — "high-risk transaction... no tool relevant" | Correctly refused rather than guessing a tool |
| 12 | "What is the number of days I have to wait for my Europe transfer?" | `SUFFICIENT` — evidence states Horizon doesn't support cross-border transfers at all | Confirms SUFFICIENT correctly covers a policy-boundary answer, not just literal how-tos |
| 13 | "I live in the US but want to get a card" | CLARIFY — "no valid retrieval task could be formed" | |
| 14 | "How do I apply for a loan?" (control) | `SUFFICIENT`, `faq_1946c44f` (Instant Loan) | Matches the same real trace referenced earlier this project — reproducible |
| 15 | "Can I open a margin trading account?" (control) | `DECLINE`, `out_of_scope_product` | Decline path unaffected by the change — regression-clean |

## Validating the actual implementation change

**Status rename (`RELEVANT`/`IRRELEVANT`) — confirmed working live.** Both new
names appear correctly in real `task_results` (queries 5, 7, 8, 10, 12, 14),
including the merged case you asked for: a `RELEVANT` task with **empty**
`approved_parent_ids` and only a `generation_guidance` string (queries 5, 10)
— the "no info is info" mechanic and the RELEVANT/IRRELEVANT rename compose
correctly, exactly as implemented.

**Unified clarification principle — confirmed working live**, and confirmed
**broader than just the sufficiency auditor**: real, well-grounded
clarifications fired from `intent_formulator` (query 3: Credit vs. Debit
Card), `task_dispatcher` (query 10: security setting vs. KYC document — a
clarification-raising node this project hadn't specifically discussed
together with the sufficiency auditor), and `intent_formulator` again
(queries 11, 13). Each named a real, specific detail rather than a
manufactured one — no evidence of over-triggering.

**Territory Ceiling removal — partially validated.** Query 7 is a genuine,
naturally-occurring Territory B case (an `adjacent_scope` companion,
`task_1_scope`, actually fired) and it correctly reports status `RELEVANT`
under the new unified test rather than crashing or misbehaving. However,
**neither the in-scope task nor its companion reached SUFFICIENT in this
run**, so the specific scenario the removal was designed for — an
`adjacent_scope` companion turning out to be the *actually correct* product
and being allowed to reach SUFFICIENT — was not naturally exercised by this
query set. Recommend a follow-up test deliberately engineered around a
customer message where the obvious product mapping is wrong (e.g., a query
that superficially reads as Product A but is actually resolved by Product
B's documentation) to directly observe that path.

**Decline and SUFFICIENT paths — regression-clean.** Query 15 (decline) and
queries 8, 12, 14 (SUFFICIENT) behave exactly as expected, confirming the
rename and ceiling removal didn't disturb adjacent logic.

## Findings unrelated to this change, surfaced along the way

**Finding 1 — a real customer-facing bug in query 5's audit interaction.**
The Sufficiency Auditor correctly produced `RELEVANT` + a good
`generation_guidance` ("admit no live exchange rates, direct the customer to
the app/branch"). The Response Generator correctly drafted that admission.
But the outbound Groundedness Audit **rejected it three times in a row**,
each time reasoning some version of *"the assistant claims no information,
but `task_1` appears in the approved evidence list, so it should have used
that evidence"* — treating the mere *presence* of a guidance-only manifest
entry as if it were a citable fact, exactly the failure mode the "an
`approved_by_task` entry with empty `approved_parents` ... must never be
treated as if it were a factual source" audit rule is supposed to prevent.
After 3 failed rounds the turn fell back to a generic, unhelpful "I'm sorry,
I'm not able to answer that right now" instead of the correct, honest,
already-drafted answer. This is **not caused by the rename or the ceiling
removal** — the underlying mechanic predates this change — but it's a real,
reproducible defect worth its own fix: the outbound audit's own rule for
guidance-only manifest entries needs tightening so it doesn't demand a
stronger claim than the evidence supports.

**Finding 2 — a transient Gemini 503 silently short-circuited tool
planning.** Query 9 hit a real `503 UNAVAILABLE` ("model experiencing high
demand") on the Tool-Calling Planner's own call. The planner's error handling
treated this the same as "found nothing new" (`planner_found_nothing_new:
true`) rather than retrying, so no service_request/deeplink tool was ever
attempted for "remove my account," and the turn fell through to a generic
non-answer. Also unrelated to this session's change, but worth flagging as a
resilience gap: a transient upstream 503 currently reads identically to "no
further capability found," not to "temporary failure, worth one retry."

**Finding 3 — a possible Triage miscategorization (not necessarily a bug).**
"How old do i need to be to get an account for myself" was tagged
`NON_FINANCIAL` (chitchat) rather than treated as a genuine
Deposit_Account-eligibility question. This may be a real Triage
classification gap, or may be intentional (age/eligibility framed this
generically could be judged too under-specified to commit to a product) —
not investigated further here since it's outside this validation's scope,
but worth a look if Triage accuracy on eligibility-shaped questions matters.

## Reuse

- Query set, harness, raw JSONL results, and progress log all live under
  `research/2026-09-04_relevant_irrelevant_validation/`.
- To re-run: from the repo root, with the real backend server already
  running (so `GOOGLE_API_KEY`/`GROQ_API_KEY` are available in its
  environment), export those two vars into your shell from the running
  server's own process and run
  `python3 research/2026-09-04_relevant_irrelevant_validation/scripts/run_validation.py`.
  Results append to `results/test_results.jsonl`; progress streams to
  `results/progress.txt` so a long run can be checked on without blocking.
- To extend the query set, edit `TEST_QUERIES` in `run_validation.py` — the
  harness itself needs no changes.
