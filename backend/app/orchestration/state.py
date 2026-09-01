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
    triage_status: str                           # 'NON_COMPLIANT', 'ESCALATION', or 'DECOMPOSED'
    repeated_need_detected_this_turn: bool       # Set by node_02d/triage: true when any of this turn's financial subqueries substantially repeats a recent prior one. Consumed only by node_08's soft-escalation rollup.
    low_confidence_mode: bool                    # Set by node_03c when this task's own live dynamic confidence is genuinely below threshold (not merely a session with no track record yet) -- surfaced to the frontend progress stream as "Thinking harder" (see api_gateway.py's chat_stream).
    live_task_confidence: float | None            # Set by node_03c each round: average _dynamic_task_confidence across this round's tasks, 0.0-1.0. None when RAG hasn't run this turn. Streamed live to the frontend progress bar, updating once per retry round -- see RAG_RETRY_EFFORT_v1.0.md.
    territory_b_unlocked: bool                   # Extra Effort v2.1: set by node_03c each round via EFFORT_TIER_GATE/_attempt_unlocked(MAXIMUM_EFFORT_ATTEMPT, ...) -- whether node_02e may construct a Territory B (cross-topic) companion this retry pass, alongside Territory A's own unconditional filter-widen. Read only by node_02e's retry section.

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
    #
    # node_02d always decomposes the whole turn into components and tags
    # each one TOOL_ELIGIBLE / NON_FINANCIAL / CLARIFICATION (an exhaustive
    # partition, the same principle node_02e's own SEARCH/CLARIFY/DECLINE
    # split already uses one node downstream). Only TOOL_ELIGIBLE
    # components ever land here -- a bypass-tagged component never appears
    # in financial_subqueries; it lives in conversational_tasks or
    # clarification_tasks instead and never enters the RAG/tool-calling
    # loop at all.
    triaged_subqueries: List[Dict[str, Any]]     # Legacy alias for financial_subqueries during in-flight sessions
    financial_subqueries: List[Dict[str, Any]]   # n2d-resolved TOOL_ELIGIBLE components only; see the schema note above
    # NON_FINANCIAL-tagged components: {subquery_id, customer_component},
    # same shape as a financial subquery. No tool_attempts, never enters
    # the RAG loop -- joins final response generation/audit as its own
    # entry kind (see _build_conversational_context), the same way
    # decline_tasks/clarification_tasks already do for their own lanes.
    conversational_tasks: List[Dict[str, Any]]
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
    # One standard clarification contract regardless of which node raised
    # it -- see docs/DECLINE_CLARIFICATION_DESIGN_v1.0.md. Entries are
    # {subquery_id, originator, scope, unresolved_request,
    # clarification_reason, clarification_guesses}. subquery_id is the
    # sole link back to financial_subqueries -- mutual exclusivity (a
    # subquery resolves to exactly one of SEARCH/CLARIFY/DECLINE in
    # node_02e, and a SEARCH task terminates with at most one clarification
    # in node_03c) guarantees it is already unique across this list, so no
    # separate clarification id is needed. originator is "intent_formulator"
    # (node_02e, before any retrieval was attempted) or "sufficiency_auditor"
    # (node_03c, after reviewing retrieved evidence). Only subquery_id,
    # scope, unresolved_request, and clarification_guesses are ever shown
    # to an LLM (generation or node_06's audit, via
    # _clarification_prompt_view) -- originator and clarification_reason
    # stay internal, the latter being purely a forcing function on the
    # authoring node's own prompt, never a fact for anything downstream to
    # check. There is no stored clarification_evidence field: the
    # customer-facing question is composed fresh by generation from
    # unresolved_request/scope/clarification_guesses every time, with a
    # deterministic fallback template (_clarification_fallback_text)
    # computed on demand only if generation produces nothing usable.
    clarification_tasks: List[Dict[str, Any]]
    task_resolution_instructions: List[Dict[str, Any]]  # Terminal retrieval feedback for natural clarification generation
    safe_answer_for_fallback: str                 # Individually approved answer retained for deterministic clarification fallback
    selected_ux_journey: str                     # FLEXIBLE UX guidance (legacy TEXT/PROGRESS/LIST values may complete in-flight sessions)
    presentation_plan: Dict[str, Any]            # Internal UX guidance; never a required customer-visible task/block plan
    outbound_compliance_result: Dict[str, Any]    # Focused outbound MAS-compliance audit result
    outbound_groundedness_result: Dict[str, Any]  # Audit result plus structured, non-evidential grounding_violations for retries
    pending_advance: bool                           # V4.1 Fix: Tracks if the current step should advance upon safety clearance

    # --- OUTBOUND RESPONSE REVISION LOOP (per-task partial retry, node_06/node_04c) ---
    # Deterministic source of truth carried across retry rounds within one turn: the
    # exact response_block dicts (verbatim, never re-passed through an LLM) whose full
    # sourceTaskIds set had zero audit violations as of the most recent audit round.
    # Written only by _partition_audited_response (via node_06_outbound_audit_join).
    approved_response_blocks: List[Dict[str, Any]]
    # Task ids implicated by any conduct or grounding violation this round, expanded to
    # every co-block sibling id (a block's own sourceTaskIds couples its ids together
    # for revision -- see _partition_audited_response). Empty == fully approved; this is
    # the termination signal for a normal_response-scope turn. A non-financial/
    # evidence-only turn never sets this (stays [] and keeps using the whole-response
    # retry path).
    pending_task_ids: List[str]
    # True when the latest audit round produced at least one conduct/grounding violation
    # whose own task_id is "" (unattributable to a single block, e.g. overall tone).
    # When true, nothing is approved this round and node_04c is given every block.
    response_wide_violation_this_round: bool
    # This-round-only (overwritten, not accumulated) normalized violations keyed by
    # task_id ("" key holds response-wide ones), each tagged with its own audit axis.
    # This is the scoped feedback node_04c_response_reviser's prompt reads, instead of
    # draft_feedback_history's raw strings, for a normal_response-scope turn only.
    task_violation_history: Dict[str, List[Dict[str, Any]]]

    # --- ISOLATED LOOP TRACKERS ---
    safety_retry_count: int                      # Max 3
    turn_count: int
    search_feedback_history: List[str]
    draft_feedback_history: List[str]
