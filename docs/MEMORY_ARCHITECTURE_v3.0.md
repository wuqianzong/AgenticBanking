# Horizon Memory Architecture Specification v3.0 (Implemented)

**Status:** Implemented. Supersedes `MEMORY_ARCHITECTURE_v1.0.md` (the
system this replaced) and `MEMORY_ARCHITECTURE_v2.0.md` (a design proposal
for a richer per-task memory layer that was written but never built).

## 1. Why cross-session memory was dropped, not redesigned

The project requirement does not call for cross-session conversational
continuity: a customer's history from a previous login does not need to be
carried into the next one. Given that, the v1.0 mechanism -- generating and
Groundedness-v2.0-validating a `last-session` summary and a `five-session
baseline` for every user at every checkout, then re-injecting both into
every future prompt as advisory context -- was pure cost with no required
benefit:

- Every checkout paid for an LLM summary call plus two-axis groundedness
  validation (up to 3 attempts each), and every fifth-or-so checkout also
  paid for a baseline-consolidation call and its own validation pass, all
  regardless of whether the resulting text was ever read again.
- The greeting itself was an LLM call audited by two more LLM calls
  (conduct + evidence), auditing content whose only real inputs were two
  strings that were frequently empty.
- Carrying free-text summaries across sessions is also a standing prompt-
  injection and data-retention surface with no corresponding requirement to
  justify it.

v2.0 proposed replacing the *content* of cross-session memory with a
cleaner per-task record. That direction is now moot: the decision is to
carry no conversational content across sessions at all, and to answer the
one question a greeting actually needs -- "is there anything this login
should surface before the customer asks?" -- with direct, on-demand,
deterministic database checks instead of any stored or generated summary.

## 2. What was removed

- `Persistent_Customer_Dialogue_Registry` (per-session `compact_summary_footprint`,
  `session_exit_status`) and `Persistent_User_Baseline_Memory` -- both the
  tables and every function that read or wrote them
  (`fetch_compact_footprint`, `fetch_user_baseline`, `refresh_user_baseline`,
  `compile_and_archive_session`).
- The LLM-generated greeting (`_generate_returning_customer_greeting`) and
  its dedicated outbound audit branches in `node_06_outbound_groundedness_gate`
  and `node_06_outbound_audit_join`.
- The `last_session_summary` / `user_baseline_summary` state fields are no
  longer populated by anything. Other prompts that read them via
  `state.get(..., "")` (triage, planner, response generator) degrade
  harmlessly to "None" -- they were always designed to treat this context as
  optional, so no other node required a code change.

## 3. What was kept

- **Durable raw transcript** (`Persistent_Chat_Message`, written by
  `persist_chat_history`): unchanged. This is what powers "load earlier
  messages" and cross-session transcript browsing (`/api/history/*`). It is
  a factual record, never summarized into a claim, so it carries no
  groundedness risk and needed no redesign.
- **In-session rolling compaction** (`compacted_history_summary`,
  `summarize_trimmed_history`): unchanged. This only ever spans the current
  graph session (trims history before turn 11, 21, ...) and is a different
  problem from cross-session memory -- it still needs an LLM call and still
  goes through `node_memory_summary_validator`.
- **Session-checkout archiving**: still happens, but is now a pure SQLite
  write (`persist_chat_history`). No LLM call, no baseline refresh.

## 4. The new greeting: on-demand, deterministic, unaudited

`node_00_context_hydrator_and_greeting` no longer calls an LLM. It runs
three direct, cheap checks and assembles the greeting from fixed,
translated templates:

```text
node_00
  1. has_prior_session(customer_id)
       SELECT 1 FROM Persistent_Chat_Message WHERE customer_id = ? LIMIT 1
       -> chooses "greeting_new" vs "greeting_returning" (translated,
          optionally with the customer's nickname substituted in)
  2. service_request_service.list_registrations(customer_id)
       filtered to status == PENDING
       -> if non-empty: appends a deterministic one-sentence reminder
          naming the true total and distinct request types, pointing at
          the header's requests icon (see below) -- never attaches
          serviceRequestActions to the greeting turn itself.
  3. fetch_recent_turn_resolutions(customer_id, limit=29)
       SELECT ... FROM Turn_Resolution_Feedback WHERE customer_id = ?
       ORDER BY created_at DESC LIMIT 29
       -> seeds turn_escalation_history with this customer's real
          standing history across every past session, not an empty list
          -- see RAG_RETRY_EFFORT_v1.0.md §4a for why this needed to
          survive a login rather than reset on every one.
```

Both checks are direct reads of durable, verifiable state -- never a
generated claim -- so the greeting needs no groundedness or conduct audit.
`node_00` now routes straight to `END` in `graph.py`, bypassing the entire
outbound audit / precision-substitution / escalation-analysis chain, the
same way the service-request special route (`n_svc`) already does.

**The pending-request reminder no longer attaches the form itself.**
Originally step 2 also attached `serviceRequestActions` to the greeting's
`ui_action_payload`, exactly as `node_07` does mid-conversation, so the
form was immediately actionable from the greeting. This stopped being
correct once the frontend's embedded service-request rendering changed
from a compact shortcut into a full inline form (`renderEmbeddedServiceRequest`):
attaching it to the greeting meant the form occupied the entire chat
screen immediately after every single login. The pending badge/count is
already refreshed independently via the frontend's own
`fetchServiceRequestList` poll, so nothing is lost by leaving the greeting
as a pointer rather than an attachment -- the customer opens the form
deliberately from the header icon instead.

**The reminder also now reports the true total, not just the latest
item.** `_build_deterministic_greeting` (`nodes.py`) previously followed
only `pending_requests[0]` (newest-first), silently ignoring any others --
live-observed to undercount by a factor of 11 on one heavily-tested
account (nine `Change Address` resubmissions plus one each of two other
types, reported as just "one pending request"). It now groups all pending
records by `service_request_id`, resolves each distinct type's
`action_label` once, and picks one of three deterministic templates:
singular (`service_request_reminder_single`), same-type plural
(`..._plural_same`, count + one label), or mixed-type plural
(`..._plural_mixed`, count + a natural "A, B, and C" join of distinct
labels via `_natural_join`) -- never one sentence per individual pending
row, which would be unreadable for a customer with several duplicates of
the same request.

**`node_00b_followup_check` narrowed to transcript-only.** It previously
also read `pending_shortcuts` off the greeting's `serviceRequestActions`
to let its own LLM-generated follow-up mention them again -- now dead
weight, since `node_00`'s reminder above already covers pending requests
deterministically and unconditionally, before `node_00b` even runs.
`has_followup_candidate` (which decides whether `node_00b` runs at all,
via `route_n00`) now depends only on `last_session_transcript`; the node's
prompt, evidence bundle, and groundedness rules were all trimmed to match,
leaving it solely responsible for surfacing an unresolved conversational
topic from the customer's last session.

This also means a session that produces no substantive conversation (e.g.
a login immediately followed by a logout) no longer has any way to degrade
the *next* greeting: there is nothing computed and stored from a prior
session for the new one to be missing.

## 5. Net effect

Per login/logout cycle, this removes: 1 greeting-generation LLM call, 2
greeting-audit LLM calls, 1 session-summary LLM call, its up-to-3-attempt
groundedness+conduct validation, 1 baseline-consolidation LLM call (on
roughly every 5th checkout), and its own validation pass -- all unconditional
costs regardless of whether the memory was ever read. What remains is the
in-session rolling summarizer (still needed, still audited, but scoped to a
single live session) and two cheap SQL reads at login.
