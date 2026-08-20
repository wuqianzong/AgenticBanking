# BANKING77 Parent-Dispersion Confirmation v1.0

## Status

**Fresh-language confirmation; not target-level or production validation.**

This report records the planned follow-up to the post-hoc direct-parent-neighbourhood dispersion diagnostic. It tests the locked dispersion trigger on Banking77 test messages excluded from every previous research fixture and result artifact.

The experiment confirms the direction of the discovery finding: bridge uplift is substantially larger in the low-dispersion group. It does not confirm that the locked `0.038` threshold is sufficient as a standalone production gate, because useful bridge gains also occur above the threshold.

## Frozen design

| Item | Value |
|---|---|
| Source | PolyAI BANKING77 test split (CC BY 4.0) |
| Fresh messages | 120 total; 15 each across eight previously reviewed mapped needs |
| Prior-message exclusion | 200 exact Banking77 messages found in all prior research JSON artifacts |
| Vector corpus | Historical generic child-vector staging, normalized to canonical metadata |
| Vector ranking | In-memory exact cosine ranking using Chroma's default embedding function |
| Fusion | Parent-level RRF, `K=60`, eight candidates per view, four-parent cap, 50% drop-off |
| Locked trigger | Direct selected-parent cosine SD `<= 0.038` |

No production application code, Chroma collection, or SQLite knowledge record was modified. The reproducibility artifacts are:

- `scripts/run_parent_dispersion_confirmation.py`
- `fixtures/banking77_parent_dispersion_confirmation_v1.json`
- `results/banking77_parent_dispersion_confirmation_v1.json`

## Policies compared

| Arm | Retrieval policy |
|---|---|
| Direct | Original Banking77 customer message only |
| Always bridge | Direct message plus one frozen task-semantic bridge view |
| Dispersion-gated | Same bridge only when direct parent dispersion is `<= 0.038`; otherwise direct only |

The bridge angle was fixed before selecting fresh messages: procedural for activation, PIN, lost/stolen card, funding, and passcode; policy for card arrival, transfer timing, and cancellation. The bridge phrases are the previously reviewed mapping narratives. This is a deterministic experiment control, not a deployable runtime router: it uses the Banking77 source intent as a proxy for a future resolved-task classifier.

## Feature definition

For each direct-query RRF-selected parent `p`, retain the highest raw direct child cosine returned for that parent:

```text
parent_sim(q, p) = max cosine(q, child), for returned children of p
parent_dispersion(q) = std(parent_sim(q, p)) across direct-selected parents
```

This run records the actual child-level scores used by its isolated retriever; it does not reconstruct scores from parent text after the fact.

## Main results

| Arm | Bridge calls | Recall@4 | MRR@4 | Change in MRR@4 vs direct |
|---|---:|---:|---:|---:|
| Direct | 0 / 120 | 0.9250 | 0.7514 | — |
| Always bridge | 120 / 120 | 1.0000 | 0.8646 | +0.1132 |
| Dispersion-gated | 38 / 120 | 0.9750 | 0.8167 | +0.0653 |

The gated policy uses a bridge for 31.7% of queries, avoiding 68.3% of always-bridge calls. It improves on direct retrieval, but loses `0.0479` MRR@4 relative to always bridging.

| Direct-result group | Cases | Direct Recall@4 | Always-bridge Recall@4 | Always-bridge mean MRR delta | Improve / regress / tie |
|---|---:|---:|---:|---:|---:|
| Low dispersion; trigger fires | 38 | 0.8421 | 1.0000 | +0.2061 | 18 / 5 / 15 |
| High dispersion; trigger does not fire | 82 | 0.9634 | 1.0000 | +0.0701 | 20 / 11 / 51 |

The low-dispersion group has nearly three times the average always-bridge MRR uplift of the high-dispersion group. The high-dispersion group still has positive average uplift, so the threshold cannot safely mean “never bridge.”

### By mapped need

| Need | Triggered / 15 | Direct MRR@4 | Always bridge MRR@4 | Gated MRR@4 |
|---|---:|---:|---:|---:|
| Activate card | 4 | 0.7500 | 0.9667 | 0.8833 |
| Change PIN | 0 | 1.0000 | 1.0000 | 1.0000 |
| Card arrival | 9 | 0.5278 | 0.7000 | 0.5778 |
| Lost or stolen card | 5 | 0.9222 | 1.0000 | 0.9556 |
| Transfer timing | 4 | 0.9667 | 0.4722 | 0.8500 |
| Cancel transfer | 4 | 0.6167 | 0.8889 | 0.7056 |
| Transfer into account | 8 | 0.2611 | 0.8889 | 0.5611 |
| Forgotten passcode | 4 | 0.9667 | 1.0000 | 1.0000 |

Transfer timing is the key warning: its frozen policy bridge performs badly when always applied, while the gate avoids most of that harm. This supports conditional bridge control, but also shows that bridge quality and angle choice remain independent problems.

## Uncertainty

The 120 messages are repeated phrasings within only eight mapped needs. An exploratory bootstrap therefore resampled source-intent clusters rather than individual messages:

| Quantity | Cluster-bootstrap 95% interval for MRR delta |
|---|---:|
| Always bridge minus direct | -0.0986 to +0.3104 |
| Dispersion-gated minus direct | -0.0083 to +0.1479 |
| Low-dispersion uplift minus high-dispersion uplift | -0.0010 to +0.2595 |

The wide intervals are expected from eight clusters and prevent a decisive target-level statistical claim.

## Interpretation and limitations

The evidence supports a narrower claim: direct parent-neighbourhood dispersion is useful prioritisation information, because low-dispersion direct searches are more likely to gain from a narrative bridge. It does not support a single universal stop/go threshold.

Important limitations:

1. Messages are fresh, but only eight previously reviewed semantic targets remain; this is language robustness, not target-level generalisation.
2. The frozen bridge router uses Banking77 source labels as a controlled proxy. A runtime system must derive angle from the resolved customer task alone.
3. The fixed mapping narratives are not query-aware LLM rewrites. They isolate the trigger test but do not validate the intended query-aware bridge generator.
4. Exact in-memory cosine ranking is appropriate for these small filtered scopes but is not a byte-for-byte replay of production HNSW behaviour.
5. Acceptable-parent mappings remain a retrieval proxy rather than blinded human judgment of every evidence set.

The next experiment should add at least 60 distinct FAQ targets across more product and intent families, use a locked task-only angle router and semantic-equivalence gate, and evaluate parent dispersion jointly with direct RRF margin and sufficiency outcomes.
