# Live Validation: Whole-System, Not One Node

**Date:** 2026-09-04
**Status:** Complete — 27/27 queries processed, zero errors, zero rate limits hit.
Content correctness reasoned and source-verified for all 27 (see below).
**Scope:** Broad, live validation of the entire pipeline — Triage decomposition,
Intent Classification (search/clarify/decline), RAG retrieval + Sufficiency,
deeplink/service_request/api dispatch, Response Generation, the outbound
Compliance & Groundedness audit + Per-Task Reviser loop, Precision
Substitution, and the personal-data/token mechanism — using a fresh set of
real BANKING77 queries plus two hand-built controls, run against the real,
unmodified backend. This is a companion study to
[2026-09-04_relevant_irrelevant_validation](../../2026-09-04_relevant_irrelevant_validation/reports/RESEARCH_SUMMARY.md),
which targeted one node specifically; this one deliberately does not.

## Method

Same harness design and safety guarantees as the prior study — see that
report for the full rate-limit/backoff/no-persistence explanation, which
applies identically here. Differences this round:

- **27 queries** (25 real BANKING77 queries from 25 categories not used in
  the prior study, plus 2 hand-built controls: a personal-data lookup and a
  deliberately multi-part message combining a deeplink action, another
  deeplink action, and an out-of-scope decline in one turn).
- **Broader state capture** per turn: `triage_status`, full task results,
  decline/clarification tasks, `service_request_actions`,
  `navigation_actions`, `personal_data_reveals`/`personal_data_substituted`,
  `precision_substituted`, `soft_escalation_available`, and
  `safety_retry_count` (how many outbound-audit rounds a turn needed) — not
  just sufficiency status.
- **Important distinction the user asked about directly during this run:**
  this harness calls the real graph code in its own process — same logic,
  same nodes — but it is a **separate process** from the live `uvicorn`
  server, not a request routed through it. Confirmed by PID/PPID inspection
  (harness PID's parent is `1`, i.e. detached from the server). Both
  processes append to the same `backend/logs/system.jsonl`, which is how
  this analysis was able to trace individual turns after the fact.

## Results at a glance

| # | Query | Outcome |
|---|---|---|
| 1 | Activate new card | `SUFFICIENT`, real FAQ steps |
| 2 | Closest Mastercard ATM | `SUFFICIENT`, 2 real parents, **Precision Substitution fired** |
| 3 | Transfer arrival time | `SUFFICIENT` w/ guidance on recipient-bank delay |
| 4 | Link new card | Resolved via deeplink only, no RAG needed |
| 5 | Cash withdrawal charge | `SUFFICIENT`, correct guidance (no Horizon fee, ATM operator may surcharge) |
| 6 | PIN change location | `RELEVANT` w/ guidance + real parent; deeplink also offered |
| 7 | New card documents | `SUFFICIENT`, 2 real parents (MyInfo / passport etc.) |
| 8 | Declined card payment | **Real Territory B fired** — see below |
| 9 | Disposable card daily limit | `SUFFICIENT` w/ guidance directing to app settings |
| 10 | Currency exchange | `SUFFICIENT` — correctly states Horizon doesn't support forex at all |
| 11 | Disputing a statement fee | `SUFFICIENT`, 3 real parents, dispute steps |
| 12 | Virtual disposable card | `SUFFICIENT`, real parent on Horizon Link instant issuance |
| 13 | Lost/stolen card, police report filed | `SUFFICIENT` via deeplink + RAG combined |
| 14 | Retrieve passcode | `SUFFICIENT`; **1 audit/reviser round** before passing |
| 15 | Top-up still pending | `RELEVANT`, empty parents, honest "no info" guidance — **correctly reflected in the final answer** |
| 16 | Reinstate PIN | `SUFFICIENT`, 4 real parents, distinguishes Horizon Key vs. card PIN |
| 17 | Receive salary in different currency | `SUFFICIENT` — same forex-not-supported answer as #10, consistent |
| 18 | "Get an item refund" | No RAG match → genuine **CLARIFY** (credit/debit/deposit account?) |
| 19 | Foreign card charge + free top-up cards | **Two RELEVANT tasks, two real `unresolved_term`s, two clarifications from `sufficiency_auditor`** — see below |
| 20 | Top-up failed | `RELEVANT`, empty parents — **correctly reflected in the final answer** |
| 21 | Charged twice for a restaurant | `SUFFICIENT`; **Precision Substitution fired** |
| 22 | Source of funds | `SUFFICIENT`, 3 real parents |
| 23 | Get a Visa card | `SUFFICIENT`, 3 real parents |
| 24 | Identity verification frequency | `SUFFICIENT`; **1 audit/reviser round**, guidance correctly trimmed an over-claim |
| 25 | ATM gave less cash | `SUFFICIENT`, dispute steps |
| 26 | (control) "What's my address on file?" | `api` tool, **personal-data mechanism confirmed working end to end** |
| 27 | (control) multi-part: activate card + change PIN + margin trading | **Real bug found — see Headline Finding below** |

## Content correctness assessment (reasoned, source-verified)

The results table above checks *mechanics* — did the right node fire, did
the right status get assigned. This section goes one level deeper: for each
of the 27 responses, is the actual *content* correct given the query? Every
claim below was checked against the real FAQ source text in
`backend/data/etl/canonical_taxonomy/artifacts/tagged_horizon_faqs.json`
before being called a finding, not asserted from the response text alone.

**The large majority (roughly 20 of 27) are genuinely well-grounded and
correct** — either a direct, accurate answer from real evidence, or an
honest "no information" admission rather than a fabrication. Two are worth
calling out as good examples of judgment, not just correctness: query 16
correctly disambiguates "Horizon Key PIN" vs. "card PIN" without being
asked to, and query 17 makes a sound, appropriately-bounded inference
(forex not supported → salary can't be received in another currency)
rather than overreaching beyond what the evidence states.

**Five real correctness concerns were confirmed against source text:**

1. **Query 2 ("closest Mastercard ATM")** — `SUFFICIENT`, but the source FAQ
   (`faq_768b41d9`) only states overseas ATMs work "with **Visa** enabled."
   Mastercard is never mentioned. The customer's specific network question
   went unanswered while the response read as if it had been addressed.
2. **Query 23 ("How do I get a visa card?")** — same pattern: all three
   approved parents describe the generic application process (MyInfo,
   passport, payslip) with zero mention of card network. Whether Horizon
   even issues Visa-branded cards is never confirmed. Two independent
   occurrences of "customer names a specific card network, auditor approves
   network-agnostic evidence as SUFFICIENT without checking the network
   detail is covered" suggests a real, recurring pattern, not a fluke.
3. **Query 11 ("I'm not okay with this fee on my statement")** — all three
   approved parents (`faq_9aed7ae8`, `faq_9ffc5766`, `faq_f7a7feed`) are
   explicitly about **ATM cash-dispensal errors**, not statement fees. A
   clear topic mismatch that happened to produce an answer generic enough
   ("contact Customer Care to dispute") not to state anything false — but
   the evidence match itself is wrong.
4. **Query 21 ("Why am I being charged twice for a restaurant?")** — the
   approved parent's (`faq_d61a0a79`) own stated premise is *"I did **not**
   make a transaction on my card, but I got charged"* — an
   unauthorized-charge scenario. The customer clearly did make the
   transaction; their complaint is a duplicate charge, a different
   situation. This is exactly the premise mismatch the Evidence
   Applicability Rule exists to catch, and it was not caught here.
5. **Query 14 (passcode retrieval)** — not customer-facing (the real answer
   was correct, using the right FAQ), but the logged `generation_guidance`
   reads *"admit there is no information... do not provide instructions,"*
   flatly contradicting the SUFFICIENT status and the real instructions
   actually given. Confirms `generation_guidance` from an earlier,
   less-successful round can go stale and persist into a later round's
   successful verdict, even when the final answer correctly ignores it.

**One plausible-but-unconfirmed concern:** query 22 ("Where are my funds
being sourced from?") came from BANKING77's `verify_source_of_funds`
category — normally an AML-style "prove where your money came from"
question — but the system answered "how to fund your account" (FAST/PayNow
methods) instead. The query text alone is genuinely ambiguous enough that
this can't be called confirmed-wrong, just worth a second look.

## Headline finding: a mixed turn can silently lose already-resolved actions

Query 27 combined three subqueries in one message: activate a card
(→ deeplink), change a PIN (→ deeplink), and open a margin trading account
(→ out-of-scope decline). Tracing `backend/logs/system.jsonl` for this exact
turn:

1. Triage correctly decomposed all three components as `TOOL_ELIGIBLE`.
2. The Tool-Calling Planner correctly routed subqueries 1 and 2 to
   `deeplink` (tagged `high_risk_transaction` domain) — **both deeplinks
   registered and resolved successfully** (`card-management` →
   `/app/cards/manage`, `security-settings` → `/app/profile/security`).
3. Subquery 3 was routed to `rag`, reached Intent Classification, and
   correctly resolved to `DECLINE` (`out_of_scope_product`, margin trading).
4. **The log then stops.** No `resolution.planned`, no
   `response.flexible.generated`, no `outbound.compliance.completed` —
   nothing. The final response the customer actually received was *only*
   the decline text: *"Sorry, for your request, I can't provide an answer —
   it's not a product or service Horizon currently offers."* The two
   already-resolved deeplinks never reached the customer at all —
   `navigation_actions` came back empty.

**Root cause, precisely:** Intent Classification & Query Formulator
(`node_02e`) has a whole-turn shortcut: when the components it personally
formulates this round produce zero `search_tasks` and zero
`clarification_tasks` but at least one `decline_tasks` entry, it returns
`preflight_status: "declined"`, which routes straight to
`node_10_standard_rejection` → `END` — bypassing Resolution Planner and
Response Generation entirely (this is the same "all-decline" edge case
flagged, but not diagrammed, earlier in this project's own architecture
review). The bug is that this check only inspects **what node_02e itself
formulated this round** (here, just subquery_3, since subqueries 1 and 2
never reach node_02e at all — they were dispatched directly to `deeplink` by
the tool planner). It has no visibility into `financial_subqueries[*].
tool_attempts` already marked `completed` by other tools in the *same*
round. So "all of node_02e's own subqueries declined" gets treated as "the
whole turn is a decline," even when sibling subqueries in the identical turn
already succeeded via a different tool moments earlier.

**Impact:** any turn where every RAG/search-routed component declines,
while at least one other component in the same message was already resolved
via deeplink/service_request/api, will silently drop those resolved actions
and show the customer only the decline message. This is a real,
reproducible, customer-facing defect — not a hypothetical.

**Suggested fix direction:** the "declined" shortcut in `node_02e` should
also check whether any sibling subquery in `financial_subqueries` already
has a `completed` tool_attempt from a non-RAG tool this round, and if so,
fall through to Resolution Planner/Response Generation instead of
short-circuiting to Standard Rejection — the decline still needs to be
communicated, but alongside the turn's other genuine results, not instead of
them.

## Secondary findings

**The "guidance-only RELEVANT rejected by audit" bug from the prior study is
real but intermittent, not systemic.** This round produced three more
guidance-only `RELEVANT` cases with empty `approved_parent_ids` (queries 15,
20, and part of 8) — and in **all three**, the final response correctly
reflected the honest "no information" guidance, with no audit-rejection
loop. Only the prior study's query 5 (exchange rates) hit the failure mode.
This suggests the outbound audit's confusion is triggered by something more
specific than "guidance-only entry exists" — worth a targeted follow-up
comparing what differed about that one case (e.g., the resolved
subquery's own intent label, "General Inquiries," may itself have been read
by the audit as implying broader evidence should exist).

**Real Territory B fired again (query 8), still without reaching
SUFFICIENT.** `task_1` (in_scope) came back `RELEVANT` with empty parents
plus a real courtesy guidance; its companion `task_1_scope` (adjacent_scope)
came back `RELEVANT` with one real parent. Across both studies now, Territory
B has fired twice live, and neither occurrence reached SUFFICIENT — the
specific scenario the Territory Ceiling removal was designed for (an
adjacent_scope companion turning out to be the *actually correct* answer)
remains unobserved in live traffic. Still recommend a deliberately-engineered
query for this, per the prior report.

**Multi-fact partial answers correctly trigger multiple real
clarifications.** Query 19 combined two genuinely separate facts (a foreign
transaction fee question and a "which cards are free" question) into one
subquery; the Sufficiency Auditor split its judgment into two `RELEVANT`
tasks, each with its own real `unresolved_term`, each producing its own
clarification from `sufficiency_auditor` — while the final answer still gave
a real, specific, useful response (naming the Horizon Cashback card and its
fee waiver). This is exactly the intended "no info is info, ask only when it
would genuinely change the outcome" behavior working correctly on a
realistically messy real query.

**The audit/reviser loop is not just a theoretical safety net — it fired
live, twice, correctly.** Queries 14 and 24 each needed exactly one revision
round (`safety_retry_count: 1`) before passing; both final answers read as
appropriately trimmed/corrected rather than degraded, consistent with the
per-task reviser only touching what needed fixing.

**Coverage note, not a defect:** no query in this batch triggered
`service_request` (the form-based path was exercised in the prior study's
queries 2–3, so overall coverage across both studies is fine, but this batch
alone leaned entirely on `rag`/`deeplink`/`api`).

## Reuse

Query set, harness, raw JSONL results, and progress log live under
`research/2026-09-04_full_system_validation/`. Same re-run instructions as
the prior study (export the running server's `GOOGLE_API_KEY`/
`GROQ_API_KEY` into your shell, then run `scripts/run_validation.py` from
the repo root). To extend coverage, edit `TEST_QUERIES` — the harness itself
needs no changes.
