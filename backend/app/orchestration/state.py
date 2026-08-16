from typing import List, Dict, Any, TypedDict
from langchain_core.messages import BaseMessage

class AgentState(TypedDict):
    # --- PERSISTENT MEMORY (Cross-Turn) ---
    chat_history: List[BaseMessage]
    history_sequence_offset: int                 # Number of trimmed messages before chat_history
    compacted_history_summary: str               # Rolling summary of messages trimmed from graph memory
    last_session_summary: str                    # Advisory recall from the most recently completed session
    user_baseline_summary: str                   # Advisory aggregate of up to five prior sessions
    
    # V2.1: The 2D Relational Matrix (Noun + Verbs)
    active_product_intents: List[Dict[str, Any]] # e.g., [{"product": "Credit_Card", "intents": ["Dispute_Transaction"]}]
    
    active_plan: Dict[str, Any]                  # e.g., {"steps": [], "current_step_index": 0}
    completed_steps: List[str]                   # Stateful Milestone Tracker for Anti-Amnesia
    process_slots: Dict[str, Any]                # Verified data (e.g., {"age": 25})
    approved_evidence_cache: Dict[str, Dict[str, Any]]  # Session-scoped parent-ID cache keyed by product + intent set
    
    # --- VOLATILE TURN STATE (Reset per turn) ---
    user_id: str
    user_input: str
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
    triage_status: str                           # 'ESCALATION', 'CLARIFICATION', or 'VALID_REQUEST'
    
    # V2.5 Tri-Angle RRF Search Tasks
    search_tasks: List[Dict[str, Any]]           # Immutable task definitions with stable task_id values
    task_results: List[Dict[str, Any]]           # Parent-ID evidence selections and sufficiency result per task
    task_retry_counts: Dict[str, int]            # Retry count keyed by task_id
    task_feedback: Dict[str, str]                # Task-specific RAG refinement feedback
    intent_clarification_tasks: List[Dict[str, Any]]  # Unresolved user-derived components that cannot form a valid retrieval task
    task_resolution_instructions: List[Dict[str, Any]]  # Terminal retrieval feedback for natural clarification generation
    safe_answer_for_fallback: str                 # Individually approved answer retained for deterministic clarification fallback
    clarification_fallback_required: bool         # True only when clarification retries exhausted while answer remains safe
    selected_ux_journey: str                     # TEXT, PROGRESS, LIST, or ESCALATION presentation type
    presentation_plan: Dict[str, Any]            # Evidence-selected UI and response plan
    pending_evidence_cache_updates: Dict[str, Dict[str, Any]]  # Promote only after outbound semantic approval
    outbound_compliance_result: Dict[str, Any]    # Focused outbound MAS-compliance audit result
    outbound_groundedness_result: Dict[str, Any]  # Focused outbound evidence-groundedness audit result
    pending_advance: bool                           # V4.1 Fix: Tracks if the current step should advance upon safety clearance

    # --- ISOLATED LOOP TRACKERS ---
    safety_retry_count: int                      # Max 3
    turn_count: int
    search_feedback_history: List[str]
    draft_feedback_history: List[str]
