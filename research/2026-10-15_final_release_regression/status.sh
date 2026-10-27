#!/bin/zsh
set -u
STUDY=/Users/bensonwu/Projects/AgenticBanking/research/2026-10-15_final_release_regression
PY=/Users/bensonwu/miniconda3/envs/agentic_banking/bin/python

if [[ -f $STUDY/state/launcher.pid ]]; then
  PID=$(<$STUDY/state/launcher.pid)
  if kill -0 $PID 2>/dev/null; then
    print "launcher: RUNNING pid=$PID"
  else
    print "launcher: stale pid file ($PID)"
  fi
else
  print "launcher: not running"
fi

for suite in h3_safety h1_banking77 h2b_journeys h4_controls c4_cache; do
  $PY $STUDY/scripts/check_complete.py $STUDY/runs/$suite/study.json 2>/dev/null || true
done

print "recent batch events:"
tail -12 $STUDY/results/batch.log 2>/dev/null || true
