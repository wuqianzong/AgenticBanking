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
| Normal RAG answer (Node 06) | Task-Level Approved Evidence (Node 03c-approved parent IDs) | Yes | Requests extra fields for the evidence/bridge split and `response_recovery_strategy` — see below. |
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

**Known open item, deliberately not yet fixed**: the separate `Node 12`
non-financial conversational path (`_generate_conversational_response`) can
still open with an unfulfilled promise of material information (for example
"Sure, I can give you an overview!" that is never actually followed by one),
even though its prompt already explicitly forbids promising material
information. Both checks in this document pass such a reply correctly, since
it genuinely contains no ungrounded claim and no conduct violation — the gap
is in the generator's own adherence to its instruction, not in either
Groundedness v2.0 check.

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
