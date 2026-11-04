"""R7b known-defect replay (design metric R7b): re-introduce three fixed defects, each in its own temporary
git worktree of the release commit, and check that the defect's own regression suite fails on its assertions
(not by crashing). The release checkout is never modified.

Whole-commit reverts were tried first: BUG-055 and BUG-058 conflict with later commits and BUG-038 fails only
through a signature error, so each defect is seeded at the mechanism level instead:
  BUG-055 compliance routing        the join ignores the inbound screen's non-compliant verdict
  BUG-058 reply-generation routing  shortcut (tool-only) obligations go back to the full generator
  BUG-038 obligation partition      a flag on a block's shortcut handle or non-obligation citation no longer
                                    finds the block, and an unmatched flag approves every block (pre-fix logic)
"""
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
OUT = Path(__file__).resolve().parents[1] / "offline"
SCRATCH = Path(sys.argv[1])
NODES = "backend/app/orchestration/nodes.py"
SEEDS = {
    "BUG-055": ("test_bug055_inbound_compliance_screen", [
        ('    if state.get("inbound_compliance", {}).get("non_compliant"):\n        print("   ⛔ [COMPLIANCE JOIN]',
         '    if False and state.get("inbound_compliance", {}).get("non_compliant"):\n        print("   ⛔ [COMPLIANCE JOIN]')]),
    "BUG-058": ("test_bug058_tool_only_turn", [
        ('_EVIDENCE_ONLY_GENERATION_RULES = {\n    _GENERATION_RULE_BY_TYPE["shortcut"],\n',
         '_EVIDENCE_ONLY_GENERATION_RULES = {\n')]),
    "BUG-038": ("test_bug038_obligation_partition", [
        ('                *(block.get("sourceShortcutHandles") or []),\n                *(block.get("sourceUnmatchedIds") or []),\n            ) if i',
         '            ) if i'),
        ('    if flagged - matched:\n        return [], every_block_id | flagged, True\n    return approved, pending, False',
         '    return approved, pending, False')]),
}
results = {}
for bug, (suite, edits) in SEEDS.items():
    wt = SCRATCH / f"r7b_{bug}"
    subprocess.run(["git", "worktree", "remove", "--force", str(wt)], cwd=ROOT, capture_output=True)
    subprocess.run(["git", "worktree", "add", "-q", "--detach", str(wt), "HEAD"], cwd=ROOT, check=True)
    src = (wt / NODES).read_text()
    for old, new in edits:
        assert src.count(old) == 1, f"{bug}: seed anchor not unique"
        src = src.replace(old, new)
    (wt / NODES).write_text(src)
    diff = subprocess.run(["git", "diff"], cwd=wt, capture_output=True, text=True).stdout
    run = subprocess.run([sys.executable, f"backend/tests/regression/{suite}.py"], cwd=wt, capture_output=True, text=True)
    fails = re.findall(r"^FAIL (.*)$", run.stdout, re.M)
    total = re.findall(r"^(\d+)/(\d+)\s*$", run.stdout, re.M)
    crashed = run.returncode != 0 and not total
    results[bug] = {"suite": suite, "seeded_diff_lines": sum(1 for l in diff.splitlines() if l[:1] in "+-" and l[:3] not in ("+++", "---")),
                    "result": f"{total[-1][0]}/{total[-1][1]}" if total else "no total", "failed_checks": fails,
                    "crashed": crashed, "detected": bool(fails) and not crashed}
    (OUT / f"r7b_{bug}_seed.diff").write_text(diff)
    (OUT / f"r7b_{bug}_suite.log").write_text(run.stdout + run.stderr)
    subprocess.run(["git", "worktree", "remove", "--force", str(wt)], cwd=ROOT, check=True)
json.dump(results, open(OUT / "r7b_summary.json", "w"), indent=1)
print(json.dumps(results, indent=1))
