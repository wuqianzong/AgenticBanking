# Tool Calling v3.0 — Subquery-Owned Attempt Schema

**Status: IMPLEMENTED**, with two deliberate deviations from the original
design below, both agreed before implementation to minimize risk against a
system with no automated test coverage. This document supersedes the
data-model portion of `docs/TOOL_CALLING_v2.0.md` (that document's
turn-boundary and triage sections, and its Runtime Flow through Node 02d,
remain accurate — only the `tool_task` / `tool_result` / `search_task` /
`task_result` schema and the Node 04a resolution step changed).

**Implementation notes:**

1. **RAG's internal retry machinery was left untouched ("boundary-only"
   migration), not rebuilt around `result.steps`.** `node_02e`,
   `node_03b_rag_worker`, and `node_03c_sufficiency_auditor` still operate
   entirely on `search_tasks` / `task_results` / `task_retry_counts` /
   `task_rejected_parent_ids` / `task_partial_evidence` /
   `task_sufficiency_history` / `task_feedback` / `deferred_retry_task_ids`
   / `task_resolution_instructions` -- all still real `AgentState` fields,
   exactly as before this migration. Rebuilding that intricate, proven
   multi-stage retry state machine (angle/product/intent expansion, retry
   barriers) to read/write a nested `result.steps` array was judged higher
   risk than the reconciliation-gap bug this migration exists to fix,
   with no test suite to catch a subtle regression. Instead, a single new
   function, `_finalize_rag_attempts` (replacing the old
   `_finalize_rag_tool_results`), is the one integration point: once RAG's
   internal loop reaches a terminal per-subquery outcome, it projects that
   outcome onto the owning subquery's `rag` tool_attempt (`status` via the
   verdict table below, `result: {retrieval_state, approved_parent_ids}`).
   **Consequence**: a `rag` attempt's `result` never actually contains a
   `steps` array in this implementation -- the round-by-round history
   this document describes below stays exactly where it always lived,
   inside RAG's own internal state, not nested in the attempt. If RAG's
   retry engine is ever rebuilt, `result.steps` remains the intended target
   shape.
2. **`node_04a`'s LLM-driven capability-matching call was fully removed**
   (the more invasive of the two options considered), not preserved
   alongside an additive deterministic status. `resolution_status` is now
   computed purely by `_compute_subquery_status`, and the "does an untried
   capability's scope match this subquery" judgment -- previously a second,
   independent LLM call in `node_04a` -- is not re-derived anywhere. It is
   `node_03a`'s own planning call, invoked every round for every subquery
   whose rollup is `OPEN` (not gated behind a separately-computed
   `tool_gaps` object anymore, which is retired entirely).

Verified live end-to-end: the exact query that previously caused a
contradictory apology and forced escalation now resolves and answers
cleanly (both the `api`-covered and RAG-covered subqueries correctly
combined, zero contradiction); a plain RAG-only query and a
`service_request` flow both regression-tested clean with zero errors.

**Follow-up (same status, landed in a second pass)**: problem #2 in "Why
this exists" below -- the mechanical "RAG always runs" default -- was
originally left as future work (see the superseded "Explicitly not decided"
bullet). It has since been closed; see "Relevance-first dispatch" below for
the design and `node_03a`'s docstring/prompt for the implementation.
Verified via four targeted unit tests covering the cost-tiebreak's exact
branches, plus two further live traces (a 2-subquery turn where RAG was
correctly skipped entirely, and the 5-subquery mixed-tool turn resolving
`api`/`rag`/`deeplink`/`service_request` together in one round with RAG
invoked only for the one subquery nothing else covered).

**Follow-up (separate doc)**: `node_02e`'s own SEARCH/CLARIFY/DECLINE
resolution (base-product identification, DECLINE's honest category
wording, and how a CLARIFY/DECLINE-only turn is presented to the customer)
was substantially reworked after this document's initial migration -- see
`docs/DECLINE_CLARIFICATION_DESIGN_v1.0.md`. **Stale as of a later
revision, corrected here**: this originally also added a `route_n19`
branch straight to `node_11` for a turn with a pending
`intent_clarification_task` and no `search_tasks` at all. That branch no
longer exists -- `route_n19`'s conditional-edge map still lists `"n25"` as
a legal mapping key, but `route_n19`'s own function body never returns it;
`node_11` is reachable only via `route_n20`/`route_n21`'s three-failed-
retry condition now, for every exhausted-retry turn, not specifically
clarification-shaped ones. See `RAG_RETRY_EFFORT_v1.0.md` and
`DECLINE_CLARIFICATION_DESIGN_v1.0.md` §4a for the current clarification
pipeline, including `node_03c`'s own evidence-authored clarifications --
unified with `node_02e`'s into one `clarification_tasks` contract, added
after this passage was written.

## Why this exists

Two problems surfaced while tracing a live turn ("what's my address on
file, and what loyalty tier am I on, and what's the interest rate for my
savings account"):

1. **A reconciliation gap, not a generation bug.** The response generator
   correctly answered the loyalty-tier subquery using the `api` tool's
   verified fact, and correctly judged RAG's parallel evidence for that same
   subquery too weak to cite. But the coverage-tracking safety net in
   `_generate_response_blocks` only understands RAG's own `search_tasks`
   world (a `task_id` either got cited in `source_task_ids` or it didn't) —
   it has no visibility into the tool world (`_verified_api_context`,
   `_personal_data_placeholders`), which is keyed by subquery text
   (`for_request`), not `task_id`. The net saw an "uncited" RAG task and
   force-appended a contradicting "sorry, I don't have an answer for your
   loyalty tier" sentence right after a sentence that already answered it —
   which the outbound audits correctly caught and rejected, three times,
   until the turn escalated.
2. **A mechanical, not smart, tool-selection default.** `node_03a`
   unconditionally creates a RAG task for *every* financial subquery in
   round 1, regardless of whether another registered tool will end up being
   the actual, exclusive answer. Other tools are only ever *additive* to
   that default, never a replacement for it. This is what put a RAG task
   and an `api` task on the same subquery in the first place, and is the
   root reason the two coverage systems could ever disagree about the same
   subquery.

The underlying design flaw both problems trace back to: `financial_subquery`
is the true atomic unit of customer need, but nothing downstream of triage
actually models it that way. RAG's evidence and every other tool's results
sit in parallel, independently-keyed ledgers (`search_tasks`/`task_results`
vs. `tool_tasks`/`tool_results`), correlated only by an incidentally-shared
`subquery_id` field that nothing ever uses to reconcile "is this subquery
actually covered."

## The core model: the subquery owns its attempts

```json
{
  "subquery_id": "subquery_2",
  "customer_component": "What is my current loyalty tier?",
  "resolution_status": "RESOLVED",
  "tool_attempts": [ /* see below */ ]
}
```

`financial_subqueries` becomes the sole aggregation root. Each subquery owns
a list of every tool attempt made on its behalf, across every planning
round of the current turn. `resolution_status`
(`PENDING | RESOLVED | NEEDS_TOOLS | EXHAUSTED`) is a new field, computed
deterministically (see below) — never separately asserted by an LLM call
the way it is today.

### Cardinality

- `subquery_id → search_task` (RAG): one-to-one. One logical retrieval unit
  per subquery, however many internal retries it takes.
- `subquery_id → tool_attempts`: one-to-many, and can grow across planning
  rounds. A subquery may pick up `rag` in round 1, `api` alongside it in
  the same round, and another tool in round 2+ if a gap remains.

## The attempt envelope — identical shape for every tool

```json
{
  "attempt_id": "subquery_2::rag::r1::1",
  "tool_name": "rag",
  "operation": "retrieve_faq_evidence",
  "planning_round": 1,
  "status": "completed",
  "input": {"customer_component": "What is my current loyalty tier?"},
  "result": { /* tool-specific, opaque to everything outside that tool's own code */ },
  "availability_reason": ""
}
```

- `attempt_id` = `{subquery_id}::{tool_name}::r{round}::{sequence}` —
  self-describing in logs (unlike today's `tool_rag_1`, which tells you
  nothing about which subquery or round it belongs to), and collision-proof
  across rounds without a global counter.
- `input` is the **stable, original assignment** from the planner — one
  key, validated against the tool's registry, never mutated afterward. This
  is true for every tool, including `rag` (`customer_component` never
  changes across its internal retries — only `result.steps[i].input` does;
  see below).
- `status` is one of `pending | running | completed | unavailable`, set
  exactly once per transition, by the tool's own dispatch code — never
  inferred by an LLM. `pending` → created by `node_03a`. `running` → RAG
  only, while its internal retry loop continues (the other three tools are
  synchronous one-shot and skip this state entirely). `completed` /
  `unavailable` are terminal.
- `result`'s *contents* are entirely tool-defined and opaque to every other
  tool's code and to the generic aggregation logic below — only that
  tool's own dispatcher and its own consumer-facing sanitized views
  (`_verified_api_context`, `_personal_data_placeholders`,
  `_available_response_shortcuts`, RAG's evidence hydration) know how to
  read it.
- `rag_role` is an extra field present only on `rag`-tool attempts —
  `"needed" | "complementary" | "redundant"`, assigned by `node_03a`'s
  planner call at creation time and never changed afterward. It exists
  purely to drive the deterministic cost tiebreak described in
  "Relevance-first dispatch" below; nothing else reads it.

### Per-tool `result` shapes

| tool | `result` contents | notes |
|---|---|---|
| `api` (placeholder mode) | `{"disclosure_mode": "placeholder", "placeholder_token": "{{data-fe8700b3}}"}` | never the real value — see `docs/AGENTIC_CAPABILITY_ROADMAP_v1.0.md` WP-1/WP-3 |
| `api` (direct-value mode) | `{"disclosure_mode": "direct_value", "value": "Gold", "fact": "The customer's current loyalty tier is Gold."}` | safe to expose everywhere — WP-5 |
| `deeplink` | `{"route": "/app/cards/manage"}` | destination_id already lives in `attempt.input` |
| `service_request` | `{"special_id": "svc_8f2a...", "description": "...", "form_schema": {...}}` | |
| `rag` | `{"retrieval_state": "PARTIALLY_SUFFICIENT", "approved_parent_ids": [...], "steps": [...]}` | the only tool whose terminal state comes from more than one internal try — see next section |

An `unavailable` attempt of any tool has an empty `result` and a filled
`availability_reason` (e.g. `"Destination 'atm-locator' not in registry"`)
instead.

## RAG's internal multi-round retrieval: one attempt, not three

RAG's three internal rounds (direct search, then up to `MAX_RAG_ATTEMPTS -
1` refinement stages: intent expansion, product expansion, angle expansion)
are **not** separate attempts. The test: an attempt is created by the
*planner* (`node_03a`) assigning a tool to a subquery — an external,
planning-driven event. RAG's own retry loop (`node_18`'s sufficiency
auditor returning `RETRY`) never goes back to the planner; it's the same
tool continuing to work the one job it was already given, with a refined
query. Modeling each round as its own attempt would reintroduce the exact
problem this redesign removes: several same-tool records for one subquery,
needing a new "which one is authoritative" reconciliation rule layered back
on top.

Instead, the round history is nested **inside** `result`, as an optional,
generic `steps` array — available to any tool whose terminal state takes
more than one internal try (only RAG uses it today):

```json
{
  "attempt_id": "subquery_2::rag::r1::1",
  "tool_name": "rag",
  "planning_round": 1,
  "status": "completed",
  "input": {"customer_component": "What is my current loyalty tier?"},
  "result": {
    "retrieval_state": "PARTIALLY_SUFFICIENT",
    "approved_parent_ids": ["faq_da866599"],
    "steps": [
      {"step": 1, "input": {"retrieval_mode": "DIRECT", "direct_query": "What is my current loyalty tier?"}, "output": {"candidate_parent_ids": ["faq_112233"]}, "verdict": "RETRY"},
      {"step": 2, "input": {"retrieval_mode": "MULTI_ANGLE", "angle_groups": [{"group_id": "base"}]}, "output": {"candidate_parent_ids": ["faq_998877"]}, "verdict": "RETRY"},
      {"step": 3, "input": {"retrieval_mode": "MULTI_ANGLE", "angle_groups": [{"group_id": "base"}, {"group_id": "retry_2"}]}, "output": {"candidate_parent_ids": ["faq_da866599"]}, "verdict": "PARTIALLY_SUFFICIENT"}
    ]
  }
}
```

Two fields resolve what would otherwise be a real ambiguity ("the input
varies per round, but `attempt.input` is supposed to be stable"):
`attempt.input` is the original, unchanging assignment; `result.steps[i].
input` is the refined, per-try input (retrieval mode, angle groups,
product/intent) that genuinely does change round to round.

`steps` is deliberately **not** promoted to sit beside `result` as a
top-level, always-present field on every attempt. Two reasons: (1) it would
create two competing sources of truth for "the outcome" (`attempt.result`
vs. `attempt.steps[-1].output`) for the three tools that never need it, and
drift between them is exactly the class of bug this whole redesign removes;
(2) it would permanently tax three atomic, one-shot tools with a nesting
layer they'll never populate. `result`'s own top-level fields remain the
single, canonical contract every consumer reads, regardless of how many
internal tries produced them.

## Status-consistency rule for RAG (the one tool where drift is possible)

Because `api`/`deeplink`/`service_request` set `status` exactly once, at
their single one-shot outcome, they can never drift. RAG is the only tool
where "the last thing that happened" (a step's verdict) and "the attempt's
outward status" are two different pieces of state — so the mapping from one
to the other must be a fixed table, applied by exactly one writer:

| latest step's `verdict` | `attempt.status` | meaning |
|---|---|---|
| `RETRY` | `running` | budget not exhausted, another step follows |
| `SUFFICIENT` | `completed` | terminal, full coverage |
| `PARTIALLY_SUFFICIENT` | `completed` | terminal, partial coverage — the *degree* lives in `result.retrieval_state`, not in `status` |
| `CLARIFICATION_REQUIRED` | `unavailable` | terminal, budget exhausted, zero evidence ever found |

**Enforcement rule**: whatever code appends a new entry to `result.steps` is
the *only* code permitted to write `attempt.status`, and it does so purely
by running the new step's verdict through this table — never by a second,
independent judgment. Any other code that needs to know "is this attempt
done" reads `attempt.status`; it never re-derives an opinion from
`result.steps` itself. One writer, many readers.

**Known open edge case, deferred as a minor detail**: what `attempt.status`
should be if `MAX_TOOL_PLANNING_ROUNDS` (the *outer* tool-planning loop
budget) is reached while a RAG attempt is still internally mid-retry (i.e.
`RETRY` but the RAG-specific `MAX_RAG_ATTEMPTS` budget hasn't separately run
out). Not resolved in this document; likely resolution is to force the
attempt terminal (`unavailable` if no partial evidence, `completed` if
partial evidence exists) using the same table's `CLARIFICATION_REQUIRED`
row, but this needs confirming against the outer loop's actual budget
interaction before implementation.

## Subquery-level `resolution_status`: deterministic rollup, not a second LLM opinion

```
per-attempt "does this fully cover the subquery" predicate:
  rag:                            result.retrieval_state == "SUFFICIENT"
  api / deeplink / service_request:  status == "completed"   (binary by construction)

compute_subquery_status(subquery):
  attempts = subquery.tool_attempts

  if any(a.status in {"pending", "running"} for a in attempts):
      return "PENDING"

  if any(fully_covers(a) for a in attempts):
      return "RESOLVED"

  return "OPEN"   # all attempts terminal, none fully covers it
```

This alone is airtight and needs no LLM: it's a direct read of data our own
deterministic tool code already produced. Applied to `subquery_2` from the
traced turn: `rag` → `PARTIALLY_SUFFICIENT` (doesn't satisfy the
predicate), `api` → `completed` (does) → `RESOLVED`. No separate judgment
call is asked to reconcile these — there is nothing left to reconcile.

### `OPEN` still splits into `NEEDS_TOOLS` vs. `EXHAUSTED` — and that decision already exists elsewhere

Distinguishing "try another capability" from "nothing more can help"
requires matching a tool's scope description against the subquery's actual
need — genuine semantic judgment. But `node_03a`'s planner call already
makes exactly this judgment, every round, when it decides whether to
propose a new candidate task for an `OPEN` subquery. Rather than a second,
independently-fallible LLM call (today's `node_04a`) re-deriving the same
judgment and risking disagreement with the first, `NEEDS_TOOLS` /
`EXHAUSTED` should be **read off of `node_03a`'s own output**: `OPEN` +
"the planner proposed a new attempt this round" = `NEEDS_TOOLS`; `OPEN` +
"the planner proposed nothing new, or the round/task budget is spent" =
`EXHAUSTED`. This means exactly one place in the whole pipeline ever makes
a "does this capability apply" judgment call — everything downstream of it
(resolved-or-not, which attempts to cite, whether to apologize) is a
deterministic read of the attempt list. `node_04a`'s role shrinks
accordingly; its exact remaining scope (if any) is an open implementation
question, not decided in this document.

## Relevance-first dispatch: a cost tiebreak, not a procedure

Problem #2 in "Why this exists": `node_03a` used to auto-create a `rag`
attempt for *every* subquery in round 1, unconditionally, in code, before
the LLM was even consulted — other tools were only ever additive to that
default, never a replacement for it. This wasted the most expensive part of
a turn: a personal-data subquery like "what's my loyalty tier" would get a
parallel RAG attempt that retried 3–4 internal rounds chasing a tangential
FAQ chunk, before `api` answered it outright and the RAG evidence was
discarded unused.

The fix is **not** a fixed procedure ("try cheap tools first, fall back to
RAG second") — that was considered and rejected during design, because it
would replace one hardcoded rule (RAG always) with another (a fixed
priority order) rather than actually letting the model reason. Instead:

- **Every round applies the exact same judgment**, whether it's the first
  or the third: for each subquery still `OPEN`, decide which registered,
  enabled tools are genuinely *relevant* — RAG is an ordinary candidate in
  this same call now, not auto-created beforehand. There is no separate
  "round 1 wording" vs. "round 2 wording"; the same prompt, re-invoked with
  updated context each time (what's already been tried, and why it fell
  short), naturally produces "try what's left" behavior without needing to
  be told that's what round 2 is for. `MAX_TOOL_PLANNING_ROUNDS` is
  unchanged — convergence is an emergent property of the relevance
  judgment, not a designed phase count.
- **Relevance is the model's job. Cost is not** — with one narrow
  exception. The model is given each tool's cost characteristic as a plain
  fact (`api`/`deeplink`/`service_request` are single deterministic
  operations, effectively free; `rag` requires an internal iterative
  process and is the only tool with real cost), and for every `rag`
  candidate it proposes, it must tag `rag_role`:
  - `needed` — no other registered tool covers this subquery at all.
  - `complementary` — another tool also covers this subquery, but
    explaining the underlying policy/procedure has independent value
    beyond what that tool does (the manual method is never made obsolete
    just because a shortcut exists — this is the same principle behind
    `_available_response_shortcuts`'s closing-sentence mechanism).
  - `redundant` — another tool fully covers this subquery, and RAG's
    evidence would only restate what that tool already provides.
- **A small deterministic pass, not the model, decides whether a
  `redundant` tag survives.** `node_02e`/`node_03c` batch every currently-
  pending `rag` attempt into one model call per round regardless of how
  many subqueries are in it (see the cardinality note above) — so once a
  RAG batch is happening anyway this round because some other subquery is
  genuinely `needed`, including an additional `redundant` subquery in that
  same batch costs close to nothing. Initiating a whole batch solely for
  one droppable subquery does not. Concretely: scan the round's newly
  proposed attempts once; if any `rag` candidate is tagged `needed`, keep
  every `redundant` candidate too (they're now free); otherwise, drop every
  `redundant` candidate. `needed` and `complementary` are never dropped,
  regardless of batch economics — dropping a `complementary` one would
  remove genuine, independent value just because it happened to be cheap
  to skip, which was never the point.
- **A missing or malformed `rag_role` defaults to `needed`** — the safe
  direction. Since RAG is the fallback source of last resort, an
  under-tagged candidate must never be the one the cost tiebreak
  accidentally cuts.

This keeps the model's job purely semantic (relevance, and — the one place
real judgment is still required — whether an explanation is independent or
redundant, which is not a cost question at all) while keeping the
economics decision fully deterministic and auditable
(`tool.task.rag_redundant_dropped` is logged whenever the tiebreak fires).

## Reliability hardening: making the planner's structured output actually match its own rules

Two live-traced bugs, both eventually root-caused to the *schema* the
planner's LLM call decodes against, not to the prompt wording — worth
recording since prompt-only fixes provably could not have worked here.

**Bug 1 — the model could not physically emit `service_request_id`,
`destination_id`, or `field_id`.** `node_03a`'s call uses Gemini's
structured output (`response_schema`), which constrains the model to
*only* the fields a Pydantic schema declares — a field missing from the
schema is not merely discouraged, it is structurally impossible to decode,
regardless of what the prompt's rules say. `ToolTaskInput`
(`llm_service.py`) declared only `customer_component`, so a
`"service_request"`/`"deeplink"`/`"api"` task's `input` could never carry
its required id field no matter how the prompt was worded — the model was
forced to echo `customer_component` for every `tool_name`, because that
was the only field the decoder could produce. Confirmed live: two rounds
of prompt-only fixes (an explicit prohibition, then a contrastive
right/wrong example) produced byte-identical wrong output across every
retry, which is the signature of a hard decoding constraint, not a soft
LLM tendency. Fixed by adding the three missing optional fields to
`ToolTaskInput`, each documented as belonging to exactly one non-`rag`
tool type.

**Bug 2 — the id, once reachable, still wasn't constrained to a real
registry value.** The ids named above are already orchestration-assigned
(never LLM-invented — see `service_request_registry.py`'s own docstring),
but as plain `str` fields the schema couldn't guarantee the *value*
matched one, only that a string landed in the right field. `node_03a`
builds a structured-output schema fresh per call now
(`_tool_calling_plan_schema`, `nodes.py`), whose id fields are a
dynamically-constructed `Optional[Literal[...]]` over exactly that
request's live registry ids (`enabled_tool_capabilities()`), passed to
`call_llm_router` via a new `response_schema_override` parameter (bypassing
the static, prompt-content-matched schema selection for this one call).
An invented, malformed, or copied-from-elsewhere value is no longer
rejected after the fact by `node_03a`'s allow-list check — it's
impossible to decode in the first place, the same guarantee structured
output already gave `tool_name`/`operation`. One live API constraint
worth recording: Gemini's controlled generation rejects an empty string as
an `enum` member outright (`400 INVALID_ARGUMENT`); the "not this
`tool_name`" sentinel is `None`/`null` (`Optional[...]`), never `""`.

**A related contradiction this made visible, not created by it:** a
subquery is commonly planned with `rag` (often tagged `"complementary"`)
alongside a real tool in the same round. Once the real tool started
reliably succeeding after the fixes above, RAG's own empty result for the
same subquery could still surface as a no-info claim sitting right next to
that tool's real answer. See `DECLINE_CLARIFICATION_DESIGN_v1.0.md` §6 for
the fix (`_resolved_by_other_tool`) — it belongs to the no-info/decline
design, not the dispatch mechanics here, but the trigger condition is
exactly the multi-tool-per-subquery planning this section describes.

## Triage decomposes per-component, not per-turn (node_02d)

`financial_subqueries` used to only exist on a whole-turn `VALID_REQUEST`
classification — any non-financial or ambiguous content elsewhere in the
same turn was reduced to a thin, discarded label (`non_financial_context`)
with no real content, since triage's gate operated on the *entire turn* at
once. A mixed turn ("how to apply for a loan, and have you got my answer
for the question I asked earlier?") meant the financial part got decomposed
normally while the rest was silently unaddressable downstream — traced live
as a real bug (a customer's deliberate follow-up reference was dropped
entirely, with no mechanism able to answer, resolve, or clarify it).

Fixed by moving the gate from whole-turn to per-component: `node_02d`
always decomposes the turn into one or more components (an exhaustive
partition, same principle as `node_02e`'s own `SEARCH`/`CLARIFY`/`DECLINE`
split one node downstream), each tagged its own `resolution` —
`TOOL_ELIGIBLE` (any real banking ask, however vague — proceeds exactly as
before), `NON_FINANCIAL` (tone-only content with no ask — a greeting,
thanks, feeling, or genuinely unrelated remark), or `CLARIFICATION`
(reserved narrowly for when the category itself, for one component, is
genuinely undecidable between the other two — not merely because its
content is unclear, which is `TOOL_ELIGIBLE`'s job to carry downstream).
`NON_COMPLIANT`/`ESCALATION` remain whole-turn vetoes, deliberately not
folded into this — that would change customer-visible compliance/
escalation behavior and needs its own product decision, not a side effect
of this refactor.

Only `TOOL_ELIGIBLE` components ever populate `financial_subqueries` and
proceed to `node_02e`/RAG/tool-calling — this is what preserves triage's
original cost-saving purpose (keeping expensive RAG away from content that
doesn't need it), now enforced per-component instead of per-turn.
`NON_FINANCIAL`-tagged components populate a new list,
`conversational_tasks` (`{subquery_id, customer_component}`, same shape as
a financial subquery, never carrying `tool_attempts`), and
`CLARIFICATION`-tagged components become a `clarification_tasks` entry
(originator `"triage"`, see `DECLINE_CLARIFICATION_DESIGN_v1.0.md` §4b) --
keyed to that component's own real `subquery_id` now, not a synthetic one,
since triage always decomposes before tagging.

Both bypass lanes rejoin at final response generation and audit as their
own entry kind (`_build_conversational_context`, mirroring
`_build_decline_context`/`_build_clarification_context`) -- the same
"compose, don't relay" treatment `clarification_tasks` already gets, and
the same coherent-blending mechanism (`source_task_ids` as a list) already
proven for clarification. A turn that's *entirely* `NON_FINANCIAL` still
gets the same cheap, narrow audit path and 3-strikes deterministic apology
fallback it always did -- see `_is_pure_conversational_turn` in `node_06`,
which replaces the old whole-turn `triage_status == "NON_FINANCIAL"` check
now that that status no longer exists (`triage_status` narrows to
`NON_COMPLIANT` / `ESCALATION` / `DECOMPOSED`). `node_12_non_financial_response`/
`_generate_conversational_response`/graph node `n26` were removed entirely
-- no longer reachable, since a pure-conversational turn now flows through
the same `n19` → `n8` (response generator) → `n20`/`n21` (audit) path as
any other turn.

**Verified live**, including a real three-way mixed turn ("how to apply for
a stock account? and have you watched startrek last night? also what's
your answer for the question we discussed last time?"): triage correctly
split it into a `TOOL_ELIGIBLE` stock-account component (real evidence
answer), a `NON_FINANCIAL` Star Trek component (honest tone-only reply, no
invented banking content), and a `TOOL_ELIGIBLE` cross-session reference
component that -- correctly not resolvable by any downstream node -- became
a genuine `intent_formulator`-originated clarification instead of being
silently dropped. Also confirmed: a pure-chitchat turn still skips `n14`
entirely (zero RAG/tool-calling cost); a pure product-decline turn is
unaffected; a pure-conversational turn that fails audit three times still
lands on the same deterministic apology fallback as before.

## What this replaces

- **Removed entirely**: `tool_tasks`, `tool_results` (flat, `AgentState`-level
  lists) — folded into `financial_subqueries[].tool_attempts`.
- **Removed entirely**: `search_tasks`, `task_results`, `task_retry_counts`,
  `task_rejected_parent_ids`, `task_partial_evidence`,
  `task_sufficiency_history`, `task_feedback` — folded into the owning
  RAG attempt's `result` (steps, retry state, rejected/partial parent ids
  all become fields of that one record instead of five separately-keyed
  top-level state lists).
- **Extended, not replaced**: `financial_subqueries` — same identity
  (`subquery_id`, `customer_component`), now also the owner of
  `resolution_status` and `tool_attempts`.
- **Possibly derived rather than stored**: `pending_tool_task_ids` — could
  become "attempts with `status == pending`" computed at dispatch time
  instead of a separately tracked list. Not decided.

## Explicitly not decided in this document

- ~~The exact new/narrowed responsibility of `node_04a`~~ — **resolved**:
  `node_04a`'s LLM call was removed entirely (see Implementation notes #2).
- ~~Whether `node_03a`'s round-1 behavior changes to allow skipping RAG~~ —
  **resolved**: see "Relevance-first dispatch" above.
- Migration strategy: big-bang replacement of the old state fields vs. an
  incremental path that keeps them alive alongside the new schema during a
  transition. (In practice, the implementation went with big-bang — see
  Implementation notes.)
- The `MAX_TOOL_PLANNING_ROUNDS`-vs-mid-retry edge case noted above. Still
  open — the relevance-first dispatch change did not touch this.
