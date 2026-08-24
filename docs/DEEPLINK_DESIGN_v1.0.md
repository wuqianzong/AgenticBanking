# Deeplink Capability Design v1.0

**Status:** Design proposal — pending your confirmation. Nothing in this
document is implemented yet. This is the living design doc for the
`deeplink` tool; it will be updated in place as we discuss and build it, so
future agents can pick up exactly where this conversation left off without
you re-explaining the design.

This expands WP-2 in `docs/AGENTIC_CAPABILITY_ROADMAP_v1.0.md` into full
implementation detail. That document's dependency graph and acceptance
criteria still apply; this document is the detail layer underneath it.

---

## 1. Design principle (why this tool exists)

The bot must stay free of transaction data entirely. It never receives,
holds, or reasons about a customer's actual account, card, or transaction
records. `deeplink` resolves a destination *key* to a validated, allow-listed
in-app route and lets the bank's own existing app screen — with its own
access controls — do the actual displaying. This is also why the destination
registry is a strict allow-list rather than a template the model could
parameterize: the full set of places the bot can ever point a customer to is
fixed and reviewed in advance, never constructed at runtime by a model.

Concretely, this replaces a pattern already all over the FAQ corpus today —
the bot telling the customer, in prose, "sign in to the Horizon App, tap
Profile, then Security" — with an actual clickable action that takes them
there directly. It doesn't add a new capability so much as upgrade an
existing one from text instructions to a real UI action.

## 2. Current frontend reality check

This matters for scoping the work, so it's worth stating plainly: the
frontend today (`frontend/src/`) is a single chat widget —
`App.jsx`, `CaseSummary.jsx`, `Stepper.jsx`, nothing else. There is no
router and no concept of a "page" outside the chat panel itself. This means
`deeplink` isn't a backend-only ticket the way `api` (WP-1) mostly is — it
requires:

- a backend half (registry, executor, dispatcher branch, graph wiring — as
  already scoped in WP-2), and
- a frontend half that does not exist yet at all: a minimal way to render a
  destination as an actual screen, since there is currently nothing to
  navigate *to*.

The mock pages proposed in Section 6 are that frontend half's entire scope
for this MVP: static, non-functional screens that exist only so the deeplink
mechanism has somewhere real to send the customer.

## 3. End-to-end flow

```text
Customer: "how do I freeze my card, I think I lost it"
  → node_02d resolves a financial_subquery
  → node_03a proposes a `deeplink` tool_task (destination: card-management)
  → node_03b_tool_dispatcher branches on tool_name == "deeplink"
  → node_03b_deeplink_worker resolves "card-management" against the
    destination registry (Section 4)
  → terminal tool_result: { destination_id: "card-management",
                             route: "/app/cards/manage" }
  → node_04a resolution planning treats this as a satisfied capability
  → response generator produces a short instruction PLUS a UI action
    (Section 5), never fabricated route text of its own
  → node_07 attaches the UI action to ui_action_payload
  → outbound compliance/groundedness gates review the action the same as
    any other customer-facing content
  → frontend renders a button/chip; tapping it opens the mock page
```

No step in this flow ever gives the model a free-text field to write a
route into. The model chooses *which registered destination_id applies*
(or none); the actual route string always comes from the registry, never
from generation.

## 4. Destination registry contract

New file, `backend/app/orchestration/deeplink_destination_registry.py`
(scoped in WP-2). One entry per destination:

```python
DEEPLINK_DESTINATION_REGISTRY: dict[str, dict] = {
    "card-management": {
        "route": "/app/cards/manage",
        "allowed_products": ["Credit_Card", "Debit_Card"],
        "allowed_intents": ["Card Logistics", "Authentication And Security", "Features And Limits"],
        "description": "View & Manage Card screen — freeze, replace, enable overseas usage.",
    },
    ...
}
```

- `allowed_products` / `allowed_intents` bound which financial_subquery
  contexts may even request this destination — the planner should not be
  able to offer `card-management` for a Personal_Loan question, for
  instance.
- Unregistered or scope-mismatched requests resolve to `unavailable`, never
  a best-guess route — same discipline as every other tool in this
  codebase.

## 5. UI payload contract addition

Extend the existing `ui_action_payload` (already handled by
`node_07_precision_substitution`) with a `navigationAction` field:

```json
{
  "navigationAction": {
    "label": "Manage your card",
    "destinationId": "card-management",
    "route": "/app/cards/manage"
  }
}
```

`label` is generator-written (ordinary, audited customer-facing text);
`destinationId` and `route` are always the registry's own values, never
model output. The frontend renders this as a single button/chip; tapping it
navigates to the mock page. This is additive to the payload shape — it does
not replace or interact with the existing `options`/`stepDetails`/etc.
fields used by RAG-sourced responses.

## 6. Suggested mock pages for this MVP

Every Tier 1 destination below is backed by an FAQ answer that *already*
tells the customer, in prose, to go to this exact screen — meaning deeplink
here is a direct upgrade of something the bot already correctly says today,
not a new claim about app structure. Tier 2 is broader product-taxonomy
coverage with no direct FAQ prose backing it; include only if you want
wider demo coverage than the strongest-grounded set.

Each page is static: a header, a handful of labeled mock fields with
plausible placeholder values, and — where the FAQ mentions them — the named
buttons/toggles as inert visual elements (not wired to anything). No page
needs to load real data, submit a form, or persist state.

### Tier 1 — directly grounded in existing FAQ instructions

| destination_id | Page title | Mock content | Products | Backing FAQ |
|---|---|---|---|---|
| `card-management` | View & Manage Card | Masked card number, inert "Freeze Card" / "Replace Card" / "Overseas usage" toggle | Credit_Card, Debit_Card | `faq_7883a8c2` (missing card → freeze), `faq_4da6cc26` (overseas activation) |
| `account-statements` | Statements | A short static list of 3–4 mock past statement rows (month + "Download" as an inert link) | Deposit_Account | `faq_6bdd90e1` |
| `profile-personal-details` | Profile → Personal Details | Mock name, address, phone, email fields shown read-only, with an inert "Edit" affordance | Horizon_APP | `faq_24568045` |
| `security-settings` | Profile → Security | Mock Horizon Key status, inert "Update PIN" button | Horizon_APP | `faq_e50ada50` |
| `pay-credit-card-bill` | Money → Credit Card → Pay | Mock outstanding balance, inert "Pay from Savings" button | Credit_Card, Deposit_Account | `faq_7672e1dd` |

### Tier 2 — broader taxonomy coverage, no direct FAQ prose backing (optional)

| destination_id | Page title | Mock content | Products |
|---|---|---|---|
| `trading-account` | Investments → Trading Account | Mock portfolio summary row, inert "View instruments" list | Stocks, Mutual_Funds |
| `rewards-catalog` | Rewards | Mock points balance, a small static grid of 3–4 inert reward items | Rewards_Program |
| `loan-overview` | Loans → My Loans | Mock loan status card (e.g. "Personal Loan — Active"), inert "View schedule" button | Personal_Loan |
| `insurance-policies` | Insurance → My Policies | Mock policy row with inert "View coverage" button | Insurance |

My recommendation: build **Tier 1 only** for this MVP. It's the set with
direct evidence that the bot already describes these exact screens
correctly today, it covers the products with the heaviest FAQ traffic
(Credit_Card, Debit_Card, Deposit_Account, Horizon_APP), and it keeps the
frontend scope to five small static pages rather than nine. Tier 2 can be
added later with the same registry mechanism, no architecture change
required.

## 7. Safety rules (recap, unchanged from WP-2 / TOOL_CALLING_v2.0)

- Allow-list only: no model-generated destination or route is ever runnable.
- The registry is the sole source of the `route` string; the model only
  ever selects a `destination_id`.
- An unregistered or scope-mismatched destination request terminates
  `unavailable` and never reaches the UI as a link.
- Outbound compliance/groundedness gates review `navigationAction.label`
  like any other customer-facing text.

## 8. Open decisions to confirm before implementation

These are the specific points where more than one reasonable answer exists
— flagging them now rather than picking silently:

1. **Navigation UX** — does tapping the action replace the chat view, open
   an overlay/modal above it, or open in a separate panel? This affects
   whether "return to chat" needs its own affordance on the mock page.
2. **Tier 1 vs. Tier 1+2** — confirm the 5-page MVP scope above, or expand.
3. **Destination-id naming convention** — the kebab-case scheme above
   (`card-management`, `account-statements`) versus something else, so
   WP-1's `api` field-registry and this one stay stylistically consistent.
4. **Mock data realism** — plain static placeholders (as described above)
   vs. slightly randomized/seeded mock data per session, in case the demo
   needs to show the "same" mock account consistently across a session.

## Changelog

- v1.0 (initial draft): end-to-end flow, registry contract, UI payload
  contract, Tier 1/Tier 2 mock page proposal, open decisions. Not yet
  confirmed or implemented.
