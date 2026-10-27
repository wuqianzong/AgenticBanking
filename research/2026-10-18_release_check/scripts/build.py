"""Release check on the frozen code (commit 45329fe): the demo rehearsal script plus cases for each post-freeze
fix (BUG-070 schemas, BUG-071 handover override, BUG-073 tag spelling) and the safety gates. Pass/fail release
check, not a metrics run. Fresh copy of the live data with an empty answer cache, as in every evaluation run.
    python research/2026-10-18_release_check/scripts/build.py
"""
import json
import shutil
from pathlib import Path

import chromadb

D = Path(__file__).resolve().parents[1]
REPO = D.parents[1]
FIX = REPO / "research/2026-10-04_v51_holdout/fixtures"
BASE = REPO / "research/2026-10-15_final_release_regression"


def pick(name, ids):
    rows = {json.loads(l)["case_id"]: json.loads(l) for l in open(FIX / name) if l.strip()}
    return [rows[i] for i in ids]


suites = {
    "demo": [json.loads(l) for l in open(REPO / "research/2026-10-03_demo_rehearsal/fixtures/cases_v1.jsonl") if l.strip()],
    # BUG-073 (General_Inquiries), and replies whose writer or fact-check JSON was malformed on 15 Oct (BUG-070)
    "h1_banking77": pick("h1_cases.jsonl", ["h1_h1_contactless_not_working_1", "h1_h1_pending_top_up_2",
                                             "h1_h1_card_payment_not_recognised_4", "h1_h1_wrong_amount_of_cash_received_1"]),
    "h3_safety": pick("h3_safety.jsonl", ["h3_inject_direct_01", "h3_jailbreak_01", "h3_leakage_02", "h3_harmful_01", "h3_multi_turn_07"]),
    # BUG-071: all five explicit handover requests, including "Transfer me to customer service."; typed credentials
    "h4_controls": pick("h4_controls.jsonl", ["h4_b6_01", "h4_b6_02", "h4_b6_03", "h4_b6_04", "h4_b6_05", "h4_r3_02", "h4_r3_07"]),
}
for suite, rows in suites.items():
    run = D / "runs" / suite
    (run / "results").mkdir(parents=True, exist_ok=True)
    with open(run / "fixture.jsonl", "w") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    template = BASE / "runs" / ("h1_banking77" if suite == "demo" else suite) / "study.json"
    study = json.load(open(template))
    study.update({"study_id": f"2026-10-18_release_{suite}", "fixture": "fixture.jsonl",
                  "notes": "Release check on the frozen code (45329fe): demo script, post-freeze fixes and safety gates."})
    json.dump(study, open(run / "study.json", "w"), indent=1)
    print(suite, len(rows))

root = D / "isolated_root"
if not root.exists():
    (root / "backend").mkdir(parents=True)
    for entry in (REPO / "backend").iterdir():
        dest = root / "backend" / entry.name
        if entry.name == "data":
            shutil.copytree(entry, dest)
        elif entry.name == "logs":
            dest.mkdir()
        else:
            dest.symlink_to(entry)
    for name in ("research", ".env"):
        (root / name).symlink_to(REPO / name)
    client = chromadb.PersistentClient(path=str(root / "backend/data/vector_store"))
    if "subquery_answer_cache" in {c.name for c in client.list_collections()}:
        client.delete_collection("subquery_answer_cache")
    print("isolated root ready; answer cache cleared; FAQ chunks:", client.get_collection("faq_child_chunks").count())
