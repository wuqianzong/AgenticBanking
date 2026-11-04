import sys
sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[3]))
import backend.app.orchestration.nodes as N
N.log_event = lambda *a, **k: None

checks = []


def check(name, good):
    checks.append(good)
    print(("PASS " if good else "FAIL ") + name)


# The previous turn already repeated the need; this turn repeats it again, so
# the repeated-need rule fires (2 turns) whatever this turn's reply is.
prior = [{"turn_number": 1, "had_repeated_need": True, "subqueries": [{"text": "Where is my refund?", "score": 0.0}], "user_rating": None}]
unresolved = [{"subquery_id": "subquery_1", "customer_component": "Where is my refund?", "resolution_status": "OPEN", "tool_attempts": []}]


def offered(blocks, final_clarification, subqueries=unresolved):
    state = {**N._fresh_volatile_state_reset(), "turn_count": 2, "turn_escalation_history": list(prior),
             "repeated_need_detected_this_turn": True, "financial_subqueries": subqueries, "chat_history": [],
             "final_clarification": final_clarification, "final_response": " ".join(b["text"] for b in blocks),
             "ui_action_payload": {"uiType": "blocks", "responseBlocks": blocks}}
    return N.node_08_soft_escalation_analyzer(state)["ui_action_payload"]["softEscalationAvailable"]


no_info = [{"blockId": "task_1", "type": "text", "text": "I don't have information available on the status of your refund."}]
check("no-info turn of a repeated need: offer shown (was suppressed)", offered(no_info, no_info[0]["text"]) is True)
question = [{"blockId": "subquery_1", "type": "clarification", "text": "Which refund do you mean -- a card purchase or a transfer?"}]
check("clarifying-question turn: still no offer", offered(question, question[0]["text"]) is False)
resolved = [{"subquery_id": "subquery_1", "customer_component": "Where is my refund?", "resolution_status": "RESOLVED", "tool_attempts": []}]
answer = [{"blockId": "task_1", "type": "text", "text": "Refunds reach your account within 5 business days."}]
check("fully answered turn: still no offer", offered(answer, "", resolved) is False)
print(f"{sum(checks)}/{len(checks)}")
