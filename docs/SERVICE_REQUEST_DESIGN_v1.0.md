# Service Request Capability Design v1.0

**Status:** Implemented (all three build targets: address update, card
replacement, KYC document resubmission). Live-verified end-to-end at the
HTTP layer, including the low-risk path, the intermediate-risk
credential-check pass and fail paths, ownership validation, and the
serialization guard. Like `DEEPLINK_DESIGN_v1.0.md`, this remains a living
document — update it in place as anything changes, rather than letting
drift accumulate between this file and the actual code.

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
  the registration stays exactly as it was (`pending`), and the link
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

## 3. Candidate service requests

Surveyed against the FAQ corpus and the requirements taxonomy table before
picking build targets, so the choices are grounded rather than invented.
Evidence strength is noted honestly — some of these lean on the taxonomy
table alone, not on existing FAQ content.

### Low risk (no credential gate)

| Candidate | Steps | Grounding |
|---|---|---|
| **Postal address update** | 2 (enter address → confirm) | Named directly in the taxonomy table (Profile → address update). First build target. |
| **Statement delivery preference** (paperless vs. mailed) | 1 | No direct FAQ hit; included as the simplest possible case to prove the no-credential path with minimal steps. |
| **Physical card replacement** (worn/damaged, not lost/stolen) | 3 (select card → reason → confirm mailing address) | Strongly grounded (15 corpus hits for "replace"). Second build target — see the note below on why this is distinct from `deeplink`'s card-management destination. |
| **Complaint / feedback filing** | 2–3 (category → description → contact preference) | Named in-scope ("complaint and feedback channels"); thin corpus grounding (2 hits for "feedback," 0 for "complaint"). |
| **Branch appointment request** | 2 (reason → preferred branch/time) | Thin grounding (1 hit); zero financial/security exposure. |

### Intermediate risk (mocked credential gate)

| Candidate | Steps | Grounding |
|---|---|---|
| **Email or mobile number update** | 2 (new value → confirm) | Same form shape as the low-risk address update, deliberately — demonstrates that risk is a property of *what* changes (email/phone gate account recovery and 2FA), not of the form's shape. |
| **Transaction dispute filing** | 4 (select transaction → reason → details → confirm) | Best-grounded intermediate candidate (6 corpus hits); "dispute and charge reporting guidance" is explicitly in-scope. Genuine downstream financial consequence (can trigger a chargeback/refund review). |
| **KYC document resubmission** | 4 (see Section 11) | Named in-scope ("onboarding and KYC-related queries"); no direct FAQ hit under that phrasing. **Third build target — the flagship worked example.** |

### Boundary cases — deliberately NOT service_request candidates

Recorded so a future agent doesn't reflexively register these just because
they appear in the taxonomy table:

- **Standing instruction / recurring transfer setup** — named directly in
  the taxonomy table and shaped like an ordinary form, but it is real money
  movement between accounts. Belongs to `deeplink` (point at the app's
  existing screen), not `service_request`.
- **Raising a card or ATM transaction limit** — same trap: looks like a
  profile-style toggle, but directly changes fraud exposure. High-risk,
  `deeplink` territory.

### Rejected as a build target: full account-opening KYC

Account opening was considered as the flagship example instead of
resubmission, and rejected for this MVP — recorded here so it isn't
re-proposed without re-deriving why:

1. **No existing `user_id` to anchor a registration to.** Every other
   `service_request` candidate is something an *already-authenticated
   existing customer* does to their own account. A prospect opening their
   first account has no prior session to bind the registration's `user_id`
   to — it would require a separate "prospect" registration mode the rest
   of this design doesn't otherwise need.
2. **The mocked step-up credential check doesn't map onto identity
   establishment.** The credential check (Section 6) answers "is this still
   the same customer who's already enrolled" by re-challenging a credential
   they already have. Verifying a *brand-new* person's claimed identity is a
   different question with no existing credential to step up with — it
   would need its own gate type, not a reuse of this one.

**KYC document *resubmission*** (Section 11) avoids both problems: it's an
existing, authenticated customer, so the registration model and the
existing credential-check gate both apply unmodified, while still
exercising a multi-step form and a new field type.

## 4. Registry contract

New file, `backend/app/orchestration/service_request_registry.py`, shown
here with the three chosen build targets:

```python
SERVICE_REQUEST_REGISTRY: dict[str, dict] = {
    "profile-address-update": {
        "risk_level": "low",              # "low" | "intermediate" -- never "high"
        "requires_step_up": False,
        "allowed_products": ["Horizon_APP"],
        "allowed_intents": ["Profile Updates"],
        "form_schema": {...},              # Section 5
        "description": "Submit a new postal address for the customer's profile.",
    },
    "card-replacement-request": {
        "risk_level": "low",
        "requires_step_up": False,
        "allowed_products": ["Credit_Card", "Debit_Card"],
        "allowed_intents": ["Card Logistics"],
        "form_schema": {...},
        "description": "Request a replacement physical card for a non-fraud reason.",
    },
    "kyc-document-resubmission": {
        "risk_level": "intermediate",
        "requires_step_up": True,
        "allowed_products": ["Horizon_APP"],
        "allowed_intents": ["Authentication And Security"],
        "form_schema": {...},              # Section 11
        "description": "Resubmit an identity document for an existing customer's KYC record.",
    },
}
```

Same allow-list discipline as `deeplink`'s destination registry and WP-1's
`api` field registry: the model selects a registered `service_request_id`;
every other detail — risk level, whether step-up is required, the actual
form shape — comes from this registry, never from generation.

## 5. Registration record and state machine

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

**Revised state machine — only three externally-visible statuses:
`pending`, `expired`, `completed`.** No "cancelled by closing" state (closing
the form without submitting is simply "not yet submitted"), and no "denied"
state either, on the same principle applied more broadly: this registry
tracks that a submission was *made*, not whether the underlying banking
action succeeded in whatever legacy system actually processes it. Tracking
real transaction outcomes here would mean integrating with every system a
given service_request eventually touches, which this registry deliberately
has no visibility into and isn't meant to. If a customer wants to know
whether their address actually changed, that's a read-path question —
answered through `api` (WP-1) or `deeplink` or the bank app itself, never
through this table's `status` field.

```text
pending  ──(submit)──>  pending  [submitted_data set]
  low risk:          ──> completed  [result_payload set]
  intermediate risk:  ──(credential check pass)──> completed  [grant_id + result_payload set]
                       ──(credential check fail)──> pending   [submitted_data cleared -- resubmittable]
any pending row past expires_at ──> expired
```

A failed credential check is **not terminal**. It resets the submission
(clearing `submitted_data`, `submitted_data_hash`, `grant_id`) and leaves
`status` exactly as it was: `pending`. The customer sees an honest denial
message for that attempt (Section 8), but the registration itself remains
open, and `GET /api/service-request/{special_id}` returns the same
`pending` + form_schema response whether this is a first attempt or a
resubmission after a failure — the frontend never needs to know which. This
means every `pending` row is always resubmittable up to `expires_at`,
regardless of how many prior attempts failed.

Any `pending` link remains clickable and reopenable from old chat history
for as long as the record hasn't reached `expired` or `completed`.

## 6. Form JSON schema (frontend-rendered, LLM-invisible)

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

`field_type` stays a small, closed enum — never anything a client could
interpret as executable:

- `text`, `options`, `checkbox` — the original set, sufficient for
  address update, card replacement, and most of resubmission.
- `document_reference` — new, added for KYC resubmission (Section 11).
  Holds only an opaque token, never file bytes or a raw upload. For this
  MVP the token is produced by a mocked "Simulate Upload" affordance in the
  frontend rather than a real file picker or storage backend — there is no
  real KYC process behind this demonstration, so the upload itself is
  faked. What must stay real is the *shape*: the submitted payload carries
  only a reference token either way, so swapping in an actual upload
  integration later changes what produces the token, not anything
  downstream of it.

The frontend is a fixed renderer over this closed set, the same allow-list
reasoning as the registries themselves. Submitted data goes straight from
the frontend to the backend's form-submission endpoint; it is never
included in an LLM prompt at any stage.

## 7. Credential check and grant (mocked for this MVP)

For `risk_level: "intermediate"` entries only. The check itself is mocked —
a fixed UI surface, **not** a form-JSON step, presented as "Bank Centralized
Credential Check" — but the architecture around it is real and should be
built so a genuine credential service is a drop-in replacement later, not a
rewrite:

1. After submit, if `requires_step_up`, the mocked check surface is invoked
   with only the `special_id` as context — never the submitted form data
   itself. `status` stays `pending` throughout; nothing about invoking the
   check changes it.
2. The mock returns pass/fail through the same interface contract a real
   check would use.
3. On pass, a **grant** is issued: a short-lived, single-use record scoped
   to this exact `special_id` and bound to `submitted_data_hash` — not just
   the ID in the abstract, so a grant can't silently authorize a payload
   different from the one actually checked, even though the mock doesn't
   need to verify the hash today.
4. Only a valid, unexpired, unconsumed grant permits the downstream central
   API call. The grant is consumed the moment that call happens, and only
   then does `status` move to `completed`.
5. On fail, the registration is **reset, not failed**: `submitted_data`,
   `submitted_data_hash`, and any `grant_id` are cleared, and `status`
   stays `pending` — there is no `denied` status. The customer still
   receives an honest, audited decline response for that specific attempt
   (Section 8), but the registration itself remains open for a resubmission
   up to `expires_at`, per the revised state machine in Section 5.

The step-up interaction itself must never be a text field collecting a raw
credential inside the generic form renderer — see the design-rationale
discussion this doc summarizes: that would reintroduce exactly the
credential-handling risk the placeholder mechanism elsewhere in this project
was built to avoid, and it trains customers to enter banking credentials
into a chat-adjacent surface. Delegate to the app's own native
re-authentication surface (biometric prompt, PIN re-entry) and accept back
only an opaque, short-lived assertion token — never the credential.

This is a re-authentication check ("is this still the same enrolled
customer"), not an identity-establishment check ("who is this person"). The
distinction is why this mechanism applies cleanly to KYC *resubmission* (an
existing customer) but would not have applied to full account-opening KYC
(a prospect with no existing credential) — see Section 3's rejected-scope
note.

## 8. The special route: how a submission becomes a chat response

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

## 9. Multiple pending service requests: a to-do list, not a queue

**Superseded design, kept for context:** the original v1.1 design serialized
service requests — only one non-terminal registration allowed per session,
everything else blocked. Live testing surfaced why that's the wrong model:
a customer asking for two unrelated things in one message ("I lost my card,
also I want to change my address") had the second one silently blocked with
no visible reason, and there was no way to revisit a form the customer
hadn't gotten around to yet except by re-asking in conversation.

**Current design:** there is no limit on how many non-terminal registrations
a customer can have at once, and no serialization check at registration
time. Each registration simply gets a longer validity window —
**`REGISTRATION_TTL_HOURS = 72`** (3 days) — long enough that a customer
juggling several requests doesn't lose one to expiry mid-conversation.
Instead of blocking, the frontend gives every registered form a durable,
revisitable home: a **service-request to-do list**, scoped by `user_id` so
it persists across sessions, not tied to whichever session happened to
register a given item.

- **`GET /api/service-request/list/{user_id}`** returns every registration
  for that customer — pending, completed, and expired, newest first.
  `service_request_service.list_registrations` is the backing query; it
  applies the same lazy-expiry check as a single-item `get`.
- The header shows a badge with the count of currently-pending items
  (`status == "pending"`), refreshed after every chat turn and after every
  submit/credential-check resolution. A registration that just had a failed
  credential check still counts as pending here — it's resubmittable, not
  gone.
- Tapping the badge opens the full list. Each item shows its description and
  a status chip (pending / completed / expired). Only pending items are
  tappable; tapping one re-opens the form through the exact same
  `GET /api/service-request/{special_id}` → open-if-still-pending path used
  for reopening a link from old chat history — no separate code path for
  "opened from the list," "opened from a chat message," or "resubmitting
  after a failed check." They're all the same lookup.
- Registering a new form for the same subquery never conflicts with an
  existing one for a *different* need — `profile-address-update` and
  `kyc-document-resubmission` can both sit pending at once, each shown as
  its own to-do item.

This keeps the "no suspended turns" principle from Section 2 completely
intact — nothing about this list is a graph-side wait state. It is a plain,
independently-queryable read of the same registration table that already
existed; the graph and the special route never needed to know it exists.

## 10. Safety rules (recap, consistent with the rest of this project)

- Allow-list only: no model-generated `service_request_id`, risk level, or
  form shape is ever runnable — all three come from the registry.
- `user_id` on a registration is server-bound to the authenticated session,
  never a client-suppliable field.
- `special_id` is unguessable, single-use per terminal transition, and
  expires.
- Form data is never included in an LLM prompt.
- A grant authorizes one specific submitted payload, not a special_id in
  the abstract.
- A `document_reference` field carries only an opaque token, never file
  content, even while the token itself is mocked.
- Every customer-facing message this capability produces — the initial
  "please complete this form," the eventual success confirmation, and the
  denial message — passes the same outbound compliance/groundedness gates
  as any other response.

## 11. Worked examples: the three build targets

### 11.1 Postal address update (`profile-address-update`, low risk)

1. **New address** — `line1` (text), `line2` (text, optional), `city`
   (text), `postal_code` (text), `country` (options, default `SG`).
2. **Review and confirm** — read-only summary of step 1, confirm button.

Submit → low risk, no gate → downstream call → result: *"Your address has
been updated to [new address]."* — a direct-value fact from the downstream
result, not a generated guess, per the same evidence discipline as
everything else in this project.

### 11.2 Physical card replacement (`card-replacement-request`, low risk)

1. **Select card** — `card_id` (options, populated from the customer's own
   cards via existing `api`-style context, not re-typed).
2. **Reason** — `reason` (options: `worn` / `damaged` / `other`) —
   deliberately excludes `lost` / `stolen`, which imply fraud/security
   exposure and should route to a higher-scrutiny path, not this form.
3. **Confirm mailing address** — read-only summary (reusing existing
   profile address unless the customer just changed it via 11.1), confirm
   button.

Submit → low risk, no gate → downstream call → result: *"Your replacement
card has been ordered and will arrive by mail. Reference: [ref]."* — never
a promised delivery date unless the downstream result actually supplies
one, per the "no commitments" conduct rule.

### 11.3 KYC document resubmission (`kyc-document-resubmission`, intermediate risk, flagship)

1. **Document selection** — `document_type` (options: `NRIC` / `Passport` /
   `Employment Pass` / `Other`), `document_number` (text).
2. **Document upload** — `document_file` (`document_reference`, populated
   by a mocked "Simulate Upload" affordance, no real file handling this
   MVP), `expiry_date` (text/date).
3. **Confirm personal details** — name/DOB shown read-only from existing
   profile context (not re-typed), checkbox: "these details are still
   accurate." A customer noting something *has* changed (e.g. address)
   should be pointed at 11.1 separately rather than this form trying to
   handle it too — composability over one form doing everything.
4. **Review and submit** — full summary, a terms/accuracy acknowledgment
   checkbox, submit.

Submit → intermediate risk → `credential_check_pending` → mocked
credential check:
- **Pass** → grant → downstream call → result: *"Your updated
  identification document has been received. Reference: KYC-XXXXX.
  Verification will be completed within [N] business days."*
  Deliberately "received," never "verified" or "approved," per the
  no-guaranteed-outcomes rule — this is true even though it's the customer's
  own resubmission, because the actual verification is a backend process
  this turn cannot claim to have completed. Also deliberately attributes no
  specific actor (no "our compliance team") — the mock's evidence payload
  is only `{reference, business_days}`, and naming a department was an
  unsupported addition the evidence-groundedness audit correctly rejected
  during live testing (see Implementation notes).
- **Fail** → registration resets and stays `pending` (Section 5) → an
  honest, audited decline through the same special route (Section 8) for
  this specific attempt, not a dead end — e.g. *"We weren't able to verify
  your identity for this request. Please try again or visit a branch."*
  "Please try again" is literal here: the same `special_id` is still open
  for a resubmission, not a new registration.

## 12. Open decisions — resolved during implementation

1. **Registration expiry window: 72 hours (3 days).** `REGISTRATION_TTL_HOURS`
   in `service_request_service.py` — revised up from an initial 48-hour
   choice once multiple concurrent pending requests became the design
   (Section 9); a customer juggling more than one open request needs more
   room before the oldest one expires. Expiry is lazy (checked and applied
   on read, via `_expire_if_due`), not a background job.
2. **Credential-check mock: demo-friendly pass/fail toggle**, not an
   always-pass stub. The frontend's credential-check modal offers both
   "Simulate: Confirm Identity" and "Simulate: Cannot Confirm" so the
   denial path (Section 11.3) is actually reachable in a demo, not just
   documented.
3. **Deferred second-request wording** — **superseded, see Section 9.** This
   was implemented as described (an `unavailable` tool result with a
   deferral reason), but live testing showed the underlying serialization
   guard itself was the wrong design, not just its wording; Section 9 now
   describes the to-do-list model that replaced it. Left here only so the
   history of this decision isn't lost.
4. **`document_reference` mock token format**: a per-submission generated
   placeholder, `mock-doc-ref-<timestamp>` (frontend-generated on the
   "Simulate Upload" click), not a fixed string — makes it visibly obvious
   in logs/testing which submission a token came from.

## 13. Future direction: closing the loop with `api` (WP-1) — design idea only, not implemented

Directly follows from the status-model redesign above: this registry
deliberately stops at "submission completed," never claiming to know the
real-world outcome. The natural next question a customer asks is "so did it
actually happen?" — and the answer to *that* should come from a read, not
from this table. This section records the intended shape of that read
before it's built, the same way `MEMORY_ARCHITECTURE_v2.0.md` records a
design ahead of its implementation.

**The idea:** each entry in `SERVICE_REQUEST_REGISTRY` (the static, per-type
contract in `service_request_registry.py` — not a per-instance field on
`Service_Request_Registration`) can declare a deterministic pointer to the
`api` field that answers "what's the current value of the thing I just
submitted" — for example, `profile-address-update` would point at whatever
`api` field later exposes the customer's current on-file address. This
lives beside `action_label`/`description`/`form_schema` in the registry,
looked up fresh by `service_request_id` exactly the same way those already
are, rather than duplicated onto every registration row.

**The flow once `api` (WP-1) exists:** a `completed` item in the
service-request to-do list — and only a `completed` one, since there is
nothing to check before that — shows an additional button (something like
"Check Current Status"). Tapping it does not reopen a form or touch this
registry's `status` at all; it triggers a normal turn that resolves through
the `api` tool, using the field the registry declared, and the bot reports
back whatever the (as-yet-unbuilt) live read actually returns. This is
deliberately the same trigger-a-turn shape the "special route" already
established for form results (Section 8) — a deterministic, code-driven
action attached to a customer-facing button, not something an LLM decides
to fetch on its own.

**Why this belongs to `api`, not to an extension of this registry:** it
keeps the separation of concerns from Section 1 intact — `service_request`
writes, `api` reads, and this registry's own `status` field never has to
carry a claim about a real-world outcome it can't actually verify. Building
it depends on WP-1 existing first; nothing here should be started before
that.

## Status model redesign: three statuses, no "denied," failure means resubmit

**Supersedes the original five/six-status state machine described in
Sections 5 and 7 above where they haven't already been updated in place.**
The original design treated a failed credential check as a genuine terminal
outcome (`denied`), on the same footing as `completed`. That conflated two
different things this registry was never meant to conflate: whether a
*submission* happened, and whether the *underlying banking action*
ultimately succeeded — the second is a question for whatever legacy system
actually processes the request, which this registry has no visibility into
and was never designed to track. Modeling `denied` as terminal also meant a
customer who mistyped a PIN once lost their entire request and had to start
over from a fresh registration, which doesn't match how a real
re-authentication failure should behave.

**Current design:** exactly three externally-visible statuses —
`pending`, `expired`, `completed` — full detail in Sections 5 and 7. A
failed credential check resets the submission and leaves the registration
`pending`, resubmittable up to the same `expires_at` it already had. The
customer still gets an honest per-attempt decline message; the registration
itself just doesn't die because of it. This also simplified the code:
`service_request_service.py` no longer needs separate `registered` /
`submitted` / `credential_check_pending` / `granted` status values to track
internal progress — "is there submitted_data," "is there a grant_id," and
"is status still pending" are together sufficient to know exactly where a
registration stands, without a proliferating status enum to keep in sync
across the service layer, the API endpoints, and the frontend.

## Response-generation redesign: no LLM citation, multiple forms supported

**Supersedes the citation-based coverage mechanism described in several of
the notes below.** After three separate live-trace bugs all turned out to
be the same underlying model — a language model unreliably echoing back a
`special_id` for coverage-tracking (wrong key, dropped citation, invented
key) — the mechanism itself was removed rather than patched a fourth time.

**Current design (revised twice — see both notes below):**

- The generator is told only a **count** — `Pending service request forms
  registered this turn: N` — never the ids, descriptions, or anything else
  about them, and never asked to write anything about a form at all. Its
  only use for the count is deciding *tone*: when N > 0, lead with whatever
  answer it has instead of an apology or decline framing, because a
  resolution is coming either way. It must not mention a form, service
  request, or link in any wording, at any count. This was tightened after
  finding that even a generic LLM-authored sentence ("I've included a quick
  form below") is structurally unverifiable — see the note below.
- The actual introduction to the form(s) is a **fully deterministic line**,
  authored in `node_07_precision_substitution`, not generated: "You can
  fulfil your request using the form below" (singular) or "...these
  requests using the forms below" (plural), chosen by a plain count check
  and rendered through `get_message()` in the customer's preferred
  language. It's appended once, after every registered request's button is
  already built, both as a response block (what renders) and onto the
  stored draft (what a future turn's conversation history will see).
- `node_07_precision_substitution` independently and unconditionally builds
  `ui_action_payload.serviceRequestActions` — **a list**, not a single
  object — by reading every completed `service_request` result straight out
  of `tool_results` and looking up its registry contract. This runs
  regardless of what the draft said, mirroring how the button attachment
  always worked, just no longer capped at the first request. **Multiple
  simultaneous pending forms are a first-class case, not an edge case**: the
  planner already loops across rounds to register more than one in a single
  turn (Section 9), and every one of them gets its own button, self-labeled
  with its `action_label` ("Change Address," "Replace Card," ...).
- The old per-request coverage-fallback ("To move forward, please complete
  the form below: {description}") is gone entirely, and so is any LLM-
  authored framing sentence — both existed only to compensate for
  mechanisms (citation tracking, then a generic-but-generated sentence)
  that turned out not to be needed once the intro line became deterministic.
- The evidence-groundedness bundle carries `pending_service_request_count`
  (an integer) purely so the audit can catch a model that mentions a form
  anyway — the count is not itself a permitted source for any customer-
  facing statement.

This is the reason the sequence of bug notes below reads the way it does:
several of them describe first a citation mechanism, then a generic-
sentence mechanism, each failing in a different way before the pattern
became clear enough to make the whole introduction deterministic rather
than asking a model to produce any part of it. They're kept as the record
of why this design looks the way it does now, not as descriptions of
current behavior.

## Implementation notes (things a reader of the design might not expect)

- **Bug found via live trace, fixed — the generic sentence was structurally
  unverifiable, not just occasionally worded badly.** After the v1.3
  citation removal, the generator was still asked to write one generic
  sentence like "I've included a quick form below to help with that." A
  live trace showed the conduct and evidence audits both correctly reject
  this: `node_06`'s audit gates run *before* `node_07` ever attaches the
  actual button to `ui_action_payload`, so at the moment the sentence is
  checked, the payload genuinely contains no form yet. Any claim that a
  form "is included" or "is below" is asserting something the auditor
  cannot see at that point in the pipeline, regardless of how the sentence
  is worded — this was never going to pass reliably, because the ordering
  itself makes the claim unverifiable, not because of a specific phrasing
  choice. Fixed by removing the generic sentence from the LLM's job
  entirely and making the introduction a deterministic, translated line
  added by `node_07` itself, after the button already exists — see
  "Response-generation redesign" above for the full mechanism. The count is
  now used only to steer tone (lead with the answer, not an apology),
  never to produce a customer-facing statement.
- **Bug found via live trace, fixed — unrelated topics collapsed into one
  paragraph during a redraft, losing the structure the first draft
  correctly used.** The same trace's first attempt used a clean
  `PROGRESS` element for the card-freeze steps; after that draft was
  rejected for the generic-sentence issue above, the redraft merged the
  unrelated card and address topics into a single dense `TEXT` block,
  discarding the earlier structure entirely — a known LLM tendency to
  anchor on a previous draft's shape when told to fix one flagged
  sentence, rather than re-deciding structure fresh. The existing rule
  ("evidence from multiple tasks only when naturally related") was already
  correct but too abstract to survive redraft pressure. Tightened with a
  concrete example (a lost card and an address change are not related just
  because they arrived in the same message) and an explicit statement that
  redrafting to remove one flagged sentence is never a reason to leave the
  rest of the structure unexamined.
- **Bug found via live trace, fixed — a genuinely wrong capability match,
  the root cause of a full escalation.** The same "lost card, also change my
  address" turn was retried after switching to a different model to work
  around a rate limit, and this time both `node_04a` and the tool-calling
  planner matched the *lost* card need to `card-replacement-request` —
  whose own registry description explicitly reads "for a non-fraud reason
  (worn or damaged)," deliberately excluding lost/stolen (Section 3's
  boundary-case reasoning). Both nodes matched on the general topic ("card
  replacement") without checking the description's own scope qualifier.
  Registering the wrong capability then cascaded through three
  increasingly incoherent redraft attempts — the generator tried to
  reconcile "lost card" with a form literally described as being for a
  different reason, produced sentences splicing address wording with the
  card form's description, invented an unsupported "provide your Horizon
  Key... takes effect instantly" claim while trying to route around the
  audit's objections, and after three straight rejections the turn
  escalated to a human agent. Every audit rejection along the way was
  *correct* — this was a real defect being caught repeatedly, not audit
  overreach. Fixed by adding the same instruction to both decision points
  (`node_04a_resolution_planner` and the tool-calling planner in
  `node_03a_knowledge_blueprint_planner`): match on a capability's whole
  description, not its nearest keyword, using this exact example in both
  prompts so they can't drift apart; and when every registered capability's
  scope excludes the customer's actual situation, that's `EXHAUSTED` for
  that need, not `NEEDS_TOOLS` — RAG's own answer, if sufficient, may still
  stand on its own (which it already did here: task_1's freeze-and-replace
  steps were a complete, correct answer for a lost card with no capability
  needed at all).
- **Bug found via live trace, fixed — the third distinct way a model has
  mis-cited a service-request id, so the defense moved to code.** The same
  trace also had the generator put the address request's `special_id`
  under an invented `"special_id"` key on the element instead of inside
  `source_task_ids`, so the coverage check (trusting only
  `source_task_ids`) treated it as uncovered and inserted a redundant
  duplicate invitation anyway. This is the third distinct instruction-
  following slip in this same category — first the tool-planner's
  `service_request_id` landed under the wrong key entirely, then an id was
  dropped from `source_task_ids` when combining facts, now an id lands
  under a different key the model invented on its own. Three different
  failure shapes from the same underlying tendency means the fix belongs in
  code, not in a fourth prompt rewording. Added a defensive recovery in
  `_generate_response_blocks`: after reading the declared
  `source_task_ids`, every other value on the element (any key, string or
  list) is also checked against the known pending service-request ids, and
  a real match is folded in as genuine coverage regardless of which key it
  arrived under. The prompt instruction stays as the primary mechanism;
  this is the safety net behind it, matching the same "recover the value
  even when the key is wrong" principle already used for the tool-planner's
  `service_request_id` recovery.
- **Bug found via live trace, fixed — the coverage-fallback safety net
  (added a few fixes above specifically to prevent silent omission)
  misfired into causing a contradiction instead.** Both RAG and
  `service_request` were correctly invoked for the same "lost card, also
  change my address" turn — but the final response read as though it
  contradicted itself: a detailed paragraph explaining how to freeze and
  replace the card, immediately followed by "Sorry, I don't have a complete
  answer for the part about... reporting it," and the address-update form
  offered twice. Root cause: the model combined task_1's (card) content
  into the *extended_text* of an element whose `source_task_ids` named only
  `task_2` (address) — a legitimate way to combine related facts, but the
  card task_id was never credited. The coverage check only trusts
  `source_task_ids`, so it saw task_1 and the service request's `special_id`
  as both missing and inserted fallbacks for content that had, in fact,
  already been given — the exact opposite of what the safety net exists to
  prevent. Fixed by adding an explicit instruction to
  `_generate_response_blocks`: when one element draws on more than one
  task or pending service request, every one of their ids must be listed in
  that element's `source_task_ids`, framed with *why* it matters ("an id
  left out is treated as never addressed, and a contradictory duplicate...
  gets appended right beside content that already covered it") rather than
  as a bare rule, consistent with how the rest of this prompt already
  explains reasoning instead of just issuing instructions. If this
  resurfaces despite the instruction — this project has seen LLM
  instruction-following gaps before (the `service_request_id` key
  mismatches) — the next escalation is a code-level check rather than a
  third prompt rewording: detect likely-redundant fallback content before
  inserting it, not just trust declared `source_task_ids`.
- **Bug found via live trace, fixed — a real loophole in an earlier fix of
  this same document.** The "RAG and service_request are independent
  coverage decisions" rule (added a few fixes above) explicitly protected
  against *adjacent* RAG evidence suppressing a matching capability, but
  never explicitly addressed the case where RAG evidence is a *complete,
  correct, directly-on-topic* answer — and that gap was exactly what broke
  next: a live "change my registered address" request retried through RAG's
  full ladder and came back genuinely `SUFFICIENT` (real FAQ steps for
  updating an address manually in the app). Because the evidence fully
  answered the question, `node_04a` returned `RESOLVED` with an empty
  `tool_gaps` and never even considered offering `profile-address-update`,
  even though its description precisely matches the request. Being able to
  *explain* how a customer could do something manually is not a substitute
  for being able to *do it for them* through a registered capability — the
  form is strictly more valuable, not an alternative resolution to the same
  question, so a complete RAG answer must never suppress a matching
  capability any more than an adjacent one may. Fixed by removing that
  loophole from the `node_04a_resolution_planner` rule entirely (a matching
  capability is now offered "with no exception, including when RAG evidence
  is itself a complete, well-grounded, directly-on-topic answer") and by
  updating the parallel instruction in `_generate_response_blocks`, which
  previously only asked the model to lead with the service request "if
  separate evidence... does not itself resolve the request" — now updated
  to lead with it regardless, presenting any manual-steps evidence as
  optional reference afterward, never as a reason the request is already
  handled.
- **Not a bug: an escalation traced to a Groq daily token-quota exhaustion
  (429), not a defect** — a turn asking about a lost card and an address
  change escalated to a human agent after 3 failed safety attempts. All
  three `FLEXIBLE RESPONSE GENERATION` calls for that turn failed outright
  with `Rate limit reached for model openai/gpt-oss-120b ... tokens per day
  (TPD): Limit 200000, Used ~199,820`. This is the same root cause behind
  an earlier-looking "RAG regression" trace too — once the generator can't
  run at all, every attempt falls back to the deterministic coverage-
  fallback text, which is then legitimately re-evaluated by the audits each
  retry. The existing 3-strikes-then-escalate logic did exactly what it's
  supposed to do here. Resolves on its own once the daily quota resets;
  nothing to fix in this codebase for the exhaustion itself.
- **Bug found via the same trace, fixed independently of the quota issue**:
  the conduct audit rejected the coverage-fallback text — *"To move
  forward, please complete the form below: Submit a new postal
  address..."* — citing Rule 4 (No Fabricated Authority), reasoning that
  inviting the customer to a form implies the assistant is executing the
  address change itself. That reasoning is wrong, and the gap is real
  regardless of what triggered this specific occurrence: `BANK_CONDUCT_
  CONTEXT` never told the auditor that directing a customer to a
  registered, separately-gated service-request form is the *opposite* of
  fabricated authority — a compliant hand-off, not an execution claim. Left
  unfixed, this could false-positive on normally-generated invitation text
  the same way, any time the auditor's judgment leans this direction, with
  no rate-limit excuse to explain it away next time. Fixed by adding an
  explicit carve-out to Rule 4 in `compliance.py`: "Inviting a customer to
  complete a registered, gated service-request form is not fabricated
  authority... Directing a customer to it is a compliant hand-off, not an
  unauthorized action." This is a shared-context change, so it also
  protects `node_svc_service_request_resolver`'s own conduct check on the
  result/denial messages, not just the invitation.
- **Not a bug, a misdiagnosis worth recording**: a "lost credit card" answer
  came back as a generic "I don't have a complete answer" apology, and it
  looked like a RAG regression — it wasn't. The trace showed the card task
  was judged `SUFFICIENT` with four approved parents on every single
  attempt. What actually happened: the *address* request's invitation
  sentence oversold what it was allowed to say ("Once submitted, we'll
  apply the new address to your profile" — a claim about outcome, not just
  an invitation), the evidence audit correctly rejected it, and the
  resulting redraft call hit a transient `llm.error` from the model
  provider. The exception handler correctly avoided a crash, but the
  fallback path discarded the *entire* draft, including the card answer
  that had never been the problem — and the existing "omitted task" safety
  net then inserted a generic apology for it. Two real, separate bugs came
  out of chasing this down (below); RAG itself needed no fix.
- **Bug found via live trace, fixed**: the pending-service-request
  invitation sentence oversold what it's allowed to say, the same shape of
  failure as the two evidence-bundle bugs above but on the *invitation* side
  rather than the *result* side — the generator isn't only forbidden from
  describing the form's fields, it also has no basis for claiming what
  happens after submission. Fixed by tightening the design principle in
  `_generate_response_blocks`: "Never state or imply what will happen after
  the customer submits it (an outcome, an automatic effect, a timeline) —
  you do not know that either, only that the form exists and can be
  completed."
- **Bug found via live trace, fixed**: this is what actually produced the
  "blunt link" you saw — `node_07` attaches the service-request button
  unconditionally whenever a registration exists in `tool_results`,
  regardless of whether the surviving draft text still mentions it. When
  the invitation sentence above got redrafted away, the button had nothing
  next to it. The existing "omitted task" safety net only ever covered RAG
  `task_id`s, never a service request's `special_id`, so there was no
  equivalent protection on this path. Fixed by giving a pending service
  request the same addressable identity as a RAG task: the generator is now
  told to cite a request's `special_id` in `source_task_ids` when it
  invites the customer to it, `valid_task_ids` now includes those
  `special_id`s so such citations aren't filtered out, and a second
  coverage-fallback loop (mirroring the RAG one exactly) inserts a plain
  "To move forward, please complete the form below: {description}" whenever
  a registered request's `special_id` never made it into any surviving
  element — so the button can no longer appear with nothing beside it,
  regardless of what happened upstream in the draft/redraft cycle.
- **Rename, applied everywhere the form's identity is shown to a
  customer**: each registry entry now carries a short `action_label`
  (`profile-address-update` → "Change Address", `card-replacement-request`
  → "Replace Card", `kyc-document-resubmission` → "Resubmit KYC Document"),
  distinct from `description` (the longer sentence the generator uses for
  its invitation wording). `node_07` uses `action_label` for the button
  text instead of a generic "Complete Form", and the
  `/api/service-request/list/{user_id}` endpoint returns it as the to-do
  list's headline per item, with `description` underneath as secondary
  detail — the list no longer just shows the long sentence as its only
  label.
- **Bug found via live trace, fixed**: a sixth issue, surfaced by a
  two-subquery turn ("I lost my credit card... also I want to change my
  address"). The address-change subquery correctly triggered a
  `service_request` registration attempt, which correctly came back
  `unavailable` because of the one-active-request-per-session guard (a
  form from earlier testing was still sitting unsubmitted). But the
  customer-facing response never mentioned the address request at all — it
  silently vanished. Root cause: `_pending_service_requests` (Section 3's
  helper for a *completed* registration) was the only service_request
  signal wired into `_generate_response_blocks` and the groundedness
  evidence bundle; nothing surfaced an `unavailable` service_request result
  the way an unavailable RAG task already gets a fallback acknowledgment.
  This directly contradicted what open decision #3 (resolved above) claimed
  — that the deferral "flows into the response generator like any other
  unavailable tool result" — which turned out to be true only for RAG, not
  for `service_request`, since tool_results were never generically wired
  into response generation at all (each tool's signal has to be surfaced on
  its own, as `_pending_service_requests` already was for the completed
  case). Fixed by adding a sibling helper, `_unavailable_service_requests`,
  surfacing only the deterministic `availability_reason` already recorded
  at dispatch time, wired into the same two places
  (`_generate_response_blocks`'s prompt and `_normal_outbound_grounding_
  context`'s evidence bundle) with an instruction that an unavailable
  capability still deserves an honest acknowledgment, never silent
  omission — the same standard already applied to declines and terminal
  unresolved RAG topics elsewhere in this same prompt.
- **Bug found via live trace, fixed**: a fifth issue, same root cause as the
  "compliance team" one below but on the denial path — a failed credential
  check produced the generic fallback instead of a clear denial message,
  because the evidence bundle for a denial was just `{"outcome": "denied"}`
  and the auditor correctly wouldn't let the template say "we weren't able
  to verify your **identity**" when that concept wasn't literally present.
  Two failures with the same shape (a template's wording outrunning what a
  bare status/data field actually asserts) means the fix belongs at the
  pattern level, not as a second one-off patch. Restructured: every
  template in `_SERVICE_REQUEST_RESULT_TEMPLATES` and the denial message now
  has a matching `_SERVICE_REQUEST_RESULT_FACTS` / `_SERVICE_REQUEST_DENIAL_
  FACTS` entry — an explicit list of plain-English statements, one per
  clause the template makes — and `node_svc_service_request_resolver` passes
  that facts list, not just the raw mock payload, as the evidence bundle.
  The audit's job is now "does the content stay within these named facts,"
  which is unambiguous by construction, instead of "infer whether this
  phrasing is a reasonable reading of a bare data field," which is where
  both prior failures came from. Any new service_request template should
  get its facts list written first, one entry per clause, before the
  template wording is finalized — not fitted to the wording afterward.
- **Bug found via live trace, fixed**: same "can i change my kyc docs?"
  request, run again, exposed a fourth issue — this time RAG retrieval
  happened to surface a different, marginally-approved parent
  (`faq_29a71c6f`, about updating personal details via Singpass/MyInfo) that
  cleared the PARTIALLY_SUFFICIENT bar, so `node_04a` returned `RESOLVED`
  directly and never even considered the `service_request` gap — the KYC
  form was never offered at all this run. The response confidently walked
  the customer through a Singpass/MyInfo address-update flow and called it
  "updating your KYC documentation," which is a bridge inference beyond
  what that evidence actually supports (updating a personal-details field is
  not the same action as resubmitting an identity document).

  Not a conflict between RAG and `service_request` to be arbitrated — the
  system already supports surfacing both together for the same subquery
  (`NEEDS_TOOLS` and a populated `validated_task_ids` already co-occur
  elsewhere in this codebase's traces), so the bug was specifically that
  `node_04a` treated "some evidence cleared sufficiency" and "a matching
  capability should be offered" as alternatives instead of two independent
  questions. Fixed by rewriting the rule in `node_04a_resolution_planner`'s
  prompt to ask both questions separately: does approved evidence precisely
  and completely answer what the customer asked, and, separately, does a
  registered capability's own description precisely match the action being
  requested? A "yes" on the second always adds the tool_gap, regardless of
  the first answer — a subquery can be validated from evidence for one part
  of the picture while still needing a tool for the specific action, and
  both should reach the response generator together. Evidence about a
  related-but-different action is adjacent context, never a substitute for
  offering the capability that actually performs the requested action.
- **Bug found via live trace, fixed**: the *initial* response offering the
  form — not the post-submission one — read backwards. For a real "can i
  change my kyc docs?" turn, the generator produced: *"I'm sorry, I don't
  have specific guidance on changing your KYC documents. The available
  information describes how to update a home address via Singpass, which
  doesn't apply to KYC updates. If you need to resubmit a document for your
  KYC record, please complete the KYC document resubmission form below."*
  The system had already registered a fully working form for exactly this
  need, but the response led with an apology and buried the form as a
  third-sentence afterthought — reading as a failure followed by a
  consolation prize instead of a direct answer. Root cause: the original
  prompt instruction told the generator to mention a pending service
  request, but never told it that mention should *lead* when the request
  directly resolves the customer's stated need, rather than being
  subordinate to unrelated partial RAG evidence that happened to also be
  present (here, address-update evidence that doesn't apply to KYC at all).
  Fixed by adding an explicit instruction in `_generate_response_blocks`:
  when a pending service request directly addresses the need, present it as
  the direct answer and lead with it; unrelated approved evidence may still
  be mentioned briefly afterward as optional context, never before the
  invitation and never framed as the reason for redirecting to the form.
- **Bug found via live trace, fixed**: after the first bug above was fixed,
  the same live KYC-resubmission conversation surfaced a second, unrelated
  one — the customer completed the form, the mocked credential check
  passed, the downstream mock ran, but the customer received the generic
  fallback ("there's been an update to your request, but we can't display
  the details right now") instead of a proper confirmation. The
  evidence-groundedness audit had correctly rejected the deterministic
  result template: it said *"Our compliance team will complete
  verification,"* but the mock's evidence payload is only `{reference,
  business_days}` — nothing in it says which team performs the
  verification, so "compliance team" was an unsupported addition, exactly
  the kind of claim this audit exists to catch. The audit and the
  fail-closed fallback both worked correctly; the bug was in the template
  wording, not the architecture. Fixed by removing the specific actor
  attribution from `_SERVICE_REQUEST_RESULT_TEMPLATES["kyc-document-
  resubmission"]` in `nodes.py`, keeping only claims traceable to the
  evidence dict (the reference number and the business-day count, both
  literally present). Worth remembering for any future service_request
  template: every clause needs a literal source in the mock's result
  payload, not just a reasonable-sounding domain inference — the audit does
  not grant templates any more latitude than LLM-authored text, by design.
- **Bug found via live trace, fixed**: the round-2/3 tool-calling planner
  correctly chose `kyc-document-resubmission` for a real "can I change my
  kyc doc?" turn, but keyed it `{"customer_component": "kyc-document-
  resubmission"}` instead of the required `{"service_request_id": "..."}`
  — pattern-matching the `customer_component` key visible elsewhere in the
  same prompt (financial subqueries, rag tasks) despite an explicit
  instruction to use a different key for this tool. The strict-key
  validation correctly rejected the malformed shape each round (fail-closed,
  no wrong task got created), but the net effect was the capability silently
  failing for all 3 planning rounds and the turn falling back to a plain
  "I don't have that information" answer — never registering the form at
  all. Fixed two ways in `node_03a_knowledge_blueprint_planner`: the prompt
  now gives a concrete correct-shape example and explicitly names
  `customer_component` as the wrong key to avoid copying; and, more
  importantly for robustness against future prompt-following slips, the
  parser now recovers a valid `service_request_id` from *any* key in the
  candidate's `input` dict if the expected key is empty or missing, since
  the registered-id set is closed and validated regardless of which key the
  value arrived under — the model's chosen *value* was correct, and a
  cosmetic key mismatch shouldn't discard that.
- **The registry planner integration needed one addition beyond the
  registry itself**: `enabled_tool_capabilities()` in `tool_registry.py`
  now special-cases `service_request` to also surface
  `available_service_requests()` (id/description/risk only, never the form
  schema) so the tool-calling planner's prompt can choose a concrete
  `service_request_id`, not just the tool name. `node_04a` needed no
  changes — it only ever needed to recognize *that* an enabled capability
  could help, which it already did generically.
- **`service_request` never got its own graph node.** Unlike `rag` (which
  needs an async subgraph for retries), a service_request registration is
  a single deterministic DB write with no LLM call, so it resolves inline
  inside `node_03b_tool_dispatcher` via `_dispatch_service_request_task` —
  no new node, no new edge for registration itself.
- **The special route did need a new graph node and a new state field**
  (`service_request_event`, `session_id`) and one new `route_start` branch,
  since — unlike registration — the result has to become an audited chat
  message reachable from a non-chat HTTP entry point. `session_id` also
  had to be threaded into `AgentState` for the first time (it previously
  only existed at the API/checkpointer-config layer, never inside state
  itself); `/api/chat`'s `initial_turn_state` now includes it alongside
  `user_id`.
- **The result text is a deterministic Python template, not LLM-generated**
  — but it still passes through `check_evidence_groundedness` and
  `check_conduct_compliance` before being shown, exactly as Section 10
  requires, with a safe generic fallback if either fails. Fidelity to "every
  customer-facing message passes the same gates" was kept even though
  nothing here needed generation.
- **Live verification ran without `GOOGLE_API_KEY` configured**, so the
  audit calls correctly fail closed to the generic fallback message in that
  environment — this is expected, matches this codebase's existing
  fail-closed pattern everywhere else, and does not indicate a bug. The
  deterministic templates themselves were verified directly (see the
  worked-example wording in Section 11, confirmed to match exactly).

## Changelog

- v1.0 (initial draft, consolidating multi-turn discussion): three-tool risk
  split, registry contract, no-suspended-turns model, registration state
  machine, form schema, mocked credential check + grant mechanism, the
  special resumption route, and multi-request serialization.
- v1.1: added the candidate service-request survey (low-risk,
  intermediate-risk, and boundary/negative examples), the rejected
  full-account-opening-KYC scope note, the `document_reference` field type
  with its mocked-upload treatment, and full worked step sequences for the
  three chosen build targets (address update, card replacement, KYC
  document resubmission as flagship). Confirmed through discussion; not yet
  implemented.
- v1.2: implemented, plus six fixes found through live tracing (see
  Implementation notes) — a planner key-mismatch recovery, two
  evidence-bundle-too-sparse template failures (fixed at the pattern level
  via explicit facts lists), a response-framing fix so a matching service
  request leads instead of following an apology, a node_04a fix so RAG and
  service_request are evaluated as independent coverage questions instead
  of alternatives, and a fix so an unavailable service_request result gets
  acknowledged instead of silently dropped. Section 9 was then superseded:
  the original one-active-request-per-session serialization guard was
  replaced with unlimited concurrent pending requests, a 72-hour validity
  window, and a `user_id`-scoped to-do list UI (badge + full list,
  pending items reopen the form) once live testing showed serialization
  silently dropped a customer's second request with no way to revisit it.
- v1.3: removed the citation-based coverage mechanism entirely after three
  separate live-trace bugs (key confusion, dropped citation, invented key)
  turned out to be the same underlying failure mode — see "Response-
  generation redesign" above. The generator saw only a count and wrote at
  most one generic framing sentence; `node_07` attaches every pending
  request's button independently via a new `serviceRequestActions` list
  (plural, replacing the old single-object `serviceRequestAction`), so
  multiple simultaneous forms in one turn are fully supported and each is
  self-labeled regardless of what the generated text says. Also fixed, from
  the same debugging sequence: a genuinely wrong capability match (a lost
  card matched to a worn/damaged-only replacement form) that had caused a
  full escalation, by teaching both `node_04a` and the tool-calling planner
  to match on a capability's whole description, not its nearest keyword.
- v1.4: removed the LLM-authored generic framing sentence too, after
  finding it was structurally unverifiable regardless of wording (the
  audit gates run before node_07 attaches the button they'd need to see).
  The introduction is now a fully deterministic, translated line
  (`service_request_intro_single`/`_plural` in `translations.py`) appended
  by `node_07` itself, after the buttons already exist; the LLM's only
  remaining connection to the count is steering tone (lead with the
  answer, don't apologize), never producing a statement about a form. Also
  tightened the "naturally related" element-combination rule with a
  concrete example after a redraft collapsed two unrelated topics (a lost
  card and an address change) into one undifferentiated paragraph, losing
  a `PROGRESS` structure the first draft had used correctly.
- v1.5: collapsed the status model to three values — `pending`, `expired`,
  `completed` — removing `denied` entirely (see "Status model redesign"
  above). A failed credential check no longer terminates a registration;
  it resets the submission and leaves the registration `pending` and
  resubmittable up to its existing `expires_at`. This registry's job is
  now stated explicitly as tracking submissions, not transaction outcomes,
  which stay a read-path concern (`api`/`deeplink`/the bank app), not this
  table's.
