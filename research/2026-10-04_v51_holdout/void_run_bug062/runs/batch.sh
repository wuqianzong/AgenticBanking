#!/bin/zsh
# v5.1-final live runs, in the design's order (Part I): planted-FAQ attempt 2, H3, H1, H2b, H4 controls, C4 repeats.
# Delivered defaults (Gemini 3.5 Flash-Lite, 10-key pool from research/.research_api_keys). No keys in this file.
P=/Users/bensonwu/miniconda3/envs/agentic_banking/bin/python
H=/Users/bensonwu/Projects/AgenticBanking/research/2026-10-04_v51_holdout
LOG=$H/runs/batch.done.log
cd $H/planted/root
$P -u -m research._framework.run_study ../v2/study.json --capture-log ../v2/results/capture_log.jsonl > ../v2/run_stdout.log 2>&1
echo "planted_v2 exit=$? $(date '+%F %T')" >> $LOG
cd /Users/bensonwu/Projects/AgenticBanking
for s in h3_safety h1_banking77 h2b_journeys h4_controls h4_c4_repeats; do
  $P -u -m research._framework.run_study $H/runs/$s/study.json --capture-log $H/runs/$s/results/capture_log.jsonl > $H/runs/$s/run_stdout.log 2>&1
  echo "$s exit=$? $(date '+%F %T')" >> $LOG
done
echo "batch finished $(date '+%F %T')" >> $LOG
