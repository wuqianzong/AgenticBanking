# Deflection Economics: A Business Metrics Framework for the Horizon Bot

**v2.0 — draft, 2026-09-17.** Adds a proper statistical treatment to
Tier 3's compliance/guardrail rows: a 95% one-sided upper confidence
bound (exact binomial/Clopper-Pearson — the real audit-industry
"attribute sampling" method, not a normal approximation), rather than
bare percentages or an unquantified "not observed." Turns "unauthorized
financial advice: not observed" into an actual, falsifiable claim ("true
incidence is no higher than 0.66% at 95% confidence"). Explicitly
excludes two rows (PII mishandling, AI-disclosure) from this treatment,
since neither rests on a sample — one is a structural design guarantee,
the other an exhaustive code read — and a confidence interval doesn't
apply to either. Adds this as a fifth measurement basis to the
Statistical Methodology appendix.

**v1.9 — draft, 2026-09-17.** Closes the open question from v1.8: the
user has explicitly scoped model-level tuning (self-consistency voting,
a model-tier swap, rubric tightening) as out of scope for this project —
*"model tunning is out of scope, so what we can do is surface the same
parent and keep the cache update."* Auditor-judgment non-determinism
(the other half of BUG-025) is therefore documented throughout as an
accepted, permanent limitation of this system, not a pending task. The
answer cache's read/write mechanism — already implemented and
live-validated — is the complete, final architectural mitigation this
project ships for the Fairness finding.

**v1.8 — draft, 2026-09-17.** Adds the answer cache's live before/after
comparison result (Tier 2): re-running the exact same question live
found 6/6 identical outcomes with the cache active, up from 9/12 (75%)
historically, while a parallel test on a different question reproduced
auditor-judgment non-determinism live (the same cache-injected candidate
present all 4 runs, approved twice and rejected twice). This is the
clean empirical line between what architecture can and can't fix here:
retrieval-pool consistency (solved) vs. auditor-judgment consistency (a
separate, harder, model-behavior problem — see
`docs/FAIRNESS_ANSWER_CACHE_DESIGN.md` S6a — model-tuning fixes for
that half were considered and explicitly scoped out of this project by
the user; it stands as an accepted permanent limitation, not an open
task).

**v1.7 — draft, 2026-09-17.** Ships a first, partial engineering
mitigation for the Fairness findings below: a cross-session answer cache
(`backend/app/orchestration/answer_cache.py`) now surfaces a
historically-confirmed FAQ as an extra sufficiency-audit candidate
whenever a close-enough (≥0.85 cosine similarity) question recurs,
seeded from the historical BANKING77 corpus. It never bypasses the
audit and never answers directly — see `docs/FAIRNESS_ANSWER_CACHE_DESIGN.md`
for the full design, including why two of the design's open questions
(cache-corruption safety, multi-neighbor tie-breaking, and whether to
show the auditor prior precedent) were decided the way they were. This
targets the retrieval-pool-variance half of BUG-025 specifically; it does
not touch auditor-judgment non-determinism, and does not help BUG-023's
own traced pair (similarity 0.65, below any safe threshold) — status on
both bugs is "partial mitigation," not "fixed."

**v1.6 — draft, 2026-09-17.** Confirms and deepens the Fairness finding
with a second, embedding-based test: every BANKING77 subquery embedded
with the production RAG pipeline's own embedding model, full pairwise
cosine similarity computed, and checked whether close-similarity question
pairs actually retrieve from the same evidence. Even the closest
similarity band shares an evidence basis only 60.9% of the time,
confirming BUG-023 generalizes beyond the one spot-checked category. A
more severe sibling finding fell out of the same analysis and is tracked
separately as **BUG-025**: 35.2% of *byte-identical* repeated questions
(the exact same sentence, no wording variation possible) still land on a
different retrieval outcome — a fairness/consistency defect that no
ambiguity argument can excuse.

**v1.5 — draft, 2026-09-13.** Adds the three remaining computed metrics
from `AGENTIC_EVALUATION_PLAN_v1.md`: a real cost-per-resolved-query
figure using published API pricing ($0.039 BANKING77 / $0.095 multi-turn,
B1), Tool-Selection Accuracy (94.7%, n=94 manual sample, Tier 2), and a
reframed Fairness metric — per explicit instruction, tested as
same-intent phrasing-consistency rather than demographic variation
(72.6% mean outcome-concentration, 24/77 categories confirmed
inconsistent, Tier 2 and BUG-023). Also documents a confirmed
Transparency gap (zero AI-disclosure anywhere in the product, Tier 3 and
BUG-024). Every metric identified in the companion evaluation plan is now
computed.

**v1.4 — draft, 2026-09-13.** Corrects a real label error found via a
full open-bug review before finalizing this evaluation: 22 of BUG-015's
24 confirmed cases were silently counted as "resolved" by every automated
metric despite the customer receiving a total-failure generic message.
No code was fixed (that would mean evaluating a different system than the
one tested); the 26 affected tasks are relabeled as a confirmed bot
failure, separate from BUG-016's projected rate, and every downstream
figure in this document (Overall Resolution, Containment/Handoff Rate,
Bot Reliability) is restated accordingly. Companion document
`docs/report/evaluation/AGENTIC_EVALUATION_PLAN_v1.md` now carries the full six-pillar
evaluation plan (retrieval quality, generation quality, agentic task
completion, handoff/cost, trust/compliance, latency) this framework feeds
into. Adds a Statistical Methodology appendix
distinguishing full-corpus, automated-proxy, and sample-projected
metrics; reconciles a 973-vs-1,036 unresolved-task discrepancy into a
corrected category breakdown (content gap / bot failure — confirmed /
bot failure — projected / out-of-scope / legitimate clarification); and
carries the resulting 95%
confidence intervals into Bot Reliability and KB Coverage rather than
reporting bare point estimates. Also adds the Bot Reliability × KB
Coverage decomposition (B1b), grounds it in the RAGAS and SQM Group
industry precedents, and benchmarks our containment/handoff rate against
Gartner, NatWest, and Trust Bank Singapore (B1c). Redesigns the
technical-academic framing of
`Metrics & Telemetry Reference Guide v3.5` around a business question a
bank's operations and finance stakeholders actually ask: **does this bot
save cost and labour, and does it improve service quality while doing
it?** Grounded throughout in real numbers already measured from two
completed studies — the 3,080-case single-turn BANKING77 full-coverage
run and the 249-conversation multi-turn depth test — not projections.

## Why redesign the v3.5 framework

v3.5 is a sound technical skeleton (containment rate, FCR, intent
accuracy, groundedness, latency are the right five things to instrument),
but it was written before the system reached its current shape, and two
things need fixing before it can carry a business argument:

1. **Several of its cited data sources don't exist.** It points at
   `langgraph_state.db` (the real checkpointer, `MemorySaver()`, is
   in-memory and wiped on restart) and `guardrail_violation.log` (the real
   log is `backend/logs/system.jsonl`). The actual telemetry is richer
   than what v3.5 assumed — it just needs the citations corrected.
2. **It leads with technical metrics, not business ones.** F1 scores and
   TTFT are how you prove a system is well-engineered to an examiner; they
   are not how you prove to a COO that a rollout pays for itself. This
   framework puts cost, labour, and service quality at the top, and
   demotes the technical metrics to what they actually are: diagnostics
   that explain *why* the business numbers move.

## North Star: three numbers a business reader should be able to quote

| # | Metric | Current baseline | What it proves |
|---|---|---:|---|
| 1 | **Full-session resolution rate** | 69.5% (multi-turn), 67.1% (single-turn, BUG-015-corrected) | How much genuine customer *labour* — not just single FAQ lookups — the bot absorbs without a human |
| 2 | **Trust & safety incident rate** | 2.1% of balance-fetch turns contain a false capability claim (BUG-018) | Whether the bot is safe to scale before cost savings are worth pursuing at all |
| 3 | **Median turn latency** | 9.2s (multi-turn), 22.7s (single-turn) — vs. an 800ms/2.5s technical target | Whether slow responses are quietly cancelling out the cost savings via abandonment |

---

## Tier 1 — Business Outcome Metrics

These are the numbers that go in front of operations and finance. Each
has a real baseline already measured; each also has a placeholder for the
one input only the bank itself can supply — real agent unit costs.

### B1. Automated Containment (the cost-and-labour headline)

**What it answers:** *"Of everything customers currently need a human
for, how much can this bot take off the queue?"*

Rather than the v3.5 definition (containment fails only when a customer
accepts an escalation offer — a real signal, but one that turns out to
almost never fire in either completed study: `soft_escalation_available`
was true in 0/3,552 BANKING77 turns and 6/1,964 multi-turn turns, so by
that literal definition containment is ~100% and tells you nothing), this
framework uses the three-tier split every call-centre operation already
thinks in:

$$\text{Fully Contained} = \frac{\text{Queries needing zero human follow-up}}{\text{Total queries}} \qquad \text{Partially Contained} = \frac{\text{Queries where the bot delivered real value but not the full answer}}{\text{Total queries}}$$

| | Fully contained | Partially contained | Needs a human |
|---|---:|---:|---:|
| BANKING77 (single-turn, 3,080 queries) | 67.1% | 13.2% | 19.6% |
| Multi-turn (249 conversations) | 69.5% | 30.5% | 0.0% |

*(BANKING77 figures corrected 2026-09-13 for the BUG-015 label error
below — 22 cases moved from "fully contained" to "needs a human." Partial
is unaffected; none of the 22 came from that bucket.)*

**Cost translation (template — plug in real unit costs):**

$$\text{Monthly Savings} = \big(\text{Fully Contained Volume}\big) \times \big(\text{Avg. Agent Handle Time}\big) \times \big(\text{Fully-Loaded Agent Cost per Minute}\big) \; - \; \big(\text{Bot Operating Cost}\big)$$

Bot operating cost itself decomposes cleanly from what's already
instrumented: total LLM calls × per-call API cost + infrastructure. Both
studies already log every `llm.request` event, so this is a real,
computable number the moment a per-call price is supplied — not a
guess.

**Computed illustrative cost, using real published API pricing
(2026-09-13).** Applying published per-token rates (Gemini 3.5-flash-lite
$0.30/$2.50 per million input/output tokens; Gemini 3.1-flash-lite
$0.25/$1.50; Llama Prompt Guard 2 86M via Groq $0.04/million) to token
counts estimated from the `raw_prompt`/`raw_response` text already logged
on every `llm.request`/`llm.response` event (~4 characters per token — an
approximation, not the model's own exact tokenizer output, so treat the
totals below as illustrative, not a billing-grade figure):

| | Total LLM spend | Requests | Cost per case | **Cost per resolved case** |
|---|---:|---:|---:|---:|
| BANKING77 (3,080 cases) | $81.50 | 31,849 | $0.026 | **$0.039** |
| Multi-turn (249 conversations) | $16.45 | — | $0.066 | **$0.095** |

BANKING77 breaks down as $58.20 across 15,038 gemini-3.5-flash-lite
requests, $23.29 across 13,740 gemini-3.1-flash-lite requests, and
effectively $0 across 3,071 llama-prompt-guard-2-86m requests. "Cost per
resolved case" divides total spend by the fully-contained volume (B1's
own numerator: ~2,066 BANKING77 cases at 67.1%, ~173 multi-turn sessions
at 69.5%) rather than by total case count — the number a business reader
actually wants ("what does it cost to close a case with no human
involved") is higher than the flat per-case average, because unresolved
cases still spend LLM calls without producing a resolution. This is the
number to compare against fully-loaded agent handle-time cost in the
Monthly Savings formula above, once the bank's own agent-cost inputs are
supplied.

**Honest caveat carried over from the v3.5 review:** because
`soft_escalation_available` almost never fires in either test corpus,
"accepted a suggestive hand-off" as a failure mode is not really being
exercised yet. The 0%/19.6% "needs a human" figures above should be read
as *the bot declining or asking for clarification*, not as *a live
escalation being triggered and accepted* — a genuinely different signal
that would need an adaptive test harness (one that can react to the bot's
own hand-off offer) to measure properly. Flagged here rather than
smoothed over.

### B1b. Decomposing containment: Bot Reliability × KB Coverage

**The problem this section fixes:** once the bot-failure defects
identified below (BUG-016 and its relatives) are fixed, the *remaining*
human-handoff volume will be almost entirely content gaps — cases where
the knowledge base simply never had the answer. If containment is
reported as one blended number, it will then be measuring **knowledge
base completeness**, not **bot reliability** — two different things, with
different owners, that improve on completely different timelines (content
coverage rises purely from authoring more FAQs, with zero code changes).
Reporting one number after that point silently credits or blames
engineering for something purely a content-operations outcome.

**The fix — the same split real contact centres already use for human
agents** (did the agent have the right knowledge-base article, vs. did
the agent use it correctly), applied to the bot: classify every
unresolved query as either a **content gap** (the KB genuinely has no
answer for the fact asked) or a **bot failure** (the KB had a usable
answer and the pipeline failed to surface or use it), then track two
independent rates instead of one:

$$\text{Bot Reliability} = \frac{\text{Resolved}}{\text{Resolved} + \text{Bot Failures}} \qquad \text{KB Coverage} = \frac{\text{Resolved} + \text{Bot Failures}}{\text{Total Queries}}$$

$$\text{Overall Resolution} = \text{Bot Reliability} \times \text{KB Coverage}$$

Computed across the full BANKING77 corpus (4,928 tasks; see the
**Statistical Methodology** appendix at the end of this document for
exactly which of these numbers are full-corpus counts versus
sample-projected estimates, and why that distinction matters):

| Metric | Formula | Value | 95% CI | Owner |
|---|---|---:|---:|---|
| **Bot Reliability Rate** | 3,866 resolved ÷ 4,033 resolvable (in-scope, unambiguous queries only) | **95.9%** | 94.3%–97.5% | Engineering |
| **KB Coverage Rate** | 4,033 resolvable ÷ 4,865 in-scope total | **82.9%** | 81.5%–84.3% | Content / knowledge ops |
| Overall Resolution (customer-facing, all queries) | 3,866 ÷ 4,928 | 78.5% | — | — |

*(Task-level, whole-corpus counts — see the BUG-015 correction directly
below for why "resolved" is 3,866, not the 3,892 an earlier draft of this
section reported before that correction was applied.)*

**How "bot failure" was measured, including a retracted first attempt.**
A first pass tried to classify this automatically at scale, flagging any
unresolved case as a bot failure if a shown candidate document contained
*any* negation-shaped sentence ("does not…", "cannot…"). Spot-checking 15
of those flags found the heuristic was matching negations from an
unrelated part of the same document (e.g. a misdirected-transfer FAQ's
"we cannot guarantee recovery" getting matched against an unrelated
"refund not showing up" query) — a large false-positive rate, and that
automated number was discarded rather than reported. The number actually
used instead is the **already manually-verified rate from the 88-case
semantic sample** (see `docs/KNOWN_ISSUES_TRACKER.md` BUG-016): **14.8%
(95% CI 7.7%–21.8%)** of "shown but rejected" tasks were confirmed, by
direct human comparison of the query against the actual candidate text,
to be genuine bot failures.

**A second, more consequential label error was found separately (BUG-015,
2026-09-13), while checking the bug tracker for anything that needed
fixing or offsetting before finalizing this evaluation.** Of the 24
confirmed BUG-015 cases (a generic failure fallback overwriting an
already-resolved turn's text — see `KNOWN_ISSUES_TRACKER.md`), **22 still
carried a `task_results` status of `SUFFICIENT`/`RELEVANT`**, meaning
every automated metric in this document was silently counting a total
customer-facing failure as a success. Unlike BUG-016's rate, this one
needed no projection — the 26 affected tasks are exactly known, not
estimated, so they're tracked as their own **confirmed** bot-failure
category, separate from BUG-016's **projected** one, rather than folded
into the same sampled rate. This is the correction responsible for the
"resolved" figures above being 3,866, not the 3,892 that would come from
`task_results` alone.

**A discrepancy was found and reconciled while preparing this section**,
not just flagged. An earlier pass counted 973 unresolved *tasks* (the
population the retrieval-miss analysis and the 88-case sample were both
drawn from); a fresh full-corpus count found 1,036. The gap is real and
now fully explained: **63 cases are declined or asked for clarification
at the dispatcher stage, before any RAG task is ever created** — so they
never appear in `task_results` at all, and the earlier count silently
excluded them rather than double-counting or erroring. Splitting the full
corpus correctly, across **five** categories rather than the two used
originally:

| Category | Count | Share of unresolved | Fixable how |
|---|---:|---:|---|
| FAQ / content gap | 832 | 78.3% | Author the missing content |
| Bot failure — confirmed (BUG-015) | 26 | 2.4% | Engineering fix, root cause not yet found |
| Bot failure — projected (BUG-016, 95% CI 73–207) | 141 | 13.3% | Engineering fix (already root-caused) |
| Correctly declined — out of scope | 15 | 1.4% | Not a defect — Horizon genuinely doesn't offer the product asked about (e.g. FX exchange, margin trading) |
| Legitimate clarification needed | 48 | 4.5% | Not a defect — the query was genuinely ambiguous (e.g. "my card" without specifying credit or debit); appears "unresolved" only because BANKING77 is single-turn and can never supply the follow-up answer a real conversation would get |

The last two categories are new to this framework and matter precisely
because neither is a content gap *or* a bot competence problem — folding
them into either bucket, as the original two-category split implicitly
did, would have misattributed 63 tasks (6.1% of all originally-unresolved
cases) to the wrong owner. Bot Reliability and KB Coverage above are
scoped to exclude both, since a business-scope decision and a single-turn test
artifact are neither an engineering metric nor a content metric.

**Where the content gaps cluster.** Ten categories account for 226 of
581 fully-unresolved BANKING77 cases (38.9%), each showing the *same*
missing-fact pattern across nearly every case in it — not occasional
variation:

| Category | Unresolved | Nature of the gap |
|---|---:|---|
| exchange_rate | 28 | Live data — a current FX rate can never live in a static KB |
| top_up_by_card_charge | 27 | Card-based top-up fees never documented |
| Refund_not_showing_up | 24 | KB covers refund mechanics, never timeline/status |
| top_up_reverted | 23 | No "why was my top-up reversed" content exists |
| disposable_card_limits | 23 | Confused with supplementary cards; not a documented product |
| country_support | 23 | No canonical "which countries we operate in" list |
| verify_source_of_funds | 20 | A real compliance process, never written as a customer FAQ |
| balance_not_updated_after_cheque/cash_deposit | 20 | No deposit-processing-time content |
| fiat_currency_support | 19 | Same enumeration gap as country_support |
| card_payment_wrong_exchange_rate | 19 | Live-data variant of exchange_rate |

The one exception worth naming explicitly: **`exchange_via_app`** (23
unresolved cases) is the only top category dominated by a confirmed *bot*
failure (BUG-016) rather than a content gap — the KB explicitly states
"we don't handle foreign-currency transfers," but the auditor doesn't
recognize that negative statement as a complete answer. Fixing BUG-016
should resolve most of this one category specifically; it has no effect
on the other nine.

**Business implication for handoff design:** a topic recognized as
belonging to one of the ten content-gap categories above cannot be fixed
by retrying generation — no prompt or retrieval improvement conjures a
fact that was never authored. For those, the efficient design is to route
straight to a live-data source or a "let me get you that" hand-off and
log a content-authoring ticket, rather than spending latency and LLM
spend on an attempt that is structurally certain to fail. Retry budget
and escalation-precision effort belong on the ~15% bot-failure share
instead, where an engineering fix genuinely moves the number.

#### Grounding B1b in established standards, not an ad hoc split

Two independent, pre-existing precedents formalize exactly this
decomposition — which also means the industry's own literature already
warns why a single blended containment number is unsafe to compare
across bots or vendors.

**RAGAS** (the same RAG-evaluation framework v3.5 itself cited for
Metric 4) formally splits every evaluation into two independent halves,
one per side of our split:

| RAGAS metric | What it measures | Maps to |
|---|---|---|
| Context Recall / Context Precision | Does the retrieved content actually contain what's needed, and is it relevant | **KB Coverage** |
| Faithfulness / Answer Relevance | Given what was retrieved, did the model use it correctly and stay grounded | **Bot Reliability** |

This is the field's standard evaluation shape for any RAG system, not
something invented for this project — B1b's split is the same idea under
project-specific names.

**SQM Group's Source-of-Error framework**, the standard root-cause
methodology contact centres have used for decades to judge First Call
Resolution failures, splits blame three ways: Customer 13%, Agent 38%,
**Organization 49%** — where "Organization" specifically means policies,
systems, and knowledge gaps, deliberately kept separate from agent skill
so a human agent is never penalized for a knowledge-base gap outside
their control. Our own split (85% content-gap / 15% bot-attributed) skews
even further toward "organization" than that decades-old human baseline
— a real, credible reference point, not just an internal number.

**The caveat this grounding surfaces:** the chatbot industry's own
literature acknowledges the blended metric is manipulable —
*"[g]aps in the knowledge base are the most common cause of low
containment"* and best practice is to *"track [KB coverage] as a separate
metric from containment rate"* (Botpress), while *"[s]tandard containment
metrics can actively mislead teams... you are manufacturing fake wins"*
(Botpress) if containment and true resolution are allowed to diverge. In
practice this guidance is inconsistently followed — most published vendor
containment numbers are blended, with no way to know from the outside how
much reflects content investment versus orchestration quality. Two
implications: (1) any external containment-rate comparison in B1c below
should be read as illustrative, not rigorous, since a competitor's number
could reflect either dimension in unknown proportion; (2) **Bot
Reliability specifically is the one claim in this whole framework that
can't be inflated just by adding more FAQ content** — it's already
normalized against whatever the KB happens to contain — which makes it
the correct number to lead with in any external or competitive claim
about engineering quality.

### B1c. Industry Benchmark Comparison — Human Handoff Rate

**Definition, tightened to match how the industry actually defines
containment** ("fully resolved within the automated channel without
escalation" — a partially-resolved query does not count as contained
under this stricter, externally-comparable reading):

$$\text{Human Handoff Rate} = 1 - \frac{\text{Fully Resolved}}{\text{Total Queries}} \qquad \text{Containment Rate} = \frac{\text{Fully Resolved}}{\text{Total Queries}}$$

| Study | Containment | Handoff rate |
|---|---:|---:|
| BANKING77 (single-turn) | 67.1% | 32.9% |
| Multi-turn (whole session) | 69.5% | 30.5% |

| Benchmark | Value | Source |
|---|---:|---|
| Gartner 2025 — mature RAG deployments | 55–65% containment | Gartner Customer Service Technology Survey |
| Gartner 2025 — rule-based bots | 20–35% containment | Gartner Customer Service Technology Survey |
| Industry-wide "leaders" | 70–90% containment | Multiple 2026 chatbot-KPI surveys |
| Industry-wide typical | 20–40% containment | Multiple 2026 chatbot-KPI surveys |
| "Healthy" handoff rate | 15–25% | Conferbot Chatbot Human Handoff Guide |
| NatWest Cora+ (proof-of-concept) | 67% reduction in adviser handoffs | Reported case study |
| Trust Bank Singapore (live Gen-AI chatbot) | 50% cut in human-handled chats, ~40% fewer complaints | Fintech Singapore / IBS Intelligence |

**Reading our numbers against these:** 67.6–69.5% containment sits
**above Gartner's mature-RAG average (55–65%)** — a fair comparison since
our architecture is exactly what that bucket describes — and just below
the "leaders" bracket. Our 30.5–32.9% handoff rate sits at the edge of
the "healthy" 15–25% range, on the high side but not in the "customers
distrust it" zone (>30% is the warning threshold per Conferbot, so we are
right at that boundary, not comfortably inside it).

**Two caveats that matter more than the raw comparison:**

1. Per the grounding above, none of these external numbers are known to
   be decomposed the way B1b decomposes ours — treat the comparison as
   illustrative positioning, not a rigorous benchmark match.
2. **Escalation itself is barely exercised in either test corpus**
   (`soft_escalation_available` true in 0%/0.3% of turns), so our
   "handoff rate" here means *the bot declined or asked for
   clarification*, not *a live hand-off was triggered and accepted* — the
   thing industry escalation-rate figures more typically measure. Also
   worth noting: 86% of consumers want the *option* to reach a human, and
   40% abandon entirely when they can't find one (2025 Salesforce study)
   — since our escalation path is real but essentially untested by either
   study, whether it's discoverable and trusted by real customers is a
   separate, unanswered question this framework doesn't yet cover.
3. **BANKING77 is a harder-than-real-life test by design** (40 balanced
   queries across all 77 categories, including every confirmed
   content-gap topic) — real production traffic, typically skewed toward
   a small set of common questions, would very likely score higher, so
   67.6–69.5% is best read as a floor, not a ceiling.

**The differentiated claim worth leading with:** most cited industry
figures are a single blended number with no visibility into what drives
it. We can say something most competitors publishing a containment
rate cannot: *"our containment already exceeds the mature-RAG average,
and we can show exactly what moves it from here — content authoring
for ~85% of the remaining gap, three named, already-diagnosed bug fixes
for the rest."*

### B2. Full-Session Resolution Rate (replaces FCR)

**What it answers:** *"Can the bot carry a customer through an entire
multi-step need — not just answer one FAQ — in a single sitting?"*

v3.5's FCR was scoped to a single exchange. The multi-turn study is
direct evidence the bot can do something more valuable: hold a customer
through several linked asks (check balance, transfer, re-check balance,
close out) inside one uninterrupted session, at **69.5%** full
resolution across all 249 sampled conversations, averaging **7.9 turns
each**. That is the stronger business claim — it's closer to "replaces an
entire teller interaction" than "answers one question a website FAQ page
already could."

$$\text{Full-Session Resolution (\%)} = \frac{\text{Conversations where every turn resolved}}{\text{Total conversations}} \times 100$$

Both baselines (67.1% single-turn, 69.5% full multi-turn session) sit
below a reasonable **80% target** — a real, quantified gap, not a
rounding error, and the next section explains most of where it's coming
from.

### B3. Customer Effort & Time-to-Resolution

**What it answers:** *"Is the bot fast enough that customers actually
wait for the answer instead of giving up and calling anyway?"* — the
metric that keeps B1's savings honest, because an abandoned bot session
that ends in a phone call anyway isn't a saved contact, it's a slower one.

| | Median turn latency | p90 | Turns meeting a 2.5s target |
|---|---:|---:|---:|
| BANKING77 (single-turn) | 22.7s | 39.5s | 0.9% |
| Multi-turn | 9.2s | 15.2s | 0.0% |

This is the clearest gap in the whole framework, and it's structural, not
incidental: the pipeline runs several *sequential* LLM calls per turn
(triage → planning → generation → audit → substitution), and nothing
about the final answer streams token-by-token to the customer today (the
system does stream live per-node progress captions — "Thinking..." — so
customers aren't staring at a truly blank screen, but the substantive
answer itself only appears once the whole turn completes). Business
framing: every second of wait time is a direct tax on B1's savings,
since it's the single biggest lever on whether a customer bails to a
human channel before the bot even finishes.

### B4. Service-Quality Index

**What it answers:** *"When the bot does answer, is the answer actually
good — correct, honest, and appropriately toned?"* This is the metric
that answers the "improve service quality" half of the brief, not just
"is this cheaper."

Measured via structured, sample-size-justified manual review (the same
method used for both completed studies: stratified/random sampling sized
for a 95% confidence interval, then read case-by-case against a fixed
rubric — on-topic, correct or honestly declined, appropriately toned):

| Study | Sample | Semantically sound | 95% CI |
|---|---:|---:|---|
| BANKING77 (single-turn) | 385 | ~96–97% | tight |
| Multi-turn (whole conversations) | 70 | 54.3% | ±9.9pp |

The gap between these two is itself the finding, and it's a constructive
one: single-turn quality is excellent, and the multi-turn shortfall
traces almost entirely to one already-diagnosed, narrow defect (see
Tier 2 below) — not a broad quality problem. That means the fix is
bounded and the expected lift is quantifiable, which is exactly the kind
of thing a business case wants to say: *we know precisely what's wrong,
and fixing it is expected to move this number from ~54% toward the
90%+ range the single-turn number already proves is achievable.*

---

## Tier 2 — Diagnostic Metrics (why the business numbers move)

These aren't shown to a COO; they're shown to the team, and they exist to
explain and drive Tier 1.

| Metric | What it measures | Current reading |
|---|---|---|
| **Bot Reliability Rate** | Of queries the KB could answer, how often the bot actually delivered it — isolates pure engineering performance from content completeness (see B1b) | **95.9%** (95% CI 94.3%–97.5%) |
| **KB Coverage Rate** | Of all queries, how many the KB has an answer for at all — a content-ops metric, not an engineering one (see B1b) | **82.9%** (95% CI 81.5%–84.3%) |
| Turn-level resolution rate | Did the retrieval/tool layer actually complete correctly | 95.8% of multi-turn turns; task-level SUFFICIENT/RELEVANT in BANKING77 |
| **RAG retrieval completion rate** | Of every `rag` dispatch, did it return usable evidence at all (vs. nothing) — `deeplink`/`api`/`service_request` are 100% by construction, excluded from this number | **78.4%** (BANKING77), **91.7%** (multi-turn). Worst 10 categories by failure rate exactly match B1b's 10 content-gap categories — independent confirmation |
| **Context Precision** | Of every candidate ever shown to a task, what fraction was actually approved | **18.9%** macro / **15.8%** micro — low by design (wide-net retrieval, filtered downstream by the sufficiency auditor), not a defect; read together with KB Coverage, not alone. Full detail in `AGENTIC_EVALUATION_PLAN_v1.md` Pillar A |
| Outbound audit pass rate | Did the response clear the live compliance/groundedness gate on the first try | 84.2% (BANKING77) |
| **Balance-surfacing rate** | Of every successful balance fetch, did the customer actually see the number | **88.0%** (74/619 fetched-but-dropped — this single mechanism explains the bulk of B4's multi-turn gap) |
| Multi-turn coherence rate | Whole-conversation semantic soundness | 54.3% (see B4) |
| Escalation precision | Did the bot escalate exactly when warranted, no more, no less | Not yet measurable — near-zero escalation volume in both test corpora means this needs a dedicated, adversarial test set designed to actually trigger it |
| **Tool-Selection Accuracy** | Of every subquery, was the right tool (`rag`/`api`/`deeplink`/`service_request`) dispatched for what the wording actually called for | **94.7%** (95% CI ~90.2%–99.2%, n=94 manually judged, ±10% margin). 89/94 clean; 1 confirmed mismatch (a card-top-up question routed to `deeplink`-only instead of the informational `rag` answer); 3 borderline "could have been paired better" cases, not scored as errors |
| **Outcome-concentration rate (Fairness proxy)** | Same-intent consistency: across a BANKING77 category's 40 phrasings of one nominal intent, do they land on the same outcome | **72.6% mean** across 77 categories; **24/77 below 60%** — confirmed real, not noise (see BUG-023): near-identical `declined_card_payment` phrasings split resolved/unresolved. Reframed per explicit instruction from a demographic-fairness test (no data supports one) to a phrasing-consistency test, since the bot treats every request as customer-agnostic |
| **Cosine-similarity share rate (Fairness, embedding-based)** | Of question pairs close in embedding space (same model the production pipeline uses for retrieval), what fraction share a common approved-evidence basis | **60.9%** at 0.97–1.0 similarity, degrading to **45.1%** by 0.80–0.85 — confirms BUG-023 generalizes past the one spot-checked category. A more severe sibling result: **35.2%** of *byte-identical* repeated questions (no wording variation at all) still land on a different outcome — tracked as **BUG-025**. Partial mitigation shipped 2026-09-17: a cross-session answer cache (`docs/FAIRNESS_ANSWER_CACHE_DESIGN.md`) now surfaces a historically-confirmed FAQ as an extra audit candidate for ≥0.85-similarity repeats |
| **Answer-cache live re-test (before vs. after)** | Same exact question, live, before vs. after the cache | `"delete my account"` (n=6): **6/6 identical**, up from 9/12 (75%) historically — organic retrieval found the right FAQ in 0/6 fresh runs, the cache alone carried every resolution. `"payment declined"` (n=4): same cache-injected candidate present in all 4 runs, auditor still split 2/2 — isolates the architectural ceiling exactly: retrieval-pool consistency is a solved, architecture-layer problem; auditor-judgment consistency on identical evidence is a separate, model-behavior problem — model-tuning fixes for it (self-consistency voting, a model-tier swap, rubric tightening) were considered and explicitly scoped out of this project by the user; it stands as an accepted permanent limitation (`docs/FAIRNESS_ANSWER_CACHE_DESIGN.md` S6a) |

---

## Tier 3 — Trust & Safety Guardrails (zero-tolerance, tracked as counts, not rates)

Banking is a regulated, trust-sensitive domain: a single bad incident
here costs more than a percentage point moved on Tier 1 is worth. These
are tracked as absolute incident counts trending to zero, not as a
percentage to maximize.

**Statistical treatment — a different method than every other CI in this
document, deliberately.** Every other confidence interval here (Bot
Reliability, KB Coverage, Tool-Selection Accuracy) is two-sided, because
the question is "what's our best estimate." Compliance is a one-sided
question — *"what's the worst this could plausibly be"* — so these rows
instead use a **95% one-sided upper confidence bound**, computed with the
exact binomial (Clopper-Pearson) method rather than a normal
approximation (these are small counts of rare events, where a normal
approximation is unreliable). This is the same method real financial and
quality audits use for compliance sampling ("attribute sampling"), and
the same "zero-defects" treatment used in rare-adverse-event safety
monitoring: even a row with zero observed incidents gets a real,
quantified bound instead of an unquantified "not observed."

| Incident class | Found | Rate (x/n) | **95% one-sided upper bound** | Example |
|---|---|---:|---:|---|
| **False capability denial** (bot claims it can't do something it just did) | Yes | 13/619 (2.10%) | **3.32%** | *"While live account balances cannot be checked directly..."* — moments after successfully fetching it |
| **Fabricated account/entity** (plays along with a false premise) | Yes | 2/70 (2.86%) | **8.72%** | Inventing a nonexistent "other savings account" rather than correcting the customer |
| Raw internal template leakage into customer-facing text | Yes | 6/3,080 (0.19%) | **0.38%** | Precision Substitution fragment leak |
| Unauthorized financial advice / yield predictions | Not observed (0/455 manually-reviewed cases — 385 BANKING77 + 70 multi-turn) | 0.00% | **0.66%** | We can state, at 95% confidence, that the true incidence is no higher than this — not just "not observed," an actual bound |

**Two rows deliberately excluded from this table** because a confidence
interval doesn't apply to either, and force-fitting one would overstate
rigor rather than add it:

- **PII/personal-data mishandling** — "not observed" here rests on the
  `{{data-*}}` reveal-token design being structurally incapable of
  persisting PII, confirmed by reading the mechanism itself, not by
  sampling behavior. There is no sampling uncertainty to bound; it's a
  design guarantee.
- **No AI-disclosure to the customer** (MAS FEAT Transparency gap,
  **BUG-024**) — this came from an exhaustive read of every greeting
  template (all 3 languages) and the frontend UI code, not a sample of
  turns. A confidence interval doesn't apply to a complete census —
  there's nothing left uncertain to bound. It remains a confirmed content
  gap, tracked as a bug, not a rate.

Every row above already has a full write-up with root cause status and
reproduction detail in `docs/KNOWN_ISSUES_TRACKER.md` (BUG-014, BUG-017,
BUG-018, BUG-020, BUG-024). The Fairness/consistency findings (BUG-023,
BUG-025 — Tier 2 above) are rates rather than zero-tolerance incidents,
so they stay in Tier 2 rather than this table.

---

## How this is actually measured (corrected data sources)

| v3.5 said | Actually use |
|---|---|
| `langgraph_state.db` (SQLite checkpointer) | `backend/logs/system.jsonl` — structured JSONL event log, every `llm.request`/`llm.response`/`task.*`/`outbound.*` event timestamped and session-tagged |
| `guardrail_violation.log` | `outbound.compliance.completed` / `outbound.groundedness.completed` events in `system.jsonl`, each carrying a `passed` boolean |
| Ad hoc pandas parsing of checkpoint rows | `Persistent_Chat_Message` and `Turn_Resolution_Feedback` tables in `backend/data/simulated_ledger.db` for durable, cross-session history |
| sklearn macro-F1 over a 3-domain classifier | Component-level precision/recall against `node_02d`'s actual multi-component, multi-product decomposition output — **not yet built**; needs a one-time mapping of BANKING77's 77 categories onto the real product×intent grid before it's computable |
| Offline RAGAS/TruLens judge, built from scratch | Validate/calibrate the **already-live** `node_06` compliance/groundedness gate first; add an independent-model offline judge only where the live gate's own blind spots (self-grading risk, since generation and audit may currently share a model family — worth confirming) need a check |

---

## What's not yet measurable, and the next steps

1. **Real unit costs (partially resolved).** The API side of B1's cost
   formula is now real, computed from published pricing (see B1) — what's
   still missing is the bank's own fully-loaded agent-minute cost, the
   other input to Monthly Savings. The moment that one number exists,
   every dollar figure in B1 is real end-to-end, not illustrative.
2. **Escalation precision/recall.** Needs a test set deliberately designed
   to trigger both mandatory and suggestive hand-off paths (fraud
   language, explicit distress, repeated failure) — neither completed
   study touches this at all.
3. **Component-level intent accuracy.** Needs the BANKING77-category →
   product/intent mapping exercise described above; fully doable from
   existing data once that mapping exists.
4. **Content-authoring backlog.** The ten categories in B1b's clustering
   table are a ready-made, evidence-based priority list for content
   ops — each already has a confirmed, concentrated gap (20+ cases each)
   rather than being a guess at where to write next.
5. **BUG-023 root cause.** The Fairness/phrasing-consistency finding
   (Tier 2) confirms 24 categories split outcomes by wording, but not yet
   whether retrieval or generation/sufficiency judgment is the dominant
   driver per category — needed before a targeted fix can be scoped.
6. **BUG-024 remediation.** The Transparency finding (Tier 3) is a content
   gap, not a measurement gap — the next step is simply deciding and
   writing the disclosure copy (greeting line, persistent UI badge, or
   both), not further analysis.
7. **BUG-025 root cause.** 35.2% of byte-identical repeated questions get
   a different retrieval outcome, with no wording ambiguity to blame —
   needs a dedicated trace of a few split cases to localize the actual
   non-determinism to the intent/product-mapping step, the sufficiency
   auditor, or genuine retrieval-side variance.

---

## Appendix: Statistical Methodology

Every number in this framework falls into one of five measurement bases,
each with a different level of confidence attached. Conflating them —
reporting a sample-projected estimate with the same certainty as a
full-corpus count — is the single easiest way to overstate precision, so
every metric above is traceable to exactly one of these five:

| Basis | What it means | Which metrics use it |
|---|---|---|
| **Full-corpus, deterministic** | Pulled directly from every case's own system-generated verdict (`task_results` status, `passed` booleans on audit events) — nothing sampled, nothing judged by a human | Overall Resolution, Containment/Handoff Rate (B1, B1c), Turn-level resolution rate, Outbound audit pass rate |
| **Full-corpus, automated proxy** | Computed across every case, but via an automated heuristic rather than a system-native verdict or human judgment (e.g. checking whether a category's known-good FAQ ID appears anywhere in a case's prompts) | The 950 shown-but-rejected / 23 never-shown retrieval-miss split; the 15 out-of-scope / 48 legitimate-clarification dispatcher-level split |
| **Sample-based, manually verified, then projected** | A human read a statistically-sized random/stratified sample case-by-case against a fixed rubric; the resulting rate is then applied to the full population it was drawn from, carrying that sample's confidence interval forward | The 14.8% (95% CI 7.7%–21.8%) bot-failure rate underlying Bot Reliability and KB Coverage; the Service-Quality Index (96–97% BANKING77, 54.3% multi-turn); Tool-Selection Accuracy (94.7%, 95% CI ~90.2%–99.2%, n=94) |
| **Full-corpus, deterministic proxy (no sampling)** | A per-category score computed from every case's own already-known outcome classification, with no new sampling or judgment beyond spot-checking the result for face validity | The Fairness outcome-concentration score (72.6% mean, 24/77 categories below 60%) — the underlying classification is full-corpus; only the illustrative `declined_card_payment` query pairs were individually re-read |
| **Compliance/attribute sampling, one-sided upper bound** | A count of observed violations against a known sample or corpus size, converted to the single number that actually matters for a zero-tolerance guardrail — not "what's the estimate" but "what's the worst this could plausibly be," via a 95% one-sided exact-binomial (Clopper-Pearson) bound, never a two-sided normal-approximation CI (unreliable at these small counts) | Every Tier 3 row with a real x/n behind it (13/619, 2/70, 6/3,080, 0/455) — see Tier 3's own methodology note for why two rows (PII mishandling, AI-disclosure) are excluded from this treatment entirely, being structural guarantees or exhaustive reads rather than samples |

**Sample sizes were chosen deliberately, not arbitrarily**, using the
standard finite-population-corrected proportion formula for a 95%
confidence interval:

$$n = \frac{n_0}{1 + \frac{n_0 - 1}{N}} \qquad \text{where } n_0 = \frac{z^2 \cdot p(1-p)}{\text{margin}^2}$$

with $z = 1.96$ and $p = 0.5$ (the most conservative assumption absent
prior information). This produced n=385 (±5% margin) for the BANKING77
semantic review against N=3,080, n=88 (±10% margin) for the bot-failure
sample against N=950, and n=70 (±10% margin) for the multi-turn semantic
review against N=249 — each sized to the population it was actually
drawn from, not reused across different-sized populations.

**Propagating sample uncertainty into a downstream metric.** Bot
Reliability and KB Coverage are each a formula with the 14.8%
(95% CI 7.7%–21.8%) sample rate as one input. The correct way to report
the result is to run the formula at both ends of that input's interval,
not just at its point estimate — reporting a bare percentage with no
interval would silently claim a precision the underlying sample doesn't
support. Both numbers in B1b now carry the interval this produces.

**Two reconciliations, not just caveats.** An earlier draft of this
document reported an unresolved-task count (973) that didn't match a
later, more complete count (1,036). This was tracked down rather than
left as an open question: the gap was 63 real cases that a narrower
script definition had silently excluded (dispatcher-level declines and
clarifications that never reach `task_results`), not a data error or
double-count. Separately, a full bug-tracker review before finalizing
this evaluation (per explicit instruction: check whether any open bug
needs fixing or offsetting first) found that 22 of BUG-015's 24 confirmed
cases carried a `task_results` status of `SUFFICIENT`/`RELEVANT` despite
the customer receiving a total-failure generic message — silently
inflating every automated metric in this document by 26 tasks' worth
until corrected. Neither bug was fixed (that would mean evaluating a
different, not-yet-tested system); both were relabeled for accurate
reporting instead. The corrected, five-category breakdown in B1b and the
restated Bot Reliability/Overall Resolution/Containment figures
throughout this document are the result.

---

*Companion to `docs/KNOWN_ISSUES_TRACKER.md` (bug-level detail) and the
two completed studies: `research/2026-09-05_banking77_full_coverage/` and
`research/2026-09-10_multiturn_banking_extension/`.*
