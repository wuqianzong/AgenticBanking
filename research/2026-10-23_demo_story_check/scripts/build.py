"""Demo user-story check, 23 Oct 2026: replay every demo story (S1-S6), the backups (B1-B5) and three taboo
probes through the real chat API on the current code, and compare each reply with the expectation written in the
report's "Demo user stories" tab. Pass/fail check of the demo script, not a metrics run.

Fresh copy of the live data with an empty answer cache, as in every evaluation run. The test customer USR_999 is the
only one with mock balance and address data, so it is used inside this isolated copy only, with its profile pinned
(English, professional) and its old service requests expired so the Story 2 login reminder sees only its own request.
    python research/2026-10-23_demo_story_check/scripts/build.py
"""
import json
import shutil
import sqlite3
import sys
from pathlib import Path

import chromadb

D = Path(__file__).resolve().parents[1]
REPO = D.parents[1]
TEMPLATE = REPO / "research/2026-10-15_final_release_regression/runs/h1_banking77/study.json"

sys.path.insert(0, str(Path(__file__).resolve().parent))
from cases import CASES as cases  # noqa: E402

run = D / "runs/stories"
(run / "results").mkdir(parents=True, exist_ok=True)
with open(run / "fixture.jsonl", "w") as f:
    for c in cases:
        f.write(json.dumps({"case_id": c["case_id"], "title": c["title"], "messages": c["messages"],
                            "tags": [f"story:{c['story']}"], "expected": {}}, ensure_ascii=False) + "\n")
study = json.load(open(TEMPLATE))
study.update({"study_id": "2026-10-23_demo_story_check", "fixture": "fixture.jsonl",
              "notes": "Demo user-story check on the current code: S1, S3-S6, B1-B5 and taboo probes."})
json.dump(study, open(run / "study.json", "w"), indent=1)
(D / "runs/story2/results").mkdir(parents=True, exist_ok=True)
print("cases", len(cases))

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
    with sqlite3.connect(root / "backend/data/simulated_ledger.db") as conn:
        conn.execute("UPDATE Persistent_Customer_Profile SET language='English', tone='professional', emoji='enabled', "
                     "financial_literacy='intermediate' WHERE customer_id='USR_999'")
        conn.execute("UPDATE Service_Request_Registration SET status='expired' WHERE user_id='USR_999' AND status='pending'")
        profile = conn.execute("SELECT * FROM Persistent_Customer_Profile WHERE customer_id='USR_999'").fetchall()
    print("isolated root ready; answer cache cleared; FAQ chunks:", client.get_collection("faq_child_chunks").count())
    print("pinned profile:", profile)
