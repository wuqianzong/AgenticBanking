# Horizon User Journey v2.0

## Purpose

User Journey v2.0 separates banking-topic retrieval from response presentation.
Product, intent, and free-form task queries determine what FAQ knowledge is retrieved. The presentation type is selected only after task-level evidence has passed sufficiency.

## Presentation types

- `TEXT` — factual answer, explanation, or concise topic sections.
- `PROGRESS` — one supported, multi-step FAQ procedure displayed as an expandable progress payload. All grounded step details are delivered together; the first step is expanded initially and customers may open any other step.
- `LIST` — a titled checklist of three or more independent, grounded items, such as eligibility criteria, required documents, prerequisites, fees, limits, or security checks. Each item has a green check marker.
- `ESCALATION_CONFIRMATION` — a separate handoff-confirmation block when human assistance is required or explicitly requested.

Clarification is response content, not a presentation type. It can appear after any answer block while retaining its separate groundedness and compliance checks. A response may contain several independently useful blocks; the UI structure is not constrained to the number or order of retrieval tasks.

## Presentation decision principle

The planner visualizes the mental model the customer needs at that moment. A need to understand uses `TEXT`; a need to scan or remember independent items uses `LIST`; and a need to know where they are in, or complete, a process uses `PROGRESS`. Supported choices may be appended as up to two chips when the customer needs to choose a next action or topic. A comparison remains organised `TEXT` until a dedicated comparison component is introduced.

When multiple tasks are supported, the planner and generator organise aggregate approved evidence around the customer’s cognitive need, not a one-task/one-block rule. A block may draw on the union of its internally recorded source tasks; an unmapped block may use aggregate approved evidence. The response must remain coherent, avoid mixing unrelated facts into a misleading title or list, and preserve task provenance internally for auditing.

The customer’s higher-level intent selects the presentation, while approved FAQ evidence constrains every displayed fact. Account-opening and application/onboarding requests therefore prefer `PROGRESS` when the evidence supports at least two honest actions, prerequisites, or constraints that can be expressed as preparation stages. The planner must not invent app navigation or represent preparation as a complete bank workflow.

## Execution flow

```text
Inbound safety
  → request triage
  → formulate task-id-labelled direct retrieval tasks
  → live direct retrieval, with qualifying verified session evidence joining first-pass RRF
  → batched three-way sufficiency and parent-ID evidence selection
  → retry only unresolved tasks: intent scope, product scope, then angle group
  → retain partial evidence; turn wholly terminal tasks into clarification context
  → flexible UX guidance planner
  → coherent multi-block response generator and deterministic UI payload
```

## Evidence contract

Each RAG task retains the selected parent documents as `{parent_id, content}`. The task sufficiency evaluator returns only approved parent IDs that were supplied for that same task. The backend validates IDs deterministically before forwarding the matching content to the User Journey Planner or response generator.

Successful task evidence is never overwritten by a retry for another task. A
`PARTIALLY_SUFFICIENT` task retains its selected parent IDs while the unresolved
portion retries; at the final attempt it remains answerable for that supported
portion and carries clarification context. A wholly unsupported task after four
attempts is marked `CLARIFICATION_REQUIRED`. The exact SQLite parent records
returned by that task's final failed retrieval attempt are passed downstream only as
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

### Verified session evidence re-ranking (Search Strategy v2.7)

The graph retains a bounded record of approved parent IDs and their search-task
metadata for the latest ten verified session turns. It is not a response cache.
For a new task’s first direct live retrieval only, deterministic code finds
prior records with overlapping normalized product and intent scope, rehydrates
their authoritative SQLite parents, and lets them participate in the same RRF
candidate ranking as live results. They remain candidates only: current-turn
sufficiency must approve them before they can support a response. The record
stores no hydrated parent text, unapproved candidates, drafts, or cross-session
state, and is cleared with the graph session.

At logout or timeout, the gateway archives the durable transcript and memory
summaries first, then deletes the LangGraph checkpoint thread. This releases
ephemeral graph state, including verified session evidence history, while
refreshes and normal turns retain the active session state.

### Search Strategy v2.7

The resolved task subquery is the default vector query. Attempts one through
three use that direct query while the metadata filter expands in a fixed order:
initial scope, then additive intent expansion, then additive product expansion.
Only the fourth attempt uses complete procedural, policy, and conceptual angle
groups. Product and intent remain one shared matrix-valid metadata filter.

On the final angle attempt, RRF fuses the ranked lists within each angle group
and combines group scores with equal normalisation at task level. The engine
then retains the parent-selection contract: rank one is selected, later
candidates stop at a greater-than-50% score drop from the preceding selected
candidate, and no more than four parents are hydrated. There is no cosine-score
direct-hit bypass.

## Presentation policy

| Evidence outcome | Presentation |
|---|---|
| All tasks factual and sufficient | Natural text and optional UI blocks organised for comprehension, not necessarily by task topic. |
| Supported sequential process | `PROGRESS` when stages genuinely reduce cognitive load; supplementary grounded text may appear in the same or another block. |
| Account opening or application asks how to proceed | Prefer `PROGRESS` when approved evidence supports at least two honest preparation stages. |
| Three or more independent requirements or review items | `LIST` with a concise dynamic title and green check markers. |
| Several supported topics | Use one or more response blocks only when each makes the answer easier to understand; never force retrieval-task-shaped blocks. |
| A task is terminally unresolved after retries | Answer supported tasks, then provide a safe, connected clarification; do not include UI options. |

## Removed v1 behavior

V2.0 removes product/topic-labelled user journeys, `pending_journey_queue`, `pending_journey_switch`, and cross-turn pause/resume confirmation. A new substantive request replaces the active guided plan; the customer can return to another topic naturally through conversation.

## Observability and safety

The system log records task formulation, verified-session evidence candidates,
retrieval completion, three-way sufficiency outcomes, retry/refinement evidence,
approved parent IDs, UX guidance, flexible response blocks, and final audit
decisions. Existing trusted-input, Prompt Guard, outbound compliance,
Groundedness v2.0, precision substitution, and memory-safety controls remain in
force.
