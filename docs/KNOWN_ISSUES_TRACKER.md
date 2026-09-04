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
| BUG-002 | Guidance-only RELEVANT admission sometimes rejected in a loop by the outbound audit | Medium | Confirmed (intermittent) | LLM judgment | Fix implemented, live-verified on original repro |
| BUG-003 | Transient upstream 503 on the Tool-Calling Planner is treated as "nothing found," not retried | Medium | Confirmed | **Deterministic code** | **Fixed, verified (mocked failure)** |
| BUG-004 | Network-specific questions (Visa/Mastercard) answered with network-agnostic evidence marked SUFFICIENT | Medium | Confirmed (2 occurrences) | LLM judgment, **now deterministically enforced** | **Fixed, live-verified (4 independent cases)** |
| BUG-005 | Evidence approved as SUFFICIENT can be topically mismatched to the customer's actual request | Medium–High | Confirmed | LLM judgment | Open |
| BUG-006 | Evidence Applicability Rule missed a real premise mismatch (unauthorized-charge FAQ used for a duplicate-charge complaint) | Medium–High | Confirmed | LLM judgment | Open |
| BUG-007 | Stale `generation_guidance` from an earlier round can persist into a later round's successful verdict | Low | Confirmed | **Deterministic code** | **Fixed, live-verified** |
| SUSPECTED-001 | Triage may misclassify a genuine eligibility question as chitchat | Low | Suspected | LLM judgment | Needs verification |
| SUSPECTED-002 | Possible query-intent misinterpretation for an ambiguous "source of funds" question | Low | Suspected | LLM judgment | Needs verification |
| BUG-008 | `NARROW_TO_EVIDENCE_SCOPE` recovery silently drops the unconfirmed claim instead of admitting it | Low–Medium | Confirmed | LLM judgment (prompt gap) | Fix implemented, live-verified |
| BUG-009 | Outbound audit silently PASSes a turn when response generation returns zero blocks | Medium–High | Confirmed (2 occurrences) | **Deterministic code** | Open |

**All three deterministic-code bugs (001, 003, 007) are fixed**, and BUG-004
is now also fixed via a hybrid approach (see its own entry) that converts an
LLM-judgment problem into a deterministically-enforced contract. BUG-002 and
BUG-008 have fixes implemented and live-verified but not yet proven at scale
(see each entry's own caveats). BUG-005, BUG-006, and the two suspected items
remain open. **BUG-009 is a newly-found, still-open deterministic-code bug**
— see its own entry.

**Why the distinction matters:** a deterministic-code bug (001, 003, 007,
009) has a fix with a guaranteed outcome — patch the logic, the failure mode
is gone. An LLM-judgment issue (002, 005, 006, both suspected items) is the
model's own reasoning going wrong on a specific input despite the relevant
rule already being correctly wired into the prompt; a fix (tightening the
rule, adding an example) can reduce the rate but can't guarantee elimination
the way a code patch can — these need re-testing after any prompt change,
not just a one-time verification. **BUG-004 shows a third pattern worth
naming**: the underlying judgment (does this evidence confirm what the
customer specifically named?) is still made by an LLM, but the *consequence*
of getting it wrong is now closed off deterministically — the model can
still mis-resolve one item, but it can no longer let that mistake silently
produce a SUFFICIENT verdict, because the code enforces the downgrade
regardless of what the model's own `status` field says. This is a reusable
pattern (see BUG-004's "Reason-to-Guidance Contract" note) worth reaching
for whenever a similar upstream-can't-know / downstream-can-check gap shows
up again, rather than relying on prompt wording alone.

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

**Severity:** Medium–High **Confidence:** Confirmed, 2 independent occurrences **Status: Open**
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

**Suggested fix direction:** in `_partition_audited_response`, after
computing `flagged`, also check for any `task_id` in `flagged` that isn't
covered by *any* block's `sourceTaskIds` (whether because `response_blocks`
is empty or a block for that task was simply never produced) and add it to
`pending` unconditionally — a flagged task_id with nothing to show for it
should never silently drop out of consideration. This is a deterministic
fix with a guaranteed outcome, not an LLM-judgment issue: the audit itself
already correctly detects the problem every time; only the downstream
partition logic silently discards that finding.

**Not yet fixed** — found while investigating BUG-004 live-testing during
an active Gemini spike; deferred at the user's direction to finish the
BUG-004 investigation first.
