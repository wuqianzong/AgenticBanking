# Known Issues Tracker

A living record of real defects and suspected issues found in the running
system — as opposed to the dated `research/` studies, which are individual
experiments. This document is the place to check "has this already been
found," add a new entry when something new turns up, and update `Status`
when something gets fixed. Every entry links back to the research study (or
live session) that found it, so the original evidence/repro is never lost.

Severity: **High** (customer-facing data loss or a materially wrong claim),
**Medium** (a real defect, but bounded impact or narrow trigger condition),
**Low** (internal-only inconsistency, no customer-facing effect observed).

Confidence: **Confirmed** (reproduced and traced to a root cause) vs.
**Suspected** (a real observation, but not yet root-caused or reproduced a
second time).

| ID | Title | Severity | Confidence | Root Cause | Status |
|---|---|---|---|---|---|
| BUG-001 | Mixed-turn decline drops already-resolved actions from other tools | High | Confirmed | **Deterministic code** | **Fixed, live-verified** |
| BUG-002 | Guidance-only RELEVANT admission sometimes rejected in a loop by the outbound audit | Medium | Confirmed (intermittent) | LLM judgment | **Superseded, structurally eliminated (see BUG-010)** |
| BUG-003 | Transient upstream 503 on the Tool-Calling Planner is treated as "nothing found," not retried | Medium | Confirmed | **Deterministic code** | **Fixed, verified (mocked failure)** |
| BUG-004 | Network-specific questions (Visa/Mastercard) answered with network-agnostic evidence marked SUFFICIENT | Medium | Confirmed (2 occurrences) | LLM judgment, **now deterministically enforced** | **Fixed, live-verified (4 independent cases)** |
| BUG-005 | Evidence approved as SUFFICIENT can be topically mismatched to the customer's actual request | Medium–High | Confirmed | LLM judgment | **Not reproduced (re-measure 2026-09-25, Gemini, current code): original query now asks credit-card vs deposit statement; 3 same-category cases give honest limits** |
| BUG-006 | Evidence Applicability Rule missed a real premise mismatch (unauthorized-charge FAQ used for a duplicate-charge complaint) | Medium–High | Confirmed | LLM judgment | **Not reproduced (re-measure 2026-09-25): the restaurant duplicate-charge case now gives an honest no-info; the mismatched unauthorized-charge FAQ is no longer used** |
| BUG-007 | Stale `generation_guidance` from an earlier round can persist into a later round's successful verdict | Low | Confirmed | **Deterministic code** | **Fixed, live-verified** |
| SUSPECTED-001 | Triage may misclassify a genuine eligibility question as chitchat | Low | Suspected | LLM judgment | **Not reproduced (re-measure 2026-09-25): answered "16 years or older", 2/2** |
| SUSPECTED-002 | Possible query-intent misinterpretation for an ambiguous "source of funds" question | Low | Suspected | LLM judgment | **Reasonable (re-measure 2026-09-25): answered as where payments are drawn from (the primary account)** |
| BUG-008 | `NARROW_TO_EVIDENCE_SCOPE` recovery silently drops the unconfirmed claim instead of admitting it | Low–Medium | Confirmed | LLM judgment (prompt gap) | **Fixed, live-verified** |
| BUG-009 | Outbound audit silently PASSes a turn when response generation returns zero blocks | Medium–High | Confirmed (2 occurrences) | **Deterministic code** | **Fixed, verified (fixture test)** |
| BUG-010 | "No Info Is Info" misapplied to RELEVANT let a zero-evidence Territory B task fabricate specific policy details | High | Confirmed | LLM judgment, **now deterministically enforced** | **Fixed, live-verified** |
| BUG-011 | A vague reply gets attributed to a stale dormant topic instead of the assistant's own most recent turn | Medium–High | Confirmed | LLM judgment (prompt gap) | **Fixed, live-verified** |
| BUG-012 | Response blocks with a bare-string list field crash on `_llm_text() missing 1 required positional argument: 'key'` | Medium | Confirmed | **Deterministic code** | **Fixed** |
| BUG-013 | A structured-output response occasionally wrapped in a single-element list crashes with `'list' object has no attribute 'get'` | Low-Medium | Confirmed | **Deterministic code** | **Fixed** |
| BUG-014 | Precision Substitution occasionally leaves raw internal template fragments (`faq_xxxxxxxx_NNN`) in customer-facing text | Medium | Confirmed | **Deterministic code** (no check handles an unbraced placeholder the generator occasionally writes) | **Fixed (repair fixture-verified); unauthorized placeholders left to the auditor by decision** |
| BUG-015 | A small number of turns return a generic failure fallback ("please rephrase") instead of a real answer, on questions near-identical ones answer correctly -- and 22/24 of these were silently mislabeled "resolved" in every automated metric until corrected 2026-09-13 | Low-Medium | Confirmed (24 unique cases, full-corpus) | Not yet root-caused | **Mostly resolved (re-measure 2026-09-25): the card-linking case now answers; the remaining case is blocked by the Groq prompt guard (score 0.98 on "unauthorized transaction") -- a classifier judgment outside our prompts, accepted** |
| BUG-016 | Sufficiency auditor doesn't recognize an explicit negative/declarative fact as a complete answer | Medium | Confirmed (13/88 sample) | LLM judgment | **Accepted as model judgment (2026-09-25): improved (2 of 9 exchange cases still missed); rules work as designed, see below** |
| BUG-017 | A successfully fetched account balance is silently dropped when the turn also carries conversational framing | Medium | Confirmed (61/619 turns; 23/70-sample conversations at least once) | **Deterministic code** (routing condition omitted personal data) | **Fixed, live-verified** |
| BUG-018 | Response generation falsely denies having a balance it just successfully fetched | High | Confirmed (13/619) | **Deterministic code** (a RELEVANT RAG hedge left in generation data, contradicting a same-subquery tool's ground truth) | **Root-caused and fixed, fixture-verified** |
| BUG-019 | A short account-type follow-up gets reattached to an already-resolved transfer instead of the actual current balance question | Medium | Confirmed (3/70 sample) | LLM judgment | **Not reproduced (re-measure 2026-09-25): "my savings account, please" gets the balance, 2/2** |
| BUG-020 | Bot fabricates a second "other" savings account rather than correcting a customer's false premise | Medium | Confirmed (2/70 sample) | LLM judgment | **Accepted for MVP (2026-09-25): demo balance API has no multi-account notion; one-savings-account fact added to shared business facts (helps in 1 of 2 live runs)** |
| BUG-021 | Inconsistent handling of "how long will the transfer take" -- sometimes answered correctly, sometimes a generic non-answer | Low-Medium | Confirmed (3/70 sample) | Not yet root-caused | **Not reproduced (re-measure 2026-09-25): transfer-time follow-ups answered, 3/3 + control** |
| BUG-022 | A balance follow-up ("what about my savings account?") answered with a generic product-info dump instead of the number | Medium | Confirmed (1/70 sample) | Not yet root-caused | **Not reproduced (re-measure 2026-09-25): "what about my savings account?" gets the balance** |
| BUG-023 | Outcome depends on surface phrasing, not underlying intent -- 24/77 BANKING77 categories show a genuinely split outcome across their 40 phrasings of the same nominal question | Medium | Confirmed (full-corpus, `declined_card_payment` spot-checked) | Not yet root-caused | **Accepted as a known limitation (2026-09-25): consistent where an FAQ covers the topic (lost card 8/10), varies where none does (declined payment, unexplained charge) -- data gap plus model judgment** |
| BUG-024 | Bot never discloses it is an AI to the customer, in any supported language or anywhere in the UI | Medium | ~~Confirmed~~ **Retracted** -- original check missed an existing persistent footer disclosure | n/a | **Retracted (not a defect)** |
| BUG-025 | The exact same customer question, asked more than once, does not reliably get the same retrieval outcome -- no wording ambiguity involved | High | Confirmed (31/88 informative exact-duplicate-text groups, full-corpus) | Root-caused: two independent mechanisms found (retrieval-pool variance + auditor-judgment non-determinism) | **Retrieval-pool half: mitigated and live-validated. Auditor-judgment half: accepted permanent limitation, model-tuning scoped out** (see below) |
| BUG-026 | The response generator rarely writes the required closing sentence pointing to an attached shortcut button/form -- 20 of 236 logged shortcut turns (8.5%) despite a MUST rule in its prompt | Low-Medium | Confirmed (236 logged generator calls with a shortcut supplied) | LLM instruction-following, **now structurally guaranteed regardless of it** | **Fixed, live-verified** |
| BUG-027 | The deterministic fallback for a missing shortcut sentence is skipped whenever the shortcut's label already appears anywhere in the response text (e.g. inside FAQ step wording), so no pointing sentence appears at all | Medium | Confirmed (live case + 58/236 logged calls had the label only inside steps) | **Deterministic code** (plain substring test) | **Fixed, fixture-verified** |
| BUG-028 | A wholly-omitted clarification (or decline/no-info/conversational) obligation had no repair mechanism at all, unlike a dropped RAG task -- could exhaust every retry and reach the terminal fallback with the customer's question never asked | High | Confirmed (live trace + reproduced in the BANKING77 historical corpus) | **Deterministic code** (completeness net had detection but no repair for these obligation types) | **Fixed, live-verified** |
| BUG-029 | Internal tool-routing/reasoning text leaked verbatim into a customer-facing clarification question | Medium | Confirmed (1 historical case, root cause shared with BUG-028) | **Deterministic code** (same net gap as BUG-028 -- the leaked text was the omitted obligation's own internal routing note, never meant to reach the customer) | **Fixed, live-verified** |
| BUG-030 | No coherent handling for sustained upstream model unavailability -- triage silently discards the customer's message as generic small talk, and a single turn's latency can compound into minutes | Medium–High | Confirmed (2 independent symptoms, live-traced; historical latency trend across 12 greeting turns) | **Deterministic code** (no turn/node-level policy for sustained, as opposed to transient, model unavailability) | **Fixed: symptom 1 (model-unavailable message, 2026-09-25); symptom 2 (latency) resolved per user -- per-call timeout kept at 45s** |
| BUG-031 | Spanish `chat.footer` (the persistent AI-disclosure footer) is an untranslated copy of the English string | Low | Confirmed (static check of `frontend/src/translations.js`) | **Deterministic code (missing translation)** | **Fixed** |
| BUG-032 | One node_05 veto permanently pollutes every later turn's audit feedback, process-wide (shared mutable list in `VOLATILE_STATE_RESET`) | High | Confirmed (reproduced in-process; 171 sessions in the multi-turn corpus) | **Deterministic code** (in-place append to a shared module-level list) | **Fixed** |
| BUG-033 | Triage decomposed by sentence, not by need, splitting one request into overlapping subqueries (a topic lead-in became its own subquery) | Medium–High | Confirmed (opposite decompositions of the same message across runs) | LLM judgment (prompt defined components by text, not need) | **Fixed, live-verified (triage node)** |
| BUG-034 | Two outbound-audit rules conflict on whether a personal-data token is permitted evidence (a correct balance sentence rejected 3x) | Medium | Confirmed (1 live trace; intermittent) | LLM judgment (conflicting prompt rules) | **Fixed, live-verified (audit node)** |
| BUG-035 | The deterministic no-info statement repeats the customer's entire request back verbatim | Low–Medium | Confirmed (live) | **Deterministic code** (template) | **Fixed** |
| BUG-036 | `verified_session_evidence.recorded` re-logs the previous turn's record when the current turn adds none | Low | Confirmed | **Deterministic code** (logging only) | **Fixed** |
| BUG-037 | A completed redirect (deeplink/service_request) suppressed RAG evidence, leading the generator to fabricate a fact (card delivery "7 to 10" vs the real 6-7 days) | High | Confirmed (live) | **Deterministic code** (regression from BUG-018's fix: guard counted any completed tool, not only a data fetch) | **Fixed, fixture-verified** |
| BUG-038 | An audit violation on a task no block carries approves the block holding the flagged sentence; the reviser gets nothing and the flagged text ships | High | Confirmed (live) | **Deterministic code** (a RELEVANT task's separate "terminal marker" outlived its removed evidence; partition attribution) | **Fixed, live-verified: in the 2026-09-25 code-change validation run an unsupported claim was caught, the violation matched no block, the round went response-wide, and the reviser fixed it** |
| BUG-039 | No turn-level consistency check: results collected for one turn could contradict each other in the response (e.g. "no information on your balance" next to the fetched balance) whenever the same-subquery BUG-018 guard could not see the conflict | High | Confirmed (live, triage merged the two components the guard relies on) | **Missing capability** (semantic, turn-wide conflict judgment) | **Fixed, fixture- and live-judgment-verified** |
| BUG-040 | A RELEVANT entry's "not covered" notice contradicts another result in the same reply ("the available information does not cover how to view your current address" right after showing it from the customer's data) | Medium | Confirmed (live, Gemini validation) | **Deterministic code / prompt** (compose_partial guidance from 03fb488 does not limit the notice to what no other result covers) | **Fixed (bb5b83d + full-picture sufficiency auditor), replay- and live-verified** |
| BUG-041 | A card (shortcut) sentence is written twice -- by the model and by the completeness net; when the audit flags the duplicate, the flag is matched to a different block, so the reviser cannot remove it and the turn exhausts its retries | Medium | Confirmed (live, 3 cases) | **Deterministic code** (net insertion + partition attribution) | **Fixed: generator citation rule (V2); replay 0 misses, live-verified** |
| BUG-042 | A clarifying question the sufficiency auditor authored in retry round 1 is dropped when round 2 returns none, so no question is asked | Medium | Confirmed (live, 1 case) | **Deterministic code** (clarification not retained across sufficiency rounds) | **Fixed (487b3dd), fixture-verified** |
| BUG-043 | A no-info statement is suppressed whenever a redirect covers the need -- harmful when the planner picked the wrong screen ("Delete this account!" got only a Security Settings button) | Medium | Confirmed (live, 1 case) | **Design + LLM judgment** (redirect-covers-need rule; wrong deeplink choice) | **Fixed: planner chooses tools by the service they offer (not by risk); replay-verified** |
| BUG-044 | Small generation artifacts in customer text: a stray "(4)" after flattened steps, internal jargon ("no approved evidence or record"), a repeated opening sentence | Low | Confirmed (live, 3 cases) | LLM judgment / formatting | **Closed: jargon fixed (ed6e28d); placeholder step numbers = data quirk accepted for MVP; repetition = model writing under the existing coherence rule** |
| BUG-045 | A need the planner assigns no tool gets no obligation at all -- not even a no-info entry -- so the reply is empty and the customer gets the generic apology | High | Confirmed (live, DeepSeek) | **Deterministic code** (no fallback obligation for an open subquery with no tool and no evidence) | **Fixed (ff2c0f0): deterministic FAQ default + no-info failsafe; fixture- and live-verified** |
| BUG-046 | "My PIN is not working and I need assistance" is rejected by the inbound word filter (`credential_pin_disclosure`) before any model runs | Medium | Confirmed (live, every model) | **Deterministic code** (keyword rule false positive) | **Fixed (29845c7), matrix + live-verified** |
| BUG-047 | The completeness net's fallback sentence quotes the customer's request verbatim and ungrammatically ("Sorry, I don't have a complete answer for the part about I withdrew cash ... right now.") | Low-Medium | Confirmed (live, DeepSeek) | **Deterministic code** (net template) | **Fixed (fa1114b)** |
| BUG-048 | FAQs reachable only under one product tag are missed when the formulator picks another defensible product (ATM card-capture FAQs under `Deposit_Account` vs `Debit_Card`; the in-app address-change FAQ vs `Credit_Card`); a cache hit hid the gap | Medium | Confirmed (live, traced) | **Data / retrieval** (product tags vs. filter; retry widening depends on the model's alternates) | **Fixed: card part (physical card = Debit_Card, 8 FAQs tagged) and address part (formulator search query + Horizon App briefing; retry recovers an imperfect intent tag); replay-verified** |
| BUG-049 | The BUG-018 guard removes a RELEVANT FAQ result whenever a data fetch completed on the same need -- even when the need also asks something the data cannot answer ("I forgot my address... where can I change it?" kept the address but lost the change-address evidence) | Medium | Confirmed (live, DeepSeek) | **Deterministic code** (guard assumes a data fetch answers the whole subquery; need-based triage can combine a lookup and a how-to in one need) | **Fixed: guard retired (full-picture auditor + consistency check handle the contradiction); fixture-verified** |
| BUG-050 | When the reviser deletes a customer-data sentence (or any cited content), the completeness net does not restore it: the block still cites the handle (citations are locked during revision), so the net treats the obligation as covered | Medium | Confirmed (live, Gemini) | **Deterministic code** (net coverage keyed on locked citations, not on the revised text) | **Retracted -- misdiagnosis: the net did restore the address; the delivered reply contains it** |

**All four deterministic-code bugs (001, 003, 007, 009) are fixed.**
BUG-004 and BUG-010 are also fixed, both via the same hybrid approach (see
each entry): an LLM-judgment problem converted into a
deterministically-enforced contract. BUG-002 is now structurally
eliminated as a side effect of BUG-010's fix (the shape it was about no
longer exists). BUG-008 has a fix implemented and live-verified but not
yet proven at scale (see its own caveats). BUG-005, BUG-006, the two
suspected items, and everything from BUG-014 onward (found during the
BANKING77 and multi-turn SGD research campaigns, all intentionally left
undiagnosed per explicit instruction to document first) remain open.

**Why the distinction matters:** a deterministic-code bug (001, 003, 007,
009) has a fix with a guaranteed outcome — patch the logic, the failure mode
is gone. An LLM-judgment issue (005, 006, both suspected items) is the
model's own reasoning going wrong on a specific input despite the relevant
rule already being correctly wired into the prompt; a fix (tightening the
rule, adding an example) can reduce the rate but can't guarantee elimination
the way a code patch can — these need re-testing after any prompt change,
not just a one-time verification. **BUG-004 and BUG-010 show a third
pattern worth naming**: the underlying judgment (does this evidence confirm
what the customer specifically named? does a real parent back this
observation?) is still made by an LLM, but the *consequence* of getting it
wrong is now closed off deterministically — the model can still misjudge,
but it can no longer let that mistake silently produce an answerable
status, because the code enforces the downgrade regardless of what the
model's own `status` field says. This is a reusable pattern (see BUG-004's
"Reason-to-Guidance Contract" note) worth reaching for whenever a similar
upstream-can't-know / downstream-can-check gap shows up again, rather than
relying on prompt wording alone. BUG-010 additionally shows the value of
precisely re-checking a design decision against its *original* intent when
a later extension of it causes trouble — the fix here was less "add a new
rule" than "notice the rule had drifted from what it was actually meant to
cover, and restore that."

---

## BUG-001 — Mixed-turn decline drops already-resolved actions from other tools

**Severity:** High **Confidence:** Confirmed **Status: Fixed, live-verified**
**Found in:** [2026-09-04_full_system_validation](../research/2026-09-04_full_system_validation/reports/RESEARCH_SUMMARY.md), query 27

**What happens:** A single customer message combining an out-of-scope
request (e.g. margin trading) with one or more in-scope requests already
resolved by a non-RAG tool in the same round (e.g. two deeplink actions)
results in the customer receiving **only** the decline message. The
already-completed actions are silently dropped — not shown, not mentioned.

**Root cause:** Intent Classification & Query Formulator (`node_02e`) has a
whole-turn shortcut: when the components it personally formulates this round
produce zero `search_tasks` and zero `clarification_tasks` but at least one
`decline_tasks` entry, it returns `preflight_status: "declined"`, routing
straight to `node_10_standard_rejection` → `END`, bypassing Resolution
Planner and Response Generation entirely. This check only inspects what
`node_02e` itself formulated this round — it has no visibility into sibling
subqueries already marked `completed` by other tools (deeplink,
service_request, api) dispatched earlier in the same round.

**Reproduction:** Send "I want to activate my new card, also how do I
change my PIN, and can I open a margin trading account with you?" as a
fresh turn. Confirmed via `backend/logs/system.jsonl`: both deeplinks
register and resolve successfully, then the log stops — no
`resolution.planned`, no `response.flexible.generated` — before the decline
template renders alone.

**Suggested fix direction:** the "declined" shortcut in `node_02e` should
also check whether any sibling subquery in `financial_subqueries` already
has a `completed` tool_attempt from a non-RAG tool this round, and fall
through to Resolution Planner/Response Generation instead of
short-circuiting — the decline still needs to be communicated, but alongside
the turn's other genuine results, not instead of them.

**Fix implemented:** `node_02e` now computes
`any_sibling_subquery_resolved_by_other_tool` (reusing the existing
`_resolved_by_other_tool` helper across all `financial_subqueries`) and
threads it into three places: the "declined" shortcut no longer fires when
a sibling was already resolved, the generic-fallback-clarification guard
gets the same exclusion, and the `continue_plan` routing check now also
falls through when this flag is set. **Verified:** re-ran the exact
original repro query directly through `app_graph.ainvoke()` and confirmed
both previously-dropped deeplink actions (Manage Card, Security Settings)
now correctly appear in `navigation_actions` alongside the decline
message, instead of being silently discarded.

---

## BUG-002 — Guidance-only RELEVANT admission sometimes rejected in a loop by the outbound audit

**Severity:** Medium **Confidence:** Confirmed, intermittent
**Found in:** [2026-09-04_relevant_irrelevant_validation](../research/2026-09-04_relevant_irrelevant_validation/reports/RESEARCH_SUMMARY.md), query 5 ("exchange rates"); re-tested and NOT reproduced in [2026-09-04_full_system_validation](../research/2026-09-04_full_system_validation/reports/RESEARCH_SUMMARY.md), queries 8/15/20

**What happens:** The Sufficiency Auditor correctly produces a `RELEVANT`
status with empty `approved_parent_ids` and a good `generation_guidance`
(an honest "no information available" admission). The Response Generator
correctly drafts that admission. The outbound Groundedness Audit then
rejects it, reasoning some version of *"the assistant claims no
information, but this task appears in the approved evidence list, so it
should have used that evidence"* — treating the mere presence of a
guidance-only manifest entry as if it were a citable fact. After 3 failed
rounds, the turn falls back to a generic, unhelpful "I'm sorry, I'm not able
to answer that right now" instead of the correct, already-drafted answer.

**Why "intermittent," not systemic:** a second, larger study reproduced the
exact same shape (guidance-only `RELEVANT`, empty parents) three more times
and all three worked correctly — the audit accepted the honest admission
without issue. Something specific to the exchange-rate query's exact
resolved subquery or intent label likely triggers the false rejection,
not the guidance-only shape itself.

**Suggested fix direction:** tighten the outbound audit's rule for
`approved_by_task` entries with empty `approved_parents` so it cannot treat
an entry's mere presence in the manifest as grounds for demanding a
stronger claim than the evidence supports. Also worth comparing what
differed about the exchange-rate case specifically (e.g. its intent label)
against the three cases that worked correctly.

**Root cause, pinned down from the original session's raw trace
(`backend/logs/system.jsonl.1`, session `validation_5_f1489e88`):** across
two independent rejection rounds, the outbound groundedness audit gave
near-identical reasoning — *"the evidence entry for 'task_1' is present in
the 'approved_by_task' list, meaning the assistant has access to the
information or should have processed the task using the provided
evidence"* — despite `approved_parents` being `[]` in that same entry. The
audit was reasoning from the entry's mere existence (and its
`mapping_reason`/`resolved_subquery` match), never from the fact that its
`approved_parents` list was empty. The pre-existing "no info is info" rule
only constrained what the *response* could claim from an empty entry; it
never told the *auditor* that an empty-list entry's presence is not
grounds to reject or demand more from an honest admission — the exact
inverse failure mode from what the rule guarded against.

**Fix implemented:** added a paragraph to the Direct Factual Grounding
rule (`nodes.py`, outbound audit prompt, ~L7556) stating the restriction
runs in both directions: an entry's task_id, subquery match, or
mapping_reason are never themselves evidence that information exists, and
an empty `approved_parents` list is the complete final word for that task
— the audit must not conclude the assistant "has access to" or "should
have used" information for that task_id merely from the entry's presence.
**Verified:** re-ran the exact original repro ("I need to know your
exchange rates.") directly through `app_graph.ainvoke()`. `task_1`
resolved `RELEVANT` with `approved_parent_ids: []`, the drafted honest
admission passed the outbound audit on the first round (previously took 3
failing rounds then fell back to the generic non-answer), and the
customer-facing response was preserved. Not yet re-tested against the
three cases that previously passed without this fix (regression check) or
against repeated runs to confirm the "intermittent" rate has actually
dropped, since this is an LLM-judgment fix and elimination isn't
guaranteed by a single passing run.

---

## BUG-003 — Transient upstream 503 on the Tool-Calling Planner is treated as "nothing found," not retried

**Severity:** Medium **Confidence:** Confirmed **Status: Fixed, verified (mocked failure)**
**Found in:** [2026-09-04_relevant_irrelevant_validation](../research/2026-09-04_relevant_irrelevant_validation/reports/RESEARCH_SUMMARY.md), query 9 ("Can you remove my account please?")

**What happens:** A real Gemini `503 UNAVAILABLE` ("model experiencing high
demand") on the Tool-Calling Planner's own call gets treated identically to
"planner found nothing new" (`planner_found_nothing_new: true`) rather than
retried. No service_request/deeplink tool is ever attempted for that
subquery; the turn falls through to a generic non-answer.

**Precipitating event vs. the bug itself — worth keeping distinct:** the
503 is a real, external model/infrastructure failure (Gemini overloaded) —
not something our code or prompts caused, and not preventable by fixing
anything in this repo. That part is not the bug. The bug is entirely inside
our own deterministic error handling: it conflates "the model call itself
failed" with "the model responded and genuinely found nothing," when those
are different situations that should be handled differently. That's why
this is filed as a deterministic-code root cause, not an LLM-judgment one —
the model never got the chance to reason at all here; our code's response
to that failure is the defect, and it's fixable with a guaranteed-outcome
code patch, not a prompt change.

**Suggested fix direction:** distinguish a transient upstream failure
(5xx, timeout) from a genuine "no further capability found" result in the
Tool-Calling Planner's own error handling, and retry the planner call once
before treating the round as exhausted.

**Fix implemented:** `backend/app/core/llm_service.py` now has
`_is_transient_llm_error(error)`, which recognizes a `google.genai.errors.
ServerError` (or a message containing `UNAVAILABLE`/`high demand`/` 503`/
`Service Unavailable`) as transient. `call_llm_router` retries its dispatch
once (2s backoff) when `_is_transient_llm_error` is true and attempts
remain, before falling through to the original raise/log behavior — so a
transient 503 no longer gets silently conflated with "the model responded
and found nothing." **Verified:** a unit-level check confirmed
`_is_transient_llm_error` correctly classifies real `ServerError`/
`ClientError`/`ValueError` instances, and a mocked end-to-end test (a
client whose first call raises `ServerError`, second call succeeds)
confirmed the retry loop recovers and returns the successful result
instead of surfacing the 503 as an empty result.

---

## BUG-004 — Network-specific questions answered with network-agnostic evidence marked SUFFICIENT

**Severity:** Medium **Confidence:** Confirmed, 2 occurrences originally, reproduced live **Status: Fixed, live-verified (4 independent cases)**
**Found in:** [2026-09-04_full_system_validation](../research/2026-09-04_full_system_validation/reports/RESEARCH_SUMMARY.md), queries 2 and 23; re-confirmed live in production sessions on the exact same pattern

**What happens:** Query 2 ("closest Mastercard ATM") was marked `SUFFICIENT`
using `faq_768b41d9`, which only states overseas ATMs accept **Visa** —
Mastercard is never mentioned. Query 23 ("How do I get a visa card?") was
marked `SUFFICIENT` using three parents that describe the generic
application process with zero mention of card network at all. In both
cases, the customer's specific network detail was never actually confirmed
by the evidence, yet the response reads as though it fully answered the
question. A related, more severe live case surfaced during investigation:
a customer asking to raise their *international wire transfer* limit was
given *domestic* transfer-limit steps as if they applied, while a
disqualifying FAQ ("Horizon does not support international wire transfers
at all") sat unused in the very same retrieval candidate pool.

**Root-cause chain (three distinct, stacked gaps, found in order):**
1. The SUFFICIENT/RELEVANT boundary itself had no principle for "a named
   qualifier the evidence never confirms caps the task at RELEVANT" — it
   only had narrow, easy-to-miss carve-outs (e.g. an "explicit policy
   boundary is response-ready even without restating the question," which
   the model over-applied to justify skipping brand confirmation
   entirely).
2. Even after rewriting that boundary around one governing principle
   ("every named detail must be confirmed or necessarily settled, never
   silently unaddressed") and adding an explicit brand-attention rule, the
   model kept rationalizing past it in prose ("the customer asked for a
   'Visa card' generally, but the evidence covers the relevant product
   family") — a `node_03c` "Product-identity note" meant only to stop the
   auditor re-litigating *product* identity (e.g. "Instant Loan" content
   still counts as `Personal_Loan` evidence) was being over-extended by the
   model to also treat a customer-named *brand/network* as already
   settled, since it lives under the same settled product classification.
3. The eventual fix — a structured field the intent formulator populates
   and the sufficiency auditor is required to individually resolve — was
   first added to the prompt text only and silently did nothing: Gemini's
   controlled generation (`response_schema` in `call_llm_router`)
   constrains output strictly to whatever is declared on the corresponding
   Pydantic `BaseModel` class in `llm_service.py`; a prompt-only field,
   however clearly described with a matching worked example, is simply
   dropped from the model's actual output with no error. (See the
   `gemini-structured-output-schema-registration` memory note for the
   general lesson.)

**Fix implemented — a "Reason-to-Guidance Contract" between two nodes:**
- `SearchTask.verification_items: List[str]` (`llm_service.py`) — the
  intent formulator (`node_02e`) lists every specific, checkable claim the
  customer's own wording implies that PRODUCT/INTENT classification does
  not need to resolve but real evidence must confirm (a named brand/network,
  a premise/condition, a scope boundary, or any other specific detail) —
  phrased as short, self-contained sentences, deliberately general-purpose,
  not limited to card brands. `node_02e` cannot resolve these itself since
  no evidence exists yet at formulation time.
- `TaskSufficiencyItem.verification_results: List[VerificationResult]`
  (`llm_service.py`) — the sufficiency auditor (`node_03c`) is required to
  independently resolve every entry against the real retrieved evidence,
  citing the exact supporting `parent_id` when confirmed.
- **Deterministic code enforcement** (`node_03c`, right after `status` is
  parsed): if any `verification_items` entry is not marked `confirmed` in
  `verification_results`, the code force-downgrades `SUFFICIENT` to
  `RELEVANT` regardless of what the model's own `status` field says — the
  model can still mis-resolve one item, but it can no longer let that
  mistake silently produce a SUFFICIENT verdict.
- This mirrors the pre-existing `generation_guidance` handoff from
  `node_03c` to the response generator — both are narrow, structured,
  independently-checkable asks passed forward when an upstream node can
  reason about *what* needs checking but not *whether* it holds, never raw
  unaudited conclusions passed forward as authority.

**Verified live, across four structurally different cases, after the
Pydantic registration fix:**
- **Visa** ("How do I get a visa card?"): `verification_items: ["the card
  network is Visa"]` → `confirmed: true, supporting_parent_id: "faq_d5c931d4"`
  (a Visa Instalments FAQ that genuinely confirms the primary Horizon
  credit card runs on Visa) → correctly `SUFFICIENT`.
- **Mastercard** ("how do i get a mastercard"): `confirmed: false` →
  correctly capped at `RELEVANT`, with an honest admission and the general
  process offered as related information, never a bait-and-switch to Visa.
- **American Express** (a network never mentioned in any prompt example —
  a genuine generalization test): `confirmed: false` → correctly
  `IRRELEVANT` (the model's own judgment, not just the code floor), with
  guidance explicitly forbidding substituting a different network.
- **Cosmetic vs. physical card damage** (a non-brand premise-mismatch case:
  "my credit card is ugly, can I replace it?"): `verification_items:
  ["The replacement procedure covers cosmetic or appearance
  dissatisfaction, not just physical damage, loss, or theft."]` →
  `confirmed: false` → correctly `RELEVANT`, with a clean admission plus
  the standard replacement path offered as a courtesy — confirming the
  mechanism generalizes beyond card networks to any named premise/condition,
  not just brand names.
- Also confirmed across a real 3-turn live production exchange (Visa →
  Mastercard → a compound "so you only have Visa, right?" question) that
  the system correctly resolves multiple qualifiers independently within
  one task, and correctly refuses to confirm or deny an *exclusivity* claim
  the evidence doesn't address either way.

---

## BUG-005 — Evidence approved as SUFFICIENT can be topically mismatched to the actual request

**Severity:** Medium–High **Confidence:** Confirmed
**Found in:** [2026-09-04_full_system_validation](../research/2026-09-04_full_system_validation/reports/RESEARCH_SUMMARY.md), query 11 ("I'm not okay with this fee on my statement")

**What happens:** All three parents approved as `SUFFICIENT`
(`faq_9aed7ae8`, `faq_9ffc5766`, `faq_f7a7feed`) are explicitly about **ATM
cash-dispensal errors** ("cash amount dispersed is incorrect") — a
completely different topic from disputing a statement fee. The response
("contact Customer Care to dispute") happened to be generic enough not to
state anything false, but the evidence match itself is simply wrong, not
just imprecise. This is a retrieval/classification miss, not a
generation-time issue.

**Suggested fix direction:** worth checking whether this reflects a genuine
gap in Horizon's FAQ corpus (no real "disputing a statement fee" entry
exists, so RAG reached for the nearest dispute-shaped content) or a
retrieval ranking issue. If it's a corpus gap, the auditor arguably should
have returned `RELEVANT`/`IRRELEVANT` rather than `SUFFICIENT` for a
topically-unmatched parent.

---

## BUG-006 — Evidence Applicability Rule missed a real premise mismatch

**Severity:** Medium–High **Confidence:** Confirmed
**Found in:** [2026-09-04_full_system_validation](../research/2026-09-04_full_system_validation/reports/RESEARCH_SUMMARY.md), query 21 ("Why am I being charged twice for a restaurant?")

**What happens:** The parent approved as `SUFFICIENT` (`faq_d61a0a79`) has
its own stated premise: *"I did **not** make a transaction on my card, but
I got charged"* — an unauthorized-charge dispute. The customer clearly did
make the restaurant transaction; their complaint is a duplicate charge, a
different situation entirely. This is exactly the class of premise mismatch
the outbound audit's Evidence Applicability Rule (see `GROUNDEDNESS_v2.0.md`)
exists to catch — and in this live run, it did not catch it.

**Suggested fix direction:** worth a targeted, repeated test of this exact
query to determine whether this was a one-off audit miss or a real gap in
how the rule handles a "did/didn't make the transaction" premise
distinction specifically (as opposed to the situational premise mismatches,
e.g. damaged vs. cosmetic card issues, the rule has been validated against
before).

---

## BUG-007 — Stale `generation_guidance` from an earlier round can persist into a later round's successful verdict

**Severity:** Low (internal-only; no customer-facing effect observed)
**Confidence:** Confirmed **Status: Fixed, live-verified**
**Found in:** [2026-09-04_full_system_validation](../research/2026-09-04_full_system_validation/reports/RESEARCH_SUMMARY.md), query 14 ("How do I retrieve my passcode?")

**What happens:** The final, logged `generation_guidance` for a
`SUFFICIENT` result (with two real, correct approved parents) reads *"admit
there is no information available... do not provide instructions"* —
directly contradicting both the SUFFICIENT status and the real instructions
the customer actually received. The customer-facing answer was correct
(it used the real evidence and ignored the stale guidance), so this is an
internal data-hygiene issue, not a customer-facing bug — but it makes the
logged state actively misleading for anyone debugging from it later
(as this investigation itself demonstrates).

**Suggested fix direction:** when a task's status resolves to `SUFFICIENT`
with real `approved_parent_ids` in a later round, the carried-forward
`generation_guidance` from an earlier, less-successful round should likely
be cleared rather than retained, since "no info is info" guidance only
makes sense for a task that never found a citable parent.

**Fix implemented:** in `node_03c_sufficiency_auditor`'s per-round loop,
the `SUFFICIENT`-with-approved-ids branch now stores the raw, this-round
`generation_guidance` instead of `effective_guidance` (which could carry
forward stale text from an earlier round's `partial_guidance`/fallback).
**Verified:** re-ran the exact original repro query ("How do I retrieve my
passcode?") directly through the graph and confirmed the `SUFFICIENT`
result's logged `generation_guidance` is now empty (`""`) instead of the
prior contradictory "admit there is no information... do not provide
instructions" text, while the correct customer-facing answer (using the
real approved parents) was unaffected.

---

## SUSPECTED-001 — Triage may misclassify a genuine eligibility question as chitchat

**Severity:** Low **Confidence:** Suspected, not verified
**Found in:** [2026-09-04_relevant_irrelevant_validation](../research/2026-09-04_relevant_irrelevant_validation/reports/RESEARCH_SUMMARY.md), query 4 ("How old do i need to be to get an account for myself")

A genuine Deposit_Account-eligibility question was tagged `NON_FINANCIAL`
(chitchat) by Triage rather than treated as a financial request. May be a
real classification gap, or may be intentional (age/eligibility framed this
generically could be judged too under-specified to commit to a product).
Not investigated further; worth a dedicated look if Triage accuracy on
eligibility-shaped questions matters.

---

## SUSPECTED-002 — Possible query-intent misinterpretation for an ambiguous "source of funds" question

**Severity:** Low **Confidence:** Suspected, not verified
**Found in:** [2026-09-04_full_system_validation](../research/2026-09-04_full_system_validation/reports/RESEARCH_SUMMARY.md), query 22 ("Where are my funds being sourced from?")

This query was drawn from BANKING77's `verify_source_of_funds` category,
normally an AML-style "prove where your money came from" question, but the
system answered "how to fund your account" (FAST/PayNow methods) instead.
The query text alone is genuinely ambiguous enough that this can't be
called confirmed-wrong — flagged for awareness, not as a defect.

---

## BUG-008 — `NARROW_TO_EVIDENCE_SCOPE` recovery silently drops the unconfirmed claim instead of admitting it

**Severity:** Low–Medium **Confidence:** Confirmed **Status: Fix implemented, live-verified**
**Found in:** live production session `sess_d9102206-15a5-4343-8fba-55a538632797`, 2026-09-04 — a real (non-test) two-turn exchange: "我想调高国际电汇的每日限额" (raise my daily international-wire limit) followed by "这个限额调整是国际电汇业务的吗" (does this limit adjustment cover international wire transfers?)

**What happens:** The Sufficiency Auditor initially over-claimed that generic
transfer-limit evidence (`faq_5d09f5aa`, `faq_6aa6b8f2` — tagged
`Deposit_Account`/`Debit_Card`, no mention of international wire transfers)
confirmed coverage of international wire transfers specifically. The
outbound groundedness audit correctly caught this and fired
`NARROW_TO_EVIDENCE_SCOPE`, which worked exactly as designed compliance-wise
— but its own instruction only told the reviser to "restate only the
explicit evidence scope," with no requirement to name what was NOT
confirmed. The result: the revised response silently dropped the
international-wire claim and represented the same 4-step generic procedure
with no acknowledgment that the customer's actual question (does this cover
international wire transfers?) was never answered — reading as if the
generic steps were the direct answer.

**Root cause:** two prompt locations governing this recovery strategy
(`nodes.py`'s outbound-audit definition of `NARROW_TO_EVIDENCE_SCOPE`, and
`node_04c_response_reviser`'s own hard-coded handling of it) never invoked
the codebase's own existing `_GENERATION_GUIDANCE_SHAPE_RULE` case 2
("confirmed part + separately admit the specific unconfirmed remainder,
never merged into one sentence") — a pattern already used elsewhere for
guidance-only RELEVANT results, but never wired into this strategy. The
reviser's rule also blanket-banned rendering the confirmed remainder as a
list/stepper, even when the confirmed evidence genuinely was a procedure.

**Fix implemented:** updated the outbound audit's `NARROW_TO_EVIDENCE_SCOPE`
definition to require the violation's own `generation_guidance` follow
Shape Rule case 2 explicitly (admit the specific unconfirmed element in the
customer's own words, separately state the confirmed part), and updated the
reviser's handling of that strategy to follow the same shape instead of
collapsing to a bare, unacknowledged scope statement — while still allowing
the confirmed remainder to keep its natural UI form (e.g. a stepper) when
the evidence supports it.

**Verified:** replayed the exact two-turn sequence live. The follow-up
question now resolves with an explicit acknowledgment — *"至于这是否专门针对国际电汇业务，建议您在
App 内的限额设置中查看具体分类，以确认国际电汇是否包含在这些限额类别中"* ("as for whether this
specifically covers international wire transfers, please check the specific
category in the app's limit settings to confirm") — followed by the same
useful 4-step procedure, instead of the earlier mechanical, unacknowledged
restatement. Note: this particular re-test resolved cleanly on the
Sufficiency Auditor's first pass and never re-entered the reviser's
`NARROW_TO_EVIDENCE_SCOPE` branch specifically (that branch's edit is
therefore not independently re-confirmed under live conditions yet — the
original failure is stochastic and wasn't intentionally re-forced, given
this project's rate-limit constraints on live testing).

---

## BUG-009 — Outbound audit silently PASSes a turn when response generation returns zero blocks

**Severity:** Medium–High **Confidence:** Confirmed, 2 independent occurrences **Status: Fixed, verified (fixture test)**
**Found in:** live re-testing during a sustained Gemini `gemini-3.5-flash-lite` high-demand spike (2026-09-04) — session `pidnote_1204f45b` and a second independent repeat run, both on "How do I get a visa card?"

**What happens:** The response-generation LLM call itself fails (a `503`
that BUG-003's retry-once logic doesn't absorb, e.g. during a sustained
outage), so `_generate_response_blocks` catches the exception and produces
zero `response_blocks`. The outbound audit correctly detects this — *"The
assistant returned an empty response despite the presence of Task-Level
Approved Evidence... directly addresses the customer's request"* — and
flags a real `grounding_violations` entry with a correctly-attributed
`task_id`. But the turn is still treated as a full **PASS**, so the
customer receives a bare, unhelpful fallback (49 characters in one
occurrence, "Sorry, I don't have an answer for that right now" in the
other) instead of being routed to the reviser or a proper deterministic
fallback.

**Root cause:** `_partition_audited_response` (`nodes.py`) determines what
is "pending" by matching each violation's `task_id` against the
`sourceTaskIds` of the *existing* `response_blocks`. When `response_blocks`
is completely empty (nothing to iterate), this matching loop never runs,
so a correctly-flagged, non-empty-`task_id` violation matches nothing —
`pending_ids` comes back as an empty set, `response_wide` stays `False`,
and `node_06`'s own check (`if not pending_ids and not response_wide:
PASS`) reads that as full compliance. The function's own docstring already
states the intended invariant for an empty `task_id` ("a violation with an
empty task_id can't be attributed to any single block... treat the whole
round as response-wide") but never considered the symmetric case: a
non-empty `task_id` violation that also can't be attributed to any block,
because no block exists at all.

**Fix implemented:** `_partition_audited_response` now computes
`covered_ids` (every `task_id` named by any actual block's `sourceTaskIds`)
after the per-block loop, and adds `flagged - covered_ids` to `pending`
unconditionally — a flagged task_id with no corresponding block, whether
because `response_blocks` is completely empty or a block for that
particular task was simply never produced, can no longer silently drop out
of consideration.

**Verified:** a fixture-level test (no LLM call needed, since this is a
pure deterministic function) replayed the exact repro shape — empty
`response_blocks`, one `grounding_violations` entry correctly attributed to
`task_id: "task_1"` — and confirmed `pending` now correctly comes back as
`{"task_1"}` instead of an empty set, which will route the turn to the
reviser (`route_n21`) instead of a false PASS. Three regression checks also
passed: a normal per-block violation still resolves correctly, a genuinely
clean response with no violations still fully approves, and an empty-`task_id`
violation still correctly triggers the response-wide path.

---

## BUG-010 — "No Info Is Info" misapplied to RELEVANT let a zero-evidence Territory B task fabricate specific policy details

**Severity:** High **Confidence:** Confirmed **Status: Fixed, live-verified**
**Found in:** live production session (2026-09-04), "I'm not okay with this fee on my statement" — traced during ongoing BUG-005 investigation

**What happens:** A Territory B (adjacent-scope, scope-expansion) companion
task resolved `RELEVANT` with `approved_parent_ids: []` — no real evidence
at all — yet still carried a confident, specific-sounding
`generation_guidance`: *"Explain that fee waivers are discretionary and
evaluated based on factors like account tenure and loyalty status. Mention
that the bank's automated review process determines eligibility..."* None
of this appears in any evidence the task actually retrieved; its own
`evidence_delta` cited an unrelated Rewards/Linkpoints spend-threshold
fact. The response generator faithfully rendered this fabricated guidance,
which the outbound audit correctly rejected as unsupported across **three
consecutive rounds** ("inventing specific criteria for fee waivers...
not present in the provided evidence"), each revision only relocating the
fabrication rather than removing it, until the turn exhausted retries and
fell through to a mechanical deterministic fallback.

**Root cause — a genuine misunderstanding of the original design intent,
corrected mid-investigation:** the "No Info Is Info" mechanism (introduced
earlier the same session) was built to let a task with zero approved
parents still earn `RELEVANT` status when the sufficiency auditor had a
genuinely useful, hedge-worthy `generation_guidance` to offer. This was a
misapplication of a principle that was only ever meant to govern
`IRRELEVANT`: the original intent was that a genuinely-nothing-found
result should let the bot plainly *acknowledge* that nothing was found
("no info" is itself the information to convey) — never that a
guidance-only observation should be allowed to manufacture a `RELEVANT`
status with no citable parent behind it. Once wired into `RELEVANT`, the
mechanism let the model's own "I can write something plausible-sounding"
substitute for genuine, evidence-backed materiality — most dangerously for
a Territory B companion, whose own product/intent pairing is *already* a
speculative, evidence-free guess (constructed by `node_02e`'s own
scope-expansion planner before any retrieval happens for it); writing a
further hedge on top of an already-unverified guess compounds speculation
rather than grounding it. A contributing factor: Territory B construction
was previously *mandatory* ("construct exactly ONE new scope-expansion
companion task" for every failed task), giving the model no way to
correctly decline when no genuine connection existed — pressuring it
toward inventing one.

**Fix implemented, three parts, restoring the original intent:**
1. **"No Info Is Info" now applies to `IRRELEVANT` only.** A task with
   zero approved parents is always `IRRELEVANT`, unconditionally — no
   exception, regardless of what `generation_guidance` might say. This
   status already renders via a fixed, deterministic, never-LLM-generated
   "I don't have information available" statement, carrying zero
   fabrication risk.
2. **`RELEVANT` (and `SUFFICIENT`) now always require at least one real
   approved parent**, enforced in code (`node_03c_sufficiency_auditor`)
   independent of what status the model's own output claims, and mirrored
   in `TaskSufficiencyItem.generation_guidance`'s schema description
   (`llm_service.py`) and the outbound audit's evidence-manifest
   construction (a zero-parent entry can no longer appear in the manifest
   at all). This applies uniformly to the primary search, Territory A's
   widened retry, and Territory B alike — no per-territory carve-out.
3. **Territory B construction can now decline.** The prompt and
   `TerritoryBPlan.companions` schema description were changed from
   "exactly one companion per failed task" to "at most one... omit a
   failed task entirely when no genuinely different product/intent with a
   concrete, statable basis exists for it."

**Verified live**, in a real production turn exercising exactly this
mechanism after the fix: the same query's Territory B companion resolved
`RELEVANT` with a *real* directly-adopted approved parent
(`faq_2ba8c382`, "how late fees are applied to consolidated statements")
instead of fabricating anything, correctly framed as *"a related note"*
(Scope-Expansion Honesty Rule) rather than a direct answer. The outbound
audit passed cleanly on the first round with zero grounding violations —
no fabrication, no retry loop, no fallback.

**Side effect:** this redesign makes **BUG-002 structurally impossible**,
not merely fixed — the guidance-only `RELEVANT` (empty `approved_parents`)
shape it was about no longer exists anywhere in the system to reject.

## BUG-011 — A vague reply gets attributed to a stale dormant topic instead of the assistant's own most recent turn

**Severity:** Medium–High **Confidence:** Confirmed **Status: Fixed, live-verified**
**Found in:** live production session (2026-09-07), 3-turn trace: `**INIT**` → `"helo"` → `"i am not sure"`

**What happens:** Session INIT generated a dormant-topic followup carried
over from the user's last session: *"Would you like to continue discussing
the fee on your credit card statement?"* The user then said `"helo"`,
which triaged correctly as `NON_FINANCIAL` and got a generic reset reply:
*"Hello! How can I help you with your banking today?"* — deliberately not
re-raising the fee topic. The user then said `"i am not sure"` — plainly a
reply to that generic greeting (i.e. "I don't know what I need help with
yet"). Instead, triage (`node_02d`) classified it as *"I am not sure about
the fee on my credit card statement,"* reactivating the dormant turn-1
topic, and the turn launched a full RAG/retrieval pipeline that produced a
long, unrequested explanation of credit card fees and waiver procedures.

**Root cause:** `_build_conversation_context` (`nodes.py`) renders
`chat_history` as a flat, unranked `User: ... / AI: ...` transcript with no
marker distinguishing the assistant's most recent turn from an older one
sitting earlier in the same window. The triage prompt's only relevant
instruction — a short/vague reply "may continue the immediately relevant
prior request" — gives the model no way to choose between two candidate
prior requests when the assistant's *own* intervening turn was a
deliberate, topic-neutral reset: with a stale-but-specific topic and a
recent-but-generic one both present, the model favored specificity over
recency. This was a genuine missing-rule gap, not a violated existing
rule — the prompt was internally consistent, it just never anticipated
that the assistant's own conversational trajectory could invalidate an
older mention still visible in the same context window.

A second, adjacent gap was found in the same investigation (not the
proximate cause of this specific trace, since it happened to be empty
throughout, but a real latent risk for a longer conversation):
`active_product_intents` (the "Product/Intent Matrix") is reset only at
session INIT or recomputed inside `node_02e` when a turn actually reaches
retrieval — there is no branch that clears or decays it after a purely
conversational turn, so a genuinely stale matrix entry from several turns
back can outlive the topic it described indefinitely.

**Fix implemented, two parts:**
1. **`_build_conversation_context`** now labels the single most recent
   AI-authored entry in the trusted history distinctly — `"AI (most recent
   assistant turn, immediately before the Latest User Input): ..."` —
   instead of a plain `"AI: ..."` indistinguishable from any older one.
   This flows into every prompt that already calls this helper (`node_02d`
   triage and `node_02e` intent/query formulation both did already), with
   no prompt needing to change just to receive the new signal.
2. **Two explicit precedence rules added, both keyed off that new
   marker:** a "RECENCY ANCHOR" rule in `node_02d`'s triage prompt states
   that a vague reply defaults to continuing the marked most-recent
   assistant turn, not an older one, unless that most-recent turn was
   itself already continuing the older topic. A parallel addition to
   `node_02e`'s "CONTEXTUAL INHERITANCE" rule (Rule 1) makes the same
   check a precondition for inheriting from the Active Context Matrix:
   check whether the most-recent-marked assistant turn actually continued
   that matrix's topic before treating it as still live.

**Verified live**, re-running the exact same 3-turn sequence against the
fixed code (same user, same dormant-topic INIT followup): `"i am not
sure"` now stays on the conversational path (`n2d` → `n19` directly, no
retrieval nodes at all, matching turn 2's shape) and responds *"That is
completely fine, take your time figuring out what you might need help
with today"* — correctly answering the assistant's own greeting rather
than reactivating the dormant fee topic.

## BUG-012 — Response blocks with a bare-string list field crash on `_llm_text() missing 1 required positional argument: 'key'`

**Severity:** Medium **Confidence:** Confirmed **Status: Fixed**
**Found in:** BANKING77 full-coverage automated test campaign (2026-09-07 round 2; second occurrence 2026-09-08 round 6)

**What happens:** Any turn whose response generator produces a
`COMPARISON`-type block, or a `CAROUSEL`-type block whose item has a
`details` list, crashes the turn with `TypeError: _llm_text() missing 1
required positional argument: 'key'`.

**Root cause:** `_llm_text(source: dict, key: str, ...)` reads a named
field out of a dict-shaped element (`nodes.py:1036`) — every other call
site uses it that way (`_llm_text(r, "feature")`, `_llm_text(a, "label")`,
etc.). Three spots break this pattern by calling it on a list of *bare
strings* instead of dicts, with no `key` argument at all: `COMPARISON`'s
`headers` and each row's `values` (`nodes.py:6345, 6350`, found first),
and `CAROUSEL`'s per-item `details` list (`nodes.py:6428`, found second,
same copy-paste mistake in a different block). Each only surfaces the
moment a real response actually contains that specific block/field
combination, which a single-category-per-query BANKING77 corpus reaches
gradually across many hundreds of cases rather than immediately — the
second occurrence surfaced a full day of testing after the first fix,
in a block shape the first pass hadn't happened to exercise yet. A full
sweep for the same single-argument `_llm_text(x)` call pattern after the
second fix found no further occurrences.

**Fix:** all three spots now clean the bare strings directly instead of
routing them through the dict-keyed helper: `[str(h).strip() for h in
_llm_list(element, "headers") if isinstance(h, str) and str(h).strip()]`,
same pattern for `values` and `details`.

## BUG-013 — A structured-output response occasionally wrapped in a single-element list crashes with `'list' object has no attribute 'get'`

**Severity:** Low-Medium **Confidence:** Confirmed **Status: Fixed**
**Found in:** BANKING77 detail-backfill run (2026-09-09), 1 occurrence in 170+ cases

**What happens:** A structured-output call for `node_04b`'s response
generator returned its JSON wrapped in a single-element array --
`[{"elements": [...], "next_actions": [...]}]` instead of the expected
bare `{"elements": [...], "next_actions": [...]}` -- for a completely
ordinary query ("When should I expect to see my transfer hit my
account?") with no unusual prompt or schema involved. The generated
content itself was fine (a well-formed, on-topic answer); only the
wrapping was wrong. `parse_json_robustly` returned that list unchanged,
and the caller's very next line (`.get("elements")` or similar) crashed
with `'list' object has no attribute 'get'` -- an opaque error with no
hint of what actually happened.

**Root cause:** an occasional model-side quirk in how a single structured
object gets serialized for a given prompt/schema pair -- not something
tied to any particular node's prompt content, and not reproducible from
the prompt text alone (the same prompt shape works correctly the
overwhelming majority of the time). `parse_json_robustly`
(`llm_service.py:764`) is the single shared JSON-parsing helper nearly
every LLM-consuming call site in `nodes.py` routes through before calling
`.get(...)` on the result, so this could in principle surface from any of
them, not just response generation.

**Fix:** `parse_json_robustly` now unwraps the specific safe case -- a
parsed list containing exactly one dict -- returning that dict directly,
fixing every call site at once rather than patching each one. Any other
list shape (empty, multiple elements, non-dict elements) is left
untouched so a genuinely different problem still surfaces clearly rather
than being silently guessed at. Verified against the exact real response
that crashed (now returns the correct dict) plus both a normal
already-correct response and an ambiguous multi-element list (both
behave exactly as before, unaffected).

## BUG-014 — Precision Substitution occasionally leaves raw internal template fragments in customer-facing text

**Severity:** Medium **Confidence:** Confirmed **Status: Fixed -- malformed-placeholder repair fixture-verified; unauthorized placeholders left to the auditor by decision; prompt shape made consistent**
**Found in:** BANKING77 semantic Q&A sampling review (2026-09-09), 385-case stratified sample

**What happens:** `banking77_r3_0181` ("I did not make this card payment.")
produced a response containing: *"您还可以通过 Horizon App 中的实时聊天、请求语音通话，或者拨打我们的热线电话
+faq_bfe32bf8_001 faq_bfe32bf8_002-HORIZ (+faq_bfe32bf8_003
faq_bfe32bf8_004 faq_bfe32bf8_005)"* — raw internal template-id fragments
(`faq_bfe32bf8_001` through `_005`) appear directly in the customer-facing
text in place of an actual phone number.

**Why the existing safeguard didn't catch it:** every automated case in
this campaign is checked against `must_not_emit: ["unresolved_placeholder"]`,
which greps for the `{{faq_...}}`-wrapped pattern (`_framework/common.py`'s
`evaluate_assertions`, `token_map["unresolved_placeholder"] = ["{{faq_",
"{{data-"]`). This leak has no `{{` wrapper at all — it looks like Node
07's Precision Substitution step (`nodes.py`, "Executing Precision
Substitution") ran and partially replaced a templated phone-number
placeholder, but left behind fragments of the *id* it was substituting
from rather than either the resolved value or the original wrapped
placeholder. The automated check has a genuine blind spot for this
specific malformed-substitution shape; only manual semantic reading
caught it.

**Full-corpus scope check:** a regex sweep of all 3,080 final responses
for a bare (non-`{{`-wrapped) `faq_[8-hex-chars]_[3-digits]` fragment
found **6 occurrences (0.19%)**, confirming this is rare but not a
one-off.

**Not yet investigated:** which specific field/template produced
`faq_bfe32bf8_...`, why the substitution partially completed instead of
either fully resolving or leaving the recognizable `{{faq_...}}` form,
and the other 5 occurrences beyond this one sampled instance. Left for
future work per explicit instruction.

**Root cause found (2026-09-24).** All 6 corpus occurrences share one
mechanism: the text reaching node_07 held the placeholder id *without* its
`{{ }}` wrapper (e.g. `faq_5ad80c63_001-faq_5ad80c63_002`), and every
downstream check only recognizes the braced form -- node_07's substitution
(nothing to substitute; `precision_substituted: False` in all 6), and
node_05's gate, whose braced-placeholder check can't see it and whose
exposed-parent-id regex `(?![_}])` deliberately skips an `_NNN` suffix.
Ruled out: the DB values are correct (`faq_5ad80c63_001` -> 6), and no code
anywhere strips braces. So the braces were most likely dropped by the
generator itself -- inferred, not directly observed, since those runs
(2026-09-05..08) didn't capture raw generator output and the logs have
rotated. The *shape* is not generally unstable: the model kept `{{ }}`
intact in ~99.4% of placeholder-bearing turns (930 substituted vs 6
leaked). Failures concentrate on values stitched from several adjacent
tokens: the `001-002` card-arrival range leaked ~7% of the time (4/59),
the 5-token phone number ~1% (3/263). All 6 leaked ids were from parents in
that turn's own approved evidence.

**Fix implemented (evidence-scoped repair), design decided by the user:**
node_07 now normalizes *any* wrapping of a precision placeholder to exactly
`{{faq_..._NNN}}` -- zero, one, three, four braces, uneven (`{{x}`), or
whitespace inside the braces (`{{ x }}`); the brace count never matters --
but only when its **exact** id appears, well-formed, in this turn's approved
evidence (the same hydrated
parents the generator read; `_evidence_precision_placeholders` /
`_repair_precision_placeholders`). Nothing is guessed -- a truncated or
altered id is left untouched -- and an id outside the evidence is left
untouched too: that is an *unauthorized* placeholder, not a malformed one,
and deliberately not this function's job. Placed in node_07 rather than
node_05 because node_11's exhausted-retry fallback reaches node_07 without
passing node_05. Each repair emits `precision.placeholder.repaired`, so
the real-world rate is measurable from logs.

**Verified (fixture, real hydrated parents from `knowledge_base.db`, no
LLM):** both real leaks rebuilt from the corpus now render correctly
(`r1_0432` -> "6-7 business days"; `r3_0181` -> "+65 313-HORIZ (+65 3138
7878)"); single-braced in-evidence id repaired; out-of-evidence bare id and
an altered id both left untouched; the well-formed path and a no-evidence
state are unchanged. A second pass (19 shape checks) covers 0-4 braces, uneven
wrapping, inner whitespace, idempotence, and that a well-formed one is not
logged as a repair.

**Open gap found while fixing this -- unauthorized placeholders are not
rejected by any deterministic check today.** Probed node_05's actual
checks: (a) a bare/single-braced placeholder id *not* in the turn's
evidence passes node_05 and would reach the customer raw; (b) a well-formed
`{{faq_...}}` that exists in `Precision_Reference_Table` but is *not* in the
turn's approved evidence also passes -- node_05 only asks "does this exist
anywhere in the table", never "was this approved this turn", leaving it to
the (LLM) groundedness audit.

**Decision (user, 2026-09-24): leave unauthorized placeholders to the
auditor -- no new deterministic check.** An FAQ placeholder may only come
from a SUFFICIENT/RELEVANT parent of that same subquery; anything else is
unauthorized. But it is not worth triggering a dedicated regeneration for
it -- better to let the audit catch it alongside every other problem in
the same pass and regenerate once. Verified the audit already enforces
this: its Direct Factual Grounding rule lists "placeholder" among what
must be directly supported by Task-Level Approved Evidence, scopes a
mapped element to "the union of approved evidence for those tasks", and
receives each approved parent's content with its well-formed placeholders
to compare against. Residual risk accepted with this choice: detection is
LLM judgment, not guaranteed.

**Prompt made consistent on placeholder shape (2026-09-24, per user):**
the generator prompt gave a mixed brace signal -- PRECISION REFERENCE
CONTRACT shows `{{faq_*}}`, but a GENERAL DESIGN PRINCIPLES bullet
("Preserve exact {{placeholders}}." inside an f-string) reached the model
as single-braced `{placeholders}`. The audit prompt had the same defect for
personal data (`{data-*}`). Both now render the real shapes,
`{{faq_*}}` and `{{data-*}}`; verified by rendering both live prompts --
12 well-formed token mentions, zero single-brace ones.

## BUG-015 — A small number of turns return a generic failure fallback instead of a real answer

**Severity:** Low-Medium **Confidence:** Confirmed (2 occurrences in a 385-case sample) **Status: Open, not yet root-caused**
**Found in:** BANKING77 semantic Q&A sampling review (2026-09-09)

**What happens:** Two sampled cases returned a generic, non-contextual
failure message instead of an answer:
- `banking77_r4_0333` ("I have an unauthorized transaction on my
  statement"): *"We cannot process your request. Please try rephrasing or
  ask another question."*
- `banking77_r5_0493` ("...link the card with my account..."): *"I'm
  sorry, I'm not able to answer that right now. Could you try again, or
  ask in a different way?"*

Both are notable because near-identical questions in the *same*
categories, elsewhere in the same 385-case sample, received full, correct,
helpful answers (`banking77_r3_0408`, `banking77_r6_0145`, `banking77_r3_0181`,
`banking77_r1_0263` for the first category; `banking77_r6_0216`,
`banking77_r4_0212`, `banking77_r4_0105` for the second). This is a
different failure shape from the system's normal, appropriate "I don't
have information about X" honest admission (which names the specific gap)
— these two are generic, unspecific catch-all messages that don't
reference the question at all, suggesting an intermittent pipeline
failure (e.g. an uncaught exception hitting a generic error handler)
rather than a genuine knowledge-base gap.

**Full-corpus scope check:** an exact-text grep of all 3,080 final
responses for these two message strings found **27 occurrences (0.88%)**
— noticeably higher than the 2/385 (0.5%) sample rate suggested, so this
is worth prioritizing over BUG-014 when it's time to investigate. Against
the final, deduplicated per-case record (preferring whichever attempt has
full detail — see `research/2026-09-05_banking77_full_coverage/`), 24
unique case_ids carry this text; the remaining gap from 27 reflects cases
where an earlier attempt hit this fallback but a later resumed/backfilled
attempt (same case_id, genuine LLM non-determinism on retry) did not.

**Evaluation-metric impact, found and corrected 2026-09-13 while building
the agentic evaluation plan (`docs/AGENTIC_EVALUATION_PLAN_v1.md`):** of
the 24 case_ids, **22 carry a `task_results` status of
`SUFFICIENT`/`RELEVANT`** (or no `task_results` at all with no
decline/clarification recorded) — i.e. the sufficiency/retrieval layer
recorded a *successful* verdict, and this generic fallback text overwrote
the customer-facing response only afterward (consistent with the
mechanism note above: a later-stage failure, most likely repeated
outbound compliance/groundedness audit exhaustion producing
`non_financial.response.audit_fallback`, replacing an already-resolved
turn's text). This means **every automated, `task_results`-based metric
(Overall Resolution, Containment Rate, Bot Reliability, KB Coverage) was
silently counting these 22 cases as successes** — the customer received a
total failure, but the internal verdict said otherwise. This is now
corrected as a data-labeling adjustment (not a code fix) everywhere these
metrics are reported: see `docs/BUSINESS_METRICS_FRAMEWORK_v1.md` B1b for
the corrected figures (Overall Resolution 67.79%→67.08%; Bot Reliability
96.5%→95.9%; KB Coverage unchanged at 82.9%, since the underlying
evidence genuinely existed — only *delivery* failed, which is exactly
what Bot Reliability, not KB Coverage, is supposed to capture). The 26
tasks these 22 cases contribute are now tracked as a **confirmed** bot
failure, separate from BUG-016's 14.8%-*projected* bot-failure rate, to
avoid mixing a fully-known mechanism with a sampled estimate. Checked
against the multi-turn corpus too: only 1 occurrence there, and it was
already correctly caught by BUG-017's existing token-presence check (a
generic fallback obviously contains no `{{data-*}}` token), so no
equivalent adjustment is needed for that study.

**Not yet investigated:** which node/exception path produces this exact
generic message, whether it's the same underlying mechanism across all 24
cases or several coincidentally similar failures, and whether it
correlates with any particular category, round, or key/model in use at
the time. Left for future work per explicit instruction.

**Likely (not confirmed) partial improvement from the Response Obligation
Contract redesign, 2026-09-22/23 -- not re-measured at this bug's original
full-corpus scale.** This bug's leading hypothesis was "repeated outbound
compliance/groundedness audit exhaustion" replacing an already-resolved
turn's text with a generic fallback. The Stage 2 completeness net,
node_04c's migration to the same unified contract, node_06's own
conditional-rules restructuring, and the `generation_guidance` evidence-
boundary alignment are all direct interventions in exactly that
retry-exhaustion pathway. A live regression check this session replayed
two real historical BANKING77 drop cases and a case that had separately
exhausted all 3 retries down to a bare apology fallback; all three now
produce a complete, correctly-grounded answer on the first or second
attempt instead of falling through. This is suggestive, not conclusive --
it was 3 hand-picked cases, not a re-run of this bug's original
3,080-case corpus, so the 24-case/0.88% rate has not been re-measured and
this entry's status is left as-is pending that.

## BUG-016 — Sufficiency auditor doesn't recognize an explicit negative/declarative fact as a complete answer

**Severity:** Medium **Confidence:** Confirmed (13/88 stratified sample, 95% CI 7.7%–21.8%) **Status: Open, not yet root-caused**
**Found in:** BANKING77 retrieval-vs-judgment diagnosis (2026-09-10)

**What happens:** For a meaningful share of unresolved BANKING77 tasks,
the correct answer was already present, word-for-word, in the evidence
shown to `node_03c_sufficiency_auditor` — but the auditor still declined
because the candidate wasn't a literal, positively-framed match to the
question's exact phrasing. The single largest repeat pattern (5 of 13
sampled cases, all in the `exchange_via_app` category): a query like
*"How do I change GBP to AUD?"* or *"Can currency exchange be done on
this app?"*, with a candidate FAQ stating *"Horizon Bank currently
supports only domestic transfers denominated in SGD. We do not handle
inbound or outbound foreign‑currency transfers, nor any cross‑border
payments."* That sentence **is** the answer — "no, this app doesn't do
that" — but the auditor's reasoning treated it as "doesn't address the
question" rather than a complete, resolvable admission. Three smaller
repeat clusters showed the same shape: an *"Apple Watch to top up?"*
query against *"the Horizon App can only be used on a smartphone"* (2
cases, not connected to "so no, a Watch won't work"); a *"strange direct
debit" / "unauthorized fee"* query against a generic *"I don't recognize
this charge — contact us to investigate"* FAQ (2 cases, not applied to an
equivalent complaint); and a *"why hasn't my transfer shown up"* query
against a transfer-security-hold FAQ (*"funds held for a few hours as a
fraud precaution"*, 2 cases). The remaining 2 sampled cases were one-offs
of the same shape (an explicit "no foreign transaction fee" statement not
recognized as answering "what's your exchange fee," and a "no fee for
SGD transfers into savings" FAQ not matched to its near-identically named
category "top-up by bank transfer charge").

**How this was found and measured:** a category-based ground truth (the
FAQ parent IDs that *other* cases in the same BANKING77 category actually
resolved with) was used to check every unresolved task for whether its
correct-for-category answer had ever been shown to any LLM call in the
case (via `task.rag.completed`'s `selected_parent_ids` and a regex sweep
of every `llm.request`'s `raw_prompt` for `faq_` IDs, since the former
event isn't emitted on every code path). Of 973 unresolved tasks
corpus-wide, 950 (97.6%) had their correct-for-category evidence shown as
a candidate at some point — meaning retrieval itself is not the
bottleneck; the vast majority of unresolved outcomes are a sufficiency-
audit judgment call, not a retrieval miss (the remaining 23 genuine
misses, where the right document was never surfaced at all, are a
separate, much smaller phenomenon, and on inspection mostly reflect real
KB content gaps rather than retrieval failures — see the BANKING77
RESEARCH_SUMMARY.md for detail).

A random sample of 88 of the 950 "retrieved but rejected" tasks (95% CI,
±10% target margin, single-task cases only for unambiguous candidate
attribution) was then manually read — query, auditor's stated reason, and
the full text of every shown candidate — to judge whether the rejection
was correct (candidate genuinely doesn't address the query) or wrong
(candidate does, in substance, answer it). 13/88 (14.8%, 95% CI
7.7%–21.8%) were judged wrong. Scaled to the full "retrieved but
rejected" population, that's roughly 140 tasks (~4.5% of the entire
3,080-case corpus, 95% CI ~2.4%–6.7%) where the sufficiency auditor had
what it needed and still declined.

**Not yet investigated:** the exact prompt wording/rule in
`node_03c_sufficiency_auditor` responsible for this bias, whether a
targeted rule addition ("an explicit statement that a feature/fee/service
does not exist or does not apply is itself a complete, citable answer to
a query asking about that feature/fee/service") would close most of this
gap without over-triggering on genuinely unrelated evidence, and whether
the same bias appears in non-BANKING77 traffic. Left for future work per
explicit instruction. Scope note: the 88-case sample was drawn only from
single-task cases (481 of the 950); multi-task cases were excluded for
attribution cleanliness and were not separately checked.

**Re-measure and decision (2026-09-25):** in `research/2026-09-25_model_judgment_remeasure` (Gemini, current code) 7 of 9
`exchange_via_app` cases improved -- e.g. "How do I make an exchange from one currency to
another?" is now SUFFICIENT from `faq_32c1662f` ("supports only domestic transfers in SGD... no
foreign-currency transfers") -- but in 2 cases the auditor saw that FAQ and still returned
IRRELEVANT ("none list the available currencies"). Analysis: the information was present;
the SUFFICIENT definition already allows a "no" ("need not be a literal yes/no; an explicit
policy boundary... is a fully adequate SUFFICIENT answer"), but is written as a per-detail
check ("every specific detail... confirmed... or NECESSARILY settled... no further assumption
or guess"), and the FAQ says "no foreign-currency transfers", not literally "no currency
exchange", so the step from one to the other is the borderline inference the definition
discourages. Three prompt changes were replayed on the logged audit prompts (48 targets,
risk controls and normal cases, plus 8 historical BUG-004 network/limit prompts): a
"ruling out settles dependent details" clause (targets 4/12 SUFFICIENT; DeepSeek dropped
adjacent courtesy parents on a duplicate-charge case), a one-line simple definition (no
target gain, looser SUFFICIENT on partial requests), and the simple definition plus a
named-detail principle (targets 2/6, BUG-004/006 safe, but some partial "contact us"
answers became IRRELEVANT). None was clearly better than the current definition. **Decision:
keep the current definition; the remaining misses are model judgment on a borderline
inference caused by an FAQ gap (no FAQ states plainly that currency exchange is not offered).**

## BUG-017 — A successfully fetched account balance is silently dropped when the turn also carries conversational framing

**Severity:** Medium **Confidence:** Confirmed (61/619 measured across a full 249-conversation campaign, not a sample) **Status: Fixed (2026-09-24), live-verified 4/4 on the bug's own trigger phrasings**
**Found in:** Multi-turn SGD Banks_1/Banks_2 depth test (2026-09-10/11), full 249-conversation run

**What happens:** The `account-balance` `api` field (added this campaign — see
`research/2026-09-10_multiturn_banking_extension/`) is a `disclosure_mode:
"placeholder"` field: the tool dispatcher correctly creates a subquery,
correctly calls the field's mock fetch function, and correctly populates
`personal_data_reveal_map`/`personal_data_reveals` with a fresh `{{data-*}}`
token and its real value. In 61 of 619 (9.9%) successful fetches across the
campaign, that token never appeared anywhere in the turn's `final_response`
at all -- the reply addressed only whatever conversational framing
accompanied the question (gratitude, a greeting, small talk) and simply
never mentioned the balance, even though it was already fetched and ready.
E.g. `mtsgd_32_00011` turn 7: *"Thanks, what's my balance?"* -> *"You are
very welcome! 😊"* (no balance mentioned at all). Other trigger phrasings:
"I appreciate your help and want to see about my balance.", "Fantastic. Can
you please tell me the balance of my savings account?", "Hello. I need to
check my balance." -> "👋 Hello!". The equivalent `deeplink`
(money-transfer) path had zero comparable failures (829/829 clean), so this
is specific to the placeholder-value surfacing step in response generation,
not a general response-generation problem. This is a distinct defect from
BUG-018 (below): here the answer is missing, not wrong.

**Full-corpus scope check:** this campaign's own full run, not a sample --
61/619 (9.9%) of every successful `account-balance` fetch in the study.

**Conversation-level confirmation:** a separate 95%-CI random sample of 70
whole conversations (out of 249, +/-10% margin -- the same method as the
BANKING77 semantic review, adapted to the conversation as the review unit
since this study's purpose is multi-turn coherence, not per-turn
correctness) found this exact pattern in **23/70 conversations (32.9%,
95% CI ~23%-43%)** -- a single ~10%-per-turn failure rate compounds to hit
roughly one in three whole conversations at least once across their ~8
turns. In most of those 23, the drop is never recovered for the rest of
the conversation -- the customer simply never receives that balance at
all, not merely on a delay.

**Not yet investigated:** which prompt/step in response generation drops
the `{{data-*}}` token when the turn also carries conversational content
(gratitude, greetings, small talk) or a compound sentence structure, and
whether the same generation-vs-conversational-framing conflict exists for
the pre-existing `profile-address` placeholder field (not exercised by this
study) or is specific to `account-balance`. Left for future work per
explicit instruction.

**Re-verification (2026-09-23):** the personal-data completeness net shipped
on 2026-09-22 (`0732485`), after this bug was found, did NOT fix it: on the
bug's own four trigger phrasings, "Thanks, what's my balance?" -> "You're
very welcome!" and "I appreciate your help and want to see about my
balance." -> "Thank you so much for your kind words." still dropped the
fetched balance (`research/2026-09-23_bug017_reverify`).

**Root cause (2026-09-24):** a routing condition, not generation quality.
`_generate_response_blocks` sends a turn to the narrow
`_generate_evidence_only_response` sub-prompt when it judges there is no
substantive content besides clarifications/declines/no-info/conversational
replies. That judgment was a hand-maintained list --
`evidence_by_task or available_shortcuts or unavailable_service_requests or
verified_api_context` -- which omitted `personal_data_placeholders`. So a
turn whose only substantive content was a fetched balance, plus a
conversational part (triage splits "Thanks" off as NON_FINANCIAL), was
routed to the narrow sub-prompt, which never receives the balance token and
does not run the completeness net: the model could only answer the thanks.
This matches the bug's exact trigger ("when the turn also carries
conversational framing") and explains why "Fantastic. Can you please tell me
the balance of my savings account?" worked (no conversational part split
off, so the narrow path was never eligible).

**Fix (user chose option C: derive from the obligation list):**
`has_real_evidence` is now "any obligation whose `generation_rule` is not one
the narrow sub-prompt handles" (`_EVIDENCE_ONLY_GENERATION_RULES` =
clarification, decline/no-info, conversational), computed from the same
`_build_response_obligations` list the generator uses -- so personal data
counts, and any obligation kind added later is counted automatically
instead of being forgotten the way this one was. Also, per the user, the
completeness net's personal-data fallback sentence is now customer-facing:
the registry's third-person "The customer's current account balance." used
to render as "The customer's current account balance: {{data-x}}."; it now
renders "Your current account balance is {{data-x}}." (any description not
in the registry's "The customer's <field>" shape keeps the safe colon
form; an empty token is skipped).

**Verified:** 12 fixture checks (no LLM): thanks + fetched balance now
routes to the full generator; thanks-only and clarification-only still take
the narrow path; RAG + thanks and balance-only stay on the full generator;
the fallback sentence for all three registry fields, an unexpected
description, and an empty token; and end-to-end, a model that writes only
"You're very welcome!" gets the balance added by the net as "Your current
account balance is {{data-...}}.". Live (`research/2026-09-24_bug017_fix_
live_check`), the two phrasings that failed now answer on the first attempt
with the full generator, audit passing, no net needed: "You are very
welcome. Your current account balance is {{data-...}}." and "I appreciate
your help as well. Your current account balance is {{data-...}}.". The
other two phrasings (re-run after a Gemini 503 outage cleared) also answer
on the first attempt: "Hello. I need to check my balance." -> "Your current
account balance is {{data-...}}." (it previously needed the net, in the
old third-person wording) and "Fantastic. Can you please tell me the
balance of my savings account?" -> "Your savings account balance is
{{data-...}}." -- 4/4 on the full generator, no retries, no net repair, no
fallback.

## BUG-018 — Response generation falsely denies having a balance it just successfully fetched

**Severity:** High **Confidence:** Confirmed (13/619 measured across a full 249-conversation campaign, not a sample) **Status: Root-caused and fixed, fixture-verified (2026-09-23) -- not yet re-measured at this bug's original full-campaign scale**
**Found in:** Multi-turn SGD Banks_1/Banks_2 depth test (2026-09-10/11), full 249-conversation run

**What happens:** In 13 of 619 (2.1%) successful `account-balance` fetches,
the generated response doesn't just omit the fetched value (see BUG-017) --
it actively contradicts the system's own successful fetch by claiming the
capability doesn't exist. E.g. `mtsgd_32_00089`: *"I need to know about my
savings account. Would you please check my current balance?"* -> *"While
live account balances cannot be checked directly, you can review your
account history and financial standing by accessing your monthly
statements..."*, followed by app-navigation instructions -- the system had
just successfully fetched the real balance in that same turn
(`personal_data_reveal_map` populated, tool status `completed`), and the
generated text then falsely denies having that capability at all,
redirecting the customer to an unrelated workaround instead.
`mtsgd_32_00014` shows the same shape: a fetched-balance turn answered with
unrelated content about domestic transfer timing and security holds. This
is materially worse than BUG-017's silent omission -- it's a false
statement about the system's own capability made directly to the customer,
meeting this tracker's own bar for High severity ("a materially wrong
claim"), not just a missing answer.

**Full-corpus scope check:** this campaign's own full run, not a sample --
13/619 (2.1%) of every successful `account-balance` fetch in the study.

**Not yet investigated (at the time):** why response generation, given a populated
`personal_data_placeholders` entry for this exact field, sometimes
constructs a sentence actively denying that capability rather than either
using the value or (per BUG-017) merely omitting it; whether this
correlates with any particular phrasing of the customer's question (both
examples found combine the balance ask with an unrelated secondary clause
or context); and whether the outbound compliance/groundedness audit should
already be catching an unsupported capability-denial claim like this
(`node_06`) but isn't. Left for future work per explicit instruction.

**Root cause found (2026-09-23), traced end to end from the original
capture log (`research/2026-09-10_multiturn_banking_extension/data/
capture_log.jsonl`, case `mtsgd_32_00089`, turn 1):**
1. Round 1's generator answered the balance question using an
   adjacent-scope RAG task (`task_1_scope`, RELEVANT, "how to find monthly
   statements") presented as a direct answer -- a real Scope-Expansion
   Honesty Rule violation, since the turn's own `personal_data_placeholders`
   already had the real, fetched balance token ready.
2. The outbound audit correctly caught the framing violation, but its own
   `groundedness` reasoning made a separate, uncaught mistake: *"The
   permitted evidence contains no direct live balance checking capability
   or data for account balances"* -- false. The audit's own evidence
   bundle already included the personal-data placeholder for the exact
   same balance; its reasoning checked only Task-Level Approved Evidence
   and never cross-referenced the placeholder before concluding no balance
   data existed at all.
3. That wrong conclusion became an authoritative instruction: the
   violation's own `NARROW_TO_EVIDENCE_SCOPE` `generation_guidance` read
   *"Admit that live account balances cannot be checked directly..."* --
   and the reviser, correctly and as designed, trusted it completely and
   wrote exactly that denial, with the real balance token still sitting
   unused in the very same prompt.

This is the mirror image of the `generation_guidance` fabrication issue
found and fixed the same day (see `_GENERATION_GUIDANCE_SHAPE_RULE`'s
alignment with the generator's own evidence boundary): there, a
`generation_guidance` author asserted an unsupported *positive* claim;
here, one asserted an incorrect *negative* claim by reasoning from only
one evidence source (Task-Level Approved Evidence) while a different,
already-available source (`personal_data_placeholders`) directly
contradicted it. `mtsgd_32_00014` (this bug's second example) shows a
related but distinct shape: its round-1 draft also denied the capability
(and separately mis-disclosed the value in prose), the audit correctly
flagged both, but the actual fix over-corrected into a silent omission
(BUG-017's shape) rather than a persisted denial -- consistent with the
two bugs sharing a common upstream trigger (the generator struggling when
a personal-data placeholder coexists with other content in the same
compound turn), with the audit/reviser interaction determining whether
the customer sees a gap or a false claim.

**Fix implemented, upstream of both the audit and the generator:**
`_response_evidence_manifest` (`nodes.py`, the single shared evidence
contract for response generation and normal-response groundedness) now
excludes a RAG task's evidence entirely when its own status is RELEVANT
(not SUFFICIENT) *and* its owning subquery already has a completed
non-RAG tool attempt (`api`/`deeplink`/`service_request` -- ground truth,
not a judgment call) -- computed via each `search_task`'s own
`subquery_id`, joined against `financial_subqueries`' own `tool_attempts`.
A SUFFICIENT verdict is exempt and still shown as genuinely additive
information, since it's a complete, standalone answer rather than a hedge
that only made sense when nothing else had already answered the
subquery. This is a proactive fix at the data-flattening layer, not a
new rule for the audit or generator to follow: the contradictory RELEVANT
evidence is never present in `generation_content` for either of them to
reason about in the first place, closing this specific class of
audit-reasoning gap without asking the audit to somehow check every
evidence source exhaustively every time.

**Verified (fixture-level, no LLM call needed since this is a pure
deterministic function):** replayed the exact `mtsgd_32_00089` shape
(RELEVANT adjacent-scope task + a completed `api` attempt on the same
subquery) and confirmed the evidence entry is now excluded from the
manifest entirely. Regression-checked: a SUFFICIENT task in the identical
shape is kept; a RELEVANT task with no other tool involved is kept
(BUG-018's fix must not suppress a normal honest-admission case); a
RELEVANT task whose other-tool success belongs to a *different* subquery
is kept; a RELEVANT task whose same-subquery tool attempt exists but
never completed (`unavailable`) is kept, since only ground truth should
suppress a hedge, not merely an attempt; and a state missing the
`financial_subqueries` key entirely (older-shape state) does not crash
and suppresses nothing. Not yet re-measured live against a fresh run of
this bug's original 249-conversation campaign.

## BUG-019 — A short account-type follow-up gets reattached to an already-resolved transfer instead of the actual current question

**Severity:** Medium **Confidence:** Confirmed (3/70 conversations in the semantic review sample) **Status: Open, not yet root-caused**
**Found in:** Multi-turn SGD Banks_1/Banks_2 depth test, 70-conversation semantic review (2026-09-11)

**What happens:** After a transfer has already been offered (Send Money
button shown) and the conversation has since moved on to a new balance
question, a short follow-up meant to specify *which account* for that
balance question -- "my savings account", "go with my savings account",
"savings account, please" -- sometimes gets reattached to the earlier,
already-resolved transfer topic instead of the actual most recent
question. E.g. `mtsgd_40_00043`: turn 4 *"Thanks a lot. What's my balance
now?"* is dropped (BUG-017), then turn 5 *"My savings account, please."* --
clearly meant to answer the just-asked balance question -- gets *"You can
transfer funds to your savings account by using the Send Money button
below."* The customer has to re-ask the balance question explicitly a
third time before it resolves. `mtsgd_40_00021` shows the identical shape.
This is inconsistent, not deterministic: near-identical short follow-ups
in other sampled conversations (`mtsgd_39_00127`, `mtsgd_40_00078`)
correctly attach to the balance question instead.

**Full-corpus scope check:** not yet run -- found via the 70-conversation
semantic review sample only (see BUG-017 for that sample's method).

**Not yet investigated:** whether this is a triage/topic-tracking recency
bug (attaching to the topic with more total turns spent on it rather than
the most recently active one) or something specific to how a bare
account-type phrase gets resolved when two candidate topics are both
still "open"; whether it correlates with the same turns where BUG-017 also
fired (both sampled instances follow a BUG-017 drop, but the mechanism
need not be related). Left for future work per explicit instruction.

## BUG-020 — Bot fabricates a second "other" savings account rather than correcting a customer's false premise

**Severity:** Medium **Confidence:** Confirmed (2/70 conversations in the semantic review sample) **Status: Accepted for MVP (2026-09-25) -- multiple accounts out of scope**
**Found in:** Multi-turn SGD Banks_1/Banks_2 depth test, 70-conversation semantic review (2026-09-11)

**What happens:** The mock `account-balance` field only models a single
account (see this study's design decision to use one fixed balance value,
not split by account type). When a customer's phrasing implies a *second*,
distinct savings account exists -- "Can you check my balance of another
savings account?" (`mtsgd_32_00012`), "please also check the balance of my
another savings account" (`mtsgd_40_00042`) -- the response plays along
with the false premise rather than correcting it: *"Your **other** savings
account balance is {{data-...}}."*, fetching and presenting the exact same
single mock value under an invented "other account" label, instead of
either clarifying that only one savings account exists or simply restating
the same account's balance without inventing a second one.

**Full-corpus scope check:** not yet run -- found via the 70-conversation
semantic review sample only (see BUG-017 for that sample's method).

**Not yet investigated:** whether this is specific to the phrase "another
savings account" or would trigger on any implied second account/entity;
whether the same play-along-with-a-false-premise behavior appears for
other single-valued mock fields (e.g. `loyalty-status`, `profile-address`)
if a customer implies multiplicity there too. Left for future work per
explicit instruction.

**Re-measure (2026-09-25, Gemini, current code):** still reproduces in 1 of 2 conversations
(`mtsgd_40_00042`: "Your other savings account balance is..."; `mtsgd_32_00012` corrected the
customer from the "only one Savings Account" FAQ). Cause: the balance lookup has no notion of
multiple accounts (a simple demo API), the one-account FAQ was not retrieved that time, and
the generator's PERSONAL DATA RULES present `for_request` -- the customer's own words -- as
"exactly which request its data was fetched for", so the token is labelled with the
customer's false premise. **Change (user's choice):** a shared `HORIZON_BUSINESS_FACTS`
(split out of the product briefing) now states that Horizon offers each customer only one
savings account, and is also given to the generator and reviser as background ("never
contradict them... not approved evidence"). **Result (live, Gemini):** `mtsgd_40_00042` now
says it cannot check another savings account and gives the one balance; `mtsgd_32_00012`
still said "other savings account" with the fact present in its prompt -- the specific
`for_request` wording in the PERSONAL DATA RULES wins over the general fact. **Decision:
accepted for the MVP** -- the demo balance API has no notion of multiple accounts, and
multiple-account handling is out of scope; the rule wording is left as is.

## BUG-021 — Inconsistent handling of "how long will the transfer take"

**Severity:** Low-Medium **Confidence:** Confirmed (3/70 conversations in the semantic review sample) **Status: Open, not yet root-caused**
**Found in:** Multi-turn SGD Banks_1/Banks_2 depth test, 70-conversation semantic review (2026-09-11)

**What happens:** After the Send Money deeplink is offered, several
sampled conversations follow up with some form of "how long will it
take?"/"how many days?". In most sampled instances this is answered
correctly and specifically, citing real Horizon policy (instant domestic
processing, plus a security-hold/cooling-off period, sometimes correctly
noting a new payee needs to clear a waiting period first -- e.g.
`mtsgd_5_00001`, `mtsgd_5_00005`, `mtsgd_5_00002`, `mtsgd_5_00012`). But in
3 sampled conversations the identical question shape gets a generic
non-answer instead: `mtsgd_4_00108` -- *"Sorry, I don't have a complete
answer for the part about How long will a transfer to Diego's savings
account take? right now."*; `mtsgd_4_00114` and `mtsgd_4_00126` show the
same generic decline, each just substituting the recipient's name into an
otherwise-identical templated non-answer. The same real FAQ content that
answers the question correctly elsewhere in the sample is evidently
available -- this isn't a case of it never existing, since near-identical
turns in other conversations use it successfully.

**Full-corpus scope check:** not yet run -- found via the 70-conversation
semantic review sample only (see BUG-017 for that sample's method).

**Not yet investigated:** whether the failing cases share something in
common in how the transfer-timing subquery gets formulated or routed
(retrieval miss vs. a sufficiency-auditor judgment call, in the spirit of
BUG-016); whether this correlates with which recipient name or amount was
used, given the templated-looking non-answer; whether this is the same
underlying mechanism as BUG-015's generic fallback from the BANKING77
campaign or a distinct one that merely produces superficially similar
text. Left for future work per explicit instruction.

## BUG-022 — A balance follow-up answered with a generic product-info dump instead of the number

**Severity:** Medium **Confidence:** Confirmed (1/70 conversations in the semantic review sample) **Status: Open, not yet root-caused**
**Found in:** Multi-turn SGD Banks_1/Banks_2 depth test, 70-conversation semantic review (2026-09-11)

**What happens:** `mtsgd_4_00114`: turn 1 correctly answers a balance
question. Turn 2, *"Ok, what about my savings account?"* -- in context, an
obvious follow-up asking for the same balance figure again -- instead
produces a long, generic marketing-style paragraph describing the Horizon
savings account's features, interest rates, fee structure, debit card
benefits, and cash-access locations, and never states a balance number at
all. This is a different failure shape from BUG-017 (which at least
recognizes a balance-adjacent turn exists and either surfaces or drops the
fetched figure) -- here the turn appears to have been resolved as a
general "tell me about the savings account" product question rather than
a balance follow-up, despite the immediately preceding turn establishing
exactly that context.

**Full-corpus scope check:** not yet run -- a single occurrence in the
70-conversation semantic review sample; scope and reproducibility are
unknown pending a full-corpus check.

**Not yet investigated:** whether this is a one-off intent-classification
error or a reproducible pattern for this specific phrasing ("what about
my X" as an ellided follow-up); whether it correlates with the same
conversation also containing BUG-021's failure mode (`mtsgd_4_00114`
exhibits both). Left for future work per explicit instruction.

## BUG-023 — Outcome depends on surface phrasing, not underlying intent

**Severity:** Medium **Confidence:** Confirmed (full-corpus; `declined_card_payment` spot-checked in detail) **Status: Accepted as a known limitation (2026-09-25) -- data gap plus model judgment**
**Found in:** BANKING77 full-coverage study, Fairness/consistency pass (2026-09-13) — reframed per explicit instruction: since the bot is stateless and not customer-segment-specific, the correct fairness test is not demographic variation but whether the *same underlying intent, expressed in different wording,* produces the *same outcome*.

**What happens:** For each of the 77 BANKING77 categories (40 differently
worded queries per category, all expressing the same nominal intent), an
"outcome concentration" score was computed:
`max(resolved_count, partial_count, unresolved_count) / 40`. A category
where the bot handled the intent consistently should cluster tightly in
one outcome; a low score means the 40 phrasings split across resolved/
partial/unresolved almost evenly, i.e. wording alone is changing the
result. Mean concentration across all 77 categories is 72.6%, but **24 of
77 categories fall below 60%** — the worst is `declined_card_payment` at
35.0% (13 resolved / 13 partial / 14 unresolved, almost an even
three-way split). Reading the actual query text in that category shows
this is not noise: near-identical questions receive opposite outcomes
purely from surface phrasing —
*"Why was my Payment declined"* → **resolved**, but
*"Please explain why my card was declined?"* → **unresolved**;
*"My card payment did not work."* → **resolved**, but
*"My card was declined in a shop"* → **unresolved**;
*"I tried using my card and it kept getting declined, Why?"* → **resolved**,
but *"Why was a transaction on my card declined?"* → **unresolved**.
Other low-concentration categories: `pending_cash_withdrawal` (35.0%),
`apple_pay_or_google_pay` (40.0%), `top_up_failed` (42.5%),
`extra_charge_on_statement` (45.0%), `get_disposable_virtual_card`
(45.0%), `automatic_top_up` (47.5%), `card_payment_wrong_exchange_rate`
(47.5%), `fiat_currency_support` (47.5%), `verify_top_up` (47.5%),
`balance_not_updated_after_cheque_or_cash_deposit` (50.0%),
`verify_source_of_funds` (50.0%). By contrast, the most consistent
categories (97.5–100% concentration) are `edit_personal_details`,
`passcode_forgotten`, `atm_support`, `card_arrival`,
`cash_withdrawal_not_recognised`, `compromised_card`,
`lost_or_stolen_card`, `lost_or_stolen_phone` — all narrow, single-shape
intents with little room for a rephrasing to land differently.

**Full-corpus scope check:** the underlying concentration score is
computed across the full 3,080-case BANKING77 corpus (all 77 categories,
40 cases each) — not a sample. Only the illustrative query-pair evidence
above (`declined_card_payment`) was manually spot-checked; the other 23
low-concentration categories have the same score computed but have not
each been individually read through.

**Root-cause trace, one confirmed pair (2026-09-13).** Traced
`banking77_r2_0060` ("Please explain why my card was declined?",
unresolved) against `banking77_r1_0077` ("Why was my Payment declined",
resolved) end to end through the pipeline's own logged reasoning:

- `banking77_r2_0060` ("card"): the intent formulator mapped it to
  **Credit_Card / Transaction Disputes** — the correct product. But the
  only FAQs the KB has under that bucket concern **card *applications*
  being declined** (income, credit exposure, missing documents) and
  credit-limit reviews/closures — nothing about an already-issued card
  being rejected at the point of sale. The sufficiency auditor correctly
  identified the mismatch (*"none of the parents provide the specific
  reason why an individual customer's credit card transaction was
  declined"*) and returned `CLARIFICATION_REQUIRED`.
- `banking77_r1_0077` ("payment"): the intent formulator mapped it
  instead to **Deposit_Account / Transaction Disputes**, reasoning that
  *"payments are processed through a customer's primary transaction
  account."* This is a genuinely **different product** than the card
  case received, triggered purely by word choice, even though both
  utterances describe the identical real-world event in the dataset (a
  card transaction failing at a shop). Under Deposit_Account, retrieval
  surfaced an FAQ about **automated/recurring payment arrangement
  failures** (e.g. a GIRO or standing instruction) — also not a real
  match. Here the auditor's own reasoning admits the mismatch (*"it does
  not fully resolve a general or manual payment decline inquiry since it
  specifically addresses automated payment arrangement failures"*) but
  approved it as `RELEVANT` anyway, with `generation_guidance` broad
  enough (insufficient funds, blocked account, limits) to produce a
  plausible-sounding answer.

This one pair shows the mechanism is at least two-layered, not a single
cause: (1) the intent formulator routes "card" and "payment" phrasings of
the same event to two different product families (Credit_Card vs.
Deposit_Account) — not unreasonable for "payment" in isolation (it could
mean a bank transfer), but it splits an otherwise-identical question into
two different FAQ search pools; and (2) the sufficiency auditor was
stricter on one resulting path than the other given comparably
loosely-matching evidence. Underneath both paths sits a genuine content
gap: no FAQ exists for "why was my active card declined during a
purchase," as distinct from "why was a card application declined."

**Still not investigated:** whether this same product-split mechanism
(word choice routing to a different product family for what the dataset
treats as one intent) recurs across the other 23 low-concentration
categories, or whether some of those are driven purely by auditor
strictness variance with both paths already on the same, correct
product; whether this correlates with any of the already-known retrieval
or sufficiency bugs (BUG-005, BUG-006, BUG-016). Left for future work per
explicit instruction.

**Embedding-based confirmation at scale, not just anecdote (2026-09-17).**
The `declined_card_payment` finding above was one manually spot-checked
pair. To test the same question systematically, every BANKING77 primary
subquery (3,028 usable texts) was embedded with chromadb's own
`DefaultEmbeddingFunction` (all-MiniLM-L6-v2 -- the same local embedding
model the production RAG pipeline itself uses, no LLM API calls) and the
full pairwise cosine-similarity matrix computed. For each pair, "SHARE"
means both were resolved with at least one common approved parent id
(consistent answer basis); "DISJOINT" means both resolved but with zero
common parent id; "MIXED" means one resolved and the other didn't.
Script: `research/2026-09-05_banking77_full_coverage/scripts/fairness_cosine_consistency.py`,
full pair listing in `research/2026-09-05_banking77_full_coverage/reports/fairness_cosine_consistency.md`.

| Similarity band | Pairs | SHARE rate |
|---|---:|---:|
| 0.97-1.00 (near-duplicate) | 716 | 60.9% |
| 0.93-0.97 | 1,094 | 52.6% |
| 0.90-0.93 | 1,236 | 51.1% |
| 0.85-0.90 | 2,977 | 47.7% |
| 0.80-0.85 | 4,885 | 45.1% |

Even in the closest band, only 60.9% of near-duplicate questions share an
evidence basis -- confirming the `declined_card_payment` pattern is
general, not an isolated pair, though the effect is a gradient (share
rate degrades steadily with distance) rather than a sharp cliff. See
**BUG-025** for a related, more severe finding surfaced by the same
analysis: even *byte-identical* repeated questions (similarity = 1.000,
no wording variation at all) are inconsistent 35.2% of the time.

**Partial mitigation shipped (2026-09-17).** A cross-session answer cache
(`backend/app/orchestration/answer_cache.py`, `docs/FAIRNESS_ANSWER_CACHE_DESIGN.md`)
now surfaces a historically-confirmed parent id as an extra sufficiency-
audit candidate for any sufficiently close (cosine similarity >= 0.85)
future question, seeded from the historical BANKING77 corpus. This
directly helps pairs close enough to clear that threshold, but **not**
the specific `declined_card_payment` pair traced above: its own
similarity is only 0.65 (checked directly, same embedding model), well
below any threshold that could be set without conflating unrelated
intents. The underlying product-misrouting root cause (S1 above) is
unpatched; see the design doc's S9 for the full scope of what this
mitigation does and does not cover.

**Re-measure and decision (2026-09-25):** re-run on Gemini with the current code (3
categories x 10 phrasings, `research/2026-09-25_model_judgment_remeasure`). `lost_or_stolen_card`
is now consistent (8/10 SUFFICIENT with the freeze-and-replace answer). `declined_card_payment`
and `extra_charge_on_statement` still split by wording. Traced cause: the knowledge base has a
direct FAQ for a lost card but none for "why was my card declined" or "what is this small
extra charge" (BANKING77 categories from another bank's app), so the outcome depends on
whether a nearby FAQ is retrieved and whether the auditor offers it as related information.
Two declined-payment phrasings retrieved the same candidates (incl. `faq_efca64c1`, common
reasons automated payments fail); one auditor offered it as a RELEVANT side result, the other
did not. Two extra-charge phrasings retrieved different candidates because their search queries
differed ("unrecognized charge" vs "unrecognized pending charge"). **Decision: accepted as a
known limitation for the MVP** -- a data-coverage gap plus normal model judgment; the rules
work as designed. Adding FAQs for these topics would close it but is a data change outside
the MVP scope.

## BUG-025 — The exact same question, asked twice, does not reliably get the same answer

**Severity:** High **Confidence:** Confirmed (31/88 informative exact-duplicate-text groups, full-corpus) **Status: Open, not yet root-caused**
**Found in:** BANKING77 full-coverage study, embedding-based Fairness/consistency pass (2026-09-17) -- surfaced as a side effect of the BUG-023 cosine-similarity analysis above, when isolating pairs that are not merely *similar* but the exact same string.

**What happens:** the BANKING77 test campaign ran multiple rounds, and
several exact customer utterances occur more than once (independently,
each in its own fresh session, same category label both times). Grouping
by (category, exact text) across the full corpus finds 110 such
duplicate-text groups. Of the 88 groups with at least one resolved
occurrence (informative -- the other 22 groups never resolved at all, in
any occurrence, so say nothing about consistency), **31 (35.2%) show a
split outcome**: either some occurrences resolved and others returned
`CLARIFICATION_REQUIRED`/unresolved, or every occurrence resolved but
with zero overlapping approved evidence across occurrences. Concrete
example -- *"I want to delete my account."* (`terminate_account`), asked
12 separate times across the campaign: 9 times resolved identically
(`faq_49230a05` + `faq_a1fd1b2a`), but 2 times returned unresolved and
once resolved with only one of the two FAQs. No wording variation is
possible here -- it is the same sentence -- so this cannot be explained
by the ambiguity argument that partly excuses BUG-023 (e.g. "payment"
being genuinely ambiguous). Other examples: *"My transfer was
declined."* resolved once, unresolved the next time; *"My card is not
working."* resolved 3 of 6 times with varying evidence sets each time.

**Full-corpus scope check:** the underlying grouping is full-corpus (all
3,028 usable primary-subquery texts, every naturally-occurring exact
duplicate found and classified) -- not a sample.

**Not yet investigated:** the actual mechanism. The leading hypothesis is
LLM sampling variance (non-zero temperature, no caching) in one or more
of the per-turn LLM calls that sit between identical input text and the
final approved-parent decision -- most likely the intent
formulator's product/intent mapping step (if it maps the same sentence to
a different product or intent on a different run, it searches a
different candidate pool entirely) and/or the sufficiency auditor's
approve/reject judgment (if it is not perfectly consistent given the same
candidates). Distinguishing between these two candidate loci -- and
ruling out a genuinely different candidate pool from retrieval-side
non-determinism -- needs a dedicated trace of a few of the 31 split
groups' full `system_log_events`, comparable to the `declined_card_payment`
trace under BUG-023. Left for future work per explicit instruction.

**Root cause found, and partial mitigation shipped (2026-09-17).** A
concrete trace of the `"I want to delete my account."` duplicate-text
group (raw `task.rag.completed` candidate pools, not just final verdicts)
found the 35.2% inconsistency rate is driven by two distinct, independently-
occurring mechanisms within the same 12-occurrence group: (1) **retrieval-
pool variance** -- 2 of 4 problem occurrences never had the correct FAQ
(`faq_49230a05`) surfaced as a candidate at all, a different and smaller
set having been retrieved that run; (2) **auditor-judgment non-
determinism** -- the other 2 occurrences had the exact correct two-FAQ
pair present in the candidate pool, and the auditor still rejected both,
both times. A cross-session answer cache
(`backend/app/orchestration/answer_cache.py`, design and rationale in
`docs/FAIRNESS_ANSWER_CACHE_DESIGN.md`) has been implemented and seeded
from the historical BANKING77 corpus to address mechanism (1): a parent
id that has, historically, recurred as the confirmed answer for a
sufficiently similar question is now always offered as an extra candidate
to the sufficiency auditor, regardless of what a given run's live
retrieval happens to surface -- never bypassing the audit itself.
Mechanism (2) is explicitly untouched by this change (see the design
doc's S6 and S9). Candidate follow-ups (self-consistency voting, a
model-tier A/B test, rubric tightening) were proposed and then
explicitly **scoped out of this project by the user** (2026-09-17,
design doc S6a): all three are model-level tuning, ruled out as a class
regardless of which specific one — *"model tunning is out of scope, so
what we can do is surface the same parent and keep the cache update."*
Mechanism (2) is therefore a **permanent, accepted limitation of this
system, not a pending task** — status stays "partial mitigation," and
will stay there by design, not because the remaining work hasn't been
done yet.

**Live-validated (2026-09-17).** A 6-rerun live re-test of this exact
group's text (`research/2026-09-17_fairness_cache_validation/`, full
detail in `docs/FAIRNESS_ANSWER_CACHE_DESIGN.md` S9) found organic
retrieval failed to surface either correct FAQ in *any* of the 6 fresh
runs this time (retrieval reliability on this question appears to have
drifted further since the original study) — yet all 6 resolved
identically via the cache-injected candidate, up from 9/12 (75%)
historically. A parallel 4-rerun test of `"Why was my payment
declined?"` confirmed the predicted limitation just as directly: the
same cache-injected candidate was present in all 4 runs, and the
auditor still split 2/2 on approving it -- live proof the mitigation
fixes retrieval-pool variance but not auditor-judgment non-determinism.

## BUG-024 — No AI-disclosure anywhere in the product

**Severity:** Medium **Confidence:** Confirmed (all 3 supported languages + frontend UI checked directly) **Status: RETRACTED (2026-09-24) -- not a real defect, see correction below**
**Found in:** Direct source check of `backend/app/core/translations.py`
and `frontend/src/App.jsx` (2026-09-13), prompted by the MAS FEAT
Transparency principle (`AGENTIC_EVALUATION_PLAN_v1.md` Pillar E) and the
EU AI Act's August 2026 chatbot-disclosure requirement.

**What was originally reported (wrong -- see correction):** none of the
four greeting templates (`greeting_new`, `greeting_new_named`,
`greeting_returning`, `greeting_returning_named`), in any of the three
supported languages (English, Chinese, Spanish), contain any AI-disclosure
language (no "AI", "assistant", "automated", "chatbot", or equivalent).
The frontend UI (`App.jsx`) likewise carries no persistent label
identifying the interface as AI-driven anywhere a customer would see it.

**Correction (2026-09-24):** the greeting-template observation is
accurate, but the frontend-UI claim was wrong -- the original 2026-09-13
check evidently missed a persistent disclosure that already exists.
`App.jsx` (~L3642-3644) renders a permanent footer directly under the
chat input, on every screen, in every session, alongside a lock icon:
`{t(preferences.language, "chat.footer")}`, which resolves (
`frontend/src/translations.js`) to *"AI-generated responses may not be
fully accurate."* in English and *"AI助理的回复可能不完全准确。"* in
Chinese -- both explicitly containing "AI" and framing the assistant as
AI-generated, satisfying the disclosure concern this bug was raised
for. Flagged directly by the user (2026-09-24) before any fix was
implemented, which is what caught this. This bug is retracted rather
than marked fixed, since no code change was needed or made -- the
disclosure was already there the whole time, in a persistent, always-
visible location.

**One genuine, much smaller gap found while correcting this** -- see
BUG-031 below: the Spanish `chat.footer` string is an exact,
un-translated copy of the English text, rather than a Spanish
translation.

*(The two paragraphs below are the original 2026-09-13 text, kept for
the record. Both are superseded by the correction above -- the claim that
"the disclosure text simply does not exist anywhere in either file" is
false; `App.jsx` renders it via the `chat.footer` translation key.)*

**Full-corpus scope check (original, superseded):** not applicable — this is a static-content
gap (the templates and UI markup were read in full, not sampled), so the
finding is complete by construction: the disclosure text simply does not
exist anywhere in either file, in any language.

**Not yet investigated (original, superseded):** the specific wording/placement Horizon would
want for a disclosure (a greeting-line addition vs. a persistent UI
badge vs. both); whether this is legally required today for Horizon's
jurisdiction or only anticipated (the EU AI Act provision cited is
forward-looking); this is a content/feature gap, not a logic defect, so
"root cause" is simply "never built," and the fix is additive (write and
insert disclosure copy) rather than corrective. Left for future work per
explicit instruction.

**Lesson worth keeping from this retraction:** a "does X exist anywhere"
check against a UI must search the rendered translation keys too, not
just literal strings in the component file -- `App.jsx` never contains
the word "AI" as a literal at the footer site, only
`t(preferences.language, "chat.footer")`, so a plain grep of `App.jsx`
for "AI" alone would miss it, which is the most likely way the original
check went wrong.

## BUG-026 — The response generator rarely writes the required closing sentence for an attached shortcut

**Severity:** Low–Medium **Confidence:** Confirmed (236 logged generator calls with a shortcut supplied) **Status: Fixed, structurally (Response Obligation Contract redesign, 2026-09-22/23)**
**Found in:** live turn review, 2026-09-21 (session `sess_dc50c73d…`, "i lost my card in this raining weather"), then measured across the logged BANKING77 corpus.

**What happens:** when a turn resolves with a real evidence-based answer
*and* a shortcut is attached (a "navigation" button such as *Manage Card*,
or a "form" card such as *Replace Card*), the response generator's prompt
(`nodes.py`, "Available shortcuts this turn" section) tells it it MUST end
the response with one short closing statement acknowledging every such
shortcut, quoting the label verbatim and phrasing it for the shortcut's
`kind`, and that "running out of room, or the response already feeling
complete is never a reason to drop it." In practice the model writes that
sentence rarely: across 236 logged generator calls where a shortcut was
supplied, a closing sentence naming the label with button/form/"below"
wording appeared in **20 (8.5%)** -- 0/127 in two of the larger runs
(2026-09-08), 11/74 and 9/33 in the two 2026-09-09 runs, 0/2 in the
2026-09-17 validation run. The rule was present in every one of those
prompts. This is a systematic weakness, not a one-off.

**Not yet investigated (hypotheses only, none tested):** the rule is one
of dozens in a roughly 48k-character prompt and the generator is a small
model; the output schema has no dedicated field for this sentence, so it
depends on the model remembering to add it to a freeform element; in
card-loss cases the FAQ steps themselves say "View & Manage Card", so the
model may treat the shortcut as already covered. Independent of the model
question, the design already relies on a deterministic fallback for this
sentence -- see BUG-027, which currently defeats that fallback in about
a quarter of cases. Candidate directions to weigh later: always insert the
sentence in code and drop the rule from the prompt (also shortens the
prompt), or give the sentence its own output slot. Left for later per
explicit instruction.

**Fix implemented (docs/RESPONSE_OBLIGATION_CONTRACT_DESIGN.md, Stages
1-2), closing exactly the candidate direction above of giving the sentence
its own tracked slot rather than relying on freeform prose compliance.**
Every shortcut is now a first-class obligation with its own short handle
(`nav_N`/`svc_N`) that the model cites in `sourceShortcutHandles`, the
same citation mechanism a RAG task already used -- "did the model
acknowledge this" became a structural, checkable fact instead of a hope
that a ~48k-character prompt's one rule among dozens gets followed. The
Stage 2 completeness net (`_apply_response_completeness_nets`) checks
every shortcut obligation for coverage and, when the model doesn't cite
it, deterministically inserts the exact pointing sentence itself ("You can
also use/fill in and submit the '\<label\>' button/form below.") -- so the
customer-facing outcome (a shortcut is always introduced by a sentence) no
longer depends on the generator's own compliance rate at all, regardless
of what that rate happens to be. **Verified live** in this session's
regression check: replaying real historical BANKING77 turns that
previously produced this omission now produces a complete response with
the shortcut correctly introduced, with no change needed to the
generator's own prompt discipline.

## BUG-027 — The shortcut-omission fallback is skipped when the label appears anywhere in the response

**Severity:** Medium **Confidence:** Confirmed (one live turn traced end to end; 58 of 236 logged calls had the label only inside progress steps) **Status: Fixed (Response Obligation Contract redesign, 2026-09-22/23)**
**Found in:** same 2026-09-21 live turn as BUG-026.

**What happens:** `nodes.py` (~5700, "Shortcut-omission fallback") appends
`You can also use the "<label>" button below.` (or the form equivalent)
only when `label in combined_so_far` is false -- a plain substring test
over the whole generated response. In the traced turn the shortcut label
was `Manage Card`; the model's own step read *choose "View & Manage
Card."* (wording taken from the FAQ). That contains the label, so the
fallback treated the shortcut as already mentioned and inserted nothing.
The button itself was still delivered (`navigationActions` in the
persisted UI payload contained the *Manage Card* link) -- what is missing
is only the sentence pointing to it, so the customer sees a button with
no introduction. Because the generator writes the sentence itself only
~8.5% of the time (BUG-026), the fallback carries most of the load, and in
the logged corpus the label appeared *only* inside step text in 58 of 236
shortcut-supplied calls (about 25%) -- exactly the cases this test wrongly
passes. Not caused by the 2026-09-21 shorter-reasoning change: the
sufficiency reason is not part of the generator prompt, and the fallback
logic is unchanged.

**Not yet investigated:** fix options -- (a) count the shortcut as
mentioned only when the label appears in a pointing phrase ("button",
"form", "below", or quoted next to one of those), (b) always append the
deterministic sentence and only suppress it when the model wrote its own,
or (c) resolve via BUG-026's "insert in code" direction. Left for later
per explicit instruction.

**Fix implemented, option (c)+.** `_is_covered`'s check for a shortcut
obligation (`nodes.py`) now requires the SAME block to both cite the
shortcut's own handle in `sourceShortcutHandles` AND contain its label
text -- a bare substring match anywhere in the response (e.g. a coincidental
"Manage Card" inside an unrelated FAQ step like "choose View & Manage
Card") no longer satisfies coverage on its own, since that step's own
block never cites the shortcut's handle. This closes exactly the loophole
this bug describes: only a block that genuinely, citably references the
shortcut counts as covering it; anything else falls through to the same
deterministic fallback sentence BUG-026's fix installs. Verified via
fixture tests during the Stage 1/2 rollout (a label-only-in-steps case no
longer suppresses the fallback) and confirmed no regression on a normal
turn where the model does correctly cite and mention a shortcut.

## BUG-028 — A wholly-omitted clarification (or decline/no-info/conversational) obligation had no repair mechanism at all

**Severity:** High **Confidence:** Confirmed **Status: Fixed, live-verified**
**Found in:** live trace during the Response Obligation Contract investigation, 2026-09-22 (a 3-part turn: "can i buy a car using the personal loan, or the credit card, also my credit card is ugly, can i replace it?"); independently reproduced in the historical BANKING77 corpus (`banking77_r1_0237`, `research/2026-09-05_banking77_full_coverage`)

**What happens:** the response generator's own completeness net
(`_apply_response_completeness_nets`, pre-redesign) only had a real,
inserting repair for a dropped RAG task or a dropped shortcut/tool-result.
For every other obligation type — clarification, decline, no-info,
conversational — the net could *detect* an omission (log it) but never
*fix* it: no block existed for the reviser to find or edit either, since
the reviser is explicitly scoped to editing existing blocks' text, never
adding one. A turn whose only real omission was a wholly-dropped
clarification therefore had no path to recovery at all: it burned every
one of its 3 audit retries re-generating the same incomplete response,
then fell through to node_11's terminal deterministic fallback — the
customer's actual question (in the traced case, which product the
customer meant) was never asked, in any of the 3 attempts or the final
fallback. `banking77_r1_0237` shows the identical shape independently: two
tasks and their associated clarifications were dropped in round 1, the net
of that era logged the omission but inserted nothing, two reviser rounds
made no difference (nothing existed for them to edit), and the turn ended
on a bare, unhelpful apology for both tasks.

**Root cause:** deterministic code — the coverage net was built
type-by-type, and only two of the (eventually) nine obligation types ever
received a real repair; the other seven were detection-only by omission of
implementation, not a deliberate design choice. This is the same
underlying gap `BUG-009`'s fix addressed for a *different* failure shape
(zero response_blocks entirely) — an omission this fundamental
structurally cannot be caught downstream of generation, and needs a
generation-time (or generation-adjacent) fix.

**Fix implemented:** `docs/RESPONSE_OBLIGATION_CONTRACT_DESIGN.md`'s
unified Response Obligation Contract gives every obligation type — RAG
evidence, verified facts, clarification, decline, no-info, conversational,
shortcuts, unavailable service requests, personal data — the same
`{id, generation_content, generation_guidance, generation_rule}` shape and
the same completeness net, rewritten (Stage 2) as one loop dispatched by
`generation_rule` that gives every type a real, mode-appropriate
deterministic repair when the model drops it: a clarification obligation
that's uncovered gets a real `type: "clarification"` block inserted
(`_clarification_fallback_text`, with its `options` populated from
`clarification_guesses`), not just a log line. This is what `node_11`'s
terminal fallback path already calls, so the exhausted-retry case inherits
the same guarantee.

**Verified live:** the exact 3-part turn that originally exposed this
(address + balance + apply-for-account + lost card, from the design doc's
own validation) now resolves all 4 obligations in one generation call, no
reviser round, no omission events at all. Separately, this session's
regression check replayed `banking77_r1_0237` itself end to end: the first
generation pass again omitted both clarifications (same as the historical
failure), but this time the net's `response.flexible.entry_omitted` event
carried `"fallback_inserted": true` for both, one reviser round refined
the wording, and the final response asked both clarifying questions
correctly instead of falling back to a double apology.

---

## BUG-029 — Internal tool-routing/reasoning text leaked verbatim into a customer-facing clarification question

**Severity:** Medium **Confidence:** Confirmed (1 historical case; root
cause shared with BUG-028, not independently measured at corpus scale)
**Status: Fixed, live-verified**
**Found in:** this session's live regression check, 2026-09-23, comparing
a fresh replay of `banking77_r1_0237` against its own historically-logged
`final_response` (`research/2026-09-05_banking77_full_coverage`)

**What happens:** the old system's logged response for this case reads:
*"Could you clarify what you mean by 'The customer is reporting an ATM
discrepancy, but no registered tool covers dispute resolution or ATM
error reporting.'?"* — the text inside the quotes is not a paraphrase of
anything the customer said or a natural clarifying question; it reads as
an internal routing/capability note (something closer to a task
dispatcher's own reasoning about why no tool could handle the request)
surfaced directly as if it were the thing being asked about. A second,
near-identical instance appears in the same response for the turn's other
dropped task. This is a distinct, more severe symptom than BUG-028's bare
omission — the customer isn't just missing an answer, they're shown raw
internal system language.

**Root cause:** shares its origin with BUG-028 — this turn is the same
historical double-clarification-drop case, and the leaked text is
consistent with the pre-redesign clarification-recovery path reading an
internal reasoning-style field directly into the "Could you clarify what
you mean by X?" template rather than composing a natural question from
`unresolved_request`/`scope` under one consistent, audited rule. Not
independently deep-traced against the old code (which this session's
redesign has substantially replaced), so this is inferred from the
before/after behavior change rather than a line-level trace of the exact
prior mechanism.

**Fix implemented:** the same Stage 2 net fix as BUG-028 — a dropped
clarification is now always repaired via `_clarification_fallback_text`,
a deterministic, non-LLM template keyed only on `unresolved_request` and
`originator`, never on any internal reasoning/routing field — plus the
unified contract's own consolidated CLARIFICATION RULES section, which
governs every clarification obligation (whether model-composed or
net-repaired) with one explicit rule against asserting, assuming, or
echoing anything beyond `unresolved_request`/`scope`/`clarification_guesses`.

**Verified live:** the fresh replay of the identical query
(`refactor_check_banking77_r1_0237`, this session) produced two properly
phrased clarifying questions — *"are you looking for the policy on how to
report an ATM discrepancy, or do you need to navigate to a specific
transaction management screen?"* and *"are you asking for the bank's
refund policy, or are you looking for a way to initiate a dispute through
the app?"* — with a full-text grep of the output for "no registered tool"
returning zero matches.

## BUG-030 — No coherent handling for sustained upstream model unavailability: silent turn-content loss and multi-minute latency spikes

**Severity:** Medium–High **Confidence:** Confirmed (2 independent symptoms, both directly traced from live `backend/logs/system.jsonl`) **Status: Symptom 1 fixed (2026-09-25), fixture- and gateway-verified; symptom 2 (timeout latency) under discussion**
**Found in:** live production log tracing, 2026-09-23, during an ongoing Gemini high-demand period; historical trend confirmed across all rotated log files back to 2026-09-21

**What happens — two distinct symptoms of the same underlying gap:**

1. **Triage silently discards the customer's actual message and reclassifies the whole turn as generic small talk.** `node_02d_user_journey_classifier_and_triage`'s own whole-turn LLM call is wrapped in a bare `except Exception` that, on any failure, sets `raw_components = []`; when that empties every one of `financial_subqueries`/`conversational_tasks`/`clarification_tasks`, a fail-safe (`nodes.py` ~L2365) substitutes one synthetic component: `"General conversational remark with no specific banking request."` -- discarding what the customer actually said. Traced live, session `sess_57f483f6-...`, 2026-09-23: the identical message *"I need to know about my savings account. Would you please check my current balance?"* was sent twice; both times the triage call hit a genuine `503 UNAVAILABLE` ("high demand"), exhausted its one existing retry (BUG-003's fix), and both turns silently became `conversational_subquery_ids: ["subquery_1"]` with `financial_subquery_ids: []` -- no RAG, no tool call, nothing, for a request that plainly needed one. The two customer-facing replies ("I can certainly help you look into that." / "I can certainly chat with you, but I do not have access to live account details...") both come from the conversational-only path answering a synthetic placeholder, not the customer's real question. This is a strictly worse consequence than BUG-003's own case (one subquery's tool not attempted) -- here the entire turn's financial intent vanishes, and nothing in the customer-facing response indicates this was an infrastructure failure rather than a considered answer.
2. **A sustained upstream spike can push a single turn's total latency into multiple minutes, with no fast-fail or "we're experiencing delays" signal.** `call_llm_router` (aliased as `call_groq_lpu`) retries a transient failure up to `len(_google_key_pool)` times (9) per model, each attempt capped at `_LLM_CALL_TIMEOUT_SECONDS` (45s), then falls back to `_GEMINI_MODEL_FALLBACK`'s other Gemini model and repeats the same 9-attempt cycle there -- a reasonable design for eventually succeeding through a spike, but with a worst-case budget measured in minutes per call, and no shorter, latency-sensitive path for turns (like a login greeting) where the customer is actively waiting. Traced live, session `sess_436d5d29-...`, 2026-09-22: a plain login greeting's optional follow-up line -- itself 3 sequential LLM calls (generate the line, then two separate audits: `check_evidence_groundedness`, `check_conduct_compliance`) -- took **278.82 seconds** end to end. The raw sequence shows repeated `503 UNAVAILABLE` errors interleaved with `llm.call_timeout` events (a call abandoned client-side after 45s while its own network request keeps running in the background, per `call_llm_router`'s own docstring), with individual `llm.response` events eventually arriving with `elapsed_ms` as high as **143,737ms** (~2.4 minutes) for one single call. A historical scan of every `**INIT**` (login greeting) turn across all rotated log files (`system.jsonl` through `system.jsonl.5`, 12 total occurrences) shows a clear trend, not a one-off: 3.95s / 1.54s / 2.35s / 4.99s on 2026-09-21 through the morning of 2026-09-22, then climbing sharply from that afternoon onward -- 11.40s, 60.72s, **278.82s**, 39.82s, 25.62s, 55.47s, 46.53s, 74.93s -- consistent with a sustained period of Gemini-side degradation rather than a design flaw in the greeting's own call chain (which is fast and unremarkable under the earlier, healthy-latency data points).

**Root cause:** both symptoms trace back to the same gap -- every existing resilience mechanism (`_is_transient_llm_error`'s retry-once from BUG-003, `call_llm_router`'s key-rotation/timeout loop) operates purely *per call*, with no policy at the *node* or *turn* level for what should happen when the model is broadly, persistently unavailable rather than transiently blipping once. Nothing distinguishes "the call never got a chance to run" from "the model gave a genuinely ambiguous or malformed answer" at the point triage decides how to fail safely, and nothing caps a single turn's *total* wait budget across however many sequential calls and models a node happens to make, independent of the customer-facing cost of that wait.

**Explicitly not being treated now, per instruction.** Documented here so it isn't rediscovered as a mystery later, and so this exact pair of symptoms is recognized immediately if it recurs. Candidate directions for whenever this is picked up (not evaluated or committed to): distinguish a transient-error exception from a malformed-response exception in `node_02d`'s own handler, so only the latter falls back to the generic-conversational safety net (the former should presumably retry the whole triage call once more, or surface a distinct "temporarily unable to process your request" state rather than silently reinterpreting it as small talk); and/or a turn-level or node-level wall-clock budget, independent of the existing per-call retry budget, so a customer-facing node fails fast and honestly past some ceiling instead of silently compounding several calls' worth of 45-second timeouts.

**Fix (2026-09-25, design decided by the user):** a model that is
overloaded (503 UNAVAILABLE / "high demand") or has used up the daily
quota on every key is *unavailable* -- neither a retry nor another key can
restore it, so the turn stops and the customer retries later.
- `llm_service.ModelUnavailableError` is raised for: a 503 UNAVAILABLE /
  "high demand" on the first occurrence (no internal retry, no key or
  model switch -- the old one-retry from BUG-003 only met the same
  overloaded model), and a 429 once every key has been tried for both
  Gemini models. Other failures (a 500, a 400, a local config error) are
  unchanged: one retry for a transient 5xx, then the node's own fallback.
- It is deliberately a `BaseException`, so no node's `except Exception`
  fallback can swallow it and turn an outage into a misleading answer
  (symptom 1: triage reading the message as small talk). It stops the
  graph and reaches `api_gateway.chat_stream`, which returns "The model
  is currently unavailable. Please try again later." -- followed by the
  provider's exact error when the developer reasoning display
  (`debug_reasoning`) is on -- and logs `chat.model_unavailable`.
- Memory validation's prompt guard keeps failing closed on it (not a
  customer answer); the research harness catches it explicitly so an
  unattended run still waits out an outage exactly as before.
- Verified: service-level fixtures 6/6 (503 -> one call, no retry;
  quota on all 10 keys x 2 models -> unavailable after 20 calls; 500/400
  unchanged); in-process `/api/chat` with the provider mocked to 503 on
  USR_999 3/3 (turn stops at triage's first call; exact message; exact
  error appended in developer mode).

**Symptom 2 (timeouts) -- deferred (2026-09-25):** the live model moved to paid DeepSeek, which has no free-tier quota spikes, so the timeout redesign was set aside (see docs/DEEPSEEK_MIGRATION_AND_PROMPT_CACHING.md). Original note: a call that never replies is still
retried up to once per key for both models at 45s each. The user proposed
a 15s timeout with one retry; pending a check of how many successful calls
take longer than 15s.

## BUG-031 — Spanish AI-disclosure footer is an untranslated copy of the English string

**Severity:** Low **Confidence:** Confirmed (static check) **Status: Fixed (2026-09-24)**
**Found in:** while correcting BUG-024 (2026-09-24) -- static read of `frontend/src/translations.js`

**What happens:** the persistent chat footer (`App.jsx` ~L3644,
`t(preferences.language, "chat.footer")` -- the product's actual AI
disclosure, see BUG-024's correction) is translated for English and
Chinese, but the Spanish entry (`translations.js` ~L265) is a verbatim
copy of the English text: *"AI-generated responses may not be fully
accurate."* A Spanish-preference customer still sees the disclosure (and
"AI" is recognizable across both languages), so the disclosure itself is
not missing -- only its localization is. Low severity for that reason.

**Fix applied (2026-09-24):** replaced the Spanish value with a
proper translation, e.g. *"Las respuestas generadas por IA pueden no ser
completamente precisas."* -- a one-line content change with no logic
impact. Swept every Spanish key against its English counterpart: the only
other identical values are `prefs.emoji` ("Emojis") and `chat.botName`
("Horizon"), both legitimately the same in both languages -- no other
untranslated strings, and no English keys missing from Spanish.

## BUG-032 — One node_05 veto permanently pollutes every later turn's audit feedback, process-wide

**Severity:** High **Confidence:** Confirmed (reproduced in-process; 171 sessions affected in the multi-turn corpus) **Status: Fixed (2026-09-24) -- leak site removed and the reset itself hardened**
**Found in:** 2026-09-24, while measuring node_05's veto rate for the gate-relocation design

**What happens:** `node_05_outbound_deterministic_gate`, on a veto, does
`history = state.get("draft_feedback_history", [])` then
`history.append(...)` -- mutating the list in place. At the start of every
turn that key is reset from the module-level `VOLATILE_STATE_RESET` dict
via `**VOLATILE_STATE_RESET`, which copies the *reference* to one shared
`[]`, not a fresh list. So the first veto in a server process appends its
feedback to that shared list itself, and every subsequent turn -- in every
session, for every customer, until the process restarts -- begins with the
stale veto text already in `draft_feedback_history`, which the generator
prompt renders under "Failed outbound-audit feedback" as if this turn had
failed. Later vetoes accumulate on top (the prompt shows the last 4).

**Evidence:** reproduced directly in-process: before any veto the shared
default is `[]`; after one veto on a draft containing `{{faq_*}}` it holds
the feedback string, and a freshly reset turn starts with it. In the
2026-09-10 multi-turn study, one genuine veto (the generator wrote a
literal `{{faq_*}}` as a stand-in for "860 bucks", session
`..._78_7f38814f`) was followed by 171 later sessions whose *first*
generation prompt already carried that same feedback -- every one of them
for a problem that never occurred in that session (including BUG-018's
`mtsgd_32_00089`). This also inflates any "veto rate" computed from prompt
text: an earlier estimate of ~7% of sessions vetoed was almost entirely this
leak, not real vetoes.

**Scope sweep:** a pattern search for `x = state.get("<list-valued reset
key>", [])` followed by in-place mutation found only this site
(`nodes.py` ~L8043). That sweep only covers this exact pattern; the
contrasting safe idiom already exists elsewhere (node_07 uses
`list(state.get(...))`).

**Fix direction (not applied, pending discussion):** copy before mutating
at this site, and/or make the per-turn reset construct fresh lists instead
of sharing one module-level object, so no future call site can reintroduce
it.

**Update (2026-09-24): the only known leak site no longer exists.** The
deterministic gate was folded into the audit join (user design decision:
collect every finding -- probabilistic and deterministic -- first, then
revise once; see "Deterministic output checks" in `nodes.py`), and
node_05 with its in-place `history.append(...)` was removed. Its checks now
return violation records into `task_violation_history` instead of writing
to `draft_feedback_history` at all, and the join's other-scope path already
copies (`list(state.get(...))`) before extending. Regression-tested: a
failing join leaves `VOLATILE_STATE_RESET["draft_feedback_history"]`
untouched. Still open: the reset itself shares one list object per key, so
any future in-place mutation elsewhere would reintroduce this; building
fresh lists in the reset would remove the trap for good.

**Reset hardened (2026-09-24):** all six per-turn reset sites now spread
`_fresh_volatile_state_reset()` -- a deep copy of `VOLATILE_STATE_RESET` --
instead of the module-level dict itself, so every turn gets its own
list/dict objects and no future in-place mutation anywhere can leak across
turns again. Fixture-verified: same values as before; all 29 list/dict
values are distinct objects per turn; mutating one turn's reset leaves the
next turn and the module dict untouched, including through a real reset
site (node_01).

## BUG-033 — Triage decomposed by sentence, not by need, splitting one request into overlapping subqueries

**Severity:** Medium–High **Confidence:** Confirmed (same message, opposite decompositions across live runs) **Status: Fixed, live-verified (triage node)**
**Found in:** 2026-09-24 audit-merge live validation (`research/2026-09-24_audit_merge_live_validation`, case `merge_check_bug018_balance`) and a follow-up live turn

**What happens:** "I need to know about my savings account. Would you
please check my current balance?" was decomposed two different ways on two
runs: once into two TOOL_ELIGIBLE components ("I need to know about my
savings account." / "Please check my current balance for my savings
account."), once into one. Every node downstream assumes each subquery is a
*different* request, so the split case sent the lead-in sentence to FAQ
retrieval and the balance ask to the balance API; the FAQ side then wrote
"I don't have direct access to your current balance details..." next to the
real balance -- the BUG-018 contradiction by another route, which BUG-018's
per-subquery suppression cannot see because the same need sits on two
subquery ids.

**Root cause:** the triage prompt's DECOMPOSED rule defined a component by
text, not by need ("an exhaustive partition covering every distinct part...
every part becomes exactly one component... never silently drop a part"),
and its vague-ask rule ("a vague but real ask is TOOL_ELIGIBLE") then
licensed a topic-introducing lead-in to stand alone. The only counterweight
was one short clause ("do not split a coherent topic artificially"), and
nothing required components not to overlap.

**Fix (design decided by the user: decomposition is of user needs, not
literal sentences):** section 3 of the `node_02d` prompt now decomposes by
distinct customer NEED. Sentences serving the same need -- a lead-in that
introduces or frames the topic, a restatement, or an added reason, detail,
or constraint -- are combined into that need's one component ("never drop a
part" still holds: they merge rather than vanish). A new component starts
only when the customer asks for something additional in its own right
(e.g. "and also", a second, different question). Components must never
overlap. A vague sentence stands alone only when nothing else in the turn
states the same need more specifically. Greetings/thanks stay their own
NON_FINANCIAL component. Three worked examples (one need across two
sentences x2, two needs).

**Verified live (triage node called directly, 2026-09-24):** the target
message produced exactly one component on 4 of 4 valid runs ("Check the
current balance of my savings account."; one further run hit a Gemini 503
and fell into BUG-030's fallback -- not a decomposition result, re-run).
Two phrasings that are not prompt examples also merged correctly ("I have a
question about my card. Can you tell me how to freeze it?" -> one; "I'm
travelling to Japan next week. Will my debit card work there?" -> one).
Regression cases kept their real splits: "Tell me about my savings account,
and also what's my balance?" -> two; the real 4-part turn (address /
balance / apply / lost card) -> four; "Thanks, what's my balance?" -> one
need + one NON_FINANCIAL; "I have a question about my loan." alone -> one.
Noted, not a defect of this change: the travel case kept "Japan" but dropped
"next week" from its component text.

**Still recommended as a failsafe (user's stated plan):** a turn-level
guard against internal contradiction, since triage is LLM judgment and a
prompt rule reduces but cannot guarantee correct decomposition.

## BUG-034 — Two outbound-audit rules conflict on whether a personal-data token is permitted evidence

**Severity:** Medium **Confidence:** Confirmed (1 live trace; behavior intermittent) **Status: Fixed (2026-09-24), live-verified on the audit node**
**Found in:** 2026-09-24 audit-merge live validation, case `merge_check_bug018_balance` (`research/2026-09-24_audit_merge_live_validation`)

**What happens:** the audit rejected a correct balance sentence ("Your
current balance for your savings account is {{data-719ca175}}.") on three
consecutive rounds -- "the permitted evidence (faq_24638b08)... does not
contain the customer's current account balance" -- exhausting retries and
forcing node_11's fallback. The audit prompt for that call did contain both
the balance entry in `personal_data_placeholders` and the personal-data
source rule, so this is not missing input (and not from the 2026-09-23
Stage 3 conditional-rules change).

**Likely root cause:** two rules in the same audit prompt disagree. Direct
Factual Grounding (`nodes.py` ~L8802) says every "... placeholder ... must
be directly supported by the Permitted Evidence (Task-Level Approved
Evidence)" -- the parenthetical narrows permitted evidence to RAG evidence
only -- while Source-Boundary Rules say `personal_data_placeholders`
entries *do* permit a sentence containing that token. With conflicting
instructions the verdict is unstable: the same kind of sentence passed on
the first attempt in other runs (e.g. all four BUG-017 re-verification
phrasings).

**Fix (2026-09-24):** the narrowing appeared in three places, not one, all
now aligned with the full Permitted Evidence the prompt already lists:
(a) Direct Factual Grounding now says every assertion/placeholder must be
supported by "Task-Level Approved Evidence, or one of the tool-derived
entries listed with it (available shortcuts, unavailable service requests,
verified customer-specific facts, personal data references), within
exactly what the Source-Boundary Rules below allow that entry to support";
(b) "An unmapped element may use any Task-Level Approved Evidence" now says
any Permitted Evidence on the same Source-Boundary terms, and names the
case that triggered this -- a block citing only a tool-derived entry such
as `data_1` never appears in the task mapping, so it was always "unmapped"
and judged against RAG evidence alone; (c) the Courtesy Context Rule's
"assess every remaining financial statement... only against Task-Level
Approved Evidence" now points to the same definition. Nothing loosened: the
Source-Boundary Rules still bound exactly what each tool entry supports.
Covers `verified_api_context` facts the same way.

**Verified live (audit node called directly, 2026-09-24):** the failing
shape rebuilt -- a RAG block (account-number steps, `faq_24638b08`) plus a
block citing only `data_1`, "Your current balance for your savings account
is {{data-719ca175}}." -- passed 3/3 (it failed 3/3 rounds in the original
trace), each reason explicitly citing the personal-data authorization.
Negative control: the same block with a fabricated figure ("... is
S$5,240.18.") was correctly rejected as an unsupported factual claim. n=3
is consistent with the fix, not a measured rate; the original trace also
had a RAG-side denial the rebuilt case omits.

## BUG-035 — The deterministic no-info statement repeats the customer's entire request back verbatim

**Severity:** Low–Medium **Confidence:** Confirmed (live) **Status: Fixed (2026-09-24), fixture- and live-verified (triage)**
**Found in:** same trace as BUG-034 (node_11 fallback)

**What happens:** `_no_info_evidence_text` (`nodes.py` ~L745) builds
`f"I don't have information available regarding: {resolved_subquery}."`,
and `resolved_subquery` can be the customer's whole message. Live, the
customer received "I don't have information available regarding: I need to
know about my savings account. Would you please check my current
balance?." -- awkward, a doubled period, and (because the same turn had
already shown the balance) self-contradictory. This text is deterministic
(never LLM-generated) and is relayed as-is by the no-info path and by
node_11's fallback. Since the BUG-033 triage fix this specific trigger
(an overlapping lead-in subquery) no longer occurs for that message, but
the template itself still echoes whatever `resolved_subquery` holds.

**Fix (design decided by the user, 2026-09-24): name the need, and have
triage produce it.** Triage components now carry a `need` -- a short noun
phrase for the one distinct customer need, written to the customer, e.g.
"the current balance of your savings account" -- declared in the
`TriageComponent` schema *before* `customer_component` (registered in the
Pydantic class, not only the prompt), and the need-based decomposition rule
now tells the model to name each component's need first ("if two candidate
components would get the same need, they are one component"), so the field
also reinforces BUG-033's rule. The need rides on each financial subquery,
is attached to every no-info entry where it is created (node_03c's
`_terminal_retrieval_feedback`, node_02e's unresolvable-task entries), and
`_no_info_evidence_text(resolved_subquery, need)` now renders "I don't
have information available on {need}." Without a need (older state, or the
model omitted it) the old form is kept, minus the doubled end punctuation.
Trust is unchanged: `resolved_subquery` was already triage's own output.
The `components` schema description, which still described the old
"exhaustive partition" rule, was also aligned with need-based
decomposition.

**Verified:** 11 fixture checks (template with/without need, punctuation,
marker, split, older markers without need, schema order, triage carry,
prompt, node_11 fallback). Live, triage called directly on six messages:
`need` populated every time and reads naturally in the template -- e.g.
"I don't have information available on the current balance of your savings
account.", "... on freezing your card.", "... on reporting your lost credit
card." -- with BUG-033's decomposition intact (target -> 1, the 4-part turn
-> 4, thanks split off). A Chinese message yields an English need, matching
the template's (pre-existing) English-only wording.

## BUG-036 — `verified_session_evidence.recorded` re-logs the previous turn's record when the current turn adds none

**Severity:** Low (observability only; no customer-facing effect) **Confidence:** Confirmed **Status: Fixed (2026-09-24), fixture-verified**
**Found in:** live session `sess_ca094793-...`, 2026-09-24 (turn 2, a balance-only turn with no RAG lookup)

**What happens:** turn 2 of that session ran no retrieval at all, yet the
log showed `verified_session_evidence.recorded` with `task_1` and three FAQ
parents and `turn_number: 1`. Not stale state: `search_tasks` and
`task_results` are both in `VOLATILE_STATE_RESET` and reset every turn.
`_append_verified_task_history` returns the existing history unchanged
when the turn verified nothing, and node_06_outbound_audit_join then logs
`verified_task_history[-1]` whenever the history is non-empty -- i.e. the
previous turn's record, presented as if this turn had recorded it. It
misled this session's own debugging.

**Fixed (2026-09-24):** both logging sites in node_06_outbound_audit_join
now log only when the returned history is longer than the turn's incoming
history, i.e. this turn actually appended a record. Fixture-verified: a
turn that verified nothing emits no event (it previously re-logged turn 1);
a turn that verified something logs once, with its own parents.

## BUG-037 — A completed redirect (deeplink/service_request) suppressed RAG evidence, leading to a fabricated fact

**Severity:** High (a materially wrong claim reached the customer) **Confidence:** Confirmed (live) **Status: Fixed (2026-09-24), fixture-verified -- regression introduced by BUG-018's fix**
**Found in:** combined-fixes regression, case `reg_card_arrival` (`research/2026-09-24_combined_fixes_regression`)

**What happens:** "When will my physical card arrive?" -> subquery_1 got a
RAG lookup (task_1 RELEVANT, approved parents `faq_5ad80c63` and
`faq_9d7a4b83`, both "6-7 business days") and a Manage Card deeplink, which
completed. BUG-018's contradiction guard suppressed a RELEVANT RAG task
whenever *any* non-RAG tool had completed on the same subquery, so the
correct evidence was removed; the generator's only obligation was the
shortcut, and with no evidence it invented "Horizon Bank physical cards
typically arrive within 7 to 10 business days." The audit caught it in
round 1 but the flagged sentence still shipped (see BUG-038).

**Root cause:** BUG-018's fix treated every completed non-RAG tool as
ground truth answering the subquery. True for an `api` data fetch (the
answer itself); false for a redirect. Per the user: a deeplink or service
request is resolved at subquery level -- it is what the bot can do, and it
scores 1 in turn resolution (resolved 1 / partly resolved 0.5 /
unresolved 0) -- but because of its redirect nature it can never suppress
other results. Its resolution *status* is not the question; its nature is.

**Fix:** the guard in `_response_evidence_manifest` now counts only a
completed `api` attempt (`subqueries_answered_by_data_fetch`). Turn
resolution is untouched: `_fully_covers`, `_compute_subquery_status`, and
`_score_subquery` still treat a completed redirect as RESOLVED. The other
consumers of "resolved by another tool" (node_02e/node_03c) only skip a
decline or no-info statement when a redirect covers the need -- they never
drop positive evidence, so they already fit this principle. Fixture-
verified: RELEVANT + completed deeplink or service_request -> evidence
kept; RELEVANT + completed api -> still suppressed (BUG-018 intact);
deeplink-only and service_request-only subqueries still RESOLVED.

## BUG-038 — An audit violation on a task no block carries approves the block holding the flagged sentence

**Severity:** High (a flagged, unsupported claim can ship) **Confidence:** Confirmed (live, same trace as BUG-037) **Status: Fixed (2026-09-25), all three steps fixture-verified; live-verified in the code-change validation run**

**What happens:** the generator cited `['task_1', 'task_1_scope',
'nav_1']` for one element; task_1 was not in `valid_task_ids` (its evidence
had been suppressed, BUG-037), so the stored block kept only
`sourceShortcutHandles: ['nav_1']` and `sourceTaskIds: []`. The audit
flagged "...arrive within 7 to 10 business days." against `task_1`.
`_partition_audited_response` found no block carrying `task_1`, so it
approved every block -- including the one holding the flagged sentence --
and added `task_1` to pending with no block behind it (the BUG-009 path).
The reviser received `pending_block_ids: []` and had nothing to edit; audit
round 2 then passed the same text, and it reached the customer.

**Why it matters beyond BUG-037:** any violation whose `task_id` no block
carries -- a dropped or mis-cited id, a handle cited in the task field --
lets its flagged content ship while looking approved. Candidate directions
(not decided): treat such a violation as response-wide (every block
pending), and/or locate the flagged block by matching the violation's
`unsupported_content` against block text.

**Why the block had no task id -- root cause (traced 2026-09-25):** the
generator was not shown any evidence for `task_1` (the BUG-037 guard had
removed it), but it was still shown a "Terminal unresolved task marker"
for `task_1` and `task_1_scope` (status RELEVANT). A marker was a second,
separate pointer to a RELEVANT task, outside the obligations, and its rule
told the generator "some evidence exists (already reflected in the
Approved evidence elements above)". The evidence was gone; the marker
stayed. The generator wrote the answer the missing evidence would have
given ("7 to 10 business days"), cited `task_1`/`task_1_scope` from the
markers, and code dropped those ids as non-obligations -- leaving a block
whose only source was the `nav_1` shortcut. The same gap also meant the
evidence entry never stated its own status, although EVIDENCE RULES and
the audit's Scope-Expansion rule both branch on it.

**Design principles (decided with the user):**
- A RELEVANT entry always has at least one approved parent (already
  enforced: "RELEVANT REQUIRES A REAL PARENT" in node_03c's prompt and
  code); with none it is IRRELEVANT and becomes a no-info entry.
- Every block must be linked to the obligation data the generator received
  (`id`, `generation_content`, `generation_guidance`, `generation_rule`) --
  a block based on nothing is a generation error.
- The reviser sees what the generator saw when it generated the block.
- The block is the unit of revision; a block may cite one or several
  obligations.

**Step 1 -- markers merged into the evidence entry (fixed 2026-09-25):**
- `_response_evidence_manifest` carries `resolution_status` (SUFFICIENT /
  RELEVANT) on each entry; the evidence obligation's `generation_content`
  includes it, and a RELEVANT entry's `generation_guidance` is a new
  `compose_partial` mode (answer what the parents cover, then a neutral
  availability notice for the rest, never filling it in). EVIDENCE RULES
  now open with the SUFFICIENT/RELEVANT definitions and carry the marker's
  former rule (no clarifying question for the uncovered part; a
  Clarification Input for the same subquery is still rendered).
- The markers section is removed from the generator, reviser and audit
  prompts; `_split_terminal_markers` became `_no_info_entries` (no-info
  entries only). The audit's `approved_by_task` entries carry
  `resolution_status` (a fact about the evidence, not generation guidance,
  so audit independence holds), and its RELEVANT availability rule moved
  into the core grounding rules.
- The generator's `clarification` output field existed only for markers;
  the prompt now asks for it empty (text put there anyway is still kept
  and audited).
- Effect: a task's evidence and its status are one entry, so any guard or
  the consistency check that removes the entry removes everything about
  that task -- nothing can point at removed evidence.
- Fixture-verified (15/15, LLM mocked): RELEVANT and SUFFICIENT entries
  render with status and the right guidance in generator, reviser and
  audit; a guard-removed RELEVANT task leaves no trace in the generator's
  data; CLARIFICATION_REQUIRED still renders its no-info entry.

**Step 2 -- the reviser sees what the generator saw (fixed 2026-09-25):**
- The generator records every obligation entry it received
  (`generation_obligations`, keyed by id). Each generated block keeps any
  id it cited that was not an obligation (`sourceUnmatchedIds`) instead of
  that id being silently dropped.
- node_04c gives the reviser exactly the obligation entries the pending
  blocks cited, from that recorded copy -- no longer rebuilt from the
  flagged task ids, and shortcuts/customer-data entries no longer attached
  to every revision regardless of citation. A response-wide round gets
  every recorded entry (the generator saw all of them). Without a recorded
  copy (e.g. the evidence-only path) the old rebuild is the fallback.
- New deterministic findings on normal_response turns
  (`_deterministic_outbound_findings(check_citations=True)`):
  `non_obligation_citation` (a generated block cites an id that was not an
  obligation; the finding names the ids) and `no_obligation` (it cites
  none -- e.g. text in the generator's `clarification` field). Keyed by
  the block's own blockId. Raised only on a block as generated: a block the
  reviser rewrote is marked `revised` and not re-checked, since the reviser
  cannot change citations and a repeated check could never pass. For a
  `no_obligation` block the reviser, which then has no entries for it, is
  told to remove every banking fact and keep at most a courtesy sentence
  (the reviser cannot delete blocks).

**Step 3 -- no flag can approve the text it flags (fixed 2026-09-25):**
`_partition_audited_response` matches a block by every id it is known by:
blockId, sourceTaskIds, sourceShortcutHandles and sourceUnmatchedIds. A
violation that still matches no block makes the round response-wide
(nothing approved, every block to the reviser) instead of approving every
block. In the BUG-038 trace the flag on `task_1` now finds the block
through its recorded citation of `task_1`.

**Verification:** fixture-verified with the LLM mocked -- step 1 15/15;
steps 2-3 21/21 (partition matching incl. the BUG-038 block shape, a
handle, a blockId finding, unmatched -> response-wide, BUG-009 empty
blocks; citation findings, not repeated after revision, off for other
scopes; generator records obligations and non-obligation citations;
reviser gets only the pending block's cited entries, all of them when
response-wide); turn-consistency fixtures 9/9 unaffected. A live check was
blocked by a Gemini high-demand spike (503 on every key).

## BUG-039 — No turn-level consistency check between a turn's collected results

**Severity:** High (a self-contradicting response reaches the customer) **Confidence:** Confirmed (live) **Status: Fixed (2026-09-24), fixture-verified (9/9 decision-validation cases) and live-judgment-verified (3/3)**
**Found in:** live trace during the BUG-033 decomposition work

**What happens:** each result of a turn (FAQ evidence, the customer's own
data, verified facts, no-info statements) is produced by its own task, and
nothing checked them against each other before generation. BUG-018's guard
only suppresses a RELEVANT RAG task when an `api` fetch completed on the
*same* subquery; when triage merged the need differently, or the
contradiction ran across subqueries, both results reached the generator
-- e.g. "I don't have information available on the current balance of your
savings account" alongside the fetched balance.

**Design (decided with the user):**
- Runs inside node_04a (resolution planner), only after tool calling has
  settled (no more retry), and only when the turn has at least two
  asserting results -- a single result cannot contradict itself, so most
  turns make no call (`turn.consistency.skipped`).
- Redirects (deeplink/service_request), clarifications and declines are
  not included: a redirect stays RESOLVED in turn resolution but, by its
  redirect nature, is never a factual claim and never suppresses anything.
- **The LLM decides.** It names each conflict and marks which side to
  `drop` (reasoning first, then `conflicts_with`, then `decision`). A first
  version resolved conflicts by a fixed authority order; per the user this
  was replaced, since a deterministic priority would make the semantic
  call nearly pointless. Code only validates the decision: a drop counts
  only for an item named in a conflict, the customer's own data is never
  dropped, and if both sides of one conflict are dropped both are kept.
  Rejections are logged (`rejected_decisions`).
- Dropped items are removed from what generation reads
  (`consistency_suppressed_ids` honored by `_response_evidence_manifest`;
  no-info entries removed from `task_resolution_instructions` /
  `unresolvable_search_tasks`). A failed call drops nothing.
- **Placeholders:** the checker sees FAQ precision placeholders resolved to
  their real values -- a live false positive flagged two card-arrival FAQs
  (both 6-7 days) as conflicting because they cited different placeholder
  ids. These are public FAQ figures; the resolved text exists only in the
  checker's view, and generation still receives the `{{faq_*}}`
  placeholders, substituted only at node_07. The customer's own data stays
  a `{{data-*}}` token (its value is never shown to any LLM); a
  "PLACEHOLDERS ARE VALUES" prompt rule tells the checker to treat each
  token as the value it stands for. Real FAQ values were deliberately
  *not* extended to the sufficiency auditor or the outbound audit: those
  compare a draft to its own evidence by token identity, which real values
  would weaken.

**Verification:** decision-validation fixtures 9/9; live judgment 3/3 --
no-info vs. fetched balance (checker dropped the no-info item itself),
complementary FAQ + balance (nothing dropped), two agreeing card-arrival
FAQs (no conflict, previously a false positive). A natural live turn that
reaches the drop path is rare now that need-based triage (BUG-033) sends a
plain balance request only to the data fetch.

## BUG-040 — A RELEVANT entry's "not covered" notice contradicts another result in the same reply

**Severity:** Medium **Confidence:** Confirmed (live) **Status: Open**
**Found in:** code-change validation on Gemini, case `val_address_change` (`research/2026-09-25_code_change_validation_gemini`)

**What happens:** "what's my address in the system, can i change it?" -> the address came
back as a data token (data_1) and the change-address FAQ was RELEVANT. The generator
wrote "Your current postal address on file is {{data-...}}", then, for the RELEVANT
entry: "while the available information does not cover how to view your current address
in the system, you can update your personal information...". The audit rejected the
contradiction three times; the turn fell back to the plain address + form.

**Cause:** the RELEVANT guidance added with BUG-038 step 1 (03fb488, `compose_partial`)
says to give "a neutral notice that the available information does not cover the rest of
their request". It does not limit "the rest" to what no *other* result covers, so the model
attached the notice to a part the customer's data had already answered.

**Root cause found (2026-09-25, while testing a first fix):** the contradiction is
*instructed* by the sufficiency auditor. Its generation_guidance for the RELEVANT entry
read "...while admitting plainly that there is no information available regarding how to
view or check your current address in the system." -- true from its view (it sees only
its own task's FAQ candidates, not the data fetch), and the generator deliberately follows
generation guidance. A wording fix to the RELEVANT notice (limit it to what no other entry
answers) is correct but insufficient: 6/6 test replies still contradicted the data when
given the real guidance. Options awaiting decision: (a) show the sufficiency auditor the
subquery's other results (e.g. "address fetched, value hidden") so its guidance never
claims them unavailable; (b) let the generator ignore the part of an auditor note that
another entry contradicts (weakens "the generator trusts generation guidance").

**Fix (2026-09-25, decided with the user):** part 1 (bb5b83d) limits the generator's RELEVANT
notice to what no other entry answers. Part 2 gives the sufficiency auditor the whole picture:
every completed non-FAQ tool result this turn (customer data -- what was fetched, never the
value; app screens; service forms), tagged with its need and whether it is the task's own need,
scoped to what each can support (`other_tool_results_this_turn`, OTHER TOOL RESULTS THIS TURN
rule). Design principle (user): a judging node gets the full picture; wrong decisions are fixed
by tuning its rules, not by withholding information. Replay of 20 logged sufficiency prompts x3
on Gemini: false "unavailable" guidance 2 -> 0; the two tool-driven suppressions each dropped
FAQ content that did not fit the need (onboarding address procedure; damaged-card replacement for
an ATM-retained card), which the user accepted as intended ("no need to overfeed"). Live on both
models, the multi-need turn now answers the address part with the data token and the form only.
BUG-049's data-fetch guard is unchanged by this and remains open.

## BUG-041 — Duplicate card sentence the reviser cannot remove

**Severity:** Medium **Confidence:** Confirmed (live, 3 cases) **Status: Fixed (2026-09-25) -- prompt-level citation rule; replay- and live-verified**
**Found in:** `research/2026-09-25_code_change_validation_gemini` -- `banking77_r1_0096` ("You can also use the Manage Card button
below." twice), `val_address_change`, `mtsgd_32_00103` turn 3.

**What happens:** the model mentions a card (deeplink/form) and the completeness net also
appends its own sentence for the same handle. In `val_address_change` the audit flagged the
repetition, but the violation was attributed to the stepper block, not the block holding
the duplicate, so the reviser was never handed it; three failed audits, then fallback.
Duplicates existed before (e.g. `mtsgd_32_00011` turn 4 on 2026-09-10) but now also cause
audit loops. Candidate directions (undecided): the net should recognise a card already
mentioned in text; attribute a duplicate-card violation to the handle's own block.

**Root cause (2026-09-25):** not the net's logic. The net treats an uncited obligation as
omitted, and the model often wrote the content without citing it. In the live firings, 20
of 21 misses were an element that cited one obligation but not a second one it also
covered: a card sentence inside the evidence element (cited only `task_1`), or two entries
for the same request (`task_1` + `task_1_scope`, or a no-info entry merged into the entry
it bridges to). The shared OBLIGATIONS header said "id (cite it, and only it, in that
element's own source_task_ids)", which reads as one id per element, and no rule section or
example showed a multi-id citation. The net then appended a duplicate card sentence or a
contradicting "Sorry, I don't have complete information..." sentence.

**Fix:** the generator's OBLIGATIONS header now says to put each id in every element that
addresses the entry, together with the other entries' ids, and names the two common cases.
Placement was chosen by replaying the logged failing prompts (7 failure prompts, 5 controls,
2 runs each, on the original models): baseline 7/13 runs with a missing citation; header
rewording alone 8/14; **header + the two named cases 0/13 (controls 0/10)**; an
end-of-prompt check also 0/14; shortcut-guidance-only fixed cards but left 2/10 control
misses. No over-citation seen. The header sits after the data marker, so the cached prefix
is unchanged (about 137 extra uncached tokens per call). The reviser needs no change: it
cannot set citations (blocks keep their stored ids). **Live (Gemini):** "I was getting cash
and can't get my card back." and "what's my address in the system, can i change it?" both
cited the card with the task (`['task_1','nav_1']`, `['task_1','svc_1']`); the net did not
fire. Observation, not part of this bug: in the address turn the model wrote the form
sentence as a sixth stepper step, which the audit flagged as unsupported by the FAQ steps;
the reviser fixed it in one round.

## BUG-042 — Clarifying question dropped between sufficiency rounds

**Severity:** Medium **Confidence:** Confirmed (live, 1 case) **Status: Open**
**Found in:** `research/2026-09-25_code_change_validation_gemini`, `banking77_r1_0021` ("Delete this account!").

**What happens:** round 1 of the sufficiency auditor set `unresolved_term: "account type"`
with guesses; round 2 (after widening) returned none. The terminal result used round 2, so
no clarifying question was asked -- unlike retained partial evidence, an authored
clarification is not carried across rounds.

**Fix (487b3dd):** the last clarification the auditor authored per task is retained
across rounds (`task_partial_clarification`) and used when a later round returns none;
cleared when the task becomes SUFFICIENT.

## BUG-043 — No-info statement suppressed by a wrong redirect

**Severity:** Medium **Confidence:** Confirmed (live, 1 case) **Status: Fixed (2026-09-25) -- planner tool-selection wording**
**Found in:** `research/2026-09-25_code_change_validation_gemini`, `banking77_r1_0021`.

**What happens:** the planner chose the Security Settings deeplink for "Delete this
account!" (a model judgment). The FAQ search then found nothing, and because a completed
redirect "covers" the need, the no-info statement was skipped by design (see BUG-037's
notes), leaving only an unrelated button. The rule is right when the redirect fits and
harmful when it does not; whether to keep it is a design decision.

**Decision (2026-09-25):** a completed deeplink or service_request is a sufficient
resolution, so the redirect-covers-need rule stays. The defect is the planner choosing a
deeplink whose service does not fit the request.

**Root cause:** the planner's instructions routed deeplinks by risk, not by the service a
screen offers. The domain was `high_risk_transaction` ("a high-risk action (money
movement, security changes)"); the service_request description said high-risk actions
are "deeplink's job"; and step 2 read as "for each domain, choose an entry", with the
no-entry way out last. "Delete this account!" is high-risk, so the planner named the
deeplink domain and took the entry sharing the word "security" (Security Settings:
"update PIN, view Horizon Key status"). Replay of the logged prompt: 5/8 wrong picks, all
after naming the high-risk domain (its reasoning: "a high-risk security and account
management action").

**Fix:** tools are chosen by the service their own entries offer. The deeplink domain is
now `app_self_service` (the customer wants to do something themselves that one of the
listed app screens offers; only the listed screens exist); the service_request domain is
worded the same way; a principle states that risk only limits which tool may carry an
action (high-risk actions are never forms) and never makes a tool fit an action none of
its entries offers; step 2 starts with "plan one only when an entry's whole description
offers what the customer wants -- a domain named in step 1 never obliges a task"; the
registry descriptions of service_request and deeplink say the same.

**Verified (replay, 9 queries x old/new text, 4 Gemini + 3 DeepSeek runs):** no-match
requests (delete account, revert a transaction, close a credit card, raise a transfer
limit) plan only an FAQ search; send money, change PIN and lost card keep their screen;
"download last month's statement" now gets View Statements in 7/7 runs (old text missed it
in 5/7). The wrong pick itself did not reproduce on the old text in this session (0/12,
vs 5/8 earlier on the identical prompt), so the fix is verified as aligned and
non-regressive rather than measured against a live failure. Behaviour change, accepted:
an address change now matches both the Change Address form and the Update Profile screen;
DeepSeek asks which one the customer wants (the existing tie rule), Gemini offers both.
Model differences in such a genuinely two-option case are acceptable.

## BUG-044 — Small generation artifacts in customer text

**Severity:** Low **Confidence:** Confirmed (live) **Status: Closed (2026-09-25) -- jargon fixed; the rest accepted**
**Found in:** `research/2026-09-25_code_change_validation_gemini` -- a stray "(4)" after flattened steps (`banking77_r1_0177`); internal
jargon "There is no approved evidence or record to execute a transfer" (`mtsgd_32_00103`
turn 2); the same point stated twice in a row (`banking77_r1_0014`).

**Resolution (2026-09-25):**
- *Internal jargon:* fixed (ed6e28d).
- *Stray "(4)" and step numbers inside stepper details -- a data quirk, accepted for the
  MVP:* ETL tokenisation stored the list numbers of 20 FAQs (92 placeholders) as precision
  placeholders (`{{faq_7883a8c2_007}}. Horizon will issue a new card...`). The generator
  cannot see placeholder values and must keep them verbatim, so when it reorganised the
  steps it carried the "numbers" along: "1. Log into..." inside an already-numbered
  stepper, and "(4)" when step 4 moved into prose. The model followed the placeholder
  contract correctly; the cause is the data, and the user decided not to fix it in this
  MVP.
- *The same point stated twice -- model writing, accepted:* two evidence entries for the
  same need both lead with "you can get a new card", and the model restated it. The
  generator already carries the general "one coherent, natural response" instruction; with
  that rule in place, the remaining variation is the model's own writing.

## BUG-045 — A need with no tool gets no obligation, so the reply is empty

**Severity:** High **Confidence:** Confirmed (live) **Status: Open -- made rare by the planner rewrite (cc99564)**
**Found in:** DeepSeek comparison (`research/2026-09-25_deepseek_banking77_comparison`,
`banking77_r1_0017` "Is there a way to revert my transaction from this morning?").

**What happens:** the planner planned no tool for the subquery; it stayed OPEN with the
budget exhausted and produced no obligation of any kind -- not even a no-info entry. The
generator returned an empty element list, the audit passed the empty draft, and the
customer got "Sorry, I don't have an answer for that right now." The planner rewrite makes
FAQ search the default, so this rarely triggers now, but an open subquery with no tool and
no evidence should still receive the standard no-info entry.

**Fix (ff2c0f0, decided with the user):** after the planner runs, any open subquery
with no task, no tool-selection clarification and no earlier FAQ search gets a FAQ search
in code (`tool.plan.faq_default_added`); node_04a adds the standard no-info statement for a
subquery that still reaches generation with nothing (`resolution.no_info_failsafe`).

## BUG-046 — PIN word-filter false positive

**Severity:** Medium **Confidence:** Confirmed (live, every model) **Status: Open**
**What happens:** "My PIN is not working and I need assistance." is rejected in 0.02s by
the inbound sensitive-word rule `credential_pin_disclosure` (`inbound.sensitive_word.rejected`),
which treats any mention of a PIN as disclosing one. Reproduced on Gemini (2026-09-05 and
2026-09-25) and DeepSeek.

**Fix (29845c7):** a credential phrase ending in "is" blocks only when the next word is
not a state word ("not", "wrong", "blocked", "expired", ...); disclosures stay blocked.

## BUG-047 — Garbled completeness-net fallback sentence

**Severity:** Low-Medium **Confidence:** Confirmed (live) **Status: Open**
**What happens:** the net's fallback sentence embeds the customer's request verbatim:
"Sorry, I don't have a complete answer for the part about I withdrew cash from an ATM but
received the wrong amount, and I want the cash back ... right now." Seen on DeepSeek
(`refactor_check_banking77_r1_0237`, `refactor_check_banking77_r1_0375`); related to BUG-035's
fix of the no-info template, which did not cover this net sentence.

**Fix (fa1114b):** "Sorry, I don't have complete information available on {need} right
now." (generic sentence when no need is named).

## BUG-048 — ATM card-capture FAQs unreachable from a Debit_Card search

**Severity:** Medium **Confidence:** Confirmed (traced) **Status: Fixed (2026-09-25) -- card part and address part**
**What happens:** `faq_47fe2093` / `faq_7d02d0a7` ("An ATM has captured my Horizon card...")
are tagged `Deposit_Account` only. When the formulator maps the need to `Debit_Card` (a
defensible choice), the product-filtered search never retrieves them, and retry widening
uses the model's own `alternate_products` (it chose Credit_Card). Gemini on 2026-09-05
happened to choose Deposit_Account. A near-identical query succeeded only because the
answer cache injected the two parents. Candidate directions (undecided): add `Debit_Card`
tags to card-handling FAQs filed under Deposit_Account; always widen Debit_Card <->
Deposit_Account on retry; formulator guidance.

**Second example (live, DeepSeek, 2026-09-25):** "...i forgot my address registered in
the system. Where can i change it for delivery of the new replacement of card?" -- the
formulator filed the address change under `Credit_Card` (because of the replacement-card
context); the widened retry (Credit_Card + Horizon_APP) never reached the in-app
"update your address" FAQ (`faq_24568045`, retrieved successfully in earlier address
tests), so round 2 approved only the onboarding Singpass/ICA procedure as RELEVANT. The
general pattern: an FAQ reachable under a single product tag is missed whenever the
formulator picks a different, defensible product for the same need.

**Decision (2026-09-25):** not a design problem and not a data problem to merge away -- it
reflects Horizon's business: one physical card that is a debit card from the start and can
also have a credit function enabled; Deposit_Account is the account linked to the debit
card. The FAQ tags follow that business (card FAQs are split across Debit_Card,
Credit_Card and Deposit_Account), so the fix belongs in how the formulator reads a card
request, not in the tags.

**Fix:**
- A shared `HORIZON_PRODUCT_BRIEFING` constant (next to the product matrix, reusable by any
  call that chooses a product), included in the formulator and the Territory B retry, in
  the fixed (cached) part of each prompt. It states the one-card/two-functions fact and the
  deposit-account link, and how to classify: a physical-card question (lost, stolen,
  damaged, captured by an ATM, activation, PIN, replacement, delivery, appearance) targets
  Debit_Card, Credit_Card and Deposit_Account together; a function question is decided from
  the customer's words and the conversation; an unclear function is a CLARIFY (debit or
  credit?), never a guess.
- The formulator's schema forced `target_commercial_product: str` (the router attaches
  `QueryFormulation` to this prompt), so DeepSeek wrote a single product even when its own
  reasoning named all three, and Gemini packed several into one comma string that the
  normaliser dropped (leaving no valid search scope). The field is now `List[str]`, the
  prompt examples use lists, and `_normalized_product_tags` also splits a comma or
  stringified-list string.
- The formulator example "my credit card is ugly, can I replace it?" (Credit_Card only)
  contradicted the briefing and now uses all three products.

**Verified:** replay of 9 card questions, old vs new text, both models. Physical-card
questions: all three products in every run (old: one product, often the wrong one --
"I lost my card" / "activate my new card" went to Credit_Card on Gemini). "An ATM
swallowed my card": the card-capture FAQs are now retrieved in 5/5 runs (old 0/5).
Function questions keep their product (credit limit, credit card bill -> Credit_Card);
savings interest stays Deposit_Account. "What's the annual fee on my card?": DeepSeek
asks debit or credit (2/3; once it reasoned CLARIFY but wrote SEARCH with no product),
Gemini treats it as a card-level question (all three). Live on DeepSeek: both card-capture
FAQs retrieved and approved SUFFICIENT; the reply gives the order-a-new-card / Customer
Care steps.

**Refinement (2026-09-25, same day): the physical card is the debit card.** Searching all
three products for a physical-card question widened the candidate pool (Deposit_Account
alone has 272 FAQs), letting ranking push the right FAQ down. Business rule: the physical
card is the debit card, so a physical-card question targets **Debit_Card only**. To keep
every physical-card FAQ reachable under that rule, `Debit_Card` was added to the 8
physical-card FAQs that lacked it -- `faq_8336a2c7` (replacement fee, lost/damaged card)
and `faq_83fb94a7` (lost-card replacement), both Credit_Card only; `faq_47fe2093` and
`faq_7d02d0a7` (ATM captured the card) and `faq_c9998e4f`, `faq_34e4a4ab`, `faq_78b1b823`,
`faq_bde7d1d5` (metal card), all Deposit_Account only. Function FAQs (overseas usage,
credit limit) and the Savings Pot unlock FAQs (a deposit feature) were deliberately left
unchanged. Applied surgically to exactly those 8 entries: the canonical source
`rectified_horizon_faqs.json` (8 added lines), the regenerated staging artifact (only
those 8 parents and their 26 child chunks differ), the 8 `knowledge_parents` rows and the
26 Chroma chunk metadata entries (no re-ingestion, no re-embedding). **Verified:** replay on
both models -- every physical-card question (ATM swallowed, lost card, replacement cost,
activation, ugly card) -> `["Debit_Card"]` in 5/5 runs; credit limit -> Credit_Card;
savings interest -> Deposit_Account; "annual fee on my card" -> CLARIFY (debit or credit)
in 5/5. Retrieval: ATM card-capture FAQs 5/5, replacement-fee FAQ 5/5, lost-card FAQs 5/5.

**Address example -- root cause and fix (2026-09-25):**
- *Why it ranked 10-14:* the address subquery carried two needs -- "where can I change my
  registered address **for delivery of the new replacement card**" -- so the product
  (Credit_Card), all three search angles, and the direct query (the subquery text itself)
  were about card delivery. The widened retry reused the same wording, so 13 card FAQs
  outranked `faq_24568045`. Similarity search cannot tell two needs in one text apart.
- *Fix 1, a search query:* the formulator now writes `search_query` first (new first field
  of `SearchTask`) -- the query: what is being searched for, like telling a librarian what
  you want, not why; the need in general terms, scoped to general, non-personal
  information (no personal reason, purpose, circumstances, dates, places, amounts, names,
  or wording of another need), consistent with the subquery. Product, intents, and angles
  are chosen for it, and the direct-query track searches it; the full `customer_component`
  still goes to the auditor and the generator, so the reason is not lost. Tried first in
  triage (Gemini leaked the purpose 3/3) and moved to the formulator, which already does
  the retrieval engineering (angles). Replay, both models: personal details stripped 12/12
  (dates, places, amounts, names); address query clean 5/6; no reason substituted.
- *Fix 2, product briefing:* Horizon offers its services through the Horizon App, so a
  service shared across products (e.g. managing personal details, app access) concerns
  Horizon_APP. Address changes now get Horizon_APP in 11/12 runs (was 0-2/12), phone-number
  changes 6/6; card and transfer questions keep their products.
- *Retry:* with the clean query the widened retry selects `faq_24568045` 4/4 (was 0).
- *Remaining:* round 1 still misses `faq_24568045` because that FAQ is tagged intent
  `General Inquiries` only, while the formulator (correctly) picks `Profile Updates`; with
  General Inquiries included it ranks #1 on both models. **Decision: the data stays as is** --
  the design should be resilient to imperfect tags, and it is: the widened retry covers the
  FAQ's intent and selects it (4/4). Accepted cost: one extra retrieval round for this case.
  **Follow-up (same day):** the tag was then confirmed as a mis-tag against the taxonomy's
  own definitions (Profile Updates: "Address, phone number, and email updates"; General
  Inquiries: "Catch-all. App navigation, UI bugs..."): the source listed the raw intents
  `App Navigation` and `General Inquiries`, both consolidated to General Inquiries. `Profile
  Update` was added to `faq_24568045` only (canonical source, staging: that parent plus its 2
  new Profile Updates chunks; the SQLite row; 2 new Chroma chunks, embeddings identical to the
  existing chunks of the same text). Round 1 now retrieves it on both models.

**Auditor side -- designed behaviour, not a defect (2026-09-25):** in the traced session the
sufficiency auditor approved `faq_6e2d941d` (changing an address while applying for an
account) as RELEVANT for an existing customer. It had all the information, and its rules
deliberately let a parent on the same topic count as material and be offered as an
unclaimed courtesy with an honest admission ("no info is info"). Approving it was the
model's judgment within that design. With the search query and the retag, round 1 now
finds the right FAQ, so this case no longer depends on it.

## BUG-049 — The BUG-018 guard removes FAQ evidence a data fetch does not replace

**Severity:** Medium **Confidence:** Confirmed (live, DeepSeek) **Status: Fixed (2026-09-25) -- guard retired, fixture-verified**
**Found in:** live check 2026-09-25, session `sess_0be5670c...`, turn 1 ("...Also i forgot my
address registered in the system. Where can i change it for delivery of the new
replacement of card?").

**What happens:** triage made one need of "I forgot the address registered in the system;
where can I change it...". The planner gave it a data fetch (`profile-address`), the Change
Address form, and a FAQ search. The FAQ search ended RELEVANT (task_2) with a related-topic
companion (task_2_scope). Because a data fetch *completed* on that subquery,
`_response_evidence_manifest`'s guard (`subqueries_answered_by_data_fetch`, BUG-018/037)
dropped both RELEVANT tasks: they never reached the consistency check or the generator.
The reply showed the address and the form, but nothing from the FAQ about changing it --
only a clarifying question that the retrieved evidence had prompted.

**Cause:** the guard assumes a completed data fetch answers the whole subquery, so any
RELEVANT (partial) FAQ result for it can only be a contradicting hedge (the original
BUG-018 case: "I can't access your balance" next to the fetched balance). That holds when
the need is purely a lookup; it fails when one need combines a lookup ("what is my
address") with a how-to ("where can I change it"), which need-based triage (BUG-033)
does by design. The turn consistency check (BUG-039) now exists to catch real
contradictions semantically, so the blanket guard may be removable or narrowable.
Candidate directions (undecided): drop the guard and rely on the consistency check;
or keep it only when the subquery's domains are purely personal_data_retrieval.

**Fix (2026-09-25): the guard is retired.** `_response_evidence_manifest` no longer drops
non-SUFFICIENT FAQ tasks on a subquery with a completed data fetch. The contradiction
BUG-018 was about is now judged with the whole picture rather than prevented by
withholding evidence: the sufficiency auditor sees the fetch as a same-need result (BUG-040
part 2) and must not call a tool-covered part unavailable; `compose_partial` gives no
notice for a part another entry answers; and the turn consistency check (BUG-039), which
the guard used to hide this evidence from, drops evidence that conflicts with another
result. **Verified:** fixture on the traced state (RELEVANT task_2 and its `_scope`
companion now reach the manifest; consistency suppression still honoured; 5/5) and the
existing regression suites. Live: "Hello. I need to check my balance." on DeepSeek gives
the balance token with no denial (BUG-018 not reintroduced). The combined lookup + how-to
need could not be reproduced live: triage now splits it into two needs in both attempts,
so the retired guard would not have fired there either.

## BUG-050 — Completeness net misses content the reviser deleted

**Severity:** Medium **Confidence:** Confirmed (live) **Status: Open**
**Found in:** Gemini comparison run of the 2026-09-25 multi-need turn, session `test_gemini_compare_61d0646e`
("...i lost my credit card... Also i forgot my address registered in the system. Where can
i change it...").

**What happens:** the generator wrote the address step block with "Your registered postal
address on file is {{data-...}}." (citing `task_2`, `data_1`, `svc_2`). Gemini's audit wrongly
flagged the token as disclosing the value (the misreading BUG-034 addressed in the prompt);
the reviser then deleted that sentence. The completeness net runs after revision, but the
block still cites `data_1` -- `sourceShortcutHandles` is locked during revision -- so the net
counted the personal-data obligation as covered and did not re-add it. The customer, who had
said they forgot their address, never got it.

**Cause:** the net's coverage check (`_apply_response_completeness_nets`, `_is_covered`) keys
on a block's citation fields, which only describe what the block was generated from, not
what its text still says after revision. Related to BUG-041 (the net's duplicate sentence
vs. the model's mention): both come from citations and text drifting apart. Candidate
directions (undecided): for handle-type obligations with a fixed token (personal data),
check the token is still present in the block text; or refresh a block's citations after
revision.

**Retracted (2026-09-25):** the delivered reply (Persistent_Chat_Message) ends with
"Your current postal address on file is {{data-ecc2a1cb}}." -- the completeness net *did*
re-add it; `_is_covered` already requires the token to be present in the block text. The
original diagnosis read the reviser's raw output instead of the final reply.
