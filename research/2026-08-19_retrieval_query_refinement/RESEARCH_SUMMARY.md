# Retrieval Query Refinement Research Summary — 2026-08-19

## Executive Summary

This study investigated how to improve semantic retrieval for Horizon FAQ
questions without changing the production chatbot. The work examined three
separate layers:

1. **Child-vector representation:** whether to use original Question-and-Answer
   child chunks or a three-angle child-vector representation.
2. **Query refinement:** whether to add FAQ-shaped narratives derived from a
   Product × Intent scope.
3. **Narrative-angle search:** whether procedural, policy/requirements, and
   conceptual query views add useful retrieval signal to a direct query.

The resulting research direction is intentionally conservative:

```text
Keep original Q&A child-vector retrieval as the current research baseline.
Keep one noise-reduced, resolved task query as the default retrieval query.
Do not use broad Product × Intent narrative expansion.
Investigate one selectively chosen, complete narrative angle as an optional
direct-query supplement only after a broader controlled evaluation.
```

No result in this folder changes the live RAG workflow. The active runtime
documents and ETL assets remain under `docs/` and `backend/`.

## Research Map

```text
Raw customer message
  │
  ├─ 1. Child-vector representation decision
  │    ├─ Original Q&A child chunks retained as research baseline
  │    └─ Stored three-angle child-vector representation not selected
  │       as the current baseline because added representation noise and
  │       metadata-history complexity were not justified by a stable gain.
  │
  ├─ 2. Query formulation decision
  │    ├─ One noise-reduced, complete task query retained as default
  │    ├─ Forced partial procedural/policy/conceptual splitting retired
  │    └─ Broad Product × Intent FAQ-pattern expansion rejected
  │
  └─ 3. Current research hypothesis
       └─ Direct task query + one LLM-selected, complete, equivalent
          narrative view, fused with the same RRF contract.
```

## 1. Child-Vector Representation

### Question

Should retrieval use a database whose child vectors are stored as forced
procedural, policy, and conceptual paraphrases, or original FAQ Question and
Answer chunks?

### Finding

The study retained the original Q&A child-vector collection as the research
baseline. A three-angle child-vector representation was **not selected as the
current direction**.

The rationale was architectural as well as empirical:

- Original Q&A chunks preserve the authoritative FAQ wording and require no
  additional semantic paraphrase layer before embedding.
- Forced three-angle child representations can introduce paraphrase drift and
  false similarity when an FAQ is naturally dominated by one narrative form.
- The historical three-angle source also required metadata reconciliation;
  original Q&A chunks provide a cleaner baseline for evaluating query-side
  changes.
- Controlled historical diagnostics did not demonstrate a stable material gain
  for fixed partial-angle RRF over a strong, noise-reduced direct query.

This is a research baseline decision, not a deletion of the tri-angle ETL
history. The reconciled tri-angle staging and active ETL documentation remain
under `backend/data` and `docs/` for future, separately governed work.

## 2. Direct Query Refinement

### Retained Principle: Noise Reduction

Complex user messages should first be decomposed into a resolved retrieval
task. The task query should be complete, concise, and preserve product,
intent, direction, constraints, and requested outcome.

The retained baseline is:

```text
Customer request → resolved task → one complete, noise-reduced query
```

Historical fixed-angle diagnostics found no stable gain from splitting the
task into partial procedural, policy, and conceptual queries. In the reviewed
80-formulation fixture:

| Method | Recall@4 | MRR@4 |
|---|---:|---:|
| Combined, cleaned direct query | 80/80 | 0.925 |
| Fixed partial-angle RRF | 80/80 | 0.897 |

The fixed-angle approach is retired because its branches can omit parts of a
task or implicitly change its semantic boundary.

## 3. Product × Intent Narrative Variation

### Hypothesis

Use valid Product × Intent pairs to create FAQ-shaped additional query views.

### Finding

The broad-scope version is rejected as a default retrieval strategy.

The major failure mode was **scope contamination**. A Product × Intent scope
such as `Deposit_Account × Payments_And_Transfers` contains many valid but
distinct topics: funding, FAST, PayNow, AutoPay, transfer cancellation, and
other servicing content. Reusing the same broad FAQ narratives for every task
in that scope caused RRF to promote generic in-scope records while suppressing
the customer's specific clue.

Examples observed in the blinded evaluation:

- It could help translate “transfer money into my account” to the FAQ wording
  “fund a savings account.”
- It could harm questions whose unique clue mattered, such as “China,” “wrong
  account,” or a specific transaction-reversal context.

| Strategy | Expected parent in top four | MRR@4 |
|---|---:|---:|
| Direct query | 68/80 (85.0%) | 0.7042 |
| Broad Product × Intent patterns | 67/80 (83.8%) | 0.6250 |

Therefore Product × Intent remains a **filter boundary**, not a universal
query-narrative generator.

## 4. Complete Narrative-Angle Search

The research then changed the question from “should all angles run?” to
“which narrative perspective, if any, adds useful evidence for this task?”

Each valid additional view must be a complete standalone restatement of the
resolved task. It cannot be the original query with a generic suffix, and it
cannot add unsupported banking facts.

Illustration:

```text
Resolved task:
How can I fund my Horizon savings account from another bank?

Procedural view:
How do I transfer money from an external bank into my Horizon savings account?

Policy/requirements view:
What transfer methods and requirements apply when funding my Horizon savings
account from another bank?

Conceptual view:
How are incoming transfers from another bank credited to a Horizon savings
account?
```

### 80-Case Retrieval-Only Ablation

All arms used the same metadata filters, RRF contract, four-parent cap, and
drop-off rule. The only variable was the added standalone view.

| Retrieval arm | Expected-parent Recall@4 | MRR@4 |
|---|---:|---:|
| Direct anchor only | 68/80 (85.0%) | 0.7042 |
| Direct + procedural | 72/80 (90.0%) | 0.7010 |
| Direct + policy/requirements | 72/80 (90.0%) | **0.7229** |
| Direct + conceptual | 69/80 (86.2%) | 0.6958 |
| Direct + all three | 71/80 (88.8%) | 0.7010 |

### Interpretation

- **Policy/requirements** was the strongest point estimate in this procedural
  and support-heavy sample.
- **Procedural** improved top-four coverage but did not improve MRR.
- **Conceptual** was noisy in this sample; the benchmark did not contain enough
  genuine product-mechanics questions to judge it fairly.
- **All three together** diluted the policy-only result. RRF does not guarantee
  that more query views are better when one view is weak or generic.

The policy MRR improvement is promising, but its bootstrap confidence interval
crosses zero. It is a research hypothesis, not a production adoption result.

## Current Conclusion

The design to evaluate next is:

```text
Resolved task
  → LLM selects whether an extra angle is useful
  → LLM selects one appropriate perspective
     - procedural for actions and processes
     - policy/requirements for conditions and restrictions
     - conceptual for product mechanics
  → LLM generates one complete equivalent narrative view
  → direct query + selected view use the same RRF selection contract
```

The generator must pass a semantic-equivalence validation before retrieval:

- same task and requested outcome;
- no omitted material constraint;
- no changed product, money direction, or user role; and
- no invented procedure, feature, or policy.

## What Is Retired, Retained, and Open

| Status | Decision |
|---|---|
| Retired | Forced partial-angle query splitting |
| Retired | Broad Product × Intent FAQ-pattern expansion |
| Not selected as current baseline | Three-angle child-vector representation |
| Retained | Original Q&A child-vector research baseline |
| Retained | One complete, noise-reduced direct task query |
| Open research hypothesis | Direct query plus one selected, complete narrative angle |
| Open research need | Larger stratified evaluation with product-knowledge, pricing, wealth, and multi-component tasks |

## Study Layout

```text
2026-08-19_retrieval_query_refinement/
├── RESEARCH_SUMMARY.md       # This decision map
├── data/                     # External BANKING77 evaluation data
├── fixtures/                 # Frozen benchmark cases and mappings
├── reports/                  # Methodology and results documents
├── results/                  # Case-level output and blinded judgments
└── scripts/                  # Read-only research harnesses
```

## Supporting Documents

- [Retrieval Query Strategy Evaluation](reports/RETRIEVAL_QUERY_STRATEGY_EVALUATION_v1.md)
- [Retrieval Angle Ablation Findings](reports/RETRIEVAL_ANGLE_ABLATION_FINDINGS_v1.md)

## Reproducibility Notes

- The scripts are evaluation-only and use an isolated generic benchmark
  collection; they do not write to the production Chroma collection or SQLite
  knowledge base.
- The raw case-level outcomes are preserved in `results/`.
- LLM-based answerability results should be interpreted alongside their judge
  limitations: fixed evidence order, explicit handling of verified numeric
  placeholders, and repeat adjudication are required for a future decisive
  evaluation.
