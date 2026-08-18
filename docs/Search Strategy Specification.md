# Search Strategy Specification v2.6

**Project:** Horizon Bank Agentic Banking Chatbot  
**Module:** RAG Search Strategy and Retrieval Scope Expansion  
**Status:** Proposed architecture for implementation  
**Branch baseline:** `development`  
**Supersedes:** Search Strategy v2.5 retry-expansion logic only; parent-child evidence architecture, RRF selection, sufficiency auditing, evidence approval, cache safety, and outbound groundedness remain authoritative unless explicitly changed below.

---

## 1. Purpose

Search Strategy v2.6 replaces the fixed v2.5 retry ladder with a deterministic, evidence-informed **three-angle graph-guided expansion** strategy.

The design preserves the strongest properties of the current RAG system:

- task-level Product/Intent metadata constraints;
- procedural, policy, and conceptual query decomposition;
- Chroma child-vector retrieval;
- Reciprocal Rank Fusion (RRF) for parent-ID convergence;
- a maximum of four selected parent IDs;
- SQLite parent hydration as the authoritative evidence source;
- task-level sufficiency auditing before evidence is allowed downstream.

The central change is how the system decides what to search after an insufficient retrieval.

Instead of a fixed sequence such as:

```text
angle expansion -> intent expansion -> product expansion
```

v2.6 uses information already produced by the failed search to decide which **Product x Intent search scope** should be added next.

The design principle is:

> **LLMs define the semantic map; deterministic mathematics decides how to expand the live search; RRF decides which parent evidence converges.**

---

## 2. Problem with v2.5

The current v2.5 search is intentionally additive, but its retry order is predefined. A task can broaden semantic angles, then intents, then products, regardless of what the failed retrieval itself indicates.

This has four limitations:

1. **The retry path is not evidence-directed.** A weak policy retrieval and a weak procedural retrieval enter the same fixed ladder.
2. **Cosine similarity is computed but not used to guide expansion.** It is currently telemetry rather than a navigation signal.
3. **Multi-intent parent metadata contains corpus relationships that are not used for retry selection.** An FAQ tagged with both `Application` and `Eligibility` can reveal that the corpus itself associates those concepts for the current query.
4. **Intent expansion and product expansion are treated as separate retry stages even though the actual retrieval boundary is a Product/Intent pair.**

v2.6 unifies these into one search-space expansion model.

---

## 3. Core design

For each task, v2.6 maintains an **active search scope** containing one or more valid Product/Intent nodes.

A node is:

```text
(Product, Intent)
```

Examples:

```text
(Deposit_Account, Application)
(Deposit_Account, Eligibility)
(Credit_Card, Fees And Charges)
(Horizon_APP, Authentication And Security)
```

The active search state is additive.

Initial state:

```text
A0 = {
  (Deposit_Account, Application)
}
```

After one expansion:

```text
A1 = {
  (Deposit_Account, Application),
  (Deposit_Account, Eligibility)
}
```

The original node is never discarded merely because a later node is added.

This is not graph traversal in the road-network sense. The system does not need to walk through every intermediate node. The graph is a semantic map used to decide **which scope to add next**.

---

## 4. Three semantic angles

Every task is represented through three canonical retrieval angles:

### 4.1 Procedural

Information about actions, steps, sequences, setup, application process, execution, or what the customer must do.

Examples of the semantic question represented by this angle:

```text
What must the customer do?
What are the steps?
How is the process completed?
```

### 4.2 Policy

Information about rules, eligibility, conditions, limits, fees, restrictions, requirements, timing constraints, compliance, or permitted behavior.

Examples:

```text
What rules or conditions apply?
Who qualifies?
What limits or restrictions govern the action?
```

### 4.3 Conceptual

Information about definitions, features, meaning, relationships, purpose, or how a product or capability works.

Examples:

```text
What is this?
How does it work?
What feature or concept explains the behavior?
```

These three angles are query-side semantic lenses. v2.6 does **not** require document-side vectors to be permanently classified as procedural, policy, and conceptual. The active ETL can continue to use question anchors and intact/splintered answer vectors.

---

## 5. RRF remains the convergence mechanism

v2.6 does not replace RRF.

For every active scope state:

1. build one shared metadata filter from all active, matrix-valid Product/Intent pairs;
2. run the procedural query;
3. run the policy query;
4. run the conceptual query;
5. collect raw Chroma hits and cosine similarities;
6. perform parent-level RRF using the existing rank-based logic;
7. apply the existing drop-off rule and `MAX_PARENTS = 4`;
8. hydrate the selected parent IDs from SQLite;
9. run task-level sufficiency evaluation.

The graph affects **which Product/Intent scope is eligible for retrieval**. It does not directly change the RRF score of a parent.

The search therefore remains:

```text
semantic expansion -> vector retrieval -> RRF convergence -> sufficiency
```

---

## 6. Why expansion is additive

When a search fails sufficiency, it does not mean all retrieved dimensions were poor.

For example:

```text
Procedural: strong
Policy:     weak
Conceptual: strong
```

If the system replaced `Application` with `Eligibility`, it could discard useful application-process evidence already present in the original scope.

Instead, v2.6 expands:

```text
{Application}
      +
{Eligibility}
      =
{Application, Eligibility}
```

The next retrieval is rerun across the expanded filter. This keeps the original strong semantic territory in competition while introducing new territory that may repair the weak dimension.

RRF is then allowed to reconverge over the enlarged candidate space.

---

## 7. Two independent signals for the next expansion

v2.6 uses two complementary sources of semantic information.

### 7.1 Signal A: common-sense semantic graph

The graph is generated offline using banking common sense, product guidelines, intent definitions, and the three angle definitions.

It answers:

> If retrieval under Product/Intent node A is insufficient, how useful would Product/Intent node B be as an additional scope for procedural, policy, or conceptual retrieval?

This signal is corpus-independent.

Its main purpose is **targeted repair of a weak angle**.

### 7.2 Signal B: FAQ immediate neighbour

The FAQ corpus provides a second signal through multi-tagged parent records.

Example:

```text
Current active scope:
(Deposit_Account, Application)

RRF-selected parent:
product_tags     = Deposit_Account
intent_families  = Application, Eligibility
```

The parent reveals that the source knowledge itself associates `Eligibility` with evidence retrieved under the current search.

`(Deposit_Account, Eligibility)` becomes an **FAQ-derived candidate expansion** even if it is not a direct graph neighbour.

This signal is query-specific and corpus-derived.

Its main purpose is **broad semantic expansion of the whole three-angle pool**.

### 7.3 Relationship between the two signals

The two signals must remain conceptually independent:

```text
Common-sense graph
  -> predefined semantic prior
  -> targeted by weak-angle imbalance

FAQ immediate neighbour
  -> observed corpus relationship
  -> derived from the current retrieval result
```

If both point to the same node, confidence in that expansion is high.

If only one points to a node, the node may still be considered, provided it is a valid Product/Intent scope.

---

## 8. Semantic graph model

### 8.1 Node definition

Each graph node is one valid runtime search scope:

```text
node = (product, intent)
```

Only Product/Intent pairs permitted by the authoritative runtime taxonomy may become graph nodes.

The graph generator must use the **same consolidated Product/Intent matrix consumed by runtime task formulation**. It must not silently construct nodes from raw or unconsolidated intent labels.

If the taxonomy artifact contains labels outside the approved master-intent vocabulary, graph generation must fail validation rather than producing a mixed graph.

### 8.2 Directed edges

Edges are directed.

```text
A -> B
```

is evaluated independently from:

```text
B -> A
```

This is necessary because search usefulness is conditional on the current scope.

For example, `Application -> Eligibility` can be highly useful for repairing policy coverage, while the reverse direction may have a different semantic profile.

### 8.3 Three-angle edge vector

Every retained edge carries one three-dimensional weight vector:

```text
edge(A -> B) = [procedural, policy, conceptual]
```

Example:

```text
(Deposit_Account, Application)
    ->
(Deposit_Account, Eligibility)

procedural = 0.5
policy     = 1.0
conceptual = 0.5
```

The weights mean:

> How useful is the destination node as an additional retrieval scope when the source search is weak in this angle?

They are search-expansion priors. They are **not factual confidence scores** and may never be used as answer evidence.

### 8.4 Initial weight scale

The first implementation should use a small discrete scale:

```text
0.0 = no meaningful expansion value
0.5 = useful / adjacent
1.0 = strongly useful
```

This is preferable to asking an LLM to invent pseudo-precise values such as `0.83`.

Continuous weights may be introduced later only if evaluation demonstrates a measurable benefit.

### 8.5 Edge existence

An ordered pair is stored as an edge if at least one angle has non-zero expansion value.

```text
[0.0, 0.0, 0.0] -> no edge
[0.0, 1.0, 0.5] -> retain edge
```

### 8.6 Cross-product edges

Cross-product edges are allowed.

Example:

```text
(Credit_Card, Payments And Transfers)
    ->
(Deposit_Account, Fees And Charges)
```

However, the LLM rubric should be conservative: a cross-product edge exists only when adding the destination product/intent is plausibly useful to retrieve evidence for a question originating from the source scope.

Product expansion and intent expansion therefore become one unified operation: **add a Product/Intent node**.

---

## 9. Offline graph construction

The graph is generated offline and versioned as an application artifact. Runtime requests do not ask an LLM to invent graph edges.

### 9.1 Inputs

The graph builder receives:

- approved product definitions;
- approved master-intent definitions/guidelines;
- the authoritative valid Product/Intent matrix;
- precise procedural/policy/conceptual definitions;
- the source Product/Intent node;
- the destination Product/Intent node.

The graph-building LLM must **not** receive FAQ text, FAQ co-tag statistics, Chroma results, or parent records.

This maintains independence between the common-sense graph and the FAQ semantic signal.

### 9.2 Deterministic pair enumeration

Code constructs the valid node list deterministically.

If there are `N` valid nodes, it evaluates every ordered pair where source != destination:

```text
N * (N - 1)
```

The LLM does not decide which pairs deserve inspection.

This prevents semantic relationships from being omitted simply because the model was never asked about them.

### 9.3 Pair evaluation prompt

For every ordered pair, the semantic evaluator should receive an instruction equivalent to:

```text
SOURCE SEARCH SCOPE:
<Product A, Intent X>

DESTINATION SEARCH SCOPE:
<Product B, Intent Y>

Using only the supplied banking product definitions, intent guidelines,
and angle definitions:

If retrieval under SOURCE is insufficient, would adding DESTINATION as
an additional retrieval scope meaningfully improve retrieval?

Evaluate independently for:
1. procedural usefulness
2. policy usefulness
3. conceptual usefulness

Allowed weight values: 0.0, 0.5, 1.0.
Do not use FAQ corpus knowledge.
Do not infer facts about Horizon Bank beyond the supplied guidelines.
```

### 9.4 Structured LLM output

Example retained edge:

```json
{
  "source": {
    "product": "Deposit_Account",
    "intent": "Application"
  },
  "destination": {
    "product": "Deposit_Account",
    "intent": "Eligibility"
  },
  "edge": true,
  "weights": {
    "procedural": 0.5,
    "policy": 1.0,
    "conceptual": 0.5
  },
  "reason": "Eligibility rules commonly constrain whether and how an application can proceed."
}
```

Example rejected pair:

```json
{
  "source": {
    "product": "Deposit_Account",
    "intent": "Application"
  },
  "destination": {
    "product": "Stocks",
    "intent": "Branch And ATM Locator"
  },
  "edge": false,
  "weights": {
    "procedural": 0.0,
    "policy": 0.0,
    "conceptual": 0.0
  },
  "reason": "The destination does not provide a meaningful expansion path for the source scope."
}
```

### 9.5 Graph validation

Before publishing a graph version, validate deterministically:

- every node exists in the authoritative Product/Intent matrix;
- source and destination differ;
- reverse edges are treated independently;
- every weight is one of the permitted values;
- `edge=false` implies all three weights are zero;
- `edge=true` implies at least one non-zero weight;
- there are no duplicate ordered pairs;
- every retained edge contains an audit reason;
- every product and intent uses canonical spelling;
- the graph artifact records model name, prompt version, taxonomy version, generation timestamp, and graph version.

### 9.6 Runtime graph artifact

Recommended conceptual structure:

```json
{
  "graph_version": "2.6.0",
  "taxonomy_version": "...",
  "weight_scale": [0.0, 0.5, 1.0],
  "edges": [
    {
      "from": ["Deposit_Account", "Application"],
      "to": ["Deposit_Account", "Eligibility"],
      "procedural": 0.5,
      "policy": 1.0,
      "conceptual": 0.5
    }
  ]
}
```

The explanatory reason may be stored in an audit artifact if runtime size needs to remain minimal.

---

## 10. Runtime search state

Each task should maintain at least:

```text
task_id
base_angle_group
active_scope_pairs
selected_parent_ids
angle_coverage
angle_need
expansion_history
retry_count
```

Example:

```json
{
  "task_id": "task_1",
  "active_scope_pairs": [
    {
      "product": "Deposit_Account",
      "intents": ["Application"]
    }
  ],
  "expansion_history": []
}
```

After adding Eligibility:

```json
{
  "active_scope_pairs": [
    {
      "product": "Deposit_Account",
      "intents": ["Application", "Eligibility"]
    }
  ],
  "expansion_history": [
    {
      "added": ["Deposit_Account", "Eligibility"],
      "source": "graph+faq"
    }
  ]
}
```

Equivalent scopes may be compacted by product for storage, but candidate selection should reason in canonical Product/Intent pairs.

---

## 11. Measuring three-angle coverage

RRF uses rank and intentionally ignores cosine magnitude. v2.6 should preserve that behavior for final parent fusion while separately using cosine similarity as a diagnostic signal.

Let:

```text
S = parent IDs selected by RRF, |S| <= 4
A = {procedural, policy, conceptual}
```

For each selected parent `p` and angle `a`, record the best cosine similarity returned for that parent under that angle:

```text
sim(p, a) = maximum cosine similarity among child hits for parent p in angle a
```

If the parent did not appear within that angle's retrieval candidate pool:

```text
sim(p, a) = 0
```

For each angle, define normalized coverage:

```text
coverage(a) = average over selected parents of sim(p, a)
```

Because no more than four parents are selected, average and sum contain the same ordering information, but the average keeps the value in an interpretable range.

Then define raw need:

```text
raw_need(a) = 1 - coverage(a)
```

Normalize the three needs:

```text
need(a) = raw_need(a) / sum(raw_need(all three angles))
```

If the denominator is zero, use a uniform vector:

```text
[1/3, 1/3, 1/3]
```

Example:

```text
coverage:
procedural = 0.82
policy     = 0.48
conceptual = 0.74

raw need:
procedural = 0.18
policy     = 0.52
conceptual = 0.26
```

Policy receives the strongest expansion pressure.

### 11.1 Important interpretation

Low cosine similarity is not itself proof that the evidence is factually insufficient. It is only a search-navigation signal.

The task-level sufficiency auditor remains the authority on whether retrieved evidence is adequate.

The angle-need vector is used only **after sufficiency has failed**.

---

## 12. Graph candidate score

Let the current active node set be `A_t`.

For a graph candidate destination `v`, each active source node `u` may have an edge vector:

```text
w(u, v) = [w_proc, w_policy, w_concept]
```

Given the current normalized angle need vector `d`, define the edge repair value:

```text
edge_value(u, v)
  = d_proc * w_proc
  + d_policy * w_policy
  + d_concept * w_concept
```

For a destination connected from multiple active nodes, use the strongest direct justification rather than summing and rewarding nodes merely for having many incoming edges:

```text
graph_score(v) = max over active u of edge_value(u, v)
```

This score answers:

> Which candidate scope is most likely to repair the semantic dimension that is currently weakest?

---

## 13. FAQ immediate-neighbour discovery

### 13.1 Required parent metadata

Current RAG hydration returns only:

```text
parent_id, content
```

v2.6 requires retrieval/navigation metadata to remain available for the selected parents:

```text
parent_id
product_tags
intent_families
content
```

This metadata is used only for search navigation and eligibility checks.

### 13.2 Candidate extraction

For each RRF-selected parent:

1. read all canonical parent product tags;
2. read all canonical parent intent families;
3. construct possible Product/Intent pairs;
4. validate every pair against the authoritative Product/Intent matrix;
5. discard pairs already present in the active search set;
6. retain the remainder as FAQ-derived candidate expansions.

This allows a multi-intent parent to expose a semantically related intent that was not part of the current filter.

### 13.3 FAQ support score

For a selected parent `p`, define its three-angle retrieval strength:

```text
parent_strength(p)
  = average(
      sim(p, procedural),
      sim(p, policy),
      sim(p, conceptual)
    )
```

For FAQ candidate node `v`, define:

```text
faq_score(v)
  = max parent_strength(p)
    across selected parents whose metadata exposes v
```

Using the maximum prevents a candidate from becoming artificially dominant simply because the same semantic relationship appears in many duplicated child records.

This score answers:

> How strongly does the current corpus retrieval point toward this additional Product/Intent scope?

---

## 14. Unified next-node selection

The candidate set is the union of:

```text
1. valid outgoing graph neighbours from any active node
2. valid FAQ immediate neighbours exposed by selected parents
```

A candidate does not need to exist in both sources.

For candidate `v`:

```text
graph_score(v) = 0 if there is no graph edge
faq_score(v)   = 0 if no selected parent exposes it
```

Initial combined scoring:

```text
candidate_score(v)
  = lambda_graph * graph_score(v)
  + lambda_faq   * faq_score(v)
```

with:

```text
lambda_graph + lambda_faq = 1
```

Recommended initial configuration:

```text
lambda_graph = 0.60
lambda_faq   = 0.40
```

These are tuning defaults, not semantic truths. They should be validated on an evaluation set and kept configurable.

Rationale for making the graph slightly stronger initially:

- graph score is explicitly conditioned on the weak angle;
- FAQ co-intent evidence can be strong but may reflect how source FAQs happened to be partitioned;
- agreement between both signals naturally receives contributions from both terms.

Deterministic tie-break order:

```text
1. candidate_score descending
2. faq_score descending
3. graph_score descending
4. canonical Product/Intent key ascending
```

Only the highest-ranked unseen node is added per retry in the initial implementation.

Adding one node at a time preserves observability, limits Chroma scope growth, and makes offline evaluation easier.

---

## 15. Runtime expansion loop

The target v2.6 loop is:

```text
1. Node 02e formulates the task
   - stable task_id
   - initial Product/Intent scope
   - one procedural query
   - one policy query
   - one conceptual query

2. Run Chroma retrieval under the active scope

3. Compute RRF

4. Select <= 4 parent IDs

5. Hydrate authoritative SQLite parents + navigation metadata

6. Run sufficiency auditor

7. If sufficient:
     approve evidence and continue downstream

8. If insufficient:
     compute angle coverage
     compute angle need
     discover graph candidates
     discover FAQ immediate neighbours
     rank unified candidates mathematically
     add the best unseen Product/Intent node to active scope

9. Rerun all three angle searches under the expanded scope

10. RRF reconverges over the enlarged candidate pool

11. Repeat until sufficient or retry budget is exhausted
```

The key state transition is:

```text
A_(t+1) = A_t union {best_candidate}
```

not:

```text
A_(t+1) = {best_candidate}
```

---

## 16. Example

User task is initially formulated as:

```text
Product: Deposit_Account
Intent:  Application
```

Active scope:

```text
A0 = {
  (Deposit_Account, Application)
}
```

The three searches return parents that RRF selects, but sufficiency fails.

Measured coverage:

```text
Procedural = 0.81
Policy     = 0.46
Conceptual = 0.73
```

The normalized need vector therefore strongly biases policy.

The common-sense graph contains:

```text
(Deposit_Account, Application)
  -> (Deposit_Account, Eligibility)
     [procedural=0.5, policy=1.0, conceptual=0.5]

(Deposit_Account, Application)
  -> (Deposit_Account, Fees And Charges)
     [procedural=0.0, policy=0.5, conceptual=0.5]
```

One selected parent is tagged:

```text
product_tags: Deposit_Account
intent_families: Application, Eligibility
```

Therefore `Eligibility` receives both:

```text
high graph weak-angle support
+
high FAQ semantic support
```

The next active state becomes:

```text
A1 = {
  (Deposit_Account, Application),
  (Deposit_Account, Eligibility)
}
```

The same procedural, policy, and conceptual queries are rerun against a metadata filter that now permits both scopes.

The original strong Application evidence remains eligible. Eligibility knowledge is added to the pool. RRF then decides which parent IDs converge across the three rankings.

---

## 17. No BFS requirement

v2.6 does not require Breadth-First Search.

Suppose a selected FAQ parent exposes destination `D`, but the static graph represents:

```text
A -> B -> C -> D
```

There is no retrieval requirement to search B and C before D.

The intermediate graph nodes only express semantic structure. They do not imply that their FAQ parents can answer the customer's question.

If D is a valid Product/Intent candidate and the unified score chooses it, D can be added directly:

```text
active scope = active scope union {D}
```

A shortest-path algorithm would add computation without necessarily improving evidence quality.

The graph is therefore used for **candidate scoring**, not mandatory path traversal.

---

## 18. Interaction with existing RRF

The initial v2.6 implementation should leave core RRF behavior unchanged:

```text
RRF_K_CONSTANT = 60
RRF_CANDIDATES_PER_ANGLE = 8 by default
MAX_PARENTS = 4
50% sequential drop-off rule
```

Cosine similarity is used for search diagnosis and candidate expansion only.

RRF remains rank-based because it has a different job:

```text
Cosine magnitude -> diagnose where retrieval is weak
Graph + FAQ signal -> decide what scope to add
RRF -> identify parents consistently surfaced by the angle searches
Sufficiency auditor -> decide whether those parents can answer the task
```

These responsibilities should not be collapsed into one score without evaluation evidence.

---

## 19. Retry budget and termination

To preserve current operational bounds, v2.6 should initially retain the existing maximum of four live retrieval attempts per task:

```text
Attempt 1: initial precise scope
Attempt 2: graph/FAQ-guided expansion 1
Attempt 3: graph/FAQ-guided expansion 2
Attempt 4: graph/FAQ-guided expansion 3
```

After each insufficiency, exactly one unseen Product/Intent node is added.

Terminate expansion when any of the following is true:

- sufficiency succeeds;
- live retry budget is exhausted;
- no valid unseen graph or FAQ candidates remain;
- the active Product/Intent scope already contains every candidate returned by the ranking stage.

On terminal failure, preserve the current `CLARIFICATION_REQUIRED` behavior.

Cache insufficiency should continue to fall back to live retrieval without consuming the live-RAG retry budget.

---

## 20. Separation of navigation evidence and factual evidence

This is a mandatory safety boundary.

Graph edges and FAQ co-intent relationships may influence **where the system searches**.

They may not support customer-facing claims.

Specifically:

```text
Graph weights              -> navigation only
Cosine scores              -> navigation only
FAQ co-intent relationships -> navigation only
RRF rank                    -> evidence selection candidate only
Approved SQLite parent      -> factual evidence after sufficiency approval
```

A parent that failed sufficiency may still expose a useful candidate Product/Intent node for the next search, but its content remains unapproved for factual generation.

The existing evidence approval, outbound groundedness, and clarification safety boundaries remain unchanged.

---

## 21. Proposed implementation changes

### 21.1 `backend/app/orchestration/rag_engine.py`

Extend retrieval output so the caller can access both evidence and navigation diagnostics.

Required additions:

- preserve raw per-angle parent cosine scores;
- expose the selected RRF parent IDs together with their per-angle best similarities;
- hydrate `product_tags` and `intent_families` for navigation purposes;
- continue returning authoritative parent `content` for sufficiency;
- allow the active Product/Intent scope to grow additively using the existing shared-filter mechanism.

Recommended conceptual return structure:

```json
{
  "parents": [],
  "selected_parent_ids": [],
  "angle_diagnostics": {
    "procedural": {},
    "policy": {},
    "conceptual": {}
  },
  "coverage": {
    "procedural": 0.0,
    "policy": 0.0,
    "conceptual": 0.0
  }
}
```

### 21.2 Task state / orchestration

Add fields for:

```text
active_scope_pairs
angle_coverage
angle_need
expansion_history
last_expansion_candidates
```

Node 02e should remain responsible for semantic formulation of the original task and its three query angles.

The retry expansion decision should be deterministic and should not ask Node 02e to guess a broader product or intent.

The expansion logic can be implemented as a dedicated orchestration node or a deterministic helper invoked after an insufficient task result.

### 21.3 Parent hydration

Current hydration selects:

```sql
SELECT parent_id, content
FROM knowledge_parents
```

v2.6 navigation requires:

```sql
SELECT parent_id, product_tags, intent_families, content
FROM knowledge_parents
```

Downstream factual consumers may continue to receive only the approved evidence fields they require.

### 21.4 Graph artifact and generator

Recommended new components:

```text
backend/data/search_graph/
  generate_search_graph.py
  semantic_search_graph.json
  semantic_search_graph_audit.json
```

Exact paths may be adjusted to repository conventions, but graph generation must remain offline and versioned.

---

## 22. Observability requirements

Every live expansion should emit structured telemetry containing:

```text
task_id
attempt number
active scope before expansion
selected parent IDs
per-angle cosine coverage
normalized angle need
candidate nodes
graph score per candidate
FAQ score per candidate
combined score per candidate
selected expansion node
active scope after expansion
RRF-selected parents after rerun
sufficiency result
```

Example log concept:

```text
[SEARCH v2.6]
Task: task_1
Attempt: 2
Coverage: P=.81 Policy=.46 C=.73
Weakest dimension: Policy

Candidates:
Deposit_Account/Eligibility
  graph=.88 faq=.79 combined=.844

Deposit_Account/Fees And Charges
  graph=.46 faq=.00 combined=.276

Selected expansion:
Deposit_Account/Eligibility
```

The exact numeric values above are illustrative only.

---

## 23. Deterministic invariants

The runtime implementation must enforce these invariants:

1. Search scope only expands; it never silently drops a previously active valid Product/Intent node.
2. Every added Product/Intent node is matrix-valid.
3. The same Product/Intent node is never added twice.
4. Graph construction never runs during a customer request.
5. Graph weights never enter factual generation prompts as evidence.
6. FAQ-derived candidates come only from metadata attached to actual retrieved parents.
7. Failed parent content cannot become approved evidence merely because it generated a useful navigation candidate.
8. RRF parent selection remains independent from graph edge weights in the first v2.6 implementation.
9. Sufficiency remains the sole authority on whether retrieved parent evidence is adequate.
10. Terminal insufficiency continues to route to clarification rather than fabricated completion.

---

## 24. Testing strategy

### 24.1 Graph-generation tests

Validate:

- exhaustive ordered pair generation;
- no self-pairs;
- correct matrix filtering;
- canonical product and intent names;
- allowed weight values only;
- no duplicate edges;
- correct edge/no-edge consistency;
- graph version metadata.

### 24.2 Runtime unit tests

Cover at least:

**Weak-angle graph expansion**  
Policy is weakest; graph-only Eligibility candidate wins.

**FAQ-only expansion**  
A selected multi-intent parent exposes a valid destination absent from direct graph neighbours; destination can still be added.

**Signal agreement**  
Graph and FAQ both identify the same destination; candidate receives both contributions.

**Additive scope preservation**  
After expansion, the original Product/Intent scope remains present.

**Cross-product expansion**  
A valid cross-product destination may be added when selected by the scoring model.

**Invalid FAQ pair rejection**  
A metadata combination not present in the authoritative matrix is discarded.

**Duplicate candidate suppression**  
An already active node is never re-added.

**No candidate**  
An insufficient search with no unseen candidate terminates safely.

**RRF isolation**  
Changing graph weights must not directly modify RRF points for a fixed set of raw rankings.

### 24.3 Integration tests

For representative banking questions, capture:

```text
initial scope
angle coverage
expansion decision
expanded scope
RRF parent IDs
sufficiency result
```

Compare against v2.5.

---

## 25. Evaluation metrics

v2.6 should be accepted because it improves retrieval, not because the graph is elegant.

Measure at minimum:

- task sufficiency success rate;
- percentage of tasks resolved on attempt 1, 2, 3, and 4;
- mean live retrieval attempts per task;
- terminal `CLARIFICATION_REQUIRED` rate;
- mean Chroma queries per task;
- retrieval latency;
- rate at which the chosen expansion introduces an eventually approved parent;
- rate of irrelevant Product/Intent expansions;
- parent precision against a manually labelled evaluation set;
- proportion of expansions driven by graph-only, FAQ-only, or agreement signals.

The primary comparison should be an A/B evaluation:

```text
v2.5 fixed expansion ladder
vs.
v2.6 three-angle graph-guided expansion
```

The graph should be considered justified only if it improves evidence sufficiency and/or reduces unnecessary retries without materially reducing precision.

---

## 26. Parameters to tune, not hard-code as truth

The following values should be configuration parameters and validated empirically:

```text
lambda_graph
lambda_faq
RRF_CANDIDATES_PER_ANGLE
maximum live attempts
one-node vs multi-node expansion per attempt
cosine coverage aggregation method
```

The first v2.6 implementation should prefer the simplest settings:

```text
edge weights: 0.0 / 0.5 / 1.0
one new node per retry
lambda_graph = 0.60
lambda_faq   = 0.40
existing RRF constants unchanged
existing four-live-attempt bound unchanged
```

---

## 27. Architecture summary

Search Strategy v2.6 can be summarized as four layers:

### Layer 1 — LLM semantic formulation

The LLM interprets the user's task and produces the procedural, policy, and conceptual query phrases.

Offline, an LLM also compiles product/intent common sense into a directed graph with three-angle edge weights.

### Layer 2 — Vector retrieval

Chroma retrieves child-vector candidates under the current additive Product/Intent scope and exposes cosine similarity.

### Layer 3 — Deterministic search expansion

If sufficiency fails:

```text
angle cosine imbalance
        +
common-sense three-angle graph
        +
FAQ parent co-tag semantics
        ->
mathematical next-scope selection
```

The selected Product/Intent node is added to the existing active scope.

### Layer 4 — Evidence convergence and approval

RRF selects parent IDs from the enlarged search pool. SQLite hydrates authoritative parent content. The sufficiency auditor decides whether the evidence can answer the task.

The final conceptual loop is:

```text
Three-angle query
      ↓
Chroma retrieval
      ↓
RRF convergence
      ↓
Sufficiency
      ↓ insufficient
Measure three-angle weakness
      +
Graph semantic prior
      +
FAQ semantic neighbour signal
      ↓
Add best Product/Intent scope
      ↓
Repeat three-angle retrieval
      ↓
RRF reconverges
```

The defining principle is:

> **The semantic model decides what relationships mean; runtime mathematics decides which semantic territory to add; RRF and sufficiency decide which evidence survives.**
