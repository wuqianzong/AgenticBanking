# Judged-Target Dispersion Rerun v1

## Label correction

This rerun replaces the static BANKING77 category-to-parent mapping with
existing blind Gemini judgments from
`results/threeway_narrative_validation_v1.json`. No new LLM calls were made.

The earlier judge presented hydrated FAQ evidence in a deterministic shuffled
order and returned `relevant_evidence_indexes`. The rerun reverses that
shuffle and maps each index back to its parent ID. A parent is accepted only
when it was marked relevant in at least two separate **full-answerability**
strategy presentations. This conservative rule retains multiple parents when
the judge found multiple FAQ records genuinely useful.

- Original BANKING77 questions: 80
- Questions with reconstructed conservative acceptable set: 46
- Excluded questions: 34 (no repeated full-answerability parent evidence)

The resulting labels are stored in
`fixtures/threeway_judged_acceptable_parents_v1.json`.

## Corrected results

| Strategy | Recall@4 | MRR@4 | Delta vs direct |
|---|---:|---:|---:|
| Direct | 100.0% | **0.9366** | — |
| Direct + procedural | 97.83% | 0.9203 | -0.0163 |
| Direct + policy | 100.0% | 0.9312 | -0.0054 |
| Direct + conceptual | 97.83% | 0.9040 | -0.0326 |
| Direct + all three | 97.83% | 0.9076 | -0.0290 |

Even an oracle that selects the best of the three single angles improves only
1 of 46 labeled questions, with mean MRR@4 delta `+0.0109`.

## Dispersion association

| Measure | Result |
|---|---:|
| Spearman: direct parent dispersion vs oracle single-angle gain | -0.241 (p=.106) |
| Point-biserial: dispersion vs any oracle improvement | -0.223 (p=.136) |
| Lowest-dispersion quartile mean oracle gain | +0.0417 |
| Highest-dispersion quartile mean oracle gain | 0.0000 |

The direction remains compatible with the original idea, but it is not
statistically reliable and has only one improving case. The corrected data
does **not** validate a dispersion threshold or a fixed narrative angle.

## Remaining limitation

These labels are materially better than assigning every message its
BANKING77 category parent. However, they are still reconstructed from judged
candidate sets produced by the systems being compared, so they are not an
independent full-corpus relevance set. A future confirmation should judge a
pooled candidate set before scoring, as described in the selective-judge
protocol.
