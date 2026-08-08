from typing import List, Dict, Any, TypedDict
from langchain_core.messages import BaseMessage

class AgentState(TypedDict):
    # --- PERSISTENT MEMORY (Cross-Turn) ---
    chat_history: List[BaseMessage]
    
    # V2.1: The User Journey (UI Sandbox)
    active_user_journey: str                     # e.g., "ACCOUNT_OPENING"
    
    # V2.1: The 2D Relational Matrix (Noun + Verbs)
    active_product_intents: List[Dict[str, Any]] # e.g., [{"product": "Credit_Card", "intents": ["Dispute_Transaction"]}]
    
    active_plan: Dict[str, Any]                  # e.g., {"steps": [], "current_step_index": 0}
    completed_steps: List[str]                   # Stateful Milestone Tracker for Anti-Amnesia
    process_slots: Dict[str, Any]                # Verified data (e.g., {"age": 25})
    
    pending_journey_switch: Dict[str, Any]       # e.g., {"target_journey": "ACCOUNT_OPENING", "target_product_intents": [...]} 
    pending_journey_queue: List[Dict[str, Any]]  # Stores: {"journey": str, "product_intents": list, "plan": dict, "completed_steps": list}
    
    # --- VOLATILE TURN STATE (Reset per turn) ---
    user_input: str
    preflight_status: str                        # Maps edge routing conditions
    knowledge_blueprint: Dict[str, Any] 
    target_path: str
    raw_retrieved_text: str
    working_context: str
    final_draft: str
    final_response: str
    ui_action_payload: Dict[str, Any]
    active_tool_routes: List[str]                # The list of parallel nodes to trigger
    rag_payload: str                             # Buffer for RAG agent
    api_payload: str                             # Buffer for API agent
    deeplink_payload: str                        # Buffer for Deeplink agent
    triage_status: str                           # 'ESCALATION', 'CLARIFICATION', 'OUT_OF_SCOPE', 'VALID_JOURNEY'
    extracted_journeys: List[str]                # Holds the Pass 1 Journey Array to pass to Pass 2
    
    # V2.5 Tri-Angle RRF Search Tasks
    search_tasks: List[Dict[str, Any]]           # Holds: [{"procedural": "...", "policy": "...", "conceptual": "...", "target_commercial_product": "...", "target_intent": ["..."]}]
    pending_advance: bool                           # V4.1 Fix: Tracks if the current step should advance upon safety clearance
    response_complexity: str                        # V4.1: Holds "simple" or "multi-step" from Node 03c

    # --- ISOLATED LOOP TRACKERS ---
    knowledge_retry_count: int                   # Max 3
    safety_retry_count: int                      # Max 3
    turn_count: int
    search_feedback_history: List[str]
    draft_feedback_history: List[str]