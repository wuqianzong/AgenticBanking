"""Labeller B view for scoring replies: question, frozen labels and key, reply, buttons/forms, evidence ids.
Shows no Labeller A verdict.   python scripts/view_score_b.py <run> <start> <end> | <run> todo <n>   (run: h1 | h3 | h4 | c4 | h2b)"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from judge_common_v51 import D, KEYS, LABELS, results, turn_view  # noqa: E402

RUN = {"h1": "h1_banking77", "h3": "h3_safety", "h4": "h4_controls", "c4": "h4_c4_repeats", "h2b": "h2b_journeys"}
which = sys.argv[1]
res = sorted(results(RUN[which]).items())
if sys.argv[2] == "todo":  # next N cases not yet scored by Labeller B
    f = D / f"labels/score_B_{which}.jsonl"
    scored = {json.loads(l)["case_id"] for l in open(f)} if f.exists() else set()
    res = [x for x in res if x[0] not in scored]
    a, b = 0, int(sys.argv[3])
else:
    a, b = int(sys.argv[2]), int(sys.argv[3])
h2b_keys = {json.loads(l)["case_id"]: json.loads(l) for l in open(D / "fixtures/h2b_keys.jsonl")} if which == "h2b" else {}
for cid, r in res[a:b]:
    item = next((t.split(":", 1)[1] for t in r["tags"] if t.startswith("item:")), None)
    head = f"\n### {cid} [{r.get('status')}]"
    if item:
        lab = LABELS[item]
        head += f" {lab['coverage']} amb={int(lab['ambiguous'])} routes={','.join(lab['acceptable_routes'])}"
        head += f"\n  KEY: {' | '.join(KEYS.get(item, [])) or '-'}\n  REF: {','.join(p[4:] for p in lab['answering_faq_ids'])}"
    else:
        head += " " + " ".join(t for t in r["tags"] if t.split(":")[0] in ("category", "control", "group", "variant", "form"))
    print(head)
    for i, t in enumerate(r.get("turns", [])):
        tv = turn_view(t)
        extra = f" need={h2b_keys[cid]['turns'][i]['need']!r} routes={h2b_keys[cid]['turns'][i]['acceptable_routes']}" if which == "h2b" else ""
        print(f"  Q{i + 1}: {t['message']}{extra}")
        print(f"  A{i + 1}: {tv['reply']}")
        print(f"     [pre={tv['preflight']} tri={tv['triage']} ev={','.join(p[4:] for p in tv['approved']) or '-'} nav={tv['nav']} forms={tv['forms']} "
              f"soft={int(tv['soft_escalation'])} pd={tv['personal_data_reveals'] or '-'}]")
