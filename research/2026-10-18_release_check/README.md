# Release check, 18 Oct 2026

Pass/fail check of the frozen code (commit 45329fe) before writing the report and deck. Not a metrics run.

- Offline: 17/17 suites pass (`results/offline_regression.log`).
- Live: 24 conversations on a fresh copy of the live data with an empty answer cache. Covers the demo script
  (8, from `2026-10-03_demo_rehearsal`), BUG-073 and three BUG-070 cases, all five B6 handover requests
  (including BUG-071 "Transfer me to customer service."), five attacks and two typed credentials.
- Gates (`scripts/compare.py`, `results/compare_stdout.txt`): all pass. 24/24 completed; 0 unparseable in 157
  Gemini replies; no R4 leaks or R2a hits; credentials blocked before any model; no exhausted revision loop;
  5/5 handover.
- Direct review: every reply correct and safe. Minor notes: the lost-card reply repeats the freeze steps from two
  FAQ entries; the tool-listing probe got a vague deflection with nothing revealed; "What is PayNow?" got
  no-information this time (it was answered on 17 Oct): run-to-run variation.
- The FAQ data contains two hotline numbers (+65 3138 7878, +65 3134 7469). The owner accepts the bot quoting
  whichever entry it uses.

Rebuild: `python scripts/build.py`, then `nohup ./run.sh >> launcher.out 2>&1 &`.
