# Agentic Capability Roadmap v1.0

**Status:** Design finalized, not yet implemented. This document exists so
each work package (WP) below can be assigned to a different coding agent
independently, with enough context that no agent needs to re-derive the
design from conversation history.

## Scope note (read first)

A prior gap analysis against the project requirements document also flagged
several missing FAQ topics (mortgages, lines of credit, fixed/time deposits,
operating hours, standing instructions, complaint channels, T&C summaries)
and thin coverage in a few others. **None of that belongs in this roadmap.**
Those are corpus/data gaps, not code gaps — `PRODUCT_INTENT_MATRIX` and the
FAQ table are data, and enriching them changes zero lines of orchestration
code. They are FAQ-authoring work, not an engineering ticket.

Also explicitly out of scope for this roadmap (do not assign, do not fold
into any WP below):
- Cross-session Memory — concluded to sit outside the project's stated MVP
  scope (every memory/context requirement in the requirements document is
  scoped to "the same session"). The v1.0 last-session-summary/user-baseline
  mechanism has since been removed entirely (not merely left unreplaced) and
  the v2.0 richer-memory proposal was superseded before implementation; see
  `docs/MEMORY_ARCHITECTURE_v3.0.md` for the on-demand deterministic login
  check that replaced both.
- The chitchat unfulfilled-promise bug in `_generate_conversational_response`
  (e.g. "Sure, I can give you a quick overview!" followed by no overview) —
  known, root-caused, explicitly deferred by product decision.
- The triage-prompt heading regression that broke Gemini's marker-based
  schema dispatch — known, found during a prior commit review, never
  requested to be fixed. Flagging again here so it isn't lost, but it is a
  bug-fix ticket, not part of this capability roadmap.

## Design rationale (read before starting WP-1, WP-2, WP-3, WP-4, or WP-5)

This section exists so every agent picking up a ticket below shares the same
mental model of *why* each mechanism works the way it does, not just the
mechanical steps. It captures the reasoning behind the design, not new
requirements — when in doubt, the WP sections below are the authoritative
task list; this section is the context that keeps five different agents from
each independently reinventing (and subtly diverging on) the same answer.

### Why the API tool uses a placeholder token at all

The governing rule is PDPA-style data minimization: **the LLM must never
handle, generate, or directly manipulate raw personal data.** It should only
ever see and echo an opaque token. This is not a new pattern for this
codebase — it is the existing FAQ precision-reference mechanism
(`{{faq_xxx}}`, resolved by `fetch_placeholders_from_db` in
`node_07_precision_substitution`), extended to cover identity-exposing
customer data. Orchestration code — deterministic, not model-generated — is
the only layer that ever touches the real value.

**Worked walkthrough of the full flow**, spelled out explicitly because the
sequencing here is easy to get wrong (an earlier design discussion needed two
passes before the "orchestration pre-fetches, LLM never sees the real value"
sequencing was correctly understood — this walkthrough exists so no future
agent has to rediscover it the hard way):

1. Before response generation, WP-1's executor (`node_03b_api_worker`)
   fetches the requested field's real value from its data source — this
   happens in orchestration code, on the backend, never inside a prompt.
2. Orchestration caches **both** the real value and the value↔placeholder
   mapping into `state` (inside `tool_results`). The real value never enters
   any LLM prompt at this stage.
3. The response-generation LLM only ever sees and writes the placeholder
   token itself — e.g. `{{api-profile-address}}` — in its draft, exactly as
   it already does today for FAQ placeholders. The model never sees, and
   therefore cannot leak, paraphrase, or misstate, the real value.
4. `node_07_precision_substitution` resolves the token into the real value
   for what the customer is shown on screen this turn.
5. Unlike an FAQ placeholder, the resolved value must **never** flow into
   what gets persisted to `chat_history` — that divergence is exactly what
   WP-3 builds.

### Why a second, direct-value API mode exists

Not every API-sourced fact is identity-exposing. A loyalty tier or a
product-holding flag is customer-specific but doesn't expose identity on its
own the way an address or account number does. For fields classified this
way, the placeholder round-trip is unnecessary overhead: the value can be
placed directly into orchestration context and treated as a plain verified
fact, the same footing as a bank-conduct-approved knowledge parent. The
response generator then just uses the fact directly in its draft — there is
no placeholder token in that draft, so `node_07`'s substitution step has
nothing to do for that field, and `node_08` persists the generator's own
sentence to `chat_history` exactly as it does for any other answer.

This is also the field-level switch that decides which WP-3 path a given
field takes: `disclosure_mode: "placeholder"` fields go through the
resolve-for-display-only path; `disclosure_mode: "direct_value"` fields skip
it entirely and are just facts.

### The data-sensitivity nuance that must not get lost

"Not identity-exposing" is an engineering split for choosing *placeholder
vs. direct-value handling* — it is **not** the same statement as "not
personal data." Under most PDPA-style definitions, data relating to an
identified or identifiable individual is personal data regardless of how
sensitive it is; a loyalty tier tied to a specific logged-in customer still
qualifies, just at lower sensitivity than an address. `API_FIELD_REGISTRY`'s
`data_classification` list is a compliance decision, not an implicit
engineering judgment call — it should be reviewed and owned by whoever is
accountable for compliance in a real deployment before new fields are added,
and that ownership note should travel with the registry file itself, not
just live in this document.

### Why deeplink returns only a destination, never data

The explicit design principle: **the bot must stay free of transaction data
entirely.** It never receives, holds, or reasons about a customer's actual
transaction records. It only resolves a destination key to a validated,
allow-listed in-app route, and lets the bank's own existing app screen — with
its own access controls — display the actual data. This is also why the
destination registry (WP-2) is a strict allow-list rather than a
template/pattern the model could parameterize freely: the set of places the
bot can ever point a customer to is fixed and reviewed in advance, never
model-constructed at runtime.

### Personalization has two independent dimensions — don't conflate them

WP-4 and WP-5 together satisfy "personalization," but they solve genuinely
different halves of it, and an agent working on one should not assume it
covers the other:

- **Style personalization (WP-4):** *how* the bot talks — preferred
  nickname, preferred language. This is never a factual claim and is never
  subject to evidence-groundedness; it is purely a phrasing instruction to
  the generator, on the same footing as existing tone guidance.
- **Content personalization (WP-5, built on WP-1's direct-value mode):**
  *what* the bot can truthfully say about this specific customer's actual
  banking relationship — e.g., stating their real loyalty tier as a fact,
  not just adjusting tone around it. This is the half that introduces new
  factual claims, which is exactly why it — and only it — requires
  extending the evidence-groundedness bundle. Style personalization
  introduces no new claims and needs no such integration.

## What's actually in scope

Seven work packages. WP-6 has no dependencies and is the cheapest to hand
out first. WP-1 is foundational — WP-2, WP-3, and WP-5 all build on the
pattern or the data it produces. WP-7 is independent of WP-1/WP-2's code but
shares their allow-list/registry philosophy.

```text
WP-6 (independent, trivial)
WP-7 (independent: service_request tool, own registry)
WP-1 (foundational: api tool)
 ├── WP-2 (mirrors WP-1's pattern for deeplink; independent of WP-1's output)
 ├── WP-4 (independent of WP-1; separate subsystem)
 ├── WP-3 (needs WP-1's placeholder/direct-value split)
 └── WP-5 (needs WP-1's tool_result shape)
```

---

## WP-7: `service_request` Tool — Low/Intermediate-Risk Service Workflows

**Status: IMPLEMENTED.** All three build targets (address update, card
replacement, KYC document resubmission) are live and verified end-to-end at
the HTTP layer. **Detailed design and implementation notes:** see
`docs/SERVICE_REQUEST_DESIGN_v1.0.md` — the full registry contract,
registration state machine, form JSON schema, mocked credential-check/grant
mechanism, the no-suspended-turns resumption model, and a dedicated
"Implementation notes" section (end of that document) covering the handful
of things that turned out to need a small addition beyond the original
design (the planner's `available_service_requests` exposure, the new
`session_id`/`service_request_event` state fields, and the new `n_svc`
graph node for the special route).

### Problem

The requirements document explicitly anticipates this capability — page 6's
Agentic AI section lists *"invoke backend tools or APIs for approved service
requests"* and *"execute a service workflow"* as intended orchestration
outcomes, and the taxonomy table names *"Profile → Contact update, address
update status"* directly. Neither `api` (WP-1, read-only) nor `deeplink`
(WP-2, high-risk/money-movement, no data access) covers this: a third
category exists for low/intermediate-risk requests that actually write
data, gated by risk-tiered authorization rather than either "just read it"
or "just point at an existing screen."

### Solution

A new `service_request` tool, registry, and registration record, summarized
in the detailed design doc: a deterministic registry maps a
`service_request_id` to a risk level, a step-up requirement, and a form
schema; registering a form always lets the current turn complete normally
(no suspended graph state); the eventual form submission is a
self-contained, non-chat entry point keyed by a `special_id` that resolves
independently of whatever else happened in the conversation since; and
intermediate-risk submissions pass through a mocked (but real-interface)
credential check before a short-lived, payload-bound grant permits the
downstream write.

### Acceptance criteria

See `docs/SERVICE_REQUEST_DESIGN_v1.0.md` Section 12 for the specific open
decisions to resolve first, and Section 11 for full worked step sequences of
the three chosen build targets (address update, card replacement, KYC
document resubmission as flagship). Broadly, an agent picking this up
should verify a low-risk request (e.g. `profile-address-update`) completes
end-to-end without any credential step, an intermediate-risk request
correctly gates on the mocked check before the downstream call fires, a
closed-without-submitting form remains reopenable from old chat history,
and multiple concurrent pending requests coexist correctly and all appear in
the service-request to-do list (Section 9) rather than blocking each other.

### Files

New `backend/app/orchestration/service_request_registry.py`, a new
registration-record store, a new non-chat form-submission entry point, and
`backend/app/orchestration/tool_registry.py` / `node_03a` / `node_03b` for
the new tool's planning and dispatch branches — full detail in the design
doc.

### Dependency

None on WP-1/WP-2's code, but shares their registry/allow-list pattern —
build after at least one of them exists as a working reference.

---

## WP-1: `api` Tool — Registry Wiring and Read-Only Executor

**Status: IMPLEMENTED**, together with WP-3 and WP-5 below (all three were
built in one pass rather than staged separately — see "Implementation
notes" at the end of this section for what changed from the design below).

**Related, not required:** once this exists, `docs/SERVICE_REQUEST_DESIGN_v1.0.md`
Section 13 describes closing the loop with WP-7 — a `completed` service
request's to-do item getting a "Check Current Status" button that reads the
real outcome through `api`, since `service_request` deliberately never
tracks anything beyond "a submission was made." Design idea only, not
scheduled, and not a prerequisite for either WP.

### Problem

`TOOL_REGISTRY["api"]` exists in
[tool_registry.py](../backend/app/orchestration/tool_registry.py) but is
`enabled: False`. A stub `node_03b_api_worker` already exists in
[nodes.py:1758](../backend/app/orchestration/nodes.py#L1758):

```python
def node_03b_api_worker(state: AgentState):
    return {"api_payload": "[API DATA]:\nNo API data requested for this MVP."}
```

but it is **not wired into the graph at all** — `graph.py` only registers
`n17_dispatch` (`node_03b_tool_dispatcher`) and `n17_rag`
(`node_03b_rag_worker`). There is no `n17_api` node.

More importantly, `node_03b_tool_dispatcher`
([nodes.py:1589](../backend/app/orchestration/nodes.py#L1589)) does not
actually branch on the registry's `enabled` flag per tool — it hard-codes a
single live branch for `tool_name == "rag"` and marks every other pending
task `"unavailable"` unconditionally:

```python
for task in pending_tasks:
    tool_name = task.get("tool_name")
    if tool_name == "rag":
        task["status"] = "running"
        rag_task_ids.append(task["tool_task_id"])
        continue
    task["status"] = "unavailable"   # <-- every non-rag task, always
    ...
```

So flipping `enabled: True` on `api` in the registry, by itself, changes
nothing — the planner would propose `api` tasks, but the dispatcher would
still terminate every one of them as `unavailable`. This is the real reason
`api` is disabled today: the executor and its dispatch branch were never
built, not just the registry flag.

### Solution

1. **New file** `backend/app/orchestration/api_field_registry.py` — the "API
   guideline" the design has referred to throughout. One entry per
   retrievable field:

   ```python
   API_FIELD_REGISTRY: dict[str, dict] = {
       "profile-address": {
           "disclosure_mode": "placeholder",       # identity-exposing
           "data_classification": "identity_exposing",
           "fetch": lambda customer_id: _fetch_customer_address(customer_id),
       },
       "loyalty-status": {
           "disclosure_mode": "direct_value",       # non-identity-exposing
           "data_classification": "non_identity_low_sensitivity",
           "fetch": lambda customer_id: _fetch_loyalty_tier(customer_id),
       },
   }
   ```

   `disclosure_mode` is the field that drives everything downstream:
   - `placeholder` — the field can identify or expose personal data about
     the specific customer (address, account number, phone, full name +
     account tie). The value is only ever released into a `{{api-<field>}}`
     token; see WP-3 for why it can never be persisted resolved.
   - `direct_value` — the field is customer-specific but does not expose
     identity on its own (a loyalty tier, a product-holding flag). It can be
     placed directly into orchestration context as a plain fact.

   This list is a compliance decision, not an engineering guess — whoever
   owns data classification for the deployment should review it before new
   fields are added, per the PDPA-style point raised earlier: "not
   identity-exposing" is a reasonable engineering split for handling, but it
   does not mean the field stops being personal data under most privacy-law
   definitions. Document that distinction next to the registry, don't rely
   on the code comment alone.

2. **Real executor** — replace the stub `node_03b_api_worker` so it:
   - reads pending `api` tool_tasks (each `tool_task.input` names one
     `field_id`),
   - looks the field up in `API_FIELD_REGISTRY` (unregistered field →
     `unavailable`, mirroring the RAG worker's shape, never a fabricated
     value),
   - calls `fetch(customer_id)`,
   - returns results split by `disclosure_mode` so nothing downstream has to
     re-derive it:
     ```python
     approved_result = {
         "placeholder_map": {field_id: value, ...},   # disclosure_mode == placeholder
         "direct_values": {field_id: value, ...},     # disclosure_mode == direct_value
     }
     ```

3. **New function** `_finalize_api_tool_results(state, worker_output)` in
   `nodes.py`, mirroring `_finalize_rag_tool_results`
   ([nodes.py:1628](../backend/app/orchestration/nodes.py#L1628)) — turns
   worker output into the standard `tool_result` ledger entry (`tool_name:
   "api"`, `status`, `approved_result`, `availability_reason`).

4. **Dispatcher branch** — extend `node_03b_tool_dispatcher` with a second
   live branch parallel to the existing `rag` one:
   ```python
   if tool_name == "api":
       task["status"] = "running"
       api_task_ids.append(task["tool_task_id"])
       continue
   ```
   with its own `preflight_status` value (e.g. `"api_tool_active"`) returned
   alongside `rag_tool_active`, so the graph can route to a new `n17_api`
   node the same way it already routes to `n17_rag`.

5. **Graph wiring** in `graph.py` — add `workflow.add_node("n17_api",
   nodes.node_03b_api_worker)` and the conditional edge back into resolution
   planning, mirroring lines 21–22 and the `rag_tool_active` branch at line
   85.

6. Only after 1–5 are built and tested, flip
   `TOOL_REGISTRY["api"]["enabled"] = True`.

### Acceptance criteria

- A `financial_subquery` that resolves to a registered `api` field produces
  a terminal `completed` tool_result with the correct `placeholder_map` /
  `direct_values` split.
- An unregistered field produces `unavailable` with a clear
  `availability_reason`, never a fabricated value.
- Existing RAG-only turns are unaffected — run the existing trace/regression
  set before and after.

### Files

`backend/app/orchestration/tool_registry.py`, new
`backend/app/orchestration/api_field_registry.py`,
`backend/app/orchestration/nodes.py` (`node_03b_api_worker`, new
`_finalize_api_tool_results`, `node_03b_tool_dispatcher`),
`backend/app/orchestration/graph.py`.

### Implementation notes

Two things changed from the design above once the actual codebase was
checked (not just this doc), plus a real leak the design above didn't
anticipate:

1. **No `n17_api` graph node.** By the time this WP was built, `deeplink`
   (WP-2) and `service_request` (WP-7) had both already landed resolving
   synchronously *inline* in `node_03b_tool_dispatcher`, not through a
   separate graph node. `api`'s mock fetch is equally synchronous, so it
   follows that working precedent instead. `node_03b_api_worker` (the stub
   this doc describes "replacing") was deleted rather than replaced — it
   is genuinely dead once dispatch goes inline, the same way
   `node_03b_deeplink_worker` already was for WP-2. `graph.py` was not
   touched.
2. **A live PDPA leak this doc didn't flag.** `node_04a_resolution_planner`
   already dumps every tool_result's `approved_result` verbatim into an LLM
   prompt (`validated_tool_results`). Putting a placeholder-mode field's
   real value in `approved_result`, as originally sketched above, would
   have put a customer's real address straight into that prompt the moment
   the tool was enabled — not a future WP-3/WP-5 concern, a live one. Fixed
   by construction: the dispatcher never puts the real value in
   `approved_result` at all (only `field_id`/`disclosure_mode`/an opaque
   `placeholder_token`); the real value lives only in a new, narrowly-scoped
   state field, `personal_data_reveal_map`, that no prompt-building function
   reads — only the dispatcher (writes it) and `node_07` (reads it to build
   the outbound payload).
3. **WP-3 landed as something simpler than the `final_response_display`
   proposal below.** Rather than a second server-side response field,
   `node_07` never resolves a `{{data-*}}` token into the visible text at
   all. It sends this turn's token→value pairs alongside the response
   (`ui_action_payload.personalDataReveals`); the frontend keeps an
   in-memory, session-scoped token→value map and resolves any `{{data-*}}`
   token when rendering *any* message — this turn's or chat history's
   alike — falling back to a generic "protected, no longer available" label
   for a token it has no value for. `chat_history` and the DB therefore only
   ever store the opaque token, never the real value, without a second
   response field or a transcript-rendering redaction rule. See WP-3's
   section below for what this superseded.

---

## WP-2: `deeplink` Tool — Executor and Validated Destination Registry

**Detailed design:** see `docs/DEEPLINK_DESIGN_v1.0.md` — full end-to-end

> **Implementation status (supersedes the proposal below): IMPLEMENTED.**
> Deeplink is enabled through the generic inline dispatcher, not through a
> dedicated graph worker. The dispatcher centrally registers and resolves an
> allow-listed request in `Deep_Link_Request`; its graph result is only an
> opaque request ID. Node 07 persists `{linkId, label}`, never a route or
> destination ID. `GET /api/deeplink/open/{request_id}` re-checks the request
> and issues the server-side redirect. The proposal retained below is
> historical context only where it conflicts with this implemented contract.
flow, destination registry contract, UI payload contract, and the mock-page
scope for this MVP. That document is the authoritative detail layer for this
WP; the summary below is kept only for the roadmap's dependency overview.

### Problem

Same shape as WP-1's starting point: `TOOL_REGISTRY["deeplink"]` is
`enabled: False`, the stub `node_03b_deeplink_worker`
([nodes.py:1761](../backend/app/orchestration/nodes.py#L1761)) is not wired
into the graph, and the dispatcher has no branch for it.

Design intent (from the original tool proposal): `deeplink` never carries
banking data at all — it resolves a destination *key* to a validated
in-app navigation target, so the bot can point a customer at an existing
bank-app screen (e.g. "view your recent transactions") without the bot ever
holding transaction data itself.

### Solution

1. **New file** `backend/app/orchestration/deeplink_destination_registry.py`
   — `destination_id → { route_template, allowed_products, allowed_intents,
   description }`. This is the allow-list: only destinations listed here can
   ever be returned, so a model cannot cause the bot to point a customer at
   an arbitrary or unvalidated in-app route.

2. **Real executor** replacing the `node_03b_deeplink_worker` stub — resolve
   a requested `destination_id` against the registry; unregistered or
   scope-mismatched (`allowed_products`/`allowed_intents` don't cover the
   current task) requests return `unavailable`, never a best-guess route.

3. **New function** `_finalize_deeplink_tool_results`, mirroring
   `_finalize_rag_tool_results`, producing `approved_result: {destination_id,
   route}`.

4. **Dispatcher branch** for `tool_name == "deeplink"`, parallel to `rag` and
   the WP-1 `api` branch.

5. **Graph wiring**: `n17_deeplink` node plus conditional edge, mirroring
   WP-1's `n17_api` addition.

6. **UI payload shape** — a resolved deeplink result must surface as a UI
   action (a button/chip carrying the validated route), not as prose text
   the response generator narrates. Decide this payload shape as part of
   this WP, since `node_07_precision_substitution`'s existing
   `ui_action_payload` handling is the natural place it plugs in — extend
   `payload` with a `navigationAction: {label, route}`-shaped field rather
   than inventing a second payload channel.

7. Only after the above, flip `TOOL_REGISTRY["deeplink"]["enabled"] = True`.

### Acceptance criteria

- A resolved deeplink task produces a UI action payload carrying a
  registry-validated route.
- An unregistered or scope-mismatched destination request never reaches the
  UI as a link, and never becomes prose text asserting a destination exists.

### Files

`backend/app/orchestration/tool_registry.py`, new
`deeplink_destination_registry.py`, `backend/app/orchestration/nodes.py`
(`node_03b_deeplink_worker`, new `_finalize_deeplink_tool_results`,
`node_03b_tool_dispatcher`, `node_07_precision_substitution`'s payload
handling), `backend/app/orchestration/graph.py`.

### Dependency

None on WP-1's code, but build after WP-1 — WP-1 is the reference
implementation for the registry → worker → finalize → dispatcher-branch →
graph-wiring pattern this WP repeats.

---

## WP-3: Personal-Data Display-vs-Storage Divergence

**Status: IMPLEMENTED, but via a simpler mechanism than described below.**
Built together with WP-1 — see WP-1's "Implementation notes" for what
actually shipped: no `final_response_display` state field, no
transcript-rendering redaction rule. `node_07` never resolves a
`{{data-*}}` token server-side at all; the frontend resolves it, from a
per-turn value delivered alongside (never inside) the response text, and
falls back to a generic label for any token it has no value for (a past
session's chat history, or a fresh page load). The rest of this section is
kept for the problem statement, which is still accurate.

### Problem

`node_07_precision_substitution`
([nodes.py:4030](../backend/app/orchestration/nodes.py#L4030)) resolves
every `{{placeholder}}` token it finds in `final_draft` via
`fetch_placeholders_from_db` and writes the result into a single `draft`
value, which becomes `final_response`. `node_08_soft_escalation_analyzer`
([nodes.py:4118](../backend/app/orchestration/nodes.py#L4118)) then appends
that exact same `final_response` string, verbatim, into `chat_history`:

```python
if state.get("final_response", ""):
    history.append(AIMessage(content=state.get("final_response", "")))
```

There is currently exactly one string that is simultaneously "what the
customer sees" and "what gets persisted." That's fine for existing FAQ
placeholders (`{{faq_xxx}}`) — resolved FAQ content is non-personal
reference data, safe to store either way. It is **not** fine for WP-1's new
`placeholder`-mode API fields (e.g. `{{api-profile-address}}`): once
resolved, the customer's real address would be written into `chat_history`
verbatim, which is read by compaction, context-building, and any "view past
conversation" UI — none of which carry the access control the API call
itself had.

### Solution

1. Add one new `AgentState` field:
   `final_response_display: str` — what the frontend renders for the
   **current** turn only. Never persisted, never read by any downstream
   node.

   Keep `final_response`'s existing meaning (what `chat_history` stores),
   but stop letting placeholder-mode API tokens resolve into it.

2. In `node_07_precision_substitution`, split substitution into two
   independent passes over the same source text:
   - **FAQ map** (`fetch_placeholders_from_db`, unchanged) — resolves into
     *both* `final_response` and `final_response_display`, exactly as
     today.
   - **API placeholder map** (from WP-1's `tool_results` for the current
     turn, `disclosure_mode == "placeholder"` entries only) — resolves
     *only* into `final_response_display`. The literal `{{api-*}}` token is
     left untouched in the string that becomes `final_response`.

3. In `node_08_soft_escalation_analyzer`, no change to the append logic
   itself — it already appends `final_response`, which is now guaranteed to
   still contain the unresolved token instead of the real value, as long as
   WP-3 step 2 lands first.

4. Identify and update the response-serving boundary (outside
   `backend/app/orchestration`, wherever the graph's output is currently
   serialized back to the frontend) so the just-produced turn is served from
   `final_response_display`, not `final_response`.

5. **Historical transcript display** — when a customer (or an agent) views a
   *past* turn whose stored `AIMessage.content` still contains a raw
   `{{api-*}}` token, define one deterministic rule at the transcript
   rendering layer only (never mutate the stored ledger):
   - Replace the token with a generic, non-reversible label, e.g. `[a
     personal detail previously shown]`.
   - Do **not** re-fetch live data to redisplay a historical turn — the
     value may have changed since, and re-resolving silently would make the
     transcript describe the present, not what was actually said at the
     time.

### Acceptance criteria

- After a turn resolves `{{api-profile-address}}`, the customer's current
  screen shows the real address.
- `chat_history`'s stored `AIMessage` for that same turn contains the
  literal token, not the address — verify by inspecting `chat_history`
  directly after the turn, not just the API response.
- Any downstream consumer of `chat_history` (compaction, context-building,
  memory) only ever sees the unresolved token.
- Reloading a past session's transcript shows the redacted label, never the
  raw token and never a freshly re-fetched value.

### Files

`backend/app/orchestration/state.py` (new field), `backend/app/orchestration/nodes.py`
(`node_07_precision_substitution`, `node_08_soft_escalation_analyzer`), the
response-serialization boundary that sends the turn to the frontend
(identify during implementation — outside the orchestration package), and
the chat-history transcript rendering path.

### Dependency

WP-1 (needs the `placeholder_map` / `disclosure_mode` split its executor
produces).

---

## WP-4: Personalization — User Preference Profile (Style)

### Problem

No mechanism exists to remember a customer's preferred nickname or language
across turns or sessions. This is the "style" half of the personalization
gap identified in the requirements review; it is independent of anything
API/tool-related.

### Solution

1. A small persistent profile store — `preferred_nickname`,
   `preferred_language`, keyed by `customer_id` (a new table, or a new
   pair of columns on an existing customer profile table if one already
   exists — check before adding a new table).
2. New `AgentState` field: `user_profile: Dict[str, Any]`.
3. A lightweight loader node, early in the graph (post-authentication,
   pre-response-generation), that reads the profile row into
   `user_profile`. This should be a plain DB read, no LLM call.
4. Response-generator prompt: add profile fields as an explicit **style-only**
   instruction block ("address the customer as {nickname} if set; respond in
   {preferred_language} if set"), following the same boundary already used
   for chat history and memory elsewhere in this codebase — advisory
   context that shapes phrasing, never evidence a groundedness audit should
   treat as a factual source.
5. A way to *set* these fields — likely a dedicated, deterministic
   subquery/intent (e.g. "call me X" / "reply in Spanish") rather than a
   free-form LLM extraction, to avoid accidentally writing an incorrect
   value from a misread turn.

### Acceptance criteria

- Setting a nickname causes subsequent responses, same and future sessions,
  to address the customer by it.
- Changing `preferred_language` changes response language without needing
  any new evidence.
- `check_evidence_groundedness`'s evidence bundle is unaffected — profile
  fields never appear there, only in the style-instruction section of the
  generator prompt.

### Files

New profile table/service, `backend/app/orchestration/state.py`, a new
loader node + its graph wiring, the response-generator prompt section in
`nodes.py`.

### Dependency

None.

---

## WP-5: Direct-Value API Context as Recognized Evidence

**Status: IMPLEMENTED**, together with WP-1 — see WP-1's "Implementation
notes". `_normal_outbound_grounding_context` builds `verified_api_context`
from `disclosure_mode == "direct_value"` results exactly as described
below; the response generator and both outbound audits were extended to
treat it as a permitted factual source alongside task-approved evidence.

### Problem

`_normal_outbound_grounding_context`
([nodes.py:3464](../backend/app/orchestration/nodes.py#L3464)) builds the
evidence bundle for the normal-answer route from `approved_by_task` alone —
RAG-approved parents. Once WP-1 lands, direct-value API facts (e.g. a
loyalty tier) will be available in `tool_results`, and the response
generator will legitimately want to state something like "As a Trust+
member, you're eligible for priority support." Without this WP, the
evidence-groundedness audit has no channel to recognize that fact as
verified, and will very likely reject the sentence as an unsupported claim —
Groundedness v2.0 working exactly as designed against evidence it hasn't
been told about yet.

### Solution

1. Extend `_normal_outbound_grounding_context` to build a new list,
   `verified_api_context`, from `state["tool_results"]` where `tool_name ==
   "api"` and the field's `disclosure_mode == "direct_value"` **only** —
   deliberately exclude `placeholder`-mode entries here. Placeholder-mode
   values remain display-only substitution (WP-3); they must not also become
   free-form evidence text the model can narrate around, since that would
   let a raw personal-data value leak into the model's own generated
   sentence structure instead of staying a fixed, audited substitution.
2. Return `verified_api_context` alongside `approved_by_task` in the dict
   `_normal_outbound_grounding_context` produces, and pass it into
   `check_evidence_groundedness`'s `evidence_bundle` for the normal-answer
   call (around [nodes.py:3841](../backend/app/orchestration/nodes.py#L3841)).
3. Add one line to that call's `route_specific_rules` string (the block
   starting near [nodes.py:3820](../backend/app/orchestration/nodes.py#L3820)):
   state explicitly that `verified_api_context` entries are a permitted
   factual source for the normal-answer route, on the same footing as
   task-approved evidence.

### Acceptance criteria

- A response referencing a verified direct-value API fact passes
  evidence-groundedness.
- A response inventing an API-shaped fact that is *not* present in
  `tool_results` still fails — confirm the audit doesn't over-generalize
  from the new source category into accepting unverified claims of the same
  shape.

### Files

`backend/app/orchestration/nodes.py`
(`_normal_outbound_grounding_context`, the normal-answer
`check_evidence_groundedness` call and its `route_specific_rules`).

### Dependency

WP-1 (needs `tool_results` entries with the `disclosure_mode` split).

---

## WP-6: Licensing-Scope and Conduct-Rule Precision

**Status: licensing-scope half CLOSED.** The tax/legal-advice gap below is
implemented (`REGULATORY_LICENSING_CONTEXT` rule 5 in
[compliance.py](../backend/app/core/compliance.py)). The policy-override
conduct gap is still open — see Solution step 2, not yet applied.

### Problem

Two distinct gaps, easy to mistakenly file under the same rule set:

**Tax and legal advice — a licensing-scope (compliance) gap, not a conduct
gap.** `BANK_CONDUCT_CONTEXT` covers these only implicitly, via the broad "No
Personalized Advice or Recommendations" rule. But the reason a bank cannot
give tax or legal advice is not a self-imposed behavioral standard the way
"neutral tone" or "no commitments" are — it is that giving legal advice is
practicing law, and giving tax advice typically requires tax-agent
certification, neither of which the bank (or its chatbot) is licensed for.
That is structurally the *same* kind of restriction as the rule already
sitting in `MAS_REGULATIONS_CONTEXT`
([compliance.py:4](../backend/app/core/compliance.py#L4)):

```text
1. Financial Advisers Act (FAA): Prohibits providing personalized financial,
   investment, or trading advice without a Capital Markets Services (CMS)
   license.
```

Both are "this entity is not licensed to perform this professional
activity" facts, external to the bank's own choices — which makes tax/legal
advice a compliance-axis gap, not a conduct-axis one. It belongs beside the
FAA rule, not beside "neutral tone."

One naming wrinkle to resolve at the same time: `MAS_REGULATIONS_CONTEXT` is
named for MAS-administered financial regulation specifically. Neither legal
practice licensing nor tax-agent certification is a MAS-administered regime
— filing them under a constant literally named "MAS regulations" would be
correct in effect but misleading in name to the next reader. Fix the name,
not just the content.

**Policy-override authority — a genuine conduct gap.** This one *is*
correctly a `BANK_CONDUCT_CONTEXT` addition: rule 4 ("No Fabricated
Authority") covers inventing a policy that doesn't exist, but not the
different failure mode of implying the assistant can grant an exception to,
or waive, a real policy on the customer's behalf. That's about the
assistant overstating its own authority, the same species of problem as the
rest of that rule set, not a licensing question.

### Solution

1. **(Done)** Renamed `MAS_REGULATIONS_CONTEXT` to
   `REGULATORY_LICENSING_CONTEXT` in
   [compliance.py](../backend/app/core/compliance.py) and updated its two
   call sites — [groundedness.py:108](../backend/app/core/groundedness.py#L108)
   (`check_conduct_compliance`'s outbound audit) and
   [nodes.py:870](../backend/app/orchestration/nodes.py#L870) (`node_02d`'s
   inbound whole-turn triage veto, which now also screens an inbound "give me
   legal advice"-style request at the compliance stage, not only an outbound
   leak). Added rule 5:

   ```text
   5. Licensed Professional Services: Prohibits providing tax guidance, legal
      interpretation, or legal advice, even in general or hypothetical terms
      -- both require professional licenses (legal practice, tax-agent
      certification) the assistant does not hold, a scope restriction
      independent of and additional to the FAA/CMS restriction above. Direct
      such questions to a qualified tax advisor or legal professional.
   ```

2. **(Not yet done)** Add to `BANK_CONDUCT_CONTEXT` (unchanged location, this
   one really is conduct, not licensing):

   ```text
   6. No Policy Override Authority: Never imply the assistant can grant an
      exception to, waive, or override a stated bank policy on the
      customer's behalf.
   ```

Both additions are purely additive — no existing rule text changes, so no
existing compliance or conduct behavior should regress.

### Acceptance criteria

- Existing conduct-compliance and evidence-groundedness traces/tests pass
  unchanged — confirmed no stray references to the old constant name remain
  (`grep -rn MAS_REGULATIONS_CONTEXT backend/app` returns nothing) and all
  three touched files compile.
- A new trace asking for tax guidance or legal advice should produce a
  `conduct_violations` entry with `"rule": "regulation"`, not
  `"bank_conduct"` — `check_conduct_compliance`'s existing output schema
  ([groundedness.py:119](../backend/app/core/groundedness.py#L119)) already
  distinguishes the two, so this is directly checkable in the audit's raw
  JSON. **Not yet live-traced** — verify with a real turn before considering
  this fully closed.
- A new trace asking for a policy exception ("can you just waive the fee for
  me") should produce a `conduct_violations` entry with `"rule":
  "bank_conduct"` once step 2 above is implemented.

### Files

`backend/app/core/compliance.py` only.

### Dependency

None. Cheapest, lowest-risk ticket in this roadmap — good candidate to
assign and land first, independent of everything else.
