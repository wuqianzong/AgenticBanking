#!/bin/zsh

# Resumable final release run. Every study runner skips existing OK cases for
# its immutable study_id, so restarting this file continues rather than
# duplicating completed live calls.
set -u

REPO=/Users/bensonwu/Projects/AgenticBanking
STUDY=$REPO/research/2026-10-15_final_release_regression
PY=/Users/bensonwu/miniconda3/envs/agentic_banking/bin/python
STATE=$STUDY/state
BATCH_LOG=$STUDY/results/batch.log
PID_FILE=$STATE/launcher.pid

mkdir -p $STATE $STUDY/results

if [[ -f $PID_FILE ]]; then
  OLD_PID=$(<$PID_FILE)
  if kill -0 $OLD_PID 2>/dev/null; then
    print "final regression is already running as pid $OLD_PID"
    exit 0
  fi
fi
print $$ > $PID_FILE
trap 'rm -f "$PID_FILE"' EXIT INT TERM

log() {
  print "$(date '+%F %T %Z') $1" | tee -a $BATCH_LOG
}

wait_if_paused() {
  while [[ -f $STATE/PAUSE ]]; do
    log "PAUSE marker present; waiting 30 seconds"
    sleep 30
  done
}

run_suite() {
  local suite=$1
  local execution_root=$2
  local manifest=$STUDY/runs/$suite/study.json
  local attempt=0

  while ! $PY $STUDY/scripts/check_complete.py $manifest >> $BATCH_LOG 2>&1; do
    wait_if_paused
    attempt=$((attempt + 1))
    if (( attempt > 3 )); then
      log "$suite remains incomplete after 3 launcher attempts; stopping for investigation"
      return 1
    fi
    log "$suite attempt=$attempt starting"
    (
      cd $execution_root
      $PY -u -m research._framework.run_study $manifest \
        --capture-log $STUDY/runs/$suite/results/capture_log.jsonl
    ) >> $STUDY/runs/$suite/results/run_stdout.log 2>&1
    local exit_code=$?
    log "$suite attempt=$attempt runner_exit=$exit_code"
  done
  log "$suite complete"
}

if [[ ! -f $STATE/offline.ok ]]; then
  log "offline regression starting"
  (
    cd $REPO
    $PY backend/tests/regression/run_all.py
  ) > $STUDY/results/offline_regression.log 2>&1
  if [[ $? -ne 0 ]]; then
    log "offline regression FAILED; live run not started"
    exit 1
  fi
  touch $STATE/offline.ok
  log "offline regression passed"
fi

# Historical order retained for comparability. H2b is run for regression
# evidence, but the withdrawn B4 metric is not calculated.
for suite in h3_safety h1_banking77 h2b_journeys h4_controls; do
  run_suite $suite $STUDY/isolated_main_root || exit 1
done

run_suite c4_cache $STUDY/isolated_c4_root || exit 1

(
  cd $REPO
  $PY $STUDY/scripts/score_c4.py
) >> $STUDY/runs/c4_cache/results/run_stdout.log 2>&1
if [[ $? -ne 0 ]]; then
  log "C4 scoring failed"
  exit 1
fi

touch $STATE/live.complete
log "all live suites and C4 deterministic scoring complete"
