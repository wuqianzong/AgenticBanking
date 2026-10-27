"""Replay the captured inbound compliance-screen prompt for h4_b6_03 ("Transfer me to customer service.")
six times, to see how often the unchanged screen blocks it. Output: results/replay_b6_03.json"""
import json
import sys
import time
from pathlib import Path

D = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(D.parents[1]))
from backend.app.core.llm_service import call_llm_router  # noqa: E402

prompt = None
for l in open(D / "runs/h4_controls/results/capture_log.jsonl"):
    e = json.loads(l)
    if e["event"] == "llm.request" and "INBOUND COMPLIANCE SCREEN" in (e.get("raw_prompt") or "") \
            and "Transfer me to customer service." in e["raw_prompt"]:
        prompt = e["raw_prompt"]
assert prompt
out = []
for i in range(6):
    raw = call_llm_router(prompt)
    r = json.loads(raw) if isinstance(raw, str) else raw
    out.append({"verdict": r.get("verdict"), "route": r.get("compliant_route", "")[:160]})
    print(i + 1, out[-1])
    time.sleep(4)
json.dump(out, open(D / "results/replay_b6_03.json", "w"), indent=1)
