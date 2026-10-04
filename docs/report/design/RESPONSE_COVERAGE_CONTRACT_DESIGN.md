# Response Coverage Contract and Inline Action Placement (design, draft)

Addresses BUG-026 / BUG-027 (and prepares BUG-017 / BUG-018). Rollback point: tag `pre-response-coverage-redesign`.

## Problem
Only RAG tasks have a handle (`task_N`), a citation field (`source_task_ids`), a coverage net and an audit rule.
Tool-derived subtasks (deeplink, service request, personal data, unavailable service request) reach the generator as
unlabelled side lists linked by free text, so the generator drops them far more often (about 8-20% of turns for
shortcuts vs a low single-digit rate for RAG). The fallback net matches by substring and uses English-only text.
All shortcuts are also summarised in one closing sentence, which is poor UX with several links.

## Constraint (unchanged)
The LLM never produces or sees routes, link ids, destination ids, special ids or private values. Buttons, forms
and `{{data-*}}` tokens stay deterministic and are built by node_07.

## Contract
1. Each tool artifact gets a per-turn alias (`nav_1`, `svc_1`, `data_1`, `unavail_1`) -- not a real id.
2. Aliases are listed as obligations next to the RAG tasks and cited through the same `source_task_ids` field.
3. Coverage check: every alias must be cited (or, failing that, its label / token found in text). Missing ones get
   a typed deterministic fallback sentence (localized template + label, template + token, supplied reason),
   inserted after the last block of the same subquery, else at the end.
4. Aliases are stripped from task_results, verified history and the answer cache; logged per type as
   model-cited vs net-inserted.
5. Audit sees the same shape; attribute violations to the alias; add a rule that denying a capability while a
   matching artifact exists is unsupported (BUG-018).

## Inline placement
node_07 computes an anchor per action: the block after which the alias was cited (or the net-inserted sentence).
Actions sharing an anchor stack in tool order. Frontend renders each action right after its anchor block;
messages without anchors (legacy) fall back to end-of-message rendering. The generator writes one short
sentence per shortcut right after the related content instead of one closing sentence.

## Validation (replay, 42 logged shortcut prompts + 16 personal-data prompts, temp 0)
| Metric | Baseline | Handle contract |
|---|---|---|
| Shortcut alias cited | 0/43 (n/a) | 43/43 |
| Shortcut sentence with English label + pointer wording | 6/43 | 18/43 (metric is English-only; sampled outputs are all Chinese/English sentences using the label) |
| RAG tasks cited | 45/55 | 46/55 |
| Personal-data token present | 15/16 | 14/16 |
| Personal-data alias cited | n/a | 14/16 |
| Capability denial | 0 | 0 |

Caveats: the replay metric misses non-English wording, so sentence presence must be re-measured with a
language-neutral check (label substring only). Personal-data coverage did not improve at n=16 (15 -> 14); the
extension for personal data stays a second stage. Most cited shortcut sentences landed in the last element in the
replay (36/43) because most turns have one element; inline placement matters mainly on multi-block turns.

## Stages (each a separate commit)
1. Backend: aliases in generator input, coverage net with typed localized fallbacks, audit alignment.
2. node_07 anchors + frontend inline rendering.
3. Personal-data alias extension (with BUG-017/018).
