"""Run a manifest-driven, production-isolated graph validation study.

Usage:
    python3 -m research._framework.run_study research/YYYY-MM-DD_study/study.json
"""
from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

from research._framework.common import StudyConfig, load_cases, run_graph_study


def main() -> None:
    parser = argparse.ArgumentParser(description="Run a manifest-driven Horizon research study.")
    parser.add_argument("manifest", type=Path, help="Path to a study.json manifest")
    parser.add_argument("--dry-run", action="store_true", help="Validate manifest and fixture without invoking models")
    args = parser.parse_args()
    manifest = args.manifest.resolve()
    config = StudyConfig.from_manifest(manifest)
    cases = load_cases(config.fixture_path)
    if args.dry_run:
        print(f"Valid: study={config.study_id} runner={config.runner} cases={len(cases)} fixture={config.fixture_path}")
        return
    run = asyncio.run(run_graph_study(config, cases, manifest.parent / "results"))
    print(f"Completed run {run['run_id']}")
    print(f"Results: {run['results_path']}")
    print(f"Report: {run['report_path']}")


if __name__ == "__main__":
    main()
