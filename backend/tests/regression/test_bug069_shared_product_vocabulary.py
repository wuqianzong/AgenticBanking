"""BUG-069 decision: canonical product names are approved customer vocabulary.

The intent formulator and outbound auditor must receive one shared equivalence
map, so `Deposit_Account` and `savings account` are judged as the same product.
No live model call is made.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from backend.app.orchestration import nodes as N


passed = 0
total = 0


def check(name, condition):
    global passed, total
    total += 1
    if condition:
        passed += 1
        print(f"PASS: {name}")
    else:
        print(f"FAIL: {name}")


check(
    "every formulator matrix product has one shared vocabulary entry",
    set(N.PRODUCT_NAME_EQUIVALENCES) == set(N.PRODUCT_INTENT_MATRIX),
)
check(
    "deposit account and savings account are equivalent",
    "savings account" in N.PRODUCT_NAME_EQUIVALENCES["Deposit_Account"],
)
check(
    "intent-formulator briefing receives the shared contract",
    N._PRODUCT_VOCABULARY_CONTRACT in N.HORIZON_PRODUCT_BRIEFING,
)

captured = {}
original_call = N.call_groq_lpu


def fake_call(prompt, model_id=None, **kwargs):
    captured["prompt"] = prompt
    return json.dumps({
        "conduct_reason": "all checks: none",
        "conduct_compliant": True,
        "conduct_violations": [],
        "answer_compliant": True,
        "clarification_compliant": True,
        "rewrite_instruction": "",
        "evidence_reason": "Conversational response contains no factual claim.",
        "evidence_grounded": True,
        "evidence_violations": [],
        "answer_bridge_grounded": True,
        "clarification_grounded": True,
        "clarification_connected": True,
    })


try:
    N.call_groq_lpu = fake_call
    N.node_06_outbound_combined_audit_gate({
        "user_input": "Thanks",
        "final_answer": "You're welcome.",
        "final_clarification": "",
        "ui_action_payload": {"uiType": "blocks", "responseBlocks": []},
        "financial_subqueries": [],
        "clarification_tasks": [],
        "decline_tasks": [],
        "conversational_tasks": [{"subquery_id": "subquery_1", "customer_component": "Thanks"}],
    })
finally:
    N.call_groq_lpu = original_call

audit_prompt = captured.get("prompt", "")
check(
    "outbound auditor receives the identical shared contract",
    N._PRODUCT_VOCABULARY_CONTRACT in audit_prompt,
)
check(
    "auditor is told canonical and familiar names are equally permitted",
    "standardized product key instead of one of its equivalent names" in audit_prompt,
)

print(f"{passed}/{total} checks passed")
raise SystemExit(0 if passed == total else 1)
