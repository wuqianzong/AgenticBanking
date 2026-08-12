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
from backend.app.services.memory_service import LongTermMemoryManager, ShortTermMemoryManager
from backend.app.core.system_logger import log_event, reset_request_context, set_request_context

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
    reason: str = "logout"


async def archive_session_history(payload: CloseSessionPayload, archive_reason: str, compact: bool = False):
    """Write the complete in-memory transcript to SQLite before a session boundary."""
    config = {"configurable": {"thread_id": payload.session_id}}
    state_snapshot = app_graph.get_state(config)
    active_history = state_snapshot.values.get("chat_history", []) if state_snapshot.values else []
    history_sequence_offset = state_snapshot.values.get("history_sequence_offset", 0) if state_snapshot.values else 0

    if not active_history:
        return {"status": "ignored", "reason": "No active conversation history found."}

    if compact:
        summary = await asyncio.to_thread(
            LongTermMemoryManager.compile_and_archive_session,
            payload.user_id,
            payload.session_id,
            active_history,
            history_sequence_offset,
        )
        return {"status": "archived", "message_count": len(active_history), "summary": summary}

    inserted_count = await asyncio.to_thread(
        LongTermMemoryManager.persist_chat_history,
        payload.user_id,
        payload.session_id,
        active_history,
        archive_reason,
        history_sequence_offset,
    )
    return {"status": "archived", "message_count": len(active_history), "new_messages": inserted_count}


async def compact_working_history_in_background(config: dict) -> None:
    """Keep only 10 turns in graph memory after replying; never block that reply."""
    try:
        state_snapshot = app_graph.get_state(config)
        state = state_snapshot.values
        history = state.get("chat_history", [])
        trimmed_history = ShortTermMemoryManager.trim_history(history)
        discarded_count = len(history) - len(trimmed_history)
        if discarded_count <= 0:
            return

        prior_summary = state.get("compacted_history_summary", "")
        evicted_messages = history[:discarded_count]
        updated_summary = await asyncio.to_thread(
            LongTermMemoryManager.summarize_trimmed_history,
            prior_summary,
            evicted_messages,
        )
        app_graph.update_state(config, {
            "chat_history": trimmed_history,
            "history_sequence_offset": state.get("history_sequence_offset", 0) + discarded_count,
            "compacted_history_summary": updated_summary,
        })
        print(
            f"🧾 [MEMORY SERVICE] Compacted {discarded_count} earlier messages; "
            f"retained the latest 10 turns in graph memory."
        )
    except Exception as error:
        # The durable transcript has already been persisted; a failed summary
        # must never affect an already-completed customer response.
        print(f"⚠️ [MEMORY SERVICE] Background history compaction skipped: {error}")

@app.post("/api/session/checkout")
async def close_and_compact_session(payload: CloseSessionPayload):
    """
    Asynchronous Compaction Webhook Trigger.
    Saves and condenses active short-term chat logs right upon thread close event.
    """
    token = set_request_context(session_id=payload.session_id, user_id=payload.user_id)
    try:
        log_event("session.checkout", reason=payload.reason)
        result = await archive_session_history(payload, payload.reason, compact=True)
        result["session_id"] = payload.session_id
        log_event("session.checkout.completed", status=result["status"])
        return result
    except Exception as error:
        log_event("session.checkout.failed", level="ERROR", error=str(error))
        raise
    finally:
        reset_request_context(token)


@app.get("/api/history/{user_id}")
async def get_chat_history(
    user_id: str,
    session_id: str,
    limit: int = 20,
    before_sequence: int | None = None,
):
    """Return one ordered, user-filtered transcript page (20 messages = 10 turns)."""
    if not 1 <= limit <= 100:
        return {"user_id": user_id, "messages": [], "has_more": False, "error": "limit must be 1-100"}
    page = await asyncio.to_thread(
        LongTermMemoryManager.fetch_chat_history_page,
        user_id,
        session_id,
        limit,
        before_sequence,
    )
    return {"user_id": user_id, "session_id": session_id, **page}


@app.get("/api/history/{user_id}/previous")
async def get_prior_session_history(
    user_id: str,
    current_session_id: str,
    limit: int = 20,
    before_row_id: int | None = None,
):
    """Page through earlier sessions owned by this user, excluding the active one."""
    if not 1 <= limit <= 100:
        return {"user_id": user_id, "messages": [], "has_more": False, "error": "limit must be 1-100"}
    page = await asyncio.to_thread(
        LongTermMemoryManager.fetch_prior_session_history_page,
        user_id,
        current_session_id,
        limit,
        before_row_id,
    )
    return {"user_id": user_id, "session_id": current_session_id, **page}

@app.post("/api/chat")
async def process_chat(payload: ChatPayload):
    """
    Upgraded to Streaming NDJSON Response.
    Yields node execution progress in real-time before returning the final payload.
    """
    config = {"configurable": {"thread_id": payload.session_id}}
    log_event("chat.request", session_id=payload.session_id, user_id=payload.user_id, message=payload.message)

    # --- FRONTEND INITIALIZATION ---
    # Restore an existing thread, or run Turn 0 to generate and persist a greeting.
    is_initialization = payload.message == "**INIT**"
    if is_initialization:
        state_snapshot = app_graph.get_state(config)
        history = state_snapshot.values.get("chat_history", []) if state_snapshot.values else []

        if history:
            async def history_stream():
                context_token = set_request_context(session_id=payload.session_id, user_id=payload.user_id)
                try:
                    serialized_history = [
                        {
                            "role": "user" if isinstance(m, HumanMessage) else "ai",
                            "content": m.content,
                        }
                        for m in history
                    ]
                    log_event("chat.history_sync", message_count=len(serialized_history))
                    yield json.dumps({
                        "type": "init",
                        "uiType": "history_sync",
                        "items": serialized_history,
                    }) + "\n"
                finally:
                    reset_request_context(context_token)

            return StreamingResponse(history_stream(), media_type="application/x-ndjson")

    # --- STANDARD INVOCATION (STREAMING) ---
    async def chat_stream():
        context_token = set_request_context(session_id=payload.session_id, user_id=payload.user_id)
        previous_snapshot = app_graph.get_state(config)
        previous_turn_count = previous_snapshot.values.get("turn_count", 0) if previous_snapshot.values else 0
        initial_turn_state = {
            "user_id": payload.user_id,
            "user_input": "__INIT__" if is_initialization else payload.message,
            "turn_count": previous_turn_count if is_initialization else previous_turn_count + 1,
        }

        try:
            # 1. Stream the Node Execution Progress
            async for chunk in app_graph.astream(initial_turn_state, config=config):
                for node_name, state_update in chunk.items():
                    log_event("graph.node.completed", node=node_name, state_keys=list(state_update.keys()))
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

            # Persist every completed turn so a browser refresh can restore the
            # transcript. Inserts are idempotent by session-wide sequence number.
            archive_reason = "turn_0" if is_initialization else "turn_complete"
            await archive_session_history(
                CloseSessionPayload(
                    session_id=payload.session_id,
                    user_id=payload.user_id,
                    reason=archive_reason,
                ),
                archive_reason=archive_reason,
            )
            active_history = execution_result.get("chat_history", [])
            if active_history:
                history_sequence_offset = execution_result.get("history_sequence_offset", 0)
                await asyncio.to_thread(
                    LongTermMemoryManager.persist_message_ui_payload,
                    payload.user_id,
                    payload.session_id,
                    history_sequence_offset + len(active_history) - 1,
                    execution_result.get("ui_action_payload", {}),
                )

            final_payload = {
                "type": "complete",
                "text": execution_result.get("final_response", ""),
                "uiType": execution_result.get("ui_action_payload", {}).get("uiType", "text"),
                "options": execution_result.get("ui_action_payload", {}).get("options", []),
                "items": execution_result.get("ui_action_payload", {}).get("items", []),
                "stepDetail": execution_result.get("ui_action_payload", {}).get("stepDetail", ""),
                "precisionSubstituted": execution_result.get("ui_action_payload", {}).get("precisionSubstituted", False),
                "currentStep": execution_result.get("ui_action_payload", {}).get("currentStep"),
                "totalSteps": execution_result.get("ui_action_payload", {}).get("totalSteps"),
            }
            yield json.dumps(final_payload) + "\n"
            log_event("chat.response", ui_type=final_payload["uiType"], response_length=len(final_payload["text"]))

            # Schedule after the payload is yielded. The customer sees the
            # response immediately while older context is compacted in back.
            if not is_initialization:
                asyncio.create_task(compact_working_history_in_background(config))

        except Exception as e:
            # Safe fallback if the graph crashes during execution
            yield json.dumps({
                "type": "complete",
                "text": f"System encountered an error: {str(e)}",
                "uiType": "text",
                "options": [],
            }) + "\n"
            log_event("chat.error", level="ERROR", error=str(e))
        finally:
            reset_request_context(context_token)

    return StreamingResponse(
        chat_stream(),
        media_type="application/x-ndjson",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
