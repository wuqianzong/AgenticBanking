# Confidence-Scaled RAG Retry Effort v1.0

**Status:** Implemented and live-verified. This is a living document —
update it in place as anything changes.

**Revision note:** this design originally gated RAG's retry depth by the
session's rolling *historical* confidence alone (the same signal
`SOFT_ESCALATION_v1.0.md` uses for the "Talk to Agent" chip). That version
shipped, was traced live, and was replaced with a *dynamic*, per-task,
in-turn signal instead — the historical/session signal alone was the
wrong sole basis for this job: it decided whether *this* task got more
effort based on how *other, unrelated* past questions went, which could
deny a genuinely struggling task extra effort just because the session's
broader track record looked good. The historical signal wasn't dropped
entirely, though (§3) — it now participates in the same dilution average
as one equal-weight vote alongside this turn's own evidence, rather than
as a pre-decided override. It also remains exactly as before, completely
independently, for soft escalation.

## 1. What this is

`node_03c_sufficiency_auditor` always used to give every RAG task the
same fixed budget: up to `MAX_RAG_ATTEMPTS` (4) rounds —
DIRECT → INTENT_EXPANSION → PRODUCT_EXPANSION → ANGLE_EXPANSION — before
giving up. Rounds 3 and 4 are RAG's most expensive attempts (each a full
retrieval + sufficiency LLM round-trip) for, as it turns out, the lowest
marginal payoff (§2). DIRECT + INTENT_EXPANSION (`NORMAL_EFFORT_RAG_ATTEMPTS = 2`)
remain an unconditional floor every task gets. Whether a task continues
past that floor into rounds 3-4 is now decided fresh every round, from
this turn's own live evidence, not from history.

## 2. Why rounds 1-2 are the floor, not part of the gate

A historical analysis of 333 real (session, turn, task) RAG outcomes
(deduplicated by `(session_id, turn_index, task_id)`, chronologically
ordered across rotated log files) found a heavily front-loaded funnel:

| Stage | Marginal rescue rate (of tasks reaching this stage) | 95% Wilson CI |
|---|---|---|
| Round 1 DIRECT | 47.4% (158/333) | [42.1%, 52.8%] |
| Round 2 INTENT_EXPANSION | 30.9% (54/175) | [24.5%, 38.1%] |
| Round 3 PRODUCT_EXPANSION | 9.1% (11/121) | [5.2%, 15.5%] |
| Round 4 ANGLE_EXPANSION | 5.4% (5/92) | [2.3%, 12.1%] |

Rounds 1+2 together capture **~93-94% of every eventual success** (212/228
raw; 94.0% after correcting for duplicate/near-duplicate test queries via
fuzzy-cluster weighting — see §2a). Round 2 is also free relative to
rounds 3/4: it's a deterministic expansion (no extra LLM call), unlike
PRODUCT_EXPANSION and ANGLE_EXPANSION, which each cost one. This is why
rounds 1-2 are an unconditional floor rather than part of the confidence
gate at all — cutting a task off before round 2 would forfeit a real,
cheap 16-35%-of-remainder rescue rate for no compute savings.

Rounds 3 and 4 are *not* split into two separate gated tiers ("extra" vs
a hypothetical "max effort"): their rescue-rate confidence intervals
overlap (9.1% vs 5.4%, both wide), and an earlier backtest of historical
confidence against which round a turn actually needed showed no
separation between the two populations. There's no data basis for a
second threshold between them, so they're gated together as one decision,
re-evaluated at each of the two boundaries (after round 2, and again after
round 3) using the same live signal described in §3.

### 2a. Caveat on the historical numbers

The 333-outcome sample includes heavy duplication from iterative live
testing (the same or near-identical query replayed across many sessions
during development) — 159 distinct normalized query texts, collapsing to
105 fuzzy-matched clusters. A cluster-weighted re-analysis (Kish effective
sample size, each occurrence weighted `1/cluster_size` so a query replayed
ten times contributes the same total evidence as one asked once) gives
wider but directionally identical intervals (round 1: 49.7% [41.8%,
57.6%]; round 4: 1.8% [0.2%, 12.2%]) — the round-1+2 dominance conclusion
is stable across both methods; the exact round-3/4 magnitude is not
tightly pinned down by either.

## 3. Live per-task dynamic confidence, and turn-level dilution

`_dynamic_task_confidence(result, partial_evidence)` (`nodes.py`) scores
one task's *current-round* sufficiency result — reusing the same 0/0.5/1.0
tiers `_score_subquery` already uses elsewhere, but reflecting what just
happened this round, not a rolling average:

| This round's status | Dynamic confidence |
|---|---|
| `SUFFICIENT` | 1.0 |
| `PARTIALLY_SUFFICIENT` | 0.5 |
| `RETRY` (still mid-ladder) with retained partial evidence | 0.5 |
| `RETRY` with nothing yet, or terminal `CLARIFICATION_REQUIRED` | 0.0 |

After `NORMAL_EFFORT_RAG_ATTEMPTS` (2), a task that isn't yet
`SUFFICIENT` doesn't automatically get to continue, and doesn't
automatically stop either — the decision comes from **this turn's whole
picture**, not this one task in isolation: `node_03c` averages the
dynamic confidence of *every* task resolved this round (including
siblings already finalized `SUFFICIENT` earlier in the same round) into
one `provisional_turn_confidence`. A task past the floor continues into
the next round only if that average is below
`EXTRA_EFFORT_CONFIDENCE_THRESHOLD` (0.7); `MAX_RAG_ATTEMPTS` (4) is still
a hard ceiling regardless.

This means the same struggling task is treated differently depending on
its siblings in the same turn:

- **A lone struggling task** (the common case — most turns have one or a
  handful of subqueries) almost always continues: with nothing else to
  average against, its own low score *is* the turn average, reliably
  below 0.7.
- **A struggling task with several already-successful siblings this
  round** may not get extended: three `SUFFICIENT` siblings (1.0 each)
  averaged with one failing task (0.0) is already 0.75 — above threshold,
  so that task is finalized at the round-2 boundary rather than spending
  two more paid LLM calls on a turn that, overall, is already reading as
  confidently resolved. Two successful siblings only average to 0.667 with
  one failure, which still extends — dilution has to be real (several
  successes, or the failing task itself carrying partial evidence) to
  flip the decision; it isn't a rounding error.

### 3a. The session's rolling historical confidence joins the same average

The lone-struggling-task case above is the common one, and it means the
turn-level average rarely differs from that one task's own score — the
session's history had no way to matter for the majority of real turns.
`provisional_confidences` now includes the session's rolling historical
confidence (`_rolling_bot_confidence`) as one more equal-weight entry in
the same average, whenever it's trustworthy enough to count at all (same
`MIN_SCORED_TURNS_FOR_CONFIDENCE_TRUST` guard `node_08` uses for its own
rule). Not a separate blending formula, and not a pre-decided override —
a real current failure still drags a confident session's average down and
extends (`0.0` averaged with a `0.9` history is `0.45`, still `< 0.7`),
but a confident session's history now gives real ballast against
extending on a merely-*partial* result (`0.5` averaged with `0.9` history
is exactly `0.7` — no longer automatically extended). This is where the
feature's cost savings return for the common single-subquery turn,
without reintroducing the flaw §1's revision note describes: history
alone can no longer decide the outcome when this turn's own evidence
says otherwise, but it earns a real, bounded say when the evidence is
ambiguous (partial, not a clean failure).

Verified numerically for the boundary cases (0, 1, 2, 3, 4 successful
siblings against one fully-failing task, and 2 successes against one
partially-successful task) before shipping — see the worked table in the
commit history for this file if reconstructing the check.

## 4. What this replaced, and what stayed the same

**Replaced:** a static gate keyed *solely* on the session's rolling
*historical* confidence (`_rolling_bot_confidence` over up to the last 10
completed turns) with three guards (minimum sample size, strict `>`, a
repeated-need override) deciding, once per turn before round 1 even ran,
whether this task's ladder was capped at the floor or got the full
budget — a decision this turn's own evidence had no way to override.
`_rolling_bot_confidence`/`_turn_confidence` are still called from
`node_03c` (§3a), but only as one vote in a live per-turn average, never
as a sole, pre-decided gate again.

**Stayed the same:** `_rolling_bot_confidence` still independently gates
`node_08`'s "Talk to Agent" chip exactly as `SOFT_ESCALATION_v1.0.md`
describes, at its own separate threshold
(`SOFT_ESCALATION_CONFIDENCE_THRESHOLD = 0.5`) — that use is untouched by
anything in this document. The two consumers now read the same
underlying number for genuinely different purposes: node_08 uses it
alone for a session-level judgment (should a human be offered);
`node_03c` blends it into a task-level one (should this specific task get
another try right now), where it's one input among several, never the
deciding one on its own.

`is_low_confidence` / `low_confidence_mode` ("Thinking harder") now
tracks the *dynamic* `provisional_turn_confidence` instead of the old
static rolling confidence — still against the same
`EXTRA_EFFORT_CONFIDENCE_THRESHOLD` (0.7), since its job is still "is
extra effort actually happening on this turn right now," which is a
better fit for a live signal than a historical one anyway.

## 5. Live confidence: streamed to the frontend, logged for future analysis

`live_task_confidence` (`AgentState`) carries `provisional_turn_confidence`
out of `node_03c` every time it runs — once per retry round, not once per
turn, so it's genuinely live rather than a single number computed up
front. `api_gateway.py`'s SSE progress stream reads it off each node's
state update (`taskConfidence` in the JSON payload, deliberately *not*
sticky like `lowConfidenceMode` — it only updates when `node_03c` actually
returns a fresh value, otherwise holds the last one) and the frontend
(`taskConfidence` in `App.jsx`) renders it as a live percentage above the
loading bubble, alongside (not replacing) the existing "Thinking harder"
label.

Every individual task's dynamic confidence is also logged per round
(`task.dynamic_confidence`: `task_id`, `round`, `status`, `confidence`),
and the terminal boundary decision logs the turn-level average that drove
it (`evidence.boundary.terminal_unresolved`'s `turn_confidence_if_stopped`)
— both durable, directly queryable records for future analysis of this
mechanism, unlike the historical-confidence version, which had to be
reconstructed indirectly from terminal task events (and was shown to be
error-prone doing so — see the historical-validation trace this document's
§2 data came from).

## 6. `node_08`'s own confidence rule had a separate, unrelated thin-history bug

While the RAG-effort gate above no longer uses `_rolling_bot_confidence`
at all, `node_08`'s own `confidence_triggered` rule (the "Talk to Agent"
chip's low-confidence signal) still does, and had never had a
minimum-sample guard: a single bad first turn (score 0.0) made rolling
confidence 0.0 with n=1, which satisfies `< 0.5`, offering a human handoff
after one failed exchange. Fixed by requiring
`scored_turn_count >= MIN_SCORED_TURNS_FOR_CONFIDENCE_TRUST` (4) before
that rule can fire, logged via a new `scored_turn_count` field on
`escalation.soft.triggered`. This constant (originally
`MIN_SCORED_TURNS_FOR_LEAN_RAG_RETRY`, renamed once it had a second
consumer) is the one piece of the historical-confidence machinery this
document's revision leaves untouched, since it protects `node_08`, not
`node_03c`.

## 7. Exhausted retries and hard escalation

What happens when even the full ladder exhausts is covered in
`DECLINE_CLARIFICATION_DESIGN_v1.0.md` §10: hard escalation
(`node_09`/"n23") is reserved for a customer's own explicit request now,
never for the bot's own retry exhaustion, and every exhausted-retry turn
is scored 0.0 for the rolling (historical, escalation-facing) confidence
via `_append_failed_turn_escalation_history` — closing a gap where such a
turn previously contributed no data point at all instead of the failure
it represented.

## 8. Files touched

`backend/app/orchestration/nodes.py` (`EXTRA_EFFORT_CONFIDENCE_THRESHOLD`,
`NORMAL_EFFORT_RAG_ATTEMPTS`, `_dynamic_task_confidence`, `node_03c`'s
two-pass retry-decision loop and `provisional_turn_confidence`/
`live_task_confidence` -- including the `_rolling_bot_confidence` vote in
`provisional_confidences`, §3a -- `node_08`'s `confidence_triggered`
guard),
`backend/app/orchestration/state.py` (`live_task_confidence`),
`backend/app/api_gateway.py` (`taskConfidence` on the SSE progress
stream), `frontend/src/App.jsx` (`taskConfidence` state, live percentage
readout), `frontend/src/translations.js` (`caption.confidence`,
`caption.thinkingHarder`).
