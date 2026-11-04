"""Print FAQ answers (precision values filled) for Labeller B: python faq.py id8 [id8 ...] [--n 500]"""
import re, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "research/2026-09-27_v4_measurement_judges/scripts"))
from judge_common import _db, fill_precision
n = 420
args = [a for a in sys.argv[1:]]
if "--n" in args:
    n = int(args[args.index("--n") + 1]); args = args[:args.index("--n")]
for a in args:
    pid = a if a.startswith("faq_") else "faq_" + a
    row = _db.execute("select content from knowledge_parents where parent_id=?", (pid,)).fetchone()
    t = fill_precision(row[0]).replace("\n", " ") if row else "(missing)"
    print(f"[{pid[4:]}] {re.sub(r'  +', ' ', t)[:n]}\n")
