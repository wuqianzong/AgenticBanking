import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from backend.app.core.llm_service import parse_json_robustly
from backend.app.orchestration import nodes as N


checks = []


def check(label, condition):
    checks.append(bool(condition))
    print("PASS" if condition else "FAIL", label)


raw = json.dumps({
    "elements": [{
        "type": "TEXT",
        "source_task_ids": ["task_1"],
        "answer": "Horizon supports only domestic SGD transfers.",
    }]
}) + "}"
parsed = parse_json_robustly(raw)
check("removes a trailing unmatched closer", parsed["elements"][0]["answer"].startswith("Horizon"))

try:
    parse_json_robustly('{"elements": [{"answer": "truncated"}')
    rejected_interior_damage = False
except json.JSONDecodeError:
    rejected_interior_damage = True
check("does not guess how to repair interior or truncated JSON", rejected_interior_damage)

pending = [{
    "blockId": "net_fallback_task_1",
    "sourceTaskIds": ["task_1"],
    "sourceShortcutHandles": [],
    "type": "text",
    "contextText": "",
    "text": "Sorry, I don't have complete information available right now.",
    "extendedText": "",
    "requiresEvidenceRevision": True,
}]
generated = {
    "response_blocks": [{
        "block_id": "task_1",
        "type": "text",
        "revision_status": "pending",
        "content": "Freeze the card and contact Horizon immediately.",
    }]
}
normalized = N._normalize_reviser_output(generated, pending)
check("maps an unambiguous source task ID to its pending block", set(normalized) == {"net_fallback_task_1"})
check("maps generic content to the text block leaf", normalized["net_fallback_task_1"]["text"].startswith("Freeze"))

spliced = N._reassemble_response_blocks([], pending, {"net_fallback_task_1"}, normalized)
check("recovered content replaces the fallback", spliced[0]["text"].startswith("Freeze"))
check("successful recovery is marked revised", spliced[0].get("revised") is True)

state = {
    "final_draft": pending[0]["text"],
    "ui_action_payload": {"uiType": "blocks", "responseBlocks": pending},
}
findings = N._deterministic_outbound_findings(state, check_citations=True)
check("unrevised evidence fallback cannot pass outbound checks", any(f["violation_type"] == "approved_evidence_omitted" for f in findings))

revised_state = {
    "final_draft": spliced[0]["text"],
    "ui_action_payload": {"uiType": "blocks", "responseBlocks": spliced},
}
revised_findings = N._deterministic_outbound_findings(revised_state, check_citations=True)
check("recovered answer clears the omission finding", not any(f["violation_type"] == "approved_evidence_omitted" for f in revised_findings))

ambiguous = pending + [{**pending[0], "blockId": "net_fallback_task_1_second"}]
check("ambiguous source-ID aliases are rejected", not N._normalize_reviser_output(generated, ambiguous))

print(f"{sum(checks)}/{len(checks)}")
