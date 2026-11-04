"""Compact view for Labeller B: item text + candidate FAQ questions (no labels, no bot output)."""
import json, re, sys
from pathlib import Path
D = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(D.parents[1] / "research/2026-09-27_v4_measurement_judges/scripts"))
from judge_common import _db
q = {pid: re.search(r"Question:\s*(.*)", c).group(1).strip()[:110] for pid, c in _db.execute("select parent_id, content from knowledge_parents")}
items = [json.loads(l) for l in open(D / "labels/candidates.jsonl")]
a, b = int(sys.argv[1]), int(sys.argv[2])
for it in items[a:b]:
    print(f"\n### {it['item_id']}: {it['text']}")
    seen = set()
    for pid in it["candidates"]:
        if q[pid] in seen: continue
        seen.add(q[pid]); print(f"  {pid[4:]} {q[pid]}")
