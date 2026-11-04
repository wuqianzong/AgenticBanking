"""Replay ten captured outbound-audit prompts with the shared product mapping.

The target BUG-069 case is always included. Nine additional previously-passing
H1 audit prompts are selected with a fixed seed. Only the shared product
vocabulary contract is inserted; prompts are not persisted in the output.
"""
from __future__ import annotations

import hashlib
import json
import random
import sys
from pathlib import Path


D = Path(__file__).resolve().parents[1]
ROOT = D.parents[1]
sys.path.insert(0, str(ROOT))

from backend.app.orchestration import nodes as N  # noqa: E402


RESULTS = D / "runs/h1_banking77/results/20261014T185348Z_20d837bd_results.jsonl"
OUTPUT = D / "results/shared_product_vocabulary_audit_replay.json"
TARGET = "h1_h1_declined_card_payment_1"
HEADER = "GROUNDEDNESS v2.0 — COMBINED CONDUCT & EVIDENCE AUDIT"
ADDITION = (
    N._PRODUCT_VOCABULARY_CONTRACT
    + "\nApply this same vocabulary contract used by the intent formulator. "
    + "Do not reject an otherwise supported statement merely because it uses "
    + "a standardized product key instead of one of its equivalent names, or vice versa."
)
VERDICT_FIELDS = (
    "conduct_compliant",
    "answer_compliant",
    "clarification_compliant",
    "evidence_grounded",
    "answer_evidence_grounded",
    "answer_bridge_grounded",
    "clarification_grounded",
    "clarification_connected",
)


def verdict(result: dict) -> bool:
    evidence = bool(result.get("evidence_grounded", False))
    return all((
        bool(result.get("conduct_compliant", False)),
        bool(result.get("answer_compliant", False)),
        bool(result.get("clarification_compliant", False)),
        evidence,
        bool(result.get("answer_evidence_grounded", evidence)),
        bool(result.get("answer_bridge_grounded", evidence)),
        bool(result.get("clarification_grounded", False)),
        bool(result.get("clarification_connected", False)),
    ))


def audit_pairs(case: dict) -> list[tuple[str, dict]]:
    events = case.get("system_log_events", [])
    pairs = []
    for index, event in enumerate(events):
        prompt = event.get("raw_prompt", "")
        if event.get("event") != "llm.request" or HEADER not in prompt:
            continue
        for later in events[index + 1:]:
            if later.get("event") != "llm.response":
                continue
            try:
                pairs.append((prompt, N._parse_json_robustly(later.get("raw_response", ""))))
            except Exception:
                pass
            break
    return pairs


eligible = {}
for line in RESULTS.open():
    case = json.loads(line)
    pairs = audit_pairs(case)
    if pairs and verdict(pairs[-1][1]):
        eligible[case["case_id"]] = pairs[-1]

if TARGET not in eligible:
    raise RuntimeError(f"Required target {TARGET} has no captured passing audit prompt")

rng = random.Random(690)
other_ids = sorted(case_id for case_id in eligible if case_id != TARGET)
selected_ids = [TARGET, *rng.sample(other_ids, 9)]
requested_positions = {
    int(value) for value in sys.argv[1:]
} if len(sys.argv) > 1 else set(range(1, 11))

records = []
for position, case_id in enumerate(selected_ids, start=1):
    if position not in requested_positions:
        continue
    original_prompt, original_result = eligible[case_id]
    replay_prompt = original_prompt.replace(HEADER, HEADER + "\n\n" + ADDITION, 1)
    replay_result = N._parse_json_robustly(
        N.call_groq_lpu(replay_prompt, model_id=N.MODEL_B_AUDITOR)
    )
    original_pass = verdict(original_result)
    replay_pass = verdict(replay_result)
    record = {
        "position": position,
        "case_id": case_id,
        "model": N.MODEL_B_AUDITOR,
        "original_prompt_sha256": hashlib.sha256(original_prompt.encode()).hexdigest(),
        "only_prompt_change": "inserted shared product vocabulary contract",
        "original_pass": original_pass,
        "replay_pass": replay_pass,
        "verdict_preserved": original_pass == replay_pass,
        "original_fields": {key: original_result.get(key) for key in VERDICT_FIELDS},
        "replay_fields": {key: replay_result.get(key) for key in VERDICT_FIELDS},
        "replay_conduct_violation_count": len(replay_result.get("conduct_violations", [])),
        "replay_grounding_violation_count": len(
            replay_result.get("grounding_violations", replay_result.get("evidence_violations", []))
        ),
        "replay_evidence_reason": replay_result.get("evidence_reason", ""),
    }
    records.append(record)
    print(
        f"{position:02d}/10 {case_id}: original={'PASS' if original_pass else 'FAIL'} "
        f"replay={'PASS' if replay_pass else 'FAIL'}"
    )

summary = {
    "test": "shared product vocabulary outbound-audit replay",
    "selection": "target case plus nine fixed-seed random previously-passing H1 cases",
    "seed": 690,
    "requested_positions": sorted(requested_positions),
    "calls": len(records),
    "original_passes": sum(record["original_pass"] for record in records),
    "replay_passes": sum(record["replay_pass"] for record in records),
    "verdicts_preserved": sum(record["verdict_preserved"] for record in records),
    "degradations": [
        record["case_id"] for record in records
        if record["original_pass"] and not record["replay_pass"]
    ],
    "records": records,
}
OUTPUT.write_text(json.dumps(summary, indent=2) + "\n")
print(json.dumps({key: value for key, value in summary.items() if key != "records"}, indent=2))
