import sys, json
sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[3]))
import backend.app.orchestration.nodes as N
N.log_event = lambda *a, **k: None
ok = total = 0
def check(label, cond):
    global ok, total
    total += 1; ok += bool(cond); print(("PASS " if cond else "FAIL ") + label)
SQ = {"subquery_id": "subquery_1", "need": "reverting your transaction from this morning", "customer_component": "Revert my transaction from this morning.", "tool_attempts": []}
def plan_state(sq=SQ):
    return {**N._fresh_volatile_state_reset(), "user_input": "Is there a way to revert my transaction from this morning?", "chat_history": [], "user_profile": {"language": "English"},
            "financial_subqueries": [dict(sq)], "tool_planning_round": 0, "clarification_tasks": []}
def run_planner(reply, sq=SQ):
    N.call_groq_lpu = lambda *a, **k: json.dumps(reply)
    out = N.node_03a_knowledge_blueprint_planner(plan_state(sq))
    sqs = out.get("financial_subqueries", [])
    return [(a["tool_name"], a.get("rag_role")) for s in sqs for a in s.get("tool_attempts", [])], out
empty = {"subquery_domains": [{"subquery_id": "subquery_1", "domain_reasoning": "money movement", "domains": ["high_risk_transaction"]}], "tool_tasks": [], "clarifications": []}
tools, _ = run_planner(empty)
check(f"planner planned nothing -> FAQ search added ({tools})", tools == [("rag", "needed")])
clar = dict(empty, clarifications=[{"subquery_id": "subquery_1", "unresolved_request": "whether you want X or Y", "clarification_reason": "tie", "clarification_guesses": []}])
tools, out = run_planner(clar)
check(f"planner asked a tool-selection clarification -> no FAQ added ({tools})", tools == [])
prior = dict(SQ, tool_attempts=[{"attempt_id": "subquery_1::rag::r1::1", "subquery_id": "subquery_1", "tool_name": "rag", "status": "completed", "operation": "retrieve_faq_evidence", "result": {"retrieval_state": "CLARIFICATION_REQUIRED"}, "planning_round": 1}])
tools, _ = run_planner(empty, prior)
check(f"FAQ search already ran -> not added again ({[t for t in tools if t[0]=='rag' and t != ('rag', None)]})", sum(1 for t in tools if t[0] == "rag") == 1)
deep = dict(empty, tool_tasks=[{"subquery_id": "subquery_1", "tool_name": "deeplink", "operation": "resolve_navigation", "input": {"destination_id": "money-transfer"}, "rag_role": ""}])
tools, _ = run_planner(deep)
check(f"planner chose a tool -> nothing added ({tools})", tools == [("deeplink", None)])
# failsafe in node_04a
def res_state(sq, extra=None):
    st = {**N._fresh_volatile_state_reset(), "financial_subqueries": [dict(sq)], "tool_planning_round": 9, "tool_plan_status": "exhausted", "search_tasks": [], "task_results": [],
          "task_resolution_instructions": [], "clarification_tasks": [], "user_input": "x", "decline_tasks": []}
    st.update(extra or {}); return st
out = N.node_04a_resolution_planner(res_state(SQ))
ni = out.get("unresolvable_search_tasks", [])
check(f"need left with nothing -> no-info entry added ({[e.get('no_info_evidence') for e in ni]})", len(ni) == 1 and "reverting your transaction" in ni[0]["no_info_evidence"])
existing = {"unresolvable_search_tasks": [{"task_id": "task_9", "subquery_id": "subquery_1", "resolved_subquery": "x", "need": "y", "no_info_evidence": "I don't have information available on y."}]}
out = N.node_04a_resolution_planner(res_state(SQ, existing))
check("need already has a no-info entry -> nothing added", "unresolvable_search_tasks" not in out or len(out["unresolvable_search_tasks"]) == 1)
print(f"\n{ok}/{total}")
