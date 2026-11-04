"""Append Labeller B reply scores. One line per reply: <set>|<case_id>|<field=value,...>|<note>
Fields mirror Labeller A's (e.g. outcome=ANSWERED,subst=1,addr=1,major=0,keys=110,gap=-,limit=0,invent=0,next=0,advice=0,
exec=0,channel=1,clar=-). Values: 1/0, '-' for not applicable, or a word. Last line per case wins."""
import json
import sys
from pathlib import Path

D = Path(__file__).resolve().parents[1]
n = 0
for line in sys.stdin:
    line = line.strip()
    if not line or line.startswith("#"):
        continue
    s, cid, fields, note = (line.split("|", 3) + [""])[:4]
    rec = {"case_id": cid.strip(), "note": note.strip()}
    for kv in fields.split(","):
        k, v = kv.split("=", 1)
        v = v.strip()
        rec[k.strip()] = None if v == "-" else (int(v) if v.isdigit() and k.strip() != "keys" else v)
    with open(D / f"labels/score_B_{s.strip()}.jsonl", "a") as out:
        out.write(json.dumps(rec, ensure_ascii=False) + "\n")
    n += 1
print("saved", n)
