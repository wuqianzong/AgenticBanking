#!/bin/zsh
# Demo user-story check. Resumable: run_study skips cases already OK for this study_id and is re-run until
# check_complete.py confirms every case is OK. Story 2 runs first, so its login reminder sees only its own request.
# Launch detached:  nohup ./run.sh >> launcher.out 2>&1 &
set -u
D=/Users/bensonwu/Projects/AgenticBanking/research/2026-10-23_demo_story_check
PY=/Users/bensonwu/miniconda3/envs/agentic_banking/bin/python
CHECK=/Users/bensonwu/Projects/AgenticBanking/research/2026-10-15_final_release_regression/scripts/check_complete.py
print $$ > $D/launcher.pid
print "$(date '+%T') code at $(git -C $D/../.. rev-parse --short HEAD); backend changes: $(git -C $D/../.. status --short backend/app | wc -l | tr -d ' ')"
if [[ ! -s $D/runs/story2/results/story2_results.jsonl ]]; then
  print "$(date '+%T') story2 start"
  (cd $D/isolated_root && $PY -u $D/scripts/story2_flow.py) >> $D/runs/story2/results/run_stdout.log 2>&1
  print "$(date '+%T') story2 exit=$?"
fi
attempt=0
while ! $PY $CHECK $D/runs/stories/study.json >> $D/launcher.out 2>&1; do
  attempt=$((attempt + 1))
  if (( attempt > 5 )); then print "$(date '+%T') stories incomplete after 5 attempts; stopping"; break; fi
  print "$(date '+%T') stories attempt=$attempt start"
  (cd $D/isolated_root && $PY -u -m research._framework.run_study $D/runs/stories/study.json \
     --capture-log $D/runs/stories/results/capture_log.jsonl) >> $D/runs/stories/results/run_stdout.log 2>&1
  print "$(date '+%T') stories attempt=$attempt exit=$?"
done
$PY $D/scripts/check.py > $D/results/check_stdout.txt 2>&1
print "$(date '+%T') all done; see results/CHECK_REPORT.md"
touch $D/DONE
