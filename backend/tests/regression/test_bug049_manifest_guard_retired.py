import sys; sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[3]))
from backend.app.orchestration.nodes import _response_evidence_manifest
def state(**kw):
    s={
     "search_tasks":[{"task_id":"task_2","subquery_id":"subquery_2","search_territory":"in_scope"},
                     {"task_id":"task_2_scope","subquery_id":"subquery_2","search_territory":"adjacent_scope"},
                     {"task_id":"task_1","subquery_id":"subquery_1"}],
     "task_results":[{"task_id":"task_2","status":"RELEVANT","approved_parent_ids":["faq_addr_change"]},
                     {"task_id":"task_2_scope","status":"RELEVANT","approved_parent_ids":["faq_x"]},
                     {"task_id":"task_1","status":"SUFFICIENT","approved_parent_ids":["faq_card"]}],
     "financial_subqueries":[{"subquery_id":"subquery_2","tool_attempts":[
         {"tool_name":"api","status":"completed"},{"tool_name":"service_request","status":"completed"},{"tool_name":"rag","status":"completed"}]},
         {"subquery_id":"subquery_1","tool_attempts":[{"tool_name":"rag","status":"completed"}]}],
    }
    s.update(kw); return s
ok=0;tot=0
def check(name,cond):
    global ok,tot; tot+=1; ok+=bool(cond); print(("PASS " if cond else "FAIL ")+name)
m={e["task_id"]:e for e in _response_evidence_manifest(state())}
check("BUG-049: RELEVANT task on a data-fetched need is kept", "task_2" in m and m["task_2"]["resolution_status"]=="RELEVANT")
check("BUG-049: its _scope companion is kept", "task_2_scope" in m)
check("unrelated SUFFICIENT task unaffected", "task_1" in m)
m=_response_evidence_manifest(state(consistency_suppressed_ids=["task_2_scope"]))
check("consistency-check suppression still honoured", [e["task_id"] for e in m]==["task_2","task_1"] or set(e["task_id"] for e in m)=={"task_2","task_1"})
m=_response_evidence_manifest(state(task_results=[{"task_id":"task_2","status":"INSUFFICIENT","approved_parent_ids":[]}]))
check("non-answerable task still excluded", m==[])
print(f"{ok}/{tot}")
