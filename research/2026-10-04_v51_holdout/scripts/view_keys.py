"""Show items needing an answer key (FULL: all; PARTIAL: end-to-end) with answering-entry text."""
import json, re, sys
from pathlib import Path
D = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(D.parents[1] / "research/2026-09-27_v4_measurement_judges/scripts"))
from judge_common import _db, fill_precision
txt = {p: fill_precision(c) for p, c in _db.execute("select parent_id, content from knowledge_parents")}
B = {}
for l in open(D / "labels/labels_B.jsonl"):
    r = json.loads(l); B[r["item_id"]] = r
items = [json.loads(l) for l in open(D / "labels/labels_final.jsonl")]
need = [r for r in items if r["coverage"] == "FULL" or (r["coverage"] == "PARTIAL" and r["phase"] == "end_to_end")]
a, b = int(sys.argv[1]), int(sys.argv[2])
if a == 0: print("TOTAL needing keys:", len(need))
for r in need[a:b]:
    ids = B[r["item_id"]]["answering_faq_ids"] + [i for i in r["answering_faq_ids"] if i not in B[r["item_id"]]["answering_faq_ids"]]
    print(f"\n### {r['item_id']} [{r['coverage']}] {r['text'][:160]}")
    for i in ids[:3]:
        ans = re.sub(r"\s+", " ", txt.get(i, "?").split("Answer:", 1)[-1]).strip()
        print(f"  {i[4:]}: {ans[:330]}")
