import sys, json
sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[3]))
from backend.app.orchestration import nodes as N
ITEMS = [{"item_id": "task_1", "kind": "faq"}, {"item_id": "task_9", "kind": "no-info"},
         {"item_id": "data_1", "kind": "cust", "_customer_data": True}, {"item_id": "task_2", "kind": "faq"}]
N._turn_consistency_items = lambda s: [dict(i) for i in ITEMS]
N.log_event = lambda *a, **k: None
def run(model_items):
    N.call_groq_lpu = lambda *a, **k: json.dumps({"items": model_items})
    return N._run_turn_consistency_check({"task_resolution_instructions": [{"task_id": "task_9"}], "unresolvable_search_tasks": []}).get("consistency_suppressed_ids", [])
def it(i, c=(), d="keep"): return {"item_id": i, "reason": "", "conflicts_with": list(c), "decision": d}
cases = [
 ("model drops no-info side", [it("task_9", ["data_1"], "drop"), it("data_1", ["task_9"])], ["task_9"]),
 ("model drops FAQ over no-info (its choice respected)", [it("task_1", ["task_9"], "drop"), it("task_9", ["task_1"])], ["task_1"]),
 ("drop customer data rejected", [it("data_1", ["task_9"], "drop"), it("task_9")], []),
 ("drop without conflict rejected", [it("task_2", [], "drop")], []),
 ("both sides dropped -> keep both", [it("task_1", ["task_2"], "drop"), it("task_2", ["task_1"], "drop")], []),
 ("conflict, no drop -> nothing", [it("task_1", ["task_2"]), it("task_2")], []),
 ("unknown id ignored", [it("task_77", ["task_1"], "drop")], []),
 ("case-insensitive DROP", [it("task_2", ["task_1"], " DROP ")], ["task_2"]),
 ("missing decision defaults keep", [{"item_id": "task_1", "conflicts_with": ["task_2"]}], []),
]
ok = 0
for label, m, exp in cases:
    got = run(m); ok += got == exp
    print(("PASS" if got == exp else "FAIL"), label, got)
print(f"{ok}/{len(cases)}")
