from typing import List, Dict, Any, TypedDict
from langchain_core.messages import BaseMessage

class AgentState(TypedDict):
    # --- PERSISTENT MEMORY (Cross-Turn) ---
    chat_history: List[BaseMessage]
    active_intent: str
    active_plan: Dict[str, Any]      # e.g., {"steps": [], "current_step_index": 0}
    completed_steps: List[str]       # V3.73: Stateful Milestone Tracker for Anti-Amnesia
    process_slots: Dict[str, Any]    # Verified data (e.g., {"age": 25})
    pending_intent_switch: Dict[str, Any] # e.g., {"target_intent": "ACCOUNT_OPENING"}
    
    # --- VOLATILE TURN STATE (Reset per turn) ---
    user_input: str
    preflight_status: str            # Maps edge routing conditions
    knowledge_blueprint: Dict[str, Any] 
    target_path: str
    raw_retrieved_text: str
    working_context: str
    final_draft: str
    final_response: str
    ui_action_payload: Dict[str, Any]
    
    # --- ISOLATED LOOP TRACKERS ---
    knowledge_retry_count: int       # Max 3
    safety_retry_count: int          # Max 3
    turn_count: int
    search_feedback_history: List[str]
    draft_feedback_history: List[str]