# Automated Metrics Harness Guideline v1.0

## 1. Purpose

This guideline defines how agents must design, run, assess, and report
automated evaluation of the Horizon Assistant. The primary harness exercises
the real compiled production graph and its real model, RAG, safety, and tool
contracts—not mocks of individual LLM nodes. Its purpose is repeatable
performance measurement and regression detection without contaminating
ordinary customer-like sessions, memories, or operational records.

This is an evaluation framework, not an implementation specification. Agents
may choose the runner language and storage format, but must preserve the
contracts and controls below.

## 2. Core principles

1. **Real pipeline, isolated execution.** The default research runner imports
   and invokes the same compiled `app_graph` and initial state contract used by
   `/api/chat`; it uses real Gemini/Groq calls, vector retrieval, safety gates,
   and registered tools. It does not mock nodes or substitute their results.
   It deliberately does not call persistence helpers. A separate API mode is
   used only when HTTP/frontend/persistence behaviour itself is under test.
2. **Behaviour over exact prose.** Because model wording is non-deterministic,
   assertions target capability, safety, evidence, tool selection, and response
   quality—not exact response strings.
3. **Traceable runs.** Every result must be attributable to the source case,
   source revision, model configuration, knowledge-base version, and system-log
   trace.
4. **No hidden state.** A test must declare whether it is single-turn,
   multi-turn, session-memory, or cross-session-memory. Fresh state is the
   default.
5. **No production side effects.** Tests must not access real customer data or
   make a real-world change. The default direct-graph mode never calls chat or
   memory archival helpers, so it does not write customer-facing chat/ledger
   rows. API, deeplink, and service-request testing remains within the
   project's registered mock/test contracts.
6. **Human review remains authoritative.** Automated rules and LLM judging
   triage results efficiently; they do not replace review of safety-critical,
   disputed, or novel failures.

## 3. Scope and non-goals

The harness evaluates the deployed MVP behaviour, including:

- inbound deterministic screening, Prompt Guard, and triage;
- tool selection and tool-result integration;
- RAG retrieval, RRF ranking, retry/extra-effort stages, and sufficiency;
- approved-evidence-only response generation and groundedness/compliance;
- user-journey payloads, forms, opaque deeplinks, and escalation confirmation;
- rolling/session memory only when a case explicitly requires it;
- latency, errors, retries, and operational outcomes.

It must not:

- call model nodes directly in place of the API for a primary measurement;
- treat a model's fluent wording as proof of factual correctness;
- infer performance from one anecdotal trace;
- mix evaluation traffic into ordinary named users or demo sessions.

## 4. Research directory and artifact convention

Every study lives under a self-contained dated directory:

```text
research/YYYY-MM-DD_short_study_name/
  data/       # external or source datasets; do not modify production data
  fixtures/   # versioned query sets, mappings, expected outcomes
  scripts/    # standalone harness and report-generation scripts
  results/    # append-only JSONL/raw output/progress artifacts
  reports/    # human-readable methodology, findings, and limitations
```

This matches the existing retrieval-refinement and live-validation studies.
Nothing under `research/` is imported by the production chatbot at runtime.
The root `research/README.md` is an index of completed or active studies; add
or update an entry only when its report accurately states the study status.

### 4.1 Shared framework

Use `research/_framework/` for recurring live-validation mechanics. It has:

- `create_study.py` to create the standard dated study layout, `study.json`,
  JSONL fixture, and report skeleton;
- `run_study.py` to validate a manifest and invoke the real compiled graph;
- `common.py` for request context, unique sessions, safe state collection,
  quota-aware pacing/retry, JSONL/progress/report output, and stable checks.

Run it from the repository root:

```bash
python3 -m research._framework.create_study 2026-09-05_example_study
python3 -m research._framework.run_study research/2026-09-05_example_study/study.json --dry-run
python3 -m research._framework.run_study research/2026-09-05_example_study/study.json
```

`graph_validation` is the only implemented shared adapter. This is deliberate:
it reuses the proven September direct-graph design without pretending that an
HTTP/persistence/frontend study is equivalent. A future API adapter must be
implemented as a separate, explicitly named runner with namespaced evaluation
users and persistence controls.

Existing live studies may keep a small `TEST_QUERIES` list in their runner for
reproducibility. New or growing suites should prefer a separately versioned
fixture in `fixtures/`, so query changes can be reviewed without changing the
runner's logic.

## 5. Test corpus contract

### 5.1 Versioned corpus

The corpus is a version-controlled JSONL or JSON file. Each case has a stable,
never-reused `case_id`. Changes to a case are a deliberate new version, not a
silent overwrite of a historical benchmark.

Minimum case shape:

```json
{
  "case_id": "rag_loan_application_001",
  "title": "Loan application procedure",
  "messages": [
    "How do I apply for a loan in the app?",
    "What documents do I need?"
  ],
  "mode": "multi_turn_same_session",
  "tags": ["rag", "personal_loan", "application"],
  "expected": {
    "must_use_tools": ["rag"],
    "must_not_use_tools": ["service_request"],
    "must_not_escalate": true,
    "must_not_emit": ["raw_route", "destination_id", "unresolved_placeholder"],
    "required_topics": ["application", "eligibility_or_documents"]
  },
  "review_rubric": ["answer_relevance", "evidence_groundedness", "naturalness"]
}
```

`expected` fields are optional where a scenario intentionally tests failure,
clarification, decline, or escalation. A test must say what *is* expected
instead of relying on an implicit happy path.

For a multi-turn case, shared `expected` assertions apply to the final turn by
default. Set `"assert_each_turn": true` only when each individual turn must
meet the same assertion set; otherwise early contextual setup turns would
produce misleading failures.

### 5.2 Required categories

Maintain a balanced corpus across these categories:

| Category | Required examples |
|---|---|
| RAG retrieval | direct retrieval, metadata expansion, extra effort, no-answer |
| Multi-task | two or more banking needs in one message; partial success |
| Context | continuation, correction, pronoun/reference, session memory |
| Tool calling | RAG, API, deeplink, service request, mixed-tool turns |
| Response UX | text, list, progress, form, link, clarification, decline |
| Safety | prompt injection, sensitive-word disclosure, prohibited request |
| Non-financial | pure polite conversation and mixed financial/non-financial input |
| Escalation | explicit human request and confirmation flow |
| Reliability | model-rate-limit simulation where available, unavailable tool, retries |

Every confirmed production-like defect must become a regression case before
the fix is considered complete.

### 5.3 Test modes

- `single_turn_fresh_session`: one message in an empty evaluation session.
- `multi_turn_same_session`: messages sent sequentially to one fresh session.
- `session_memory`: checkout, re-initialize, then test last-session recall.
- `baseline_memory`: only for deliberate multi-session baseline tests.
- `tool_contract`: validates registered mock API, deeplink, or service flow.

Do not use memory-bearing modes by default. They are slower, more variable,
and must declare setup/teardown steps explicitly.

## 6. Isolation and safety rules

### 6.1 Evaluation identities

Direct-graph research runs must use a unique `thread_id`/`session_id`, for
example:

```text
fullval_{case_index}_{random_suffix}
```

These runs may use the existing simulator user (`USR_999`) only because the
harness does not call `archive_session_history` or memory persistence helpers.
This must be demonstrated by a before/after row-count check when a new harness
is introduced.

HTTP/API or persistence-mode runs must instead use a namespaced evaluation
user and session, for example `EVAL_{run_id}_{case_id}`. They must never use
ordinary demo or human identities, because those modes may persist chat,
memory, request, or link records.

### 6.2 Data and tool isolation

- The knowledge base is read-only for a benchmark run. Record its DB/vector
  revision or checksum in the run manifest.
- Service requests must use test-only identities and must not represent a real
  submission.
- Deeplink cases validate the opaque `linkId` and central redirect contract;
  they must not expect a route inside a chat payload.
- API cases must use the simulator/test ledger only.
- Never put credentials, personal data, or secrets in corpus messages or
  artifacts.

### 6.3 State lifecycle

The runner must record whether it:

- created a fresh session;
- loaded prior messages;
- invoked session checkout;
- waited for memory compaction/summary completion;
- cleaned test records, or intentionally retained them for a memory case.

State reset is part of the test protocol, not an assumed implementation detail.

## 7. Run protocol

1. Create a `run_id` and immutable run manifest before sending requests.
2. Capture the application Git commit, corpus version, configuration/model
   identifiers, environment, and knowledge-base revision.
3. For each case, create the declared isolated graph state and request context.
4. In default research mode, invoke `app_graph.ainvoke(...)` using the same
   state shape and thread configuration as the API gateway. Do not invoke node
   functions individually. Use the HTTP API only for an explicitly declared
   endpoint/persistence/frontend study.
5. Await the complete response and any declared background action before the
   next message. Respect provider rate limits and use bounded concurrency.
6. Record API payloads, response blocks, UI payloads, timing, and correlation
   identifiers. Do not persist secrets.
7. Extract the correlated system-log events for only that case/session.
8. Run deterministic assertions, then rubric/LLM evaluation, then queue any
   disputed or high-risk case for human review.
9. Write a run-level report. Never overwrite an earlier run artifact.

The runner should use low concurrency initially (one active graph invocation
per model provider). The established live-validation practice is a 25-second
pacing gap between turns. On a Gemini `429`, inspect the structured error:
wait for `RetryInfo.retryDelay` plus a small buffer for per-minute limits;
stop the run on a daily `QuotaFailure`; and cap per-case retries. Record each
rate-limit event rather than hiding it. Uncontrolled parallelism makes model
and retrieval results uninterpretable.

### 7.1 Standard live-validation outputs

Follow the existing live-study convention unless a study has a justified
alternative:

```text
results/test_results.jsonl  # one complete, structured final-state record per case
results/progress.txt        # timestamped progress, waits, stops, and completion
results/run_stdout.log      # optional redirected terminal output
reports/RESEARCH_SUMMARY.md # method, result table, source review, findings, limits
```

`test_results.jsonl` should expose the state required to audit the whole turn,
including triage result, financial subqueries and tool attempts, task
statuses, approved parent IDs, clarification/decline tasks, response blocks,
UI actions, precision/personal-data substitutions, and safety retry count.
The existing `2026-09-04_full_system_validation` runner is the reference
example for this extraction shape. A report must distinguish an observed
mechanical result from a source-verified content-correctness conclusion.

### 7.2 System-log correlation

The direct-graph runner and the running backend may write to the same
structured system log. Set and reset a request context around every graph
invocation, including the unique session ID and test user ID. Extract only the
matching session/request events into the study artifact; never infer a case
trace from neighbouring log entries. This preserves the current ability to
investigate an individual turn without copying the complete operational log.

## 8. Evidence capture contract

Every case artifact must contain:

```text
run_id / case_id / attempt number
request and response timestamps
sanitized initial graph state and final graph state (or API request/response
when API mode is explicitly used)
response blocks and UI payloads
selected tools and terminal tool states
RAG task IDs, retrieval stages, approved parent IDs, and sufficiency verdicts
outbound deterministic/compliance/groundedness verdicts and retry counts
latency by observable phase and total latency
correlated structured system-log excerpt
deterministic assertion outcomes
LLM-judge/rubric outcomes, model and prompt version
human-review outcome, if required
```

Raw rejected evidence may be stored only in access-controlled internal research
artifacts. It must never be copied into customer-facing reports, response
fixtures, or prompts outside the RAG retry boundary.

## 9. Metric framework

### 9.1 Operational metrics

Measure per case and aggregate by category:

- end-to-end latency: median, p95, p99;
- API/system error rate;
- provider rate-limit/error rate;
- mean LLM/tool calls and RAG attempts per completed turn;
- retry distribution by stage;
- escalation, clarification, decline, and fallback rate;
- tool selection and unavailable-tool rate.

### 9.2 Retrieval and evidence metrics

- task formulation validity (product/intent/filter validity);
- retrieval candidate relevance, when labelled;
- sufficiency distribution: sufficient / partial / insufficient;
- approved-parent relevance and coverage of the customer need;
- extra-effort trigger rate and incremental recovery rate;
- clarification rate after maximum RAG effort;
- groundedness pass rate and the reason for every failure.

### 9.3 Response-quality metrics

Assess with a stable rubric:

1. addresses the actual current request and relevant context;
2. makes only supported banking claims;
3. communicates partial evidence and uncertainty honestly;
4. uses an appropriate, comprehensible UI payload when useful;
5. is natural, concise, and non-mechanical;
6. handles non-financial remarks politely without making material claims.

Report each dimension separately. Do not collapse a response that is fluent but
ungrounded into a single "good" score.

### 9.4 Safety metrics

- sensitive-word veto recall on the controlled safety corpus;
- Prompt Guard rejection/fail-closed outcomes;
- prohibited-request routing correctness;
- leakage rate for placeholders, routes, destination IDs, or unapproved facts;
- compliance and groundedness false-positive / false-negative review counts.

## 10. Assessment method

### 10.1 Deterministic checks first

Use deterministic assertions for stable properties: no HTTP failure, expected
tool route, schema validity, no raw link route, no unresolved placeholder,
expected terminal state, and absence/presence of escalation where specified.

### 10.2 LLM-as-judge second

An LLM judge may assess relevance, adequacy, naturalness, and evidence support
only after receiving the customer request, context allowed by the case, the
candidate answer, and the approved evidence. It must not be given rejected
retrieval evidence as answer support.

Use a structured rubric and retain the judge model/prompt version. Where
possible, evaluate the same evidence order for competing variants; otherwise
judge randomness can appear as a retrieval improvement.

### 10.3 Human adjudication

Mandatory human review applies to:

- safety/compliance failures;
- materially different competing variants;
- judge disagreement or low confidence;
- new capability categories;
- a representative sample of each benchmark run.

Human assessment should mark `pass`, `partial`, `fail`, or `needs policy
decision`, plus a short reason and any replacement expected behaviour.

## 11. Comparison and regression policy

Compare only runs with a compatible corpus, knowledge-base revision, model
configuration, and environment. If any differ, label the comparison
exploratory rather than causal.

For a prompt, RAG, model, or orchestration change:

1. Run the smoke suite before merging.
2. Run the regression suite against both baseline and candidate when feasible.
3. Compare aggregate metrics **and** paired case-level outcomes.
4. Investigate regressions in safety, evidence grounding, direct-answer rate,
   p95 latency, or error rate even if the overall average improves.
5. Promote material failures to permanent regression cases.

Never claim an improvement from a small sample, unreviewed judge score, or
different evidence set without disclosing those limitations.

## 12. Suggested suite cadence

| Suite | Typical size | When to run | Purpose |
|---|---:|---|---|
| Smoke | 15–25 cases | after significant local changes | Catch broken routing, schemas, and safety regressions quickly |
| Regression | 80–150 curated cases | before a demo/release | Measure cross-capability quality and compare versions |
| Deep RAG | targeted labelled cases | after retrieval/index/prompt changes | Inspect retrieval, RRF, sufficiency, and extra effort |
| Reliability | controlled longer/concurrent cases | periodically | Measure latency, provider failure, memory, and recovery |
| Exploration | new/failed real-test cases | continuously | Discover gaps; promote confirmed cases to regression |

## 13. Run report template

```markdown
# Evaluation Run: {run_id}

- Application commit: {git_sha}
- Corpus/version: {corpus_id}
- Knowledge base revision: {db_or_index_id}
- Models/configuration: {model_summary}
- Environment: {environment}
- Started/completed: {timestamps}

## Aggregate results

| Metric | Result | Baseline | Delta |
|---|---:|---:|---:|
| Case pass rate | | | |
| Groundedness pass rate | | | |
| Safety-routing pass rate | | | |
| Median / p95 latency | | | |
| Mean RAG attempts | | | |
| Escalation rate | | | |

## Material failures

| Case | Category | Observed result | Expected result | Trace/artifact |
|---|---|---|---|---|

## Interpretation and next actions

- What improved, with the supporting paired evidence.
- What regressed, including safety and latency effects.
- Cases requiring human/policy review.
- New regression cases to add.
```

## 14. Agent checklist

Before running or changing the harness, every agent must confirm:

- [ ] The corpus and run have unique versioned identifiers.
- [ ] Evaluation user/session namespaces are isolated.
- [ ] The intended state mode is declared.
- [ ] The app commit, model config, and knowledge-base revision are captured.
- [ ] The default run invokes the compiled `app_graph`, not individual node
  shortcuts or mocked results; API mode is explicitly declared when used.
- [ ] Test tools cannot create real customer or external side effects.
- [ ] Every failure has a trace artifact and is classified before conclusions.
- [ ] New confirmed defects are added to the regression corpus.
- [ ] Generated artifacts are not accidentally committed with production code
  unless the commit explicitly concerns evaluation data.

## 15. Relationship to existing architecture documents

Use this guide alongside:

- `End-to-End Process Specification.md` for graph routing;
- `RAG Specification.md` and `Search Strategy Specification.md` for
  retrieval and retry interpretation;
- `TOOL_CALLING_v3.0.md` for tool-task/result contracts;
- `GROUNDEDNESS_v2.0.md` for outbound audit interpretation;
- `MEMORY_ARCHITECTURE_v3.0.md` for memory-specific cases;
- `DEEPLINK_DESIGN_v1.0.md` and `SERVICE_REQUEST_DESIGN_v1.0.md` for
  registered tool contracts.

When this guide conflicts with an implementation contract, record the conflict
in the run report and update the documents before treating the result as a
formal benchmark.
