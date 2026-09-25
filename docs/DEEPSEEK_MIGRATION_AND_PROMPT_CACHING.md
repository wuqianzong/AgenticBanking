# DeepSeek Migration and Prompt Caching (2026-09-25)

This document records why the Horizon assistant's LLM calls moved from free-tier
Gemini to paid DeepSeek, how the migration was implemented and validated, and how
restructuring the prompts for DeepSeek's prefix cache cut the running cost by about
60%. All numbers are measured from the system log (`llm.usage` events) unless marked
as an estimate.

## 1. Why migrate

The free Gemini tier (`gemini-3.5-flash-lite` / `gemini-3.1-flash-lite`) repeatedly
returned `503 UNAVAILABLE -- high demand` for hours at a time. On 2026-09-24 every key in
the 10-key pool was affected; single turns took up to 4 minutes because each call waited
out 45-second timeouts, and many live checks could not run at all (tracked as BUG-030).
A paid DeepSeek account has no daily quota and a per-account concurrency cap of 2,500
requests for `deepseek-flash`, which removes this failure mode.

## 2. What DeepSeek offers (from the official docs)

| Item | deepseek-flash (DeepSeek-V4.1-Flash) |
|---|---|
| API | OpenAI-compatible, `https://api.deepseek.com` (`/beta` for strict tool calling) |
| Context / max output | 1M tokens / 384K tokens |
| Input price, cache miss | ¥2 per 1M tokens (¥1 off-peak) |
| Input price, cache hit | ¥0.04 per 1M tokens (¥0.02 off-peak) -- about 1/50 of a miss |
| Output price | ¥8 per 1M tokens (¥4 off-peak) |
| Off-peak | 50% off outside Beijing 09:00-12:00 and 14:00-18:00 on weekdays |
| Limits | no daily quota; 429 only above 2,500 concurrent requests |
| Errors | 402 insufficient balance, 429 concurrency, 500 server error, 503 server busy |

## 3. Implementation

All changes are in `backend/app/core/llm_service.py`; nodes call the same router as
before. Gemini and Groq remain fully supported.

- **Provider by model id.** `gemini-*` goes to Google, `deepseek-*` to DeepSeek, the prompt
  guard and other ids to Groq. Each role (generator, auditor, planner/triage) is set
  independently and can be overridden per process with `HORIZON_MODEL_A/B/C`, e.g.
  `HORIZON_MODEL_A=gemini-3.5-flash-lite` for a validation run while the live backend
  stays on DeepSeek. All roles now default to `deepseek-flash`.
- **Thinking mode off.** DeepSeek's thinking mode is on by default and ignores
  `temperature`; every call sends `thinking: disabled`, `temperature: 0` and an explicit
  `max_tokens`. The SDK's own retries are disabled so the router keeps control.
- **Schema-enforced output.** Gemini enforces a Pydantic `response_schema`. DeepSeek offers
  only JSON mode (no schema) or *strict tool calling* (beta). Schema'd calls are sent as a
  single forced tool call whose parameters are the converted Pydantic schema: every
  object fully required with `additionalProperties: false`, `$ref`s expanded in place,
  `const` turned into `enum`, `title`/`default` removed.
- **Our own validation.** Live probes showed strict mode checks the schema's syntax but
  **does not enforce it on the output**: an out-of-enum value, an extra property and a
  `null` required object all came back, and a `$ref` inside `anyOf` is rejected. Every
  schema'd reply is therefore validated against its Pydantic model and checked for
  omitted fields (a field whose default is `""`/`null` may be omitted). On failure the
  call is re-asked once with the exact errors; a second failure falls back to the
  node's normal error path.
- **Errors.** 402 (balance) and 503 (busy) end the turn with "The model is currently
  unavailable. Please try again later." (the BUG-030 rule); 429 and 5xx get one retry;
  Gemini key rotation is not triggered by a DeepSeek 429.
- **Key storage.** `DEEPSEEK_API_KEY` or the git-ignored `.deepseek_api_key` file -- never
  `.env`, which is tracked in git.
- **Usage logging.** Every call logs exact prompt, cache-hit, cache-miss and output tokens
  (`llm.usage`) for both DeepSeek and Gemini.

## 4. Validation of the migration

- All 29 Pydantic schemas (including the tool planner's per-call schema) accepted by
  DeepSeek and every reply valid; median 1.4s, max 4.5s.
- The latest real prompt of every call type replayed on DeepSeek: 0.7-4.8s per call
  (the logged Gemini times for the same prompts were 1.1-22s, partly during spikes).
- 10 BANKING77 cases compared with their Gemini answers: DeepSeek better in 5, similar in
  1, worse in 3, identical in 1; turns took 5-28s (Gemini baselines 17-53s); no audit
  revision rounds were needed.
- DeepSeek answered at noticeably greater length. A shared RESPONSE LENGTH principle
  (related context gets its most useful point in one or two sentences; direct answers
  stay complete) shortened the three longest replies from 225/292/345 to 130/194/216 words.

## 5. Prompt caching

### How the cache works
DeepSeek remembers the beginning of recent prompts. A new prompt that **starts with** the
same text is billed at the cache price for that shared opening; everything after the
first difference is billed at full price, in 64-token blocks. Identical text later in a
prompt does not count if something earlier differs.

### Starting point (baseline, 3 BANKING77 turns)

| Call | Prompt tokens | Cache hits |
|---|---|---|
| Sufficiency auditor | 77,988 | 10% |
| Tool planner | 30,052 | 26% |
| Response generator | 26,606 | 0% |
| Intent formulator | 24,512 | 0% |
| Combined audit | 23,780 | 0% |
| Triage | 14,092 | 0% |
| Consistency check | 4,567 | 0% |
| **Total** | **201,597** | **8%** |

Cost: ¥0.476 for the 3 turns at peak prices (¥0.159 per turn); cache-miss input was 78%
of it.

Two causes were found:

1. **Per-user cache isolation.** The code sent a hashed session id as DeepSeek's
   `user_id`, and DeepSeek keeps a separate cache per `user_id`. Measured on the same
   2,008-token prompt: **89% cache hits without `user_id`, 0% with one**. Every new
   session started cold. As a single-user school MVP, the `user_id` was removed so the
   cache is shared.
2. **Data before rules.** In most prompts the customer's message or data appeared within
   the first 1-9% of the text, so the long fixed rules after it could never be cached:

   | Prompt | Identical opening before the change |
   |---|---|
   | Triage | 9% |
   | Tool planner | 7% |
   | Intent formulator | 1% |
   | Sufficiency auditor | 1% |
   | Combined audit | 13% |
   | Response generator | 23% |
   | Consistency check | 55% |

### The change
Every prompt now puts its fixed rules first, then one shared line
("THE RULES ABOVE ARE FIXED. EVERYTHING BELOW IS SPECIFIC TO THIS CALL -- its data, and
any rules that apply only to this call."), then that call's own rules and data. The
generator's turn-specific rule sections come after its fixed principles. No rule wording
changed apart from correcting "above/below" references.

This applies to **all 15 LLM prompts** (only the Groq prompt guard is not a DeepSeek
call): the seven main prompts (triage, intent formulator, tool planner, sufficiency
auditor, combined audit, response generator, consistency check) and, in a follow-up
change, the eight smaller ones -- reviser, statement composition, login follow-up check,
the shared evidence and conduct audits in `groundedness.py` (used for greetings, memory
and escalation summaries), escalation case summary, Territory B retry, and memory
summary. The marker text is one shared constant, `compliance.DATA_FOLLOWS`. The eight
smaller prompts were confirmed live on DeepSeek: login follow-up, statement composition,
escalation summary and its two audits fired in a full API session (81% cache hits);
memory summary, reviser and Territory B were exercised with real inputs.

### A side effect found and fixed: the tool planner
With the reordering alone, the tool planner stopped planning an FAQ search for borderline
problem reports, and two of three test cases returned an empty answer. A controlled test
on one case (identical wording, 10 runs per variant) isolated it:

| Planner prompt variant | FAQ search planned |
|---|---|
| Old order (customer data near the top) | 8 / 10 |
| New order (rules first) | 0 / 10 |
| New order, without the marker line | 1 / 10 |
| New order + an overview paragraph | 1 / 10 |
| New order, registry moved to the end | 4 / 10 |
| New order + tool definitions repeated after the data | 3 / 10 |
| New order + a fixed format example | 4 / 10 |
| New order + a fixed worked example | 5 / 10 |
| **Rewritten, clearer planner prompt, new order** | **23 / 23** (three previously failing cases) |

Other prompts (triage, sufficiency auditor) showed no change with the new order. The
cause was the planner prompt's unclear meaning -- no stated purpose, "information" defined
only by question form, no default for a need no specific tool covers, and three rules
that allowed returning nothing -- which let reading order tip a borderline decision. The
rewrite states the purpose, makes FAQ search the default, and groups the decision rules;
controls stayed correct (balance -> data lookup only; change address -> service form +
complementary FAQ search; send money -> app screen).

### Result (same 3 turns, after all changes)

| Call | Cache hits after |
|---|---|
| Triage | 97% |
| Intent formulator | 95% |
| Tool planner | 94% |
| Sufficiency auditor | 81% |
| Response generator | 75% |
| Combined audit | 63% |
| Consistency check | 58% |
| **Total** | **79%** (87,421 of 110,247 prompt tokens) |

### Cost savings
Applying the measured per-call hit rates to the baseline workload (like-for-like):

| | Per turn, peak | Per turn, off-peak | Per 1,000 turns (peak / off-peak) |
|---|---|---|---|
| Before | ¥0.159 | ¥0.079 | ¥159 / ¥79 |
| **After** | **¥0.060** | **¥0.030** | **¥60 / ¥30** |
| **Saving** | **−62%** | **−62%** | |

After the change, output tokens are 57% of the remaining cost; the sufficiency auditor's
per-parent `evidence_delta` is the largest remaining lever.

Gemini's implicit prefix caching was also measured with the same layout: 0 cached tokens
on the free tier (1.35M prompt tokens in the validation run), which does not affect cost
there.

## 6. Cost of this work
About **¥2** of DeepSeek usage for all testing on 2026-09-25 (estimated; exact token
logging began midway, and most runs fell in Beijing off-peak hours).

## 7. Validating the code changes separately from the model change
To separate the two, the code changes since `6ac3d03` were validated on Gemini -- the same
model as the past research -- with 24 cases / 43 turns
(`research/2026-09-25_code_change_validation_gemini/reports/VALIDATION_REPORT.md`):
8 better, 10 same, 4 mixed, 2 worse than the stored Gemini answers. BUG-017/018/033 are
fixed in real multi-turn sessions; issues found are logged as BUG-040 to BUG-044. The
model comparison (DeepSeek vs Gemini on the same code) is a separate, later step.

## Commits
`9003669` DeepSeek provider · `8eb6e95` own schema validation · `568afbd` all roles to
DeepSeek · `bb67117` RESPONSE LENGTH · `b851221` usage logging · `4846afe` shared cache (no
`user_id`) · `201551b` rules-first layout · `cc99564` planner rewrite · `6ea3874` omittable
fields · `0e24985` per-process model override + Gemini usage logging.
