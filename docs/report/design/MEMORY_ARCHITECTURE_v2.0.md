# Memory Architecture v2.0 (Proposed, superseded before implementation)

**Status: SUPERSEDED, never implemented.** This proposed replacing v1.0's
cross-session summaries with a richer per-task memory layer. That direction
turned out to be unnecessary: the project does not require cross-session
conversational continuity at all, so cross-session memory was dropped
entirely rather than redesigned. See `MEMORY_ARCHITECTURE_v3.0.md` for what
was actually built -- two on-demand deterministic checks at login, no
stored or generated cross-session content, no LLM call, no audit. This
document is kept only as a record of the reasoning that was considered and
rejected in favor of the simpler outcome.

## Why v1.0 needs replacing

v1.0 proactively generates and validates a `last-session` summary and
refreshes a `five-session baseline` for every user at session checkout,
regardless of whether either is ever read again. At the scale of a large
bank — millions of users, each producing many sessions — that means paying
LLM generation *and* two-axis Groundedness v2.0 validation cost for memory
that, for a large fraction of customers, is never subsequently retrieved at
all. It also means every future turn gets these summaries injected as
always-on advisory context whether or not that turn has any use for them,
which is the same "broad context regardless of relevance" imprecision this
project has spent considerable effort steering *search* strategy away from.

## Two domains that must not be merged

The core design error to avoid: treating a remembered fact about the past
the same way as current evidence. They are not interchangeable, because bank
policy drifts over time — the same `parent_id` that said "eligibility: 21
years old" when it was approved months ago can say "18 years old" today. A
customer asking "but you told me 21 before" is making a legitimate reference
to history, but answering them still requires finding out what is true
*now*, not replaying what was true *then*.

- **Memory (context domain):** a historical fact about the conversation —
  what was asked, what was said. This is true forever regardless of current
  policy, and its groundedness question is narrow: is this an accurate
  record of what actually happened? It is never used as evidence for a
  current-policy claim.
- **Search strategy (evidence domain):** owns every claim about what's true
  now. A parent_id recalled from memory is at most a retrieval hint about
  where to look first — it must still pass a fresh Node 03c sufficiency
  judgment before it can support an answer, exactly like a freshly retrieved
  candidate. Nothing recalled from memory is ever pre-approved evidence.

## Layer 1: the Store

One structured record per resolved task, persisted at the point it resolves
— not summarized, not compressed, just archived. This is not new
computation: every field already exists transiently in `task_results` and
`search_tasks` during normal turn processing and is currently discarded when
the graph session ends.

```text
turn_task_record
  { customer_id, session_id, timestamp,
    customer_component,                 # resolved subquery text
    target_commercial_product,          # matrix product tag(s)
    target_intent,                      # matrix intent tag(s)
    approved_parent_ids,                # Node 03c's verified evidence, if any
    resolution_status,                  # SUFFICIENT | PARTIALLY_SUFFICIENT | CLARIFICATION_REQUIRED
    delivered_text }                    # the actual customer-facing text shown
```

`delivered_text` is worth storing verbatim rather than reconstructing later
from `approved_parent_ids` — for memory's purpose specifically, "what we
actually told this customer" is itself the historical fact worth recalling,
independent of whether the underlying parent's content later changes.

No chunking subroutine is needed. The system already decomposes every turn
into exactly this unit as part of normal task formulation; persisting it is
archival, not a new NLP problem.

## Layer 2: the Index

The index deliberately mirrors `RAGEngine`'s own shape, rather than
inventing a new retrieval pattern: a metadata-filterable embedding
collection for candidate generation, full records hydrated from the Store
for whatever confirms them, and a judge that makes the final call. Recency
is the one addition, since "most recent for this customer" is meaningful for
this data in a way it isn't for a shared FAQ corpus.

1. **Recency** — most within-conversation references to "before" concern the
   last few turns. A plain "most recent N records for this customer" query
   costs nothing and covers a large share of real cases, with no embedding
   or filter step at all.
2. **Hard filter (product/intent)** — every record already carries the same
   matrix tags the RAG filter uses today, so this is a plain metadata
   condition, no embeddings required. Unlike RAG, this filter is
   **consumer-configurable, not universally mandatory**: search strategy
   applies it strictly, the same way it does for fresh retrieval, since a
   candidate has to be product/intent-valid to ever be admissible evidence.
   Memory can relax or drop it, since a genuinely useful piece of context
   (a stated preference, a mentioned account type) can be relevant to a
   conversation on a completely different product than when it was said —
   memory's bar is "useful to know," not "evidence-scope-valid."
3. **Cosine similarity** — a cheap way to surface candidates with a high
   *probability* of matching meaning, not a guarantee, which is exactly why
   it is never the final word — every hit still gets hydrated from the
   Store and judged. Symmetric to the FAQ corpus's existing multi-child
   pattern (`question_anchor`, `intact_answer`, ... all pointing at one
   `parent_id`), a single turn record may be worth indexing under more than
   one embedded representation, not one canonical string: the original raw
   user input, the resolved `customer_component`, and optionally a short
   topic label — each a separate embedded child row pointing at the same
   `turn_task_record`. This is what "might need chunking" resolves to in
   practice: multiple indexed representations per record, not splitting
   long text, since nothing here is long-form content the way an FAQ answer
   is.
4. **Hydration** — whatever a filter or cosine search surfaces is a pointer,
   never content on its own. The matched record is fetched in full from the
   Store before anything downstream sees it, exactly mirroring
   `_hydrate_parents_from_sqlite` today.

## Layer 3: two consumers, one index, disjoint judges

- **Search strategy** queries with the hard filter applied, retrieves
  matching past records' `approved_parent_ids`, and feeds them into the
  current task's RRF candidate pool. This is not new machinery — it is the
  existing `verified_session_parent_ids` mechanism (Search Strategy v2.7+),
  which already re-admits a prior candidate into RRF while still requiring
  a fresh sufficiency judgment. **The judge here is Node 03c, unchanged** —
  a cross-session candidate is judged exactly as strictly as a freshly
  retrieved one, which is what makes reusing a possibly-stale parent_id
  safe at all.
- **Memory** queries by topical similarity, with the hard filter relaxed,
  retrieves matching past records, and surfaces `customer_component` +
  `delivered_text` as advisory context ("previously discussed: X, told:
  Y"). **The judge here is new and deliberately lighter-weight than Node
  03c** — memory's question is "is this genuinely useful context for the
  current conversation," not "is this sufficient evidence for a financial
  claim," so it does not need the same bar, the same rejection categories,
  or necessarily even the same model tier. Its exact design is an open item
  below, not yet specified.

Whichever judge is used, neither one lets a candidate skip the judgment step
— cosine similarity narrows the field, it never decides on its own, and this
proposal only widens the existing `verified_session_parent_ids` mechanism's
reach from "the current session's last ten verified turns" to the full
cross-session index, not its standard of proof.

**How far a given strategy reaches into that index is a parameter, not a
fixed constant** — the same way `MAX_RAG_ATTEMPTS` and
`RRF_CANDIDATES_PER_ANGLE` are tunable today rather than hard-coded
assumptions. A first-attempt direct search might not consult cross-session
history at all; a task that has already exhausted
`INTENT_EXPANSION`/`PRODUCT_EXPANSION` might reach further back as an
additional signal before committing to `ANGLE_EXPANSION`. The index should
expose a bounded, explicit reach argument (e.g. how many past sessions, or
how wide a product/intent net) so each retry stage — or a future stage not
yet designed — can choose its own tradeoff between recall and noise, rather
than the index imposing one fixed policy on every caller.

Whatever reach is chosen, Node 03c still re-judges every admitted candidate
exactly as it does today. A policy that changed since a parent was last
approved is caught the same way a wrong intent guess is caught — by the
audit, not by the index deciding what's still true.

## Greeting flow under this design

No memory-specific work happens at session checkout — only the store write.

1. At session start, render a deterministic, templated standard greeting
   immediately. No LLM call is on the critical path for the first thing the
   customer sees.
2. Separately, look up the customer's most recent `turn_task_record` (a
   plain store query, no LLM involved). If its `resolution_status` is
   `CLARIFICATION_REQUIRED` or `PARTIALLY_SUFFICIENT`, that alone is the
   trigger — no separate "unresolved" flag needs to be computed or stored
   ahead of time; it is read directly off a field the store already has.
3. Only when that trigger fires, generate a second response block
   addressing the unresolved topic, so the turn reads as continuous rather
   than as two unrelated messages. This block goes through the same
   Groundedness v2.0 greeting-route checks as any other greeting content.

A customer who never returns costs nothing beyond the store write that
already happens as a byproduct of normal turn processing — not even a
lightweight flag computation.

## What this replaces from v1.0

- Proactive `last-session` summary generation and validation at every
  checkout — replaced by the store write (no generation) plus the on-demand
  greeting lookup above.
- Proactive `five-session baseline` consolidation — replaced by on-demand,
  query-scoped retrieval through the index when a specific turn's context
  calls for deeper history, rather than a generic rolling artifact
  maintained whether or not it's ever read.
- Rolling compact memory's role is unaffected by this document; it is
  session-scoped, already cheap, and not part of the cross-session cost
  problem this proposal addresses.

## Open questions, not yet resolved

- The memory-side judge's design: its rejection categories, prompt, and
  whether it needs the same model tier as Node 03c or can run on something
  cheaper given its lower bar.
- Exact index technology for the semantic tier (dedicated Chroma collection
  vs. another approach) once tag-matching and recency prove insufficient in
  practice.
- Retention/deletion policy for `turn_task_record` at bank-production scale
  — this document inherits the same unresolved gap `MEMORY_ARCHITECTURE_v1.0.md`
  already flags for v1.0's own storage.
- Where exactly the reach parameter is set per retry stage, and whether it
  should be a static per-stage constant or itself informed by sufficiency
  feedback.
