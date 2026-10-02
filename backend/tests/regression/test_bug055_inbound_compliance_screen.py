import sys, json
sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[3]))
import backend.app.orchestration.nodes as N
N.log_event = lambda *a, **k: None
from backend.app.orchestration.graph import app_graph

base = {**N._fresh_volatile_state_reset(), "user_input": "Someone from Horizon called and asked for my PIN. Can I give it?",
        "chat_history": [], "user_profile": {"language": "English"}}
checks = []


def check(name, good):
    checks.append(good)
    print(("PASS " if good else "FAIL ") + name)


def screen(reply):
    seen = {}
    def fake(prompt, *a, **k):
        seen["prompt"] = prompt
        if isinstance(reply, Exception):
            raise reply
        return json.dumps(reply)
    N.call_groq_lpu = fake
    return N.node_02f_inbound_compliance_screen(dict(base)), seen.get("prompt", "")


out, prompt = screen({"predicted_bank_response": "No -- never share your PIN; Horizon will never ask for it.", "non_compliant": False})
check("compliant prediction passes", out == {"inbound_compliance": {"non_compliant": False, "predicted_bank_response": "No -- never share your PIN; Horizon will never ask for it."}})
check("screen writes only inbound_compliance (triage writes the rest in the same step)", set(out) == {"inbound_compliance"})
check("prompt carries both shared modules and the latest message",
      N.REGULATORY_LICENSING_CONTEXT.strip()[:40] in prompt and N.BANK_CONDUCT_CONTEXT.strip()[:40] in prompt and base["user_input"] in prompt)
check("prompt is routed to the InboundComplianceScreen schema", "INBOUND COMPLIANCE SCREEN" in prompt and "WHOLE-TURN FINANCIAL TRIAGE" not in prompt)

out, _ = screen({"predicted_bank_response": "PROHIBITED", "non_compliant": False})
check("a PROHIBITED prediction blocks even if the flag disagrees", out["inbound_compliance"]["non_compliant"] is True)
out, _ = screen({"predicted_bank_response": "PROHIBITED", "non_compliant": True})
check("PROHIBITED with the flag set blocks", out["inbound_compliance"]["non_compliant"] is True)
out, _ = screen(ValueError("unparseable"))
check("an unreadable verdict fails closed", out["inbound_compliance"]["non_compliant"] is True)

join = N.node_02g_inbound_compliance_join
check("join blocks a non-compliant verdict",
      join({**base, "inbound_compliance": {"non_compliant": True}, "triage_status": "DECOMPOSED"}) == {"preflight_status": "non_compliant", "triage_status": "NON_COMPLIANT"})
check("join leaves a compliant turn to triage (non-empty update)", join({**base, "inbound_compliance": {"non_compliant": False}, "triage_status": "ESCALATION"}) == {"triage_status": "ESCALATION"})

import asyncio
def stream_updates(screen_reply):
    def fake(prompt, *a, **k):
        if "INBOUND COMPLIANCE SCREEN" in prompt:
            return json.dumps(screen_reply)
        if "WHOLE-TURN FINANCIAL TRIAGE" in prompt:
            return json.dumps({"triage_status": "ESCALATION", "components": [], "repetitive_issue_detected": False})
        if prompt == base["user_input"]:
            return "0.0001"  # prompt guard score: safe
        raise RuntimeError("unexpected call")
    N.call_groq_lpu = fake
    updates = []
    async def run():
        async for chunk in app_graph.astream({**base, "preflight_status": "pass"}, config={"configurable": {"thread_id": "t055"}}):
            updates.extend(chunk.items())
    try:
        asyncio.run(run())
    except Exception:
        pass
    return updates
ups = stream_updates({"predicted_bank_response": "No -- never share your PIN.", "non_compliant": False})
check("streamed join update on a passed turn is a dict (gateway can read it)", any(n == "n2g" and isinstance(u, dict) for n, u in ups) and not any(n == "n2g" and u is None for n, u in ups))

seen = {}
def fake_triage(prompt, *a, **k):
    seen["prompt"] = prompt
    return json.dumps({"triage_status": "NON_COMPLIANT", "components": [], "repetitive_issue_detected": False})
N.call_groq_lpu = fake_triage
tri = N.node_02d_user_journey_classifier_and_triage(dict(base))
check("triage prompt no longer judges compliance", "NON_COMPLIANT" not in seen["prompt"] and "MAS COMPLIANCE CONTEXT" not in seen["prompt"])
check("a stray NON_COMPLIANT from triage is treated as DECOMPOSED", tri["triage_status"] == "DECOMPOSED")

edges = {(e.source, e.target) for e in app_graph.get_graph().edges}
check("triage and the screen both run after the profile loader", {("n1b", "n2d"), ("n1b", "n2f")} <= edges)
check("both feed the join, which routes onward", {("n2d", "n2g"), ("n2f", "n2g"), ("n2g", "n5"), ("n2g", "n23"), ("n2g", "n14"), ("n2g", "n19")} <= edges)
check("triage no longer routes directly", not any(src == "n2d" and dst != "n2g" for src, dst in edges))
print(f"{sum(checks)}/{len(checks)}")
