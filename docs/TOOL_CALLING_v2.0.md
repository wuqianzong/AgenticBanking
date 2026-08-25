# Tool Calling v2.0

**Status: PARTIALLY SUPERSEDED.** The Generic Contracts section below
(`tool_task` / `tool_result`) and Node 04a's resolution role are replaced by
`docs/TOOL_CALLING_v3.0.md`'s subquery-owned `tool_attempts` schema — not
yet implemented. The Turn Boundary, Registry State, and Safety Rules
sections on this page remain accurate.

## Purpose

Tool Calling v2.0 separates financial request decomposition, generic tool
orchestration, tool execution, evidence validation, and response presentation.
It replaces the former graph-level assumption that every valid request begins
with RAG.

The design is additive and read-only. A tool may contribute validated results
to one financial subquery, but it cannot perform a banking action or bypass
the outbound compliance and groundedness gates.

## Turn boundary

Node 02d first resolves the customer’s true request from the latest message and
trusted current-session context. It then applies this whole-turn order to that
resolved meaning:

1. `NON_COMPLIANT`: only a request for Horizon or the assistant to perform,
   facilitate, provide tactics for, or advise on prohibited conduct vetoes the
   turn. A question about whether an action is allowed, or a correction to a
   premise, remains a normal evidence-bound banking request unless it itself
   requests that prohibited assistance.
2. `ESCALATION`: an explicit human-agent request or a contextual confirmation
   of a preceding escalation routes to escalation confirmation.
3. `VALID_REQUEST`: compliant banking content is decomposed into
   `financial_subqueries`; unrelated wording is omitted from tool input. Small
   short internal attention cues for conversational asides (such as a
   preference or feeling) may be retained separately as
   `non_financial_context` for response tone only. The original user input is
   always the sole customer-facing wording source; cues are never evidence, a
   banking request, tool input, or text to quote/paraphrase.
4. `NON_FINANCIAL`: greetings, chitchat, and unrelated requests route to Node
   12 for a brief natural, polite response without factual banking content.
   This route has its own groundedness contract: it permits only a non-material
   acknowledgement or generic banking-assistant scope statement, and its audit
   retries remain on Node 12 rather than entering the FAQ response generator.

Only `VALID_REQUEST` reaches the tool planner. Product, intent, retrieval
strategy, and UI presentation are deliberately not triage responsibilities.

## Generic contracts

The registry in `backend/app/orchestration/tool_registry.py` is the authority
for a callable capability.

```text
financial_subquery
  { subquery_id, customer_component }

tool_task
  { tool_task_id, subquery_id, tool_name, operation, input,
    fingerprint, status, planning_round }

tool_result
  { tool_task_id, subquery_id, tool_name, status, approved_result,
    customer_safe_summary, availability_reason }
```

`fingerprint` makes an outer-loop task idempotent. A task may be `pending`,
`running`, `completed`, or `unavailable`. Only a terminal, validated
`tool_result` is available to the Global Resolution Sufficiency Check.

## Runtime flow

```text
Node 02d financial subqueries
  → Node 03a Tool-Calling Planner
  → Node 03b Generic Dispatcher
  → registered executor
  → validated terminal tool result
  → Node 04a Resolution and User Journey Planner
  → response generation and outbound gates
```

The initial planner deterministically creates one RAG task for each financial
subquery. On a later loop, Node 04a can emit a concrete capability gap. Node
03a may add a task only when the registry says the capability is enabled,
registered, non-duplicate, and within the round/task budgets. The outer loop
is capped at three planning rounds and eight total tool tasks per turn.

## RAG integration

RAG is an internal executor, not the generic orchestrator:

```text
RAG tool task
  → Node 02e intent formulation
  → Node 03b_1 retrieval
  → Node 03c task-level sufficiency and retries
  → terminal RAG tool result
```

Node 03c is the RAG evidence boundary. It converts a terminal task outcome
into a tool result containing only approved parent IDs. Raw candidates,
unapproved parent text, and retry evidence deltas remain inside RAG. A
terminal unresolved RAG task exposes only its resolved topic and status for a
safe, general clarification.

## Registry state in this release

| Tool | Operation | Status | Contract |
|---|---|---|---|
| `rag` | `retrieve_faq_evidence` | Enabled | Retrieves and verifies Horizon FAQ evidence. |
| `api` | `read_customer_data` | Disabled | Reserved for authorised, read-only customer data. |
| `deeplink` | `resolve_navigation` | Disabled | Reserved for validated Horizon destinations. |

Disabled tools are not proposed by the planner. If an unavailable executor is
ever dispatched defensively, the dispatcher records an `unavailable` result
and continues; it never fabricates data.

## Safety rules

- The registry is allow-list based: no model-generated tool name or operation
  is runnable unless it is registered and enabled.
- The planner cannot recreate a terminal, pending, or running task with the
  same fingerprint.
- API and deeplink integrations require a read-only executor, explicit input
  validation, an approved-result schema, and outbound audit coverage before
  they can be enabled.
- Resolution planning sees only validated terminal results. Response
  generation and answer groundedness use only approved evidence for financial
  claims. In a mixed turn, the original user input may support a brief
  non-material courtesy acknowledgement only; it cannot support a banking
  fact, instruction, policy, eligibility conclusion, or outcome.
- The response generator uses the original request and trusted session context
  to determine whether evidence is applicable to the customer's situation.
  The Response Generator alone chooses presentation. A conditional procedure
  cannot become a customer action path merely because a tool returned grounded
  instructions for a different condition.
- Existing Prompt Guard, deterministic placeholder validation, and precision
  substitution remain unchanged. Outbound compliance and groundedness now run
  as Groundedness v2.0's shared evidence and conduct checks (see
  `docs/GROUNDEDNESS_v2.0.md`); every route's external pass/fail contract is
  unchanged, only the underlying implementation is now shared.
- The escalation case summary — one of the surfaces this document's own
  design implies should eventually carry validated tool-result facts — now
  passes the same Groundedness v2.0 checks before it is shown, closing what
  was previously a gap: it is customer-facing (rendered directly in the
  escalation-confirmation UI block) and had no groundedness or compliance
  check at all before this upgrade.
