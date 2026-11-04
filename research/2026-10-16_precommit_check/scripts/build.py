"""Build the pre-commit live check: a fixed sample of the 15 Oct final-run cases, rerun on the current code
from a fresh isolated data copy (empty answer cache), so each case has a 15 Oct baseline to compare with.

    python research/2026-10-16_precommit_check/scripts/build.py
"""
import json
import random
import shutil
from pathlib import Path

import chromadb

D = Path(__file__).resolve().parents[1]
REPO = D.parents[1]
FIX = REPO / "research/2026-10-04_v51_holdout/fixtures"
BASE = REPO / "research/2026-10-15_final_release_regression"
ROOT = D / "isolated_root"
rng = random.Random(20261016)


def load(name):
    return [json.loads(l) for l in open(FIX / name) if l.strip()]


def tag(row, key):
    return next((t.split(":", 1)[1] for t in row["tags"] if t.startswith(key + ":")), None)


cmp = {r["case_id"]: r["category"] for r in map(json.loads, open(BASE / "results/semantic_comparison.jsonl"))}
h1 = load("h1_cases.jsonl")
pick = [r for r in h1 if cmp.get(r["case_id"]) in ("degraded", "mixed")]
pick += rng.sample(sorted((r for r in h1 if cmp.get(r["case_id"]) == "improved"), key=lambda r: r["case_id"]), 8)
pick += rng.sample(sorted((r for r in h1 if cmp.get(r["case_id"]) == "stable"), key=lambda r: r["case_id"]), 25)
h3 = [r for r in load("h3_safety.jsonl") if "self_harm" not in json.dumps(r["tags"]) and "selfharm" not in r["case_id"]]
h4 = load("h4_controls.jsonl")
suites = {
    "h1_banking77": pick,
    "h3_safety": rng.sample(sorted(h3, key=lambda r: r["case_id"]), 20),
    "h2b_journeys": rng.sample(load("h2b_journeys.jsonl"), 5),
    "h4_controls": rng.sample([r for r in h4 if tag(r, "control") == "R3"], 5) + [r for r in h4 if tag(r, "control") == "B6"],
}
for suite, rows in suites.items():
    run = D / "runs" / suite
    (run / "results").mkdir(parents=True, exist_ok=True)
    with open(run / "fixture.jsonl", "w") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    study = json.load(open(BASE / "runs" / suite / "study.json"))
    study.update({"study_id": f"2026-10-16_precommit_{suite}", "fixture": "fixture.jsonl",
                  "notes": "Pre-commit live check of the uncommitted changes (schemas, BUG-065..069) on a sample of the 15 Oct final-run cases."})
    json.dump(study, open(run / "study.json", "w"), indent=1)
    print(suite, len(rows))

if not ROOT.exists():
    (ROOT / "backend").mkdir(parents=True)
    for entry in (REPO / "backend").iterdir():
        dest = ROOT / "backend" / entry.name
        if entry.name == "data":
            shutil.copytree(entry, dest)
        elif entry.name == "logs":
            dest.mkdir()
        else:
            dest.symlink_to(entry)
    for name in ("research", ".env"):
        (ROOT / name).symlink_to(REPO / name)
    client = chromadb.PersistentClient(path=str(ROOT / "backend/data/vector_store"))
    if "subquery_answer_cache" in {c.name for c in client.list_collections()}:
        client.delete_collection("subquery_answer_cache")
    print("isolated root ready; answer cache cleared")
