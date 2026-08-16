# Horizon User Journey v2.0

## Purpose

User Journey v2.0 separates banking-topic retrieval from response presentation.
Product, intent, and free-form task queries determine what FAQ knowledge is retrieved. The presentation type is selected only after task-level evidence has passed sufficiency.

## Presentation types

- `TEXT` — factual answer, explanation, or concise topic sections.
- `PROGRESS` — one supported, multi-step FAQ procedure displayed as an expandable progress payload. All grounded step details are delivered together; the first step is expanded initially and customers may open any other step.
- `LIST` — a titled checklist of three or more independent, grounded items, such as eligibility criteria, required documents, prerequisites, fees, limits, or security checks. Each item has a green check marker.
- `ESCALATION` — human assistance is required or explicitly requested.

Clarification is response content, not a presentation type. It can be appended to `TEXT`, `PROGRESS`, or `LIST` while retaining its separate groundedness and compliance checks. Only one interactive presentation may be active in a response. Multiple business tasks are allowed.

## Presentation decision principle

The planner visualizes the mental model the customer needs at that moment. A need to understand uses `TEXT`; a need to scan or remember independent items uses `LIST`; and a need to know where they are in, or complete, a process uses `PROGRESS`. Supported choices may be appended as up to two chips when the customer needs to choose a next action or topic. A comparison remains organised `TEXT` until a dedicated comparison component is introduced.

When multiple tasks are supported, `LIST` and `PROGRESS` have one primary task only. Their title and items or stages must be derived only from that primary task's approved evidence. Other supported tasks are displayed as separate supporting text rather than mixed into the primary payload.

The customer’s higher-level intent selects the presentation, while approved FAQ evidence constrains every displayed fact. Account-opening and application/onboarding requests therefore prefer `PROGRESS` when the evidence supports at least two honest actions, prerequisites, or constraints that can be expressed as preparation stages. The planner must not invent app navigation or represent preparation as a complete bank workflow.

## Execution flow

```text
Inbound safety
  → request triage
  → formulate up to three task-id-labelled retrieval tasks
  → deterministic product-and-intent evidence-cache comparison, per task
  → database parent-ID fetch for cache hits; tri-angle RRF only for misses
  → batched task sufficiency and parent-ID evidence selection
  → retry only retryable tasks, up to four genuine RAG attempts per task
  → turn terminally unresolved tasks into safe clarification instructions
  → User Journey Planner
  → response generator and deterministic UI payload
```

## Evidence contract

Each RAG task retains the selected parent documents as `{parent_id, content}`. The task sufficiency evaluator returns only approved parent IDs that were supplied for that same task. The backend validates IDs deterministically before forwarding the matching content to the User Journey Planner or response generator.

Successful task evidence is never overwritten by a retry for another task. A task
that remains unsupported after four attempts is marked
`CLARIFICATION_REQUIRED`. The exact SQLite parent records returned by that
task's final failed retrieval attempt are passed downstream only as
suggestion-only clarification evidence. The generator may use them to offer up
to two related topics, but may not use them as an answer, instruction, product
claim, or UI action. It produces a natural clarification without quoting the
internal retrieval feedback or asserting unsupported banking facts. Answer text
and answer-related UI options remain grounded in approved FAQ evidence;
clarification is separately checked for suggestion-evidence grounding,
connection to the unresolved customer term, compliance, and safety. A terminal
clarification response does not create UI options, including when another task
has approved answer evidence.

### Intent-Formulation Clarification (Clarification v2.0)

Node 02e may return valid `search_tasks` and user-derived
`intent_clarification_tasks` in the same turn. An intent clarification task is
used only when a component of a valid banking request cannot be safely mapped
to a Product and Intent retrieval task. It retains the exact unresolved
customer wording and an internal reason; it never guesses a product merely to
create a search task.

Durable memory is advisory only. Node 02e must not revive a clarification from
a prior-session or baseline summary unless the latest user input explicitly
continues that topic. This scope is guided by the intent-formulator prompt,
rather than a brittle deterministic text-match rule.

Searchable tasks continue through RAG and task-level sufficiency normally.
Intent clarification tasks bypass RAG and travel with any approved evidence to
the presentation and response layers. The generator answers supported tasks,
then names each unresolved customer term explicitly and asks for clarification.
It must not replace the term with “that”, “it”, or an internal label. Any
unresolved task disables UI options for the response.

The semantic gate validates answer grounding against approved evidence,
retrieval-based suggestions against rejected retrieval evidence, and
intent-level clarification terms against the original user request. If three
clarification rewrites fail, the deterministic fallback preserves an
independently safe answer. When no retrieval task could be formed at all, it
uses a distinct full-intent fallback that asks for the product or service and
the information the customer wants.

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
| All tasks factual and sufficient | `TEXT`, grouped by task topic when useful. |
| One task has grounded sequential steps | `PROGRESS` for that task, with supporting factual topics in the response. |
| Account opening or application asks how to proceed | Prefer `PROGRESS` when approved evidence supports at least two honest preparation stages. |
| Three or more independent requirements or review items | `LIST` with a concise dynamic title and green check markers. |
| Two or more tasks could support progress processes | Select the single most helpful `PROGRESS` process from the customer context; present other supported topics as concise supporting information rather than asking the customer to choose a workflow. |
| A task is terminally unresolved after retries | Answer supported tasks, then provide a safe, connected clarification; do not include UI options. |

## Removed v1 behavior

V2.0 removes product/topic-labelled user journeys, `pending_journey_queue`, `pending_journey_switch`, and cross-turn pause/resume confirmation. A new substantive request replaces the active guided plan; the customer can return to another topic naturally through conversation.

## Observability and safety

The system log records task formulation, evidence-cache hits/misses/promotions,
retrieval completion, sufficiency outcome, retry/unavailable decisions,
approved parent IDs, and the final presentation decision. Existing trusted-input,
Prompt Guard, outbound groundedness, precision substitution, and memory-safety
controls remain in force.
