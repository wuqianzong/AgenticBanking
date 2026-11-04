"""Offline regression suites (no model calls). Run before committing a change:

    python backend/tests/regression/run_all.py

Each suite prints PASS/FAIL lines and a final "passed/total" line. This
runner executes every test_*.py in this folder, prints one summary line per
suite, and exits non-zero if any suite fails or crashes.
"""
import re
import subprocess
import sys
from pathlib import Path

here = Path(__file__).resolve().parent
failed = 0
for suite in sorted(here.glob("test_*.py")):
    run = subprocess.run([sys.executable, str(suite)], capture_output=True, text=True, cwd=here.parents[2])
    totals = re.findall(r"^(\d+)/(\d+)(?:\s+checks passed)?\s*$", run.stdout, re.M)
    passed, total = (int(totals[-1][0]), int(totals[-1][1])) if totals else (0, 0)
    ok = run.returncode == 0 and total > 0 and passed == total
    failed += not ok
    print(f"{'PASS' if ok else 'FAIL'}  {suite.stem:45s} {passed}/{total}")
    if not ok:
        print(run.stdout[-1500:], run.stderr[-1500:], sep="\n")
print("\n" + ("all suites passed" if not failed else f"{failed} suite(s) failed"))
sys.exit(1 if failed else 0)
