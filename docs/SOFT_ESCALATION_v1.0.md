# Soft Escalation v1.0

**Status:** Implemented and live-verified end-to-end (unit-level rule
isolation plus real HTTP traces against the running server, including the
cross-request live confidence adjustment described in section 4). This is a
living document — update it in place as anything changes.

## 1. What this is

`node_08_soft_escalation_analyzer` surfaces a non-blocking offer alongside
a normal answer whenever there is evidence — but not necessarily an
explicit request — that the customer might be struggling. This is
distinct from `node_09_hard_escalation_broker`, which handles an explicit
"transfer me to a human" request; soft escalation is always the bot's own
suggestion, never forced, and the customer is free to ignore it.

**Surfaced as a banner above the input box, not a chat-history chip** (see
§1a) — the description below still refers to it as "the chip" in a few
places inherited from the original design; read that as "the soft-
escalation offer" throughout, since the underlying four-signal trigger
logic this section describes is unchanged by the banner rework.

It previously fired on a single flat rule: `turn_count >= 5`. That has been
replaced with four independent, deterministically-evaluated signals, each
logged with its own name and computed values so the reason a chip appeared
is always inspectable after the fact:

1. **Long session** — elapsed time since login exceeds
   `SOFT_ESCALATION_SESSION_DURATION_SECONDS` (10 minutes).
2. **Same-intent streak** — `same_intent_streak_count` exceeds
   `SOFT_ESCALATION_SAME_INTENT_STREAK` (20 consecutive turns on an
   unchanged product/intent pair).
3. **Low rolling confidence** — a bot-confidence average over the last
   `MAX_ESCALATION_HISTORY_TURNS` turns (10) drops below
   `SOFT_ESCALATION_CONFIDENCE_THRESHOLD` (0.5).
4. **Repeated need** — the same underlying need recurring across
   `SOFT_ESCALATION_REPEATED_NEED_TURN_COUNT` (3) turns, as judged by
   triage.

Rules combine as independent OR-triggers (any one firing shows the chip),
consistent with every other audit/gate in this codebase being deterministic
and individually explainable rather than an opaque composite score. None of
the four signals are persisted as a sticky "escalation active" flag — all
are recomputed fresh every turn from live or windowed state, so a pattern
that stops recurring naturally stops offering the chip once it ages out of
the window (or, for the streak/duration signals, once the customer moves on
or the session ends).

## 1a. Banner, not a chat-history chip

Originally rendered by appending `"Talk to Agent"` to `payload["options"]`
— the same mechanism as any other response chip, which meant it was baked
into that turn's message and stayed visible in the chat history forever,
even long after the triggering signal was gone. Replaced: `node_08` now
sets a plain boolean, `ui_action_payload["softEscalationAvailable"]`,
surfaced on every turn's response (`api_gateway.py`,
`softEscalationAvailable` on `final_payload`). The frontend renders this as
a banner above the input box, driven strictly by the *latest* turn's own
value — never a sticky flag, never attached to a specific past message —
so it appears and disappears turn-by-turn exactly as the underlying signals
do. Tapping the banner's action sends `"Talk to Agent"` as a normal message,
same downstream effect as the old chip tap.

The existing guard is unchanged: a turn ending in `final_clarification`
still never sets this (clarification responses intentionally carry no UI
actions, banner included).

## 2. Bot confidence: a continuous score, not a binary count

Tool Calling v3.0's schema already tracks resolution at a finer grain than
resolved/not-resolved: a subquery can be `RESOLVED`, or `OPEN` with real
partial evidence (a `rag` attempt whose `retrieval_state` is
`PARTIALLY_SUFFICIENT`), or `OPEN` with nothing useful at all. Collapsing
that to a binary "did this turn fail" count would throw away information
the schema already tracks. `_score_subquery` (`nodes.py`) instead scores
each subquery:

| Case | Score |
|---|---|
| `RESOLVED` | 1.0 |
| `OPEN` with a `rag` attempt at `PARTIALLY_SUFFICIENT` | 0.5 |
| `OPEN` with nothing | 0.0 |

A turn's own confidence is the average of its subqueries' scores. A pure
non-financial/chitchat turn (no financial subqueries) contributes no data
point either way. Session confidence is the average of the last 10 turns'
own confidences (see `_turn_confidence`) — note this is an average of
per-turn averages, not a flat pool of every subquery's score across the
window, so a turn with many subqueries doesn't get weighted more heavily
than a turn with one.

## 3. Repeated-need detection: folded into triage, not a new call or embeddings

The repeated-need signal went through two rejected designs before landing
on the current one, both worth recording so they aren't tried again:

- **Embedding/cosine-similarity comparison of subquery text.** Tested
  empirically against this project's actual Chroma embedding function
  (`all-MiniLM-L6-v2`) and separately against `sentence-transformers`'
  larger `all-mpnet-base-v2`, using realistic sentence pairs. Both showed
  the same failure mode: cosine similarity between "same need, differently
  worded" pairs (0.41–0.63) overlapped entirely with "different need, same
  topic" pairs (0.34–0.61) — no threshold cleanly separated them. This
  fine-grained a distinction needs real semantic judgment, consistent with
  every other scope/relevance decision in this codebase being an LLM call,
  not a heuristic.
- **A separately-injected list of prior subqueries fed into triage's
  prompt**, with a per-component `is_repeated_need` output flag. This
  worked, but required the deterministic system to maintain and inject an
  artificial "recent prior subqueries" context block on every triage call,
  and it forced a cross-turn identity match (which prior subquery does
  *this* one repeat?) that this codebase has no reliable stable key for —
  `subquery_id` is a fresh `f"subquery_{index}"` every turn, not a durable
  identifier. It also diluted triage's primary decomposition task with an
  extra context block on every call.

The current design instead asks triage's existing per-turn LLM call
(`node_02d_user_journey_classifier_and_triage`) to judge repetition
directly from the Trusted Conversation Context it already receives — no
injected list, no new call, no new Groq/Gemini rate-limit exposure. The
`TriageEvaluation` schema (`llm_service.py`) carries one new top-level,
turn-scoped boolean: `repetitive_issue_detected`. This also turned out more
robust in practice: a live trace showed the model correctly flagging
`False → True → True` across three turns whose `customer_component`
wording was different every single time (no exact-text overlap at all) —
exactly the case that would have broken a text-matching approach.

## 4. Turn-quality feedback: thumbs up/down for the ambiguous band

A separate, additive feature layered on top of the same confidence
scoring. When a turn's own (unwindowed) confidence lands in the open band
`TURN_FEEDBACK_SCORE_LOWER_BOUND <= score < TURN_FEEDBACK_SCORE_UPPER_BOUND`
(0.5 to 0.85) — the schema's own ambiguous middle ground, not a case it is
already confident about — the response carries `showFeedbackPrompt: true`
and the frontend offers a thumbs up/down. This is deliberately *not* shown
on every turn: a `RESOLVED` turn (1.0) or an `OPEN`-with-nothing turn (0.0)
is a case the schema already has a confident read on, and asking on every
turn would be needless survey fatigue for signal that isn't needed there.

**Temporary**: `TURN_FEEDBACK_SHOW_ON_ANY_SCORE` (`nodes.py`) currently
bypasses the band above so the prompt shows on every scored turn regardless
of confidence, for broader feedback collection during testing. The band
constants themselves are untouched; set the flag back to `False` to restore
band-gated behavior.

**Why a rating, not a numeric quality score.** The customer's own feeling
about the whole response is real ground truth, better than any proxy —
reserving it for exactly the band where the proxy is least sure is the same
principle as reserving genuine LLM judgment for cases that need it rather
than manufacturing an artificial score everywhere.

**Per turn, not per subquery.** Subqueries are an internal decomposition
never shown to the customer, who only ever sees one response to one
question. A rating is recorded once per turn (`up = 1`, `down = 0`) and, in
`_turn_confidence`, *replaces* that turn's schema-derived average entirely
rather than blending with it — once a human has weighed in on a turn, that
judgment is the whole story for it.

**Storage: raw schema, not a derived score.** Every turn — regardless of
whether the thumbs prompt was ever shown — durably logs its full
`financial_subqueries` schema (the same `resolution_status`/`tool_attempts`
shape Tool Calling v3.0 already produces) to a new `Turn_Resolution_Feedback`
SQLite table (`memory_service.py`), keyed by `(session_id, turn_number)`.
The 0/0.5/1.0 score is deliberately *not* stored alongside it: it's always
recomputable from the raw schema via `_score_subquery`, so storing it too
would just be a value that could silently drift out of sync with a future
change to the scoring formula. The user's rating is the one thing that
truly can't be derived from anything else, so it's the only additional
column (`user_rating`, nullable, set later via the feedback endpoint).

This also gives the raw data a second, independent use: because the rating
is per-turn but the schema is per-subquery, a turn where every subquery
came back `RESOLVED`/`SUFFICIENT` yet still got thumbs-down is a concrete
signal that the sufficiency auditor is being too lenient, or the tool
planner selected evidence that was topically right but not what the
customer actually needed — a finding the derived score alone couldn't
surface.

**Live cross-request adjustment.** A rating isn't only durable analysis
data — the endpoint (`POST /api/feedback/turn-rating` in `api_gateway.py`)
also patches the *live*, still-open session's checkpointed
`turn_escalation_history` via `app_graph.update_state(...)`, setting
`user_rating` on the matching turn's entry. Because the rating necessarily
arrives after that turn's own response (and escalation decision) already
went out, it can only sharpen *later* turns' rolling confidence — this is a
deliberate lagging correction, not a retroactive one. Verified live: a
turn that scored 0.0 was rated thumbs-up, and a subsequent turn's rolling
confidence (computed from a fresh graph invocation, a different in-process
request than the one that submitted the rating) correctly reflected the
override — a `low_confidence` trigger that would otherwise have fired did
not.

## 5. New/changed state and storage

`AgentState` (`state.py`), session-persistent unless noted:

```text
session_started_at: str                # ISO timestamp, set once at login
same_intent_streak_count: int          # maintained by node_02e
turn_escalation_history: list[dict]    # rolling window, capped at MAX_ESCALATION_HISTORY_TURNS
repeated_need_detected_this_turn: bool # volatile; set by node_02d, consumed by node_08
```

Each `turn_escalation_history` entry:

```text
{
  "turn_number": int,
  "had_repeated_need": bool,           # this turn's repetitive_issue_detected
  "subqueries": [{"text": str, "score": float}],
  "user_rating": int | None,           # 1, 0, or None until rated
}
```

`Turn_Resolution_Feedback` (SQLite, `memory_service.py`):

```text
session_id, turn_number, customer_id, financial_subqueries (JSON),
user_rating (nullable), created_at, rated_at
PRIMARY KEY (session_id, turn_number)
```

## 5a. A related consumer, and a matching bug fix (since superseded for that consumer)

`node_03c`'s RAG retry-depth decision briefly shared this same
rolling-confidence primitive (`_rolling_bot_confidence`, `_turn_confidence`)
as a second consumer, gated by a minimum-sample guard
(`MIN_SCORED_TURNS_FOR_CONFIDENCE_TRUST`, previously
`MIN_SCORED_TURNS_FOR_LEAN_RAG_RETRY`). That guard's introduction is what
surfaced this node's own `confidence_triggered` rule had never had an
equivalent one, so a single bad first turn (score 0.0, n=1) could trigger
the "Talk to Agent" chip's low-confidence signal immediately, before any
real track record existed. Fixed identically here:
`confidence_triggered` now also requires
`scored_turn_count >= MIN_SCORED_TURNS_FOR_CONFIDENCE_TRUST`, logged via a
new `scored_turn_count` field on `escalation.soft.triggered` — this part
is still exactly as described.

`node_03c`'s own use of the *rolling/historical* signal was subsequently
replaced, and has since gone through a couple of further iterations —
most recently a formal one-tailed hypothesis test over up to 30 recent
turn grades, with this turn's own in-progress grade as the sample's
newest point (see `RAG_RETRY_EFFORT_v1.0.md` for the full current design
and the reasoning that led there). This node's `confidence_triggered`
rule, and the guard fixed above, are unaffected by any of that — a plain
rolling average is the right tool for node_08's session-level judgment
("should a human be offered"), even though `node_03c` needed something
more rigorous for its own, differently-shaped decision.

One storage detail worth knowing: `turn_escalation_history` is now stored
at a longer 30-turn window (`HYPOTHESIS_TEST_SAMPLE_SIZE`, needed by
`node_03c`'s test), not the 10-turn `MAX_ESCALATION_HISTORY_TURNS` it used
to be capped at. This node's own signals are unaffected — it explicitly
slices `history[-MAX_ESCALATION_HISTORY_TURNS:]` before computing its own
rolling confidence and repeated-need count, so a confidence dip or a
repeated need from 20+ turns ago still ages out of *this* node's view
exactly as before, even though the underlying stored list is now longer.

Also see `DECLINE_CLARIFICATION_DESIGN_v1.0.md` §10: a turn that ends in
`node_09`/`node_11`'s exhausted-retry fallback now always appends a
force-scored 0.0 entry to `turn_escalation_history` via
`_append_failed_turn_escalation_history`, since neither of those nodes
passes through this node to get one otherwise — previously such a turn
was invisible to the rolling-confidence window instead of dragging it
down.

**Banner rework (§1a):** `backend/app/orchestration/nodes.py` (`node_08`
no longer appends to `payload["options"]`; sets
`payload["softEscalationAvailable"]` unconditionally every turn, logs
`escalation.soft.triggered` only when true), `backend/app/api_gateway.py`
(`softEscalationAvailable` on `final_payload`), `frontend/src/App.jsx`
(`softEscalationAvailable` state, reset on send; banner JSX above the
input box with its own action button; deleted the old options-array chip
rendering path for this specific chip), `frontend/src/translations.js`
(`misc.softEscalationBanner`/`misc.softEscalationAction` keys).

## 6. Files touched

`backend/app/core/llm_service.py` (`repetitive_issue_detected` on
`TriageEvaluation`), `backend/app/orchestration/state.py` (new fields
above), `backend/app/orchestration/nodes.py` (`node_02d`'s repetition rule,
`node_02e`'s same-intent streak, `node_08`'s full rewrite plus
`_score_subquery`/`_turn_confidence`), `backend/app/services/memory_service.py`
(`Turn_Resolution_Feedback` table, `persist_turn_resolution`,
`record_turn_rating`), `backend/app/api_gateway.py` (durable per-turn write,
`showFeedbackPrompt`/`turnNumber` response fields,
`POST /api/feedback/turn-rating`), `frontend/src/App.jsx` (thumbs UI,
top-level escalation-chip rendering for `blocks`-type responses — see
section 7), `frontend/src/translations.js` (`feedback.*` keys).

## 7. Related fix: the escalation chip was invisible for `blocks`-type responses

While building this, live tracing found that the pre-existing chip
mechanism (`ui_action_payload.options`) was silently dropped by the
frontend whenever a response used `uiType: "blocks"` — the default format
for any multi-element answer, and therefore the common case. The renderer
only ever read each block's own nested `options` (follow-up suggestion
chips from the response generator), never the top-level array the
escalation chip lives in. This affected the original `turn_count >= 5`
mechanism too, not just this rework; it just went unnoticed because that
flat threshold rarely fired in normal testing. Fixed in `App.jsx` by
rendering `msg.options` as chips beneath the response blocks when present.

A second, unrelated bug surfaced during the same work: `node_02e`'s
same-intent-streak signature hashed `target_commercial_product` directly,
which crashes with `unhashable type: 'list'` whenever a retry widens that
field to multiple products (a `PRODUCT_EXPANSION` retry stage). Fixed by
routing it through the existing `_normalized_product_tags` helper before
hashing.
