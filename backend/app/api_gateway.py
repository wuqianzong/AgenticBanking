from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from langchain_core.messages import HumanMessage, AIMessage  # FIXED: Added missing import

import redis
import json

# IMPORT THE NEW COMPILED TRI-TIER ENGINE
from backend.app.orchestration.graph import app_graph

app = FastAPI(title="Production Horizon Core Gateway")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Active Redis Management
try:
    r_client = redis.Redis(host='localhost', port=6379, db=0, decode_responses=True)
    r_client.ping()
    USE_REDIS = True
except redis.ConnectionError:
    USE_REDIS = False
    mem_cache = {}

class ChatPayload(BaseModel):
    session_id: str
    message: str
    user_id: str

class CloseSessionPayload(BaseModel):
    session_id: str
    user_id: str

@app.post("/api/session/checkout")
async def close_and_compact_session(payload: CloseSessionPayload):
    """
    Asynchronous Compaction Webhook Trigger.
    Saves and condenses active short-term chat logs right upon thread close event.
    """
    # FIXED: Updated absolute import path for safety
    from backend.app.orchestration.graph import app_graph
    
    # 1. Fetch current short-term state payload from LangGraph Checkpointer storage tracking table
    config = {"configurable": {"thread_id": payload.session_id}}
    state_snapshot = app_graph.get_state(config)
    
    if not state_snapshot.values or "chat_history" not in state_snapshot.values:
        return {"status": "ignored", "reason": "No active conversation matrix trace found."}
        
    active_history = state_snapshot.values["chat_history"]
    
    # 2. Run out-of-band high-density compaction routine
    from backend.app.services.memory_service import LongTermMemoryManager
    summary = LongTermMemoryManager.compile_and_archive_session(
        customer_id=payload.user_id,
        session_id=payload.session_id,
        messages=active_history
    )
    
    return {
        "status": "archived",
        "session_id": payload.session_id,
        "compiled_footprint_token": summary
    }

@app.post("/api/chat")
async def process_chat(payload: ChatPayload):
    """
    The Gateway now acts PURELY as a transport layer. 
    It catches the HTTP request, packages the input, hands it to LangGraph,
    and returns the JSON payload to the React frontend.
    """
    config = {"configurable": {"thread_id": payload.session_id}}
    
    # --- FRONTEND INITIALIZATION INTERCEPTOR ---
    if payload.message == "__INIT__":
        # Check if the session already has memory saved in the checkpointer
        state_snapshot = app_graph.get_state(config)
        history = state_snapshot.values.get("chat_history", []) if state_snapshot.values else []
        
        if history:
            # User refreshed the browser mid-conversation. 
            # Return a special payload containing the history so React can render the chat log.
            serialized_history = [{"role": "user" if isinstance(m, HumanMessage) else "ai", "content": m.content} for m in history]
            return {
                "text": "",
                "uiType": "history_sync", 
                "items": serialized_history
            }
        # If history is empty, let it fall through to invoke Node 00 (Greeting generation)
    
    # --- STANDARD INVOCATION ---
    initial_turn_state = {"user_input": payload.message}
    
    # FIXED: Changed from .invoke() to await .ainvoke() to support async RAG workers
    execution_result = await app_graph.ainvoke(initial_turn_state, config=config)
    
    final_payload = {
        "text": execution_result.get("final_response", ""),
        "uiType": execution_result.get("ui_action_payload", {}).get("uiType", "text"),
        "options": execution_result.get("ui_action_payload", {}).get("options", []),
        "items": execution_result.get("ui_action_payload", {}).get("items", [])
    }
    
    return final_payload