# Decline, Clarification, and No-Info Resolution v1.0

**Status:** Implemented and live-verified end-to-end. This is a living
document — update it in place as anything changes.

## 1. What this covers

How a turn that can't be answered from real evidence — because the topic
is out of scope (DECLINE), the request is ambiguous (CLARIFY), or a search
was genuinely attempted and found nothing (NO_INFO) — is resolved,
generated, and audited. The design went through several rounds since the
first version of this document: an initial fix to base-product
identification and honest DECLINE wording, then a fully separate
LLM-phrased-and-audited path for CLARIFY/DECLINE text, and finally the
current design below, which unifies all of this with ordinary answer
generation and removes free-form LLM phrasing from the evidence itself
entirely.

## 2. Base-product identification (Rule 2, PRODUCT) — unchanged

Human intuition strips a qualifier before checking whether a product
exists: "business credit card" is still fundamentally a credit card. The
prompt teaches this as a two-step, generalizable move instead of a
case-by-case list of qualifiers:

1. Strip any descriptive qualifier, tier name, or marketing brand from the
   wording (e.g. "Flexible Loan" → base family "loan") and match the base
   family, not the qualified name, to a Matrix noun.
2. Classify what kind of qualifier it was:
   - **Tier/brand/plan name** (e.g. "Flexible Loan", "Gold Card") — the
     matched base Matrix noun becomes PRODUCT immediately; the qualifier
     doesn't need separate clarification. Whether that exact named variant
     is actually covered is left to the sufficiency auditor downstream,
     which has real retrieved evidence to check it against.
   - **Non-individual customer segment** (business, corporate,
     institutional) — this does *not* become PRODUCT for SEARCH purposes.
     This FAQ corpus was confirmed, by direct inspection of the live vector
     store (2,118 chunks; zero contain "business credit card" or "business
     account"; the entire `product_tag` taxonomy is exactly the Matrix's
     individual-customer product set), to be scoped exclusively to
     individual/personal banking. A business-segment qualifier is treated
     as a scope boundary, not an ambiguity to search around.

`customer_component` — the wording judged by the evidence audit — is
explicitly preserved unchanged throughout; only the internal
`target_commercial_product` extraction is affected by base-product
stripping.

## 3. Three evidence categories, all deterministic — never LLM-phrased

Three helpers in `nodes.py`, none of which ever call an LLM:

- `_decline_evidence_text(category)` — looks up a fixed sentence in
  `_DECLINE_CATEGORY_EVIDENCE` keyed by `category`:
  - `"out_of_scope_product"` — the base product family has no Matrix entry
    at all (margin trading, crypto). *"This is not a product or service
    Horizon offers."*
  - `"individual_customers_only"` — the base product exists, but the
    component names a non-individual segment. *"This assistant's
    information covers individual, personal banking products only;
    business or corporate banking questions aren't covered here."* This
    never asserts whether Horizon actually does or doesn't serve that
    segment in reality — only that this assistant's information doesn't.
  An invalid or missing category safely defaults to `out_of_scope_product`
  (triage's own separate decline vocabulary — `non_compliant`/
  `non_financial` — is a different enum entirely and never accepted here).
- `_clarification_evidence_text(unresolved_request)` — always
  `f"Could you clarify what you mean by \"{unresolved_request}\"?"`. Since
  `unresolved_request` is already a trusted, extracted term (not free
  prose), this is exactly as safe as a fixed template while still naming
  the specific ambiguity.
- `_no_info_evidence_text(resolved_subquery)` — `f"I don't have
  information available regarding: {resolved_subquery}."`, or a generic
  fallback sentence when `resolved_subquery` is empty. Used both for a RAG
  task whose retries exhausted with zero usable evidence
  (`resolution_status == "CLARIFICATION_REQUIRED"`, see
  `TOOL_CALLING_v3.0.md`), and for a fresh SEARCH resolution whose
  product/intent pairing the matrix itself rejects (see §6 below).

**Why deterministic, not LLM-phrased (this document's biggest change from
v1's design):** an earlier version had `node_02e` phrase
`customer_facing_question`/`customer_facing_message` itself, audited by a
dedicated combined-audit call (`_audit_clarification_decline_blocks`,
removed — see §7). That closed the "who decides, who phrases" reliability
gap it targeted, but left a narrower one: nothing verified the *evidence
text itself*, only the final customer-facing response against it. Making
these three fields fixed templates over already-trusted, already-extracted
data removes that gap at the source — there's no longer a hallucination
surface for the evidence to fail, because there's no generation step
producing it. `candidate_replies` (the tappable chip guesses, see §4)
remain LLM-generated, since they're offered guesses a customer can dismiss,
not asserted facts — a different risk profile that doesn't need this same
treatment.

## 4. Clarification is generic, not product-specific

Any node that determines a request is genuinely ambiguous can trigger
CLARIFY — not only the intent formulator. `IntentClarificationTask` carries
`clarification_id`, `unresolved_request`, `reason`, and `candidate_replies`
(`List[ClarificationReply]`, 0–3 entries, most-likely-first). Each
`ClarificationReply` has:

- `label` — a short chip caption (e.g. "Personal Loan").
- `message` — a complete, natural sentence sent to the assistant if
  tapped (e.g. "I'm asking about a personal loan.") — never a bare word,
  since it becomes the next turn's actual user input.

Chips are populated only when genuinely inferable from context (e.g. a
term with exactly one plausible Matrix match); left empty when there's no
real basis to guess. They attach to whichever response block ends up
citing that clarification's `task_id` in `sourceTaskIds` — never a
separate mechanism from the block itself.

## 5. Unified response generation: one generator role, two sub-prompts

`node_04b_response_generator` always calls `_generate_response_blocks`.
That function checks whether any real evidence exists this turn
(`evidence_by_task`, `available_shortcuts`, `unavailable_service_requests`,
or `verified_api_context`):

- **Real evidence exists** (the common case, including a mixed turn with
  some real evidence and some CLARIFY/DECLINE/NO_INFO entries alongside
  it): the full `FLEXIBLE RESPONSE GENERATION` prompt runs, with
  clarification/decline/no-info entries described as evidence to relay
  faithfully (never raw material to reason from, unlike real retrieval
  evidence — see the prompt's own "Approved evidence... vs. these three"
  framing) alongside everything else. Style/personalization applies to
  every element uniformly.
- **Zero real evidence, only CLARIFY/DECLINE/NO_INFO entries** (a turn that
  resolves to nothing but a question, a boundary, or an admission of no
  information): `_generate_evidence_only_response` runs instead — a
  narrower `PRE-DECIDED STATEMENT COMPOSITION` sub-prompt whose only job is
  composing these already-decided statements into one coherent message.
  Its output schema has no alternative field to substitute a different
  answer into, which is what removes the reliability risk a fully
  open-ended prompt had here: earlier, node_04b's single open-ended prompt
  reliably gave a bland "I don't have information" answer instead of
  reliably asking a pending clarification, since that instruction had to
  compete with everything else in the prompt. With no alternative field
  available, there's no easier answer left to dodge into.

Both paths share **one** outbound audit (`node_06`'s existing compliance +
groundedness gate) — there is no separate audit mechanism for this content
anymore (see §7). `_normal_outbound_grounding_context` builds the same
"Intent Clarification Inputs" / "Decline Inputs" / "No-Info Inputs"
evidence lists the generator sees, so the audit checks exactly what the
generator was given.

Both generator paths are instructed to *rephrase* each entry's evidence
text naturally, in the customer's own style, rather than copy it
verbatim — verbatim is a last resort only, never the default. Both also
run a **deterministic completeness net**: after generation, any entry
whose `task_id` never got cited in the model's output gets its own
evidence text appended verbatim as a fallback block. This exists because
the model has been observed dropping an entry even when its prompt
explicitly forbids skipping any of them, and the audit meant to catch that
omission has also been observed rationalizing it away ("acceptable since
not yet resolved by the customer") instead of failing the draft —
completeness no longer depends on either model behaving.

## 6. Cross-tool contradiction: a subquery resolved by one tool must never also claim "no information"

A subquery is commonly planned with more than one tool in the same round —
e.g. `service_request` (register a real, working form) *and* `rag` (tagged
`"complementary"`, in case the FAQ corpus also has something useful to add).
Two situations can make RAG's own side of that turn assert "no information
available" for the very thing the other tool just resolved:

1. **RAG's formulation is scope-rejected.** `node_02e` proposes a fresh
   `SEARCH` task whose product/intent pairing `_normalize_search_task_scope`
   rejects. This never becomes a live `search_task`, so without a
   compensating entry it would vanish silently, reaching generation with
   zero evidence of any kind for that subquery.
2. **RAG's retries genuinely exhaust with nothing found**
   (`node_03c_sufficiency_auditor`, `resolution_status ==
   "CLARIFICATION_REQUIRED"`).

Both are rendered as a **NO_INFO entry, never a DECLINE** — a matrix
rejecting a RAG formulation, or RAG's own retries coming up empty, only
proves RAG couldn't answer it from FAQ text; neither proves the underlying
service doesn't exist. (An earlier version of fix #1 used `decline_tasks`
here, which both asserted the stronger, false claim that Horizon doesn't
offer the service, and routed the whole turn to `node_10`'s hard-rejection
path whenever it was the only thing produced that round — the wrong node
for "temporarily couldn't answer this from FAQ text," not "we don't do
this.") Fix #1's entries accumulate in a new state field,
`unresolvable_search_tasks` (`state.py`), following the same manual,
non-reduced accumulation pattern as `decline_tasks` so they survive a mixed
turn where `node_03c` also writes `task_resolution_instructions` for a
sibling task the same round. Both fix #1 and fix #2's entries feed into the
shared `_split_terminal_markers` helper, so the generator and the audit
always compute identical `no_info_evidence` wording for the same task.

But an unconditional NO_INFO entry is still wrong if the *same subquery*
already has a different, successful tool attempt this round: it would tell
the customer no information exists for something a working shortcut was
just registered for. Confirmed live — "How to update KYC information"
produced both a completed "Resubmit KYC Document" service request *and* a
no-info entry for the identical wording, and the generator surfaced the
false "no information" claim instead of the real shortcut. Both fix
sites now check `_resolved_by_other_tool(owning_subquery)` before creating
their entry, which looks only at that subquery's **non-`rag`** attempts for
full coverage (`_fully_covers`, the same binary-coverage helper the
`resolution_status` rollup uses). This is deliberately narrower than the
general `_compute_subquery_status` rollup: both check sites run from
*inside* RAG's own execution for that exact subquery (`node_02e`
formulating it, or `node_03c` auditing it, before `_finalize_rag_attempts`
marks it terminal later in the same node) — at that moment RAG's own
attempt is still `"pending"`, which would make the general rollup report
`PENDING` and never reach `RESOLVED`, regardless of what else already
covers it. This was confirmed live as the reason the first version of
this exact fix silently did nothing: it checked
`_compute_subquery_status(...) == "RESOLVED"`, which could never be true
at that point in the round.

**Two more sites needed the identical guard, found via live tracing after
this fix shipped.** The pattern generalizes to *any* place that manufactures
a failure-shaped entry for a subquery without first checking whether some
other tool already resolved it this round -- not just RAG's own no-info
paths:

- **The formulator's own `DECLINE` resolution.** A component the intent
  formulator judges out-of-scope can be the same subquery a different tool
  (`deeplink`, `service_request`, `api`) already fully resolved this same
  round -- the formulator's `SEARCH`/`CLARIFY`/`DECLINE` partition never
  sees other tools' results. `node_02e`'s `DECLINE` branch now checks
  `_resolved_by_other_tool(owning_subquery)` before appending to
  `decline_tasks`, skipping the decline (and setting the same
  `any_subquery_resolved_by_other_tool` flag used below) when it fires.
- **The generic "no valid retrieval task could be formed" fallback.** When
  `node_02e` produces zero `search_tasks` and zero real
  `intent_clarification_tasks`/`unresolvable_search_tasks`, it manufactures
  a catch-all `intent_clarification_1` entry asking the customer to
  clarify their whole request -- correct when nothing at all resolved the
  turn, but wrong when a scope-rejected RAG formulation's subquery was
  already fully answered by a deeplink: confirmed live, "how can I manage
  my profile info" produced a working `profile-personal-details` deeplink
  *and* a fabricated "please clarify" task for the same subquery, and the
  response generator surfaced both -- a customer-visible self-contradiction
  (an answer plus a request to clarify the thing just answered) that then
  failed groundedness three times in a row before falling through to the
  exhausted-retry fallback, for a turn that was never actually stuck.
  `node_02e` now tracks `any_subquery_resolved_by_other_tool` across the
  scope-rejection loop and skips manufacturing the fallback clarification
  whenever it's set, falling through to the same `"continue_plan"` status
  already used for "nothing new to search, but not stuck" turns instead.

## 7. What this replaced

- **`_audit_clarification_decline_blocks`** (a bespoke combined
  compliance+groundedness call over every LLM-phrased CLARIFY/DECLINE
  block, tagged by `blockId`) — deleted. `node_06`'s existing audit now
  covers this content via `_normal_outbound_grounding_context`'s enriched
  evidence lists; no separate audit call is needed once the content isn't
  independently LLM-generated outside the normal generation step.
- **`customer_facing_question` / `customer_facing_message`** LLM output
  fields on `IntentClarificationTask`/`DeclineTask` — removed from the
  schema entirely, replaced by the deterministic `*_evidence` fields (§3).
  Renamed away from the `customer_facing_*` naming specifically because
  that name risked the response generator and the groundedness auditor not
  treating them as evidence to check against.
- **`route_n19`'s special-cased `n25` branch** (routing a pending
  intent-clarification with no `search_tasks` straight to
  `node_11_deterministic_clarification_fallback`) — reverted.
  `_generate_evidence_only_response` (§5) now handles that exact shape of
  turn within `n8` itself, sharing the normal audit; `node_11` is reachable
  only via `route_n20`/`route_n21`'s own three-failed-retries condition
  now (see §10 — that condition's *destination* changed again since this
  was first written), and its own rendering was simplified to relay
  `clarification_evidence`/`decline_evidence`/`no_info_evidence` directly
  with no generation or audit call of its own (see `TOOL_CALLING_v3.0.md`
  for the retry-budget mechanics that lead here).

## 10. Hard escalation reserved for explicit requests only

`route_n20`/`route_n21` originally sent an exhausted-retry turn
(`safety_retry_count >= 3`) to `node_09_hard_escalation_broker` ("n23")
unless a separately-computed `clarification_fallback_required` flag was
set, in which case it went to `node_11` ("n25") instead. This meant a
turn's own repeated inability to pass its own audit — nothing to do with
the customer asking for a human — could still end a conversation in a
hard-escalation handoff screen. Confirmed live as an unnecessary outcome:
a turn whose RAG ladder had already produced a real, deterministic
`CLARIFICATION_REQUIRED` no-info entry still routed to `n23`, because
`clarification_fallback_required` (`preserve_answer` in
`node_05_outbound_deterministic_gate`) also requires the *answer* portion
to have separately passed compliance/groundedness — under the audit's
fail-closed default (see `GROUNDEDNESS_v2.0.md`), a transient error (e.g.
an upstream rate limit) makes that condition false too, even though `n25`
already had a perfectly good deterministic no-info entry ready to render.

Both routers now send *every* exhausted-retry turn to `n25`, unconditionally:

```
route_n20 / route_n21: return "n8" if count < 3 else "n25"
```

`n23` is reachable only via a customer's own explicit request now — triage's
`ESCALATION` status (`route_n2d`) or an accepted transfer confirmation
(`route_n3`) — never via the generation/audit retry loop. `node_11`
absorbs the difference: it already rendered real
`clarification_evidence`/`decline_evidence`/`no_info_evidence` directly
when any existed; for the one case that has none at all — a real
generated answer that simply never passed compliance/groundedness, with
no clarification, decline, or no-info entry to fall back on — it now
apologizes plainly instead ("I'm sorry, I'm not able to answer that right
now. Could you try again, or ask in a different way?") rather than
escalating a case that may just be a phrasing or evidence gap, not
something only a human agent can resolve.

**Every turn routed to `n25` is scored 0.0 for the rolling confidence
signal**, regardless of which of the two deterministic contents it ends
up showing. This closes a related gap found via the same live tracing:
`n23`/`n25` both reach `END` directly, skipping
`node_08_soft_escalation_analyzer` — the only node that normally appends
to `turn_escalation_history` — so a turn this badly failed previously
contributed *no* data point to the rolling-confidence window instead of
the 0.0 it deserved, quietly inflating the average by shrinking the
sample rather than dragging it down. `_append_failed_turn_escalation_history`
(`nodes.py`) force-scores every financial subquery 0.0 (never read from
`_score_subquery`, since internal resolution state is exactly what the
customer did not end up receiving) and appends the entry itself. See
`SOFT_ESCALATION_v1.0.md` §2 for the scoring model this feeds.

## 8. Files touched

`backend/app/core/llm_service.py` (`ClarificationReply`,
`IntentClarificationTask.candidate_replies`, `DeclineTask.category`),
`backend/app/orchestration/nodes.py` (`_DECLINE_CATEGORY_EVIDENCE`,
`_decline_evidence_text`/`_clarification_evidence_text`/
`_no_info_evidence_text`, `_generate_response_blocks`,
`_generate_evidence_only_response`, `_split_terminal_markers`,
`_resolved_by_other_tool`, `node_02e`'s scope-rejection and `DECLINE`
handling, `node_03c`'s `_rag_gap_still_matters`, `node_09`'s simplified
scope (explicit-escalation only), `node_11`'s generalized rendering plus
its plain-apology fallback, `_append_failed_turn_escalation_history`),
`backend/app/orchestration/state.py` (`unresolvable_search_tasks`),
`backend/app/orchestration/graph.py` (`route_n19` reverted; `route_n20`/
`route_n21` no longer route to `n23` on retry exhaustion — see §10).

## 9. Verification

Live-traced: "business credit card eligibility" and "annual fee for my
business credit card" (`DECLINE`, `individual_customers_only`, honest
wording); "margin trading account" (`DECLINE`, `out_of_scope_product`);
"personal credit card" (unaffected, normal `SEARCH`); a pure ambiguous
"tell me about the fee" turn resolving to `_generate_evidence_only_response`
with candidate-reply chips attached; a mixed "fee + loan application +
routing number" turn producing real evidence for two subqueries and a
`no_info` entry for the third within one `_generate_response_blocks` call,
sharing one audit; "can I change my address?" producing a `service_request`
+ `deeplink` pair correctly distinguished by kind in the response text; "can
I change my KYC material and also apply for a credit card" confirmed, after
the `_resolved_by_other_tool` fix, no longer producing a false "no
information available" claim about KYC alongside the real, already-
registered KYC resubmission service request.
