"""Run a manifest-driven, production-isolated graph validation study.

Usage:
    python3 -m research._framework.run_study research/YYYY-MM-DD_study/study.json
"""
from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

from research._framework.common import (
    ApiKeyPool,
    StudyConfig,
    find_completed_case_ids,
    install_dedicated_capture_log,
    load_cases,
    run_api_study,
    run_graph_study,
)


def main() -> None:
    parser = argparse.ArgumentParser(description="Run a manifest-driven Horizon research study.")
    parser.add_argument("manifest", type=Path, help="Path to a study.json manifest")
    parser.add_argument("--dry-run", action="store_true", help="Validate manifest and fixture without invoking models")
    parser.add_argument(
        "--capture-log",
        type=Path,
        default=None,
        help=(
            "Write full per-case detail (raw prompts/responses/reasoning) to this dedicated, "
            "uncapped file instead of relying on the production system.jsonl -- that file's "
            "rotating 60MB retention cannot survive a long or high-volume run (confirmed live: "
            "it fully cycles in under an hour of real activity), silently losing detail for any "
            "case whose events get evicted before this reads them back."
        ),
    )
    args = parser.parse_args()
    manifest = args.manifest.resolve()
    config = StudyConfig.from_manifest(manifest)
    cases = load_cases(config.fixture_path)
    if args.dry_run:
        print(f"Valid: study={config.study_id} runner={config.runner} cases={len(cases)} fixture={config.fixture_path}")
        return
    key_pool = ApiKeyPool()
    if args.capture_log:
        install_dedicated_capture_log(args.capture_log)
    results_dir = manifest.parent / "results"
    # Resume by default: if this exact study was already run (even partially,
    # e.g. interrupted by a hang) against this same results_dir, don't redo
    # cases that already produced a genuine, fully-detailed OK record for
    # THIS study_id specifically -- require_study_id keeps this from ever
    # matching a different study's case_ids that happen to overlap (e.g. a
    # detail backfill re-running the same case_ids an earlier, detail-missing
    # campaign already completed), and require_full_detail=True only matters
    # when capture_log is in use, since that's the one case where an "OK"
    # record could still be missing the thing this run exists to produce.
    skip_case_ids = find_completed_case_ids(
        results_dir, "", require_study_id=config.study_id, require_full_detail=bool(args.capture_log)
    )
    if skip_case_ids:
        print(f"Resuming: skipping {len(skip_case_ids)} case(s) already completed in a prior attempt of this study.")
    runner_fn = run_api_study if config.runner == "api_validation" else run_graph_study
    run = asyncio.run(
        runner_fn(
            config, cases, results_dir, key_pool=key_pool, skip_case_ids=skip_case_ids, capture_log_path=args.capture_log
        )
    )
    print(f"Completed run {run['run_id']}")
    print(f"Results: {run['results_path']}")
    print(f"Report: {run['report_path']}")


if __name__ == "__main__":
    main()
