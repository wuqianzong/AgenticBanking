from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from langchain_core.messages import HumanMessage, AIMessage  # FIXED: Added missing import

import redis
import json
import asyncio

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
    Upgraded to Streaming NDJSON Response.
    Yields node execution progress in real-time before returning the final payload.
    """
    config = {"configurable": {"thread_id": payload.session_id}}

    # --- FRONTEND INITIALIZATION ---
    # Restore an existing thread, or run Turn 0 to generate and persist a greeting.
    is_initialization = payload.message == "**INIT**"
    if is_initialization:
        state_snapshot = app_graph.get_state(config)
        history = state_snapshot.values.get("chat_history", []) if state_snapshot.values else []

        if history:
            async def history_stream():
                serialized_history = [
                    {
                        "role": "user" if isinstance(m, HumanMessage) else "ai",
                        "content": m.content,
                    }
                    for m in history
                ]
                yield json.dumps({
                    "type": "init",
                    "uiType": "history_sync",
                    "items": serialized_history,
                }) + "\n"

            return StreamingResponse(history_stream(), media_type="application/x-ndjson")

    # --- STANDARD INVOCATION (STREAMING) ---
    async def chat_stream():
        initial_turn_state = {"user_input": "__INIT__" if is_initialization else payload.message}

        try:
            # 1. Stream the Node Execution Progress
            async for chunk in app_graph.astream(initial_turn_state, config=config):
                for node_name, state_update in chunk.items():
                    # Yield the name of the node currently executing to the frontend
                    yield json.dumps({
                        "type": "progress",
                        "node": node_name,
                    }) + "\n"
                    # Pace progress events so React can render each transition.
                    await asyncio.sleep(0.3)

            # 2. Graph has finished. Fetch the final condensed state from the checkpointer.
            state_snapshot = app_graph.get_state(config)
            execution_result = state_snapshot.values

            final_payload = {
                "type": "complete",
                "text": execution_result.get("final_response", ""),
                "uiType": execution_result.get("ui_action_payload", {}).get("uiType", "text"),
                "options": execution_result.get("ui_action_payload", {}).get("options", []),
                "items": execution_result.get("ui_action_payload", {}).get("items", []),
            }
            yield json.dumps(final_payload) + "\n"

        except Exception as e:
            # Safe fallback if the graph crashes during execution
            yield json.dumps({
                "type": "complete",
                "text": f"System encountered an error: {str(e)}",
                "uiType": "text",
                "options": [],
            }) + "\n"

    return StreamingResponse(
        chat_stream(),
        media_type="application/x-ndjson",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
