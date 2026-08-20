# Judged-Target Narrative-Gain Rerun v1

## Purpose

Re-score the saved three-way BANKING77 narrative validation with the
conservative, reconstructed Gemini-derived acceptable parent sets in
`fixtures/threeway_judged_acceptable_parents_v1.json`. No LLM calls and no
retrieval rerun were required: the saved selected parent rankings are scored
against corrected targets.

Only 46 of the original 80 messages have a conservative label: a parent must
have been marked relevant in at least two independently shuffled,
full-answerability evidence judgments. This preserves multiple relevant FAQ
parents where appropriate.

## Results

| Strategy | Recall@4 | MRR@4 | Change vs direct |
|---|---:|---:|---:|
| Direct customer-message anchor | **100.0%** | **0.9366** | — |
| Anchor-preserving complete three-angle RRF | **100.0%** | **0.9366** | 0.0000 |
| FAQ-scope-pattern narrative | 97.83% | 0.8714 | -0.0652 |

The complete three-angle strategy improved one case (rank 2 to 1), regressed
one (rank 1 to 2), and tied the remaining 44. It provides **no net narrative
gain** in the corrected data.

The FAQ-scope-pattern strategy improved two, regressed eight, and tied 36.
It is worse than direct under the corrected labels.

## Revised conclusion

The previous apparent narrative-retrieval gain was dependent on the static
BANKING77 category-to-parent proxy and must be withdrawn as a supported
conclusion. The corrected judged subset supports a narrower statement:

> Retaining the complete direct task anchor makes added narrative views mostly
> harmless in this small subset, but has not demonstrated incremental retrieval
> value. Broader corpus-pattern narratives can dilute a strong direct result.

These reconstructed labels are still pooled from judged strategy outputs, not
an independently judged full-corpus candidate pool. The result is a necessary
correction, not the final confirmation study.
