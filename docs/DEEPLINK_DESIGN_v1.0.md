# Deeplink Capability Design v1.0

**Status: IMPLEMENTED (MVP).** This document describes the implemented
centralized navigation flow. It supersedes the earlier design proposal that
placed a route in the chat payload.

## 1. Purpose and safety boundary

`deeplink` gives the assistant a safe way to direct a customer to an approved
Horizon screen without exposing account data or allowing a model to construct
a URL. The model may select a registry-backed destination identifier only. It
cannot generate a route, redirect target, or arbitrary link.

The central API owns the destination registry and route resolution. The chat
transcript and frontend receive an opaque request ID plus a safe display label,
never a route or destination identifier.

## 2. Implemented end-to-end flow

```text
Financial subquery
  -> Node 03a Tool-Calling Planner selects an enabled, allow-listed
     deeplink destination ID
  -> Node 03b Generic Tool Dispatcher registers the request centrally and
     validates it against the destination registry and task scope
  -> Deep_Link_Request persists request ID, user/session, destination,
     resolved route, and status; the graph retains only deeplink_request_id
  -> Node 07 emits navigationActions [{linkId, label}]
  -> Persistent chat history stores only that opaque link ID and label
  -> Customer clicks the action
  -> GET /api/deeplink/open/{linkId} re-checks ownership/status and returns
     a 307 redirect to the centrally resolved frontend route
  -> The MVP frontend maps that route to its static demonstration screen.
```

The response generator can use a safe destination label as presentation
context, but it is never given a route, an opaque link ID, or a free-text
destination field.

## 3. Registry and request contract

The allow-list lives in `backend/app/services/deeplink_service.py`. A registry
entry defines the internal route, safe customer label, and applicable product /
intent scope. The dispatcher calls `register_and_resolve(...)`; an unknown or
scope-mismatched destination becomes `unavailable` and cannot create a link.

`Deep_Link_Request` is the central ledger. It records the requested
destination and resolved route for server-side use only. The terminal graph
result is deliberately minimal:

```json
{"deeplink_request_id": "dlr_..."}
```

The customer-facing UI contract is deliberately different:

```json
{
  "navigationActions": [
    {"linkId": "dlr_...", "label": "Manage Card"}
  ]
}
```

`linkId` is opaque. It is not a route, destination ID, or authorization token.
The central endpoint resolves it again when clicked.

## 4. Frontend and endpoint behaviour

The frontend navigates only to the backend endpoint:

```text
GET /api/deeplink/open/{request_id}?user_id={current MVP user}
```

The endpoint validates ownership and resolved status, then returns a `307`
redirect to the configured frontend origin and the registry route. The browser
path is then used only to select the static MVP mock screen. No route is read
from a chat response or history payload.

The `user_id` query value is an MVP substitute for real authentication. A
production deployment must derive the customer identity from authenticated
server-side session credentials rather than from a client-supplied parameter.

## 5. Historical payload migration

`deeplink_service` includes the one-time `opaque_navigation_payloads_v2`
migration. It replaces old persisted `navigationActions` that contained a
`route` or `destinationId` with an opaque `linkId` and safe label, and creates
the associated central request record. The migration is recorded in
`Deep_Link_Migration` and does not repeatedly scan history after completion.

## 6. Security properties

- Allow-list only: no LLM-generated route can execute.
- Central re-authorization: a historical click is re-checked at open time.
- Least disclosure: persistent chat and the client see only opaque IDs and
  labels.
- Scope validation: destination applicability is checked before link creation.
- Audited presentation: navigation labels remain part of the normal outbound
  content audit.
- Safe failure: unavailable, unknown, unresolved, or ownership-mismatched
  requests do not redirect.

## 7. Related implementation

- `backend/app/services/deeplink_service.py`
- `backend/app/orchestration/nodes.py` (generic dispatcher and Node 07)
- `backend/app/api_gateway.py` (`/api/deeplink/open/{request_id}`)
- `frontend/src/App.jsx` (opaque-action click and static MVP screen mapping)
- `docs/TOOL_CALLING_v3.0.md`

## Changelog

- v1.0 implemented update: centralized request ledger, opaque chat payload,
  server-side redirect, historical-payload migration, and static MVP route
  rendering.
