"""Create a frozen, query-only evaluation set for retrieval-strategy A/B tests.

The source FAQ answer is deliberately not supplied to the generator.  It sees
only the already-reviewed benchmark query and its Product×Intent boundary,
then creates three independently worded customer messages and their matching
single-clean-query / three-angle formulations.  A human should review the
result before treating it as a production evaluation corpus.
"""

import argparse
import json
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[3]
RESEARCH_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.app.core.llm_service import MODEL_A_GENERATOR, call_llm_router
from benchmark_retrieval import CASES


STYLES = ("procedural_dominant", "policy_dominant", "indirect_conceptual")


def _seed_payload(cases: list[dict]) -> list[dict]:
    return [
        {
            "id": case["id"],
            "customer_query": case["customer_query"],
            "expected_parent_id": case["expected_parent_id"],
            "scope_pairs": case["scope_pairs"],
        }
        for case in cases
    ]


def _prompt(seeds: list[dict]) -> str:
    return f"""
You are constructing a retrieval evaluation set. Return JSON only: a JSON array.

For EACH seed, create exactly three variants, one for each style:
{list(STYLES)}.

Rules:
- A variant must request the SAME answerable banking information as its seed.
- Copy expected_parent_id and scope_pairs exactly. Do not infer any answer or
  add banking facts that are not in the seed customer_query.
- customer_query must sound like a plausible human message. The style should
  influence which aspect is foregrounded, but must not change the request.
- direct_query must be one concise, neutral, noise-reduced query covering the
  full request. It is a single-vector baseline, not a list.
- angle_groups must contain exactly one base group with concise procedural,
  policy, and conceptual comma-separated retrieval queries. Together they
  cover the same request as direct_query; no angle may introduce a new topic.
- Do not copy the seed wording verbatim.
- id must be the seed id followed by an underscore and the exact style name.

Every output object must have exactly these keys:
id, customer_query, direct_query, expected_parent_id, scope_pairs, angle_groups

The required angle_groups value is a JSON array containing one JSON object,
for example:
"angle_groups": [{{"group_id": "base", "procedural": "...", "policy": "...", "conceptual": "..."}}]

SEEDS:
{json.dumps(seeds, ensure_ascii=False)}
"""


def _validate(items: list[dict], seeds: list[dict]) -> list[dict]:
    expected = {f"{seed['id']}_{style}" for seed in seeds for style in STYLES}
    by_id = {item.get("id"): item for item in items if isinstance(item, dict)}
    if set(by_id) != expected:
        missing = expected.difference(by_id)
        unexpected = set(by_id).difference(expected)
        raise ValueError(f"Unexpected generated IDs; missing={sorted(missing)}, unexpected={sorted(unexpected)}")
    for seed in seeds:
        for style in STYLES:
            item = by_id[f"{seed['id']}_{style}"]
            if item.get("expected_parent_id") != seed["expected_parent_id"]:
                raise ValueError(f"{item['id']} changed expected_parent_id")
            if item.get("scope_pairs") != seed["scope_pairs"]:
                raise ValueError(f"{item['id']} changed scope_pairs")
            if not isinstance(item.get("direct_query"), str) or not item["direct_query"].strip():
                raise ValueError(f"{item['id']} has no direct_query")
            groups = item.get("angle_groups")
            if not isinstance(groups, list) or len(groups) != 1:
                raise ValueError(f"{item['id']} must have one angle group")
            group = groups[0]
            if not isinstance(group, dict):
                raise ValueError(f"{item['id']} angle group must be an object")
            if not all(isinstance(group.get(key), str) and group[key].strip() for key in ("procedural", "policy", "conceptual")):
                raise ValueError(f"{item['id']} has incomplete angle group")
    return [by_id[item_id] for item_id in sorted(expected)]


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate frozen retrieval evaluation variants.")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--batch-size", type=int, default=5)
    parser.add_argument("--max-attempts", type=int, default=3)
    parser.add_argument("--model", default=MODEL_A_GENERATOR)
    args = parser.parse_args()

    all_items = []
    for start in range(0, len(CASES), args.batch_size):
        seeds = CASES[start:start + args.batch_size]
        last_error = None
        for attempt in range(1, args.max_attempts + 1):
            try:
                raw = call_llm_router(_prompt(_seed_payload(seeds)), model_id=args.model)
                generated = json.loads(raw)
                if not isinstance(generated, list):
                    raise ValueError("Generator did not return a JSON array.")
                all_items.extend(_validate(generated, seeds))
                break
            except (json.JSONDecodeError, ValueError) as error:
                last_error = error
                print(f"Batch {start // args.batch_size + 1} attempt {attempt} rejected: {error}")
        else:
            raise RuntimeError(f"Could not generate a valid batch after {args.max_attempts} attempts: {last_error}")
        print(f"Generated {min(start + args.batch_size, len(CASES))}/{len(CASES)} anchor groups.")

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(all_items, indent=2), encoding="utf-8")
    print(f"Wrote {len(all_items)} variants to {args.output}")


if __name__ == "__main__":
    main()
