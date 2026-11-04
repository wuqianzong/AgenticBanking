"""Re-judge the five B6 explicit-handover replies with the escalation confirmation card included.

The first Labeller A pass saw only buttons, forms and the soft-offer flag, not the response block of type
escalation_confirmation that carries the customer's "transfer me to a human agent" button. Same rubric and
model; only the missing UI element is added. Output replaces the B6 rows: labels/score_A_h4_b6_rejudge.jsonl.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from judge_common_v51 import D, judge, results, turn_view  # noqa: E402
from judge_misc import RUBRIC  # noqa: E402

with open(D / "labels/score_A_h4_b6_rejudge.jsonl", "w") as out:
    for cid, r in sorted(results("h4_controls").items()):
        if not cid.startswith("h4_b6_"):
            continue
        t = r["turns"][-1]
        tv = turn_view(t)
        blocks = t["summary"].get("response_blocks") or []
        cards = [f"escalation confirmation card with the button '{b['confirmation']['message']}'" for b in blocks
                 if isinstance(b, dict) and b.get("type") == "escalation_confirmation"]
        p = RUBRIC["B6"].format(msg=t["message"], reply=tv["reply"], nav=cards or "none", forms="none", soft=tv["soft_escalation"], entries="")
        v = judge(p, pace=2.0)
        out.write(json.dumps({"case_id": cid, "control": "B6", "rejudged_with_card": True, **v}) + "\n")
        print(cid, v.get("honoured"), v.get("reason", "")[:160])
