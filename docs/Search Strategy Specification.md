# Search Strategy Specification v2.7

**Project:** Horizon Bank Agentic Banking Chatbot  
**Module:** RAG Search Strategy and Retrieval Scope Expansion  
**Status:** Active runtime implementation.
**Branch baseline:** `development`  

---

## 1. Purpose

Search Strategy v2.7 is a hybrid, evidence-informed RAG strategy. It uses **Direct Search** for high-precision retrieval on the first three attempts, and a **Three-Angle Semantic Fallback** on the final attempt to overcome vocabulary mismatch and cover the answer-side of the vector space.

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

### Attempt 1: Intent Expansion
- **Query:** Uses the exact same `direct_query`.
- **Scope:** The product filter remains the same. The LLM adds one or more additional matrix-valid intents to broaden the search scope, based on the missing detail in the feedback.
- **Action:** Live direct search repeated over the broadened scope.

### Attempt 2: Product Expansion
- **Query:** Uses the exact same `direct_query`.
- **Scope:** Retains prior product and intents. The LLM adds a plausible, matrix-valid related product tag (e.g., expanding from a specific credit card to "All Credit Cards").
- **Action:** Live direct search repeated over the broadened scope.

### Attempt 3: Three-Angle Semantic Fallback (Angle Expansion)
- **Query:** The direct query is abandoned. The system shifts to a **multi-angle vector search**.
- **Scope:** Exact product and intent filters from Attempt 2.
- **Action:** Node 02e receives the failure feedback and generates a new `retry_3` angle group to target the missing detail. The system executes concurrent searches for every angle in every group (base + retry groups) and fuses the results using RRF.

## 3. Answer-Side Semantic Angles (FAQ Format)

The cornerstone of the Attempt 3 fallback is the generation of semantic angles. Instead of comma-separated keyword lists (which produce diffuse, weak embeddings), angles are formulated as **short natural-language phrases (10-20 words) that read like sentence fragments from an FAQ answer**.

This explicitly targets the "answer-side" chunks (`intact_answer` and `splintered_answer_part_*`) in the vector corpus.

Every task is decomposed into three canonical lenses:

### 3.1 Procedural
A phrase describing the actions, steps, or navigation a customer or bank performs. It should read like an FAQ answer's step-by-step instructions.
- *Example:* "Navigate to card settings in the mobile app to view available customization options for your card."

### 3.2 Policy
A phrase describing the rules, conditions, eligibility criteria, limits, fees, or restrictions. It should read like an FAQ answer's requirements section.
- *Example:* "Card design and feature customization are subject to current bank branding guidelines and specific card tier eligibility."

### 3.3 Conceptual
A phrase describing what the product, feature, or service is and how it works. It should read like an FAQ answer's explanatory introduction.
- *Example:* "Personalization refers to the ability to modify the visual appearance or specific configurable settings of a credit card."

## 4. Topic Anchoring & Hallucination Prevention

To prevent the vector queries from drifting or hallucinating generic details that don't match Horizon Bank, the angle generation is strictly governed:
1. **Topic Anchored:** Angles must be anchored strictly to the component's topic (for the base group) or the missing detail described in the feedback (for the retry group).
2. **Vocabulary Constrained:** The LLM must use generic banking vocabulary. It is strictly forbidden from inventing specific Horizon product names, amounts, dates, or branch details that were not provided.
3. **Question Paraphrasing Forbidden:** Angles must approach from the answer-side; they must never paraphrase the customer's question.

## 5. Reciprocal Rank Fusion (RRF) Convergence

On the final multi-angle attempt (Attempt 3):
1. The engine executes vector searches for all generated angles (e.g., 6 concurrent queries for a base group + retry group).
2. It calculates RRF (using `1 / (60 + Rank)`) within each angle group.
3. It equal-normalises the scores across the groups to produce one task-level score per parent ID.
4. It applies a 50% drop-off rule: if a parent's score drops by more than 50% compared to the preceding parent, selection stops.
5. `MAX_PARENTS = 4` is enforced.
6. The selected parent IDs are hydrated from SQLite and sent to the Sufficiency Auditor.
