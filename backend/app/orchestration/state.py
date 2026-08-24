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
    verified_task_history: List[Dict[str, Any]]  # Latest verified turn records: existing search-task snapshots plus approved parent IDs
    
    # --- VOLATILE TURN STATE (Reset per turn) ---
    user_id: str
    user_profile: Dict[str, Any]
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
    triage_status: str                           # 'NON_COMPLIANT', 'ESCALATION', 'NON_FINANCIAL', or 'VALID_REQUEST'

    # Tool Calling v2.0: financial-only triage and generic task/result ledgers
    triaged_subqueries: List[Dict[str, Any]]     # Legacy alias for financial_subqueries during in-flight sessions
    financial_subqueries: List[Dict[str, Any]]   # n2d-resolved, compliant banking components only
    has_non_financial_content: bool              # Triage marker; never carries discarded wording downstream
    non_financial_context: List[str]             # Safe conversational asides; never a tool input or factual evidence source
    tool_tasks: List[Dict[str, Any]]             # Append-only tool-task ledger
    tool_results: List[Dict[str, Any]]           # Validated terminal tool-result ledger
    pending_tool_task_ids: List[str]             # Tasks eligible for the next generic dispatch
    tool_task_fingerprints: List[str]            # Terminal/pending duplicate-prevention keys
    tool_planning_round: int                     # Bounded outer orchestration-loop count
    tool_plan_status: str                        # continue | resolved | exhausted
    tool_gaps: List[Dict[str, Any]]              # Non-factual capability gaps returned by resolution planning
    decline_tasks: List[Dict[str, Any]]          # Legacy downstream scope-decline compatibility only

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
