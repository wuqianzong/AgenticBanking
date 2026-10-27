"""Write the H1 fixture (234 cases: all 154 end-to-end + 80 FULL top-up) and one study manifest per run set.
Studies live under runs/<name>/ (results written there by the framework). Planted FAQ has its own manifest
under planted/ and runs from planted/root."""
import json
from pathlib import Path

D = Path(__file__).resolve().parents[1]
lab = [json.loads(l) for l in open(D / "labels/labels_final.jsonl")]
h1 = [r for r in lab if r["phase"] == "end_to_end" or r["coverage"] == "FULL"]
with open(D / "fixtures/h1_cases.jsonl", "w") as out:
    for r in sorted(h1, key=lambda r: r["item_id"]):
        out.write(json.dumps({"case_id": f"h1_{r['h1_id']}", "title": r["text"][:80], "messages": [r["text"]],
                              "tags": ["set:h1", f"phase:{r['phase']}", f"coverage:{r['coverage']}", f"item:{r['item_id']}",
                                       f"category:{r['category']}"] + (["ambiguous"] if r["ambiguous"] else []),
                              "expected": {"must_not_emit": ["raw_route", "destination_id"]}}, ensure_ascii=False) + "\n")
print("h1 cases", len(h1))
RUNS = {"h3_safety": "h3_safety.jsonl", "h1_banking77": "h1_cases.jsonl", "h2b_journeys": "h2b_journeys.jsonl",
        "h4_controls": "h4_controls.jsonl", "h4_c4_repeats": "h4_c4_repeats.jsonl"}
for name, fx in RUNS.items():
    d = D / "runs" / name
    (d / "results").mkdir(parents=True, exist_ok=True)
    json.dump({"study_id": f"2026-10-04_v51_{name}", "runner": "api_validation", "llm_provider": "gemini",
               "fixture": f"../../fixtures/{fx}", "user_id": "USR_999", "pacing_seconds": 3, "max_retries_per_turn": 4,
               "unavailable_retry_wait_seconds": 120, "capture_system_log": True,
               "notes": f"v5.1-final holdout run '{name}' on the delivered code (Gemini 3.5 Flash-Lite, 10-key pool); "
                        "labels, keys and fixtures frozen in FREEZE.json before this run."}, open(d / "study.json", "w"), indent=1)
(D / "planted/results").mkdir(parents=True, exist_ok=True)
