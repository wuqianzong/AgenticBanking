# Retrieval Query Strategy Evaluation v1

## Question

Do complete-task narrative query representations retrieve a more useful parent
evidence set than one synthesized, noise-reduced query when both search the
same original Question-and-Answer vector corpus?

## Controlled setup

The benchmark holds the following constant for every strategy:

- the isolated historical generic Q&A Chroma collection;
- current canonical Product×Intent metadata filters;
- the embedding model;
- candidate depth and four-parent output cap; and
- the expected FAQ parent ID for each test formulation.

The current primary comparison changes only the complete-task query strategy:

1. **Combined cleaned query** — one noise-reduced query covering the complete task.
2. **Anchor-preserving narrative views** — complete task anchor plus alternate
   full-task FAQ narratives.
3. **Adaptive Product x Intent narratives** — complete task anchor plus
   FAQ-style narratives selected from valid Product x Intent pairs.

Raw customer wording remains a diagnostic only: it receives the full original
message while the correct metadata scope is supplied by the benchmark. Fixed
partial procedural/policy/conceptual splitting is retired from this decision:
it is a different approach because its query branches intentionally omit parts
of the user task.

## Dataset

The evaluation contains 80 query formulations over 20 reviewed FAQ targets:

- 20 clean anchor queries;
- 20 procedural-dominant variants;
- 20 policy-dominant variants; and
- 20 indirect/conceptual variants.

The 60 variants were generated from the reviewed anchor query and its fixed
Product×Intent scope, without giving the generator the FAQ answer text. They
are stored as a frozen fixture in
`fixtures/retrieval_query_strategy_eval_v1.json`.

This is a controlled engineering benchmark, not an independent customer
traffic study. Variants of one anchor share the same target FAQ, so results
must not be treated as 80 statistically independent customer observations.
Further, each synthetic message was generated from an anchor query selected
for that FAQ and its known Product×Intent boundary. This can inflate absolute
cosine similarity and make the cleaned direct query unusually canonical. The
generator was not given FAQ answer text, but it was not blind to the target
task. Therefore this fixture is suitable for diagnosing strategy behaviour,
not for proving real-customer retrieval superiority.

## Historical fixed-angle diagnostic (retired)

| Strategy | Recall@4 | MRR@4 |
|---|---:|---:|
| Raw customer query | 80/80 (100%) | 0.902 |
| Combined cleaned query | 80/80 (100%) | **0.925** |
| Three-angle RRF | 80/80 (100%) | 0.897 |

Breakdown by input style:

| Style | Combined cleaned MRR@4 | Three-angle RRF MRR@4 |
|---|---:|---:|
| Clean anchor | 0.925 | 0.925 |
| Procedural-dominant | **0.925** | 0.888 |
| Policy-dominant | **0.925** | 0.888 |
| Indirect/conceptual | **0.925** | 0.888 |

Paired comparison of three-angle RRF minus combined cleaned query:

- mean MRR@4 delta: **-0.028**;
- 69 ties, 7 cases favoring the combined query, and 4 favoring three-angle RRF;
- naive 20,000-resample bootstrap 95% interval: `[-0.075, 0.016]`.

The interval crosses zero. There is no statistically stable evidence that
three-angle RRF improves rank over a strong combined cleaned query in this
evaluation.

## Conclusion

The evaluation supports **query noise reduction**, but it did not demonstrate
an incremental benefit from fixed partial-angle decomposition. These figures
are retained only as a retirement record; fixed partial-angle RRF is not a
candidate in the current adoption comparison.

Recommended current default:

```text
customer message -> one noise-reduced combined query -> original Q&A vectors
```

Future evaluation should include at least 60-100 distinct target FAQs and
permit multiple relevant FAQ labels where appropriate. Test authors should be
given only a product scenario, not the target FAQ wording, identifiers, or its
retrieval query representations.

## Narrative-preserving three-query follow-up

The original three-angle strategy was revised for a follow-up run. Rather than
making each query a partial procedural, policy, or conceptual request, every
query retained the full cleaned task anchor and added only a different
narrative emphasis. This prevents a query branch from silently changing the
product, intent, requested outcome, or user constraints.

| Strategy | Recall@4 | MRR@4 |
|---|---:|---:|
| Combined cleaned query | 80/80 | 0.925 |
| Original angle-split RRF | 80/80 | 0.897 |
| Narrative-preserving three-query RRF | 80/80 | **0.931** |

The narrative strategy improved the rank of one case compared with the
combined query and was otherwise tied. It outperformed the original
angle-split method in seven formulations and lost in three. This supports the
claim that preserving the task anchor avoids some angle-split drift, but the
increment over the combined baseline is too small and too synthetic to claim
production robustness. The blind review harness now compares the narrative
strategy directly with the combined baseline.

## Adaptive Product x Intent narrative pilot

The proposed retrieval design was also evaluated without changing production
code or the active vector store. For each task, the test generated:

1. one complete, cleaned task anchor; and
2. one additional complete FAQ-style narrative per valid Product x Intent
   pair.

The Product x Intent pair selected the narrative pattern only. It did not
expand the task boundary, add products or intents, or turn parts of a request
into separate partial queries. Parent rankings from the resulting complete
query views were fused with the same parent-level RRF and four-parent
selection rule used by the retrieval engine.

| Strategy | Recall@4 | MRR@4 |
|---|---:|---:|
| Combined cleaned query | 80/80 | 0.9250 |
| Anchor-preserving three-view RRF | 80/80 | **0.9313** |
| Adaptive Product x Intent narrative RRF | 80/80 | 0.9250 |

Against the combined cleaned-query baseline, the adaptive pilot improved one
formulation (`horizon_app_obtain`: rank 2 to 1), worsened one
(`mutual_fund_costs`: rank 1 to 2), and tied the other 78. It therefore does
not meet a pre-agreed material-improvement threshold such as +5% Recall@4 or
MRR. It is nevertheless materially better than fixed partial-angle splitting:
it improved seven formulations, worsened four, and tied 69.

This is a **design diagnostic**, not an adoption result. Every fixture task
currently has one Product x Intent pair, and the FAQ patterns are taxonomy
templates rather than patterns independently derived from the corpus. The
test therefore validates that full-anchor Product x Intent narratives do not
create the observed partial-angle drift, but it does not yet validate the
central multi-pair benefit.

Before production implementation, the next evaluation must use independently
written, human-reviewed messages containing multi-intent and genuinely related
multi-product requests. Candidate parent sets should be judged blind, with no
expected FAQ preselected during query creation. Adopt the strategy only if it
improves that evaluation while remaining within the retrieval-call and latency
budget.

## Multi-component evidence-set pilot

The original rank-only benchmark could not measure the proposed strategy's
main benefit: retrieving a useful second or third parent for a compound
customer request. A second, isolated evaluation therefore used ten
independently phrased two-component requests. Each component has a reviewed
set of acceptable parent IDs. The same original-Q&A corpus, filters, candidate
depth, parent-level RRF, and four-parent cap were used for every strategy.

The scored metrics are:

- **Component Recall@4**: selected parents cover an individual requested
  component.
- **Full Task Recall@4**: selected parents cover every requested component.
- **Mean first relevant rank**: position of the first parent covering each
  component; lower is better.
- **Evidence precision@4**: share of selected parents in the reviewed
  acceptable-evidence set.

| Strategy | Full Task Recall@4 | Component Recall@4 | Mean First Relevant Rank | Evidence Precision@4 |
|---|---:|---:|---:|---:|
| Combined cleaned query | 7/10 (70%) | 17/20 (85%) | **1.71** | **0.53** |
| Anchor-preserving three-view RRF | 9/10 (90%) | 19/20 (95%) | 1.84 | **0.53** |
| Adaptive Product x Intent narrative RRF | 9/10 (90%) | 19/20 (95%) | 2.11 | 0.50 |

Adaptive narratives recovered full evidence coverage in two cases that the
combined cleaned query did not cover:

- credit-card application documentation plus balance-transfer charges;
- multiple AutoInvest plans plus low-balance handling.

In both, the additional evidence appeared as a supporting second, third, or
fourth parent rather than replacing the primary answer. This validates the
evidence-set hypothesis. However, adaptive narratives did **not** beat the
anchor-preserving-three-view strategy overall.

It also missed card activation when the task combined card activation and PIN
change. Both sit under the same `Debit_Card x Card Logistics` pair, so
Product x Intent alone has no way to represent their distinct outcomes. The
production design must therefore retain the full direct task anchor and
explicit task-aspect decomposition; it must not assume that Product x Intent
is itself a complete semantic decomposition.

This is still a small, reviewed engineering pilot rather than statistical
proof. The correct decision is **not to implement Product x Intent narratives
as a universal replacement for the direct anchor yet**. They remain a
promising conditional supplement for genuinely distinct task aspects, pending
a larger blind evidence-set evaluation with 60-100 independently authored
multi-component messages.

## BANKING77 external-language validation

To test language robustness beyond the internal fixtures, an additional
evaluation used 80 held-out messages from the BANKING77 test split: ten each
from eight labels with reviewed Horizon equivalents (card activation, PIN
change, card arrival, lost/stolen card, transfer timing, transfer
cancellation, funding an account, and forgotten app passcode). BANKING77 is
an externally authored banking-support dataset, but it is not Horizon answer
ground truth. The fixture therefore evaluates retrieval against reviewed sets
of acceptable Horizon parent IDs rather than treating the BANKING77 label as
an answer key.

Every strategy received the **same original BANKING77 customer message as its
complete anchor**. The difference was only whether it added complete
same-task narrative views before parent-level RRF. This avoids giving a
variant extra customer intent or a broader Product x Intent filter.

| Strategy | Full Evidence Recall@4 | Mean First Relevant Rank | Evidence Precision@4 |
|---|---:|---:|---:|
| Direct customer-message anchor | 73/80 (91.25%) | 1.36 | 0.266 |
| Anchor-preserving three-view RRF | **80/80 (100%)** | **1.18** | **0.298** |
| Generic Product x Intent template RRF | 70/80 (87.50%) | 1.33 | 0.258 |
| FAQ-distilled pattern narrative RRF | **80/80 (100%)** | 1.31 | **0.300** |

The anchor-preserving narrative method recovered all seven direct-anchor
misses and created no evidence-set losses. Six of those recoveries were
account-funding phrasing variants and one was a card-arrival phrasing variant.
This is a strong signal that complete same-task narrative variation can close
some customer-language-to-FAQ-language gaps without partial-angle drift.

The generic Product x Intent template method performed worse than the direct
anchor. Its templates are too broad to distinguish different outcomes within
one Product x Intent boundary, such as card delivery versus other card
logistics. This rejects the current taxonomy-template implementation as a
universal supplement.

A follow-up added two **FAQ-distilled** patterns per selected BANKING77 label.
Each pattern was paraphrased only from the reviewed mapped Horizon parent Q&A
records (for example, the account-funding patterns explicitly described FAST
beneficiary transfer and PayNow registration). The direct customer wording was
still retained as the first query view. This version recovered the same seven
direct-anchor misses as the strongest three-view narrative method, with no
regressions. It did not improve mean first relevant rank, so the result
supports corpus-shaped complete narratives as a viable supplement but does
not show them to be superior to the existing complete narrative views.

This result is more informative than the ten-case internal pilot, but it is
not a final production claim: the 80 messages cluster within eight labels,
the Horizon mappings and their distilled patterns were manually reviewed, and
BANKING77 is not a corpus of disclosed production customer chats. The current
FAQ-distilled patterns are therefore a controlled evaluation treatment, not a
production pattern-generation algorithm. The fixture and builder are retained
for repeatable regression testing.

## Fresh blind corpus-pattern validation

A subsequent validation sampled 40 previously unused BANKING77 test messages:
five per each of the eight mapped needs. The scope-level patterns were
generated without the customer messages, expected parent IDs, or retrieval
results. The pattern-generation input contained only the Product x Intent
boundary and FAQ text inside that boundary. This is materially stricter than
the earlier manually distilled-pattern treatment.

For each strategy, an LLM judge saw the customer message and an anonymised,
randomly ordered set of hydrated top-four FAQ texts. It did not see parent IDs,
RRF scores, query variants, or strategy names. It classified the evidence set
as `full`, `partial`, or `none` answerability.

| Strategy | Gemini full | Gemini partial | Gemini none |
|---|---:|---:|---:|
| Direct anchor | 22/40 | 8/40 | 10/40 |
| Direct anchor + corpus patterns | 23/40 | 7/40 | 10/40 |

Paired Gemini outcomes were 5 improvements, 32 ties, and 3 regressions. The
net gain of one fully answerable case is below the pre-proposed adoption
threshold and includes regressions. A GPT audit completed 22 paired sets
before its daily quota was exhausted; it recorded 0 improvements, 21 ties,
and 1 regression. It is therefore insufficient as a complete second judge,
but it does not corroborate a meaningful improvement.

**Decision:** do not implement automatic scope-only corpus-pattern narratives
from this evidence. The generated patterns were broad lists of recurring FAQ
topics; without the user query they could not reliably select the relevant
outcome within the scope and sometimes displaced useful direct-anchor
evidence. Preserve the direct-anchor retrieval design. Any later narrative
generation design needs a more selective, query-aware mechanism and a fresh
blinded validation, including human adjudication of all changed cases.

## Reproduction

```bash
python research/2026-08-19_retrieval_query_refinement/scripts/benchmark_retrieval.py \
  --mode generic \
  --cases-file research/2026-08-19_retrieval_query_refinement/fixtures/retrieval_query_strategy_eval_v1.json
```

Build and run the external BANKING77 validation:

```bash
python research/2026-08-19_retrieval_query_refinement/scripts/build_banking77_horizon_eval.py
python research/2026-08-19_retrieval_query_refinement/scripts/benchmark_retrieval.py \
  --mode generic \
  --cases-file research/2026-08-19_retrieval_query_refinement/fixtures/banking77_horizon_retrieval_eval_v1.json \
  --replace-default-cases \
  --query-strategies-only
```

The benchmark uses an isolated temporary generic Chroma collection and does
not modify the active vector store or SQLite knowledge database.
