import sys, json
sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[3]))
import backend.app.orchestration.nodes as N
N.log_event = lambda *a, **k: None
N.call_groq_lpu = lambda *a, **k: json.dumps({"task_results": [{"task_id": "task_1", "reason": "No parent covers closing an account.", "status": "IRRELEVANT",
    "approved_parent_ids": [], "generation_guidance": "", "evidence_delta": [], "verification_results": [], "scope_expansion_lead_reason": "",
    "scope_expansion_lead_parent_id": "", "unresolved_term": "", "clarification_reason": "", "clarification_guesses": []}]})
round1_q = {"unresolved_term": "which account type you want to close", "clarification_reason": "The customer said 'this account' without naming which one.",
            "clarification_guesses": [{"label": "Savings account", "message": "I want to close my savings account.", "reason": "Deposit_Account is the base account product."}]}
def state(retained):
    return {**N._fresh_volatile_state_reset(), "user_input": "Delete this account!", "chat_history": [], "user_profile": {"language": "English"},
        "search_tasks": [{"task_id": "task_1", "subquery_id": "subquery_1", "customer_component": "Delete this account!", "search_territory": "in_scope"}],
        "financial_subqueries": [{"subquery_id": "subquery_1", "need": "deleting your account", "customer_component": "Delete this account!", "tool_attempts": [{"tool_name": "rag", "status": "running"}]}],
        "task_results": [{"task_id": "task_1", "status": "RETRIEVED", "parents": [{"parent_id": "faq_e810ae79", "content": "Question: How do I reactivate a dormant account? Answer: ..."}]}],
        "task_retry_counts": {"task_1": 9}, "task_partial_clarification": ({"task_1": round1_q} if retained else {}), "clarification_tasks": []}
ok = 0
for retained in (True, False):
    out = N.node_03c_sufficiency_auditor(state(retained))
    cts = out.get("clarification_tasks", [])
    q = [c.get("unresolved_request") or c.get("unresolved_term") for c in cts]
    exp = retained
    good = bool(cts) == exp and (not exp or "account type" in json.dumps(cts))
    ok += good
    print(("PASS " if good else "FAIL ") + f"round-1 question retained={retained}: clarification tasks -> {q}")
print(f"{ok}/2")
