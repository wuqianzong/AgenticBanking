#!/bin/zsh
# Re-run a resumable Labeller A judge until it exits cleanly; waits out quota exhaustion (10 min between attempts).
# usage: judge_loop.sh <script.py> [args...]
P=/Users/bensonwu/miniconda3/envs/agentic_banking/bin/python
cd /Users/bensonwu/Projects/AgenticBanking/research/2026-10-04_v51_holdout
until $P -u scripts/$@; do echo "retry after failure $(date '+%F %T')"; sleep 600; done
echo "done $* $(date '+%F %T')"
