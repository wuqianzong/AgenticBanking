# Addendum: Pre-Compose Clarification Content Upstream

Extends `RESPONSE_OBLIGATION_CONTRACT_DESIGN.md`'s `ask` mode. Does not
change `compose`/`relay`/`state`. No production code changed yet.

## Motivating evidence

An experiment shortening `_generate_response_blocks`'s prompt (~49.5k chars
-> ~15.6k chars, same obligations, same model) to test whether raw prompt
length/complexity drives the citation-omission problem found earlier
(BUG-026/027 follow-up) gave, across 8 live scenarios (22 shortcut
citations total, `gemini-3.5-flash-lite`, temp 0):

| | citation | correct text | correct wording |
|---|---|---|---|
| current prompt | 22/22 (100%) | 22/22 (100%) | 21/22 (95%) |
| shortened prompt | 22/22 (100%) | 16/22 (73%) | 16/22 (73%) |

Citation itself wasn't differentially affected this run (a different result
from an earlier, smaller A/B -- citation compliance is noisier and more
scenario-dependent than a single test can characterize). The real finding:
the shortened prompt cited the right id but produced missing or wrong
wording on 2 of 8 scenarios -- a *worse* failure mode than the one being
chased, since the coverage net's label-substring check does not reliably
catch "right id, wrong wording." Cutting prose length trades away
reliability rather than freeing it up, confirming the risk flagged before
running the experiment: this prompt's length is mostly load-bearing,
previously-debugged content, not padding.

## Diagnosis

`decline`/`no_info` already avoid this trade-off structurally:
`decline_evidence`/`no_info_evidence` are deterministic, non-LLM-authored,
fixed sentences (`_decline_message`, `_no_info_evidence_text`). The final
generator's job for them was always just relay-and-personalize -- so their
paragraph in the final generator is short *because* the final generator
never did the authoring.

`clarification` is the one obligation type that doesn't follow this
pattern. Its `generation_content` today is raw material
(`unresolved_request`, `scope`, `clarification_guesses`) that the final
generator composes into an actual question, under its own dedicated
~35-line paragraph (scope restriction, "never assert an answer," guess-
voice-reversal rule, co-presence-with-partial-evidence rule). That's real
authoring work, done inside a call that is simultaneously composing RAG
answers, shortcut sentences, and everything else -- exactly the kind of
per-call load the shortening experiment showed degrades reliability.

## Design

Every clarification-raising call already exists and already has
`unresolved_request`/`clarification_reason`/`clarification_guesses` in its
own output schema, at these five sites (`nodes.py`):

| Site | Node | Has an LLM call to enrich? |
|---|---|---|
| ~L2006 | triage (node_02d) | yes |
| ~L2614 | intent formulator (node_02e), main path | yes |
| ~L2729 | intent formulator (node_02e), deterministic fallback (no valid task formed) | **no** -- synthetic, no model call |
| ~L3262 | task dispatcher | yes |
| ~L5272 | sufficiency auditor (node_03c) | yes |

For the four LLM-driven sites: add one more field, `draft_question`, to
each call's *existing* output schema -- no new LLM call, no added latency.
Each of these calls already has every fact needed to compose a safe
question (`unresolved_request`, its own `scope`, the guesses it just
wrote); it is a single-purpose call with one judgment to make, which is
exactly where the scope/safety constraints currently spelled out in the
final generator's paragraph belong -- validated in a smaller, focused
prompt instead of competing for attention in the generator's.

The deterministic fallback site (~L2729) has no model call to enrich; it
keeps producing an empty/absent `draft_question`, and that one case alone
falls back to today's compose-from-raw-fields behavior in the final
generator (see below) -- a narrow, already-safe path (this fallback fires
only when no valid retrieval task could be formed at all).

`clarification_tasks` entries gain `draft_question: str` (default `""`)
at all five construction sites, each of the four passing through
`str(raw.get("draft_question", "")).strip()` from its own call's output;
`_normalize_clarification_guesses` and every other field are unchanged.

### Obligation contract change

In `_build_response_obligations`, the `ask`-mode entry's `generation_content`
gains `draft_question` alongside the existing fields. The final generator's
instruction for `ask` mode changes from "compose your own question" to the
`decline`/`no_info` pattern: when `draft_question` is non-empty, relay/
lightly personalize it (same latitude decline/no-info already get -- rephrase
into the customer's tone, never treat it as raw material to reason further
from, never add or drop an option); when empty (the one deterministic-
fallback case), fall back to composing from `unresolved_request`/`scope`
exactly as today. This shrinks the `ask`-mode paragraph in the final
generator to roughly decline/no_info's own length, moving the ~35 lines of
scope/safety instruction to the four authoring call sites' own prompts
instead of deleting it.

Guess-chip attachment (`guesses_by_subquery_id` in the shared completeness
net) is unaffected -- it already attaches `clarification_guesses` to
whichever block cites the subquery_id, deterministically, regardless of
generator wording.

## Validation plan (before touching node_02d/node_02e/task_dispatcher/node_03c)

1. **Authoring-call quality, in isolation**: for each of the 4 sites, replay
   a handful of real captured clarification-raising prompts with the new
   `draft_question` field added to the schema; confirm (a) the call's own
   primary judgment (CLARIFY vs SEARCH vs DECLINE, or SUFFICIENT vs
   CLARIFICATION_REQUIRED, etc.) is not degraded by the added output work,
   and (b) `draft_question` itself never asserts an unconfirmed fact/product
   and stays within that site's own `scope`.
2. **Final-generator quality with a pre-composed draft**: on the same
   clarification scenarios used in the shortening experiment (plus the
   turn that dropped its clarification entirely), compare the shortened
   `ask`-mode paragraph consuming a pre-composed `draft_question` against
   today's full paragraph composing from raw fields -- coverage/citation
   should be unaffected (unrelated to this change), and response quality/
   safety should be equal or better, not merely equal length.
3. **Regression**: confirm the guess-chip attachment, the deterministic
   fallback path (empty `draft_question`), and the co-presence-with-partial-
   evidence case (a clarification sharing a subquery with real RAG evidence)
   are all unaffected.

## Staged implementation

1. Add `draft_question` to the 4 LLM-driven schemas + the 5 construction
   sites (additive only -- final generator still composes from scratch,
   ignoring the new field). Validate authoring-call quality (step 1 above)
   before relying on it for anything customer-facing.
2. Switch the final generator's `ask`-mode instruction to relay-style,
   consuming `draft_question` when present. Validate step 2/3 above.
3. Revisit whether `decline`/`no_info`'s own (already short) final-generator
   paragraphs can tighten further now that `ask` follows the same shape,
   as a documentation/consistency pass, not a behavior change.
