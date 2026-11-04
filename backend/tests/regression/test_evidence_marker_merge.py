import sys, json, re
sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[3]))
from backend.app.orchestration import nodes as N
N.log_event = lambda *a, **k: None
captured = []
def fake_llm(prompt, *a, **k):
    captured.append(prompt)
    if "GROUNDEDNESS" in prompt[:300]:
        return json.dumps({"conduct_compliant": True, "answer_compliant": True, "clarification_compliant": True, "conduct_violations": [], "evidence_grounded": True, "answer_evidence_grounded": True, "answer_bridge_grounded": True, "clarification_grounded": True, "clarification_connected": True, "grounding_violations": [], "evidence_violations": []})
    return json.dumps({"elements": [{"type": "TEXT", "source_task_ids": ["task_1"], "answer": "Cards arrive in {{faq_9d7a4b83_001}}-{{faq_9d7a4b83_002}} days."}], "clarification": "", "next_actions": []})
N.call_groq_lpu = fake_llm
RAG_OK = {"tool_name": "rag", "status": "completed", "result": {"retrieval_state": "RELEVANT"}}
def base(**kw):
    st = {**N._fresh_volatile_state_reset(), "user_input": "When will my physical card arrive?", "chat_history": [],
          "user_profile": {"language": "English"},
          "search_tasks": [{"task_id": "task_1", "subquery_id": "subquery_1", "customer_component": "When will my physical card arrive?"}],
          "task_results": [{"task_id": "task_1", "status": "RELEVANT", "approved_parent_ids": ["faq_9d7a4b83"], "generation_guidance": ""}],
          "financial_subqueries": [{"subquery_id": "subquery_1", "need": "when your physical card will arrive", "customer_component": "When will my physical card arrive?", "tool_attempts": [RAG_OK]}],
          "task_resolution_instructions": [N._terminal_retrieval_feedback("task_1", "When will my physical card arrive?", "RELEVANT", "when your physical card will arrive")],
          "clarification_tasks": [], "preflight_status": "presentation_ready"}
    st.update(kw); return st
ok = 0; total = 0
def check(label, cond):
    global ok, total
    total += 1; ok += bool(cond); print(("PASS " if cond else "FAIL ") + label)

# 1. RELEVANT task: one evidence obligation with status + partial guidance, no markers section
captured.clear(); N._generate_response_blocks(base())
g = captured[0]
m = re.search(r'\{"id": "task_1".*?"generation_rule": "EVIDENCE RULES"\}', g)
check("generator: task_1 evidence obligation carries resolution_status RELEVANT", m and '"resolution_status": "RELEVANT"' in m.group(0))
check("generator: task_1 guidance is compose_partial", m and "no other entry in this reply answers" in m.group(0))
check("generator: no markers section", "Terminal unresolved task markers" not in g)
check("generator: EVIDENCE RULES has RELEVANT paragraph", "RELEVANT: they connect to the request" in g)
check("generator: clarification field rule says leave empty", "Leave `clarification` an empty string" in g)

# 2. SUFFICIENT task: compose guidance
captured.clear(); N._generate_response_blocks(base(task_results=[{"task_id": "task_1", "status": "SUFFICIENT", "approved_parent_ids": ["faq_9d7a4b83"]}], task_resolution_instructions=[]))
m = re.search(r'\{"id": "task_1".*?"generation_rule": "EVIDENCE RULES"\}', captured[0])
check("generator: SUFFICIENT entry has status SUFFICIENT + compose guidance", m and '"resolution_status": "SUFFICIENT"' in m.group(0) and "Compose a natural answer from this." in m.group(0))

# 3. BUG-038 shape: RELEVANT evidence removed by api guard -> nothing about task_1 reaches the generator
API = {"tool_name": "api", "status": "completed", "result": {"disclosure_mode": "placeholder", "placeholder_token": "{{data-11112222}}", "field_id": "account-balance", "field_description": "Account balance"}}
st = base(financial_subqueries=[{"subquery_id": "subquery_1", "need": "your balance", "customer_component": "When will my physical card arrive?", "tool_attempts": [RAG_OK, API]}])
captured.clear(); N._generate_response_blocks(st)
g = captured[0]
check("guard-removed RELEVANT task: no task_1 anywhere in generator prompt data", '"task_1"' not in g.split("GENERAL DESIGN PRINCIPLES")[0])

# 4. CLARIFICATION_REQUIRED still becomes a no-info obligation
st = base(task_results=[{"task_id": "task_1", "status": "CLARIFICATION_REQUIRED", "approved_parent_ids": []}],
          task_resolution_instructions=[N._terminal_retrieval_feedback("task_1", "When will my physical card arrive?", "CLARIFICATION_REQUIRED", "when your physical card will arrive")])
captured.clear(); N._generate_response_blocks(st)
g = captured[0]
check("no-info entry still rendered for CLARIFICATION_REQUIRED", "I don't have information available on when your physical card will arrive." in g)

# 5. audit context: approved_by_task carries status; no terminal markers; RELEVANT rule in core rules
ctx = N._normal_outbound_grounding_context(base())
check("audit: approved_by_task entry has resolution_status RELEVANT", ctx["approved_by_task"][0].get("resolution_status") == "RELEVANT")
check("audit: no terminal_clarifications key", "terminal_clarifications" not in ctx)
check("audit: no generation_guidance leaked into approved_by_task", "generation_guidance" not in ctx["approved_by_task"][0])
out = N._generate_response_blocks(base())
st = {**base(), **out}
captured.clear(); N.node_06_outbound_combined_audit_gate(st)
a = captured[0] if captured else ""
check("audit prompt: has RELEVANT availability rule in core rules", 'For a\n          RELEVANT entry, a neutral statement' in a or "For a RELEVANT entry" in re.sub(r"\s+"," ",a))
check("audit prompt: no Terminal Unresolved Task Markers", "Terminal Unresolved Task Marker" not in a)

# 6. reviser: RELEVANT entry reaches reviser with status, no markers section
st = {**st, "approved_response_blocks": [], "pending_task_ids": ["task_1"], "response_wide_violation_this_round": False,
      "task_violation_history": {"task_1": [{"task_id": "task_1", "axis": "grounding", "unsupported_content": ["x"]}]}, "safety_retry_count": 1}
captured.clear(); N.node_04c_response_reviser(st)
r = captured[0] if captured else ""
check("reviser: task_1 evidence with resolution_status RELEVANT", '"resolution_status": "RELEVANT"' in r)
check("reviser: no markers section", "Terminal unresolved task markers" not in r)
print(f"\n{ok}/{total}")

# RESPONSE LENGTH rule (option C)
captured.clear(); N._generate_response_blocks(base()); g = captured[0]
check("generator: RESPONSE LENGTH principle present", "RESPONSE LENGTH: make the reply proportionate" in g)
check("generator: RELEVANT guidance limits related context", "anything it offers only as related context gets its most" in g)
captured.clear(); N.node_04c_response_reviser(st); r = captured[0]
check("reviser: RESPONSE LENGTH principle present", "RESPONSE LENGTH: make the reply proportionate" in r)
print(f"{ok}/{total}")
