"""Retrieval-only ablation of the saved three-way narrative variants.

This diagnostic reuses the blind evaluation's frozen cases and generated
variants.  It does not call an LLM or modify production retrieval assets.
Every arm uses the same RRF and parent-selection contract so that the only
variable is which standalone narrative views are included.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[3]
RESEARCH_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from benchmark_retrieval import _get_or_build_generic_collection, _run_query_view_rrf_case
from backend.app.orchestration.rag_engine import RAGEngine


DEFAULT_INPUT = RESEARCH_ROOT / "results" / "threeway_narrative_validation_v1.json"
DEFAULT_OUTPUT = RESEARCH_ROOT / "results" / "threeway_angle_ablation_v1.json"


def _rank(parent_ids: list[str], expected_parent_id: str) -> int | None:
    try:
        return parent_ids.index(expected_parent_id) + 1
    except ValueError:
        return None


async def _run(input_path: Path, output_path: Path) -> dict:
    source = json.loads(input_path.read_text(encoding="utf-8"))
    engine = RAGEngine(collection=_get_or_build_generic_collection(False))
    rows = []

    for index, case in enumerate(source["cases"], start=1):
        anchor = case["customer_query"]
        variants = source["angles"][case["id"]]
        views = {
            "direct_anchor": [anchor],
            "direct_plus_procedural": [anchor, variants["procedural"]],
            "direct_plus_policy": [anchor, variants["policy"]],
            "direct_plus_conceptual": [anchor, variants["conceptual"]],
            "direct_plus_all_three": [
                anchor,
                variants["procedural"],
                variants["policy"],
                variants["conceptual"],
            ],
        }
        outcomes = await asyncio.gather(*[
            _run_query_view_rrf_case(engine, case, query_views)
            for query_views in views.values()
        ])
        strategies = {
            name: {
                "selected_parent_ids": outcome["selected_parent_ids"],
                "expected_parent_rank": outcome["expected_parent_rank"],
                "passed": outcome["passed"],
            }
            for name, outcome in zip(views, outcomes)
        }
        rows.append({
            "id": case["id"],
            "source_intent": case["source_intent"],
            "customer_query": anchor,
            "expected_parent_id": case["expected_parent_id"],
            "strategies": strategies,
        })
        print(f"Ablated {index}/{len(source['cases'])}: {case['id']}")

    output = {
        "metadata": {
            "purpose": "retrieval-only angle ablation using frozen narrative variants",
            "selection_contract": "RRF with identical top-four and 50% drop-off rules for every arm",
            "important_limit": "expected_parent_id is a diagnostic proxy, not the sole acceptable-answer definition",
        },
        "cases": rows,
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(output, indent=2), encoding="utf-8")
    return output


def _summarize(output: dict) -> None:
    names = tuple(output["cases"][0]["strategies"])
    baseline = "direct_anchor"
    for name in names:
        ranks = [case["strategies"][name]["expected_parent_rank"] for case in output["cases"]]
        found = [rank for rank in ranks if rank is not None]
        print(
            f"{name}: recall@4={len(found)}/{len(ranks)} "
            f"mrr@4={sum(1 / rank for rank in found) / len(ranks):.4f}"
        )
    for name in names:
        if name == baseline:
            continue
        outcomes = Counter()
        categories = defaultdict(Counter)
        for case in output["cases"]:
            before = case["strategies"][baseline]["expected_parent_rank"] or 99
            after = case["strategies"][name]["expected_parent_rank"] or 99
            direction = "improve" if after < before else "regress" if after > before else "tie"
            outcomes[direction] += 1
            categories[case["source_intent"]][direction] += 1
        print(f"{baseline} -> {name}: {dict(outcomes)}")
        print(f"  by_category={json.dumps({key: dict(value) for key, value in categories.items()}, sort_keys=True)}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    output = asyncio.run(_run(args.input, args.output))
    _summarize(output)


if __name__ == "__main__":
    main()
