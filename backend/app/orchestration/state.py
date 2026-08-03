from typing import List, Dict, Any, TypedDict
from langchain_core.messages import BaseMessage

class AgentState(TypedDict):
    # --- PERSISTENT MEMORY (Cross-Turn) ---
    chat_history: List[BaseMessage]
    #active_intent: str
    active_domain: str               # e.g., "ACCOUNT_OPENING"
    active_intent_families: List[str]# e.g., ["Eligibility","documents"]
    active_plan: Dict[str, Any]      # e.g., {"steps": [], "current_step_index": 0}
    completed_steps: List[str]       # V3.73: Stateful Milestone Tracker for Anti-Amnesia
    process_slots: Dict[str, Any]    # Verified data (e.g., {"age": 25})
    pending_domain_switch: Dict[str, Any] # e.g., {"target_domain": "ACCOUNT_OPENING"}
    pending_domain_queue: List[Dict[str, Any]] # Stores: {"domain": str, "families": list, "plan": dict, "completed_steps": list}
    
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
    active_tool_routes: List[str]    # The list of parallel nodes to trigger
    rag_payload: str                 # Buffer for RAG agent
    api_payload: str                 # Buffer for API agent
    deeplink_payload: str            # Buffer for Deeplink agent
    triage_status: str                         # 'ESCALATION', 'CLARIFICATION', 'OUT_OF_SCOPE', 'VALID_DOMAIN'
    extracted_domains: List[str]               # Holds the Pass 1 Domain Array to pass to Pass 2
    search_queries: List[Dict[str, Any]]       # Holds: [{"rephrased_query": "...", "intent_families": [...]}]

    # --- ISOLATED LOOP TRACKERS ---
    knowledge_retry_count: int       # Max 3
    safety_retry_count: int          # Max 3
    turn_count: int
    search_feedback_history: List[str]
    draft_feedback_history: List[str]