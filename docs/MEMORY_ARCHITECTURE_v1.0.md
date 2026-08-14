# Horizon Memory Architecture Specification v1.0

## 1. Purpose and scope

This specification describes the memory system implemented by the Horizon banking FAQ assistant. The design provides conversational continuity and user-scoped personalization while keeping each login a new LangGraph execution session.

The assistant is a read-only FAQ/navigation service. Memory is advisory context only. It must not be treated as proof of customer identity, verified account data, authorization to perform banking actions, or a restored transactional workflow.

## 2. Design principles

- **Clean graph sessions:** Login does not restore a prior plan, task evidence, RAG payload, retry counter, or other execution state.
- **Durable history, bounded prompts:** Complete history is retained for UI/audit retrieval; prompt context is bounded through a rolling ten-turn window and compaction.
- **User isolation:** Durable records are scoped by `customer_id`; the active UI mock profile currently uses `USR_999`.
- **Trust before reuse:** A user message is untrusted until it passes the inbound safety gate. Untrusted text remains visible in history but must not enter memory summaries or prompt history.
- **Fresh policy retrieval:** Previous discussion may guide interpretation, but answers are generated against the current RAG result rather than saved RAG payloads.
- **UI is not memory:** Chips, checkbox labels, stepper entries, suggested replies, and payload options are presentation metadata, not user decisions.

## 3. Architecture overview

```text
                         ┌────────────────────────────────┐
                         │ React chat UI                   │
                         │ active session ID + user ID     │
                         └───────────────┬────────────────┘
                                         │
                                         ▼
┌─────────────────────────────────────────────────────────────────────┐
│ FastAPI gateway                                                      │
│ - streams graph progress and final UI payload                        │
│ - persists each completed turn                                       │
│ - compacts before turns 11, 21, 31, ...                              │
└───────────────┬─────────────────────────────────────────────────────┘
                │
                ▼
┌─────────────────────────────────────────────────────────────────────┐
│ LangGraph MemorySaver (ephemeral graph state)                        │
│ - latest 10 turns / 20 messages                                      │
│ - rolling compact summary                                             │
│ - active presentation plan and turn-only state                        │
└───────┬────────────────────────────┬────────────────────────────────┘
        │                            │
        │ checkout / timeout         │ prompt context
        ▼                            ▼
┌─────────────────────┐     ┌────────────────────────────────────────┐
│ SQLite durable store │     │ Selected LLM prompts                    │
│ simulated_ledger.db │     │ - request triage                       │
│                     │     │ - intent/query formulator               │
│ transcript          │     │ - response generator                    │
│ session summary     │     │ (only trusted recent user messages)     │
│ five-session        │     └────────────────────────────────────────┘
│ user baseline       │
└─────────────────────┘
```

## 4. Memory layers

| Layer | Storage | Lifetime / size | Primary use |
|---|---|---|---|
| Graph working memory | LangGraph `MemorySaver` | Process-local; current thread | Active plan, task-level evidence, recent message objects, and turn state. |
| Recent conversation window | `chat_history` in graph state | Latest 20 messages / 10 turns | Short-term prompt context. |
| Rolling compact memory | `compacted_history_summary` in graph state | Current graph session; target 100 tokens | Compounded summary of messages removed from the recent window. |
| Durable chat history | `Persistent_Chat_Message` | SQLite; paginated | UI “Load earlier messages”, refresh recovery, audit transcript. |
| Last-session memory | `Persistent_Customer_Dialogue_Registry.compact_summary_footprint` | SQLite; target 100 tokens per completed session | Returning greeting and advisory cross-session context. |
| User baseline memory | `Persistent_User_Baseline_Memory` | SQLite; one record per user, target 150 tokens | Recurring needs/preferences/themes derived from up to five completed sessions. |

## 5. Graph working memory

### 5.1 Contents

`AgentState` maintains both conversational and orchestration fields. Relevant memory fields are:

- `chat_history`: LangChain `HumanMessage` and `AIMessage` objects.
- `history_sequence_offset`: number of messages previously removed from graph history.
- `compacted_history_summary`: compounded compact context for removed messages.
- `user_baseline_summary`: read-only baseline loaded at Turn 0.
- Workflow state such as `active_product_intents`, `active_plan`, `completed_steps`, task-level evidence, and the selected response experience.

### 5.2 New login behavior

Turn 0 (`**INIT**`) runs the context hydrator. It loads the latest valid session summary and optional user baseline, creates a returning greeting, then explicitly starts graph execution state clean:

- empty `chat_history`;
- no active plan, task evidence, retrieved text, or process slots;
- retry trackers and other volatile fields reset.

The latest summary and baseline are advisory context only. They do not resume a plan or authorize an unfinished operation.

## 6. Trust lifecycle for user messages

### 6.1 States

| State | Meaning | Visible in history | Eligible for prompt/summaries |
|---|---|---:|---:|
| `untrusted` | Newly submitted user message or a message rejected by inbound controls | Yes | No |
| `trusted` | User message approved by the inbound semantic gate | Yes | Yes |

Assistant messages are treated as trusted because outbound deterministic and semantic gates run before they are finalized.

### 6.2 Promotion path

1. Node `n3` appends a submitted `HumanMessage` with `additional_kwargs.trust_status = "untrusted"`.
2. Deterministic checks reject obvious XSS, SQL-drop, and placeholder-injection patterns.
3. Prompt Guard (`n16`) evaluates the input. The MVP trust-promotion cutoff is **10%**: a score below `0.10` passes; a score of `0.10` or higher is rejected.
4. Only on a below-10%-risk Prompt Guard result does `n16` replace the latest message with an equivalent message marked `trusted`.
5. Rejected text remains untrusted and the visible transcript records it.

The graph routes deterministic rejections directly to standard rejection. The inbound Prompt Guard runs before the pending journey-switch confirmation model, so untrusted input is not supplied to that downstream LLM.

### 6.3 Prompt filtering

`_build_conversation_context()` is the shared history builder for prompts that use chat context. It includes all assistant messages but includes user messages only when `trust_status == "trusted"`.

The builder is used by:

- user journey classification;
- intent/query formulation;
- RAG sufficiency auditing;
- response generation.

## 7. Rolling compact memory

### 7.1 Trigger and operation

Compaction is scheduled before processing turns 11, 21, 31, and subsequent ten-turn boundaries. The gateway checks `previous_turn_count`; if `(previous_turn_count + 1) % 10 == 1`, it compacts before graph invocation.

The frontend has already submitted the message and disables input while processing. The gateway streams a `memory_compaction` progress node, shown as “Organizing your conversation context…”. This prevents a second turn from racing the graph-state update.

Compaction:

1. reads the active graph state;
2. retains the most recent 20 messages;
3. sends the older trusted user messages plus assistant messages to the compact summarizer;
4. combines the result with the existing `compacted_history_summary`;
5. updates graph state with the retained window, incremented `history_sequence_offset`, and new compact summary.

### 7.2 Compounding behavior

The compact summary is not replaced by only the most recently evicted block. The summarizer receives both:

```text
Existing compact memory
Newly evicted conversation
```

Its output becomes the next `compacted_history_summary`. The result represents all earlier eligible conversation in compressed form while raw graph history holds the newest ten turns.

### 7.3 Summary constraints

The rolling compaction prompt requires:

- banking-FAQ-relevant content only;
- explicit, supported facts only;
- no inference, embellishment, gap filling, or hallucination;
- omission of uncertain details, unrelated topics, and small talk;
- exclusion of UI payload metadata;
- exclusion of untrusted user content before the prompt is built.

## 8. Durable chat history

### 8.1 Storage

SQLite file: `backend/data/simulated_ledger.db`.

Table: `Persistent_Chat_Message`.

| Column | Purpose |
|---|---|
| `message_id` | UUID primary key |
| `customer_id` | User ownership boundary |
| `session_id` | Chat session / graph thread identifier |
| `message_sequence` | Ordered position within a session |
| `role` | `user` or `assistant` |
| `content` | Original visible message content |
| `created_at` | Persistence timestamp |
| `archive_reason` | `turn_0`, `turn_complete`, checkout, etc. |
| `trust_status` | `trusted` or `untrusted` for user content |
| `ui_payload` | Persisted structured UI payload for assistant messages |

The `(session_id, message_sequence)` uniqueness constraint makes repeated per-turn persistence idempotent.

### 8.2 UI retrieval

- `/api/history/{user_id}` returns a user- and session-filtered page, newest page first then restored chronologically.
- `/api/history/{user_id}/previous` retrieves earlier sessions for the same user.
- The frontend initially retrieves 20 messages (10 turns), supports a “Load earlier messages” action, and can auto-load when scrolled to the top.
- Structured UI payloads remain stored for historical rendering. Historical stepper completion controls are read-only because graph workflow state is not restored.

## 9. Last-session memory

### 9.1 Creation

On logout or timeout, `/api/session/checkout` calls `compile_and_archive_session()`.

The process:

1. ensures the durable transcript is persisted;
2. reads the session transcript, excluding user rows whose `trust_status` is not `trusted`;
3. compacts the eligible content into a banking-only session summary;
4. stores the original serialized eligible transcript and compact summary in `Persistent_Customer_Dialogue_Registry`;
5. refreshes the user baseline.

If graph memory is unavailable after a backend restart, checkout falls back to the persisted transcript. This preserves availability for history recovery and session compaction.

### 9.2 Retrieval and greeting

`fetch_compact_footprint(customer_id)` selects the latest non-empty session summary and ignores known error placeholder summaries. The context hydrator uses it to produce a neutral FAQ-recall greeting.

Returning greetings must not request age, income, residency, identity, documents, or account data; imply a transaction is in progress; or claim the assistant can perform a banking action. They may recall prior FAQ guidance and invite the user to continue the discussion or start a new topic.

## 10. User baseline memory

### 10.1 Storage

Table: `Persistent_User_Baseline_Memory`.

| Column | Purpose |
|---|---|
| `customer_id` | Primary key and ownership boundary |
| `baseline_summary` | Consolidated advisory user context |
| `source_session_ids` | JSON list of contributing sessions |
| `updated_at` | Refresh timestamp |
| `schema_version` | Baseline format version |

### 10.2 Creation

After each successful session-summary write, `refresh_user_baseline()` reads up to five most-recent valid session summaries for the user, builds a new bounded baseline, and upserts it. The baseline is overwritten as a consolidated view; it does not grow indefinitely.

The baseline prompt retains only stable banking topics, explicit communication preferences, explicitly stated facts, and unresolved themes. It favors newer explicit information on conflict and must not infer financial preferences, make decisions, preserve UI metadata, or claim a workflow remains active.

### 10.3 Use

The baseline is loaded at Turn 0. It is supplied only to:

- user journey classification;
- intent/query formulation;
- response generation.

It is not supplied to prompt-injection checks, deterministic validation, RAG sufficiency auditing, precision substitution, or outbound safety gates.

## 11. Memory input/output safeguards

### 11.1 Input safety

- New user content begins untrusted.
- Deterministic and semantic inbound gates decide whether it can be promoted.
- Untrusted user text is excluded from prompt history and memory compaction sources.
- User data remains scoped by `customer_id` in durable history queries.

### 11.2 Summary safety

All memory-writing prompts specify:

- banking FAQ context only;
- no unsupported claims, inference, embellishment, or gap filling;
- omit uncertain details rather than guess;
- exclude unrelated content and small talk;
- exclude all UI payload metadata.

Memory summary calls request JSON objects and parse a required string field (`summary` or `baseline`) before storing the result. This is compatible with the shared LLM client’s JSON-object response mode.

### 11.3 Dedicated memory-summary validation node

`LongTermMemoryManager.node_memory_summary_validator()` validates every candidate rolling compact, last-session summary, and five-session baseline before storage. It first applies Prompt Guard using the same **10% cutoff**: a probability of `0.10` or greater fails validation. No rejected candidate is stored as a rolling compact, session summary, or baseline. Prompt Guard is solely responsible for malicious-intent and prompt-injection assessment. Gemini 3.5 Flash Lite then focuses on grounding against the original eligible source and non-compliant banking content. The Gemini validator and live outbound semantic gate share the same `MAS_REGULATIONS_CONTEXT` from `backend/app/core/compliance.py`.

Each memory generator has up to three generate/validate attempts. Validation feedback is supplied to the next rewrite. After the third failure, no new memory is stored: rolling compaction keeps the prior graph state, last-session compaction does not create a summary record, and baseline refresh retains the prior baseline.

### 11.3 Output safety

Regular assistant replies traverse outbound deterministic and semantic gates. Precision placeholders are validated against the SQLite reference table before substitution. These gates are separate from memory controls.

## 12. Failure handling and graceful degradation

| Condition | Behavior |
|---|---|
| No prior session summary or baseline | First-time greeting; no personalization context. |
| Session summary exists, baseline absent | Greeting uses the latest session summary only. |
| Baseline exists, latest session summary absent | Greeting can use the baseline. |
| Baseline refresh fails | Previous baseline is retained; checkout and chat continue. |
| No valid eligible content for rolling compaction | Existing rolling summary remains unchanged. |
| Compaction fails | Existing graph context is retained; response flow continues. |
| Backend restarted before checkout | Checkout reads the persisted transcript if graph memory is unavailable. |
| Existing database lacks new fields | `ensure_memory_schema()` performs additive migrations for `ui_payload` and `trust_status`. |

## 13. Observability

`backend/app/core/system_logger.py` writes JSONL system logs to `backend/logs/system.jsonl` with request/session/user context. Logged events include chat requests, LLM raw prompts and responses, graph node completion, checkout, response metadata, and errors. The memory service also logs transcript persistence, session-summary generation/persistence, baseline refreshes, and every memory-validation start, pass, failure, retry, and three-attempt abort.

Operational log messages also identify transcript archival, baseline refreshes, compaction counts, and skipped memory work.

Because raw prompts and responses can contain customer content, access to logs must be controlled in a production deployment.

## 14. Operational limitations and follow-up controls

- **MemorySaver is process-local.** It is not a durable graph checkpointer; durable continuity is transcript and summary based.
- **Summary quality is probabilistic.** Prompt restrictions are reinforced by the dedicated memory-summary validator, but production monitoring and periodic test cases remain necessary.
- **Trust migration is forward-looking.** Existing durable user rows created before `trust_status` was introduced receive the SQLite default `trusted`; they have not been retrospectively reclassified.
- **Restart fallback requires trust preservation.** When reconstructing graph messages from durable history for checkout fallback, the implementation should preserve each row’s stored `trust_status` on reconstructed `HumanMessage` objects. This should be treated as a security-hardening follow-up so untrusted historical rows cannot be reintroduced into a fallback summary source.
- **Retention and deletion policy are not implemented.** A production banking deployment requires approved retention schedules, customer data deletion/export mechanisms, encryption controls, database access control, monitoring, and privacy/compliance review.
- **No semantic retrieval index exists for user baseline records.** The system uses the single bounded baseline per user rather than relevance-scored memory retrieval.

## 15. Primary implementation locations

| Component | File |
|---|---|
| API lifecycle, persistence, scheduled compaction | `backend/app/api_gateway.py` |
| Graph routing and safety order | `backend/app/orchestration/graph.py` |
| Trust promotion, context construction, greetings, prompt use | `backend/app/orchestration/nodes.py` |
| Graph state definition | `backend/app/orchestration/state.py` |
| SQLite schema, persistence, summaries, baseline | `backend/app/services/memory_service.py` |
| Login, history pagination, progress UI | `frontend/src/App.jsx` |
| System event logging | `backend/app/core/system_logger.py` |
