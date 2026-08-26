# Decline, Clarification, and Base-Product Resolution v1.0

**Status:** Implemented and live-verified end-to-end. This is a living
document — update it in place as anything changes.

## 1. What this covers

Three related fixes to `node_02e_intent_classifier_and_query_formulator`'s
SEARCH/CLARIFY/DECLINE resolution and how CLARIFY/DECLINE reach the
customer, all found while tracing real "business credit card" / "business
loan" style queries:

1. A qualified product name (e.g. "business credit card") was being
   declined outright as a nonexistent product, when the base product
   (`Credit_Card`) exists — it just needed a different resolution.
2. The fixed DECLINE sentence was flatly false in that case ("Horizon does
   not offer business credit cards" — nobody actually knows that; only that
   this assistant's FAQ doesn't cover it).
3. A CLARIFY-plus-DECLINE turn with nothing else to say produced an
   unnatural, template-stitched two-paragraph response.

## 2. Base-product identification (Rule 2, PRODUCT)

Human intuition strips a qualifier before checking whether a product
exists: "business credit card" is still fundamentally a credit card. The
prompt now teaches this as a two-step, generalizable move instead of a
case-by-case list of qualifiers:

1. Strip any descriptive qualifier, tier name, or marketing brand from the
   wording (e.g. "Flexible Loan" → base family "loan") and match the base
   family, not the qualified name, to a Matrix noun.
2. Classify what kind of qualifier it was:
   - **Tier/brand/plan name** (e.g. "Flexible Loan", "Gold Card") — the
     matched base Matrix noun becomes PRODUCT immediately; the qualifier
     doesn't need separate clarification. Whether that exact named variant
     is actually covered is left to the sufficiency auditor downstream,
     which has real retrieved evidence to check it against — not this
     step, which doesn't.
   - **Non-individual customer segment** (business, corporate,
     institutional) — this does *not* become PRODUCT for SEARCH purposes.
     This FAQ corpus was confirmed, by direct inspection of the live vector
     store (2,118 chunks; zero contain "business credit card" or "business
     account"; the entire `product_tag` taxonomy is exactly the Matrix's
     individual-customer product set), to be scoped exclusively to
     individual/personal banking. A business-segment qualifier is treated
     as a scope boundary, not an ambiguity to search around.

Throughout, `customer_component` — the wording shown to the customer and
judged by the evidence audit — is explicitly preserved unchanged. Only the
internal `target_commercial_product` extraction is affected by base-product
stripping.

## 3. DECLINE categories: an honest reason, not a guessed one

`decline.category` (new field on `DeclineTask`, `llm_service.py`) replaces
a single hardcoded `reason_category` (previously *always*
`"out_of_scope_product"`, regardless of what actually happened —
`nodes.py`'s parsing discarded the model's own free-text reason entirely
and mapped every DECLINE to the same "not offered" sentence). Now:

- `"out_of_scope_product"` — the base product family has no Matrix entry at
  all (margin trading, crypto — a fundamentally different instrument).
  Sentence: *"it's not a product or service Horizon currently offers."*
- `"individual_customers_only"` (new) — the base product exists, but the
  component names a non-individual segment. Sentence: *"this assistant's
  information covers individual, personal banking products only — business
  or corporate banking questions aren't covered here."* This never asserts
  whether Horizon actually does or doesn't serve that segment in reality —
  only that this assistant's information doesn't.

The category is validated against exactly these two values in `nodes.py`'s
parsing (an invalid or missing value safely defaults to
`out_of_scope_product`) — triage's own separate decline vocabulary
(`non_compliant`/`non_financial`) is a different enum entirely and is never
accepted here.

## 4. Response composition: independent blocks, not one stitched paragraph

`node_11_deterministic_clarification_fallback` (reached either after three
failed generator retries, or directly from `route_n19` when a pending
`intent_clarification_task` has no `search_tasks` at all to fall back on —
see `docs/TOOL_CALLING_v3.0.md`'s routing notes) used to flatten a
clarification and a decline into one `"\n\n".join(...)` string, using a
`scope` phrase ("the rest of your request") that assumed something else in
the turn had already been answered. When nothing else had been answered
(the common case for this node), the phrase was simply wrong, and forcing
two independently-templated sentences into one string never read as one
coherent thought.

**The fix accepts that this can't be smoothed into one perfect paragraph,
and stops trying to.** Each clarification and each decline is now its own
response block (`type: "clarification"`, the same rendering the frontend
already uses elsewhere) — shown as an independent message, not concatenated
prose. Removing the requirement that everything flow together as one
sentence removes the actual source of the awkwardness.

**Who writes the customer-facing wording**: `node_02e` itself, in the same
call that already decides CLARIFY/DECLINE — `IntentClarificationTask` and
`DeclineTask` each gained a `customer_facing_question` /
`customer_facing_message` field. This was chosen over a new dedicated
phrasing sub-call for two reasons: it adds zero new LLM calls (this
codebase's standing preference), and it structurally can't reproduce the
earlier reliability failure where the flexible generator, given a choice
between asking a hard clarifying question and giving an easy non-answer,
reliably chose the non-answer — here, the resolution (CLARIFY or DECLINE)
is already locked in by the time phrasing happens, so there's no
alternative, easier output for the model to dodge into. A fixed
deterministic sentence remains the fallback whenever the field is left
empty.

## 5. Auditing the new free-form text

Because these two fields are now genuine LLM prose (not just a short term
substituted into a fixed template), they need to honor this codebase's
standing invariant that *any* content reaching a customer passes both
Groundedness v2.0 checks (see `groundedness.py`'s module docstring) — which
the old, fully-templated version satisfied for free simply by being fixed
strings.

`_audit_clarification_decline_blocks` runs **one** compliance call and
**one** groundedness call over every LLM-phrased candidate in the response
together (not one pair per block), each tagged with its `blockId`, asking
the model to return exactly which `blockId`(s) failed
(`non_compliant_block_ids` / `ungrounded_block_ids` — reusing the
scope-tagged violation pattern already used elsewhere in this codebase,
e.g. `node_06`'s `answer_evidence_grounded` / `clarification_grounded`
split within one call). A failing block — or every block, if an audit
fails closed without naming a specific one (API error, or a top-level
failure with nothing localized) — falls back to its fixed deterministic
sentence; passing blocks keep their natural phrasing.

The retained answer (`safe_answer_for_fallback`, present only on the
three-retry path) is deliberately **not** included in this same audit
batch. It was already checked once, by `node_06`, against its real
supporting FAQ evidence at the moment it was drafted. Evidence linkage back
to that answer's approved parents isn't carried forward to `node_11` today,
so re-auditing it here would mean checking a factual answer with no
evidence to check it against — strictly worse than trusting the
evidence-aware check it already passed. Unifying this fully would require
carrying the answer's approved parent IDs forward alongside its text — a
separate, still-open piece of work.

## 6. Files touched

`backend/app/core/llm_service.py` (`DeclineTask.category`,
`customer_facing_question`, `customer_facing_message`),
`backend/app/orchestration/nodes.py` (`node_02e`'s PRODUCT/DECLINE rules,
`_DECLINE_CATEGORY_REASONS`, the DECLINE/CLARIFY parsing blocks,
`_audit_clarification_decline_blocks`, `node_11`'s full rewrite),
`backend/app/orchestration/graph.py` (`route_n19`'s new `n25` branch for a
CLARIFY-only, nothing-searchable turn).

## 7. Verification

Live-traced: "business credit card eligibility" and "annual fee for my
business credit card" (both `DECLINE`, `individual_customers_only`,
honest wording); "margin trading account" (unchanged `DECLINE`,
`out_of_scope_product`); "personal credit card" (unaffected, normal
`SEARCH`); a combined "business credit card fee + how to apply for a loan"
turn producing two independent, naturally-phrased blocks with the combined
audit confirmed running and correctly scoped (both real audit calls logged
against exactly the two candidate blockIds, both passing cleanly); the
audit's fail-closed behavior confirmed directly (API unavailable →
every block in the batch correctly falls back).
