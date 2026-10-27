"""Re-judge H1 replies whose frozen ambiguity label was corrected in the owner-requested review (labels/ambiguity_review.jsonl).

Labeller A's first pass was shown the old label ("Genuinely ambiguous: False"), which drove its clarification verdict.
Same prompt, model and inputs as judge_h1.py, with only the ambiguity label corrected.
Output: labels/score_A_h1_ambig_rejudge.jsonl (replaces those rows in aggregation).
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from judge_common_v51 import D, KEYS, LABELS, entries_text, judge, results, turn_view  # noqa: E402
from judge_h1 import PROMPT  # noqa: E402

fix = {json.loads(l)["item_id"]: json.loads(l) for l in open(D / "labels/ambiguity_review.jsonl")}
with open(D / "labels/score_A_h1_ambig_rejudge.jsonl", "w") as out:
    for cid, r in sorted(results("h1_banking77").items()):
        item = next(t.split(":", 1)[1] for t in r["tags"] if t.startswith("item:"))
        if item not in fix:
            continue
        lab, tv, key = LABELS[item], turn_view(r["turns"][-1]), KEYS.get(item, [])
        v = judge(PROMPT.format(question=lab["text"], coverage=lab["coverage"], ambiguous=fix[item]["ambiguous"],
                                routes=", ".join(lab["acceptable_routes"]), key=json.dumps(key, ensure_ascii=False) if key else "(none)",
                                reference=entries_text(lab["answering_faq_ids"]), evidence=entries_text(tv["approved"]),
                                reply=tv["reply"] or "(empty)", nav=tv["nav"] or "none", forms=tv["forms"] or "none",
                                soft=tv["soft_escalation"], pd=tv["personal_data_reveals"] or "none"), pace=2.0)
        out.write(json.dumps({"case_id": cid, "item_id": item, "rejudged_ambiguity": True, **v}, ensure_ascii=False) + "\n")
        print(cid, v.get("outcome"), v.get("clarification_necessary"))
