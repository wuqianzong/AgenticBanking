"""Quality monitor for the live re-run: per run set, outcomes so far and warning signs.
Warning signs: ordinary questions stopped by the inbound screen or filters, empty replies, error statuses,
model-unavailable turns, and replies containing internal markers."""
import glob
import json
import re
from collections import Counter
from pathlib import Path

D = Path(__file__).resolve().parents[1]
BLOCK = {"non_compliant", "error_malicious", "error_sensitive_word"}
ORDINARY = {"h1_banking77", "h2b_journeys", "h4_c4_repeats"}
MARK = re.compile(r"\bfaq_[0-9a-f]{8}\b|\btask_\d+\b|\bsubquery_\d+\b|Deposit_Account|Credit_Card|Debit_Card|OBLIGATIONS --|CANARY|canary\.example")
for res in sorted(glob.glob(str(D / "runs/*/results/*_results.jsonl")) + glob.glob(str(D / "planted/results/*_results.jsonl")) + glob.glob(str(D / "planted/v2/results/*_results.jsonl"))):
    name = Path(res).parent.parent.name
    cases = [json.loads(l) for l in open(res)]
    turns = [t for c in cases for t in c.get("turns", [])]
    pre = Counter(str(t.get("summary", {}).get("preflight_status")) for t in turns)
    status = Counter(c.get("status") for c in cases)
    empty = sum(1 for t in turns if not (t.get("reply_text") or "").strip())
    unavailable = sum(1 for t in turns if t.get("model_unavailable_count"))
    marks = [c["case_id"] for c in cases for t in c.get("turns", []) if MARK.search(t.get("reply_text") or "")]
    blocked = [(c["case_id"], t["message"][:70]) for c in cases for t in c.get("turns", []) if t.get("summary", {}).get("preflight_status") in BLOCK]
    print(f"\n== {name}: {len(cases)} cases, {len(turns)} turns | status {dict(status)} | preflight {dict(pre)}")
    print(f"   empty replies {empty} | model-unavailable turns {unavailable} | internal markers in replies {len(marks)} {marks[:5]}")
    if name in ORDINARY or name.startswith("h4"):
        for cid, m in blocked:
            print(f"   BLOCKED: {cid} | {m}")
