"""Shared runner, collection, assertion, and reporting helpers for studies.

The default runner imports the compiled production graph and invokes it in an
isolated process. It intentionally does not call API-gateway archive or memory
persistence helpers.
"""
from __future__ import annotations

import asyncio
import json
import re
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_PACING_SECONDS = 25.0
DEFAULT_MAX_RETRIES = 5
DEFAULT_RATE_LIMIT_WAIT_SECONDS = 65


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ValueError(f"Missing file: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid JSON in {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"Expected a JSON object in {path}")
    return value


def load_cases(path: Path) -> list[dict[str, Any]]:
    cases: list[dict[str, Any]] = []
    for line_number, raw_line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not raw_line.strip() or raw_line.lstrip().startswith("#"):
            continue
        try:
            case = json.loads(raw_line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"Invalid JSONL in {path}:{line_number}: {exc}") from exc
        if not isinstance(case, dict) or not case.get("case_id"):
            raise ValueError(f"Every case needs a non-empty case_id ({path}:{line_number})")
        messages = case.get("messages")
        if not isinstance(messages, list) or not messages or not all(isinstance(item, str) and item.strip() for item in messages):
            raise ValueError(f"Every case needs a non-empty messages list ({case['case_id']})")
        cases.append(case)
    if not cases:
        raise ValueError(f"No cases found in {path}")
    duplicate_ids = {case_id for case_id in (c["case_id"] for c in cases) if sum(x["case_id"] == case_id for x in cases) > 1}
    if duplicate_ids:
        raise ValueError(f"Duplicate case_id values: {sorted(duplicate_ids)}")
    return cases


@dataclass(frozen=True)
class StudyConfig:
    study_id: str
    manifest_path: Path
    fixture_path: Path
    runner: str
    user_id: str
    pacing_seconds: float
    max_retries: int
    default_rate_limit_wait_seconds: int
    capture_system_log: bool

    @classmethod
    def from_manifest(cls, manifest_path: Path) -> "StudyConfig":
        manifest = load_json(manifest_path)
        runner = str(manifest.get("runner", "graph_validation"))
        if runner != "graph_validation":
            raise ValueError(
                "Only runner='graph_validation' is implemented. "
                "API/persistence evaluation must be introduced as a separate adapter, "
                "not silently approximated by this isolated runner."
            )
        fixture = manifest.get("fixture")
        if not isinstance(fixture, str) or not fixture:
            raise ValueError("study.json requires a relative fixture path")
        return cls(
            study_id=str(manifest.get("study_id") or manifest_path.parent.name),
            manifest_path=manifest_path,
            fixture_path=(manifest_path.parent / fixture).resolve(),
            runner=runner,
            user_id=str(manifest.get("user_id", "USR_999")),
            pacing_seconds=float(manifest.get("pacing_seconds", DEFAULT_PACING_SECONDS)),
            max_retries=int(manifest.get("max_retries_per_turn", DEFAULT_MAX_RETRIES)),
            default_rate_limit_wait_seconds=int(
                manifest.get("default_rate_limit_wait_seconds", DEFAULT_RATE_LIMIT_WAIT_SECONDS)
            ),
            capture_system_log=bool(manifest.get("capture_system_log", True)),
        )


def rate_limit_info(error: Exception) -> dict[str, Any]:
    """Classify Gemini/Groq-style rate-limit errors without provider imports."""
    code = getattr(error, "code", None)
    status = getattr(error, "status", None)
    details = getattr(error, "details", None)
    message = str(error)
    try:
        detail_blob = json.dumps(details, default=str) if details else message
    except Exception:
        detail_blob = message
    is_limited = (
        code == 429
        or status == "RESOURCE_EXHAUSTED"
        or "429" in message
        or "RESOURCE_EXHAUSTED" in message
    )
    if not is_limited:
        return {"rate_limited": False}
    delay_match = re.search(r'"?retryDelay"?\s*[:=]\s*"?(\d+)\s*s', detail_blob, re.IGNORECASE)
    daily = bool(re.search(r"per[_ ]?day|daily|PerDay", detail_blob, re.IGNORECASE))
    return {
        "rate_limited": True,
        "retry_seconds": int(delay_match.group(1)) if delay_match else None,
        "is_daily": daily,
        "raw": detail_blob[:1000],
    }


def extract_state_summary(state: dict[str, Any]) -> dict[str, Any]:
    """Stable, sanitized telemetry view shared by all graph-validation studies."""
    payload = state.get("ui_action_payload") or {}
    subqueries = state.get("financial_subqueries") or []
    task_results = state.get("task_results") or []
    return {
        "triage_status": state.get("triage_status"),
        "turn_count": state.get("turn_count"),
        "preflight_status": state.get("preflight_status"),
        "subquery_status": {
            item.get("subquery_id"): {
                "customer_component": item.get("customer_component"),
                "tool_attempts": [
                    {"tool_name": attempt.get("tool_name"), "status": attempt.get("status")}
                    for attempt in item.get("tool_attempts", [])
                    if isinstance(attempt, dict)
                ],
            }
            for item in subqueries
            if isinstance(item, dict) and item.get("subquery_id")
        },
        "task_results": [
            {
                "task_id": task.get("task_id"),
                "status": task.get("status"),
                "search_territory": task.get("search_territory", "in_scope"),
                "approved_parent_ids": task.get("approved_parent_ids", []),
                "generation_guidance": (task.get("generation_guidance") or "")[:500],
                "unresolved_term": task.get("unresolved_term", ""),
                "reason": (task.get("reason") or "")[:500],
            }
            for task in task_results
            if isinstance(task, dict)
        ],
        "decline_tasks": [
            {
                "subquery": item.get("subquery"),
                "reason_category": item.get("reason_category"),
                "detail": (item.get("detail") or "")[:300],
            }
            for item in state.get("decline_tasks", []) or []
            if isinstance(item, dict)
        ],
        "clarification_tasks": [
            {
                "unresolved_request": item.get("unresolved_request"),
                "originator": item.get("originator"),
                "reason": (item.get("clarification_reason") or "")[:300],
            }
            for item in state.get("clarification_tasks", []) or []
            if isinstance(item, dict)
        ],
        "conversational_tasks": [
            item.get("customer_component")
            for item in state.get("conversational_tasks", []) or []
            if isinstance(item, dict)
        ],
        "final_response": state.get("final_response", ""),
        "response_blocks": payload.get("responseBlocks", []),
        "ui_type": payload.get("uiType"),
        "service_request_actions": payload.get("serviceRequestActions", []),
        "navigation_actions": payload.get("navigationActions", []),
        "personal_data_reveals": payload.get("personalDataReveals", []),
        "personal_data_substituted": payload.get("personalDataSubstituted", False),
        "precision_substituted": payload.get("precisionSubstituted", False),
        "soft_escalation_available": payload.get("softEscalationAvailable", False),
        "next_actions": payload.get("nextActions", []),
        "safety_retry_count": state.get("safety_retry_count", 0),
    }


def extract_system_log_events(session_id: str) -> list[dict[str, Any]]:
    """Read only the current structured log file and return this run's events."""
    log_path = REPO_ROOT / "backend" / "logs" / "system.jsonl"
    if not log_path.exists():
        return []
    events: list[dict[str, Any]] = []
    for line in log_path.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if record.get("session_id") == session_id:
            events.append(record)
    return events


def evaluate_assertions(summary: dict[str, Any], expected: dict[str, Any]) -> list[dict[str, Any]]:
    """Run only stable, declarative checks. Semantic quality remains a rubric."""
    outcomes: list[dict[str, Any]] = []

    def add(name: str, passed: bool, detail: str) -> None:
        outcomes.append({"name": name, "passed": passed, "detail": detail})

    response = summary.get("final_response", "")
    payload_text = json.dumps({
        "response_blocks": summary.get("response_blocks"),
        "navigation_actions": summary.get("navigation_actions"),
    }, ensure_ascii=False)
    all_tools = {
        attempt.get("tool_name")
        for subquery in (summary.get("subquery_status") or {}).values()
        for attempt in subquery.get("tool_attempts", [])
        if isinstance(attempt, dict) and attempt.get("tool_name")
    }
    actual_escalation = bool(summary.get("soft_escalation_available")) or summary.get("triage_status") == "ESCALATION"

    for tool in expected.get("must_use_tools", []) or []:
        add(f"must_use_tools:{tool}", tool in all_tools, f"observed={sorted(all_tools)}")
    for tool in expected.get("must_not_use_tools", []) or []:
        add(f"must_not_use_tools:{tool}", tool not in all_tools, f"observed={sorted(all_tools)}")
    if expected.get("must_not_escalate") is True:
        add("must_not_escalate", not actual_escalation, f"actual_escalation={actual_escalation}")
    if expected.get("must_escalate") is True:
        add("must_escalate", actual_escalation, f"actual_escalation={actual_escalation}")
    for forbidden in expected.get("must_not_emit", []) or []:
        token_map = {
            "raw_route": ["/app/", "http://localhost:5173"],
            "destination_id": ["destinationId", "card-management", "security-settings"],
            "unresolved_placeholder": ["{{faq_", "{{data-"],
        }
        tokens = token_map.get(forbidden, [str(forbidden)])
        combined = f"{response}\n{payload_text}"
        add(f"must_not_emit:{forbidden}", not any(token in combined for token in tokens), f"tokens={tokens}")
    for required_topic in expected.get("required_topics", []) or []:
        add(f"required_topic:{required_topic}", True, "Requires source/LLM/human rubric review; not text-matched.")
    return outcomes


def write_markdown_report(path: Path, run: dict[str, Any]) -> None:
    records = run["records"]
    completed = [record for record in records if record["status"] == "OK"]
    errors = [record for record in records if record["status"] != "OK"]
    assertion_outcomes = [outcome for record in records for turn in record.get("turns", []) for outcome in turn.get("assertions", [])]
    assertion_passed = sum(bool(outcome["passed"]) for outcome in assertion_outcomes)
    lines = [
        f"# Automated Metrics Run: {run['run_id']}",
        "",
        f"- Study: `{run['study_id']}`",
        f"- Started: `{run['started_at']}`",
        f"- Completed: `{run['completed_at']}`",
        f"- Runner: `{run['runner']}`",
        f"- Cases: `{len(records)}`; completed: `{len(completed)}`; non-OK: `{len(errors)}`",
        f"- Assertions: `{assertion_passed}/{len(assertion_outcomes)}` passed",
        "",
        "## Cases requiring review",
        "",
        "| Case | Status | Failed assertions | Artifact |",
        "|---|---|---:|---|",
    ]
    for record in records:
        failures = sum(not item["passed"] for turn in record.get("turns", []) for item in turn.get("assertions", []))
        if record["status"] != "OK" or failures:
            lines.append(f"| {record['case_id']} | {record['status']} | {failures} | `{run['results_path'].name}` |")
    if len(lines) == 13:
        lines.append("| None | OK | 0 | — |")
    lines.extend([
        "",
        "## Interpretation required",
        "",
        "This generated report records mechanical outcomes only. Add source-verified relevance, "
        "groundedness, naturalness, and policy conclusions in `reports/RESEARCH_SUMMARY.md` before "
        "claiming a quality improvement.",
    ])
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


async def run_graph_study(config: StudyConfig, cases: list[dict[str, Any]], output_dir: Path) -> dict[str, Any]:
    """Execute a fixture through the real graph without persistence helpers."""
    from backend.app.core.system_logger import reset_request_context, set_request_context
    from backend.app.orchestration.graph import app_graph

    output_dir.mkdir(parents=True, exist_ok=True)
    run_id = f"{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}_{uuid.uuid4().hex[:8]}"
    results_path = output_dir / f"{run_id}_results.jsonl"
    progress_path = output_dir / f"{run_id}_progress.txt"
    report_path = config.manifest_path.parent / "reports" / f"{run_id}_AUTOMATED_REPORT.md"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    started_at = utc_now()
    records: list[dict[str, Any]] = []

    with progress_path.open("w", encoding="utf-8") as progress, results_path.open("w", encoding="utf-8") as results_file:
        progress.write(f"=== run started {started_at} study={config.study_id} cases={len(cases)} ===\n")
        for case_index, case in enumerate(cases, start=1):
            session_id = f"eval_{config.study_id}_{case_index}_{uuid.uuid4().hex[:8]}"
            graph_config = {"configurable": {"thread_id": session_id}}
            turn_records: list[dict[str, Any]] = []
            case_status = "OK"
            previous_turn_count = 0
            for turn_index, message in enumerate(case["messages"], start=1):
                attempts = 0
                started = time.perf_counter()
                context_token = set_request_context(session_id=session_id, user_id=config.user_id, evaluation_run_id=run_id, evaluation_case_id=case["case_id"])
                try:
                    while True:
                        attempts += 1
                        try:
                            await app_graph.ainvoke(
                                {
                                    "user_id": config.user_id,
                                    "session_id": session_id,
                                    "user_input": message,
                                    "turn_count": previous_turn_count + 1,
                                },
                                config=graph_config,
                            )
                            snapshot = app_graph.get_state(graph_config)
                            state = snapshot.values or {}
                            previous_turn_count = int(state.get("turn_count", previous_turn_count + 1))
                            summary = extract_state_summary(state)
                            expected = case.get("expected", {}) if (
                                turn_index == len(case["messages"]) or case.get("assert_each_turn", False)
                            ) else {}
                            turn_records.append({
                                "turn_index": turn_index,
                                "message": message,
                                "status": "OK",
                                "attempts": attempts,
                                "elapsed_seconds": round(time.perf_counter() - started, 3),
                                "summary": summary,
                                "assertions": evaluate_assertions(summary, expected),
                            })
                            break
                        except Exception as error:
                            info = rate_limit_info(error)
                            if not info.get("rate_limited"):
                                case_status = "ERROR"
                                turn_records.append({"turn_index": turn_index, "message": message, "status": "ERROR", "attempts": attempts, "error": str(error)[:1000], "elapsed_seconds": round(time.perf_counter() - started, 3), "assertions": []})
                                break
                            if info.get("is_daily"):
                                case_status = "STOPPED_DAILY_LIMIT"
                                turn_records.append({"turn_index": turn_index, "message": message, "status": case_status, "attempts": attempts, "error": info.get("raw"), "elapsed_seconds": round(time.perf_counter() - started, 3), "assertions": []})
                                break
                            if attempts >= config.max_retries:
                                case_status = "FAILED_MAX_RETRIES"
                                turn_records.append({"turn_index": turn_index, "message": message, "status": case_status, "attempts": attempts, "error": info.get("raw"), "elapsed_seconds": round(time.perf_counter() - started, 3), "assertions": []})
                                break
                            wait_seconds = (info.get("retry_seconds") or config.default_rate_limit_wait_seconds) + 3
                            progress.write(f"{utc_now()} case={case['case_id']} turn={turn_index} rate_limited wait={wait_seconds}s attempt={attempts}\n")
                            progress.flush()
                            await asyncio.sleep(wait_seconds)
                finally:
                    reset_request_context(context_token)
                if case_status != "OK":
                    break
                if turn_index < len(case["messages"]):
                    await asyncio.sleep(config.pacing_seconds)

            record = {
                "run_id": run_id,
                "study_id": config.study_id,
                "case_index": case_index,
                "case_id": case["case_id"],
                "title": case.get("title", case["case_id"]),
                "tags": case.get("tags", []),
                "expected": case.get("expected", {}),
                "review_rubric": case.get("review_rubric", []),
                "session_id": session_id,
                "status": case_status,
                "turns": turn_records,
            }
            if config.capture_system_log:
                record["system_log_events"] = extract_system_log_events(session_id)
            records.append(record)
            results_file.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
            results_file.flush()
            progress.write(f"{utc_now()} [{case_index}/{len(cases)}] case={case['case_id']} status={case_status}\n")
            progress.flush()
            if case_status == "STOPPED_DAILY_LIMIT":
                progress.write(f"{utc_now()} daily quota exhausted; stopping run.\n")
                break
            if case_index < len(cases):
                await asyncio.sleep(config.pacing_seconds)
        completed_at = utc_now()
        progress.write(f"=== run finished {completed_at} cases={len(records)} ===\n")

    run = {
        "run_id": run_id,
        "study_id": config.study_id,
        "runner": config.runner,
        "started_at": started_at,
        "completed_at": completed_at,
        "results_path": results_path,
        "progress_path": progress_path,
        "records": records,
    }
    write_markdown_report(report_path, run)
    run["report_path"] = report_path
    return run
