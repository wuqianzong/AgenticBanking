import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from backend.app.orchestration import nodes as N


checks = []


def check(label, condition):
    checks.append(bool(condition))
    print("PASS" if condition else "FAIL", label)


pending = [{
    "blockId": "clarification",
    "sourceTaskIds": [],
    "sourceShortcutHandles": [],
    "type": "clarification",
    "contextText": None,
    "text": "Would you like information about a different topic?",
    "extendedText": None,
}]
generated = {
    "response_blocks": [{
        "block_id": "clarification",
        "type": "clarification",
        "contextText": None,
        "text": "",
        "extendedText": None,
        "revision_status": "pending",
    }]
}

normalized = N._normalize_reviser_output(generated, pending)
check("exact pending entry preserves an explicit empty edit", normalized == {"clarification": {"text": ""}})

spliced = N._reassemble_response_blocks([], pending, {"clarification"}, normalized)
check("explicit empty edit clears the rejected text", spliced[0]["text"] == "")
check("explicit deletion is marked revised", spliced[0].get("revised") is True)
check("cleared clarification renders no customer text", N._flatten_response_block_text(spliced[0]) == "")

# An alias remains useful for BUG-065 content recovery, but must not acquire
# deletion power: only the exact transport block identity can clear content.
aliased_pending = [{
    **pending[0],
    "blockId": "net_fallback_task_1",
    "sourceTaskIds": ["task_1"],
    "type": "text",
}]
aliased_empty = {
    "response_blocks": [{
        "block_id": "task_1",
        "type": "text",
        "content": "",
        "revision_status": "pending",
    }]
}
check("source-task alias cannot erase a block with empty content", not N._normalize_reviser_output(aliased_empty, aliased_pending))

# Reproduce the writer's dual representation. This is the exact predicate
# used before the completeness net: equal apart from whitespace and case.
question = "Would you like to know how to view your existing virtual card details in the app?"
duplicate_block = {
    "blockId": "element_2",
    "sourceTaskIds": ["subquery_1"],
    "sourceShortcutHandles": [],
    "type": "text",
    "contextText": question,
    "text": "",
}
kept = N._drop_duplicate_terminal_clarification_elements(
    [duplicate_block, {**duplicate_block, "blockId": "element_3", "contextText": "Different content."}],
    "  WOULD YOU LIKE TO KNOW HOW TO VIEW YOUR EXISTING VIRTUAL CARD DETAILS IN THE APP?  ",
)
check("duplicate ordinary element is removed before completeness checking", [b["blockId"] for b in kept] == ["element_3"])

print(f"{sum(checks)}/{len(checks)} checks passed")
