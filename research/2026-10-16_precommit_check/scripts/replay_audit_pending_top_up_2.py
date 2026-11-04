"""Replay the final-round audit prompt of h1_h1_pending_top_up_2, five times with the NormalCombinedAudit schema
and five times without, to see whether the schema explains the missed own-case claim
("Your top-up transaction is currently experiencing a temporary security hold"). Output: results/replay_audit_pending_top_up_2.json"""
import glob
import json
import sys
import time
from pathlib import Path

D = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(D.parents[1]))
from backend.app.core.llm_service import NormalCombinedAudit, call_llm_router, parse_json_robustly  # noqa: E402

sid = next(json.loads(l)["session_id"] for f in glob.glob(str(D / "runs/h1_banking77/results/*_results.jsonl"))
           for l in open(f) if json.loads(l)["case_id"] == "h1_h1_pending_top_up_2" and json.loads(l)["status"] == "OK")
audits = [json.loads(l)["raw_prompt"] for l in open(D / "runs/h1_banking77/results/capture_log.jsonl")
          if '"llm.request"' in l and json.loads(l).get("session_id") == sid and "COMBINED CONDUCT & EVIDENCE AUDIT" in (json.loads(l).get("raw_prompt") or "")]
prompt = audits[-1]
assert "currently experiencing a temporary security hold" in prompt
out = []
for i in range(5):
    for label, schema in (("schema", NormalCombinedAudit), ("no_schema", None)):
        raw = call_llm_router(prompt, response_schema_override=schema)
        r = raw if isinstance(raw, dict) else parse_json_robustly(raw)
        flagged = "currently experiencing" in json.dumps(r.get("evidence_violations", []) + r.get("grounding_violations", []))
        out.append({"run": i + 1, "variant": label, "evidence_grounded": r.get("evidence_grounded"), "flagged_own_case_claim": flagged,
                    "reason": str(r.get("evidence_reason", ""))[:200]})
        print(out[-1]["run"], label, r.get("evidence_grounded"), flagged)
        time.sleep(4)
json.dump(out, open(D / "results/replay_audit_pending_top_up_2.json", "w"), indent=1)
