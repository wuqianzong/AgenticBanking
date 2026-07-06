from typing import List, Dict, Any, TypedDict
from langchain_core.messages import BaseMessage

class AgentState(TypedDict):
    # --- PERSISTENT TIER ---
    # Maintained across turns via standard checkpointer memory overwrite tracking
    chat_history: List[BaseMessage]
    
    # --- VOLATILE RESET TIER (Cleared by Node_0_Turn_Init) ---
    user_input: str
    active_intent: str
    retrieval_strategy: str
    target_path: str
    optimized_query: str
    raw_retrieved_text: str
    working_context: str
    final_draft: str
    final_response: str
    
    # --- ISOLATED LOOP TRACKERS & FEEDBACK ---
    knowledge_retry_count: int
    safety_retry_count: int
    search_feedback_history: List[str]
    draft_feedback_history: List[str]
    
    # --- CLIENT PAYLOAD ---
    ui_action_payload: Dict[str, Any]
    
    # --- INTERNAL ROUTING FLAGS ---
    semantic_evaluation_status: str  # "pass", "mas_violation", "groundedness_fail", "generation_fail", "fatigue"
    substitution_status: str         # "valid", "exception"