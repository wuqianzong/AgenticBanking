# Service Request Capability Design v1.0

**Status:** Design confirmed through discussion; not yet implemented. Like
`DEEPLINK_DESIGN_v1.0.md`, this is a living document — update it in place as
implementation surfaces new decisions, rather than letting drift accumulate
between this file and the actual code.

This is a new capability beyond the original six work packages in
`docs/AGENTIC_CAPABILITY_ROADMAP_v1.0.md`; see that document's WP-7 entry for
the one-paragraph summary and its place in the overall dependency picture.

---

## 1. Where this sits relative to the other two tools

Three tools now, cleanly separated by risk and data-handling posture, not by
arbitrary category:

| Tool | Risk | What it touches | Data-handling posture |
|---|---|---|---|
| `deeplink` | High-risk / money-movement | Nothing — points to an existing app screen with its own controls | Bot never receives the underlying data at all |
| `api` (WP-1) | None (read-only) | Customer data, retrieval only | Placeholder or direct-value split; LLM never sees a placeholder's real value |
| `service_request` (this doc) | Low / intermediate | Customer data, a real write happens | Form data never touches the LLM; risk-tiered authorization gates the write |

A `service_request` registry entry simply does not exist for anything
high-risk — that category is served by `deeplink` instead. The boundary is
enforced by what's absent from a registry, the same allow-list discipline
used everywhere else in this codebase, not by a runtime judgment call.

## 2. Design principle: no suspended turns

The turn that registers a form **always runs to completion and closes
normally** — every other subtask in that turn resolves and gets answered,
and the service-request subtask gets an honestly-scoped response ("please
complete the form below to update your address") with the form's UI action
attached. Nothing is left open, paused, or waiting inside the graph.

This was a real design correction made during discussion, worth recording
because the reasoning generalizes: an earlier draft of this design tried to
have the graph "pause" mid-turn and "resume" later. That doesn't fit how
turns actually execute in this codebase — each is a one-shot invocation,
hydrated from persisted state and closed at the end, not a long-running
process holding an in-flight call stack. More importantly, a suspended-turn
model creates an invariant ("nothing else may touch this session while a
form is pending") that a real customer's behavior can violate in ways the
backend can't fully prevent from the frontend alone — a second tab, a
delayed message, a UI glitch.

The turn-completes-normally model has no such invariant to violate:

- Customer ignores the form and keeps chatting normally: those are just
  ordinary turns; nothing about them interacts with the pending
  registration.
- Customer abandons the form entirely: nothing is blocked or waiting; the
  registration record sits until it expires.
- Customer closes the form without submitting: **not a terminal state** —
  the registration stays exactly as it was (`registered`), and the link
  left in that turn's chat history stays clickable, reopening the same form
  against the same `special_id` whenever the customer chooses to.
- Customer comes back and submits the form many messages later: the
  eventual submission is a self-contained event, looked up fresh from the
  registration record — it works the same regardless of what happened in
  the conversation in between.
- Customer gets around the blocked input textbox somehow: harmless. The
  blocked textbox is now a **UX choice for focus, not a correctness
  mechanism** — there is no fragile state left for a stray message to
  corrupt.

## 3. Registry contract

New file, `backend/app/orchestration/service_request_registry.py`:

```python
SERVICE_REQUEST_REGISTRY: dict[str, dict] = {
    "profile-address-update": {
        "risk_level": "low",              # "low" | "intermediate" -- never "high"
        "requires_step_up": False,
        "allowed_products": ["Horizon_APP"],
        "allowed_intents": ["Profile Updates"],
        "form_schema": {...},              # step/caption/field definitions, Section 5
        "description": "Submit a new contact address for the customer's profile.",
    },
    "dispute-filing": {
        "risk_level": "intermediate",
        "requires_step_up": True,
        "allowed_products": ["Credit_Card", "Debit_Card"],
        "allowed_intents": ["Transaction Disputes"],
        "form_schema": {...},
        "description": "File a transaction dispute for review.",
    },
}
```

Same allow-list discipline as `deeplink`'s destination registry and WP-1's
`api` field registry: the model selects a registered `service_request_id`;
every other detail — risk level, whether step-up is required, the actual
form shape — comes from this registry, never from generation.

## 4. Registration record and state machine

One record per registered form, keyed by a `special_id`:

```text
service_request_registration
  { special_id, session_id, user_id,               # user_id server-bound, never client-supplied
    service_request_id,                              # e.g. "profile-address-update"
    resolved_customer_component,                      # the original subquery this form fulfils
    product_tags, intent_tags,
    risk_level, requires_step_up,
    status,                                           # see below
    submitted_data,                                   # populated only after submit; never LLM-visible
    submitted_data_hash,                              # binds a grant to this exact payload, Section 6
    grant_id,                                          # populated only if a grant was issued
    created_at, expires_at }
```

State machine — deliberately with no "cancelled by closing" state, since
closing the form without submitting is simply "not yet submitted," not a
terminal outcome:

```text
registered  ──(submit)──>  submitted
  low risk:          submitted ──> processing ──> completed
  intermediate risk:  submitted ──> credential_check_pending
                          ──(pass)──> granted ──> processing ──> completed
                          ──(fail)──> denied            # genuine terminal negative outcome
any non-terminal state ──(expires_at reached)──> expired
```

`registered` and `submitted-but-not-yet-processed` links remain clickable
and reopenable from old chat history for as long as the record hasn't
reached `expired` or a terminal outcome (`completed`/`denied`).

## 5. Form JSON schema (frontend-rendered, LLM-invisible)

```json
{
  "steps": [
    {
      "step_number": 1,
      "caption": "New Address",
      "fields": [
        {"field_id": "line1", "label": "Address Line 1", "field_type": "text", "default": "", "notice": ""},
        {"field_id": "country", "label": "Country", "field_type": "options", "options": ["SG", "..."], "default": "SG"}
      ]
    }
  ]
}
```

`field_type` stays a small, closed enum (`text`, `options`, `checkbox`, ...
— never anything a client could interpret as executable). The frontend is a
fixed renderer over this closed set, the same allow-list reasoning as the
registries themselves. Submitted data goes straight from the frontend to
the backend's form-submission endpoint; it is never included in an LLM
prompt at any stage.

## 6. Credential check and grant (mocked for this MVP)

For `risk_level: "intermediate"` entries only. The check itself is mocked —
a fixed UI surface, **not** a form-JSON step, presented as "Bank Centralized
Credential Check" — but the architecture around it is real and should be
built so a genuine credential service is a drop-in replacement later, not a
rewrite:

1. After submit, if `requires_step_up`, the registration moves to
   `credential_check_pending` and the mocked check surface is invoked with
   only the `special_id` as context — never the submitted form data itself.
2. The mock returns pass/fail through the same interface contract a real
   check would use.
3. On pass, a **grant** is issued: a short-lived, single-use record scoped
   to this exact `special_id` and bound to `submitted_data_hash` — not just
   the ID in the abstract, so a grant can't silently authorize a payload
   different from the one actually checked, even though the mock doesn't
   need to verify the hash today.
4. Only a valid, unexpired, unconsumed grant permits the downstream central
   API call. The grant is consumed the moment that call happens.
5. On fail, the registration moves to `denied` — a genuine terminal outcome
   requiring an honest, audited decline response (Section 7), not a dead
   end.

The step-up interaction itself must never be a text field collecting a raw
credential inside the generic form renderer — see the design-rationale
discussion this doc summarizes: that would reintroduce exactly the
credential-handling risk the placeholder mechanism elsewhere in this project
was built to avoid, and it trains customers to enter banking credentials
into a chat-adjacent surface. Delegate to the app's own native
re-authentication surface (biometric prompt, PIN re-entry) and accept back
only an opaque, short-lived assertion token — never the credential.

## 7. The special route: how a submission becomes a chat response

A form submission (or credential-check result) is a **new, non-chat entry
point** — not a `chat.request` with free text. It carries `special_id` (+
submitted data, on first submission). It never goes through node_02d triage.

```text
Form submission event
  → look up service_request_registration by special_id
    (validate: not expired, not already terminal, user_id matches session)
  → low risk: call downstream central API directly
  → intermediate risk, no grant yet: run mocked credential check → grant → call downstream API
  → downstream API result becomes evidence for a normal response-generation pass
  → same outbound compliance/groundedness gates as any other turn
    ("no guarantee of approval" etc. apply here exactly as elsewhere)
  → response appended to chat_history as a new turn, same as any other
```

Because this is a self-contained lookup keyed by `special_id`, it works
identically whether the submission happens ten seconds or ten messages after
the form was first shown.

## 8. Serialization of multiple service requests

The planner does not register a new `service_request` while the session
already has one in a non-terminal state (`registered`, `submitted`,
`credential_check_pending`). This is a plain existence check against the
registration table — not queueing or suspension machinery. If a single
customer message implies two service requests, only the first is
registered; the second remains an unresolved need for that turn and the
customer will need to raise it again once the first reaches a terminal
state. This is a deliberate, small cost to conversational continuity in
exchange for never holding anything open across turns — worth remembering
as a conscious trade-off if it comes up in review, not an oversight.

## 9. Safety rules (recap, consistent with the rest of this project)

- Allow-list only: no model-generated `service_request_id`, risk level, or
  form shape is ever runnable — all three come from the registry.
- `user_id` on a registration is server-bound to the authenticated session,
  never a client-suppliable field.
- `special_id` is unguessable, single-use per terminal transition, and
  expires.
- Form data is never included in an LLM prompt.
- A grant authorizes one specific submitted payload, not a special_id in
  the abstract.
- Every customer-facing message this capability produces — the initial
  "please complete this form," the eventual success confirmation, and the
  denial message — passes the same outbound compliance/groundedness gates
  as any other response.

## 10. Open decisions to confirm before implementation

1. Exact registration expiry window (a day? a few days?).
2. Whether the credential-check mock is a literal always-pass stub, or a
   demo-friendly pass/fail toggle for showing the denial path.
3. Backend behavior when a session already has a non-terminal
   `service_request` and the planner would otherwise want to register a
   second one for a *different* subquery in the same turn — confirmed above
   that it's simply deferred, but the exact customer-facing wording for
   that deferral hasn't been drafted.
4. Where `service_request_registry.py`'s first real entries should be —
   `profile-address-update` is the obvious first candidate given it's named
   directly in the requirements taxonomy table.

## Changelog

- v1.0 (initial draft, consolidating multi-turn discussion): three-tool risk
  split, registry contract, no-suspended-turns model, registration state
  machine, form schema, mocked credential check + grant mechanism, the
  special resumption route, and multi-request serialization. Confirmed
  through discussion; not yet implemented.
