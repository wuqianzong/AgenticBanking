"""Self-consistency re-label (design Part C step 8): a random 10% of the 308 H1 items (31, seed 20261005),
re-labelled by each labeller in a new shuffled order. Labeller A: same prompt, candidates and model.
Labeller B: the same compact view as the first pass (no labels shown).

  python scripts/selfcons.py sample    -> labels/selfcons_items.json
  python scripts/selfcons.py A         -> labels/selfcons_A.jsonl
  python scripts/selfcons.py viewB     -> prints the items for Labeller B (save with save_b.py --out selfcons_B)
"""
import json
import random
import re
import sys
from pathlib import Path

D = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(D / "scripts"))
sys.path.insert(0, str(D.parents[1] / "research/2026-09-27_v4_measurement_judges/scripts"))

mode = sys.argv[1]
if mode == "sample":
    key = json.load(open(D / "labels/item_key.json"))
    h1 = sorted(k for k, v in key.items() if v["source"] == "h1")
    rng = random.Random(20261005)
    pick = rng.sample(h1, 31)
    rng.shuffle(pick)
    json.dump(pick, open(D / "labels/selfcons_items.json", "w"), indent=1)
    print(len(pick), pick)
    sys.exit()

pick = json.load(open(D / "labels/selfcons_items.json"))
cands = {json.loads(l)["item_id"]: json.loads(l) for l in open(D / "labels/candidates.jsonl")}
from judge_common import _db, fill_precision, judge  # noqa: E402
if mode == "A":
    from labeller_a import PROMPT, TOOLS  # noqa: E402
    text_of = {pid: fill_precision(c) for pid, c in _db.execute("select parent_id, content from knowledge_parents")}
    with open(D / "labels/selfcons_A.jsonl", "w") as out:
        for item in pick:
            it = cands[item]
            c = "\n\n".join(f"[{pid}]\n{text_of[pid][:900]}" for pid in it["candidates"])
            v = judge(PROMPT.format(tools=TOOLS, question=it["text"], candidates=c), pace=2.0)
            if isinstance(v, list):
                v = v[0] if v and isinstance(v[0], dict) else {"coverage": None, "raw": v}
            out.write(json.dumps({"item_id": item, **v}, ensure_ascii=False) + "\n")
            print(item, v.get("coverage"), flush=True)
elif mode == "viewB":
    q = {pid: re.search(r"Question:\s*(.*)", c).group(1).strip()[:110] for pid, c in _db.execute("select parent_id, content from knowledge_parents")}
    for item in pick:
        it = cands[item]
        print(f"\n### {item}: {it['text']}")
        seen = set()
        for pid in it["candidates"]:
            if q[pid] not in seen:
                seen.add(q[pid])
                print(f"  {pid[4:]} {q[pid]}")
