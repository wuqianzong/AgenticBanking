"""Render anonymized parent-text sets for manual retrieval relevance review."""

import hashlib
import json
import sqlite3
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[3]
RESEARCH_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_BENCHMARK = RESEARCH_ROOT / "results" / "blind_query_strategy_benchmark_v1.json"
DEFAULT_REPORT = RESEARCH_ROOT / "results" / "blind_query_strategy_review_v1.md"
DEFAULT_MAPPING = RESEARCH_ROOT / "results" / "blind_query_strategy_mapping_v1.json"
DB_PATH = PROJECT_ROOT / "backend" / "data" / "knowledge_base.db"


def _content_for(parent_ids: list[str]) -> dict[str, str]:
    connection = sqlite3.connect(DB_PATH)
    try:
        if not parent_ids:
            return {}
        placeholders = ",".join("?" for _ in parent_ids)
        rows = connection.execute(
            f"SELECT parent_id, content FROM knowledge_parents WHERE parent_id IN ({placeholders})",
            parent_ids,
        ).fetchall()
    finally:
        connection.close()
    return dict(rows)


def _format_set(parent_ids: list[str], content: dict[str, str]) -> str:
    if not parent_ids:
        return "_No candidates returned._\n"
    blocks = []
    for rank, parent_id in enumerate(parent_ids, start=1):
        text = content.get(parent_id, "Parent text was not found.")
        blocks.append(f"#### Rank {rank}\n\n{text}\n")
    return "\n".join(blocks)


def main() -> None:
    benchmark_path = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_BENCHMARK
    benchmark = json.loads(benchmark_path.read_text(encoding="utf-8"))
    strategies = benchmark["runs"]["generic_query_strategies"]
    combined = {item["id"]: item for item in strategies["combined_clean_query"]["results"]}
    narrative = {item["id"]: item for item in strategies["narrative_three_query_rrf"]["results"]}

    all_ids = [
        parent_id
        for result in list(combined.values()) + list(narrative.values())
        for parent_id in result["selected_parent_ids"]
    ]
    content = _content_for(list(dict.fromkeys(all_ids)))
    lines = [
        "# Blinded Retrieval Candidate Review v1",
        "",
        "For each customer message, score each candidate set before consulting the mapping file.",
        "Suggested score: 0 = no useful FAQ; 1 = partial/helpful; 2 = directly answers the request.",
        "The set labels A/B are randomized per case. Do not infer their method from formatting.",
        "This fixture contains 20 FAQ-blind banking scenarios expressed in four linguistic framings each. It tests wording/noise robustness; it is not 80 independent semantic topics.",
        "",
    ]
    mapping = {}
    for case_id in sorted(combined):
        a_is_combined = int(hashlib.sha256(case_id.encode()).hexdigest(), 16) % 2 == 0
        a_result, b_result = (combined[case_id], narrative[case_id]) if a_is_combined else (narrative[case_id], combined[case_id])
        mapping[case_id] = {"A": "combined_clean_query" if a_is_combined else "narrative_three_query_rrf", "B": "narrative_three_query_rrf" if a_is_combined else "combined_clean_query"}
        lines.extend([
            f"## {case_id}",
            "",
            f"**Customer message:** {combined[case_id]['customer_query']}",
            "",
            "### Candidate set A",
            "",
            _format_set(a_result["selected_parent_ids"], content),
            "### Candidate set B",
            "",
            _format_set(b_result["selected_parent_ids"], content),
        ])
    DEFAULT_REPORT.write_text("\n".join(lines), encoding="utf-8")
    DEFAULT_MAPPING.write_text(json.dumps(mapping, indent=2), encoding="utf-8")
    print(f"Wrote blinded report: {DEFAULT_REPORT}")
    print(f"Wrote method mapping: {DEFAULT_MAPPING}")


if __name__ == "__main__":
    main()
