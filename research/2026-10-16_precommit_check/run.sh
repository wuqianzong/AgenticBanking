#!/bin/zsh
# Resumable and sleep-safe: run_study skips cases already OK for the same study_id, and each suite is
# re-run until check_complete.py confirms every case is OK (a call cut off by laptop sleep is retried).
# Launch detached:  nohup ./run.sh >> launcher.out 2>&1 &
set -u
D=/Users/bensonwu/Projects/AgenticBanking/research/2026-10-16_precommit_check
PY=/Users/bensonwu/miniconda3/envs/agentic_banking/bin/python
CHECK=/Users/bensonwu/Projects/AgenticBanking/research/2026-10-15_final_release_regression/scripts/check_complete.py
print $$ > $D/launcher.pid
for suite in h3_safety h1_banking77 h2b_journeys h4_controls; do
  attempt=0
  while ! $PY $CHECK $D/runs/$suite/study.json >> $D/launcher.out 2>&1; do
    attempt=$((attempt + 1))
    if (( attempt > 5 )); then print "$(date '+%T') $suite incomplete after 5 attempts; stopping"; exit 1; fi
    print "$(date '+%T') $suite attempt=$attempt start"
    (cd $D/isolated_root && $PY -u -m research._framework.run_study $D/runs/$suite/study.json \
       --capture-log $D/runs/$suite/results/capture_log.jsonl) >> $D/runs/$suite/results/run_stdout.log 2>&1
    print "$(date '+%T') $suite attempt=$attempt exit=$?"
  done
done
$PY $D/scripts/compare.py > $D/results/compare_stdout.txt 2>&1
print "$(date '+%T') all done; comparison written"
touch $D/DONE
