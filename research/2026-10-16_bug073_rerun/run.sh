#!/bin/zsh
# Sleep-safe: rerun until every case is OK (run_study skips OK cases). A provider outage (503) is waited out.
# Launch detached:  nohup ./run.sh >> launcher.out 2>&1 &
set -u
D=/Users/bensonwu/Projects/AgenticBanking/research/2026-10-16_bug073_rerun
PY=/Users/bensonwu/miniconda3/envs/agentic_banking/bin/python
CHECK=/Users/bensonwu/Projects/AgenticBanking/research/2026-10-15_final_release_regression/scripts/check_complete.py
print $$ > $D/launcher.pid
attempt=0
while ! $PY $CHECK $D/study.json >> $D/launcher.out 2>&1; do
  attempt=$((attempt + 1))
  if (( attempt > 8 )); then print "$(date '+%T') incomplete after 8 attempts; stopping"; exit 1; fi
  print "$(date '+%T') attempt=$attempt start"
  (cd $D/isolated_root && $PY -u -m research._framework.run_study $D/study.json --capture-log $D/results/capture_log.jsonl) >> $D/results/run_stdout.log 2>&1
  print "$(date '+%T') attempt=$attempt exit=$?"
done
print "$(date '+%T') all done"; touch $D/DONE
