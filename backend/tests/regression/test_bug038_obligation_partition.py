import sys, json, re
sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[3]))
from backend.app.orchestration import nodes as N
N.log_event = lambda *a, **k: None
ok = total = 0
def check(label, cond):
    global ok, total
    total += 1; ok += bool(cond); print(("PASS " if cond else "FAIL ") + label)
V = lambda tid: {"task_id": tid, "scope": "answer", "unsupported_content": ["x"]}
P = N._partition_audited_response

# --- Step 3: sorting flags into blocks ---
bug038 = [{"blockId": "element_1", "sourceTaskIds": [], "sourceShortcutHandles": ["nav_1"], "sourceUnmatchedIds": ["task_1", "task_1_scope"], "text": "7 to 10 days"}]
a, p, w = P(bug038, [], [V("task_1")])
check("BUG-038 shape: flag on task_1 finds the block via its non-obligation citation", a == [] and "element_1" in p and not w)
a, p, w = P(bug038, [], [V("nav_1")])
check("flag on a shortcut handle finds the block", a == [] and not w)
two = [{"blockId": "element_1", "sourceTaskIds": ["task_1"], "sourceShortcutHandles": []},
       {"blockId": "element_2", "sourceTaskIds": ["task_2"], "sourceShortcutHandles": []}]
a, p, w = P(two, [], [V("task_2")])
check("normal case unchanged: only the flagged block pending", [b["blockId"] for b in a] == ["element_1"] and p == {"element_2", "task_2"} and not w)
a, p, w = P(two, [], [V("task_9")])
check("flag matching no block -> response-wide, nothing approved", a == [] and w and "task_9" in p)
a, p, w = P([], [], [V("task_1")])
check("no blocks at all (BUG-009) -> nothing approved, flagged id pending", a == [] and p == {"task_1"})
a, p, w = P(two, [], [V("")])
check("empty task_id -> response-wide (unchanged)", a == [] and w)
a, p, w = P(two, [], [V("element_2")])
check("deterministic finding keyed by blockId finds its block", [b["blockId"] for b in a] == ["element_1"] and not w)
a, p, w = P(two, [], [])
check("no violations -> all approved", len(a) == 2 and not p and not w)

# --- Step 2: citation check ---
def det(blocks, check_citations=True):
    st = {"ui_action_payload": {"responseBlocks": blocks}, "final_draft": ""}
    return [(f["task_id"], f["violation_type"], f["evidence_gap"]) for f in N._deterministic_outbound_findings(st, check_citations=check_citations)]
f = det(bug038)
check("non-obligation citation flagged on the block, naming the ids", f and f[0][0] == "element_1" and f[0][1] == "non_obligation_citation" and "task_1, task_1_scope" in f[0][2])
check("not flagged once the block is revised", det([{**bug038[0], "revised": True}]) == [])
check("not flagged when check_citations is off (other audit scopes)", det(bug038, False) == [])
f = det([{"blockId": "clarification", "sourceTaskIds": [], "type": "clarification", "text": "Anything else?"}])
check("block citing no obligation flagged (the `clarification` field block)", f and f[0][1] == "no_obligation")
check("net-inserted block not checked", det([{"blockId": "net_fallback_task_1", "sourceTaskIds": [], "sourceShortcutHandles": [], "text": "x"}]) == [])
check("well-cited block not flagged", det([{"blockId": "element_1", "sourceTaskIds": ["task_1"], "sourceShortcutHandles": [], "sourceUnmatchedIds": [], "text": "x"}]) == [])

# --- Reassembly marks revised ---
out = N._reassemble_response_blocks([], two, {"element_2"}, {"element_2": {"text": "new", "sourceTaskIds": ["hack"]}})
check("revised block marked, citations stay locked", out[1].get("revised") is True and out[1]["sourceTaskIds"] == ["task_2"] and "revised" not in out[0])

# --- Generator records obligations + non-obligation citations ---
RAG = {"tool_name": "rag", "status": "completed", "result": {"retrieval_state": "SUFFICIENT"}}
state = {**N._fresh_volatile_state_reset(), "user_input": "q", "chat_history": [], "user_profile": {"language": "English"},
  "search_tasks": [{"task_id": "task_1", "subquery_id": "subquery_1", "customer_component": "When will my card arrive?"},
                   {"task_id": "task_2", "subquery_id": "subquery_2", "customer_component": "Closure fee?"}],
  "task_results": [{"task_id": "task_1", "status": "SUFFICIENT", "approved_parent_ids": ["faq_9d7a4b83"]},
                   {"task_id": "task_2", "status": "RELEVANT", "approved_parent_ids": ["faq_5ad80c63"]}],
  "financial_subqueries": [{"subquery_id": "subquery_1", "need": "card arrival", "customer_component": "When will my card arrive?", "tool_attempts": [RAG]},
                           {"subquery_id": "subquery_2", "need": "closure fee", "customer_component": "Closure fee?", "tool_attempts": [RAG]}],
  "task_resolution_instructions": [], "clarification_tasks": [], "preflight_status": "presentation_ready"}
prompts = []
def fake(prompt, *a, **k):
    prompts.append(prompt)
    if "FLEXIBLE RESPONSE REVISION" in prompt[:300]:
        return json.dumps({"blocks": []})
    return json.dumps({"elements": [
        {"type": "TEXT", "source_task_ids": ["task_1", "bogus_1"], "answer": "Cards arrive soon."},
        {"type": "TEXT", "source_task_ids": ["task_2"], "answer": "Closure info."}], "clarification": "", "next_actions": []})
N.call_groq_lpu = fake
gen = N._generate_response_blocks(state)
blocks = gen["ui_action_payload"]["responseBlocks"]
b1 = next(b for b in blocks if b["blockId"] == "element_1")
check("generator keeps the non-obligation citation on the block", b1.get("sourceUnmatchedIds") == ["bogus_1"] and b1["sourceTaskIds"] == ["task_1"])
check("generator records every obligation it received", {"task_1", "task_2"} <= set(gen.get("generation_obligations", {})))

# --- Reviser sees exactly the pending block's obligations ---
st2 = {**state, **gen, "approved_response_blocks": [b for b in blocks if b["blockId"] != "element_1"],
       "pending_task_ids": ["element_1", "task_1", "bogus_1"], "response_wide_violation_this_round": False,
       "task_violation_history": {"element_1": [{"task_id": "element_1", "axis": "deterministic", "unsupported_content": ["Cards arrive soon."]}]},
       "safety_retry_count": 1}
prompts.clear(); N.node_04c_response_reviser(st2)
r = prompts[0]
check("reviser gets task_1 (cited by the pending block)", '"id": "task_1"' in r)
check("reviser does NOT get task_2 (only cited by an approved block)", '"id": "task_2"' not in r)
check("reviser gets the deterministic finding for the block", "not obligations given for this response" in r or "Cards arrive soon." in r)
prompts.clear(); N.node_04c_response_reviser({**st2, "response_wide_violation_this_round": True})
check("response-wide round: reviser gets every recorded obligation", '"id": "task_1"' in prompts[0] and '"id": "task_2"' in prompts[0])
print(f"\n{ok}/{total}")
