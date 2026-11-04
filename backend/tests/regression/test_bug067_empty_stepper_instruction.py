import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from backend.app.orchestration import nodes as N


checks = []


def check(label, condition):
    checks.append(bool(condition))
    print("PASS" if condition else "FAIL", label)


placeholder_values = {
    "faq_steps_001": "1",
    "faq_steps_002": "2",
    "faq_steps_003": "3",
    "faq_steps_004": "4",
    "faq_wait_001": "24",
    "faq_phone_001": "+65 3138 7878",
}
original_fetch = N.fetch_placeholders_from_db
N.fetch_placeholders_from_db = lambda ids: {
    placeholder_id: placeholder_values[placeholder_id]
    for placeholder_id in ids
    if placeholder_id in placeholder_values
}


def findings_for(block):
    state = {
        "final_draft": N._flatten_response_block_text(block),
        "ui_action_payload": {"uiType": "blocks", "responseBlocks": [block]},
    }
    return N._deterministic_outbound_findings(state, check_citations=True)


def has_empty_step_finding(findings):
    return any(item.get("violation_type") == "empty_stepper_instruction" for item in findings)


bad = {
    "blockId": "element_1",
    "sourceTaskIds": ["task_1"],
    "sourceShortcutHandles": [],
    "type": "stepper",
    "contextText": "Follow these steps:",
    "items": [
        {
            "number": position,
            "label": f"Step {position}",
            "detail": f"{{{{faq_steps_00{position}}}}}",
            "status": "current" if position == 1 else "upcoming",
        }
        for position in range(1, 5)
    ],
    "extendedText": "",
}
bad_findings = findings_for(bad)
check("ordinal-only progress steps are rejected", has_empty_step_finding(bad_findings))
check(
    "all empty steps are reported in one block finding",
    len(next(item for item in bad_findings if item.get("violation_type") == "empty_stepper_instruction")["unsupported_content"]) == 4,
)
check(
    "finding stays attributed to the block's task for one scoped revision",
    next(item for item in bad_findings if item.get("violation_type") == "empty_stepper_instruction")["task_id"] == "task_1",
)

# The audit join passes LLM and deterministic findings to one partition call.
# Verify BUG-067 joins another finding for the same task and still produces a
# single pending block set while an unrelated block is locked as approved.
unrelated = {
    "blockId": "element_2",
    "sourceTaskIds": ["task_2"],
    "sourceShortcutHandles": [],
    "type": "text",
    "contextText": "Independent approved answer.",
    "text": "",
}
grounding_finding = {
    "task_id": "task_1",
    "violation_type": "unsupported_claim",
    "unsupported_content": ["another issue in the same block"],
}
approved, pending_ids, response_wide = N._partition_audited_response(
    [bad, unrelated], [], [grounding_finding, *bad_findings]
)
check(
    "LLM and BUG-067 findings produce one combined pending block set",
    pending_ids == {"element_1", "task_1"} and response_wide is False,
)
check("unrelated blocks lock in the same partition", [block["blockId"] for block in approved] == ["element_2"])

substantive_details = {
    **bad,
    "items": [
        {**item, "detail": detail}
        for item, detail in zip(
            bad["items"],
            [
                "Open the Horizon App and log in.",
                "Tap View Card.",
                "Choose Add to Google Pay.",
                "Follow the displayed instructions.",
            ],
        )
    ],
}
check("ordinary substantive instructions pass", not has_empty_step_finding(findings_for(substantive_details)))

numeric_with_action = {
    **bad,
    "items": [{
        "number": 1,
        "label": "Wait before retrying",
        "detail": "Wait {{faq_wait_001}} hours before trying again.",
        "status": "current",
    }],
}
check("a number inside a real instruction passes", not has_empty_step_finding(findings_for(numeric_with_action)))

substantive_title = {
    **bad,
    "items": [{
        "number": 1,
        "label": "Call support",
        "detail": "{{faq_phone_001}}",
        "status": "current",
    }],
}
check("a substantive title permits a numeric-only detail", not has_empty_step_finding(findings_for(substantive_title)))

mixed = {
    **substantive_details,
    "items": [
        substantive_details["items"][0],
        bad["items"][1],
        substantive_details["items"][2],
        substantive_details["items"][3],
    ],
}
check("one empty step rejects the whole incomplete stepper", has_empty_step_finding(findings_for(mixed)))

unknown_placeholder = {
    **bad,
    "items": [{
        "number": 1,
        "label": "Step 1",
        "detail": "{{faq_unknown_001}}",
        "status": "current",
    }],
}
unknown_findings = findings_for(unknown_placeholder)
check(
    "unknown tokens remain the existing invalid-placeholder finding",
    any(item.get("violation_type") == "invalid_placeholder" for item in unknown_findings)
    and not has_empty_step_finding(unknown_findings),
)

N.fetch_placeholders_from_db = original_fetch
print(f"{sum(checks)}/{len(checks)} checks passed")
