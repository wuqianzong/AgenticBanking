from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

import redis
import json

# IMPORT THE NEW COMPILED TRI-TIER ENGINE
# (This assumes you saved the previous graph code in backend/app/orchestration/graph.py)
from backend.app.orchestration.graph import app_graph

app = FastAPI(title="Production Horizon Core Gateway")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Active Redis Management (Used for App-Level Rate Limiting or Long-Term Syncing if needed)
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

@app.post("/api/chat")
async def process_chat(payload: ChatPayload):
    """
    The Gateway now acts PURELY as a transport layer. 
    It catches the HTTP request, packages the input, hands it to LangGraph,
    and returns the JSON payload to the React frontend.
    """
    
    # 1. Configure the LangGraph Checkpointer Thread
    # LangGraph will automatically use this thread_id to persist your chat_history
    # so we no longer need to manually append messages to a Redis array here.
    config = {"configurable": {"thread_id": payload.session_id}}
    
    # 2. Package the initial state for Node_0_Turn_Init
    initial_turn_state = {
        "user_input": payload.message
        # chat_history is automatically hydrated by LangGraph's checkpointer
    }
    
    # 3. Fire the Orchestration Engine
    execution_result = app_graph.invoke(initial_turn_state, config=config)
    
    # 4. Extract the exact parameters your React components need
    # This matches your UI's Payload Exchange Protocol
    final_payload = {
        "text": execution_result.get("final_response", ""),
        "uiType": execution_result["ui_action_payload"].get("uiType", "text"),
        "options": execution_result["ui_action_payload"].get("options", []),
        "items": execution_result["ui_action_payload"].get("items", [])
    }
    
    return final_payload

class CloseSessionPayload(BaseModel):
    session_id: str
    user_id: str

@app.post("/api/session/checkout")
async def close_and_compact_session(payload: CloseSessionPayload):
    """
    Asynchronous Compaction Webhook Trigger.
    Saves and condenses active short-term chat logs right upon thread close event
    to guarantee sub-second initial load response times next time the user loads the application.
    """
    from app.orchestration.graph import app_graph
    
    # 1. Fetch current short-term state payload from LangGraph Checkpointer storage tracking table
    config = {"configurable": {"thread_id": payload.session_id}}
    state_snapshot = app_graph.get_state(config)
    
    if not state_snapshot.values or "chat_history" not in state_snapshot.values:
        return {"status": "ignored", "reason": "No active conversation matrix trace found."}
        
    active_history = state_snapshot.values["chat_history"]
    
    # 2. Run out-of-band high-density compaction routine
    from app.services.memory_service import LongTermMemoryManager
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