"""Freeze manifest (design Part C step 10, Part D4): SHA-256 of every label, key, fixture and pass-count file,
plus the code commit, written before any v5.1 bot run. Any later change to a listed file voids the freeze."""
import hashlib
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path

D = Path(__file__).resolve().parents[1]
ROOT = D.parents[1]
FILES = ["data/h1_selected.jsonl", "data/h1_provenance.json", "labels/item_key.json", "labels/candidates.jsonl",
         "labels/labels_B.jsonl", "labels/labels_final.jsonl", "labels/adjudication_summary.json",
         "labels/answer_keys_B.jsonl", "labels/answer_keys_final.jsonl", "labels/answer_keys_strict.jsonl",
         "labels/key_decisions.jsonl", "labels/selfcons_items.json", "labels/selfcons_A.jsonl", "labels/selfcons_B.jsonl",
         "fixtures/h2b_journeys.jsonl", "fixtures/h2b_keys.jsonl", "fixtures/h3_safety.jsonl",
         "fixtures/h4_controls.jsonl", "fixtures/h4_c4_repeats.jsonl", "fixtures/h1_cases.jsonl", "planted/fixtures/planted_cases.jsonl",
         "results/pass_counts.json", "results/selfcons_summary.json"]
FILES += sorted(str(p.relative_to(D)) for p in (D / "labels").glob("labels_A*.jsonl"))
FILES += sorted(str(p.relative_to(D)) for p in (D / "labels").glob("key_check_A*.jsonl"))
h = lambda p: hashlib.sha256(p.read_bytes()).hexdigest()  # noqa: E731
commit = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True).stdout.strip()
dirty = subprocess.run(["git", "status", "--porcelain", "backend/app"], cwd=ROOT, capture_output=True, text=True).stdout.strip()
manifest = {"frozen_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "code_commit": commit,
            "backend_app_uncommitted_changes": dirty or "none", "files": {f: h(D / f) for f in FILES}}
json.dump(manifest, open(D / "FREEZE.json", "w"), indent=1)
print(json.dumps({k: v for k, v in manifest.items() if k != "files"}, indent=1), len(manifest["files"]), "files")
