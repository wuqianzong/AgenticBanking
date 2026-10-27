"""Select the C4 repeat questions (design Part B, metric C4) from the frozen coverage labels.

20 data-available (FULL or PARTIAL) questions from the end-to-end sample, at most one per BANKING77
category, drawn with random.Random(20261004). Each is run twice more in fresh sessions; the H1 run is
the first of its three runs.
"""
import json
import random
from pathlib import Path

D = Path(__file__).resolve().parents[1]
lab = [json.loads(l) for l in open(D / "labels/labels_final.jsonl")]
pool = sorted((r for r in lab if r["phase"] == "end_to_end" and r["coverage"] in ("FULL", "PARTIAL")), key=lambda r: r["h1_id"])
rng = random.Random(20261004)
rng.shuffle(pool)
chosen, cats = [], set()
for r in pool:
    if r["category"] not in cats:
        chosen.append(r); cats.add(r["category"])
    if len(chosen) == 20:
        break
with open(D / "fixtures/h4_c4_repeats.jsonl", "w") as out:
    for r in chosen:
        for rep in (2, 3):
            out.write(json.dumps({"case_id": f"h4_c4_{r['h1_id']}_run{rep}", "title": r["text"][:80], "messages": [r["text"]],
                                  "tags": ["set:h4", "control:C4", f"h1:{r['h1_id']}", f"item:{r['item_id']}", f"coverage:{r['coverage']}"],
                                  "expected": {"must_not_emit": ["raw_route", "destination_id"]}}, ensure_ascii=False) + "\n")
print(len(pool), "eligible;", len(chosen), "chosen;", sum(r["coverage"] == "FULL" for r in chosen), "FULL")
for r in chosen:
    print(r["item_id"], r["coverage"], r["category"], "|", r["text"][:70])
