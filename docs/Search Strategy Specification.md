# Search Strategy Specification v2.8

**Project:** Horizon Bank Agentic Banking Chatbot  
**Module:** RAG Search Strategy and Retrieval Scope Expansion  
**Status:** Active runtime implementation.
**Branch baseline:** `development`  

---

## 1. Purpose

Search Strategy v2.8 is a hybrid, evidence-informed RAG strategy. It uses **Direct Search** for high-precision retrieval on the first three attempts, and a **Three-Angle Semantic Fallback** on the final attempt to overcome vocabulary mismatch and cover the answer-side of the vector space.

v2.8 splits what v2.7 called "Intent Expansion" into two distinct stages with different decision-makers: a fully deterministic intent-widening step (no LLM judgment, no risk, free at the Chroma filter layer), followed by an LLM-judged product-expansion step reserved for when widening intent within the confirmed product alone was not enough. Scope is always widened — intent, then product — before the query representation itself is ever changed, since no query rewording can surface a parent that the metadata filter excludes outright.

The design preserves the strongest properties of the RAG system:
- task-level Product/Intent metadata constraints;
- Chroma child-vector retrieval;
- Reciprocal Rank Fusion (RRF) for parent-ID convergence;
- a maximum of four selected parent IDs;
- SQLite parent hydration as the authoritative evidence source;
- task-level sufficiency auditing before evidence is allowed downstream.

## 2. Four-Attempt Escalation Ladder

For each task, the system applies a deterministic four-stage escalation ladder. Every attempt performs live RAG.

### Attempt 0: Base Direct Search
- **Query:** Uses the resolved `direct_query` extracted from the customer's request.
- **Scope:** Initial matrix-valid `target_commercial_product` and `target_intent`.
- **Note:** Matches primarily against "question anchor" chunks in the vector store.
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

## 3. Multi-Task Turns

Each search task tracks its own retry count and expansion stage independently. When a customer turn produces multiple tasks that fail at different rates, a single retry cycle can contain a mix of tasks at different stages — some needing only the deterministic intent-expansion above, others needing the LLM's product-expansion or angle-expansion judgment. Node 02e splits these before building its retry prompt: deterministic tasks are resolved directly in Python, and the retry-mode LLM call, when needed at all, is scoped only to the tasks that genuinely require a judgment call for that cycle.

## 4. Answer-Side Semantic Angles (FAQ Format)

The cornerstone of the Attempt 3 fallback is the generation of semantic angles. Instead of comma-separated keyword lists (which produce diffuse, weak embeddings), angles are formulated as **short natural-language phrases (10-20 words) that read like sentence fragments from an FAQ answer**.

This explicitly targets the "answer-side" chunks (`intact_answer` and `splintered_answer_part_*`) in the vector corpus.

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

## 7. Observability

Every stage transition is logged for debugging, both as terminal output and as structured events consumable from `backend/logs/system.jsonl`:

- **`task.retry.stage_plan`** (log) / `🪜 [RETRY STAGE]` (print) — emitted once per retry cycle, before any model call. Records which stage (`INTENT_EXPANSION`, `PRODUCT_EXPANSION`, `ANGLE_EXPANSION`) every currently-failing task is assigned this cycle, and which task IDs will and will not reach the LLM.
- **`⚡ [RETRY MODEL SKIPPED]`** / **`🧠 [RETRY MODEL SCOPED]`** (print only) — states plainly whether the retry-mode LLM call was skipped entirely (an all-deterministic `INTENT_EXPANSION` cycle), or scoped to a subset of the cycle's failing tasks.
- **`task.expansion.applied`** (log) / `🔧 [<STAGE>]` (print) — emitted per task once its retry merge completes. For `INTENT_EXPANSION`/`PRODUCT_EXPANSION`, records the exact before → after product and intent sets and an explicit `no_op` flag. For `ANGLE_EXPANSION`, records the newly appended angle group's `group_id` and its procedural/policy/conceptual text.
- **`task.retry.deferred`** (log) / `⏩ [RETRY BARRIER]` (print) — unchanged mechanism, now shared by both `INTENT_EXPANSION` and `PRODUCT_EXPANSION`: fires when an expansion stage produces no actual scope change, so the task skips a redundant live RAG call and fast-forwards its retry count toward the next stage.
- **`task.rag.completed`** (log, in Node 03b) — already includes `retrieval_mode` (`DIRECT` or `ANGLE_GROUP_RRF`) per task, which combined with the stage-plan event confirms the retrieval representation actually used matched the intended stage.

Tracing a single task's full retry history is therefore a matter of filtering the log for its `task_id` across `task.retry.stage_plan` → `task.expansion.applied` (or `task.retry.deferred`) → `task.rag.completed` → `task.sufficiency.completed`/`task.retry`, in that order, once per attempt.

## 8. Implementation Verification

Live tracing against `backend/logs/system.jsonl` confirmed the ladder reaches every stage as designed, including full exhaustion. One representative turn (a branch-visit loan question with no matching FAQ) walked the entire ladder: Attempt 0 (DIRECT) failed, Attempt 1 (`INTENT_EXPANSION`, deterministic) failed, Attempt 2 (`PRODUCT_EXPANSION`, LLM-judged) failed, Attempt 3 (`ANGLE_EXPANSION`, LLM-judged) failed with `retrieval_mode` correctly switching to `ANGLE_GROUP_RRF`, and the task then correctly reported `CLARIFICATION_REQUIRED` after exhausting all four attempts — with downstream generation producing a clean, grounded terminal response rather than looping or erroring.

This tracing exercise surfaced and fixed one implementation bug: `MAX_RAG_ATTEMPTS` had been left at `3` — the ceiling correct for the prior three-attempt ladder — after splitting `META_FILTER_EXPANSION` into separate `INTENT_EXPANSION` and `PRODUCT_EXPANSION` stages added a fourth attempt. This made `ANGLE_EXPANSION` structurally unreachable: every task that reached `PRODUCT_EXPANSION` terminated immediately afterward regardless of outcome. Fixed to `4`, matching the four stages this document describes.
