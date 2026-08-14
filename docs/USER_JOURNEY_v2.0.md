# Horizon User Journey v2.0

## Purpose

User Journey v2.0 separates banking-topic retrieval from response presentation.
Product, intent, and free-form task queries determine what FAQ knowledge is retrieved. The user journey is selected only after task-level evidence has passed sufficiency.

## Journey set

- `INFORMATIONAL` — factual answer, optionally organised into topic sections.
- `GUIDED_PROCESS` — one supported, multi-step FAQ procedure displayed as a progress payload.
- `TROUBLESHOOTING` — supported diagnostic or resolution guidance, optionally with chips.
- `CLARIFICATION` — missing evidence or competing guided procedures; asks the user for direction.
- `ESCALATION` — human assistance is required or explicitly requested.

Only one interactive journey may be active in a response. Multiple business tasks are allowed.

## Execution flow

```text
Inbound safety
  → request triage
  → formulate up to three task-id-labelled retrieval tasks
  → deterministic product-and-intent evidence-cache comparison, per task
  → database parent-ID fetch for cache hits; tri-angle RRF only for misses
  → batched task sufficiency and parent-ID evidence selection
  → retry only retryable tasks, up to three genuine RAG attempts per task
  → turn terminally unresolved tasks into safe clarification instructions
  → User Journey Planner
  → response generator and deterministic UI payload
```

## Evidence contract

Each RAG task retains the selected parent documents as `{parent_id, content}`. The task sufficiency evaluator returns only approved parent IDs that were supplied for that same task. The backend validates IDs deterministically before forwarding the matching content to the User Journey Planner or response generator.

Successful task evidence is never overwritten by a retry for another task. A task
that remains unsupported after three attempts is marked
`CLARIFICATION_REQUIRED`. Its unapproved retrieval content is not sent
downstream; instead, terminal retrieval feedback is sent to the response
generator as internal context. The generator produces a natural clarification
without quoting that feedback or asserting unsupported banking facts. Answer
text and answer-related UI options remain grounded in approved FAQ evidence;
clarification is separately checked for compliance and safety. Clarification
does not create UI options.

### Session evidence cache

The graph keeps a session-scoped, bounded cache of approved parent IDs only. Its
key is the lower-cased commercial product plus the unordered lower-cased intent
set; task order and the three retrieval-angle strings do not affect matching.
For every cache hit, the backend re-reads the parent IDs from the authoritative
SQLite knowledge database and still submits the current task and evidence to
the sufficiency checker. An insufficient cache hit falls back to RAG without
consuming a RAG retry. Cache entries are promoted only after the outbound
semantic gate has approved both the evidence and the response. Cache content is
not restored across login sessions.

## Presentation policy

| Evidence outcome | Presentation |
|---|---|
| All tasks factual and sufficient | `INFORMATIONAL` grouped by task topic. |
| One task has grounded sequential steps | `GUIDED_PROCESS` for that task, with supporting factual topics in the response. |
| Two or more tasks require guided processes | `CLARIFICATION` with up to two user choices; no hidden queue. |
| A task is unavailable after retries | Show a safe availability notice while answering supported tasks. |

## Removed v1 behavior

V2.0 removes product/topic-labelled user journeys, `pending_journey_queue`, `pending_journey_switch`, and cross-turn pause/resume confirmation. A new substantive request replaces the active guided plan; the customer can return to another topic naturally through conversation.

## Observability and safety

The system log records task formulation, evidence-cache hits/misses/promotions,
retrieval completion, sufficiency outcome, retry/unavailable decisions,
approved parent IDs, and the final journey decision. Existing trusted-input,
Prompt Guard, outbound groundedness, precision substitution, and memory-safety
controls remain in force.
