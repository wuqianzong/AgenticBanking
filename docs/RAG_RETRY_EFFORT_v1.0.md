# Confidence-Scaled RAG Retry Effort v1.0

**Status:** Implemented and live-verified. This is a living document —
update it in place as anything changes.

## 1. What this is

`node_03c_sufficiency_auditor` always used to give every RAG task the
same fixed budget: up to `MAX_RAG_ATTEMPTS` (4) rounds —
DIRECT → INTENT_EXPANSION → PRODUCT_EXPANSION → ANGLE_EXPANSION — before
giving up. Rounds 3 and 4 are RAG's most expensive attempts (each a full
retrieval + sufficiency LLM round-trip) for, as it turns out, the lowest
marginal payoff. This reuses the same rolling bot-confidence signal
`SOFT_ESCALATION_v1.0.md` already computes to decide, per task, whether a
session's recent track record justifies capping the ladder early instead
of always spending the full budget.

Two effort tiers:

- **Normal effort** — DIRECT + INTENT_EXPANSION only (`effective_max_rag_attempts = 2`).
  Always granted; never gated by confidence.
- **Extra effort** — the full ladder, up to `MAX_RAG_ATTEMPTS` (4). Granted
  whenever the session isn't confidently established as "normal effort is
  enough," per the guards in §3.

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
gate at all — cutting a session off before round 2 would forfeit a real,
cheap 16-35%-of-remainder rescue rate for no compute savings.

Rounds 3 and 4 were *not* split into two separate gated tiers ("extra" vs
a hypothetical "max effort"), despite the original proposal considering
it: their rescue-rate confidence intervals overlap (9.1% vs 5.4%, both
wide), and a backtest of reconstructed rolling confidence against which
round a turn actually needed showed no separation between the two
populations (round-3-need mean confidence 0.579 vs round-4-need mean
0.717 — backwards from the assumption, on n=2/n=9, i.e. noise). There's no
data basis for a second threshold between them, so they're gated together
as one "extra effort" tier.

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

## 3. Two thresholds, not one, and three guards

`EXTRA_EFFORT_CONFIDENCE_THRESHOLD = 0.7` gates the RAG-effort decision;
`SOFT_ESCALATION_CONFIDENCE_THRESHOLD = 0.5` (unchanged, `SOFT_ESCALATION_v1.0.md`)
gates the "Talk to Agent" chip. They're deliberately different values, not
the same constant reused twice:

- An extra RAG round is cheap and invisible to the customer; offering
  human escalation is not. The cheap lever should trigger at a less severe
  confidence dip than the expensive/visible one.
- This guarantees extra effort is always already active by the time
  escalation is offered (0.7 > 0.5, never the reverse), and gives the bot
  a silent chance to recover in the 0.5-0.7 band before anything
  customer-facing changes — a customer never sees "we're trying harder"
  and "or talk to a human" appear in the same breath unless confidence has
  already dropped past both bars.

`eligible_for_lean_retry` (→ normal effort) requires all three:

1. `MIN_SCORED_TURNS_FOR_CONFIDENCE_TRUST` (4) scored prior turns exist —
   1-2 turns of history can swing the rolling average by a full 0.5 on a
   single result, which is noise, not a track record. Below this count,
   the ladder defaults to extra effort regardless of what the raw average
   says. (Originally named `MIN_SCORED_TURNS_FOR_LEAN_RAG_RETRY`; renamed
   once `node_08` needed the identical guard for its own confidence rule —
   see §4.)
2. Rolling confidence is strictly `>` (not `>=`) the threshold — exactly at
   the boundary is a coin-flip record, not confidence, matching
   `SOFT_ESCALATION_v1.0`'s own strict `<` for the opposite direction.
3. No `repeated_need_detected_this_turn` this turn — a customer visibly
   re-asking or rephrasing is a fresher, more relevant signal than a
   multi-turn rolling average, and forces the full ladder regardless of
   what the average says.

All three were added after a live-traced failure: with a naive `>=`
threshold and no minimum sample size, one successful turn drove rolling
confidence to 1.0, so the very next (unrelated) question got the lean path
and failed outright; the customer's frustrated "why, can you try harder?"
follow-up landed on exactly 0.5 confidence (one success, one failure),
which the naive `>=` check *still* classified as confident-enough,
repeating the same under-effort failure a second time in a row.

## 4. `is_low_confidence` / "Thinking harder"

`low_confidence_mode` (`AgentState`) is set by `node_03c` when rolling
confidence is genuinely low — `scored_turn_count >= MIN_SCORED_TURNS_FOR_CONFIDENCE_TRUST`
and confidence `< EXTRA_EFFORT_CONFIDENCE_THRESHOLD` — not merely "no
track record yet" (a brand-new or thin-history session gets the full
ladder too, but isn't reported as "struggling"). It tracks the
*extra-effort* threshold (0.7), not the escalation threshold (0.5),
because its job is to reflect whether extra retrieval effort is actually
happening right now, not whether escalation is imminent.

Plumbed through `api_gateway.py`'s SSE progress stream as `lowConfidenceMode`
(sticky once any chunk sets it, for the rest of that turn) and rendered by
the frontend as an amber "Thinking harder" label above the loading bubble
(`isLowConfidenceMode` in `App.jsx`).

Net result — three observable bands as rolling confidence falls:

| Rolling confidence | RAG effort | "Thinking harder" shown | "Talk to Agent" offered |
|---|---|---|---|
| `> 0.7` | Normal (rounds 1-2) | No | No |
| `0.5 - 0.7` | Extra (rounds 1-4) | Yes | No |
| `< 0.5` | Extra (rounds 1-4) | Yes | Yes |

## 5. `node_08`'s own confidence rule had the same thin-history bug

Tracing why "Thinking harder" never appeared in a session that clearly had
declining confidence led to finding that `MIN_SCORED_TURNS_FOR_CONFIDENCE_TRUST`
had only ever been applied to `node_03c`'s effort decision — `node_08`'s
own `confidence_triggered` rule (the "Talk to Agent" chip's low-confidence
signal) computed `confidence < SOFT_ESCALATION_CONFIDENCE_THRESHOLD`
directly off `_rolling_bot_confidence`, with no minimum-sample guard at
all. A single bad first turn (score 0.0) made rolling confidence 0.0 with
n=1, which satisfies `< 0.5`, offering a human handoff after one failed
exchange — exactly the failure mode §3's guards were built to prevent, just
never carried over to this second consumer of the same primitive. Fixed by
applying the same guard: `node_08` now requires
`scored_turn_count >= MIN_SCORED_TURNS_FOR_CONFIDENCE_TRUST` before its
confidence rule can fire, logged via a new `scored_turn_count` field on
`escalation.soft.triggered`.

## 6. Exhausted retries and hard escalation

What happens when even the full ladder exhausts is covered in
`DECLINE_CLARIFICATION_DESIGN_v1.0.md` §10: hard escalation
(`node_09`/"n23") is reserved for a customer's own explicit request now,
never for the bot's own retry exhaustion, and every exhausted-retry turn
is scored 0.0 for the rolling confidence this document's gating depends
on — closing a gap where such a turn previously contributed no data point
at all instead of the failure it represented.

## 7. Files touched

`backend/app/orchestration/nodes.py` (`EXTRA_EFFORT_CONFIDENCE_THRESHOLD`,
`MIN_SCORED_TURNS_FOR_CONFIDENCE_TRUST` (renamed), `node_03c`'s
`effective_max_rag_attempts`/`is_low_confidence` logic, `node_08`'s
`confidence_triggered` guard), `backend/app/orchestration/state.py`
(`low_confidence_mode`), `backend/app/api_gateway.py` (`lowConfidenceMode`
on the SSE progress stream), `frontend/src/App.jsx` (`isLowConfidenceMode`,
"Thinking harder" label), `frontend/src/translations.js`
(`caption.thinkingHarder`).
