import sys, copy
sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[3]))
import backend.app.orchestration.nodes as N
N.log_event = lambda *a, **k: None

checks = []


def check(name, good):
    checks.append(good)
    print(("PASS " if good else "FAIL ") + name)


# The shortcut list is normally built from completed tool attempts; here it
# is fixed so the net is tested on its own.
NAV = ({"kind": "navigation", "label": "Manage Card", "topic": "", "for_request": "I want to manage my card."}, ("deeplink", "r1"), "subquery_1")
N._iter_shortcut_entries = lambda state: iter(state["_shortcuts"])
state = {"_shortcuts": [NAV], "financial_subqueries": [{"subquery_id": "subquery_1"}, {"subquery_id": "subquery_2"}],
         "search_tasks": [{"task_id": "task_1", "subquery_id": "subquery_1"}, {"task_id": "task_2", "subquery_id": "subquery_2"}]}
NO_INFO = "I do not have information available to answer that request."
SHORTCUT = 'You can use the "Manage Card" button below.'


def block(block_id, text, task_ids=(), handles=()):
    return {"blockId": block_id, "type": "text", "text": text, "sourceTaskIds": list(task_ids), "sourceShortcutHandles": list(handles)}


def run(blocks, draft):
    return N._resolve_no_info_shortcut_conflicts(copy.deepcopy(state), copy.deepcopy(blocks), draft)


# The traced turn (28 Oct): the reviser turned the shortcut sentence into
# "no information" and the omission net added the shortcut sentence back.
out, draft = run([block("element_nav_1", NO_INFO, handles=["nav_1"]), block("net_fallback_nav_1", SHORTCUT, handles=["nav_1"])], NO_INFO + "\n\n" + SHORTCUT)
check("no-info on the shortcut's own subquery is removed when another block explains the button", [b["text"] for b in out] == [SHORTCUT] and draft == SHORTCUT)
out, draft = run([block("b1", NO_INFO, task_ids=["task_1"])], NO_INFO)
check("no-info alone on that subquery becomes the shortcut sentence", out[0]["text"] == SHORTCUT and out[0]["sourceShortcutHandles"] == ["nav_1"] and draft == SHORTCUT)
out, _ = run([block("b1", NO_INFO, task_ids=["no_info_subquery_1"])], NO_INFO)
check("the failsafe no_info_<subquery> id maps to its subquery", out[0]["text"] == SHORTCUT)

LOANS = "I don't have information available on loans."
out, draft = run([block("b1", LOANS, task_ids=["task_2"]), block("b2", SHORTCUT, handles=["nav_1"])], LOANS + "\n\n" + SHORTCUT)
check("no-info on a different subquery is a legitimate limit and is kept", [b["text"] for b in out] == [LOANS, SHORTCUT] and LOANS in draft)
out, _ = run([block("b1", NO_INFO, task_ids=["task_1", "task_2"])], NO_INFO)
check("no-info citing a subquery without a shortcut is kept", out[0]["text"] == NO_INFO)
out, _ = run([block("b1", NO_INFO, task_ids=["unknown_task"])], NO_INFO)
check("no-info with an unknown subquery is kept", out[0]["text"] == NO_INFO)
MIXED = "I don't have information on fees. Replacement cards arrive in 5 days."
out, _ = run([block("b1", MIXED, task_ids=["task_1"])], MIXED)
check("a block mixing no-info with real content is kept", out[0]["text"] == MIXED)
clarify = {**block("b1", NO_INFO, task_ids=["task_1"]), "type": "clarification"}
out, _ = run([clarify], NO_INFO)
check("a clarification block is never touched", out[0]["text"] == NO_INFO)
out, _ = N._resolve_no_info_shortcut_conflicts({**copy.deepcopy(state), "_shortcuts": []}, [block("b1", NO_INFO, task_ids=["task_1"])], NO_INFO)
check("no shortcut this turn: no-info is kept", out[0]["text"] == NO_INFO)
print(f"{sum(checks)}/{len(checks)}")
