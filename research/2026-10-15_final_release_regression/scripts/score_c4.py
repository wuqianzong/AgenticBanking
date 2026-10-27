"""Score C4 v1.1 directly from cache provenance; no model judge is used."""
from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path


D = Path(__file__).resolve().parents[1]
RUN = D / "runs/c4_cache"


def result_paths() -> list[Path]:
    paths = sorted((RUN / "results").glob("*_results.jsonl"), key=lambda path: path.stat().st_mtime)
    if not paths:
        raise RuntimeError("No live result file found")
    return paths


def approved(record: dict) -> set[str]:
    return {
        parent_id
        for turn in record.get("turns", [])
        for task in (turn.get("summary", {}).get("task_results", []) or [])
        if isinstance(task, dict)
        for parent_id in task.get("approved_parent_ids", [])
    }


def cache_events(record: dict) -> list[dict]:
    return [event for event in record.get("system_log_events", []) if event.get("event") == "task.rag.completed"]


def surfaced(record: dict) -> set[str]:
    return {
        parent_id
        for event in cache_events(record)
        for parent_ids in (event.get("cache_injected_parent_ids", {}) or {}).values()
        for parent_id in parent_ids
    }


def write_events(record: dict) -> list[dict]:
    return [event for event in record.get("system_log_events", []) if event.get("event") == "answer_cache.record_outcome.completed"]


paths = result_paths()
# The framework resumes into a new result file while skipping cases already
# completed in earlier files. Combine all attempts and keep the newest OK
# record per case so sleep/restart recovery does not make the scorer partial.
records_by_case = {}
for path in paths:
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        record = json.loads(line)
        if record.get("study_id") != "2026-10-15_final_release_c4_cache":
            continue
        previous = records_by_case.get(record["case_id"])
        if previous is None or record.get("status") == "OK":
            records_by_case[record["case_id"]] = record
records = list(records_by_case.values())
groups: dict[str, dict[int, dict]] = defaultdict(dict)
for record in records:
    case_id = record["case_id"]
    base, run_text = case_id.removeprefix("c4v11_").rsplit("_run", 1)
    groups[base][int(run_text)] = record

structural_errors = []
if len(groups) != 20 or len(records) != 60:
    structural_errors.append(f"expected 20 groups/60 cases, found {len(groups)} groups/{len(records)} cases")
for base, runs in groups.items():
    if set(runs) != {1, 2, 3}:
        structural_errors.append(f"{base}: runs present={sorted(runs)}")
    indices = [runs[n].get("case_index") for n in (1, 2, 3) if n in runs]
    if len(indices) == 3 and indices != list(range(indices[0], indices[0] + 3)):
        structural_errors.append(f"{base}: cases not consecutive: {indices}")
    for run, record in runs.items():
        if record.get("status") != "OK":
            structural_errors.append(f"{base} run{run}: status={record.get('status')}")

parent_hits = parent_opportunities = complete_hits = repeat_opportunities = 0
unscorable = []
per_question = {}
for base in sorted(groups):
    runs = groups[base]
    if set(runs) != {1, 2, 3}:
        continue
    expected = approved(runs[1])
    if not expected:
        unscorable.append(base)
        continue
    recorded = {pid for event in write_events(runs[1]) for pid in event.get("approved_parent_ids", [])}
    if not expected <= recorded:
        structural_errors.append(f"{base}: run1 approved parents were not confirmed by cache-write telemetry")
    detail = {"run1_approved_parent_ids": sorted(expected), "run1_cache_write_confirmed": expected <= recorded, "repeats": {}}
    for run in (2, 3):
        actual = surfaced(runs[run])
        hits = expected & actual
        extras = actual - expected
        parent_hits += len(hits)
        parent_opportunities += len(expected)
        repeat_opportunities += 1
        complete = hits == expected
        complete_hits += complete
        events = cache_events(runs[run])
        detail["repeats"][f"run{run}"] = {
            "lookup_queries": [event.get("cache_lookup_queries", {}) for event in events],
            "cache_hits": [event.get("cache_hits", {}) for event in events],
            "surfaced_parent_ids": sorted(actual),
            "expected_hits": sorted(hits),
            "missing_expected": sorted(expected - actual),
            "unexpected_extra": sorted(extras),
            "complete_parent_set_hit": complete,
        }
    per_question[base] = detail

valid = not structural_errors
live_primary = {
    "hits": parent_hits,
    "opportunities": parent_opportunities,
    "rate": parent_hits / parent_opportunities if parent_opportunities else None,
    "target": 1.0,
}
live_secondary = {
    "hits": complete_hits,
    "repeat_runs": repeat_opportunities,
    "rate": complete_hits / repeat_opportunities if repeat_opportunities else None,
}
live_decision = (
    "passed" if valid and parent_opportunities > 0 and parent_hits == parent_opportunities
    else ("invalid" if not valid else "not met")
)

configured_threshold = 0.65
primary = dict(live_primary)
primary["target"] = 1.0
secondary = live_secondary
decision = (
    "passed"
    if valid and primary.get("opportunities") and primary.get("rate") == 1.0
    else ("invalid" if not valid else "not met")
)
summary = {
    "metric": "C4_cache_parent_recall_v1.1",
    "source_results": [str(path.relative_to(D)) for path in paths],
    "valid_run": valid,
    "structural_errors": structural_errors,
    "questions_total": len(groups),
    "questions_scorable": len(per_question),
    "questions_unscorable_no_run1_parent": unscorable,
    "configured_similarity_threshold": configured_threshold,
    "evidence_mode": "live observation at the configured 0.65 threshold",
    "primary_parent_id_recall": primary,
    "secondary_complete_parent_set_recall": secondary,
    "decision": decision,
    "live_0_65_detail": {
        "primary_parent_id_recall": live_primary,
        "secondary_complete_parent_set_recall": live_secondary,
        "decision_at_0_85": live_decision,
        "per_question": per_question,
    },
}
(RUN / "results/c4_cache_summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

def percentage(value):
    return "n/a" if value is None else f"{value:.1%}"

report = f"""# C4 cache-parent recall v1.1 result

- Run valid: **{valid}**
- Scorable questions: **{len(per_question)}/{len(groups)}**
- Configured similarity threshold: **{configured_threshold:.2f}**
- Parent-ID recall: **{primary['hits']}/{primary['opportunities']} ({percentage(primary['rate'])})**
- Complete parent-set recall: **{secondary['hits']}/{secondary['repeat_runs']} ({percentage(secondary['rate'])})**
- Target: **100% parent-ID recall**
- Decision: **{decision.upper()}**
- Unscorable questions: {', '.join(unscorable) if unscorable else 'none'}
- Structural errors: {'; '.join(structural_errors) if structural_errors else 'none'}

This final release run measured the implemented 0.65 threshold live. It scores
only whether run-1 approved parent IDs surfaced before the downstream
sufficiency audit; downstream selection and answer wording do not affect C4.

See `c4_cache_summary.json` for per-question details.
"""
(RUN / "results/C4_CACHE_REPORT.md").write_text(report, encoding="utf-8")
print(report)
