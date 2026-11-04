"""Append Labeller B labels. Input lines: item|COVERAGE|ids(comma)|amb(0/1)|routes(comma)|rationale"""
import json, sys
from pathlib import Path
D = Path(__file__).resolve().parents[1]
out = open(D / "labels/labels_B.jsonl", "a")
n = 0
for line in sys.stdin:
    line = line.strip()
    if not line or line.startswith("#"): continue
    item, cov, ids, amb, routes, why = [x.strip() for x in line.split("|", 5)]
    assert cov in ("FULL", "PARTIAL", "NO_DATA"), line
    out.write(json.dumps({"item_id": item, "coverage": cov,
        "answering_faq_ids": [("faq_" + i.strip()) for i in ids.split(",") if i.strip()],
        "ambiguous": amb == "1", "acceptable_routes": [r.strip() for r in routes.split(",") if r.strip()],
        "reason": why}, ensure_ascii=False) + "\n"); n += 1
print("saved", n)
