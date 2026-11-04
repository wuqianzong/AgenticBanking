# Retrieval Angle Ablation Findings v1.0

## Purpose

This document records the retrieval experiments used to assess whether a
resolved banking task benefits from additional narrative query views. It is an
evidence record for future retrieval design work; it does not change the live
RAG workflow.

The central question was:

> Does direct retrieval improve when it is augmented with procedural,
> policy/requirements, and conceptual narrative views?

## Experiment Scope

The test used 80 held-out Banking77-style customer utterances, mapped to eight
Horizon FAQ task categories:

- activate a card
- change PIN
- card arrival
- lost or stolen card
- transfer timing
- cancel transfer
- transfer into account
- forgotten passcode

The collection was an isolated historical generic Q&A benchmark index with
the active canonical Product × Intent metadata applied. It was not the live
production tri-angle collection. Consequently, these results evaluate
query-variation and RRF behaviour on this benchmark, not the entire
production RAG architecture.

Each test case had a mapped `expected_parent_id`. This is a useful retrieval
proxy, but it is not the only parent that could support a correct answer.

## Query Arms

The initial three-way experiment compared:

1. Direct customer query.
2. Direct query plus complete procedural, policy, and conceptual variants.
3. Direct query plus FAQ patterns generated from the broad Product × Intent
   scope.

The FAQ-scope-pattern arm was rejected as a candidate default: generic scope
patterns sometimes promoted broad in-scope FAQs while suppressing the user's
specific clue, such as *China*, *wrong account*, or *password*.

The follow-up ablation therefore retained only direct retrieval and the three
generated angle variants. Every arm used the same RRF candidate-selection
contract: candidate depth, RRF constant, top-four parent cap, and 50% drop-off
rule. The only variable was the additional standalone narrative view.

| Arm | Query views |
|---|---|
| Direct anchor | Resolved customer task only |
| Direct + procedural | Anchor and procedural narrative view |
| Direct + policy | Anchor and policy/requirements narrative view |
| Direct + conceptual | Anchor and conceptual/mechanism narrative view |
| Direct + all three | Anchor and all three narrative views |

The ablation used each generated narrative as a standalone query view. This
avoids the earlier experimental construction of appending the anchor text to
every variant, which made the RRF inputs highly correlated.

## Retrieval Results

| Retrieval arm | Expected-parent Recall@4 | MRR@4 |
|---|---:|---:|
| Direct anchor only | 68/80 (85.0%) | 0.7042 |
| Direct + procedural | 72/80 (90.0%) | 0.7010 |
| Direct + policy/requirements | 72/80 (90.0%) | **0.7229** |
| Direct + conceptual | 69/80 (86.2%) | 0.6958 |
| Direct + all three | 71/80 (88.8%) | 0.7010 |

### Paired Rank Movement Against Direct Anchor

| Added view | Improved rank | Regressed rank | Tie | Expected-parent top-four gains / losses |
|---|---:|---:|---:|---:|
| Procedural | 10 | 7 | 63 | 4 / 0 |
| Policy/requirements | **11** | **5** | 64 | 4 / 0 |
| Conceptual | 11 | 11 | 58 | 2 / 1 |
| All three together | 11 | 10 | 59 | 3 / 0 |

## Detailed Data and Statistical Appendix

### A. Initial Three-Way Answerability Evaluation

Before the per-angle ablation, a blinded LLM judge reviewed the original
three-way strategy evidence sets. The judge received the customer message and
the selected raw FAQ parent content, but not the strategy name or parent IDs.

| Strategy | Full | Partial | None | Ordinal total* |
|---|---:|---:|---:|---:|
| Direct query | 47 | 17 | 16 | 111 |
| Direct + complete three-angle variants | 46 | 18 | 16 | 110 |
| Direct + FAQ-scope patterns | 48 | 12 | 20 | 108 |

\*Ordinal total assigns `full = 2`, `partial = 1`, and `none = 0`. It is a
descriptive measure only, not a validated utility score.

Paired verdict movement relative to direct retrieval was identical in count for
the two expanded strategies:

| Comparison | Improved | Tie | Regressed |
|---|---:|---:|---:|
| Direct → complete three-angle | 8 | 64 | 8 |
| Direct → FAQ-scope patterns | 8 | 64 | 8 |

These counts must not be read as clean retrieval effects. The evidence order
was randomized separately by strategy and each set was judged once. Therefore,
some verdict movement occurred even where retrieval produced the same evidence
set:

| Comparison | Changed verdict with identical selected parent set |
|---|---:|
| Direct → complete three-angle | 5 of 16 changed verdicts |
| Direct → FAQ-scope patterns | 6 of 16 changed verdicts |

This identifies material evaluator/order variance. It is one reason the
retrieval-only ablation below is the more reliable signal for comparing angles.

### B. Expected-Parent Retrieval Comparison

The expected-parent proxy from the frozen benchmark gives an objective
retrieval measure independent of the LLM answerability judge.

| Strategy | Expected parent in top four | MRR@4 |
|---|---:|---:|
| Direct query | 68/80 (85.0%) | 0.7042 |
| Complete three-angle, original construction | 70/80 (87.5%) | 0.7130 |
| FAQ-scope patterns, original construction | 67/80 (83.8%) | 0.6250 |

The original complete-three-angle arm had a small expected-parent gain, while
the scope-pattern arm degraded rank quality. The ablation then determined
which angle was responsible.

### C. Per-Category Ablation Results

Each cell is `Expected-parent Recall@4 / MRR@4` over ten cases in that
category.

| Category | Direct | + Procedural | + Policy | + Conceptual | + All three |
|---|---|---|---|---|---|
| Activate card | 9/10 · 0.8500 | 9/10 · 0.9000 | 10/10 · 0.9333 | 9/10 · 0.8500 | 10/10 · 0.9333 |
| Change PIN | 10/10 · 1.0000 | 10/10 · 1.0000 | 10/10 · 1.0000 | 10/10 · 1.0000 | 10/10 · 1.0000 |
| Card arrival | 7/10 · 0.3250 | 8/10 · 0.2833 | 8/10 · 0.3500 | 8/10 · 0.3333 | 8/10 · 0.3500 |
| Lost/stolen card | 9/10 · 0.8000 | 9/10 · 0.7000 | 9/10 · 0.6500 | 9/10 · 0.6000 | 9/10 · 0.5500 |
| Transfer timing | 9/10 · 0.8500 | 9/10 · 0.8333 | 9/10 · 0.8500 | 9/10 · 0.8500 | 9/10 · 0.8500 |
| Cancel transfer | 10/10 · 0.6000 | 10/10 · 0.5833 | 10/10 · 0.7500 | 10/10 · 0.7500 | 10/10 · 0.7000 |
| Transfer into account | 4/10 · 0.2083 | 7/10 · 0.3083 | 6/10 · 0.2500 | 4/10 · 0.1833 | 5/10 · 0.2250 |
| Forgotten passcode | 10/10 · 1.0000 | 10/10 · 1.0000 | 10/10 · 1.0000 | 10/10 · 1.0000 | 10/10 · 1.0000 |

### D. Paired Uncertainty Estimates

The following comparisons use paired case-level bootstrap resampling
(`20,000` resamples, seed `20260819`). Confidence intervals describe variation
within this 80-case benchmark; they do not establish production-level
statistical significance.

| Added view | Δ Recall@4 | 95% bootstrap CI | Δ MRR@4 | 95% bootstrap CI | Exact top-four gains / losses |
|---|---:|---:|---:|---:|---:|
| Procedural | +5.00 pp | +1.25 to +10.00 pp | -0.0031 | -0.0365 to +0.0281 | 4 / 0 |
| Policy/requirements | +5.00 pp | +1.25 to +10.00 pp | +0.0187 | -0.0156 to +0.0542 | 4 / 0 |
| Conceptual | +1.25 pp | -2.50 to +6.25 pp | -0.0083 | -0.0521 to +0.0344 | 2 / 1 |
| All three | +3.75 pp | 0.00 to +8.75 pp | -0.0031 | -0.0490 to +0.0417 | 3 / 0 |

The policy/requirements view has the strongest point estimate, but its MRR
confidence interval includes zero. This is promising evidence for a targeted
follow-up, not evidence for a universal production rule.

### E. Raw Artifacts

The complete case-level data, including user utterances, generated views,
selected parent IDs, expected-parent rank, and blinded judge outputs, remains
available in the following local artifacts:

- `results/threeway_narrative_validation_v1.json`
- `results/threeway_angle_ablation_v1.json`

The executable diagnostic is:

- `scripts/run_threeway_angle_ablation.py`

## Findings

### 1. The angle categories are useful, but not uniformly useful

The three categories remain sensible semantic perspectives:

- **Procedural:** what the user needs to do.
- **Policy/requirements:** eligibility, timing, fees, restrictions, and other
  conditions.
- **Conceptual:** how a product or service works.

However, they should not automatically be treated as equally valuable for
every task.

### 2. Policy/requirements was the strongest angle in this sample

It had the highest MRR@4, the greatest net favourable rank movement, and four
new expected-parent top-four hits without losing any existing top-four hit.

This is credible for the tested tasks, which frequently involve delivery
windows, transfer timing, cancellation conditions, and access requirements.

### 3. Procedural improved coverage but not rank quality

Procedural views also added four expected parents to the top four without
losing a prior hit. Its MRR declined slightly, however. The procedural view
can help recover a relevant FAQ while also changing the order of otherwise
strong direct-query candidates.

### 4. Conceptual views added noise for this task mix

The conceptual arm had only a marginal Recall@4 gain and a lower MRR@4 than
direct retrieval. It produced equal numbers of rank improvements and
regressions.

This does **not** establish that conceptual views are generally weak. The
sample is dominated by servicing and support tasks; it contains almost no
questions about product features, limits, interest, fees, investments, or
insurance mechanics. Those are the task families where conceptual views need
their own evaluation.

### 5. Fusing every angle can dilute the best one

The all-three arm underperformed policy-only retrieval:

```text
Policy only:   Recall@4 90.0%, MRR@4 0.7229
All three:     Recall@4 88.8%, MRR@4 0.7010
```

RRF rewards parents that rank well across several lists. If an added angle is
generic or poorly aligned, it can provide repeated votes for broadly related
FAQs and displace more useful direct or policy evidence.

## Interpretation

The data supports a **selective-angle** design rather than a mandatory
three-angle design:

```text
Resolved task
  → LLM selects the most useful narrative perspective
  → LLM writes one complete, standalone, semantically equivalent query view
  → Direct query + selected view are retrieved and fused by RRF
```

Illustrative routing guideline:

| Task need | Preferred narrative view |
|---|---|
| Steps, activation, application flow, transfers | Procedural |
| Eligibility, fees, rates, timing, restrictions, requirements | Policy/requirements |
| Features, limits, benefits, product mechanics | Conceptual |

The selected view must preserve the full resolved task. It must not invent a
bank procedure, silently change the product, alter money direction, omit a
material constraint, or introduce a new request.

## Important Evaluation Limitations

1. **Single expected parent.** A different parent, or a combination of parents,
   can still support a good answer. Recall and MRR are therefore diagnostic
   retrieval measures, not final response-quality measures.
2. **Limited category coverage.** The 80 cases do not adequately evaluate
   product-knowledge and wealth-related tasks.
3. **Sample size.** A two-to-four case Recall@4 gain is promising but too small
   to treat as conclusive without a broader, stratified evaluation.
4. **Prior answerability judging was noisy.** Identical evidence sets sometimes
   received different LLM verdicts because evidence order was separately
   shuffled and judged once. Future answerability testing must use fixed order,
   explicit treatment of unavailability as a valid answer, and repeat or
   multi-model adjudication for disagreements.

## Next Evaluation Design

Before changing production retrieval:

1. Build a stratified benchmark covering procedural, policy/requirements, and
   conceptual task families.
2. Generate only one selected, complete narrative view per task.
3. Validate the view for semantic equivalence before retrieval.
4. Compare direct-only against direct-plus-selected-view with identical RRF
   mechanics.
5. Measure acceptable-evidence Recall@4, MRR/nDCG, full-answer rate, latency,
   and paired gains/losses.
6. Treat the policy-only result above as a hypothesis to validate, not a
   universal production rule.

## Reproducibility Artifacts

- `scripts/run_threeway_narrative_validation.py`
- `scripts/run_threeway_angle_ablation.py`
- `results/threeway_narrative_validation_v1.json`
- `results/threeway_angle_ablation_v1.json`
