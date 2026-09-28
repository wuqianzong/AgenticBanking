"""Live, whole-system validation harness -- not scoped to one node.

Unlike the narrower 2026-09-04_relevant_irrelevant_validation study (which
targeted node_03c specifically), this run exercises the entire pipeline
broadly: Triage decomposition, Intent Classification (search/clarify/
decline), RAG retrieval + Sufficiency, service_request/deeplink dispatch,
Response Generation, the outbound Compliance & Groundedness audit + Per-Task
Reviser loop, Precision Substitution, and the personal-data/token mechanism
-- using a fresh, disjoint set of real BANKING77 queries plus two hand-built
controls (a personal-data lookup and a deliberately multi-part message) that
BANKING77 itself doesn't cover.

Standalone: imports the real, unmodified backend graph and invokes it
directly (the same call shape api_gateway.py's /api/chat uses). Never calls
archive_session_history, so no test conversation is written to the
persistent chat/ledger tables. Same rate-limit discipline as the prior
study: inspects google.genai.errors.APIError for a per-minute RetryInfo
(wait and retry) vs. a daily QuotaFailure (stop immediately), plus a fixed
pacing gap between every turn regardless of errors.

Run (from repo root, with the real backend server already running so
GOOGLE_API_KEY/GROQ_API_KEY are available in its environment):
    eval "$(ps eww <server_pid> | tr ' ' '\\n' | grep -E '^(GOOGLE|GROQ)_API_KEY=' | sed 's/^/export /')"
    python3 research/2026-09-04_full_system_validation/scripts/run_validation.py
"""
from __future__ import annotations

import asyncio
import json
import re
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT))

from backend.app.orchestration.graph import app_graph
from backend.app.core.system_logger import set_request_context, reset_request_context

TEST_USER_ID = "USR_999"
BASE_PACING_SECONDS = 25
MAX_RETRIES_PER_QUERY = 5
DEFAULT_RATE_LIMIT_WAIT = 65

OUT_DIR = Path(__file__).resolve().parent.parent / "results"
OUT_DIR.mkdir(parents=True, exist_ok=True)
RESULTS_PATH = OUT_DIR / "test_results.jsonl"
PROGRESS_PATH = OUT_DIR / "progress.txt"

# 25 real BANKING77 queries, drawn from 25 categories NOT used in the prior
# (2026-09-04_relevant_irrelevant_validation) study, plus 2 hand-built
# controls BANKING77 has no equivalent for.
TEST_QUERIES = [
    ("What do I need to do to activate my new card?", "activate_my_card"),
    ("Where is the closest Mastercard ATM?", "atm_support"),
    ("How long will it take for my transferred money to show up?", "balance_not_updated_after_bank_transfer"),
    ("Where do I link the new card?", "card_linking"),
    ("Why am I being charged when I withdraw cash?", "cash_withdrawal_charge"),
    ("Is the bank the only place I can change my PIN?", "change_pin"),
    ("what is required documents for new card process?", "contactless_not_working"),
    ("Why was my card payment declined?", "declined_card_payment"),
    ("I need to use my disposable card multiple times a day, is there a cutoff limit?", "disposable_card_limits"),
    ("Can I change from one currency to another?", "exchange_via_app"),
    ("I'm not okay with this fee on my statement.", "extra_charge_on_statement"),
    ("What can you tell me about getting a virtual disposable card?", "get_disposable_virtual_card"),
    ("Someone stole my card. I need to report it stolen. I made a police report already, but how do I report it with you?", "lost_or_stolen_card"),
    ("How do I retrieve my passcode?", "passcode_forgotten"),
    ("What would lead to my top up still pending?", "pending_top_up"),
    ("Will you reinstate my PIN?", "pin_blocked"),
    ("Can I receive my salary in a currency other than what it is deposited in?", "receiving_money"),
    ("Get an item refund", "request_refund"),
    ("i was charged when i used a us issued card. why and what cards are free to use to add money", "top_up_by_card_charge"),
    ("I have a top up that didn't go through. Why?", "top_up_failed"),
    ("Why am I being charged twice for a restaurant?", "transaction_charged_twice"),
    ("Where are my funds being sourced from?", "verify_source_of_funds"),
    ("How do I get a visa card?", "visa_or_mastercard"),
    ("do I need to verify my identity every time I log into my account?", "why_verify_identity"),
    ("How come the ATM gave me less cash than what I asked for?", "wrong_amount_of_cash_received"),
    ("What's my address on file?", "(control) personal data retrieval"),
    ("I want to activate my new card, also how do I change my PIN, and can I open a margin trading account with you?", "(control) multi-part decomposition + decline"),
]


def _rate_limit_info(err: Exception) -> dict:
    code = getattr(err, "code", None)
    status = getattr(err, "status", None)
    details = getattr(err, "details", None)
    message = str(err)
    try:
        blob = json.dumps(details, default=str) if details else message
    except Exception:
        blob = message
    is_429 = code == 429 or status == "RESOURCE_EXHAUSTED" or "429" in message or "RESOURCE_EXHAUSTED" in message
    if not is_429:
        return {"rate_limited": False}
    retry_seconds = None
    m = re.search(r'"?retryDelay"?\s*[:=]\s*"?(\d+)\s*s', blob, re.IGNORECASE)
    if m:
        retry_seconds = int(m.group(1))
    is_daily = bool(re.search(r'per[_ ]?day|daily|PerDay', blob, re.IGNORECASE))
    return {"rate_limited": True, "retry_seconds": retry_seconds, "is_daily": is_daily, "raw": blob[:800]}


def _extract_full_summary(state: dict) -> dict:
    task_results = state.get("task_results", []) or []
    financial_subqueries = state.get("financial_subqueries", []) or []
    payload = state.get("ui_action_payload", {}) or {}

    subquery_status = {
        sq.get("subquery_id"): {
            "customer_component": sq.get("customer_component"),
            "tool_attempts": [
                {"tool_name": a.get("tool_name"), "status": a.get("status")}
                for a in sq.get("tool_attempts", [])
                if isinstance(a, dict)
            ],
        }
        for sq in financial_subqueries
    }
    tasks = [
        {
            "task_id": r.get("task_id"),
            "status": r.get("status"),
            "search_territory": r.get("search_territory", "in_scope"),
            "approved_parent_ids": r.get("approved_parent_ids", []),
            "generation_guidance": (r.get("generation_guidance") or "")[:250],
            "unresolved_term": r.get("unresolved_term", ""),
        }
        for r in task_results
        if isinstance(r, dict)
    ]
    return {
        "triage_status": state.get("triage_status"),
        "subquery_status": subquery_status,
        "task_results": tasks,
        "decline_tasks": [
            {"subquery": d.get("subquery"), "reason_category": d.get("reason_category")}
            for d in state.get("decline_tasks", []) or []
        ],
        "clarification_tasks": [
            {
                "unresolved_request": c.get("unresolved_request"),
                "originator": c.get("originator"),
                "reason": (c.get("clarification_reason") or "")[:200],
            }
            for c in state.get("clarification_tasks", []) or []
        ],
        "conversational_tasks": [c.get("customer_component") for c in state.get("conversational_tasks", []) or []],
        "final_response": state.get("final_response", ""),
        "ui_type": payload.get("uiType"),
        "response_blocks_count": len(payload.get("responseBlocks", []) or []),
        "service_request_actions": payload.get("serviceRequestActions", []),
        "navigation_actions": payload.get("navigationActions", []),
        "personal_data_reveals": payload.get("personalDataReveals", []),
        "personal_data_substituted": payload.get("personalDataSubstituted", False),
        "precision_substituted": payload.get("precisionSubstituted", False),
        "soft_escalation_available": payload.get("softEscalationAvailable", False),
        "next_actions": payload.get("nextActions", []),
        "safety_retry_count": state.get("safety_retry_count", 0),
    }


async def run_one(index: int, query: str, category: str) -> dict:
    session_id = f"fullval_{index}_{uuid.uuid4().hex[:8]}"
    config = {"configurable": {"thread_id": session_id}}
    ctx = set_request_context(session_id=session_id, user_id=TEST_USER_ID)
    attempt = 0
    try:
        while True:
            attempt += 1
            try:
                await app_graph.ainvoke(
                    {
                        "user_id": TEST_USER_ID,
                        "session_id": session_id,
                        "user_input": query,
                        "turn_count": 1,
                    },
                    config=config,
                )
                snapshot = app_graph.get_state(config)
                state = snapshot.values or {}
                return {
                    "index": index,
                    "query": query,
                    "category": category,
                    "status": "OK",
                    "attempts": attempt,
                    "session_id": session_id,
                    **_extract_full_summary(state),
                }
            except Exception as e:
                info = _rate_limit_info(e)
                if info.get("rate_limited"):
                    if info.get("is_daily"):
                        return {
                            "index": index, "query": query, "category": category,
                            "status": "STOPPED_DAILY_LIMIT", "attempts": attempt, "error": info.get("raw"),
                        }
                    if attempt >= MAX_RETRIES_PER_QUERY:
                        return {
                            "index": index, "query": query, "category": category,
                            "status": "FAILED_MAX_RETRIES", "attempts": attempt, "error": info.get("raw"),
                        }
                    wait = (info.get("retry_seconds") or DEFAULT_RATE_LIMIT_WAIT) + 3
                    with open(PROGRESS_PATH, "a") as f:
                        f.write(f"{datetime.now(timezone.utc).isoformat()} [{index}] rate-limited, waiting {wait}s (attempt {attempt})\n")
                    await asyncio.sleep(wait)
                    continue
                else:
                    return {
                        "index": index, "query": query, "category": category,
                        "status": "ERROR", "attempts": attempt, "error": str(e)[:800],
                    }
    finally:
        reset_request_context(ctx)


async def main():
    with open(PROGRESS_PATH, "a") as f:
        f.write(f"\n=== run started {datetime.now(timezone.utc).isoformat()} ===\n")

    results = []
    with open(RESULTS_PATH, "w") as out:
        for i, (query, category) in enumerate(TEST_QUERIES, start=1):
            started = time.perf_counter()
            result = await run_one(i, query, category)
            elapsed = time.perf_counter() - started
            results.append(result)
            out.write(json.dumps(result, ensure_ascii=False, default=str) + "\n")
            out.flush()
            with open(PROGRESS_PATH, "a") as f:
                f.write(
                    f"{datetime.now(timezone.utc).isoformat()} [{i}/{len(TEST_QUERIES)}] "
                    f"status={result['status']} elapsed={elapsed:.1f}s query={query[:60]!r}\n"
                )
            if result["status"] == "STOPPED_DAILY_LIMIT":
                with open(PROGRESS_PATH, "a") as f:
                    f.write("Daily quota exhausted -- stopping the run early.\n")
                break
            await asyncio.sleep(BASE_PACING_SECONDS)

    with open(PROGRESS_PATH, "a") as f:
        f.write(f"=== run finished {datetime.now(timezone.utc).isoformat()}, {len(results)}/{len(TEST_QUERIES)} queries processed ===\n")


if __name__ == "__main__":
    asyncio.run(main())
