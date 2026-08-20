"""Extract auditable retrieval attempts from every rotated system log.

Read-only with respect to application data.  It joins a logged sufficiency
prompt (task formulation plus candidate parents) to its structured auditor
response, preserving the actual three-angle production retrieval outcome.
"""
from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
RESEARCH = Path(__file__).resolve().parents[1]
LOG_DIR = ROOT / "backend" / "logs"
OUTPUT = RESEARCH / "results" / "system_log_retrieval_attempts_v1.json"
MARKER = "Task Inputs: "
END = "\n\n        Rules:"

def parse_inputs(prompt: str):
    start = prompt.find(MARKER)
    if start < 0:
        return None
    start += len(MARKER)
    end = prompt.find(END, start)
    if end < 0:
        return None
    try:
        value = json.loads(prompt[start:end])
    except json.JSONDecodeError:
        return None
    return value if isinstance(value, list) else None

def events():
    rows = []
    for path in sorted(LOG_DIR.glob("system.jsonl*")):
        for offset, line in enumerate(path.read_text(encoding="utf-8").splitlines()):
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            row["_source"] = path.name
            row["_offset"] = offset
            rows.append(row)
    return sorted(rows, key=lambda row: (row.get("timestamp", ""), row["_source"], row["_offset"]))

def paired_response(stream, start):
    session_id = stream[start].get("session_id")
    for row in stream[start + 1:start + 40]:
        if row.get("session_id") != session_id or row.get("event") != "llm.response":
            continue
        try:
            value = json.loads(row.get("raw_response", ""))
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict) and isinstance(value.get("task_results"), list):
            return value
    return None

def main():
    stream = events()
    attempts, unmatched = [], 0
    for index, row in enumerate(stream):
        prompt = row.get("raw_prompt", "")
        if row.get("event") != "llm.request" or "TASK-LEVEL SUFFICIENCY AND EVIDENCE SELECTION" not in prompt:
            continue
        inputs = parse_inputs(prompt)
        response = paired_response(stream, index)
        if inputs is None or response is None:
            unmatched += 1
            continue
        outcome = {item.get("task_id"): item for item in response["task_results"] if isinstance(item, dict)}
        for task_input in inputs:
            task = task_input.get("task", {})
            parents = task_input.get("parents", [])
            task_id = task_input.get("task_id")
            result = outcome.get(task_id, {})
            angle_groups = task.get("angle_groups", [])
            # Earlier production records stored the same three angles as flat
            # task fields; mirror RAGEngine._normalize_angle_groups so every
            # rotated log is evaluated under its actual retrieval contract.
            if not angle_groups and any(task.get(name) for name in ("procedural", "policy", "conceptual")):
                angle_groups = [{
                    "group_id": "legacy_base",
                    "procedural": task.get("procedural", ""),
                    "policy": task.get("policy", ""),
                    "conceptual": task.get("conceptual", ""),
                }]
            attempts.append({
                "timestamp": row.get("timestamp"),
                "source_log": row["_source"],
                "session_id": row.get("session_id"),
                "task_id": task_id,
                "customer_component": task.get("customer_component"),
                "product": task.get("target_commercial_product"),
                "intents": task.get("target_intent", []),
                "angle_groups": angle_groups,
                "candidate_parent_ids": [parent.get("parent_id") for parent in parents if parent.get("parent_id")],
                "auditor_status": result.get("status"),
                "approved_parent_ids": result.get("approved_parent_ids", []),
                "auditor_reason": result.get("reason", ""),
                "search_feedback": result.get("search_feedback", ""),
            })
    summary = {
        "source_logs": [path.name for path in sorted(LOG_DIR.glob("system.jsonl*"))],
        "event_count": len(stream),
        "sufficiency_attempt_count": len(attempts),
        "unique_session_task_count": len({(item["session_id"], item["task_id"]) for item in attempts}),
        "unmatched_sufficiency_prompts": unmatched,
        "status_counts": dict(Counter(item["auditor_status"] for item in attempts)),
    }
    OUTPUT.write_text(json.dumps({"metadata": {"purpose": "Merged, timestamp-sorted extraction of production retrieval attempts from all rotated logs", "important_limit": "A task may appear repeatedly as it retries; attempts are not independent customer queries."}, "summary": summary, "attempts": attempts}, indent=2), encoding="utf-8")
    print(json.dumps(summary, indent=2))

if __name__ == "__main__":
    main()
