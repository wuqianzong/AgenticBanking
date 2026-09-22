# Unified Response Obligation Contract (design, draft)

Supersedes the per-type sections of `RESPONSE_COVERAGE_CONTRACT_DESIGN.md` (the
handle work already shipped: `10ed6ea`, `0732485`, `dc6ad5e`, `33225a3`,
`bad927a`, `769df3d`). That work gave tool artifacts a handle and one net;
this generalizes the same idea to every kind of obligation -- RAG evidence,
clarification, decline, no-info -- so the response generator stops branching
on subtask type at all, closing the class of bug traced on BUG-026/027 and
the clarification-drop found while validating stage 2.

Rollback point: tag `pre-response-coverage-redesign`, plus each shipped
commit above is its own rollback point for the already-live handle work.

## Problem

Today the generator prompt has five separately-named, separately-instructed,
separately-audited sections: Approved evidence by retrieval task /
Clarification inputs / Decline inputs / No-info inputs / (now unified)
shortcut+tool-result handles. Each has its own id namespace, its own prose
rule, and -- critically -- its own completeness net, of uneven quality: RAG
tasks and (as of the commits above) shortcuts/tool-results get a real net;
clarification/decline/no-info get detection-only logging with no repair.
Traced live: a 3-part turn dropped its clarification entirely, the reviser
had nothing to revise (no block existed for it to find), and the turn burned
all 3 retries before falling through to the deterministic fallback. This is
not a reviser competence problem -- the reviser is explicitly scoped to
editing existing blocks' leaf text, never adding a block, so a wholly-omitted
entry is structurally unfixable downstream of generation. The fix has to be
at generation time, and it has to be the same fix for every type.

## Design

### 1. One entry shape, everywhere

Every obligation the response must address becomes one entry:
```
{"id": "...", "generation_content": "...", "generation_guidance": "..."}
```
`generation_content` is the material (hydrated parent evidence, an auditor's
guidance-only finding, a decline/no-info's fixed sentence, an assembled
clarification ask, a shortcut's label/topic, a tool result's fact/reason/
token). `generation_guidance` is a short, fixed instruction for what to do
with that content. Both are deterministic content produced before the
generator ever runs -- nothing here is the model's own judgment.

No `content_mode` field appears in this JSON. `generation_guidance` is a
pure function of mode (a one-time lookup), so shipping both the tag and its
already-expanded instruction gives the model the same fact twice for no
benefit. The model reads content + instruction, writes one natural sentence,
cites the id in `source_task_ids`. Same operation, every entry, every type.

### 2. `content_mode`: internal-only, deterministic, code-assigned

Never decided by an LLM, never serialized into a prompt. It is a fixed
property of which existing state field an entry came from, assigned once by
the new builder (`_build_response_obligations`, replacing today's five
separate re-derivations in `_generate_response_blocks`/`node_04c`):

| Source (unchanged) | Decided by (unchanged) | mode | generation_guidance (canonical, written once) |
|---|---|---|---|
| `evidence_by_task` (real parents or guidance-only) | node_03c sufficiency status | `compose` | "Compose a natural answer from this. Never add a fact, procedure, or product not present in it." |
| `verified_api_context` | tool dispatch (`api`, direct_value) | `compose` | same as above -- prompt already treats these "on the same footing as approved evidence" |
| `decline_context` | triage / intent formulator / tool dispatcher | `relay` | "This is already the final statement. Rephrase it naturally; never elaborate or treat it as material to reason further from." |
| `no_info_context` | node_03c terminal CLARIFICATION_REQUIRED/no-info | `relay` | same as above |
| `intent_clarifications` | triage / intent formulator / node_03c | `ask` | "Compose a natural clarifying question about this. Never assert, assume, or answer on the customer's behalf." |
| shortcuts, unavailable requests, personal data (`_iter_tool_attempts`) | deterministic tool dispatch | `state` | "State or mention this using its own wording; the real UI/action is attached automatically regardless of your text." |

Mode is uniform within a source list -- a guidance-only evidence entry is
still `compose`, same as a full-parent one; no per-entry branching inside a
list, only across lists. It's used in exactly two code paths, never inside a
prompt:
- the builder, once, to pick which `generation_guidance` string to attach;
- the completeness net, to decide what "covered" means (a citing block's
  text must also contain a literal label/token for `state`-mode entries;
  citation alone suffices for the others) and what fallback to insert.

### 3. The shared builder

`_build_response_obligations(state, pending_ids=None)` replaces the current
separate construction + filtering of `evidence_by_task`,
`intent_clarifications`, `decline_context`, `no_info_context`,
`available_shortcuts`, `unavailable_service_requests`,
`verified_api_context`, `personal_data_placeholders` at each of their three
call sites (`node_04b`, `node_04c`, `node_06`'s grounding-context builder).
`pending_ids`, when given, filters to those ids -- replacing node_04c's
current five separate `if task_id in pending_task_ids` filters with one.

Ids: RAG/verified-fact entries keep their real `task_id`; decline/no-info
keep their existing synthetic `task_id` (already used today, proven not to
leak into `task_results`/verified-evidence caching); clarification uses its
`subquery_id` (as today); shortcuts/tool-results keep their `nav_N`/`svc_N`/
`data_N`/`fact_N`/`unavail_N` handles (already shipped). All four id shapes
already coexist safely in `valid_task_ids`/`source_task_ids` today -- the
builder only stops maintaining five parallel lists and unions instead.

### 4. One completeness net

`_apply_response_completeness_nets` becomes one loop over the builder's
output instead of three separate passes (task-omission / detection-only /
shortcut+tool-result-omission). For each entry: covered if some block cites
its id (plus, for `state` mode, the block's text contains the entry's own
literal label/token/fact/reason). If not covered, insert a fallback block at
the position already established for shortcuts (after the last block of the
same subquery, else at the end), using the mode-appropriate deterministic
text:
- `compose`: the existing task-omission apology template.
- `relay`: the entry's own `generation_content` verbatim (it already *is*
  the final sentence -- `decline_evidence`/`no_info_evidence`).
- `ask`: `_clarification_fallback_text` plus its `options` (the block type
  becomes `clarification`, not `text`, so guesses render as chips -- this is
  the concrete fix for the traced drop).
- `state`: the existing label/token/fact/reason sentence (already shipped).

This one net is what node_11 already calls (from the `769df3d` fallback
fix), so the exhausted-retry path inherits clarification coverage for free,
with no separate change needed there.

### 5. Audit simplification

`_normal_outbound_grounding_context` passes the same builder output (filtered
to what's cited, as today via `_hydrate_response_evidence`-equivalent). Its
several scope-specific paragraphs (Direct Factual Grounding / Clarification
Grounding / Decline Grounding / ...) collapse into one rule: *"for each id
the final text cites, check it stayed consistent with that entry's own
`generation_guidance`, given its `generation_content`; never a citable fact
on its own, never grounds to permit a claim beyond what its own mode
allows."* `_partition_audited_response`'s attribution needs no change -- it
already works generically over `task_id` strings regardless of namespace.

### 6. What does not change

- node_07's anchor computation and card attachment (`_shortcut_anchor_block_ids`,
  `_iter_shortcut_entries`) -- already id/handle-generic, shipped.
- `guesses_by_subquery_id`'s deterministic chip-attachment -- already
  attaches a clarification's guesses to whatever block cites its id,
  regardless of what the model wrote; unaffected by this refactor and is
  exactly the precedent that shows the model never needed to see the raw
  guesses correctly reproduced in the first place.
- Every upstream judgment (node_03c's status calls, triage's CLARIFY
  decision, the tool dispatcher's outcomes) -- untouched. This refactor is
  entirely about how already-decided results are packaged for generation,
  revision, and audit, not about any new decision.

## Validation plan (before touching production code)

1. Fixture-level: replay the builder against saved state snapshots from
   `_response_evidence_manifest`/`clarification_tasks`/`decline_tasks`
   fixtures already used for the stage-1 handle tests; confirm entry shape,
   id uniqueness across sources, and mode assignment match the table above
   with no manual per-entry overrides needed.
2. Prompt-level replay (reusing the `plan_ab.py`/`handle_ab.py` scratchpad
   harness): rebuild the generator prompt from the unified list on the same
   42 shortcut prompts plus a fresh set of logged clarification-bearing
   prompts; compare citation/coverage against the current five-section
   prompt and against RAG's own ~92% parity baseline.
3. One forced-fallback live replay (same technique as the `forced_fallback`
   demo): inject a clarification omission on a live turn and confirm the new
   net inserts a real `clarification` block with its options intact, instead
   of reaching node_11 after 3 wasted retries.
4. Regression: confirm a normal SUFFICIENT-with-parents task and a passing
   shortcut turn are byte-identical in final customer text to today's output
   (the refactor should not change wording for the common case, only close
   the gap for the omission case).

## Staged implementation (each its own commit, rollback point kept)

1. `_build_response_obligations` + the mode table, wired into `node_04b`
   only; old five-section prompt removed; net still the current three-pass
   version reading the new list's `id`s (so the net change is isolated from
   the prompt change).
2. Net rewritten to the single mode-dispatched loop; node_04c switched to
   the same builder with `pending_ids` filtering.
3. Audit's grounding-context paragraph collapsed to the one generic rule.
4. `verified_api_context` mode decision finalized (`compose`, per the
   reasoning above) and confirmed against its existing "same footing as
   approved evidence" prompt language.
