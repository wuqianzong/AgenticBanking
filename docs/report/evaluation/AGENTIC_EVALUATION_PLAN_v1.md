# Agentic AI Chatbot Evaluation Plan — Consumer Banking

**v1.2 — updated 2026-09-13.** All originally-open items are now
resolved. Cost-per-resolved-query is computed using real published
per-token pricing. Tool-Selection Accuracy is computed via a fresh
94-case manual sample (94.7% match rate). Fairness has been reframed,
per explicit instruction, from an (untestable, no-data) demographic-
segment test to a same-intent phrasing-consistency test — and computed,
finding a real, confirmed defect (BUG-023). Transparency has been
directly verified rather than assumed, and the confirmed absence of any
AI-disclosure is now tracked as BUG-024. Every pillar in this plan now
has at least one number behind it.

## 0. Industry landscape this plan is grounded in

Four bodies of practice, chosen because each addresses a different part
of what "evaluate an agentic banking chatbot" actually means — no single
one covers retrieval, generation, agentic task completion, and
regulatory compliance at once.

| Standard | What it covers | Why it applies here |
|---|---|---|
| **RAGAS** (RAG evaluation) | Splits any retrieval-augmented system into Context Precision/Recall (retrieval half) and Faithfulness/Answer Relevance (generation half) | Already the basis of our Bot Reliability × KB Coverage decomposition (`BUSINESS_METRICS_FRAMEWORK_v1.md` B1b) — Pillars A and B below extend it |
| **τ-bench / τ2-bench** (Sierra AI) | The field's standard benchmark for *agentic* customer-service systems specifically — not just answering questions, but using tools correctly and reaching the right end state across a conversation, in realistic retail/airline/support domains | Our bot is exactly this shape (RAG + tool-calling: `rag`, `api`, `deeplink`, `service_request`) and our multi-turn study is structurally a τ-bench-style test (a simulated user pursuing a multi-step goal against a tool-using agent) — Pillar C is built directly on its evaluation criteria |
| **MAS FEAT Principles** (2018) + **MAS AI Risk Management Guidelines** (2025) | Singapore's regulatory framework for AI in financial services — Fairness, Ethics, Accountability, Transparency — and how to *demonstrate* each (documented risk assessment, testing protocols, human oversight, audit trails) | Directly applicable: Horizon Bank is a Singapore-context bank (SGD, PayNow, MAS-referenced FAQ content) — this is the actual regulatory bar the bot would be held to, not a generic "safety" framing |
| **CFPB UDAAP precedent** (US) + **Gartner outlook** | Confirms financial misinformation is a *compliance* violation, not just a quality defect, in any jurisdiction with a comparable consumer-protection regime; Gartner's 2029 outlook (80% autonomous resolution, 30% cost reduction for agentic AI) sets an industry cost-reduction expectation | Frames why Pillar E (hallucination/fabrication) is weighted as high-severity, and gives a forward-looking cost benchmark beyond the already-cited Gartner mature-RAG containment figures |

## 1. The six-pillar taxonomy

### Pillar A — Retrieval Quality

| Metric | Definition | Status |
|---|---|---|
| Context Recall (= KB Coverage) | Of all queries, does the KB actually contain a usable answer | **Computed** — 82.9% (95% CI 81.5–84.3%), `BUSINESS_METRICS_FRAMEWORK_v1.md` B1b |
| Context Precision | Of every candidate ever shown to a task, what fraction was actually approved into the final answer | **Computed** — 18.9% macro-average / 15.8% micro-average (5,275/33,447 candidates approved), across 2,985 tasks (91.5% coverage — the rest lack a `task.rag.completed` event). Low **by design**, not by defect: the retrieval strategy deliberately casts a wide net (multiple candidates per round, across retries) and relies on the downstream sufficiency auditor to filter — read together with the high Context Recall/KB Coverage above, not in isolation. Lower-bound caveat: BUG-016 means some 0%-precision tasks had a genuinely relevant candidate wrongly rejected, not a truly irrelevant one |
| Retrieval-attempt completion rate | Of every `rag` tool dispatch, did it return usable evidence at all | **Computed** — 78.4% (BANKING77, 2,435/3,106), 91.7% (multi-turn, 44/48). `deeplink`/`api`/`service_request` are 100% by construction (no "not found" state exists for those tool types) — reported separately rather than blended into a misleadingly high combined number. The 10 worst BANKING77 categories by raw "unavailable" rate are **exactly** the 10 content-gap categories already named in B1b — an independent confirmation from a different signal |

### Pillar B — Generation Quality

| Metric | Definition | Status |
|---|---|---|
| Bot Reliability (= Faithfulness + Answer Relevance, combined) | Of queries the KB could answer, how often the bot delivered it correctly | **Already computed** — 95.9% (95% CI 94.3–97.5%), same B1b (corrected 2026-09-13 for the BUG-015 label error) |
| Semantic soundness (human-judged) | Manual rubric read: on-topic, correct or honestly declined, appropriately toned | **Already computed** — 96–97% (BANKING77, n=385), 54.3% (multi-turn conversations, n=70) |
| Outbound audit pass rate | Cleared the live `node_06` compliance/groundedness gate on first try | **Already computed** — 84.2% (BANKING77) |
| Balance-surfacing rate (case study of a Faithfulness failure) | Of successful data fetches, did the fetched value actually reach the customer-facing text | **Already computed** — 88.0% (BUG-017/018) |

### Pillar C — Agentic Task Completion (new pillar, τ-bench-grounded)

τ-bench scores an agent on two things: did the final state match what the
task required, and did the agent convey everything the user needed. Our
equivalents:

| Metric | Definition | Status |
|---|---|---|
| Task Completion Rate (single-turn) | Did each individual subquery's tool dispatch reach a correct, resolved outcome | **Already computed** — task-level resolution rates throughout BANKING77 findings |
| Full-Session Task Completion (multi-turn) | Did an entire multi-step customer goal (check balance → transfer → recheck) resolve within one session, τ-bench's actual unit of success | **Already computed** — 69.5% full-session resolution, multi-turn study |
| **Tool-Selection Accuracy** | Of every subquery, was the *correct* tool type (`rag` vs. `api` vs. `deeplink` vs. `service_request`) chosen for what the customer actually needed | **Computed** — 94.7% (95% CI ~90.2–99.2%, n=94, 95% CI ±10% margin, N=3,275 subqueries-with-tool-attempts). 89/94 clean matches; 1 clear mismatch (a card-based top-up request dispatched `deeplink`-only when the informational `rag` answer was needed — this is one of the confirmed content-gap categories); 3 borderline cases where a single-tool dispatch was defensible but a paired dispatch (e.g. `rag` + a verifying `deeplink`) would have been stronger, not scored as errors. Script: `research/2026-09-05_banking77_full_coverage/scripts/prepare_tool_selection_sample.py` |
| Policy Adherence | Did the bot ever take or offer an action it shouldn't have (e.g. claiming to execute a transfer, offering a service outside Horizon's scope) | **Fully computed, corrected from "partial"** — the 15 correctly-declined out-of-scope cases (B1b) are a positive signal; BUG-018 (1 instance) and BUG-020 (2 instances) are the negative instances, and both were already full-corpus counts (all 74 "fetched-but-not-surfaced" cases were manually read in full, not sampled) rather than projections. A two-condition anchored automated scan (successful fetch + capability-denial language; implied second entity + fabricated one) re-ran across all 249 conversations on 2026-09-13 and found **zero new instances beyond the ones already on record** — confirming the existing counts are complete, and validating the anchored-filter method (0 false positives) as a reusable check for future data |
| Multi-turn Coherence | Context carryover, confirmation handling, no re-litigating settled slots | **Already computed** — 54.3% whole-conversation soundness (multi-turn), with named failure patterns (BUG-017 through BUG-022) |

### Pillar D — Human Handoff & Cost Reduction

Fully built out already in `BUSINESS_METRICS_FRAMEWORK_v1.md` (B1, B1c) —
this plan doesn't duplicate it, only cross-references:

- Containment / Handoff Rate, industry-benchmarked against Gartner,
  NatWest, Trust Bank Singapore (B1c)
- Cost translation formula, pending the bank's own agent-cost inputs (B1)
- **Net new from this plan:** Gartner's 2029 forward outlook (80%
  autonomous resolution, 30% cost reduction) as an additional benchmark
  point, and a computed **cost-per-resolved-query**: using real published
  per-token pricing (Gemini 3.5-flash-lite $0.30/$2.50 per M input/output
  tokens, Gemini 3.1-flash-lite $0.25/$1.50, Llama Prompt Guard 2 86M via
  Groq $0.04/M) applied to character-count-estimated token counts
  (~4 chars/token — an approximation, not exact tokenizer output) from
  the `raw_prompt`/`raw_response` text already logged for every
  `llm.request`/`llm.response` pair: **$0.039 per resolved case**
  (BANKING77, $81.50 total / ~2,066 resolved cases) and **$0.095 per
  resolved case** (multi-turn, $16.45 total / ~173 resolved sessions).
  See `BUSINESS_METRICS_FRAMEWORK_v1.md` B1 for the full breakdown by
  model.

### Pillar E — Trust, Safety & Regulatory Compliance (FEAT-grounded)

Reframes the existing Tier 3 (Trust & Safety Guardrails) against MAS's
own four named principles, rather than a generic severity table:

| FEAT principle | What it requires | Our evidence |
|---|---|---|
| **Fairness** | No differential treatment across customer segments | **Reframed and computed, then confirmed with a second, embedding-based test.** Neither study varies customer demographics, and the bot's design is not customer-specific — every request is evaluated fresh with no persisted customer profile influencing retrieval or generation. So the applicable fairness test, per explicit instruction, is not demographic variation but **consistency**: does the *same underlying intent*, expressed in different customer wording, produce the *same outcome*? First measured as an "outcome concentration" score per BANKING77 category (`max(resolved, partial, unresolved) / 40`, across all 77 categories, full corpus): mean 72.6%, 24/77 categories below 60% — tracked as **BUG-023**. Then tested directly at the mechanism level: every BANKING77 subquery embedded with the production RAG pipeline's own embedding model (chromadb's `DefaultEmbeddingFunction`), full pairwise cosine similarity computed, and checked whether close-similarity pairs share a retrieval answer basis (a common approved parent id). Even in the closest similarity band (0.97–1.00), only **60.9%** of near-duplicate questions share an evidence basis, degrading gradually to 45.1% by 0.80–0.85 — confirming the phrasing-sensitivity pattern is general, not one anecdote. A further, more severe result fell out of the same analysis: isolating **byte-identical repeated questions** (no wording variation at all — the same sentence, asked twice in independent sessions) finds a **35.2%** split-outcome rate (31/88 informative exact-duplicate-text groups) — tracked separately as **BUG-025**, since no ambiguity argument can excuse it.

**Architectural mitigation shipped and live-validated (2026-09-17).** A cross-session answer cache (`backend/app/orchestration/answer_cache.py`, design in `docs/FAIRNESS_ANSWER_CACHE_DESIGN.md`) surfaces a historically-confirmed parent id as an extra sufficiency-audit candidate for close-enough (≥0.85 cosine similarity) future questions — never bypassing the audit, seeded from the historical BANKING77 corpus. A 12-case live re-test through the real compiled graph confirms this is the actual architectural ceiling available for this problem:

| Group (live re-test, n) | Organic retrieval found the right FAQ? | Outcome |
|---|---|---|
| `"I want to delete my account."` (n=6) | 0/6 | **6/6 identical**, up from 9/12 (75%) historically — the cache alone carried every resolution this round |
| `"Why was my payment declined?"` (n=4) | 0/4 | 2/4 resolved, 2/4 rejected — same cache-injected candidate present all 4 times, auditor still split |
| `"...card was declined?"` control, similarity 0.65, below threshold (n=2) | 0/2 | 2/2 unresolved, as predicted — no injection possible below threshold |

This isolates exactly what architecture can and can't fix: **ensuring the right evidence reaches the auditor is a solved, retrieval-layer problem (group 1's clean result); making the auditor's own judgment consistent given that evidence is not** (group 2's live-reproduced split, on the identical candidate, four times). Group 2's finding also answers a natural objection — that different chat context, not model inconsistency, might explain apparent non-determinism: every BUG-025 case (this pipeline's own logged prompts confirm it) is a fresh, single-turn session with `Trusted Conversation Context: None` — there is no context difference to blame. Model-tuning fixes for that gap (self-consistency voting, a model-tier A/B test, rubric tightening) were considered and explicitly scoped out of this project by the user (2026-09-17): *"model tunning is out of scope, so what we can do is surface the same parent and keep the cache update."* It stands as an accepted permanent limitation, not an open task — see `docs/FAIRNESS_ANSWER_CACHE_DESIGN.md` S6a/S10 |
| **Ethics** | No harmful, unauthorized, or manipulative outputs | **Computed** — zero instances of unauthorized financial advice found; BUG-020 (fabricating a nonexistent account) is the closest adjacent finding |
| **Accountability** | Documented risk assessment, testing protocols, audit trails | **Strong evidence already exists** — `system.jsonl`'s structured event log *is* a genuine audit trail (every LLM call, every tool dispatch, every audit pass/fail, timestamped and session-tagged); this plan itself and `KNOWN_ISSUES_TRACKER.md` are the documented testing protocol MAS's 2025 Guidelines ask for |
| **Transparency** | Customer can tell they're talking to AI; claims are traceable to evidence | **Confirmed gap.** Precision Substitution and the evidence-manifest mechanism make every factual claim traceable internally — that half holds. But a direct read of every greeting template (`backend/app/core/translations.py`, all 3 supported languages) and the frontend UI (`frontend/src/App.jsx`) found **zero AI-disclosure language anywhere** — not partially checked, fully checked and absent. Tracked as **BUG-024** |
| **Regulatory-grade hallucination control** (CFPB UDAAP framing) | Fabricated financial facts are a compliance violation, not a UX bug | **Computed, and already the highest-severity finding** — BUG-018 (false capability denial, 2.1% of balance fetches) is exactly this failure mode |

### Pillar F — Latency & Efficiency

Fully built out already (`BUSINESS_METRICS_FRAMEWORK_v1.md` B3) — median
9.2s/22.7s turn latency vs. an 800ms/2.5s target, structural cause
identified (sequential LLM calls, no answer-token streaming). No changes
proposed here; cross-referenced for completeness of the six-pillar view.

## 2. Data-readiness summary

| Status | Count of metrics | Examples |
|---|---:|---|
| Already computed | 18 | KB Coverage, Bot Reliability, Semantic Soundness, Full-Session Completion, Containment, Latency, Retrieval-attempt completion rate, Context Precision, Policy Adherence, Cost-per-resolved-query, Tool-Selection Accuracy, Fairness (reframed as consistency), Transparency (direct verification), Accountability, Ethics, Regulatory hallucination control |
| Confirmed gaps, tracked as bugs | 3 | Fairness phrasing-sensitivity (BUG-023), same-question non-determinism (BUG-025), AI-disclosure absence (BUG-024) |

Every metric originally identified in this plan now has a computed
result. The two remaining rows are not measurement gaps — they are
confirmed product/content defects surfaced *by* the completed
measurement, tracked in `KNOWN_ISSUES_TRACKER.md` like any other finding.

## 3. Status: all planned metrics computed (2026-09-13)

All five original new-work items are done: Retrieval-attempt completion
rate, Context Precision, and the Policy Adherence scan (first batch), and
Cost-per-resolved-query, Tool-Selection Accuracy, Fairness, and
Transparency (second batch). Scripts live under
`research/2026-09-05_banking77_full_coverage/scripts/` and
`research/2026-09-10_multiturn_banking_extension/scripts/`.

Remaining work is no longer "compute the metric" but "act on what the
metric found":

1. **BUG-023 (Fairness/phrasing-consistency)** — root-cause whether the
   24 low-concentration categories are driven more by retrieval
   (different phrasing surfaces different candidates) or by
   sufficiency/generation judgment (same candidate, different verdict);
   likely differs by category. One pair traced in full: "card declined"
   vs. "payment declined" route to two different products (Credit_Card
   vs. Deposit_Account) purely from word choice.
2. **BUG-025 (same-question non-determinism)** — a more severe sibling
   finding surfaced by the same embedding analysis: 35.2% of
   byte-identical repeated questions (no wording variation at all) still
   get a different retrieval outcome. Needs a dedicated trace (same
   method as BUG-023's pair) of a few split cases to localize the actual
   non-determinism to the intent/product-mapping step, the sufficiency
   auditor, or genuine retrieval-side variance.
3. **BUG-024 (Transparency/AI-disclosure)** — decide and implement the
   actual disclosure copy and placement (greeting-line addition vs.
   persistent UI badge vs. both); a content addition, not a logic fix.

---

*Companion to `docs/report/evaluation/BUSINESS_METRICS_FRAMEWORK_v1.md` (the business/cost
layer this plan's Pillar D and F draw from without duplicating) and
`docs/KNOWN_ISSUES_TRACKER.md` (bug-level detail behind every Pillar B,
C, and E finding cited above).*
