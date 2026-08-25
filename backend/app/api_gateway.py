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
from backend.app.services import service_request_service
from backend.app.services.service_request_mock_api import run_downstream_service, run_mock_credential_check
from backend.app.orchestration.service_request_registry import get_service_request_contract

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

class UserPreferencesPayload(BaseModel):
    user_id: str
    nickname: str = ""
    language: str = "English"
    tone: str = "professional"
    emoji: str = "enabled"
    financial_literacy: str = "intermediate"

class ServiceRequestSubmitPayload(BaseModel):
    special_id: str
    user_id: str
    session_id: str
    submitted_data: dict = {}

class ServiceRequestCredentialCheckPayload(BaseModel):
    special_id: str
    user_id: str
    session_id: str
    passed: bool


async def archive_session_history(payload: CloseSessionPayload, archive_reason: str):
    """Write the complete in-memory transcript to SQLite before a session boundary.

    Only the raw transcript is archived. No cross-session summary or
    baseline is generated here -- the project does not require carrying
    conversation content across logins, so a session boundary is a pure
    durability write, not an LLM call.
    """
    config = {"configurable": {"thread_id": payload.session_id}}
    state_snapshot = app_graph.get_state(config)
    active_history = state_snapshot.values.get("chat_history", []) if state_snapshot.values else []
    history_sequence_offset = state_snapshot.values.get("history_sequence_offset", 0) if state_snapshot.values else 0

    if not active_history:
        # MemorySaver is intentionally ephemeral.  A server restart must not
        # prevent logout/timeout from archiving the durable per-turn
        # transcript that was already written to SQLite.
        persisted_messages = await asyncio.to_thread(
            LongTermMemoryManager.fetch_chat_history,
            payload.user_id,
            payload.session_id,
        )
        active_history = [
            HumanMessage(content=message["content"])
            if message["role"] == "user"
            else AIMessage(content=message["content"])
            for message in persisted_messages
        ]
        history_sequence_offset = 0

    if not active_history:
        return {"status": "ignored", "reason": "No conversation history found for this session."}

    inserted_count = await asyncio.to_thread(
        LongTermMemoryManager.persist_chat_history,
        payload.user_id,
        payload.session_id,
        active_history,
        archive_reason,
        history_sequence_offset,
    )
    return {"status": "archived", "message_count": len(active_history), "new_messages": inserted_count}


async def compact_working_history(config: dict) -> dict:
    """Keep only 10 turns in graph memory before a scheduled new turn."""
    try:
        state_snapshot = app_graph.get_state(config)
        state = state_snapshot.values
        history = state.get("chat_history", [])
        trimmed_history = ShortTermMemoryManager.trim_history(history)
        discarded_count = len(history) - len(trimmed_history)
        if discarded_count <= 0:
            return {"compacted": False, "discarded_messages": 0}

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
        return {"compacted": True, "discarded_messages": discarded_count}
    except Exception as error:
        # The durable transcript has already been persisted; a failed summary
        # must never affect an already-completed customer response.
        print(f"⚠️ [MEMORY SERVICE] Background history compaction skipped: {error}")
        return {"compacted": False, "discarded_messages": 0, "error": str(error)}

@app.get("/api/preferences/{user_id}")
async def get_preferences(user_id: str):
    try:
        profile = await asyncio.to_thread(LongTermMemoryManager.get_user_preferences, user_id)
        return profile or {}
    except Exception as error:
        return {"status": "error", "message": str(error)}

@app.put("/api/preferences")
async def update_preferences(payload: UserPreferencesPayload):
    try:
        await asyncio.to_thread(
            LongTermMemoryManager.update_user_preferences,
            payload.dict()
        )
        return {"status": "success", "updated": True}
    except Exception as error:
        return {"status": "error", "message": str(error)}

async def _run_service_request_turn(session_id: str, user_id: str, service_request_event: dict) -> dict:
    """Invoke the WP-7 special route (node_svc_service_request_resolver) as
    its own turn on the customer's existing graph thread, then persist and
    return the same shape /api/chat's final payload uses so the frontend can
    render it identically to an ordinary assistant message.
    """
    config = {"configurable": {"thread_id": session_id}}
    token = set_request_context(session_id=session_id, user_id=user_id)
    try:
        previous_snapshot = app_graph.get_state(config)
        previous_turn_count = previous_snapshot.values.get("turn_count", 0) if previous_snapshot.values else 0
        await app_graph.ainvoke(
            {
                "user_id": user_id,
                "session_id": session_id,
                "service_request_event": service_request_event,
                "turn_count": previous_turn_count + 1,
            },
            config=config,
        )
        state_snapshot = app_graph.get_state(config)
        execution_result = state_snapshot.values

        await archive_session_history(
            CloseSessionPayload(session_id=session_id, user_id=user_id, reason="turn_complete"),
            archive_reason="turn_complete",
        )
        active_history = execution_result.get("chat_history", [])
        if active_history:
            history_sequence_offset = execution_result.get("history_sequence_offset", 0)
            await asyncio.to_thread(
                LongTermMemoryManager.persist_message_ui_payload,
                user_id, session_id,
                history_sequence_offset + len(active_history) - 1,
                execution_result.get("ui_action_payload", {}),
            )
        return {
            "type": "complete",
            "text": execution_result.get("final_response", ""),
            "uiType": execution_result.get("ui_action_payload", {}).get("uiType", "none"),
            "options": execution_result.get("ui_action_payload", {}).get("options", []),
        }
    finally:
        reset_request_context(token)


@app.get("/api/service-request/list/{user_id}")
async def list_service_requests(user_id: str):
    """The customer's full service-request to-do list -- pending, completed,
    and expired -- scoped by user_id so it stays visible across sessions."""
    records = await asyncio.to_thread(service_request_service.list_registrations, user_id)
    return {
        "items": [
            {
                "special_id": record["special_id"],
                "service_request_id": record["service_request_id"],
                "action_label": (get_service_request_contract(record["service_request_id"]) or {}).get("action_label", ""),
                "description": (get_service_request_contract(record["service_request_id"]) or {}).get("description", ""),
                "status": record["status"],
                "created_at": record["created_at"],
                "expires_at": record["expires_at"],
            }
            for record in records
        ]
    }


@app.get("/api/service-request/{special_id}")
async def get_service_request(special_id: str):
    """Deterministic lookup used both to open a form the first time and to
    reopen a still-valid one later from old chat history."""
    record = await asyncio.to_thread(service_request_service.get, special_id)
    if record is None:
        return {"status": "error", "message": "Unknown service request."}
    response = {"status": record["status"], "special_id": special_id, "service_request_id": record["service_request_id"]}
    if record["status"] == "pending":
        # Covers both a never-submitted form and one reset by a failed
        # credential check -- either way, "pending" means the form is open
        # for (re)submission until this registration expires.
        contract = get_service_request_contract(record["service_request_id"])
        response["form_schema"] = contract["form_schema"] if contract else {}
        response["description"] = contract["description"] if contract else ""
    return response


@app.post("/api/service-request/submit")
async def submit_service_request(payload: ServiceRequestSubmitPayload):
    record = await asyncio.to_thread(service_request_service.get, payload.special_id)
    if record is None or record["user_id"] != payload.user_id or record["session_id"] != payload.session_id:
        return {"status": "error", "message": "This request could not be found or has expired."}
    if record["status"] == "expired":
        return {"status": "error", "message": "This request has expired. Please ask again."}
    if record["status"] != "pending":
        return {"status": "error", "message": "This request has already been completed."}

    record = await asyncio.to_thread(service_request_service.mark_submitted, payload.special_id, payload.submitted_data)
    contract = get_service_request_contract(record["service_request_id"])
    log_event("service_request.submit.received", special_id=payload.special_id, service_request_id=record["service_request_id"])

    if record["requires_step_up"]:
        return {"status": "credential_check_required", "special_id": payload.special_id}

    result_payload = run_downstream_service(record["service_request_id"], record["submitted_data"])
    await asyncio.to_thread(service_request_service.complete_with_result, payload.special_id, result_payload)
    return await _run_service_request_turn(
        payload.session_id, payload.user_id,
        {
            "special_id": payload.special_id, "outcome": "completed",
            "service_request_id": record["service_request_id"], "result_payload": result_payload,
            "description": contract["description"] if contract else "",
        },
    )


@app.post("/api/service-request/credential-check")
async def credential_check_service_request(payload: ServiceRequestCredentialCheckPayload):
    record = await asyncio.to_thread(service_request_service.get, payload.special_id)
    if record is None or record["user_id"] != payload.user_id or record["session_id"] != payload.session_id:
        return {"status": "error", "message": "This request could not be found or has expired."}
    if record["status"] != "pending" or not record.get("submitted_data"):
        return {"status": "error", "message": "This request is not awaiting a credential check."}

    contract = get_service_request_contract(record["service_request_id"])
    passed = run_mock_credential_check(payload.passed)
    record = await asyncio.to_thread(service_request_service.resolve_credential_check, payload.special_id, passed)

    if not passed:
        return await _run_service_request_turn(
            payload.session_id, payload.user_id,
            {
                "special_id": payload.special_id, "outcome": "denied",
                "service_request_id": record["service_request_id"],
                "description": contract["description"] if contract else "",
            },
        )

    result_payload = run_downstream_service(record["service_request_id"], record["submitted_data"])
    await asyncio.to_thread(service_request_service.complete_with_result, payload.special_id, result_payload)
    return await _run_service_request_turn(
        payload.session_id, payload.user_id,
        {
            "special_id": payload.special_id, "outcome": "completed",
            "service_request_id": record["service_request_id"], "result_payload": result_payload,
            "description": contract["description"] if contract else "",
        },
    )


@app.post("/api/session/checkout")
async def close_and_compact_session(payload: CloseSessionPayload):
    """
    Asynchronous Compaction Webhook Trigger.
    Saves and condenses active short-term chat logs right upon thread close event.
    """
    token = set_request_context(session_id=payload.session_id, user_id=payload.user_id)
    try:
        log_event("session.checkout", reason=payload.reason)
        result = await archive_session_history(payload, payload.reason)
        result["session_id"] = payload.session_id
        # Checkout is the lifecycle boundary for ephemeral LangGraph state.
        # Durable transcript and memory summaries have already been archived
        # above, so deleting this thread safely releases the session-scoped
        # evidence cache and avoids unbounded in-memory checkpoint growth.
        await app_graph.checkpointer.adelete_thread(payload.session_id)
        result["graph_state_cleared"] = True
        log_event("session.checkout.graph_state_cleared", session_id=payload.session_id)
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
            "session_id": payload.session_id,
            "user_input": "__INIT__" if is_initialization else payload.message,
            "turn_count": previous_turn_count if is_initialization else previous_turn_count + 1,
        }

        try:
            # Compact before turns 11, 21, 31, ... rather than after a reply.
            # The submitted message has already disabled the input in React,
            # so no graph turn can race this state update. Stream it as a
            # regular progress stage so the customer can see the work.
            is_scheduled_compaction_turn = (
                not is_initialization
                and previous_turn_count > 0
                and (previous_turn_count + 1) % 10 == 1
            )
            if is_scheduled_compaction_turn:
                yield json.dumps({
                    "type": "progress",
                    "node": "memory_compaction",
                }) + "\n"
                await asyncio.sleep(0.3)
                yield json.dumps({
                    "type": "progress",
                    "node": "memory_validation",
                }) + "\n"
                compaction_result = await compact_working_history(config)
                log_event("memory.compaction.completed", **compaction_result)

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
                "progressTitle": execution_result.get("ui_action_payload", {}).get("progressTitle", ""),
                "stepDetail": execution_result.get("ui_action_payload", {}).get("stepDetail", ""),
                "stepDetails": execution_result.get("ui_action_payload", {}).get("stepDetails", []),
                "clarificationText": execution_result.get("ui_action_payload", {}).get("clarificationText", ""),
                "listTitle": execution_result.get("ui_action_payload", {}).get("listTitle", ""),
                "listIntro": execution_result.get("ui_action_payload", {}).get("listIntro", ""),
                "extendedText": execution_result.get("ui_action_payload", {}).get("extendedText", ""),
                "responseBlocks": execution_result.get("ui_action_payload", {}).get("responseBlocks", []),
                "listClarificationText": execution_result.get("ui_action_payload", {}).get("listClarificationText", ""),
                "precisionSubstituted": execution_result.get("ui_action_payload", {}).get("precisionSubstituted", False),
                "currentStep": execution_result.get("ui_action_payload", {}).get("currentStep"),
                "totalSteps": execution_result.get("ui_action_payload", {}).get("totalSteps"),
                "serviceRequestActions": execution_result.get("ui_action_payload", {}).get("serviceRequestActions", []),
                "navigationActions": execution_result.get("ui_action_payload", {}).get("navigationActions", []),
                "personalDataReveals": execution_result.get("ui_action_payload", {}).get("personalDataReveals", []),
                "personalDataSubstituted": execution_result.get("ui_action_payload", {}).get("personalDataSubstituted", False),
            }
            yield json.dumps(final_payload) + "\n"
            log_event("chat.response", ui_type=final_payload["uiType"], response_length=len(final_payload["text"]))

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
