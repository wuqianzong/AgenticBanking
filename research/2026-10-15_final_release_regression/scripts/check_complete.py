"""Exit successfully only when every fixture case has an OK result."""
from __future__ import annotations

import argparse
import json
from pathlib import Path


parser = argparse.ArgumentParser()
parser.add_argument("manifest", type=Path)
args = parser.parse_args()

manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
fixture = (args.manifest.parent / manifest["fixture"]).resolve()
expected = {
    json.loads(line)["case_id"]
    for line in fixture.read_text(encoding="utf-8").splitlines()
    if line.strip() and not line.lstrip().startswith("#")
}
ok: set[str] = set()
statuses: dict[str, str] = {}
for path in (args.manifest.parent / "results").glob("*_results.jsonl"):
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if row.get("study_id") != manifest["study_id"]:
            continue
        case_id = row.get("case_id")
        statuses[case_id] = row.get("status", "UNKNOWN")
        if row.get("status") == "OK":
            ok.add(case_id)

missing = expected - ok
print(f"{manifest['study_id']}: {len(ok)}/{len(expected)} OK; remaining={len(missing)}")
if missing:
    counts: dict[str, int] = {}
    for case_id in missing:
        status = statuses.get(case_id, "NOT_RUN")
        counts[status] = counts.get(status, 0) + 1
    print("remaining statuses:", json.dumps(counts, sort_keys=True))
raise SystemExit(0 if not missing else 1)
