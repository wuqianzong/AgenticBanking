"""Append answer keys written by Labeller B. Input lines: item|fact; fact; fact"""
import json, sys
from pathlib import Path
D = Path(__file__).resolve().parents[1]
n = 0
with open(D / "labels/answer_keys_B.jsonl", "a") as out:
    for line in sys.stdin:
        line = line.strip()
        if not line or line.startswith("#"): continue
        item, facts = line.split("|", 1)
        out.write(json.dumps({"item_id": item.strip(), "key_items": [f.strip() for f in facts.split(";") if f.strip()]}, ensure_ascii=False) + "\n"); n += 1
print("saved", n)
