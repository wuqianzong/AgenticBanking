"""Freeze one tagged retrieval-evaluation cohort from rotated application logs.

The source logs are operational runtime artifacts and are intentionally not
versioned.  This script copies only events belonging to one explicit session
prefix into a stable research result, retaining the full v2 retrieval telemetry
needed for direct-versus-angle analysis.
"""
from __future__ import annotations

import json
from collections import Counter, defaultdict
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
LOG_FILE = ROOT / "backend" / "logs" / "system.jsonl.1"
SESSION_PREFIX = "eval_bank77_tagged2_20260820_"
OUTPUT = (
    Path(__file__).resolve().parents[1]
    / "results"
    / "banking77_tagged_retrieval_pilot_20260820_v1.json"
)


def main() -> None:
    sessions: dict[str, dict] = {}
    for offset, line in enumerate(LOG_FILE.read_text(encoding="utf-8").splitlines()):
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        session_id = str(event.get("session_id", ""))
        if not session_id.startswith(SESSION_PREFIX):
            continue

        record = sessions.setdefault(
            session_id,
            {
                "session_id": session_id,
                "source_log": LOG_FILE.name,
                "source_offsets": [],
                "question": None,
                "turn_id": None,
                "formulations": [],
                "retrieval_attempts": [],
                "sufficiency_events": [],
                "llm_errors": [],
                "terminal": None,
            },
        )
        record["source_offsets"].append(offset)
        kind = event.get("event")
        if kind == "chat.request":
            record["question"] = event.get("message")
            record["turn_id"] = event.get("turn_id")
        elif kind == "task.formulated":
            record["formulations"].append(
                {
                    "timestamp": event.get("timestamp"),
                    "retry_mode": event.get("retry_mode"),
                    "tasks": event.get("tasks", []),
                    "planned_expansion": event.get("planned_expansion", []),
                }
            )
        elif kind == "task.retrieval.completed":
            record["retrieval_attempts"].append(
                {
                    "timestamp": event.get("timestamp"),
                    "task_id": event.get("task_id"),
                    "customer_component": event.get("customer_component"),
                    "retry_count": event.get("retry_count"),
                    "telemetry": event.get("telemetry", {}),
                }
            )
        elif kind in {
            "task.sufficiency.completed",
            "task.retry",
            "task.clarification_required",
        }:
            record["sufficiency_events"].append(
                {
                    "timestamp": event.get("timestamp"),
                    "event": kind,
                    "task_id": event.get("task_id"),
                    "status": event.get("status"),
                    "auditor_status": event.get("auditor_status"),
                    "candidate_parent_ids": event.get("candidate_parent_ids", []),
                    "approved_parent_ids": event.get("approved_parent_ids", []),
                    "reason": event.get("reason", ""),
                    "search_feedback": event.get("search_feedback", ""),
                    "retry_count": event.get("retry_count"),
                    "next_expansion_stage": event.get("next_expansion_stage", ""),
                }
            )
        elif kind == "llm.error":
            record["llm_errors"].append(
                {
                    "timestamp": event.get("timestamp"),
                    "model_id": event.get("model_id"),
                    "requested_model_id": event.get("requested_model_id"),
                    "will_retry": event.get("will_retry"),
                    "error": event.get("error", ""),
                }
            )
        elif kind == "retrieval.turn.completed":
            record["terminal"] = {
                "timestamp": event.get("timestamp"),
                "overall_outcome": event.get("overall_outcome"),
                "preflight_status": event.get("preflight_status"),
                "task_count": event.get("task_count"),
                "sufficient_task_count": event.get("sufficient_task_count"),
                "terminal_failure_task_count": event.get("terminal_failure_task_count"),
                "tasks": event.get("tasks", []),
            }

    ordered = [sessions[key] for key in sorted(sessions)]
    missing_terminal = [item["session_id"] for item in ordered if item["terminal"] is None]
    all_attempts = [attempt for item in ordered for attempt in item["retrieval_attempts"]]
    telemetry_versions = Counter(
        attempt["telemetry"].get("telemetry_version") for attempt in all_attempts
    )
    summary = {
        "session_count": len(ordered),
        "terminal_count": len(ordered) - len(missing_terminal),
        "outcome_counts": dict(
            Counter(item["terminal"]["overall_outcome"] for item in ordered if item["terminal"])
        ),
        "retrieval_attempt_count": len(all_attempts),
        "sessions_with_retrieval": sum(bool(item["retrieval_attempts"]) for item in ordered),
        "telemetry_version_counts": dict(telemetry_versions),
        "missing_terminal_sessions": missing_terminal,
    }
    if summary["session_count"] != 10 or summary["terminal_count"] != 10:
        raise RuntimeError(f"unexpected pilot cohort integrity: {summary}")
    if any(version != 2 for version in telemetry_versions):
        raise RuntimeError(f"pilot has incomplete telemetry: {summary}")

    payload = {
        "metadata": {
            "purpose": "Frozen first tagged Banking77 retrieval pilot for direct-versus-angle analysis.",
            "session_prefix": SESSION_PREFIX,
            "source_log": LOG_FILE.name,
            "important_limit": (
                "This is a ten-session telemetry and pipeline-integrity pilot, not a sufficient "
                "sample for testing the narrative or dispersion hypothesis."
            ),
        },
        "summary": summary,
        "sessions": ordered,
    }
    OUTPUT.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
