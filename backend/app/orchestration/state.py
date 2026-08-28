from typing import List, Dict, Any, TypedDict
from langchain_core.messages import BaseMessage

class AgentState(TypedDict):
    # --- PERSISTENT MEMORY (Cross-Turn) ---
    chat_history: List[BaseMessage]
    history_sequence_offset: int                 # Number of trimmed messages before chat_history
    compacted_history_summary: str               # Rolling summary of messages trimmed from graph memory
    last_session_summary: str                    # Dead field: no cross-session summary is generated or stored (see MEMORY_ARCHITECTURE_v3.0.md)
    user_baseline_summary: str                   # Dead field: no cross-session baseline is generated or stored (see MEMORY_ARCHITECTURE_v3.0.md)
    last_session_transcript: List[Dict[str, Any]]  # On-demand, unstored raw read of the prior session's trusted messages, for the login follow-up check only
    personal_data_reveal_map: Dict[str, str]      # Per-turn {{data-*}} token -> real value for completed placeholder-mode `api` fetches. No LLM prompt-building function may ever read this field -- only the tool dispatcher (writes it) and node_07 (reads it to build the outbound payload) may touch it. See docs/AGENTIC_CAPABILITY_ROADMAP_v1.0.md WP-1/WP-3.
    
    # V2.1: The 2D Relational Matrix (Noun + Verbs)
    active_product_intents: List[Dict[str, Any]] # e.g., [{"product": "Credit_Card", "intents": ["Dispute_Transaction"]}]
    
    active_plan: Dict[str, Any]                  # e.g., {"steps": [], "current_step_index": 0}
    completed_steps: List[str]                   # Stateful Milestone Tracker for Anti-Amnesia
    process_slots: Dict[str, Any]                # Verified data (e.g., {"age": 25})
    verified_task_history: List[Dict[str, Any]]  # Latest verified turn records: existing search-task snapshots plus approved parent IDs

    # Soft escalation (node_08): session-scoped signals, reset only at a new
    # login, never per-turn. See node_08_soft_escalation_analyzer's
    # docstring for the four independent trigger rules these feed.
    session_started_at: str                      # ISO timestamp, set once at login; used for the long-session duration signal
    same_intent_streak_count: int                 # Consecutive turns with an unchanged active_product_intents set
    turn_escalation_history: List[Dict[str, Any]]  # Rolling window (last MAX_ESCALATION_HISTORY_TURNS turns) of {"turn_number", "had_repeated_need", "subqueries": [{"text", "score"}]} -- feeds the confidence and repeated-need signals

    # --- VOLATILE TURN STATE (Reset per turn) ---
    user_id: str
    session_id: str
    user_profile: Dict[str, Any]
    user_input: str
    service_request_event: Dict[str, Any]        # Non-chat entry point payload for the WP-7 special route (see node_svc_service_request_resolver)
    preflight_status: str                        # Maps edge routing conditions
    knowledge_blueprint: Dict[str, Any] 
    target_path: str
    working_context: str
    final_draft: str
    final_answer: str
    final_clarification: str
    final_response: str
    ui_action_payload: Dict[str, Any]
    active_tool_routes: List[str]                # The list of parallel nodes to trigger
    rag_payload: str                             # Buffer for RAG agent
    api_payload: str                             # Buffer for API agent
    deeplink_payload: str                        # Buffer for Deeplink agent
    triage_status: str                           # 'NON_COMPLIANT', 'ESCALATION', 'NON_FINANCIAL', or 'VALID_REQUEST'
    repeated_need_detected_this_turn: bool       # Set by node_02d/triage: true when any of this turn's financial subqueries substantially repeats a recent prior one. Consumed only by node_08's soft-escalation rollup.
    low_confidence_mode: bool                    # Set by node_03c when the rolling bot-confidence over recent turns is genuinely below threshold (not merely a session with no track record yet) -- surfaced to the frontend progress stream as "Thinking harder" (see api_gateway.py's chat_stream).

    # Tool Calling v3.0: financial-only triage, each subquery owning its own
    # tool attempts. See docs/TOOL_CALLING_v3.0.md. financial_subqueries
    # entries are {subquery_id, customer_component, resolution_status,
    # tool_attempts: [{attempt_id, subquery_id, tool_name, operation,
    # planning_round, status, input, result, availability_reason}]} --
    # resolution_status (PENDING|RESOLVED|OPEN) is computed deterministically
    # by _compute_subquery_status in node_04a, never asserted by an LLM. The
    # flat tool_tasks/tool_results/pending_tool_task_ids/tool_task_fingerprints/
    # tool_gaps ledgers this superseded are gone; a `rag` attempt's `result`
    # is a terminal-only projection of RAG's own internal search_tasks/
    # task_results world below, written once by _finalize_rag_attempts.
    triaged_subqueries: List[Dict[str, Any]]     # Legacy alias for financial_subqueries during in-flight sessions
    financial_subqueries: List[Dict[str, Any]]   # n2d-resolved, compliant banking components; see the schema note above
    has_non_financial_content: bool              # Triage marker; never carries discarded wording downstream
    non_financial_context: List[str]             # Safe conversational asides; never a tool input or factual evidence source
    tool_planning_round: int                     # Bounded outer orchestration-loop count
    tool_plan_status: str                        # continue | resolved | exhausted
    decline_tasks: List[Dict[str, Any]]          # Legacy downstream scope-decline compatibility only
    unresolvable_search_tasks: List[Dict[str, Any]]  # A fresh SEARCH resolution whose product/intent pairing the matrix itself rejected -- accumulated the same manual way as decline_tasks (never a LangGraph-reduced field) so it survives a mixed turn where node_03c also writes task_resolution_instructions for a sibling task this same round. Rendered as a no_info entry, never a decline: the matrix rejecting a RAG formulation only proves RAG can't answer it from FAQ text, never that Horizon doesn't offer the underlying service.

    # Search strategy v2.6 tasks: stable direct subquery plus optional angle groups
    search_tasks: List[Dict[str, Any]]           # Immutable task definitions with stable task_id values
    task_results: List[Dict[str, Any]]           # Parent-ID evidence selections and sufficiency result per task
    task_retry_counts: Dict[str, int]            # Retry count keyed by task_id
    deferred_retry_task_ids: List[str]           # Meta-filter no-op tasks waiting for sibling retry retrieval before angle expansion
    task_feedback: Dict[str, List[Dict[str, str]]]  # Task-specific observed candidate-evidence deltas
    task_sufficiency_history: Dict[str, List[Dict[str, Any]]]  # Current-turn audit trail used to keep repeated sufficiency judgments consistent
    task_partial_evidence: Dict[str, List[str]]  # Parent IDs retained while a task is only partly answerable and continues retrieval
    task_rejected_parent_ids: Dict[str, List[str]]  # Parent IDs a prior sufficiency audit explicitly judged not relevant; excluded from future candidate pools for the same task so a widened retry can't waste a selection slot re-surfacing known-bad evidence
    intent_clarification_tasks: List[Dict[str, Any]]  # Unresolved user-derived components that cannot form a valid retrieval task
    task_resolution_instructions: List[Dict[str, Any]]  # Terminal retrieval feedback for natural clarification generation
    safe_answer_for_fallback: str                 # Individually approved answer retained for deterministic clarification fallback
    clarification_fallback_required: bool         # True only when clarification retries exhausted while answer remains safe
    selected_ux_journey: str                     # FLEXIBLE UX guidance (legacy TEXT/PROGRESS/LIST values may complete in-flight sessions)
    presentation_plan: Dict[str, Any]            # Internal UX guidance; never a required customer-visible task/block plan
    outbound_compliance_result: Dict[str, Any]    # Focused outbound MAS-compliance audit result
    outbound_groundedness_result: Dict[str, Any]  # Audit result plus structured, non-evidential grounding_violations for retries
    pending_advance: bool                           # V4.1 Fix: Tracks if the current step should advance upon safety clearance

    # --- ISOLATED LOOP TRACKERS ---
    safety_retry_count: int                      # Max 3
    turn_count: int
    search_feedback_history: List[str]
    draft_feedback_history: List[str]
