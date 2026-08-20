# Direct Parent-Neighbourhood Dispersion Diagnostic v1.0

## Status

**Post-hoc research diagnostic. Not a production rule.**

This report records an exploratory analysis of the saved 80-case retrieval
angle-ablation data. It investigates whether the geometry of the *direct
retrieval result set* can predict when an additional narrative query view is
likely to improve evidence retrieval.

The result is promising enough to justify a pre-registered held-out
evaluation. It does not justify a production threshold or a universal bridge
trigger without that evaluation.

## Question

The preceding angle ablation established that a complete narrative view can
occasionally repair customer-language-to-FAQ-language mismatch, but that
adding views blindly can dilute good direct evidence.

The diagnostic question is therefore:

> Can the direct retrieval result itself indicate whether a narrative bridge
> is needed, before deciding to run an additional vector search?

This is deliberately different from measuring the cosine spread among the
three generated query variants. The earlier three-variant dispersion test was
not predictive.

## Source data and retrieval contract

The analysis joins the following frozen artifacts:

- `results/threeway_narrative_validation_v1.json` for the direct queries and
  complete procedural, policy/requirements, and conceptual variants;
- `results/threeway_angle_ablation_v1.json` for direct and single-angle RRF
  outcomes; and
- `backend/data/etl/history/generic_chunking/artifacts/ready_for_ingestion.json`
  for the historical generic child-vector text used by the benchmark.

The source ablation contains 80 Banking77-style customer messages across
eight mapped Horizon support needs. Every arm used the same metadata filter,
candidate depth, parent-level RRF contract, four-parent cap, and drop-off
rule. The expected parent is a retrieval diagnostic proxy rather than the only
possible sufficient evidence source.

## Parent-neighbourhood measure

For each direct-query RRF-selected parent `p`, compute its best child-vector
similarity to direct query `q`:

```text
parent_sim(q, p)
  = max cosine(q, child)
    over stored generic child chunks belonging to p
```

For the direct-selected parent set `S`, define:

```text
parent_dispersion(q)
  = standard deviation of parent_sim(q, p), for p in S
```

Two companion features were also measured:

```text
parent_range(q)   = max(parent_sim) - min(parent_sim)
top_parent_gap(q) = highest parent_sim - second-highest parent_sim
```

The saved ablation artifact retained parent IDs but not raw per-hit Chroma
cosine scores. Scores were therefore recomputed with the same Chroma default
embedding function over the benchmark child text. This is suitable for this
diagnostic, but live validation must record and use the actual raw retrieval
scores rather than reconstructed values.

## Interpretation hypothesis

```text
High parent dispersion
  -> one direct parent has a clearly stronger semantic match
  -> direct retrieval may already have a focused landing point
  -> an extra view is more likely to add RRF noise

Low parent dispersion
  -> several selected parents are similarly close to the direct wording
  -> direct retrieval occupies a broad or ambiguous semantic neighbourhood
  -> a focused narrative view may supply a useful missing direction
```

This is an association hypothesis, not a causal claim. It should be used only
as a trigger candidate alongside evidence sufficiency and semantic-equivalence
checks.

## Results

### Direct parent-set composition

| Direct-selected parent count | Tasks |
|---|---:|
| 3 | 4 |
| 4 | 76 |

The mean reconstructed parent-score standard deviation was `0.0549`, ranging
from `0.0101` to `0.1308`.

### Association with angle-query benefit

| Direct retrieval feature | Outcome | Pearson r | Spearman rho | p-value (Pearson) |
|---|---|---:|---:|---:|
| Parent cosine SD | Best available single-angle MRR gain | -0.368 | -0.413 | 0.001 |
| Parent cosine SD | Any single angle improves | -0.376 | -0.404 | 0.001 |
| Parent cosine SD | Policy-angle MRR gain | -0.283 | -0.328 | 0.011 |
| Parent cosine range | Best available single-angle MRR gain | -0.379 | -0.419 | 0.001 |
| Top-parent gap | Best available single-angle MRR gain | -0.323 | -0.328 | 0.004 |
| Mean parent cosine | Best available single-angle MRR gain | -0.091 | 0.078 | 0.424 |

The result is directional: **lower direct-parent dispersion was associated
with more opportunity for a narrative angle to improve retrieval**. The mean
similarity alone was not predictive.

### Dispersion quartiles

| Parent cosine SD quartile | Tasks where any angle improved | Mean best available MRR delta | Mean policy MRR delta |
|---|---:|---:|---:|
| 0.010-0.038 | 10 / 20 | +0.1833 | +0.1417 |
| 0.038-0.053 | 3 / 20 | +0.0250 | -0.0375 |
| 0.053-0.069 | 3 / 20 | +0.0333 | -0.0292 |
| 0.069-0.131 | 1 / 20 | +0.0250 | +0.0000 |

### Relationship to direct expected-parent rank

| Direct expected-parent result | Tasks | Mean parent cosine SD | Median parent cosine SD |
|---|---:|---:|---:|
| Expected parent rank 1 | 48 | 0.0635 | 0.0571 |
| Expected parent rank 2 | 12 | 0.0518 | 0.0520 |
| Expected parent rank 3 or 4 | 8 | 0.0376 | 0.0381 |
| Expected parent absent from top four | 12 | 0.0349 | 0.0318 |

The correlation between parent dispersion and expected-parent rank, treating
an absent expected parent as rank 5, was Spearman rho `-0.520` (`p < 0.001`).
This explains why the feature is useful in this particular benchmark: lower
dispersion is associated with direct retrieval that is less decisive with
respect to the reviewed target.

## Contrast: three-query dispersion and within-query z-scores

The direct-to-variant cosine spread across procedural, policy, and conceptual
queries did **not** predict gain:

| Feature | Outcome | Pearson r | p-value |
|---|---|---:|---:|
| Three-angle cosine SD | Best available angle gain | 0.044 | 0.700 |
| Three-angle cosine SD | Any angle improves | -0.059 | 0.605 |
| Individual within-task angle z-score | Its own MRR gain | -0.042 | 0.517 |

Relative z-scores discard the absolute novelty information needed to tell
apart three redundant variants (for example, `0.88, 0.90, 0.92`) from three
genuinely different variants (for example, `0.28, 0.30, 0.32`). They should
not be used as the bridge selector.

## Candidate deterministic control flow

The proposed use is a **bridge trigger**, not a replacement for semantic task
formulation or evidence approval:

```text
resolved task
  -> direct vector retrieval
  -> direct-selected parent score distribution
  -> evidence sufficiency audit
  -> insufficient + low parent dispersion?
       -> run one selected narrative bridge query
       -> RRF direct and bridge results
       -> repeat sufficiency audit
       -> otherwise retain direct retrieval result
```

The dispersion computation itself is deterministic and has no LLM cost. If
the task formulator already produces equivalent candidate angle views, the
bridge decision also needs no additional LLM call. If views are generated on
demand, defer that LLM call until the deterministic trigger fires.

Angle selection remains a separate decision. Initial routing should use the
resolved task semantics and an absolute direct-to-variant novelty gate:

- policy/requirements for conditions, timing, eligibility, fees, limits,
  permissions, cancellation, and restrictions;
- procedural for actions, setup, tracking, transfers, and workflows; and
- conceptual for features, capabilities, and product mechanics.

Every bridge must remain semantically equivalent to the resolved task: it may
not change product, user role, money direction, requested outcome, or a
material constraint, and it may not invent a bank fact or procedure.

## Limitations

1. **Post-hoc feature discovery.** Several relationships and thresholds were
   inspected on the same data; the reported association is exploratory.
2. **Limited effective sample size.** The 80 messages comprise repeated
   phrasings over only eight mapped needs and reviewed target parents. They
   are not 80 independent production retrieval tasks.
3. **Narrow domain coverage.** The sample is mainly servicing/support work and
   does not adequately represent product features, rates, wealth, insurance,
   disputes, or multi-component tasks.
4. **Expected-parent proxy.** Multiple parents can support a correct answer;
   rank of one reviewed parent is not a complete evidence-quality measure.
5. **Reconstructed scores.** The score calculation uses stored benchmark child
   text because original raw Chroma hit scores were not persisted.
6. **Selection-conditioned feature.** The measure intentionally uses the
   direct-selected parent set. This is deployable because the set is available
   after direct retrieval, but it must be evaluated under the exact live RRF
   and filter contract.

## Required validation before adoption

1. Freeze the feature definition, score source, threshold selection process,
   and bridge decision before collecting the confirmation set.
2. Evaluate at least 60-100 **distinct FAQ targets**, stratified by product
   and intent. Several user phrasings per target are useful, but split and
   bootstrap by target so paraphrases do not inflate confidence.
3. Persist raw direct-query child hit IDs, raw cosine scores, RRF scores,
   parent IDs, metadata filter, selected-parent order, bridge decision, and
   final evidence outcome.
4. Compare direct-only, always-bridge, and low-dispersion-triggered bridge
   policies using evidence-set Recall@4, precision@4, MRR/nDCG, sufficiency
   rate, latency, and unnecessary-bridge rate.
5. Validate the dispersion trigger separately from the angle-selection rule.
   A low-dispersion trigger says a bridge may be useful; it does not establish
   whether policy, procedural, or conceptual wording is the correct bridge.

## Provisional conclusion

The direct parent-neighbourhood score distribution is a stronger candidate
signal than query-variant dispersion. It uses information already generated
by direct vector retrieval and can gate bridge search deterministically. The
finding should proceed to a locked, target-level held-out evaluation rather
than directly into production behaviour.
