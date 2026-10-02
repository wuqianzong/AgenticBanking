import sys, json
sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[3]))
import backend.app.orchestration.nodes as N
N.log_event = lambda *a, **k: None

checks = []


def check(name, good):
    checks.append(good)
    print(("PASS " if good else "FAIL ") + name)


SEND = {"kind": "navigation", "label": "Send Money", "topic": "transfer money", "for_request": "Please transfer $260.", "handle": "nav_1"}
FORM = {"kind": "form", "label": "Replace Card", "topic": "replace card", "for_request": "I need a new card.", "handle": "svc_2"}
state = {**N._fresh_volatile_state_reset(), "user_input": "Please transfer $260.", "chat_history": [], "user_profile": {"language": "English"}}


def run(reply, shortcuts, conversational=None):
    seen = {}
    def fake(prompt, *a, **k):
        seen["prompt"] = prompt
        if isinstance(reply, Exception):
            raise reply
        return json.dumps(reply)
    N.call_groq_lpu = fake
    out = N._generate_evidence_only_response([], [], [], conversational or [], state, shortcuts)
    return out, seen.get("prompt", "")


out, prompt = run({"elements": [{"source_task_ids": ["nav_1"], "text": "Sure, you can send it using the \"Send Money\" button below."}]}, [SEND])
blocks = out["ui_action_payload"]["responseBlocks"]
check("tool-only turn: the sub-prompt sees the shortcut label and kind", '"label": "Send Money"' in prompt and '"kind": "shortcut"' in prompt)
check("tool-only turn: the sentence cites the handle as a shortcut (card anchors to it)", blocks[0]["sourceShortcutHandles"] == ["nav_1"] and blocks[0]["sourceTaskIds"] == [])
check("tool-only turn: the model's sentence is used", "Send Money" in out["final_draft"] and "also" not in out["final_draft"])

out, _ = run(ValueError("bad json"), [SEND, FORM])
check("unusable model reply: fixed sentences without 'also'", out["final_draft"] == 'You can use the "Send Money" button below.\n\nYou can fill in and submit the "Replace Card" form below.')

conv = [{"subquery_id": "subquery_2", "customer_component": "Thanks!"}]
out, _ = run({"elements": [{"source_task_ids": ["subquery_2"], "text": "You're welcome!"}]}, [SEND], conv)
check("shortcut omitted by the model: its fixed sentence is added", 'You can use the "Send Money" button below.' in out["final_draft"] and "You're welcome!" in out["final_draft"])
out, _ = run({"elements": [{"source_task_ids": ["subquery_2"], "text": "You're welcome! The Send Money button is below."}]}, [SEND], conv)
check("shortcut blended into another element: no duplicate sentence", out["final_draft"].count("Send Money") == 1)

check("routing: a shortcut obligation counts as evidence-only", N._GENERATION_RULE_BY_TYPE["shortcut"] in N._EVIDENCE_ONLY_GENERATION_RULES)
check("routing: FAQ evidence and personal data still use the full generator",
      N._GENERATION_RULE_BY_TYPE["evidence"] not in N._EVIDENCE_ONLY_GENERATION_RULES and N._GENERATION_RULE_BY_TYPE["personal_data"] not in N._EVIDENCE_ONLY_GENERATION_RULES)
print(f"{sum(checks)}/{len(checks)}")
