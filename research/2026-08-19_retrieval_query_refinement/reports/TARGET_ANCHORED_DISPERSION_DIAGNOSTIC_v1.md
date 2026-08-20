# Target-Anchored Dispersion Diagnostic v1

## Question

Does the standard deviation of direct-query cosine scores across the parents
selected by direct retrieval predict whether adding one frozen narrative view
improves the rank of the known FAQ target?

## Setup

This diagnostic reuses `fixtures/retrieval_query_strategy_eval_v1.json`:

- 60 synthetic formulations, arranged as three variants of each of 20 reviewed
  FAQ targets;
- known target parent and fixed Product x Intent scope for every formulation;
- one direct customer-query view, plus each pre-existing procedural, policy,
  or conceptual narrative view in turn;
- historical generic child chunks, canonical metadata, exact in-memory cosine
  ranking, parent-level RRF (`K=60`), eight children per view, and four parents
  maximum.

For a direct query, the diagnostic calculates

`std(max child cosine for each returned parent)`

over the parents selected by direct retrieval. Gain is the change in MRR@4
after adding exactly one angle. “Best of three” is reported only as an oracle
upper bound; it is not a deployable selector.

The harness is `scripts/run_target_anchored_dispersion_diagnostic.py`; raw
results are in `results/target_anchored_dispersion_diagnostic_v1.json`.

## Results

At the **60-formulation row level**, the direction is weakly consistent with
the earlier hypothesis but is not significant:

| Measure | Result |
|---|---:|
| Best-of-three mean MRR@4 gain | +0.0722 |
| Formulations improved by an oracle angle | 8/60 |
| Spearman: dispersion vs best-of-three gain | -0.172 (p=.189) |
| Point-biserial: dispersion vs any oracle improvement | -0.173 (p=.185) |
| Lowest dispersion quartile: mean oracle gain | +0.0667 (2/15 improved) |
| Highest dispersion quartile: mean oracle gain | 0.0000 (0/15 improved) |

The single fixed views do not supply a usable angle rule:

| Added view | Mean MRR@4 delta | Improved | Regressed | Dispersion/gain Spearman |
|---|---:|---:|---:|---:|
| Procedural | -0.0208 | 3 | 6 | +0.056 (p=.669) |
| Policy | +0.0389 | 6 | 1 | -0.060 (p=.650) |
| Conceptual | +0.0028 | 5 | 5 | -0.168 (p=.198) |

## Correct unit of analysis: FAQ target

The 60 rows are **not independent**: each group of three was generated from
the same reviewed anchor and has the same expected FAQ parent. Aggregating
direct dispersion and oracle gain to the 20 unique parent targets removes the
apparent relationship:

| Target-level measure | Result |
|---|---:|
| Spearman: mean dispersion vs mean oracle gain | +0.006 (p=.979) |
| Pearson: mean dispersion vs mean oracle gain | -0.120 (p=.614) |

This is a negative result. The fixture has not reproduced a reliable
dispersion-to-bridge relationship.

## Interpretation

This outcome is unsurprising for a target-anchored, direct-favorable suite.
The synthetic messages were generated from reviewed canonical anchors, so
direct retrieval already places the expected target strongly for most rows.
Only eight rows improve under an oracle that already knows which of three
angles is best. The data can describe how fixed narratives behave in this
controlled corpus, but cannot justify a production threshold or an angle
selector.

The earlier apparent Banking77 relationship remains exploratory only because
its expected Horizon parent mapping has not yet been independently judged.
Together, the two experiments say:

1. Direct parent-score dispersion is a plausible *candidate feature*, not a
   validated bridge trigger.
2. Query geometry alone does not select procedural, policy, or conceptual
   framing in this data.
3. A deployable rule requires independently authored queries and frozen,
   blind-reviewed acceptable parent sets before threshold fitting or testing.
