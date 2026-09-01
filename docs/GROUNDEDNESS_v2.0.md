# Groundedness v2.0

## Purpose

Before this upgrade, "groundedness" and "compliance" were checked by a
different bespoke prompt for nearly every customer-facing surface: one for a
normal RAG answer, a separate one for the initial greeting, a separate one
for non-financial conversational replies, a separate one (in a different
file) for durable memory summaries, and no check at all for the escalation
case summary — even though its text is shown directly to the customer.
Conduct-style rules (no commitments, no personalized advice, no fabricated
authority) were not owned by any one of these; they were duplicated
inconsistently across several of the prompts instead.

Groundedness v2.0 replaces that with one principle: **anything that will
eventually reach a customer must pass the same two checks**, however it gets
there — a live answer, a greeting, a stored memory summary that later shapes
a future greeting, or an escalation case summary shown before a human
handoff.

1. **Evidence groundedness** (the factual axis): is every claim in the
   content traceable to its declared permitted evidence? "Evidence" is
   whatever the caller's route defines it as — approved RAG parents, a chat
   transcript, a memory source — the check itself is the same reasoning
   shape regardless of what the evidence actually is.
2. **Conduct compliance** (the conduct axis): is the content's behavior
   consistent with the bank's own conduct standards and MAS regulation? This
   axis is evidence-independent — a perfectly grounded statement can still be
   a conduct violation, and a conduct-compliant statement can still be
   ungrounded.

These are genuinely orthogonal, not two names for the same thing, which is
why they remain two separate checks (and, on the live outbound path, two
parallel graph nodes) rather than being collapsed into one.

## Shared implementation

`backend/app/core/groundedness.py` holds both checks as plain, stateless
functions any caller can invoke — a live graph node, or code that runs
entirely outside the graph (memory validation runs at session checkout, not
during a turn):

- `check_evidence_groundedness(content_block, evidence_bundle,
  evidence_description, route_specific_rules="", extra_output_fields="")`
- `check_conduct_compliance(content_block, route_specific_rules="",
  extra_output_fields="")`

Both share one prompt shell, one model call, one JSON-parsing path, and one
fail-closed error path (an unparsable or failed call always returns
`evidence_grounded: False` / `conduct_compliant: False` with a diagnostic
reason, never an exception the caller has to handle itself). A caller
supplies its own evidence and any genuinely route-specific reasoning nuance
via `route_specific_rules`; a route needing extra structured output beyond
the base contract (for example, the normal-answer route's bridge-inference
split, described below) requests it via `extra_output_fields`.

`backend/app/core/compliance.py` holds the two rule blocks these checks
inject:

- `MAS_REGULATIONS_CONTEXT` — the four MAS regulatory pillars (FAA, FEAT,
  TRM, AML/CFT Notice 626). Unchanged from before this upgrade.
- `BANK_CONDUCT_CONTEXT` — new. No commitments, no personalized advice or
  recommendations, no outcome/return predictions, no fabricated authority or
  banking-action claims, neutral non-persuasive tone. This consolidates rules
  that were previously duplicated, inconsistently, across several of the
  bespoke prompts this upgrade replaced. Updating the bank's conduct standard
  now means editing this one constant; every caller of
  `check_conduct_compliance` picks up the change automatically.

## Integration points

| Route | Evidence bundle | Conduct check | Notes |
|---|---|---|---|
| Normal RAG answer (Node 06) | Task-Level Approved Evidence (Node 03c-approved parent IDs) | Yes | Requests extra fields for the evidence/bridge split and `response_recovery_strategy` — see below. A failure on this route now drives the Per-Task Outbound Response Revision Loop, not a full redraft — see its own section below. |
| Initial greeting (Node 06) | Greeting Memory (last-session + baseline summary) | Yes | |
| Non-financial conversation (Node 06) | None — the route permits no factual evidence at all; only a courteous, non-material acknowledgement is grounded | Yes | |
| Memory summaries — rolling, last-session, five-session baseline (`memory_service.py`, outside the graph) | The source transcript being summarized | Yes | Requests one extra field, `has_clarification_dependent_content` — a memory-specific retention rule that is neither an evidence nor a conduct question, folded onto the evidence call rather than justifying a third call. |
| Escalation case summary (`_build_escalation_case_summary`) | The trusted current-session transcript | Yes | New in this upgrade — previously had no groundedness or compliance check at all, despite its `main_pursuit`/`unresolved_issues` text being rendered directly in the customer-facing escalation-confirmation UI block. Falls back to the existing deterministic summary on failure; no retry loop, since escalations are infrequent and a safe deterministic fallback already existed. |

Every route's **external contract is unchanged** — `node_06_outbound_audit_join`,
the redraft loop, and every other downstream consumer still read the same
field names (`answer_grounded`, `clarification_grounded`,
`clarification_connected`, `response_recovery_strategy`, `passed`,
`answer_compliant`, `clarification_compliant`, ...) they always did. Only the
*implementation* moved onto the shared functions; nothing downstream needed
to change.

### The normal-answer route's extra structure

The normal RAG answer is the most complex route, and keeps all of its
pre-existing nuance as `route_specific_rules`/`extra_output_fields` on top of
the shared base contract — nothing was dropped in the migration:

- The evidence/bridge split (`answer_evidence_grounded` vs.
  `answer_bridge_grounded`): direct factual support is one check, and a
  bounded logical inference from that support to the resolved subquery is a
  separate one, so a rejection can identify which layer actually failed.
- The courtesy-context carve-out: a brief conversational acknowledgement is
  grounded on its own terms and is not held to the evidence bar.
- `response_recovery_strategy` (`NO_STRATEGY` / `REMOVE_UNSUPPORTED_CONTENT`
  / `NARROW_TO_EVIDENCE_SCOPE` / `CLARIFY_FROM_ALLOWED_CONTEXT`): an internal
  behavior constraint for the next redraft attempt, never evidence or
  customer-facing wording.
- Decline-input and intent/terminal-clarification sourcing rules.

## Response-generator interaction

Everything in this section describes the **whole-response redraft path** --
still the only mechanism for a `greeting`/`non_financial_response`-scope
turn. A `normal_response`-scope turn (real `search_tasks`/task_id
decomposition) no longer redrafts wholesale on any failure -- see the Per-
Task Outbound Response Revision Loop section below, which supersedes a
full redraft for that scope specifically.

A `NARROW_TO_EVIDENCE_SCOPE` or `REMOVE_UNSUPPORTED_CONTENT` rejection is
scoped removal, not permission to abandon the topic. Live tracing found that
across two or more redraft rounds, the generator could erode toward
responding with nothing but a bare restatement of the customer's own
message — technically grounded and compliant, but conversationally useless.
The fix, in `_generate_response_blocks`'s prompt: audit feedback is now
explicitly framed as identifying one claim to remove, never license to drop
the whole topic, and there is now a hard floor — never respond with zero
Horizon information when any evidence exists for the turn. A mismatch
between the customer's stated reason and evidence's actual condition is held
to the same standard as a Decline Input: name plainly what the available
information doesn't cover, then, unlike a decline, still share the closest
genuinely evidenced information as clearly-scoped optional context.

This principle is deliberately general — it isn't a check for any specific
topic, and applies to any future turn where approved evidence covers a
related-but-not-matching scenario.

### Bridge reasoning needed a generation-side guardrail too, not just an audit-side check

Live-tracing "my card is ugly, i want to replace it" found the generator
narrating a `card-management` navigation shortcut -- whose only supplied
`topic` is *"View & Manage Card screen — freeze, replace, enable overseas
usage"* -- as *"If you'd like to **get a new look** for your card, you can
easily request a replacement using the Manage Card button below."*
"Get a new look" is an invented outcome claim: nothing in the shortcut's
own `label`/`topic` says a replacement changes the card's design, so this
implicitly promised to resolve the customer's actual complaint (disliking
the current design) without any evidence that it does. The audit passed
it -- its own reasoning checked whether the *shortcut* matched the
*request*, and never separately scrutinized whether this specific *claim
about the shortcut* was itself supported, which is a stricter, no-bridging
bar than Direct Factual Grounding gets for retrieved evidence (a
shortcut's `label`/`topic` are a fixed, already-final description, not raw
material to reason further from).

The audit's own "Inferential Grounding (Bridge Check)" rule already
existed to catch exactly this class of problem, but only on the audit
side -- the generator had no equivalent instruction, so it had no reason
not to over-infer in the first place. Fixed the same way
`_GENERATION_CONDUCT_GUARDRAILS` already mirrors `BANK_CONDUCT_CONTEXT`/
`REGULATORY_LICENSING_CONTEXT` into generation: a new shared constant,
`_GENERATION_BRIDGE_GUARDRAILS`, injected into `_generate_response_blocks`,
`_generate_evidence_only_response`, and `node_04c_response_reviser` (the
same three sites `_GENERATION_CONDUCT_GUARDRAILS` already reaches),
stating the bounded-inference boundary up front and, for shortcuts
specifically, that naming what a shortcut *lets the customer do* is
grounded but implying what it *will produce or change* is not unless the
`topic` says so.

**First live retry over-corrected**: the generator dropped the claim but
also dropped all warmth, replying with a bare *"You can manage and
replace your card directly using the Manage Card button below."* --
technically correct but curt, because nothing told it a courtesy
acknowledgment of the customer's stated feeling was still safe to include.
The audit side already has exactly this carve-out (the Courtesy Context
Rule, above), just never mirrored into generation either. Added a
**Courtesy Acknowledgment** clause to the same constant: explicitly
*encouraging*, not just tolerating, a brief empathetic reaction to the
customer's own stated feeling as a genuinely separate move from the
capability statement -- one needs no evidence at all, the other stays
strictly bounded by it. Re-verified live: *"I'm sorry to hear you're not a
fan of how your card looks! You can request a replacement directly using
the Manage Card button below."* -- warm, accurate, and (per the
pre-existing "No Commitments" conduct rule, unaffected by this change)
still never promises the replacement request will actually succeed, only
that the mechanism to ask exists.

**Known open item, deliberately not yet fixed**: the separate `Node 12`
non-financial conversational path (`_generate_conversational_response`) can
still open with an unfulfilled promise of material information (for example
"Sure, I can give you an overview!" that is never actually followed by one),
even though its prompt already explicitly forbids promising material
information. Both checks in this document pass such a reply correctly, since
it genuinely contains no ungrounded claim and no conduct violation — the gap
is in the generator's own adherence to its instruction, not in either
Groundedness v2.0 check.

## Per-Task Outbound Response Revision Loop

### Context

Live-tracing a real turn ("what are the eligibility? i want to buy a car,
can i use it?") surfaced a real bug in the whole-response redraft path
described above. The turn decomposed into `task_1` (loan eligibility) and
`task_2` (can the loan be used for a car). Round 1's draft correctly
answered *both* -- including an explicit sentence confirming the
car-purchase use case -- but was rejected for an entirely unrelated reason:
`task_1`'s eligibility numbers were hallucinated literal values instead of
the required `{{faq_*}}` precision tokens (see `RAG_ETL_SPECIFICATION_v1.0.md`
for the ETL-time tokenization these tokens come from). By round 3 (the
version that finally passed), the token issue was fixed, but the
car-purchase confirmation sentence had silently vanished -- correct,
already-approved content became collateral damage from a full redraft
aimed at an unrelated problem.

Root cause: `_generate_response_blocks` regenerates the **entire** response
from scratch on every retry, with only raw feedback strings
(`draft_feedback_history`) as guidance. Nothing preserves content the audit
never flagged. The fix, worked through over several rounds of design
review: stop trusting the generator's own discipline to "only touch what's
wrong." Make the *deterministic system* -- not any LLM call -- the sole
authority over which content is locked in and which is still open, and give
each LLM call its narrowest possible job (generate once; audit the whole
thing; revise only what's flagged). This directly mirrors this codebase's
own established pattern for retrieval retries
(`_exclude_rejected` in `rag_engine.py` permanently excludes an
already-rejected candidate) -- this is the mirror image: permanently keep
what's already been approved, unless a fresh, independent audit round
legitimately finds a real problem with it, in which case the finding is
honored, never suppressed.

### Design

1. **First generation round is unchanged.** `node_04b_response_generator`
   (`"n8"`) still produces the entire response in one call. Every block
   already carries a `blockId` and `sourceTaskIds`.
2. **The audit still assesses the whole response every round**, never a
   partial slice -- a partial audit would still need full-response context
   to judge coherence/duplication, so there's no real efficiency gain, and
   only a whole-response audit can catch a problem that only exists in
   combination (duplication, contradiction between an approved and a
   newly-revised section).
3. **Every violation -- both compliance and evidence-groundedness -- is
   tagged with the `task_id`(s) it concerns.** The evidence axis already
   did this (`grounding_violations: [{"task_id": "task_1", ...}]`);
   `conduct_violations` gained the same field, defaulting to `""` only for
   a genuinely response-wide issue (e.g. overall tone) not traceable to a
   single claim.
4. **A deterministic (pure Python, no LLM) partition step runs after every
   audit round** -- `_partition_audited_response` (`nodes.py`). A block is
   "approved" only if none of its `sourceTaskIds` appear in any violation
   this round and there is no response-wide (`task_id: ""`) violation;
   otherwise every task_id the block names becomes "pending." A block's own
   `sourceTaskIds` list is its addressability unit: this is what keeps a
   scope-expansion companion pair (`task_1`/`task_1_scope`, which the
   Scope-Expansion Honesty Rule requires woven into one sentence) revising
   together, since they already share one block.
5. **A new, dedicated revision node** -- `node_04c_response_reviser` (graph
   label `"n8b"`) -- is invoked only on retry, replacing re-invoking full
   generation. Its only job: given the full current response as read-only
   coherence context, plus the specific violations and evidence for the
   pending blocks only, output revised content for *those blocks only*. No
   UI-structure job -- it may not change a block's type, add/remove/reorder
   blocks, or touch `next_actions`.
6. **Deterministic reassembly happens before the next audit call, not
   after the revision node** -- `_reassemble_response_blocks` (`nodes.py`).
   The code takes the stored approved blocks verbatim and splices in only
   the reviser's output for the pending block ids, reading it strictly as a
   keyed lookup -- anything else the reviser's raw output might contain is
   never read. If the reviser drops a block it was asked to revise, that
   block's *prior* content is kept rather than vanishing. This reassembled
   candidate -- never the reviser's raw output directly -- is what the next
   audit call actually sees, which is what makes "the reviser cannot change
   what's approved" a structural guarantee rather than a prompt
   instruction.
7. **The audit can still legitimately reopen a previously-approved
   task_id** if it finds a genuine new problem with it. The prompt tells
   the audit which task_ids were already approved this turn
   (`previously_approved_task_ids_this_turn`), framed explicitly as a
   noise-reduction hint, never a constraint that could suppress a real
   finding -- the deterministic system honors such a finding by moving that
   task_id back to pending, never ignores it.
8. **Termination**: no pending blocks -> assemble the final payload from
   the approved set, proceed to `"n22"` (precision substitution) as usual.
   Retry cap exhausted -> degrade via the existing `"n25"`
   (`node_11_deterministic_clarification_fallback`) path, unchanged.
9. **Scope is deliberately narrow**: only `audit_scope == "normal_response"`
   turns (real `search_tasks`/task_id decomposition) use this path.
   Non-financial/pure-chitchat turns keep the whole-response redraft logic
   completely unchanged -- those turns are short, single-element, and don't
   have the multi-independent-task structure this bug depends on.
10. **Retry cap stays one turn-wide counter** (`safety_retry_count`, cap 3),
    not per-task. Every round -- regardless of how many task_ids are
    pending -- costs exactly one audit call and one reviser call; a
    per-task counter would let a multi-task turn spend far more rounds than
    today's cost/latency envelope assumes, for no benefit.

### One canonical vocabulary for the revision node's I/O

An early version of `node_04c_response_reviser` handed each pending block
to the model in its full, noisy stored shape (every block type carries
fields it never uses -- a checklist block also always carries
`contextText`/`text`/`progressTitle`/`stepDetails`/`currentStep`/
`totalSteps`, all dummy defaults), then asked for a *differently-named*
output schema (`context_text`/`answer`/`list_items`/`progress_steps`).
Live-tracing the first real production reopen of this path (a PayNow
transfer-limit turn hallucinating `S$200,000`/`S$5,000` in place of
`{{faq_6f4ef9e6_001/002/003}}`) showed the reviser correctly restored the
tokens, but comparing its raw output to what was actually merged revealed
it had echoed the block's own native field names (`listTitle`, `listIntro`)
back rather than the output schema's names -- and the mapping code, which
only recognized the schema names, silently dropped those two fields. It was
harmless that time only because the actual fix lived entirely in `items`,
a field both naming conventions happened to share; a genuine title/intro
edit would have been silently lost.

Fixed at the root rather than by teaching the mapper both conventions:
`_simplify_block_for_revision` (`nodes.py`, keyed by
`_BLOCK_EDITABLE_FIELDS`) strips each block down to only the fields
meaningful for its own type before it's ever shown to the model, and the
prompt asks for that *exact same shape* back -- one vocabulary, used
identically for input and required output. The merge step is now a plain
`{k: v for k, v in entry.items() if k not in ("block_id", "type")}`, not a
30-line per-field translator, and there is no longer a class of "field name
the model might use that the mapper forgot to handle." Verified directly
against the live PayNow payload: a simulated genuine title/intro change now
propagates correctly instead of being silently dropped, with the token
restoration behaving identically to the live case.

### Verified against real production data

The PayNow scenario above is the first live, unforced reproduction of the
original collateral-damage bug's root cause (a Precision Reference Contract
violation) since this redesign shipped, and it resolved exactly as
designed: round 1 hallucinated the currency values; the partition correctly
scoped `pending_task_ids` to `task_1` alone; `node_04c_response_reviser`
fired (confirmed via its distinct "FLEXIBLE RESPONSE REVISION (partial
retry -- edit in place only)" prompt appearing in `system.jsonl` for the
first time); the reassembled response passed a full, fresh re-audit on the
very next round. A separate multi-tool-type turn (one RAG task needing its
own two-round retrieval retry, alongside two unrelated deterministic
deeplink shortcuts in the same response) also traced cleanly, confirming no
interference between the RAG retry ladder and this response-level
mechanism.

Also fixture-verified directly (no LLM call needed, pure deterministic
logic): a violation on one task_id never touches an unrelated task's
block; a block shared by two task_ids (a scope-expansion companion pair)
pulls both into pending together on either one's violation; a response-wide
(empty `task_id`) violation reopens every block; a block the reviser
silently omits keeps its prior content instead of vanishing, including when
simulating a total LLM failure; and a previously-approved task_id
legitimately reopened by a genuine new finding is honored, not filtered
out, across a full three-round scripted scenario.

### Files touched

`backend/app/orchestration/state.py` (`approved_response_blocks`,
`pending_task_ids`, `response_wide_violation_this_round`,
`task_violation_history`); `backend/app/orchestration/nodes.py`
(`_partition_audited_response`, `_group_violations_by_task_id`,
`_reassemble_response_blocks`, `_normalize_conduct_violations`,
`_flatten_response_block_text`, `_apply_response_completeness_nets`
(extracted from `_generate_response_blocks`'s tail, now shared),
`_BLOCK_EDITABLE_FIELDS`/`_simplify_block_for_revision`, new
`node_04c_response_reviser`; `node_06_outbound_combined_audit_gate` gains
`task_id` on `conduct_violations` and the previously-approved-task-ids
framing; `node_06_outbound_audit_join` branches on `audit_scope` into this
new partition path vs. the original whole-response path;
`node_05_outbound_deterministic_gate`'s leak-veto fail branch resets the
new state fields); `backend/app/orchestration/graph.py` (new `"n8b"` node
and `route_n21` branching).

## Reasoning-before-verdict field order

`node_06_outbound_combined_audit_gate`'s combined prompt used to ask for
`conduct_compliant`/`evidence_grounded` before their own `conduct_reason`/
`evidence_reason` fields. Since an LLM call outputting JSON generates the
requested fields in the order they're requested, this meant the model
committed to each verdict *before* writing anything explaining it -- the
reason was pure post-hoc narration of a decision already made, not
something that could have shaped the decision itself. Reordered so each
axis's `_reason` is written first, with an explicit instruction telling the
model to reason there before deciding: "a verdict decided first and
explained afterward is a rationalization, not a judgment." This call isn't
schema-enforced (see the Files-touched note below), so the explicit prompt
instruction is doing real work here, not just documentation -- unlike
`TaskSufficiencyItem` below, where the schema itself enforces the order.

The same fix applies to `TaskSufficiencyItem` (`node_03c`'s sufficiency
auditor, `llm_service.py`): `reason` moved to be declared before `status`/
`approved_parent_ids` in the Pydantic schema itself. Since this call *is*
schema-enforced (Gemini structured output via the `TaskSufficiencyEvaluation`
response schema), this reorder is a stronger guarantee than a prompt-only
instruction -- the model cannot emit `status` before `reason` at all,
regardless of what the surrounding prompt text says.

Both are free changes -- no new fields, no added tokens, same content just
reordered -- applied only where a `reason` already existed on the routine
(non-exceptional) decision path. Live-verified: the sufficiency auditor's
raw output now shows `reason` genuinely preceding `status` on every round,
and the combined audit's raw output shows `conduct_reason` preceding
`conduct_compliant`.

A related, separate finding surfaced while making this change: this
combined-audit call passes no `response_schema_override`, and the marker
string `call_llm_router` checks to auto-select a schema
(`"ANSWER GROUNDEDNESS V2 AUDIT"`/`"NON-FINANCIAL CONVERSATION GROUNDEDNESS
AUDIT"`) no longer matches this prompt's actual text
(`"GROUNDEDNESS v2.0 — COMBINED CONDUCT & EVIDENCE AUDIT"`) -- so unlike
every other structured call in this codebase, this one runs fully
unconstrained JSON, relying entirely on the model following the literal
example in the prompt. Not fixed here (a different, larger change: it would
need a dynamic schema matching this call's two variable extra-field sets);
flagged for a future pass.

## Observability

- `outbound.groundedness.completed` (log) — unchanged event name and fields;
  `scope` still distinguishes `greeting` / `non_financial_response` /
  `normal_response`.
- `outbound.compliance.completed` (log) — unchanged.
- `memory.validation.completed` / `memory.validation.failed` (log) — now
  logs `stage: "groundedness_v2"` instead of the retired `"gemini_semantic"`
  stage name.
- `escalation.case_summary.rejected` (log) — new. Fires when either check
  fails for a generated case summary, before the deterministic fallback is
  used; records both axes' pass/fail and reasons.
- `response.reviser.completed` (log) — new (Per-Task Outbound Response
  Revision Loop). Fires once per `node_04c_response_reviser` call; records
  `pending_block_ids`, `revised_block_ids`, and `response_wide`.

## Verified against real production data

Tracing a repeated live scenario (a card-replacement request for a reason —
aesthetic dissatisfaction — the approved evidence doesn't cover) confirmed
both checks correctly reject an added exclusivity claim on one draft and a
genuinely unsafe instruction (telling the customer to falsely select
"Damaged" as their reason) on the next — both real catches, not
false-positives. Before the response-generator fix above, the third draft
degenerated to a bare restatement of the customer's message; after it, the
identical scenario resolved on the first generation attempt with an honest,
correctly-scoped answer. Session-checkout memory validation (`last-session`
and `five-session baseline`) was independently confirmed using the shared
functions via the `stage: "groundedness_v2"` log field.

A separate, later live trace of a card-replacement turn -- this one routed
entirely through a navigation shortcut rather than a service_request, and
requiring no retry at all, since both audit passes were fooled by the
original draft's invented "new look" outcome claim (see "Bridge reasoning
needed a generation-side guardrail too" above) -- confirmed the fix in both
directions on real replays of the identical turn: the pre-fix draft's
unsupported claim, the immediately-post-fix draft's correct but overly
curt reply, and the final draft after adding the Courtesy Acknowledgment
carve-out, which passed compliance and groundedness cleanly on the first
generation attempt while remaining both accurate and warm.
