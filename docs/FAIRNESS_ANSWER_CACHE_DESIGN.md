# Cross-Session Subquery Answer Cache: Design & Implementation

**v1.0 — 2026-09-17.** Implements a targeted mitigation for the Fairness
findings in `docs/KNOWN_ISSUES_TRACKER.md` (BUG-023, BUG-025) and
`docs/report/evaluation/AGENTIC_EVALUATION_PLAN_v1.md` (Pillar E). Documents the design
decided in conversation, the actual code shipped, and the historical
backfill run against the real production knowledge base.

## 1. The problem this solves

Two empirical findings this session:

- **BUG-023**: BANKING77 categories where 40 differently-worded phrasings
  of one nominal intent split across resolved/unresolved outcomes at a
  much higher rate than chance — root-caused, for one traced pair
  (`declined_card_payment`), to the intent formulator routing "card" and
  "payment" phrasings of the identical real-world event to two different
  product families (Credit_Card vs. Deposit_Account), which then search
  two different FAQ candidate pools.
- **BUG-025**: even **byte-identical** repeated questions (no wording
  variation at all — the same sentence, asked in independent sessions)
  disagree with themselves 35.2% of the time. Tracing one example
  (`"I want to delete my account."`, asked 12 times) found two distinct
  mechanisms live side by side: some occurrences never even had the
  correct FAQ surfaced as a *candidate* (retrieval-pool variance), while
  others had the exact correct candidate pair present and the sufficiency
  auditor still rejected both (pure judgment non-determinism).

This cache targets the **first** mechanism only — ensuring a
historically-good parent id is always at least offered as a candidate,
regardless of what this run's retrieval happens to surface. It does not
address auditor-judgment non-determinism on an already-correct candidate
set (see S6, "What this does not fix").

## 2. Design principle: surface, never bypass

The cache **adds an extra candidate parent id to a task's evidence pool**
before the sufficiency auditor runs. It never returns an answer directly
and never skips the audit. This one decision is what makes several of
the safety properties below affordable:

- A bad cache suggestion costs nothing but one extra candidate for the
  auditor to (correctly) reject — it is never served to a customer
  directly. This is why the similarity threshold below can be looser than
  a typical bypass-style semantic cache would tolerate.
- The system's own existing retrieval philosophy already casts a wide net
  and relies on the auditor to filter (see `BUSINESS_METRICS_FRAMEWORK_v1.md`'s
  Context Precision discussion) — this cache is one more candidate source
  feeding the same, already-trusted filter, not a new decision-making
  layer.

## 3. Where it hooks into the pipeline

| Point | File | What happens |
|---|---|---|
| Read | `nodes.py`, `node_03b_rag_worker.retrieve_task` | After live retrieval (`engine.execute_multi_angle_search`) returns its candidates, `AnswerCache.lookup(direct_query)` is queried. Any cache-suggested parent id not already present is hydrated (`engine.hydrate_parent_ids`) and appended to that task's `parents` list — the exact list the sufficiency auditor evaluates. |
| Provenance | `nodes.py`, same function + outer `node_03b_rag_worker` body | Which cache entries were consulted for each task is recorded separately in the new `task_cache_injected_parent_ids` state field (`{doc_id, parent_ids, similarity}` per hit) — never mixed into the `parents` payload itself, so the sufficiency-auditor prompt is completely unchanged in shape. Also logged on the existing `task.rag.completed` event (`cache_injected_parent_ids`) for the same auditability every other retrieval signal already gets. |
| Write | `nodes.py`, `_record_answer_cache_outcomes`, called from `node_06_outbound_audit_join`'s PASS branch | Only once a turn's own outbound compliance/groundedness audit has passed (see S4 for why this bar, not the retrieval-level task status) does the turn's outcome get recorded: `AnswerCache.record_outcome(text, approved_parent_ids, surfaced_doc_ids)`. |

New state field (`state.py`): `task_cache_injected_parent_ids: Dict[str, List[Dict[str, Any]]]`, reset every turn via `VOLATILE_STATE_RESET`, following the exact pattern already used for `task_partial_evidence`/`task_rejected_parent_ids`.

New module: `backend/app/orchestration/answer_cache.py` (`AnswerCache` class), backed by a second Chroma collection (`subquery_answer_cache`) in the **same** persistent client/store the production FAQ collection already uses (`backend/data/vector_store/`), with the same default local embedding model (`all-MiniLM-L6-v2`, no API calls) — chosen specifically so cache similarity is measured in the identical space live retrieval already uses, and no new infrastructure is introduced.

The write hook is scoped to the `normal_response` audit path only (the dominant path for real FAQ/RAG turns) — the other audit-scope branches (pure conversational/evidence-only turns) don't get cache writes in this first pass, a deliberate, stated limitation rather than an oversight.

## 4. Keeping the cache from being corrupted by a degraded answer

This was the first open question raised in design discussion. Three independent safeguards, all deliberately conservative:

1. **Consensus-gated promotion.** A `(normalized text, approved parent-id set)` pairing starts as `candidate` and is *never surfaced* until it has recurred and been independently reconfirmed `PROMOTION_CONSENSUS_THRESHOLD` (2) times. A single lenient or lucky approval — e.g. the traced "automated payment arrangement" FAQ getting approved with a hedge for "payment declined" — cannot alone seed anything the cache will ever suggest to a future customer; it needs an independent second occurrence landing on the exact same pairing, which a one-off mislabeling essentially never does.
2. **Outbound-audit-gated write eligibility.** The write hook fires only after `node_06`'s own compliance/groundedness gate has passed for the turn — a strictly stronger bar than the retrieval-level `task_results` status alone. This directly answers a risk this session already found concrete evidence for: BUG-015 showed 22/24 confirmed cases had a `task_results` status of SUFFICIENT/RELEVANT despite the customer actually receiving a total-failure fallback. Gating on the *outbound* audit, not the retrieval-level status, is a real, not cosmetic, improvement in write-quality over the naive "cache every SUFFICIENT verdict" version of this design.
3. **Rolling rejection-rate quarantine.** Every cache-suggested candidate's surfaced/rejected counts are tracked distinctly from organically-retrieved candidates (via the `task_cache_injected_parent_ids` provenance record). Once an entry has been surfaced at least `QUARANTINE_MIN_SURFACED` (5) times and its own suggestions have been rejected at least `QUARANTINE_REJECTION_RATE` (35%) of the time, it is automatically quarantined — regardless of how it was promoted. This is the safety net for drift: even a legitimately-promoted entry that stops being correct (a policy nuance the FAQ writers didn't anticipate, a subtly different sub-intent sharing the same phrasing) gets caught by its own track record, not left to accumulate indefinitely.
4. **Content-hash staleness, checked on every read.** Each entry snapshots a hash of every parent id's `content` field at write time. `lookup()` recomputes the live hash for every candidate parent id on every call (cheap — a handful of SQLite rows, the same table already hit for hydration) and drops/quarantines any entry whose underlying FAQ has since been edited. "Recency" for this cache means *since the source last changed*, never wall-clock time — a stale policy answer can never be served just because it was asked about recently. `validate_and_prune()` provides the same check as an exhaustive, out-of-band maintenance sweep for entries that haven't been queried recently enough to trigger the inline check.

Accepted, stated limitation: the historical backfill (S7) replays `task_results.approved_parent_ids` directly, since most historical records don't carry the fuller outbound-audit event detail needed to apply gate #2 retroactively. The consensus-of-2 requirement (#1) is what actually bounds this risk for the backfill specifically — see S7 for the numbers.

## 5. Choosing among multiple neighbors above threshold

Second open design question. The answer follows directly from S2's design principle: **don't choose — union.** `lookup()` returns up to `DEFAULT_TOP_K` (3) nearest eligible entries above `DEFAULT_SIMILARITY_THRESHOLD` (0.85), and every one of their parent ids is added as a candidate (deduplicated against what live retrieval already found). If a new question sits ambiguously between two genuinely different past clusters, both sets of candidates get surfaced and the sufficiency auditor — which already evaluates every candidate independently regardless of origin — decides which, if either, actually applies. Introducing a separate "pick the single best neighbor" mechanism at the cache layer would just relocate the ambiguity into a place with strictly less context (no access to the current turn's full task/conversation state) than the auditor already has.

The 0.85 threshold was chosen against the empirical band data this session already produced (`research/2026-09-05_banking77_full_coverage/reports/fairness_cosine_consistency.md`): even the 0.90–0.93 band showed a meaningful disjoint/mixed rate, so 0.85 is already a moderately loose floor — defensible specifically because of S2's "surface, never bypass" property, which a stricter bypass-style cache could not afford.

## 6. Should the auditor see the previous question/answer, not just the parent id? — No.

Third open design question, and the one place this design deliberately does **less** than it technically could. The cache surfaces the underlying evidence (a parent id, hydrated to the same `{parent_id, content, product_tags, intent_families}` shape live retrieval already produces) — never the fact that it was previously approved, and never the previous question's text.

Reasoning, in order of weight:

1. **Anchoring risk.** LLM judges are documented to lean toward agreeing with a prior verdict shown to them, even when the current instance differs subtly. Telling the auditor "this evidence was previously judged sufficient" risks entrenching exactly the kind of borderline call this design is trying to keep escapable (the "automated payment arrangement" FAQ, hedged and approved anyway) rather than letting each independent audit genuinely re-evaluate it.
2. **The in-conversation precedent (`prior_sufficiency_audit_history`) is a poor analogy here.** Within one conversation, staying consistent with an earlier verdict has a real customer-facing justification — the same customer would see the bot contradict itself. Across different customers' independent sessions, no one customer sees both answers, so there is no equivalent justification, and the anchoring risk would be imported without the offsetting benefit.
3. **Compliance-explainability.** "The auditor independently found this evidence sufficient" is a defensible answer under the MAS Accountability/Transparency principles this evaluation is already grounded in (`AGENTIC_EVALUATION_PLAN_v1.md` S0). "The auditor approved it because a prior instance was approved" is a materially weaker one, and reads as optimizing for consistency-of-outcome over correctness-of-outcome — precisely the "gaming" concern already raised earlier in this evaluation about FAQ-content manipulation.

For the failure mode this *would* have targeted — pure auditor-judgment non-determinism on an already-correct, already-present candidate set (half of the traced BUG-025 example) — the recommended alternative, not implemented in this pass, is **self-consistency voting**: sample the sufficiency-audit call multiple times independently and take the majority verdict. That reduces variance without ever telling the model what the "right" answer looks like.

### 6a. Improving sufficiency-auditor consistency itself — not an architecture problem, but not unfixable either

The live validation (S9) reproduced this failure mode directly: the identical cache-injected candidate (`faq_efca64c1`) was present in the auditor's pool in all 4 `"Why was my payment declined?"` reruns, and the auditor still split 2/2 on approving it. Two explanations were raised in design discussion — that different chat context could explain it, or that the model itself isn't consistent enough to recognize two evaluations as identical. On the evidence:

- **Chat context is not the explanation here.** Every BUG-025 case is a single-turn BANKING77 session — the auditor's own logged prompt carries `Trusted Conversation Context: ... None` in every one of the traced occurrences. There is no context difference to attribute the split to; the auditor received byte-identical input both times. (This *is* a real and correct caution for extending this fairness test into the multi-turn corpus, which has not been done yet — there, varying prior context legitimately should change the verdict, and that would be correct behavior, not a bug. Scoped explicitly here so a future extension doesn't conflate the two.)
- **Temperature was already checked and is not the lever.** `backend/app/core/llm_service.py`'s Gemini call path (which handles the sufficiency auditor, matched via `"TASK-LEVEL SUFFICIENCY AND EVIDENCE SELECTION" in prompt`) already sets `temperature: 0.0` unconditionally. The observed non-determinism persists despite greedy decoding — consistent with the well-documented, industry-wide phenomenon that temperature 0 does not guarantee bit-identical output on a hosted LLM API (floating-point non-associativity under different batching, and for some model families, mixture-of-experts routing variance, both outside any caller's control). This lends real weight to "the model itself isn't perfectly consistent," as opposed to a fixable prompt or config oversight.

Three suggestions, in order of how directly each targets what was actually observed:

1. **Self-consistency voting, applied adaptively rather than universally.** Run the sufficiency-audit call multiple times (e.g. 3) only for tasks where the cost is likely justified — the cleanest trigger is exactly the signal this cache's own provenance tracking already produces: a task whose cache-injected candidate got rejected (`task_cache_injected_parent_ids` non-empty, but that candidate absent from `approved_parent_ids`) is already a flagged, suspicious case, cheap to detect, and precisely where a second opinion is most valuable. Take a majority-of-3 vote before finalizing either a SUFFICIENT or a REJECTED verdict on that specific candidate — this never lowers the evidentiary bar (it is still the same criteria, evaluated three independent times) and never tells the model what a "right" answer looks like (unlike showing precedent, which S6 above rejects), it only damps the residual noise a single sample carries. Bounded cost: extra calls are spent only on already-flagged borderline cases, not on every task.
2. **A/B test the auditor's own model tier, using the evaluation methodology this session already built.** The sufficiency auditor currently runs on `gemini-3.5-flash-lite` — a smaller, cheaper tier than the flagship. Re-running the same duplicate-text/near-duplicate consistency check (`research/2026-09-05_banking77_full_coverage/scripts/fairness_cosine_consistency.py`'s methodology, or a fresh small live re-test structured like S9's) with the sufficiency-audit call alone pointed at a larger model tier would directly test the "the model itself isn't smart/consistent enough" hypothesis empirically, at low cost, using infrastructure that already exists — rather than leaving it as an untested assumption.
3. **Sharpen the decision procedure to reduce the auditor's own discretion.** Even at temperature 0, a holistic "does this feel sufficient" judgment leaves more surface area for residual model noise to flip a borderline call than a fully mechanical decision tree does. The existing PARTIAL-EVIDENCE DECISION ORDER rule is a step in this direction; auditing the current prompt for any remaining un-decomposed holistic judgment (particularly around hedged/partial-match evidence, like the "automated payment arrangement" FAQ) and converting it into an explicit, ordered checklist is a lower-cost, no-extra-LLM-call complement to (1) and (2).

**Scoped out of this project (2026-09-17).** All three of the above are
model-level tuning — a self-consistency vote changes the call pattern
around the model, a tier A/B test changes which model runs, a rubric
rewrite changes what the model is asked to decide. The user has
explicitly ruled out model-level tuning as a way to address this
project's quality/consistency defects: *"model tunning is out of scope,
so what we can do is surface the same parent and keep the cache
update."* This is a deliberate, final scope boundary, not an
oversight — none of the three are planned, and this section stays only
as a record of what was considered and why it was set aside, not as a
backlog. **The accepted, complete mitigation for this project is exactly
the read/write mechanism already shipped**: surfacing a
historically-confirmed parent id to the (unmodified) auditor, and
keeping that cache current from live traffic — see S7 for how the write
path already does this automatically, with no further engineering
required for it to keep working. Auditor-judgment non-determinism itself
remains a permanent, accepted limitation of this system, not a pending
task.

## 7. Historical backfill

`research/2026-09-05_banking77_full_coverage/scripts/build_answer_cache_backfill.py` replays every resolved case's `(subquery_1 text, task_1 approved_parent_ids)` pairing from the completed 3,080-case BANKING77 full-coverage study through the exact same `AnswerCache.record_outcome` call path the live pipeline uses — there is no separate bulk-seed code, so the backfill is a faithful historical replay, not a special case. Multi-turn data is **not yet included** (its per-conversation, multi-task-per-turn structure needs its own mapping pass before it can be replayed the same way — noted as follow-up work, not attempted in this first pass to avoid a rushed, incorrectly-shaped seed).

Run against the real production Chroma store at `backend/data/vector_store/` (the same store the live backend reads for FAQ retrieval) on 2026-09-17. See the run's own printed summary for exact counts (cases replayed, eligible vs. candidate entries, top entries by consensus).

## 8. Monitoring going forward

Every cache entry already carries the fields a monitoring pass needs: `status`, `consensus_count`, `surfaced_count`, `rejected_count`, `first_seen`/`last_updated`. Recommended, not yet built: a periodic report (reusing this session's research-script conventions) on quarantine rate, eligible-entry count over time, and — most importantly — a sample of quarantined entries read manually the same way BUG-005/006/016 were, since a cluster of quarantines in one category is itself a strong signal about where the underlying retrieval or product-mapping logic (not just the cache) needs attention.

## 9. Live validation (2026-09-17)

A small, targeted live re-test (`research/2026-09-17_fairness_cache_validation/`,
12 cases through the real compiled graph, `USR_999` isolated test account,
25s pacing) re-ran the exact same customer-facing text as two historical
BUG-025 duplicate-text groups now that the cache is live and backfilled,
plus a control group predicted to be unaffected.

| Group | n | Organic retrieval found the historical answer? | Cache injected it? | Final outcome consistency |
|---|---:|---|---|---|
| `"I want to delete my account."` (cache: consensus=8, eligible) | 6 | **0/6** — a different, wrong candidate set every single run | 6/6 | **6/6 identical** (`faq_49230a05`+`faq_a1fd1b2a`), matching the historical majority answer — up from 9/12 (75%) historically |
| `"Why was my payment declined?"` (cache: consensus=2, eligible, single parent) | 4 | 0/4 | 4/4 | **2/4** resolved with the cache-injected candidate, 2/4 rejected it outright — same candidate present all 4 times |
| `"Please explain why my card was declined?"` (control — similarity to any eligible entry is 0.65, below the 0.85 threshold) | 2 | 0/2 (no cache candidate exists to find) | **0/2, as predicted** | 2/2 unresolved, consistent with each other but still failing |

This is a small sample, and the numbers alone aren't statistically
decisive — but the mechanism-level logs make the causal story solid
regardless of sample size, because `task.rag.completed`'s own
`selected_parent_ids` (organic) vs. `cache_injected_parent_ids` fields are
directly visible per run:

- **Group 1 is the cleanest possible demonstration of the design working
  exactly as intended.** Live retrieval failed to organically surface
  either correct FAQ in *any* of the 6 fresh runs (a further sign that
  this exact question's retrieval reliability has drifted since the
  original study, not just an artifact of small n) — every single
  resolution this round came from the cache-injected candidate reaching
  the auditor, which then consistently approved it. Without the cache,
  this retest would very plausibly have reproduced the same
  unresolved/partial pattern the historical group showed, since organic
  retrieval had nothing to offer in every single attempt.
- **Group 2 is a clean, real-time reproduction of the *other*,
  un-addressed mechanism.** The identical cache-injected candidate was
  present in the auditor's pool in all 4 runs, and the auditor still
  split 2/2 on whether to approve it — direct, live confirmation that
  auditor-judgment non-determinism is untouched by this change, exactly
  as S6/S9 predicted rather than something this design happens to also
  fix.
- **Group 3 confirms the threshold boundary behaves as designed**: no
  cache entry is close enough (0.65 similarity, checked directly) to
  clear 0.85, so no candidate was injected, and the pre-existing
  unresolved outcome for this specific phrasing is untouched — the
  documented limitation (S9) holds exactly as stated, not just in theory.

One incidental fix from this run: `cache_injected_parent_ids` in the
`task.rag.completed` log event was found to log an undeduplicated list
when more than one eligible cache entry shares the same parent set (here,
`"...delete my account."` and `"...close my account."` both point at
`faq_49230a05`+`faq_a1fd1b2a`, so a lookup matched both and logged each
hit's ids separately). This was a display-only artifact — the actual
candidate list sent to the sufficiency auditor was already correctly
deduplicated — but has been fixed so the log itself reads cleanly too.

## 10. What this does not fix — and won't, by project scope

Documented explicitly so it isn't mistaken for a complete resolution of either tracked bug, and so the boundary between "not yet done" and "deliberately out of scope" stays clear:

- **BUG-023's root cause** (the intent formulator routing semantically-close questions to different products) is unpatched, and remains a genuine open item — fixing it means examining the product-mapping logic itself, an architectural change, not a model-tuning one. The cache is a downstream mitigation that increases the odds a good answer still reaches the auditor despite the misrouting, for pairs close enough in embedding space to clear the 0.85 threshold — the specific traced pair (`"payment declined"` vs. `"card declined"`, similarity 0.65) is **not** close enough for this cache to have helped.
- **Half of BUG-025's traced mechanism** (auditor-judgment non-determinism on an already-correct, already-surfaced candidate set) is untouched by this change, by design (S6) — and, per the explicit project-scoping decision in S6a, **stays untouched permanently, not pending**. Every remedy that would close this gap (self-consistency voting, a model-tier swap, rubric tightening) is model-level tuning, ruled out of scope for this project. This is the accepted ceiling of what the answer cache can do for fairness, not an intermediate step toward a fuller fix.
- **BUG-025's other half** (retrieval-pool variance — the correct candidate simply wasn't surfaced that run) is the piece this cache directly targets and has already live-validated (S9), and should keep working automatically as the cache accumulates further consensus from live traffic — no further engineering is required for that half to keep improving on its own.
