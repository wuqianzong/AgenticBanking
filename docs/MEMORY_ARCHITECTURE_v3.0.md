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

`node_00_context_hydrator_and_greeting` no longer calls an LLM. It runs two
direct, cheap checks and assembles the greeting from fixed, translated
templates:

```text
node_00
  1. has_prior_session(customer_id)
       SELECT 1 FROM Persistent_Chat_Message WHERE customer_id = ? LIMIT 1
       -> chooses "greeting_new" vs "greeting_returning" (translated,
          optionally with the customer's nickname substituted in)
  2. service_request_service.list_registrations(customer_id)
       filtered to status == PENDING
       -> if non-empty: appends the existing deterministic
          service_request_intro_single/plural line, and attaches
          serviceRequestActions to the greeting turn's ui_action_payload
          exactly as node_07 already does mid-conversation, so a pending
          form is immediately actionable from the greeting itself.
```

Both checks are direct reads of durable, verifiable state -- never a
generated claim -- so the greeting needs no groundedness or conduct audit.
`node_00` now routes straight to `END` in `graph.py`, bypassing the entire
outbound audit / precision-substitution / escalation-analysis chain, the
same way the service-request special route (`n_svc`) already does.

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
