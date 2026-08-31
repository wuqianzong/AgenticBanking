# Confidence-Scaled RAG Retry Effort v1.0

**Status:** Implemented and live-verified. This is a living document —
update it in place as anything changes.

**Design history, briefly** (full detail in git history for this file):
this went through three iterations before landing here. (1) A session's
rolling *historical* confidence alone gated whether a task's ladder was
capped at the floor — wrong sole basis, since it decided whether *this*
task got more effort based on how *other, unrelated* past questions went.
(2) Replaced with a purely *dynamic*, per-task, in-turn signal, diluted
across a turn's siblings — better, but a lone struggling task (the common,
single-subquery case) then *always* extended regardless of session
history, since with nothing to dilute against, its own low score was the
whole average — this quietly gave back most of the feature's original
cost-saving purpose. (3) The current design: a formal one-tailed
hypothesis test over a rolling sample of recent turn grades, with this
turn's own (still-forming) grade as the sample's newest point. This is
the version described below.

## 0. Why a hypothesis test, not a per-task guess or a raw threshold

The underlying cost/quality tension: rounds 3-4 are RAG's most expensive
attempts, and per §2's historical data, rescue only 9.1% and 5.4% of the
tasks that reach them, respectively. Spending them on any given task is a
bet that mostly doesn't pay off, and there is no signal available at
decision time that reliably predicts, for *this specific task*, whether
it will be one of the ones it does pay off for. Trying to guarantee that
any one turn gets the best possible answer is not a decision that can be
made well with what's actually knowable in the moment -- and guaranteeing
it anyway (spending rounds 3-4 unconditionally on every struggling task)
is exactly the cost this feature exists to avoid.

So the goal is deliberately not "make this turn as good as possible" but
"keep this customer's *average* response quality above an acceptable bar,
at the lowest total cost." That reframing turns an unanswerable per-task
question into a tractable population question -- structurally the same
problem statistical process control solves on a factory floor, or a
sequential monitoring rule solves in a clinical trial: when you can't
inspect (or fix) every unit at full cost, and can't know in advance
whether intervening on any one unit will help, the answer is to monitor
the *aggregate* and only intervene when there's real evidence the
aggregate itself has drifted, rather than reacting every time one sample
looks bad -- because a single bad sample is exactly as likely to be
ordinary noise as a real problem.

This is precisely why the mechanism is a significance test rather than a
raw moving-average-vs-threshold check. A raw threshold treats a customer
whose scores are naturally spread out (a mix of genuinely hard and easy
questions -- high variance) exactly the same as one whose scores are
naturally stable, reacting to any dip below the line regardless of
whether that dip is plausible ordinary variation for *that* customer or a
real, sustained shift. A hypothesis test is the tool built specifically
to tell those two cases apart: it estimates variance from the customer's
own sample and only rejects H0 when the dip is large relative to how
noisy their own history already is -- exactly the discrimination needed
to protect the population-level average without either reacting to every
fluke (wasting the cost the feature exists to save) or missing a real,
low-variance decline (failing the quality bar the feature exists to
protect).

## 1. What this is

`node_03c_sufficiency_auditor` always used to give every RAG task the
same fixed budget: up to `MAX_RAG_ATTEMPTS` (4) rounds —
DIRECT → INTENT_EXPANSION → PRODUCT_EXPANSION → ANGLE_EXPANSION — before
giving up. Rounds 3 and 4 are RAG's most expensive attempts (each a full
retrieval + sufficiency LLM round-trip) for, as it turns out, the lowest
marginal payoff (§2). DIRECT + INTENT_EXPANSION (`NORMAL_EFFORT_RAG_ATTEMPTS = 2`)
remain an unconditional floor every task gets. Whether a task continues
past that floor into rounds 3-4 is decided by a formal statistical test
(§4), re-evaluated fresh every round.

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
rounds 1-2 are an unconditional floor rather than part of the gate at all
— cutting a task off before round 2 would forfeit a real, cheap
16-35%-of-remainder rescue rate for no compute savings.

Rounds 3 and 4 are not split into two separately-gated tiers: their
rescue-rate confidence intervals overlap (9.1% vs 5.4%, both wide), and a
backtest of confidence against which round a turn actually needed showed
no separation between the two populations. There's no data basis for a
second threshold between them, so the same test (§4) governs both
boundaries (after round 2, and again after round 3).

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

## 3. Turn grade: the average of *every* subquery's grade, not just RAG's

Each subquery in a turn gets a 1.0/0.5/0.0 score (`_score_subquery`'s rule,
used everywhere else in this codebase: `RESOLVED` → 1.0, `OPEN` with real
partial evidence → 0.5, `OPEN` with nothing → 0.0). A turn's grade is the
plain average of its subqueries' grades — never a RAG-task-centric
computation. This distinction matters: a turn can have one subquery
resolved instantly by a different tool entirely (deeplink/service_request/
api) while another is still being chased by RAG. An earlier version of
this computation only sampled from RAG's own task results, silently
dropping the other subquery's contribution — understating the turn's true
grade whenever a turn mixed tools. `node_03c` now builds
`provisional_turn_grade` by walking every subquery in
`state["financial_subqueries"]`:

- A subquery RAG has touched this turn (this round or an earlier one)
  uses a rollup-aware score (`_dynamic_task_confidence`, or the tentative
  0.5/0.0 for one still being decided this exact round) — **never**
  `_score_subquery` directly. `_finalize_rag_attempts` (which pushes a
  settled RAG result into `financial_subqueries`' own `tool_attempts`)
  only runs once every task in the turn is fully terminal, so while a
  sibling task is still mid-ladder, `_score_subquery` would read this
  one's `rag` attempt as still `"pending"` even after it has actually
  resolved — the same staleness `_resolved_by_other_tool` exists to work
  around elsewhere (`DECLINE_CLARIFICATION_DESIGN_v1.0.md` §6).
- A subquery RAG never touched this turn at all (resolved entirely by
  another tool) uses `_score_subquery` directly — its `tool_attempts` are
  always accurately terminal already, no staleness risk.

`provisional_turn_grade` (the plain average above) is what gets displayed
and logged as this turn's own contribution -- but as of §4 below, it is
*not* what the hypothesis test samples from. The test draws on the
individual `subquery_scores` list this same computation produces, one
point per subquery, before it gets collapsed into that average.

## 4. The hypothesis test

Whether a task past the floor continues into rounds 3-4 is decided by a
one-tailed, one-sample hypothesis test, not a raw average-vs-threshold
check (which can't distinguish genuine decline from a couple of noisy
data points):

- **H0**: the population mean of recent subquery grades is `>= CONFIDENCE_TEST_POPULATION_MEAN_H0` (**0.75** -- a business decision, grounded in this system's own real, achievable baseline per §2's historical funnel data rather than an aspirational round number; an earlier attempt at 0.9 sat so far above what this system typically achieves that it rejected H0 almost unconditionally -- see §4e).
- **H1**: the population mean has genuinely dropped below 0.75.
- **Sample unit is one subquery, not one turn.** Each subquery gets its own 1.0/0.5/0.0 grade in the sample -- a turn with three subqueries contributes three points, not one averaged point. An earlier version of this test sampled turn-averages instead (`_turn_response_quality` over `turn_escalation_history`); averaging within a turn first collapsed several independent-ish observations into a single, more discrete data point before the test ever saw them, which is strictly worse for a test whose validity depends on how much real information its sample carries. `_historical_subquery_scores` flattens every subquery across `turn_escalation_history` in chronological order; **this turn's own still-forming `subquery_scores` supply the newest points**, one per subquery, not a single averaged value.
- **Sample size**: up to `HYPOTHESIS_TEST_SAMPLE_SIZE` (30) recent subquery grades, replacing the oldest once the window is full. The population being tested is this customer's subqueries generally -- past, present, and future -- not just whichever login session happens to be open, so the historical portion of the sample is seeded from durable, cross-session history at login (§4a), not restarted empty every time the customer logs back in.
- **Test statistic**: an exact one-sample **t-test** (`_reject_confidence_null_hypothesis`) — `t = (sample_mean - 0.75) / (sample_std / sqrt(n))`, rejecting H0 when `t < -critical_t` for the chosen significance level (`CONFIDENCE_TEST_ALPHA = 0.05`). `critical_t` comes from `CONFIDENCE_TEST_T_CRITICAL`, a standard published lookup table keyed by `[alpha][degrees_of_freedom]` (`df = n - 1`), falling back to the asymptotic z-value (`CONFIDENCE_TEST_Z_CRITICAL`, `1.645` one-tailed at `alpha=0.05`) once `n` exceeds the table (i.e. once the sample reaches `HYPOTHESIS_TEST_SAMPLE_SIZE`). A t-test, not a z-test: the population standard deviation is never known here, only estimated from the sample, which is exactly the situation a t-test is the correct tool for, at *any* sample size — not an approximation valid only once n is large. No scipy/numpy dependency needed at runtime — the table is hardcoded, standard textbook values (scipy was used offline only to verify the table and run the Monte Carlo checks in §4e).
- **Minimum sample size**: `MIN_SCORED_SUBQUERIES_FOR_CONFIDENCE_TRUST = 15`, smaller than the full `HYPOTHESIS_TEST_SAMPLE_SIZE` (30) window. See §4e: this floor is re-derived by Monte Carlo whenever H0 changes, since how close H0 sits to the score scale's ceiling controls how much a small, discrete 3-valued sample miscalibrates the test -- at the current H0=0.75 (well clear of the ceiling), calibration is already reasonable by n=15. A brand-new or thin-history customer gets the unconditional floor (rounds 1-2) and nothing past it from this mechanism until 15 scored subqueries accumulate -- `has_repeated_need_this_turn` (§4c) is the only lever available before then.
- **`alpha=0.05` means 95% confidence, one-sided, not the symmetric two-sided version.** Because H1 is one-directional (decline only -- this design never cares about quality becoming suspiciously "too good"), the critical value used is the **one-tailed** `1.699` at df=29, not the wider **two-tailed** `2.045` a standard symmetric 95% confidence interval would use for the same df (splitting 5% into 2.5% on each side, to also guard against the direction this design deliberately ignores). Both are legitimately "95% confidence," but they're different bounds -- using the one-tailed value here is the intentional, correct choice given the one-directional business question, not an oversight.
- **Rejecting H0 → extend effort.** Failing to reject H0 does not "prove" quality is fine, only that there isn't significant evidence it's dropped — the conventional, correct interpretation of a non-rejection.
- **A repeated need this turn independently unlocks extra effort too**, regardless of what the test concludes (`extend_if_optional = reject_null_hypothesis or has_repeated_need_this_turn`, using triage's own `repetitive_issue_detected`). This is a deliberate satisfaction-driven override, not a statistical one -- see §4c.

**This is the direct answer to "how does this turn's own current quality factor in":** it isn't a separately-blended signal layered on top of the historical sample — every one of this turn's own subqueries **is** one more of the sample's most recent data points, alongside the historical ones.

**Response quality and customer satisfaction are deliberately kept separate, and now separate at the source rather than by a dedicated function.** A subquery's own score (`_score_subquery`, or the rollup-aware equivalent for a subquery RAG is still working on) is never touched by the customer's thumbs rating in the first place -- only `_turn_confidence`'s *turn-level* average does that, for `node_08`'s unrelated escalation judgment, where a customer's own feeling about a turn is exactly what "should we offer a human" should weigh. Sampling at the subquery level meant the earlier `_turn_response_quality` (a turn-level average with the rating override specifically stripped back out) was no longer needed to keep this test's sample rating-free -- it already is, without any extra function.

### 4a. The 29-turn half of the sample survives a login, on purpose

`turn_escalation_history` used to reset to `[]` on every login
(`node_00_context_hydrator_and_greeting`) — reasonable when it only fed
`node_08`'s session-scoped escalation signal, but wrong once it also
feeds a hypothesis test whose population is explicitly "this customer's
turns generally," not "this customer's turns within whichever session
happens to be open right now." A customer who typically sends only a
handful of turns per session before logging out would otherwise never
accumulate a meaningful sample size within any single session — the test
would perpetually run on thin, freshly-restarted data despite the
customer having a long real history.

Fixed by seeding `turn_escalation_history` at login from
`Turn_Resolution_Feedback` — the durable, per-turn table
`persist_turn_resolution` already writes to unconditionally, every turn,
regardless of session (`LongTermMemoryManager.fetch_recent_turn_resolutions`,
keyed by `customer_id` directly, spanning every past session). This still
seeds turn-by-turn (up to 29 historical turns), but node_03c now flattens
those turns into individual subquery scores (§4) before sampling from
them, so the customer's very first message of a brand-new login session
is already tested against a real, standing track record instead of
starting from an empty sample -- in practice this comfortably clears the
30-subquery floor whenever those 29 turns average more than one subquery
each, and falls one or two short only for a customer whose history is
almost entirely single-subquery turns, in which case the very next turn's
own subqueries complete the window. One accepted gap: `had_repeated_need` was never part of
that table's schema, so a rehydrated historical entry can't know whether
that turn had one — defaults to `False`. This only matters to `node_08`'s
own repeated-need signal (a different, shorter 10-turn window), and only
if a customer's login boundary happens to fall within that recent a
span.

**The query must filter out non-financial rows before applying `LIMIT`,
not after.** `persist_turn_resolution` writes a row every turn
unconditionally, including pure chitchat/greeting/non-financial ones
(`financial_subqueries = '[]'`) -- `_historical_subquery_scores` naturally
contributes nothing for these (no subqueries to flatten), but if the SQL query
takes the raw last `limit` rows first and only filters afterward, a
customer whose recent history includes a run of chitchat gets a thinner
sample than intended for no real reason. Confirmed live on one
heavily-tested account: 169 total rows, 65 of them empty -- fetching the
raw last 29 rows before this fix returned as few as ~20-24 usable ones,
which is exactly why an early trace of this feature saw
`n=23`/`insufficient_sample` in `_reject_confidence_null_hypothesis`'s
diagnostics instead of a clean 29. `fetch_recent_turn_resolutions` now
filters `WHERE financial_subqueries != '[]'` in the query itself, so
`LIMIT` only ever counts turns that can actually contribute a grade.

**This table does not go back to the start of this customer's real
history, and that's a limitation, not a bug to fix by querying
differently.** `Turn_Resolution_Feedback` only exists from whenever
`persist_turn_resolution` was first added to the codebase -- a much
larger historical count exists in raw application logs from before that
point (the 333-outcome sample `RAG_RETRY_EFFORT_v1.0.md` §2's funnel data
came from), but that data was never durably written to this table and
cannot be recovered into it after the fact. A long-tenured customer's
sample will still need to accumulate up to 30 genuinely-scored turns from
this table's start date before the hypothesis test can run at full
strength; there's no query fix for data that was never persisted.

### 4b. A deliberate behavioral consequence worth knowing

A lone struggling subquery in an otherwise excellent history (29 perfect
1.0 scores plus one 0.0) will generally **not** trigger extra effort,
because that much strong evidence isn't statistically overturned by one
bad point (verified: `mean=0.967`, `t=+6.5`, strongly positive and nowhere
near the rejection region). This is the *correct* conclusion for a
rigorous test to reach, and it's a real, deliberate shift from the prior
(§1, iteration 2) design, where a lone failing task always extended
regardless of history. The trade-off: a customer hitting one hard question
in an otherwise smooth history may now get less retrieval effort on that
one question than they would have a moment ago. Variance still matters
just as much as it did before H0 moved to 0.75: two histories with almost
the same mean can land on opposite sides of the boundary depending on how
spread out they are. A high-variance mix of clean successes and outright
failures (19 subqueries at 1.0, 11 at 0.0, `mean=0.633`) does *not* reject
(`t=-1.30`, short of `critical_t=1.699`) -- the wide spread a mix of
extremes produces leaves room for that mean to plausibly just be noise
around a true mean at or above 0.75. A *lower-variance* decline at a
similar mean (21 subqueries at 0.5, 9 at 1.0, `mean=0.65`, no outright
zeros at all) correctly rejects (`t=-2.35`), precisely because a smaller
spread makes the same-sized deviation more significant, not less. A
severe, consistent decline (every subquery scoring 0.5, `mean=0.5`)
correctly rejects outright. See §4e for how much this behavior depends on
the sample size, and for why the floor is now 15 rather than a full 30
points.

### 4c. The repeated-need override exists specifically to counteract 4b

§4b's consequence -- a lone struggling task in an excellent session no
longer automatically getting extra effort -- is the statistically correct
call, but not always the customer-satisfaction-optimal one: a customer
who is visibly re-asking or rephrasing the same request is telling the
bot directly that this particular moment matters to them, independent of
what a 30-turn statistical average says about the session generally.
`has_repeated_need_this_turn` (triage's own `repetitive_issue_detected`,
computed before `node_03c` runs) unlocks extra effort unconditionally
when true, `or`-ed together with the hypothesis test's own conclusion --
never a replacement for the test, an independent, always-available second
path to the same outcome. This is a deliberate satisfaction-driven
decision, not a statistical one: it trades a small amount of the cost
control §4b was built to restore for directly rewarding the customer's
own clearest possible signal that they need more help right now.

### 4d. From a z-approximation to an exact t-test

The test originally used a normal (z) approximation to the sampling
distribution of the mean, gated by an artificially high minimum sample
size (the full `HYPOTHESIS_TEST_SAMPLE_SIZE`, 30) specifically because
that approximation is only trustworthy once n is in that neighborhood --
running it on a smaller sample would apply the same `1.645` critical
value the Central Limit Theorem only really justifies near n=30,
understating how wide the true sampling distribution of the mean is that
far from it, and skewing toward rejecting H0 too easily for a customer
who simply didn't have much history yet. That meant the test contributed
nothing at all for a customer's first ~30 scored turns, however that
history happened to look, since it was suspended outright below the full
sample size.

The correct fix isn't a smaller z critical value or an ad hoc small-sample
adjustment -- it's the right *test*. The population standard deviation is
never known here, only estimated from the sample, and a one-sample
**t-test** is the textbook-correct tool for exactly that situation, valid
at any sample size, not only once it's large. The t-distribution's own
critical value is wider at low degrees of freedom (e.g. `2.132` at `df=4`
vs `1.645` at the asymptote) and narrows as `df` grows, converging to the
same z-value by `df≈30` -- so switching to it is a strict generalization
of the old behavior, not a separate small-n special case: a full 30-turn
sample gets (to three decimal places) the same critical value as before,
while a partial sample now gets an honestly wider, appropriately
conservative one instead of being ignored outright.

At the time this was written, the minimum sample size dropped back to a
small floor (4 turns), and the test could detect and act on a genuine,
sustained quality problem for a newer customer within their first handful
of turns rather than only once they'd accumulated a full 30 -- verified
then with continuous-looking illustrative means (`0.3`, `0.9`) that didn't
reflect the sample's real, discrete 1.0/0.5/0.0 composition. **§4e below
supersedes this specific numeric conclusion**: once the sample was
switched to subquery-level scores and the population target raised to
0.9, that same small floor turned out to badly miscalibrate the test in
practice. The general argument in this section -- that a t-test is the
textbook-correct tool at any sample size when the population variance is
unknown -- still stands; it's the *practical* claim that a floor of 4 is
safe that doesn't survive contact with genuinely discrete data.

## 4e. Discreteness, H0's distance from the ceiling, and picking a floor that actually fits

Moving the sample to subquery-level scores (§4) makes every point in the
sample one of exactly three values: 1.0, 0.5, or 0.0. That's a much more
discrete distribution than a turn-level average of several such scores
was, and discreteness is exactly what a t-test's normal-theory foundation
assumes away. How close `CONFIDENCE_TEST_POPULATION_MEAN_H0` sits to the
score scale's 1.0 ceiling controls how badly this bites: the closer to the
ceiling, the more the underlying distribution is forced to be
left-skewed under H0 (there's far more room to fall below the target than
above it), which a small sample's variance estimate captures very
unreliably.

**First attempt: H0=0.9.** Checked by Monte Carlo simulation (200k-500k
replications per data point), drawing subquery scores from several
plausible distributions that all have population mean exactly 0.9 (the H0
boundary), and measuring how often the test falsely rejected H0 at various
sample sizes against the nominal `alpha=0.05`:

| n | pure 0/1, no partial credit | mild partial credit | mostly partial, rarely zero | heavier tail |
|---|---|---|---|---|
| 4 | 66.0% | 52.3% | 41.1% | 60.2% |
| 10 | 36.1% | 20.4% | 11.3% | 28.6% |
| 20 | 13.3% | 5.3% | 4.4% | 9.0% |
| 30 | 5.0% | 2.3% | 2.7% | 3.5% |

A well-calibrated test should show close to 5% in every cell. Only once
the sample reached the full 30-point window did every scenario land at or
below nominal. But a live trace surfaced a second, independent problem
with 0.9 that no amount of recalibrating the floor could fix: a real
customer's session showed `mean≈0.68-0.75`, `std_dev≈0.43` turn after
turn -- normal, healthy operation for this system (see §2's historical
funnel: rounds 1-2 alone resolve roughly two-thirds of tasks outright, and
93-94% of every eventual success) -- and against H0=0.9 that mean
*always* rejected (the break-even point works out to roughly
`0.9 - 1.699 × (0.43/√30) ≈ 0.77`). The test wasn't discriminating
"typical" from "degraded" for this workload at all -- it was rejecting H0
almost unconditionally the moment any task took more than one round,
making rounds 3-4 the de facto default rather than a response to genuine
decline.

**Corrected: H0=0.75**, set at this system's actual typical operating
point (§2) rather than an aspirational round number. Re-running the same
Monte Carlo check at the new boundary (population mean exactly 0.75):

| n | pure 0/1, no partial credit | mild partial credit | mostly partial, no zero | heavier tail |
|---|---|---|---|---|
| 4 | 32.0% | 19.4% | 12.6% | 24.8% |
| 10 | 7.6% | 3.6% | 5.6% | 5.1% |
| 15 | 3.1% | 2.4% | 5.9% | 2.5% |
| 20 | 4.4% | 2.9% | 5.8% | 2.8% |
| 30 | 2.2% | 3.4% | 4.9% | 3.3% |

Being further from the 1.0 ceiling than 0.9 was, calibration recovers much
faster with n: every scenario is within roughly 1.2-1.5x of nominal by
n=15, rather than needing the full 30. Re-verified directly against
`_reject_confidence_null_hypothesis` using this real customer's own
observed statistics: at H0=0.75, `mean≈0.68` now correctly does **not**
reject (`t=-1.54`, short of `critical_t=1.699`) -- a customer performing
at their system-typical level no longer triggers extra effort just for
being who they normally are -- while a genuinely degraded `mean=0.5`
still correctly rejects (`t=-3.30`). The test is discriminating again.

Practical conclusion: `MIN_SCORED_SUBQUERIES_FOR_CONFIDENCE_TRUST = 15`,
not the full 30-point window `HYPOTHESIS_TEST_SAMPLE_SIZE` requires --
H0's distance from the ceiling is what should set this floor, and it's
re-derived by Monte Carlo (not assumed) whenever H0 changes. Below the
floor, only `has_repeated_need_this_turn` (§4c) can unlock extra effort
past the unconditional rounds-1-2 floor.

(As a side note, both tables have non-monotonic entries -- e.g. "mostly
partial, no zero" at n=15 checking slightly worse than at n=10 and n=20 in
the H0=0.75 table -- an expected artifact of testing discrete-valued data
with a continuous distribution, not a bug: the achieved significance
level of a discrete test isn't a smooth function of n the way it would be
for genuinely continuous data.)

**When does this floor need re-deriving?** It's a function of exactly
three things: `CONFIDENCE_TEST_POPULATION_MEAN_H0` (how close the target
sits to the score scale's 1.0 ceiling -- the dominant factor, per the two
tables above), `CONFIDENCE_TEST_ALPHA` (the significance level, which
sets how large the rejection region is), and the fixed 1.0/0.5/0.0
scoring tiers `_score_subquery` already uses everywhere else in this
codebase (changing those tiers changes the discreteness the Monte Carlo
check is measuring in the first place). `HYPOTHESIS_TEST_SAMPLE_SIZE`
(the window cap) is a fourth, less central factor -- the tables above only
checked up to n=30, so raising the window materially would also want
re-checking. As long as none of these move, the floor derived here stays
valid indefinitely; it is a one-time cost per parameter choice, not a
recurring maintenance burden -- it only needs re-running (the same
Monte Carlo check, against whatever the new parameters are) if `H0` or
`alpha` are revisited, or if the window or the scoring tiers themselves
change.

## 5. `is_low_confidence` / "Thinking harder"

Tracks whether extra effort is **actually being spent right now**: both
that the hypothesis test rejected H0 this round, *and* that there is an
actual pending (non-`SUFFICIENT`) task this round for that finding to
apply to. A round where every task already resolved `SUFFICIENT` has
nothing left to extend, regardless of what the test concludes from
history — showing "Thinking harder" there would be showing effort that
isn't actually happening.

## 6. Live confidence: streamed to the frontend, logged for future analysis

`live_task_confidence` (`AgentState`) carries the hypothesis test's own
**sample mean** — up to 30 recent subquery scores, historical ones plus
this turn's own volatile, still-updating scores occupying the newest
slots — out of `node_03c` every time it runs, once per retry round. It
deliberately is *not* this turn's own grade in isolation: displaying that
raw number lets a single hard question read as "the bot has no confidence
at all" the moment it fails, even when the rest of the recent window is
healthy, and it would let the number the customer watches drift apart
from the number the hypothesis test itself reasons about when deciding
whether to extend. Because this turn's own subquery scores keep changing
round over round as they resolve, and they're re-folded into the sample
mean each time `node_03c` runs, the customer sees the aggregate move live
as retrieval progresses — not jump between "this turn only" and "history"
but a single number that responds smoothly to what's happening right now.
Falls back to this turn's own grade only when the sample is empty (mean is
`None`, e.g. the very first scored turn of a brand-new customer with
nothing else to average against). Below `MIN_SCORED_SUBQUERIES_FOR_CONFIDENCE_TRUST`
(15 -- §4e) the test itself doesn't run and never rejects H0 on its own,
but the sample mean is still computed and still displayed — a
thin-history customer sees a real, moving number, they just don't get an
extend-on-decline decision from it yet.

`api_gateway.py`'s SSE progress stream reads it off each node's state
update (`taskConfidence` in the JSON payload, deliberately *not* sticky
like `lowConfidenceMode` — it only updates when `node_03c` actually
returns a fresh value, otherwise holds the last one) and the frontend
(`taskConfidence` in `App.jsx`) renders it as a live percentage above the
loading bubble, alongside (not replacing) the "Thinking harder" label.

Every individual task's dynamic confidence is logged per round
(`task.dynamic_confidence`: `task_id`, `round`, `status`, `confidence`).
The turn-level grade and the full hypothesis-test outcome are logged
**every round, unconditionally** (`task.turn_confidence.computed`:
`provisional_turn_grade`, `reject_null_hypothesis`, plus the test's own
diagnostics — `n`, `df`, `mean`, `std_dev`, `t`, `critical_t`, `alpha`, or
`reason: "insufficient_sample"` when the test didn't run) — not only at
the terminal boundary (`evidence.boundary.terminal_unresolved`'s
`turn_confidence_if_stopped`, which still logs the same grade
specifically for a task that stops). The unconditional version was added
after tracing a case where a task that kept extending across several
rounds left no record anywhere of what had actually been shown at each
of those rounds. Both are durable, directly queryable records for future
analysis, unlike the very first (historical-only) version of this
mechanism, which had to be reconstructed indirectly from terminal task
events and was shown to be error-prone doing so (see the
historical-validation trace this document's §2 data came from).

## 7. Storage: a longer window shared with, but read differently by, node_08

`turn_escalation_history` is now stored at `HYPOTHESIS_TEST_SAMPLE_SIZE`
(30) turns, not the shorter `MAX_ESCALATION_HISTORY_TURNS` (10) it used
to be capped at — the hypothesis test needs the longer window. `node_08`
(`SOFT_ESCALATION_v1.0.md`) still wants only its own original, shorter
recency window for its own signals (a confidence dip or a repeated need
from 20+ turns ago is no longer an active pattern for *its* purposes) —
it explicitly slices `history[-MAX_ESCALATION_HISTORY_TURNS:]` before
computing its own rolling confidence and repeated-need count, rather than
reading the full stored 30. Both windows are views over the same
underlying stored list; nothing is duplicated.

## 8. `node_08`'s own confidence rule had a separate, unrelated thin-history bug

`node_08`'s own `confidence_triggered` rule (the "Talk to Agent" chip's
low-confidence signal) uses `_rolling_bot_confidence` directly (a plain
average, not the hypothesis test — a session-level judgment like "should
a human be offered" doesn't need the same statistical rigor as a
per-round cost/quality trade-off), and had never had a minimum-sample
guard: a single bad first turn (score 0.0) made rolling confidence 0.0
with n=1, which satisfies `< 0.5`, offering a human handoff after one
failed exchange. Fixed by requiring
`scored_turn_count >= MIN_SCORED_TURNS_FOR_CONFIDENCE_TRUST` (4) before
that rule can fire, logged via a `scored_turn_count` field on
`escalation.soft.triggered`.

## 8b. A round-1 ambiguity read is provisional, not a verdict — the ladder still runs to its own boundary

A subquery whose round-1 sufficiency audit sets `likely_ambiguous_request:
true` (with a full `unresolved_term`/`clarification_reason`/
`clarification_guesses`) is *not* short-circuited into an immediate
clarification, even though the judgment already looks complete. This is
deliberate, not an oversight: `INTENT_EXPANSION`/`PRODUCT_EXPANSION`/
`ANGLE_EXPANSION` each retry with a genuinely different search strategy
specifically because a plain first-round search missing the right content
does not mean the content isn't in the KB — a later round's reformulation
can still rescue it. Treating round 1's "likely ambiguous" as final would
mean never giving those later strategies the chance they exist for.

The retry-continuation check itself (`should_retry`, `nodes.py`) reflects
this on purpose — it depends only on `attempt_count` versus
`NORMAL_EFFORT_RAG_ATTEMPTS`/`MAX_RAG_ATTEMPTS` and the hypothesis test
above; it does not read `likely_ambiguous_request` at all. The actual
clarification only gets authored once the loop reaches its own boundary
(floor met with no statistical case to extend, or the ceiling), inside the
`if not should_retry:` branch — and at that point it uses *that round's*
own fresh audit output (`decision["likely_ambiguous_request"]` etc.,
appended new every round from `audit`), never a stale carry-over from
round 1. So when a turn ends in a clarification after all four rounds ran,
it's because every search strategy independently reached the same
conclusion, confirmed as of the final round — not because the system
ignored three rounds of contrary evidence to act on the first one. See
`DECLINE_CLARIFICATION_DESIGN_v1.0.md` §4a for who authors this
clarification (`node_03c`, as one of the four originators) and the
standard contract shape it's written into.

**Superseded by §8c below**: the named stages this section describes
(`INTENT_EXPANSION`/`PRODUCT_EXPANSION`/`ANGLE_EXPANSION` as three separate
sequential rounds) no longer exist as such under Extra Effort v2.0 — their
mechanisms survive, reorganized into two waves. The underlying principle
this section documents (a round's own read is provisional until the ladder
reaches a genuine boundary, using that boundary round's own fresh
judgment, never a stale earlier one) still holds, just against the new
two-wave boundary instead of a four-round one.

## 8c. Extra Effort v2.0 — two waves instead of four rounds

Two problems with the four-round ladder motivated this redesign, both
found by mining real retry outcomes from `system.jsonl` rather than
assumed:

1. **`INTENT_EXPANSION`/`PRODUCT_EXPANSION` almost never rescued a failed
   round** — 1 rescue out of 84 real task lifecycles sampled. Widening the
   metadata *filter* without also widening the *query text* doesn't
   actually help ranking find newly-eligible content; a wider net mostly
   just means the same, still-unmatched query runs against more documents.
2. **`node_02e` already authors three distinct representations**
   (`procedural`/`policy`/`conceptual`) on every `SearchTask` from round 1
   — but retrieval never used them until round 4 (`ANGLE_GROUP_RRF` was
   gated to `retry_count >= 3`). Rounds 1 through 3 all searched with a
   single `direct_query` string; three rounds were spent varying the
   *filter* while the *query representation* never changed at all.

### Stage 1 (implemented, verified): Wave 1 fuses all four representations, from the first attempt

`execute_multi_angle_search` (`rag_engine.py`) now takes a `direct_query`
parameter and folds it into the *same* RRF fusion as a fourth angle of the
base group, rather than as a separate `DIRECT` retrieval mode.
`_retrieval_mode_for_task` (`nodes.py`) now unconditionally returns
`"ANGLE_GROUP_RRF"` — the standalone `DIRECT` path, and the now-redundant
`execute_direct_search`/`_execute_direct_search_with_verified_session_evidence`
methods it alone used, are removed.

**Verified-session-parent boosting (Search Strategy v2.8) is preserved,
not lost** — it used to be a fusion exclusive to `execute_direct_search`
(re-query each already-approved-this-session parent against the direct
query, fold its rank in as one more RRF list). It's now a
`verified_session_parent_ids` parameter of `execute_multi_angle_search`
itself: the same re-query happens against the same `direct_query` text,
and its hits become one more contributing rank-list (`"VerifiedSession"`)
in the identical group-RRF sum every other angle already contributes to —
confirmed directly (a verified parent's RRF contribution added a second,
independent point contribution and correctly moved it to rank 1 without
disturbing the other three candidates' relative order).

Live-verified: a real turn ("how do I open a savings account") now shows
`retrieval_mode: "ANGLE_GROUP_RRF"` and 4 vector searches (`Procedural`,
`Policy`, `Conceptual`, `Direct`) on its very first attempt, resolving in
one round where it previously would have spent three rounds testing only
the direct-query representation before ever trying the other three.

### Stage 2 (implemented, verified): Wave 2 — intent expansion + scope expansion, two territories, one audit

Only fires when Wave 1's audit says insufficient — intent is widened only
after the narrow scope has been genuinely tested and found wanting, never
pre-emptively (an earlier version of this design, pooling narrow- and
full-intent results unconditionally, was caught and rejected mid-design:
it would have silently retired intent as a real filter, since top-K
retrieval lets noise from other intents crowd out a correct narrow match).
Two territories, never RRF-fused together since they carry different
honest ceilings on what status they can support:

- **Territory A (intent expansion)**: same product, full intent scope,
  same four representations Wave 1 already built — no new LLM call.
  Candidates here can honestly reach `SUFFICIENT`.
- **Territory B (scope expansion, Type 1 or Type 2)**: a second
  `SearchTask` under the same `subquery_id`, reusing the existing
  "one subquery, multiple search_tasks" combination already done in
  `_finalize_rag_attempts`. Type 2 (evidence-grounded: Wave 1's audit
  flags a specific unapproved candidate worth pivoting toward) takes
  priority over Type 1 (Matrix-and-context reasoning, used only when
  nothing at all connects) — one committed query, not three angles, since
  this is a single commitment, not a hedge across facets of an
  already-confirmed topic. Evidence found here must never be graded above
  `PARTIALLY_SUFFICIENT`, framed honestly as related-but-not-exact.

Also in scope for Stage 2: `node_02e`/`TaskSufficiencyItem` both gain a
reasoning field declared *before* the fields it should inform (the same
fix already applied and verified for `TaskSufficiencyItem.reason`/
node_06's audit earlier this session, extended to the fields that
construct the retrieval query itself — `SearchTask.mapping_reason` and
`TaskSufficiencyItem.scope_expansion_lead_reason`), and the
hypothesis-test machinery below is repurposed from "is another sequential
round worth it" (up to four rounds) to the simpler "is Wave 2 worth
attempting at all" (one binary decision after Wave 1).

**Critical bug found and fixed during live verification: unbounded
recursion via Territory B self-retry.** A live end-to-end test ran past a
90-second client timeout without terminating; log analysis showed
`task_1` → `task_1_scope` → `task_1_scope_scope` → `task_1_scope_scope_scope`
→ ... — each companion, being a brand-new `task_id` with its own fresh
`retry_counts` starting at 0, was structurally eligible for the general
`should_retry` formula's own Wave 2 extension, so an insufficient
companion kept spawning a companion of its own. Fixed with an explicit
`is_scope_expansion_task` guard in `node_03c` forcing `should_retry =
False` unconditionally whenever a task's `search_territory ==
"adjacent_scope"` — a Territory B companion is a single committed guess,
never a hedge, and gets exactly one evaluation, always terminal.
Re-verified live afterward: exactly 2 RAG rounds, clean termination.

A related bug surfaced alongside it: if Territory B's own audit set
`unresolved_term` (its `customer_component` is a copy of the original
task's), it could author a *second* `clarification_tasks` entry for the
same `subquery_id`, violating the "at most one clarification per
subquery_id" invariant the rest of the design relies on. Fixed by
force-clearing `likely_ambiguous_request`/`unresolved_term`/
`clarification_reason`/`clarification_guesses` for any
`is_scope_expansion_task` — clarification-authoring for a subquery
belongs exclusively to its in_scope (Territory A) task.

**Further correctness gaps found and fixed after the recursion fix, before
declaring the implementation complete:**

- **Territory ceiling enforced in code, not just in the prompt** — this
  codebase's established principle (seen elsewhere, e.g. clarification
  guesses) that a prompt instruction alone isn't a reliable guarantee for
  something this important. `node_03c` now deterministically downgrades a
  model-returned `SUFFICIENT` to `PARTIALLY_SUFFICIENT` for any
  `search_territory == "adjacent_scope"` task, before the `SUFFICIENT`
  short-circuit branch runs.
- **Subquery-level score aggregation** — `rag_task_scores_by_subquery_id`
  was a dict keyed by `subquery_id`, blindly overwritten per task result
  processed; since a subquery can now own two task_ids (Territory A + B)
  both contributing a score to the same key, whichever was processed last
  in dict-iteration order silently discarded the other's contribution.
  Fixed to take `max()` of both territories' scores, mirroring
  `_finalize_rag_attempts`' own "best of both territories" combination.
- **Evidence-manifest and audit territory tagging** — `_response_evidence_
  manifest` read `task_results` per task_id with no signal about which one
  is Territory A (literal answer) vs. Territory B (related-only),
  breaking the redesign's core honesty guarantee downstream. Fixed by
  threading a `search_territory` tag through the manifest into both
  `_generate_response_blocks`'s evidence inputs (new prompt instruction:
  introduce `adjacent_scope` evidence explicitly as related/additional
  information, never as a direct answer, and never blend it into the same
  sentence as an in_scope answer without preserving that distinction) and
  `_normal_outbound_grounding_context`'s `approved_by_task` entries feeding
  node_06 (new "Scope-Expansion Honesty Rule": an `adjacent_scope` claim
  presented as a direct answer, or blended into an in_scope answer without
  the distinction preserved, is now a `wrong_task_evidence` grounding
  violation — the underlying fact can be correct and still fail the audit
  if the framing erases which territory it came from).
- **No-info marker, Territory B side** — `_rag_gap_still_matters` (which
  suppresses a `CLARIFICATION_REQUIRED` task's own "no information
  available" marker when something else already answers its subquery) only
  ever checked *other tools* (`_resolved_by_other_tool` deliberately
  excludes `tool_name == "rag"`, to avoid a self-reference against RAG's
  own still-pending attempt). With two task_ids per subquery now possible,
  a failed Territory B companion could still emit its own no-info marker
  even when its Territory A sibling (same `subquery_id`) had already
  resolved `SUFFICIENT`/`PARTIALLY_SUFFICIENT` — telling the customer no
  information exists for a subquery the response was about to answer in
  full, directly above it.

  **First fix attempt was itself wrong, caught by tracing a real live
  turn**: suppressing based on "does *any* sibling task_id already have
  real evidence" looked right in isolation, but a real "i want to buy a
  car" turn exposed the actual failure mode — Territory A (`task_1`,
  `Personal_Loan`) genuinely found nothing (the FAQ corpus has no auto-
  financing content), while Territory B (`task_1_scope`) found only
  adjacent Savings Account content and reached `PARTIALLY_SUFFICIENT`.
  The sibling-success check suppressed `task_1`'s own genuine "no
  information for this literal request" marker merely because its
  *adjacent-scope* sibling found unrelated content — exactly the honesty
  claim this whole redesign exists to protect, left standing on the
  generator's own prompt adherence alone instead of a deterministic
  marker. **Corrected fix**: key the suppression off the *failing* task's
  own `search_territory`, not its sibling's status — a failed Territory B
  companion (`search_territory == "adjacent_scope"`) always suppresses its
  own marker unconditionally (the customer never asked about that
  territory, so silence is correct regardless of how the sibling fared);
  an in_scope task's own failure marker is never suppressed by a sibling's
  outcome. This also cleanly avoids a duplicate marker when both
  territories fail (only the in_scope task's marker survives), and passed
  direct verification against all three cases (Territory A fails/Territory
  B partial, Territory A succeeds/Territory B fails, both fail).
- **The Territory B bridge reason was computed but never surfaced** — a
  live "buy a car" turn resolved exactly as designed (Territory A found
  nothing, Territory B pivoted to a Savings Account via Type 1 reasoning:
  *"buying a car often means saving for a downpayment"*), and the audit
  correctly enforced the honesty framing -- but the actual WHY behind that
  pivot, `SearchTask.mapping_reason`, was never threaded past `node_02e`.
  The generator and node_06's auditor only ever saw the tag
  `search_territory: "adjacent_scope"` plus a generic "introduce this as
  related information" instruction, with no access to the specific
  reasoned connection -- forcing the model to either invent its own
  weaker bridge or drop the evidence in with no connecting sentence at
  all, reading as a non sequitur ("here's a Savings Account" with no
  stated reason why, right after "I can't help with car financing").
  Fixed by threading `mapping_reason` through the same three points
  `search_territory` already reaches: `_response_evidence_manifest`,
  `_generate_response_blocks`'s `evidence_inputs` (with a prompt
  instruction to use it as the basis for a specific, hedged bridging
  sentence -- "you may want to..." / "since you might be...", never
  asserted as bank fact -- and to open with the paired no-info statement
  first when the same literal request also failed, so the two read as one
  coherent reply instead of two disconnected statements), and
  `_normal_outbound_grounding_context`'s `approved_by_task` (with a new
  Scope-Expansion Honesty Rule clause treating `mapping_reason` as the
  only permitted basis for that bridge sentence, rejecting one that
  states a stronger or different connection, or drops the hedge, as an
  `unsupported_bridge_inference` violation).

## 8d. Extra Effort v2.1 — direct-adopt, and Territory A becomes free

Tracing a real "buy a car" turn under v2.0 surfaced a real flaw in Territory
B's evidence-grounded case ("Type 2"): the auditor flagged `faq_75ed6e4d`
("discusses loan approval timeframes... a logical adjacent step") as a
genuinely connected candidate — then Territory B invented a brand-new query
and re-retrieved, and `faq_75ed6e4d` itself never reappeared in the new
results. The auditor had already reviewed that candidate's real content and
judged it connected; re-querying can only drift further from it, never
closer. This became the motivating question for v2.1: **when the answer is
already in hand from an earlier round, why search for it again?**

### Direct-adopt replaces Type 2 — free, always-on, lives in `node_03c`

The evidence-grounded case is no longer "Territory B, Type 2" at all. The
moment `node_03c`'s own audit flags a genuine `scope_expansion_lead_parent_id`/
`scope_expansion_lead_reason`, it is adopted **immediately and
deterministically** as `f"{task_id}_scope"`, `PARTIALLY_SUFFICIENT`,
`adjacent_scope`, `approved_parent_ids=[lead_parent_id]` — no new query, no
retrieval, no LLM call, and critically **not gated by the extra-effort
hypothesis test** (`extend_if_optional`). It fires even on a turn where
extra effort never triggers at all (a real, live-traced scenario: `n=1,
insufficient_sample` → `extend_if_optional=False` → the source task
terminates in the very same round the lead was flagged).

This must live inside `node_03c` itself, not `node_02e`'s retry pass —
that pass only ever runs for a task whose status is `"RETRY"` this round,
and a task that terminates immediately would never reach it again. Two
guards, neither of which existed before this revision, both necessary:

- **Recursion safety**: the lead is discarded if the *auditing* task's own
  `search_territory` is already `"adjacent_scope"` — without this, an
  adjacent_scope companion's own audit round flagging a lead would
  synchronously spawn a `..._scope_scope` companion, reintroducing the
  exact unbounded-recursion class of bug `is_scope_expansion_task` was
  built to stop (see §8c), just via a code path that guard doesn't cover,
  since direct-adopt acts on the lead before that guard ever applies.
- **First-lead-wins**: a companion is only created if
  `f"{task_id}_scope"` doesn't already exist. A task can be audited across
  two attempts (Wave 1, then Territory A) before terminating, and a later
  round's audit could flag a different candidate — without this guard a
  later lead would silently overwrite an already-terminal, already-scored
  companion, or produce two `search_tasks` entries sharing one `task_id`.

`node_03c` becomes a co-writer of `search_tasks` for the first time (only
`node_02e` wrote it before). `state.py`'s `search_tasks` has no LangGraph
reducer (plain replace), so the updated list — existing tasks plus any new
direct-adopt stubs — is built once and used for **both** the final return
**and** the `_finalize_rag_attempts` call earlier in the same function.
This second use matters: `_finalize_rag_attempts` reads whatever
`search_tasks` value it's handed, and if a task terminates in the very same
round its companion is created (fully possible, per the `n=1` scenario
above), passing the raw, pre-round `state` would silently miss the
just-created companion, finalizing that subquery's `rag` tool_attempt as
`"unavailable"` even though real, direct-adopted evidence exists moments
later in the same return.

The stub deliberately never carries `target_commercial_product`/
`target_intent` — there's no cheap way to look up the flagged *parent's*
own product/intent (hydrated parents carry only `parent_id`+`content`),
and copying the *source task's* own product/intent would let a future
turn's `_verified_session_parent_ids_for_task` (which matches by
product+intent overlap) resurface this never-fully-vetted, adjacent-scope
parent as if it were verified in-scope evidence — exactly the cross-scope
leakage the territory ceiling exists to prevent. Left empty,
`_normalized_product_tags(None)` degrades to `[]` everywhere it's read.

### Territory A gains a real capability: hypothesizing the *filter*, not just the intent, was wrong

Previously Territory A only widened *intent* within the *same* product
(`_expand_intent_to_full`). It now also asks whether the *product*
classification itself might be wrong — e.g. the answer might live under a
general app feature (`Horizon_APP`) rather than the product node_02e
originally picked — while keeping the **same query representation**
(`procedural`/`policy`/`conceptual`/`direct_query`, unchanged) and only
widening the *filter*, since this attempt tests "was the filter wrong,"
never "was the query text wrong."

Critically, which alternate products are plausible is reasoned **once, at
fresh/Wave-1 formulation time** (`SearchTask.alternate_product_reason`/
`alternate_products`, capped at 2, code-enforced via `[:2]` at consumption
time rather than trusting the schema's own wording alone), never
re-derived at retry time — it's a Matrix/context question answerable from
the customer's request alone ("what are my other plausible
classifications"), not one that benefits from having already seen Wave 1's
failure. This is the same "pre-generate now, use later or not" pattern
already used for `procedural`/`policy`/`conceptual`. The prompt instruction
strongly prefers a cross-cutting **functional** product (`Horizon_APP`,
`Rewards_Program`) over another genuine financial-instrument product as
the alternate — a request genuinely spanning two financial products should
already be reflected in the primary classification or a separate
component, never deferred to this fallback.

This makes Territory A's retry-time widen **fully deterministic again,
with zero LLM call** — confirmed live: a real "buy a car" retry showed
exactly two `TASK-LEVEL SUFFICIENCY` audit calls (Wave 1, then Territory
A's own audit) and **no** retry-mode prompt logged anywhere in between.
`_valid_product_intent_scope_pairs`/`_normalized_product_tags` already
supported a `target_commercial_product` list unioning each product's own
valid intents (confirmed by reading the code, not assumed) — so widening
into a second product required zero retrieval-engine changes, only
appending the pre-computed alternates before calling
`_expand_intent_to_full`.

### Three named effort tiers, table-driven and explicitly made configurable

Made configurable on request, since the boundary between "unconditional"
and "statistically gated" is expected to move as production data comes in:

```python
NORMAL_EFFORT_ATTEMPT = 1    # Wave 1
EXTRA_EFFORT_ATTEMPT = 2     # Territory A -- also a task_id's own hard ceiling
MAXIMUM_EFFORT_ATTEMPT = 3   # Territory B -- a gate-table key only, never a real attempt count
MAX_RAG_ATTEMPTS = EXTRA_EFFORT_ATTEMPT
EFFORT_TIER_GATE = {
    NORMAL_EFFORT_ATTEMPT: "always",
    EXTRA_EFFORT_ATTEMPT: "always",
    MAXIMUM_EFFORT_ATTEMPT: "statistical",
}
```

Moving a tier's gate (e.g. if production data someday shows Territory A
needs gating too) or adding a fourth tier is an edit to this table alone,
never a restructuring of `should_retry`. A subtlety worth recording:
`MAX_RAG_ATTEMPTS` is **not** `MAXIMUM_EFFORT_ATTEMPT`. Territory B is
never a further attempt on the *same* task_id — it always spawns a
brand-new, separately-tracked companion task_id, constructed **alongside**
Territory A in the same `node_02e` retry pass, not a later, sequential
round (a task that terminates after Territory A has no live `"RETRY"`
status left for a genuinely later round to hang off of — this was the
first design tried and found unworkable: it would have required a second,
parallel "this task still needs a companion" signal threaded through
`preflight_status`/`_finalize_rag_attempts`/`node_03b`'s retrieval filter,
all to route around a task that had already, correctly, gone terminal).
So a single task_id's own ceiling (`MAX_RAG_ATTEMPTS`) stops at
`EXTRA_EFFORT_ATTEMPT` — Territory B's own gate is checked directly via
`_attempt_unlocked(MAXIMUM_EFFORT_ATTEMPT, extend_if_optional)`, a
turn-level question, never compared against any task's own attempt count.
`node_03c` returns this as `state["territory_b_unlocked"]`, which
`node_02e`'s retry section reads to decide whether to *also* construct a
Territory B companion for a retrying task this same round.

Live-verified: a real "buy a car" turn with a real 30-turn historical
sample (`reject_null_hypothesis=True`) constructed `task_1` (Territory A,
widened intent) and `task_1_scope` (Territory B, `Horizon_APP`/"manage
savings for a downpayment") **in the same round** (`task.formulated`
showing both task_ids together), both retrieved and audited together, and
— since both ended up empty this run — the sibling no-info-suppression fix
from earlier this session correctly produced exactly one no-info marker,
not two.

### `node_02e`'s retry section, restructured around what each task still needs

Territory B's LLM call (renamed `TerritoryBPlan`/`TerritoryBCompanion`,
schema narrowed to drop the now-dead "Type 1 vs Type 2" framing) is now
skipped entirely whenever no retrying task actually needs one this round —
`territory_b_needed_task_ids` (unlocked AND no existing companion from
direct-adopt) is empty in most rounds (whenever `extend_if_optional` is
`False`, or whenever direct-adopt already covered every failing task for
free). The `feedback[task_id]` payload dropped
`scope_expansion_lead_parent_id`/`scope_expansion_lead_reason` entirely —
direct-adopt now consumes the lead synchronously in `node_03c`, so nothing
downstream needs it forwarded anymore.

## 8e. Sufficiency reasoning gains a mechanism-bridge test, and a boundary fix upstream of the whole ladder

Tracing "can your personal loan be used to buy car?" surfaced two separate
findings, in the same session, on either side of `node_03c`.

### The auditor couldn't reason across a produced resource's ordinary nature

The FAQ corpus's only `Personal_Loan`-tagged content describes an
"Instant Loan" that converts a customer's credit card limit into cash —
confirmed by direct query against `knowledge_parents`, no document
anywhere in the 708-row corpus uses "various purposes"/"personal
needs"/similar generic-use framing, and none mention "car" or "vehicle"
under any product. `node_03c` was rejecting this evidence outright as
INSUFFICIENT, reasoning only about whether it *literally* named car
financing — never asking whether the evidence described an output (cash)
whose own ordinary, real-world nature would satisfy the request via one
bounded logical step, the same "Inferential Grounding / Bridge Check"
principle `GROUNDEDNESS_v2.0.md` already licenses at response-generation
and audit time. Once evidence is *approved*, that existing machinery
already knows how to use it honestly — the actual gap was narrower than
first assumed: `node_03c` needed permission to approve this kind of
evidence as `PARTIALLY_SUFFICIENT` in the first place, not a new pipeline
stage.

Added a **MECHANISM-BRIDGE REASONING** rule to `node_03c`'s prompt,
deliberately generalized (never mentions loans, cash, or cars — those are
only the illustrative example), worked through in explicit progressive
steps: (1) re-read the request one level more abstractly than its literal
wording; (2) check whether a parent already covering the customer's own
named product describes a resource/output/capability whose *ordinary,
common-knowledge nature* — not a nature the parent must spell out
explicitly — would satisfy that abstract outcome (plain cash, once
disbursed with no stated restriction, is fungible by the ordinary meaning
of "cash" itself, the same way an FAQ is never expected to state "you may
spend your salary on anything" before salary counts as unrestricted); (3)
check every parent for an actual *stated* restriction before relying on
the bridge — absence of a restriction is the default state, not grounds
for suspicion; (4) approve as `PARTIALLY_SUFFICIENT` (never `SUFFICIENT`)
with the bridge and any restriction stated explicitly in `reason`. A
product-identity note was added alongside it: a parent's own internal
marketing name (e.g. an offering called "Instant Loan") is never grounds
to doubt it concerns the customer's named product merely because the
names differ — that classification was already settled by retrieval
metadata before the parent ever reaches this prompt, and re-litigating it
here is a different failure mode than the category-adjacency rule this
sits beside (which correctly blocks a *genuinely different* product from
being treated as material merely for shared vocabulary or menu
placement).

**Honest status on this one**: live-verified to measurably change the
model's own reasoning (its `reason` text now explicitly engages with
"unrestricted"/"general-purpose" framing it never raised before this rule
existed) but, after several iterations narrowing exactly where it still
resists, the specific worked example (Instant Loan → car financing) did
not fully resolve — the model kept reverting to "is this the right kind of
product" instead of "does this product's output satisfy the need," despite
being asked for the latter. Deliberately stopped iterating rather than
keep patching the prompt with case-specific caveats in response to each
new observed failure (see the boundary fix below for why that pattern was
explicitly rejected this session) — the rule is a real, principled,
already-behavior-changing addition on its own terms; this exact edge case
may need a stronger model or a different lever, not more prose.

### The actual root cause was one node upstream of all of this

Investigating further, the real source of the instability wasn't
`node_03c` at all: `node_02d` (triage)'s own "resolve the TRUE REQUEST"
step was silently rewriting `"i want to buy a car"` into `"I would like to
apply for a car loan"` before `node_02e` (intent formulator) ever saw the
request — deciding, on triage's own authority, that a loan was the right
product. `node_02e`'s own product/intent classification was reasoning
correctly and in good faith from what it was handed; the ambiguity it
should have caught had already been erased one node earlier. Full
description of this finding and its fix (a subtractive, general-principle
rewrite of both `node_02d`'s and `node_02e`'s prompts, replacing a
would-be growing checklist of named ambiguity patterns with one shared
test — "can I complete my own defined job, right now, with genuine
confidence?" — already implied by `_CLARIFICATION_SCOPE_BY_ORIGINATOR`'s
existing per-node job statements) lives in `TOOL_CALLING_v3.0.md`'s
"Triage was silently doing node_02e's job" section, since it's a triage/
intent-formulator boundary fix, not a retry-ladder one — Extra Effort
v2.1's own mechanics (direct-adopt, Territory A/B) were all found to be
working exactly as designed throughout; they simply inherited an
unrecoverable bad premise from upstream.

## 9. Exhausted retries and hard escalation

What happens when even the full ladder exhausts is covered in
`DECLINE_CLARIFICATION_DESIGN_v1.0.md` §10: hard escalation
(`node_09`/"n23") is reserved for a customer's own explicit request, never
for the bot's own retry exhaustion, and every exhausted-retry turn is
scored 0.0 for the historical sample via
`_append_failed_turn_escalation_history` — closing a gap where such a
turn previously contributed no data point at all instead of the failure
it represented.

## 10. Files touched

`backend/app/orchestration/nodes.py`
(`HYPOTHESIS_TEST_SAMPLE_SIZE`, `CONFIDENCE_TEST_T_CRITICAL`,
`CONFIDENCE_TEST_Z_CRITICAL`,
`CONFIDENCE_TEST_ALPHA`, `CONFIDENCE_TEST_POPULATION_MEAN_H0`,
`MIN_SCORED_SUBQUERIES_FOR_CONFIDENCE_TRUST`,
`NORMAL_EFFORT_RAG_ATTEMPTS`, `_dynamic_task_confidence`,
`_historical_subquery_scores`, `_reject_confidence_null_hypothesis`,
`node_03c`'s `provisional_turn_grade`/`subquery_scores`/hypothesis-test
wiring and `has_repeated_need_this_turn` override, `node_00`'s
`turn_escalation_history` seeding from durable history (§4a), `node_08`'s
explicit `MAX_ESCALATION_HISTORY_TURNS` slicing and
`confidence_triggered` guard, both `turn_escalation_history`
storage-truncation sites widened to `HYPOTHESIS_TEST_SAMPLE_SIZE`),
`backend/app/services/memory_service.py`
(`LongTermMemoryManager.fetch_recent_turn_resolutions`),
`backend/app/orchestration/state.py` (`live_task_confidence`),
`backend/app/api_gateway.py` (`taskConfidence` on the SSE progress
stream), `frontend/src/App.jsx` (`taskConfidence` state, live percentage
readout), `frontend/src/translations.js` (`caption.confidence`,
`caption.thinkingHarder`).

**Extra Effort v2.0 (§8c) additionally touched:**
`backend/app/orchestration/rag_engine.py` (`execute_multi_angle_search`
gains `direct_query`/`verified_session_parent_ids`; `execute_direct_search`
and its helpers removed as dead code), `backend/app/orchestration/nodes.py`
(`MAX_RAG_ATTEMPTS`, `NORMAL_EFFORT_RAG_ATTEMPTS`,
`_retrieval_mode_for_task`, `_retry_expansion_plan`, `_merge_retry_task`
removed, `node_02e`'s retry-mode `ScopeExpansionPlan` call and Territory
A/B assembly, `node_03c`'s territory-ceiling downgrade,
`is_scope_expansion_task` recursion guard, `scope_expansion_lead_*`
parsing, `subquery_ids_with_rag_evidence`/`_rag_gap_still_matters`
sibling-territory fix, `rag_task_scores_by_subquery_id` max-combination
fix, `_response_evidence_manifest`/`_generate_response_blocks`/
`_normal_outbound_grounding_context` `search_territory` tagging and the
corresponding generation-side and node_06 audit-side honesty rules),
`backend/app/core/llm_service.py` (`SearchTask.mapping_reason`/
`search_territory`/`source_task_id`, `TaskSufficiencyItem.
scope_expansion_lead_reason`/`scope_expansion_lead_parent_id`, new
`ScopeExpansionCompanion`/`ScopeExpansionPlan` schemas).

**Extra Effort v2.1 (§8d) additionally touched:**
`backend/app/orchestration/nodes.py` (new `NORMAL_EFFORT_ATTEMPT`/
`EXTRA_EFFORT_ATTEMPT`/`MAXIMUM_EFFORT_ATTEMPT`/`EFFORT_TIER_GATE`/
`_attempt_unlocked` replacing the old `NORMAL_EFFORT_RAG_ATTEMPTS` floor
constant and inline `below_floor`/`at_ceiling` comparison;
`node_03c_sufficiency_auditor`'s direct-adopt block, `search_tasks`
co-writer return, `_finalize_rag_attempts` staleness fix,
`territory_b_unlocked` computation/return; `node_02e`'s retry section
restructured around `territory_b_needed_task_ids` and the deterministic
`alternate_products`-driven filter widen), `backend/app/core/llm_service.py`
(`SearchTask.alternate_product_reason`/`alternate_products`;
`ScopeExpansionCompanion`/`ScopeExpansionPlan` renamed to
`TerritoryBCompanion`/`TerritoryBPlan` and narrowed to the no-lead-only
case; `mapping_reason`'s stale "Type 1 vs Type 2" language removed),
`backend/app/orchestration/state.py` (`territory_b_unlocked`).

**§8e additionally touched:** `backend/app/orchestration/nodes.py`
(`node_03c`'s MECHANISM-BRIDGE REASONING rule, inserted alongside the
existing PARTIAL-EVIDENCE DECISION ORDER/category-adjacency rules; the
triage/node_02e boundary fix itself is a prompt-only change to
`node_02d_user_journey_classifier_and_triage`'s `customer_component`
authoring rule and `node_02e_intent_classifier_and_query_formulator`'s
Rule 2/3, both described fully in `TOOL_CALLING_v3.0.md`'s "Triage was
silently doing node_02e's job" section).
