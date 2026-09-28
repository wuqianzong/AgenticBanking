"""Live validation harness for the SUFFICIENT/RELEVANT/IRRELEVANT rename and
the Territory Ceiling removal in node_03c_sufficiency_auditor.

Standalone test script -- imports the real, unmodified backend graph and
invokes it directly (the same call shape api_gateway.py's /api/chat uses),
never calling archive_session_history, so no test conversation is written to
the persistent chat/ledger tables. Reads test queries from the real BANKING77
dataset already vendored in this repo (research/2026-08-19_retrieval_query_
refinement/data/banking77_test.csv).

Rate-limit discipline: every call to Gemini goes through the real
call_llm_router, which raises google.genai.errors.APIError (code=429,
status="RESOURCE_EXHAUSTED") on a quota hit. This harness inspects that
error's own `details` payload for a RetryInfo.retryDelay (a per-minute quota,
worth waiting out) versus a QuotaFailure whose quotaId names a daily/per-day
metric (worth stopping for -- waiting would not help). A fixed pacing delay
between turns is applied regardless of errors, so this never fires requests
back-to-back even when nothing is rate-limited yet.

Run: python3 -m research.2026-09-04_relevant_irrelevant_validation.scripts.run_validation
(invoked from the repo root so backend.* imports resolve)
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
BASE_PACING_SECONDS = 25  # deliberate minimum spacing between turns, regardless of errors
MAX_RETRIES_PER_QUERY = 5
DEFAULT_RATE_LIMIT_WAIT = 65  # used only when a 429 carries no explicit retryDelay

OUT_DIR = Path(__file__).resolve().parent.parent / "results"
OUT_DIR.mkdir(parents=True, exist_ok=True)
RESULTS_PATH = OUT_DIR / "test_results.jsonl"
PROGRESS_PATH = OUT_DIR / "progress.txt"

# Each entry: (query, banking77_category, what_we_expect_to_probe)
TEST_QUERIES = [
    ("How do I locate my card?", "card_arrival", "SUFFICIENT — card delivery/tracking FAQ"),
    ("I no longer live at my address on file, how do I change it?", "edit_personal_details", "Service Request — profile-address-update"),
    ("Would it be possible to get another card?", "getting_spare_card", "SUFFICIENT or Service Request — card-replacement-request"),
    ("How old do i need to be to get an account for myself", "age_limit", "SUFFICIENT — Deposit_Account eligibility"),
    ("I need to know your exchange rates.", "exchange_rate", "RELEVANT or IRRELEVANT — forex not a Horizon product"),
    ("Is the top up feature available on the Apple Watch?", "apple_pay_or_google_pay", "IRRELEVANT — no matching product/topic"),
    ("Can you tell me why you won't let me get my own money out of the money machine? I know I used the right PIN.", "declined_cash_withdrawal", "IRRELEVANT or escalation — no general FAQ for a specific declined withdrawal"),
    ("I think someone is using my card to make transactions I don't remember.", "compromised_card", "Credit/Debit Card fraud path — service request or escalation"),
    ("Can you remove my account please?", "terminate_account", "CLARIFICATION_REQUIRED — which account/product unspecified"),
    ("I cannot verify my identity", "unable_to_verify_identity", "Service Request — kyc-document-resubmission, or CLARIFICATION"),
    ("Can I do a bank transfer to put additional money in my account because I am out of money?", "transfer_into_account", "Ambiguous Deposit_Account vs Personal_Loan — possible Territory B expansion"),
    ("What is the number of days I have to wait for my Europe transfer?", "transfer_timing", "RELEVANT or IRRELEVANT — specific detail likely undocumented"),
    ("I live in the US but want to get a card", "country_support", "Possible DECLINE (segment/geography) or CLARIFICATION"),
    ("How do I apply for a loan?", "(control, not BANKING77)", "SUFFICIENT/RELEVANT — known-good case from earlier live traces"),
    ("Can I open a margin trading account with you?", "(control, not BANKING77)", "DECLINE — out-of-scope product, regression check"),
]


def _rate_limit_info(err: Exception) -> dict:
    """Inspect an exception for a Gemini 429. Returns rate_limited=False for
    anything else. For a real 429, returns retry_seconds (None if not
    stated) and is_daily (True when the quota violation names a per-day
    metric, in which case waiting is pointless)."""
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


def _extract_task_summary(state: dict) -> dict:
    task_results = state.get("task_results", []) or []
    financial_subqueries = state.get("financial_subqueries", []) or []
    subquery_status = {}
    for sq in financial_subqueries:
        subquery_status[sq.get("subquery_id")] = {
            "customer_component": sq.get("customer_component"),
            "tool_attempts": [
                {"tool_name": a.get("tool_name"), "status": a.get("status")}
                for a in sq.get("tool_attempts", [])
                if isinstance(a, dict)
            ],
        }
    tasks = [
        {
            "task_id": r.get("task_id"),
            "status": r.get("status"),
            "search_territory": r.get("search_territory", "in_scope"),
            "approved_parent_ids": r.get("approved_parent_ids", []),
            "generation_guidance": (r.get("generation_guidance") or "")[:300],
            "unresolved_term": r.get("unresolved_term", ""),
            "reason": (r.get("reason") or "")[:300],
        }
        for r in task_results
        if isinstance(r, dict)
    ]
    return {
        "subquery_status": subquery_status,
        "task_results": tasks,
        "decline_tasks": [
            {"subquery": d.get("subquery"), "reason_category": d.get("reason_category"), "detail": (d.get("detail") or "")[:200]}
            for d in state.get("decline_tasks", []) or []
        ],
        "clarification_tasks": [
            {"unresolved_request": c.get("unresolved_request"), "originator": c.get("originator"), "reason": (c.get("clarification_reason") or "")[:200]}
            for c in state.get("clarification_tasks", []) or []
        ],
        "conversational_tasks": [c.get("customer_component") for c in state.get("conversational_tasks", []) or []],
        "final_response": state.get("final_response", ""),
        "ui_type": (state.get("ui_action_payload") or {}).get("uiType"),
    }


async def run_one(index: int, query: str, category: str, probe: str) -> dict:
    session_id = f"validation_{index}_{uuid.uuid4().hex[:8]}"
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
                result = {
                    "index": index,
                    "query": query,
                    "category": category,
                    "probe": probe,
                    "status": "OK",
                    "attempts": attempt,
                    **_extract_task_summary(state),
                }
                return result
            except Exception as e:
                info = _rate_limit_info(e)
                if info.get("rate_limited"):
                    if info.get("is_daily"):
                        return {
                            "index": index, "query": query, "category": category, "probe": probe,
                            "status": "STOPPED_DAILY_LIMIT", "attempts": attempt, "error": info.get("raw"),
                        }
                    if attempt >= MAX_RETRIES_PER_QUERY:
                        return {
                            "index": index, "query": query, "category": category, "probe": probe,
                            "status": "FAILED_MAX_RETRIES", "attempts": attempt, "error": info.get("raw"),
                        }
                    wait = (info.get("retry_seconds") or DEFAULT_RATE_LIMIT_WAIT) + 3
                    with open(PROGRESS_PATH, "a") as f:
                        f.write(f"{datetime.now(timezone.utc).isoformat()} [{index}] rate-limited, waiting {wait}s (attempt {attempt})\n")
                    await asyncio.sleep(wait)
                    continue
                else:
                    return {
                        "index": index, "query": query, "category": category, "probe": probe,
                        "status": "ERROR", "attempts": attempt, "error": str(e)[:800],
                    }
    finally:
        reset_request_context(ctx)


async def main():
    with open(PROGRESS_PATH, "a") as f:
        f.write(f"\n=== run started {datetime.now(timezone.utc).isoformat()} ===\n")

    results = []
    with open(RESULTS_PATH, "w") as out:
        for i, (query, category, probe) in enumerate(TEST_QUERIES, start=1):
            started = time.perf_counter()
            result = await run_one(i, query, category, probe)
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
            # Deliberate pacing between turns regardless of outcome.
            await asyncio.sleep(BASE_PACING_SECONDS)

    with open(PROGRESS_PATH, "a") as f:
        f.write(f"=== run finished {datetime.now(timezone.utc).isoformat()}, {len(results)}/{len(TEST_QUERIES)} queries processed ===\n")


if __name__ == "__main__":
    asyncio.run(main())
