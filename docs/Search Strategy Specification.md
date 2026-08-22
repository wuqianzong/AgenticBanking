# Search Strategy Specification v2.9

**Project:** Horizon Bank Agentic Banking Chatbot  
**Module:** RAG Search Strategy and Retrieval Scope Expansion  
**Status:** Active runtime implementation.
**Branch baseline:** `development`  

---

## 1. Purpose

Search Strategy v2.9 is a hybrid, evidence-informed RAG strategy. It uses **Direct Search** for high-precision retrieval on the first three attempts, and a **Three-Angle Semantic Fallback** on the final attempt to overcome vocabulary mismatch and cover answer-oriented language in the canonical FAQ vector space.

The production corpus remains anchored to the original FAQ content: canonical question and question-and-answer child chunks with normalized metadata. Three-angle is a **query-time representation only**. Direct and angle-fallback retrieval both search the same canonical corpus; production does not use LLM-paraphrased, angle-specific FAQ child chunks as the primary knowledge representation.

v2.8 split what v2.7 called "Intent Expansion" into two distinct stages with different decision-makers: a fully deterministic intent-widening step (no LLM judgment, no risk, free at the Chroma filter layer), followed by an LLM-judged product-expansion step reserved for when widening intent within the confirmed product alone was not enough. Scope is always widened — intent, then product — before the query representation itself is ever changed, since no query rewording can surface a parent that the metadata filter excludes outright.

v2.9 adds a candidate-exclusion refinement on top of that ladder (Section 7): widening scope can re-surface a parent a prior attempt already had reason to discard, wasting a selection slot on evidence the system already knows isn't the answer. Every retry attempt now excludes such parents before selection runs, and skips the sufficiency model call entirely when nothing new survives.

The design preserves the strongest properties of the RAG system:
- task-level Product/Intent metadata constraints;
- Chroma child-vector retrieval;
- Reciprocal Rank Fusion (RRF) for parent-ID convergence;
- a maximum of four selected parent IDs;
- SQLite parent hydration as the authoritative evidence source;
- task-level sufficiency auditing before evidence is allowed downstream.

## 2. Four-Attempt Escalation Ladder

For each task, the system applies a deterministic four-stage escalation ladder. Every attempt performs live RAG. This specification numbers the stages from Attempt 0 through Attempt 3. Runtime logs increment `task_retry_counts` after the base audit, so they display the same stages as attempts 1 through 4 respectively.

### Attempt 0: Base Direct Search
- **Query:** Uses the resolved `direct_query` extracted from the customer's request.
- **Scope:** Initial matrix-valid `target_commercial_product` and `target_intent`.
- **Note:** Searches the canonical question / question-and-answer FAQ chunk corpus.
- **Angle Prep:** Node 02e also generates a `base` angle group (procedural, policy, conceptual), but this is held in reserve.

### Attempt 1: Intent Expansion (deterministic)
- **Query:** Uses the exact same `direct_query`.
- **Scope:** The product filter remains the same. `target_intent` is widened to *every* matrix-valid intent for the task's current product(s) — computed directly from `PRODUCT_INTENT_MATRIX`, with no LLM call involved. A task that already carries an ordered array of two products (see Section 3 of the RAG Specification) unions the full valid-intent set of *each* product, not just the first.
- **Rationale:** Widening intent within an already-confirmed product carries no cross-product relevance risk, and Chroma's metadata filter costs nothing extra per additional intent value — there is no reason to guess a subset when the full set is free and safe.
- **Action:** Live direct search repeated over the broadened scope. Because this stage never touches the LLM, a turn whose failing tasks are *all* at this stage skips the retry-mode model call entirely.

### Attempt 2: Product Expansion (LLM-judged)
- **Query:** Uses the exact same `direct_query`.
- **Scope:** Retains the full intent scope from Attempt 1. Every task reaching this stage has already been searched across every intent valid for its current product without success, so the LLM is asked only whether a different, matrix-valid, genuinely related product should be added (e.g., a specific credit card expanding to a related card product) — never merely because another product's FAQ wording might be similar. The LLM does not specify intents for an added product; once added, that product's own full intent scope is opened automatically by the same deterministic rule as Attempt 1.
- **Action:** Live direct search repeated over the broadened scope.

### Attempt 3: Three-Angle Semantic Fallback (Angle Expansion)
- **Query:** The direct query is abandoned. The system shifts to a **multi-angle vector search**.
- **Scope:** Exact product and intent filters from Attempt 2.
- **Action:** Node 02e receives the failure feedback and generates a new `retry_3` angle group to target the missing detail. The system executes concurrent searches for every angle in every group (base + retry groups) and fuses the results using RRF.

A given task's `direct_query` is generated exactly once, at Attempt 0, and reused byte-for-byte through Attempts 1 and 2 — no stage in the ladder rewords it; only the metadata scope changes. The very first generation of that query is an LLM call, however, and is not guaranteed to produce identical wording across two separate turns of the same raw customer message even at `temperature=0.0` (a known limitation of commercial LLM APIs, not a configuration gap). This means the same question asked in two different sessions can retrieve a different candidate set purely from paraphrase variance at formulation time — a pre-existing characteristic of using an LLM-generated `direct_query`, unrelated to and unaffected by the retry ladder or the exclusion mechanism in Section 7, both of which only ever act on whatever query a given turn already settled on.

## 3. Multi-Task Turns

Each search task tracks its own retry count and expansion stage independently. When a customer turn produces multiple tasks that fail at different rates, a single retry cycle can contain a mix of tasks at different stages — some needing only the deterministic intent-expansion above, others needing the LLM's product-expansion or angle-expansion judgment. Node 02e splits these before building its retry prompt: deterministic tasks are resolved directly in Python, and the retry-mode LLM call, when needed at all, is scoped only to the tasks that genuinely require a judgment call for that cycle.

## 4. Answer-Oriented Semantic Angles (FAQ Format)

The cornerstone of the Attempt 3 fallback is the generation of semantic angles. Instead of comma-separated keyword lists (which produce diffuse, weak embeddings), angles are formulated as **short natural-language phrases (10-20 words) that read like sentence fragments from an FAQ answer**.

These are answer-oriented query formulations: they help bridge a customer's wording to the way an FAQ answer commonly expresses actions, rules, or concepts. They still retrieve against the same canonical production corpus used by direct search; they do not require angle-specific vector chunks.

Every task is decomposed into three canonical lenses:

### 4.1 Procedural
A phrase describing the actions, steps, or navigation a customer or bank performs. It should read like an FAQ answer's step-by-step instructions.
- *Example:* "Navigate to card settings in the mobile app to view available customization options for your card."

### 4.2 Policy
A phrase describing the rules, conditions, eligibility criteria, limits, fees, or restrictions. It should read like an FAQ answer's requirements section.
- *Example:* "Card design and feature customization are subject to current bank branding guidelines and specific card tier eligibility."

### 4.3 Conceptual
A phrase describing what the product, feature, or service is and how it works. It should read like an FAQ answer's explanatory introduction.
- *Example:* "Personalization refers to the ability to modify the visual appearance or specific configurable settings of a credit card."

## 5. Topic Anchoring & Hallucination Prevention

To prevent the vector queries from drifting or hallucinating generic details that don't match Horizon Bank, the angle generation is strictly governed:
1. **Topic Anchored:** Angles must be anchored strictly to the component's topic (for the base group) or the missing detail described in the feedback (for the retry group).
2. **Vocabulary Constrained:** The LLM must use generic banking vocabulary. It is strictly forbidden from inventing specific Horizon product names, amounts, dates, or branch details that were not provided.
3. **Question Paraphrasing Forbidden:** Angles must approach from the answer-side; they must never paraphrase the customer's question.

## 6. Reciprocal Rank Fusion (RRF) Convergence

On the final multi-angle attempt (Attempt 3):
1. The engine executes vector searches for all generated angles (e.g., 6 concurrent queries for a base group + retry group).
2. It calculates RRF (using `1 / (60 + Rank)`) within each angle group.
3. It equal-normalises the scores across the groups to produce one task-level score per parent ID.
4. It applies a 50% drop-off rule: if a parent's score drops by more than 50% compared to the preceding parent, selection stops.
5. `MAX_PARENTS = 4` is enforced.
6. The selected parent IDs are hydrated from SQLite and sent to the Sufficiency Auditor.

Section 7 below applies one refinement on top of this: before rule 4 runs, on any retry attempt, the ranked candidate list is filtered to remove parents already known from this task's own history. The four-slot cap and 50% drop-off rule themselves are unchanged — they run exactly as described here, just against a pre-filtered list.

## 7. Candidate Exclusion Across Retries

A widened retry filter (Attempt 1 or 2) does not guarantee the newly eligible candidates outrank whatever was already tried. Left unfiltered, a parent the sufficiency auditor already rejected last attempt can rank highly again under the wider scope, occupying one of the four selection slots that the widened scope was supposed to use for something genuinely new — silently crowding out the answer the expansion was meant to surface. The same waste applies to a parent already retained as approved partial evidence: it will be re-attached to the evaluation set from SQLite regardless of whether Chroma finds it again, so letting it also consume a fresh selection slot is pure redundancy.

### 7.1 What gets excluded

For each task, before the top-four / 50%-drop-off selection runs on any attempt after the first, the candidate pool excludes:
- **Rejected parents:** any parent_id shown to the sufficiency auditor in a prior attempt for this task and *not* approved. Tracked per task in `task_rejected_parent_ids`, accumulated across attempts, and cleared once the task reaches `SUFFICIENT`.
- **Retained partial evidence:** any parent_id already approved and held in `task_partial_evidence` for this task. These remain part of the evaluation set every round — they are simply excluded from the *fresh* vector search's own candidate pool, since they are unconditionally re-attached by ID from SQLite regardless of what that search returns.

This exclusion applies uniformly to every retrieval mode a retry can use: `execute_direct_search` (including its verified-session-evidence RRF branch) and `execute_multi_angle_search`.

### 7.2 Compensating fetch depth

Excluding candidates from an already-narrow top-k window can shrink the pool without surfacing anything new: the fetch depth Chroma is asked for is a *child*-chunk count, not a parent count, and one parent can own more than one child chunk. If a fetch of `N` children collapses to only a handful of unique parents after de-duplication, and exclusion then removes some of those, the top-four selection step can be left with fewer candidates than it needs — even though the corpus may hold more relevant parents that were simply never asked for.

All three retrieval paths — `execute_direct_search`'s plain query, its verified-session-evidence RRF branch, and every per-angle query in `execute_multi_angle_search` — compensate the same way, via the shared `_fetch_depth_for_exclusion` helper: raw fetch depth is `RRF_CANDIDATES_PER_ANGLE` plus a fixed multiple (`CHILDREN_PER_PARENT_ESTIMATE = 2`, matching this corpus's typical question-plus-answer child count per parent) for every excluded parent. This is a one-shot estimate computed from a count the caller already knows exactly (no query round-trip needed to determine it) — not a guarantee. An unusually high exclusion count against a thin corpus slice can still under-fill the pool; that is an accepted, low-probability edge case, deliberately not corrected with an adaptive retry loop, since the added round-trip cost and complexity would apply to every excluding retry to guard against a case that in practice affects at most one or two parents.

### 7.3 Skipping the sufficiency call on a fully exhausted pool

If, after combining fresh candidates with retained partial evidence, a task has zero total candidates to evaluate — either because a genuinely narrow scope returned nothing, or because exclusion removed everything the fresh search found — there is nothing for the sufficiency auditor to compare against the task's `customer_component`. The verdict is deterministic in this case (`INSUFFICIENT`, with a fixed reason), and the task is logged via `task.sufficiency.skipped` and excluded from that cycle's sufficiency prompt entirely, saving a model call in a batch that otherwise still includes tasks with real evidence to judge.

### 7.4 Scope

All of the above is tracked and applied strictly per `task_id`. A multi-task turn's sibling tasks never share an exclusion list — each task's own rejected/retained history only ever affects its own future retrieval calls, computed independently inside the same `asyncio.gather` fan-out that already retrieves each task's candidates concurrently. Both `task_rejected_parent_ids` and `task_partial_evidence` are cleared at the start of every new customer turn (`VOLATILE_STATE_RESET`), so no exclusion state persists across turns even when task IDs are reused turn to turn.

## 8. Observability

Every stage transition is logged for debugging, both as terminal output and as structured events consumable from `backend/logs/system.jsonl`:

- **`task.retry.stage_plan`** (log) / `🪜 [RETRY STAGE]` (print) — emitted once per retry cycle, before any model call. Records which stage (`INTENT_EXPANSION`, `PRODUCT_EXPANSION`, `ANGLE_EXPANSION`) every currently-failing task is assigned this cycle, and which task IDs will and will not reach the LLM.
- **`⚡ [RETRY MODEL SKIPPED]`** / **`🧠 [RETRY MODEL SCOPED]`** (print only) — states plainly whether the retry-mode LLM call was skipped entirely (an all-deterministic `INTENT_EXPANSION` cycle), or scoped to a subset of the cycle's failing tasks.
- **`task.expansion.applied`** (log) / `🔧 [<STAGE>]` (print) — emitted per task once its retry merge completes. For `INTENT_EXPANSION`/`PRODUCT_EXPANSION`, records the exact before → after product and intent sets and an explicit `no_op` flag. For `ANGLE_EXPANSION`, records the newly appended angle group's `group_id` and its procedural/policy/conceptual text.
- **`task.retry.deferred`** (log) / `⏩ [RETRY BARRIER]` (print) — fires when an expansion stage produces no actual scope change, so the task skips a redundant live RAG call and fast-forwards its retry count toward the next stage.
- **`task.rag.completed`** (log, in Node 03b) — includes `retrieval_mode` (`DIRECT` or `ANGLE_GROUP_RRF`) per task, which combined with the stage-plan event confirms the retrieval representation actually used matched the intended stage.
- **`🚫 Excluding N previously-rejected parent(s)...`** (print, in `rag_engine.py`) — emitted per retry search that has an exclusion list, naming exactly which parent_ids were removed before selection.
- **`task.sufficiency.skipped`** (log) / `⚡ [SUFFICIENCY SKIPPED]` (print) — emitted per task when exclusion (or a genuinely empty search) leaves zero candidates to judge; the task is marked `INSUFFICIENT` deterministically without a model call.

Tracing a single task's full retry history is therefore a matter of filtering the log for its `task_id` across `task.retry.stage_plan` → `task.expansion.applied` (or `task.retry.deferred`) → `task.rag.completed` → `task.sufficiency.completed`/`task.retry`/`task.sufficiency.skipped`, in that order, once per attempt.

## 9. Implementation Verification

Live tracing against `backend/logs/system.jsonl` confirmed the ladder reaches every stage as designed, including full exhaustion. One representative turn (a branch-visit loan question with no matching FAQ) walked the entire ladder: Attempt 0 (DIRECT) failed, Attempt 1 (`INTENT_EXPANSION`, deterministic) failed, Attempt 2 (`PRODUCT_EXPANSION`, LLM-judged) failed, Attempt 3 (`ANGLE_EXPANSION`, LLM-judged) failed with `retrieval_mode` correctly switching to `ANGLE_GROUP_RRF`, and the task then correctly reported `CLARIFICATION_REQUIRED` after exhausting all four attempts — with downstream generation producing a clean, grounded terminal response rather than looping or erroring.

This tracing exercise surfaced and fixed one implementation bug: `MAX_RAG_ATTEMPTS` had been left at `3` — the ceiling correct for the prior three-attempt ladder — after splitting `META_FILTER_EXPANSION` into separate `INTENT_EXPANSION` and `PRODUCT_EXPANSION` stages added a fourth attempt. This made `ANGLE_EXPANSION` structurally unreachable: every task that reached `PRODUCT_EXPANSION` terminated immediately afterward regardless of outcome. Fixed to `4`, matching the four stages this document describes.

The Section 7 exclusion mechanism was verified against real production data, not just unit-level reasoning. Asking the identical question in two separate turns produced two different `direct_query` formulations (a real instance of the Section 2 non-determinism note), which made a naive before/after comparison misleading at first — the apparent difference in retrieved candidates was initially attributed to fetch depth, which further testing disproved. Controlling for query wording by re-running each turn's actual logged `direct_query` directly against the vector store confirmed the true mechanism: without exclusion, a parent already rejected in Attempt 0 would have re-entered the Attempt 1 top-four; with exclusion active, it was correctly removed and a previously out-of-range candidate was promoted into its place instead — reproducing the real production selection exactly.

A separate before/after comparison against a genuinely pre-exclusion production log (a card-personalization question, traced hours earlier under the prior `v2.7` combined `META_FILTER_EXPANSION` stage and a 3-attempt cap) showed the customer-facing effect directly, not just the internal mechanics. Before the fixes in this document, the retry stage re-fetched the exact same four already-rejected candidates verbatim (no exclusion existed yet), then exhausted its attempt budget, then failed groundedness three consecutive times while trying to draft a response from that dead end, and was ultimately escalated to a human agent — the customer received no answer at all. Re-running the identical question after all fixes in this document walked every stage of the ladder with a completely fresh candidate set each time (zero repeated parents across four attempts), correctly retained the one genuinely useful partial-evidence parent found at Attempt 0 through to the end despite it never reappearing in any later fresh search, self-corrected one real grounding hallucination during generation, and resolved `PARTIALLY_SUFFICIENT` with a delivered, evidence-backed answer.
