"""Create the standard directory and manifest skeleton for a new study.

Usage:
    python3 -m research._framework.create_study 2026-09-05_example_study
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from research._framework.common import REPO_ROOT


def main() -> None:
    parser = argparse.ArgumentParser(description="Create a standard research-study skeleton.")
    parser.add_argument("study_id", help="Dated snake/kebab-case study directory name")
    args = parser.parse_args()
    root = REPO_ROOT / "research" / args.study_id
    if root.exists():
        raise SystemExit(f"Refusing to overwrite existing study directory: {root}")
    for name in ("data", "fixtures", "scripts", "results", "reports"):
        (root / name).mkdir(parents=True, exist_ok=True)
    manifest = {
        "study_id": args.study_id,
        "runner": "graph_validation",
        "fixture": "fixtures/cases_v1.jsonl",
        "user_id": "USR_999",
        "pacing_seconds": 25,
        "max_retries_per_turn": 5,
        "default_rate_limit_wait_seconds": 65,
        "capture_system_log": True,
        "notes": "Direct compiled-graph run; does not archive chat history or memory.",
    }
    (root / "study.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    (root / "fixtures" / "cases_v1.jsonl").write_text(
        json.dumps({
            "case_id": "replace_me_001",
            "title": "Replace with a study case",
            "messages": ["Replace this with a real test query."],
            "tags": ["smoke"],
            "expected": {"must_not_emit": ["raw_route", "destination_id", "unresolved_placeholder"]},
            "review_rubric": ["answer_relevance", "evidence_groundedness", "naturalness"],
        }) + "\n",
        encoding="utf-8",
    )
    (root / "reports" / "RESEARCH_SUMMARY.md").write_text(
        f"# Research Summary: {args.study_id}\n\n"
        "## Hypothesis\n\nDescribe the hypothesis before running the study.\n\n"
        "## Method\n\nUse `study.json`, preserve the generated result artifacts, and record limitations.\n\n"
        "## Findings\n\nPopulate after source/rubric review.\n",
        encoding="utf-8",
    )
    print(f"Created {root}")
    print(f"Next: edit {root / 'fixtures' / 'cases_v1.jsonl'} then run:")
    print(f"python3 -m research._framework.run_study {root / 'study.json'} --dry-run")


if __name__ == "__main__":
    main()
