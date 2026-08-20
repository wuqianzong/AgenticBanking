"""Test direct-result dispersion against frozen narrative-view gains.

This is deliberately an isolated research harness.  It reads the historical
generic child artifact and canonical taxonomy, embeds and ranks in memory, and
writes only research results.  It does not open or modify the production
vector store, knowledge database, or application code.

The fixture is target-anchored synthetic data: its expected parent is known,
but its messages were generated from reviewed anchor queries.  Results are
therefore a diagnostic of the hypothesis, not a real-traffic estimate.
"""

from __future__ import annotations

import json
import sqlite3
import argparse
from collections import defaultdict
from pathlib import Path

import numpy as np
from chromadb.utils import embedding_functions
from scipy.stats import pointbiserialr, spearmanr


PROJECT_ROOT = Path(__file__).resolve().parents[3]
RESEARCH_ROOT = Path(__file__).resolve().parents[1]
FIXTURE = RESEARCH_ROOT / "fixtures" / "retrieval_query_strategy_eval_v1.json"
RESULTS = RESEARCH_ROOT / "results" / "target_anchored_dispersion_diagnostic_v1.json"
CHILDREN = PROJECT_ROOT / "backend" / "data" / "etl" / "history" / "generic_chunking" / "artifacts" / "ready_for_ingestion.json"
KNOWLEDGE_DB = PROJECT_ROOT / "backend" / "data" / "knowledge_base.db"
RRF_K, TOP_K, MAX_PARENTS = 60, 8, 4


def scope_key(scope_pairs):
    return json.dumps(scope_pairs, sort_keys=True, separators=(",", ":"))


def load_indices(cases, embedder, children_path, knowledge_db):
    connection = sqlite3.connect(knowledge_db)
    try:
        rows = connection.execute("SELECT parent_id, product_tags, intent_families FROM knowledge_parents").fetchall()
    finally:
        connection.close()
    taxonomy = {pid: (json.loads(products), json.loads(intents)) for pid, products, intents in rows}
    wanted = {scope_key(case["scope_pairs"]): case["scope_pairs"] for case in cases}
    scoped = {key: [] for key in wanted}
    seen = set()
    for child in json.loads(children_path.read_text())["child_data"]:
        metadata, parent_id = child.get("metadata", {}), child.get("metadata", {}).get("parent_id")
        if parent_id not in taxonomy:
            continue
        dedupe = (parent_id, child.get("text", ""), metadata.get("chunk_type", "generic"))
        if dedupe in seen:
            continue
        seen.add(dedupe)
        products, intents = taxonomy[parent_id]
        for key, pairs in wanted.items():
            if any(pair["product"] in products and intent in pair["intents"] for pair in pairs for intent in intents):
                scoped[key].append((parent_id, child["text"]))
    indices = {}
    for key, docs in scoped.items():
        if not docs:
            raise ValueError(f"No child documents for {key}")
        indices[key] = {"parent_ids": [item[0] for item in docs], "vectors": np.asarray(embedder([item[1] for item in docs]), dtype=float)}
    return indices


def retrieve(index, views, embedder):
    rrf, direct_scores = defaultdict(float), {}
    for view_index, query in enumerate(views):
        q = np.asarray(embedder([query])[0], dtype=float)
        scores = index["vectors"] @ q / (np.linalg.norm(index["vectors"], axis=1) * np.linalg.norm(q))
        seen = set()
        for rank, idx in enumerate(np.argsort(-scores, kind="stable")[:TOP_K], 1):
            parent_id, cosine = index["parent_ids"][int(idx)], float(scores[int(idx)])
            if view_index == 0:
                direct_scores[parent_id] = max(direct_scores.get(parent_id, cosine), cosine)
            if parent_id not in seen:
                seen.add(parent_id)
                rrf[parent_id] += 1 / (RRF_K + rank)
    selected, prior = [], None
    for parent_id, score in sorted(rrf.items(), key=lambda item: item[1], reverse=True):
        if len(selected) == MAX_PARENTS or (prior is not None and score / prior < .5):
            break
        selected.append(parent_id)
        prior = score
    selected_scores = [direct_scores[parent_id] for parent_id in selected if parent_id in direct_scores]
    return {"selected_parent_ids": selected, "parent_dispersion": float(np.std(selected_scores)) if len(selected_scores) > 1 else None}


def rank(selected, expected):
    return selected.index(expected) + 1 if expected in selected else None


def reciprocal(value):
    return 0 if value is None else 1 / value


def corr(x, y, method):
    statistic, pvalue = method(x, y)
    return {"coefficient": float(statistic), "p_value": float(pvalue)}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--children", type=Path, default=CHILDREN)
    parser.add_argument("--knowledge-db", type=Path, default=KNOWLEDGE_DB)
    args = parser.parse_args()
    cases = json.loads(FIXTURE.read_text())
    embedder = embedding_functions.DefaultEmbeddingFunction()
    indices = load_indices(cases, embedder, args.children, args.knowledge_db)
    outputs = []
    for number, case in enumerate(cases, 1):
        index = indices[scope_key(case["scope_pairs"])]
        direct = retrieve(index, [case["customer_query"]], embedder)
        direct_rank = rank(direct["selected_parent_ids"], case["expected_parent_id"])
        angles = case["angle_groups"][0]
        angle_results = {}
        for angle in ("procedural", "policy", "conceptual"):
            result = retrieve(index, [case["customer_query"], angles[angle]], embedder)
            result["expected_rank"] = rank(result["selected_parent_ids"], case["expected_parent_id"])
            result["mrr_delta"] = reciprocal(result["expected_rank"]) - reciprocal(direct_rank)
            angle_results[angle] = result
        outputs.append({"id": case["id"], "expected_parent_id": case["expected_parent_id"], "direct_parent_dispersion": direct["parent_dispersion"], "direct": {**direct, "expected_rank": direct_rank}, "single_angle": angle_results})
        print(f"Retrieved {number}/{len(cases)}: {case['id']}")
    valid = [case for case in outputs if case["direct_parent_dispersion"] is not None]
    dispersion = [case["direct_parent_dispersion"] for case in valid]
    best_gain = [max(result["mrr_delta"] for result in case["single_angle"].values()) for case in valid]
    any_help = [gain > 0 for gain in best_gain]
    per_angle = {}
    for angle in ("procedural", "policy", "conceptual"):
        gains = [case["single_angle"][angle]["mrr_delta"] for case in valid]
        per_angle[angle] = {
            "mean_mrr_delta": float(np.mean(gains)),
            "improved_count": sum(gain > 0 for gain in gains),
            "regressed_count": sum(gain < 0 for gain in gains),
            "spearman_dispersion_vs_gain": corr(dispersion, gains, spearmanr),
        }
    q1, q3 = np.quantile(dispersion, [.25, .75])
    low, high = [gain for d, gain in zip(dispersion, best_gain) if d <= q1], [gain for d, gain in zip(dispersion, best_gain) if d >= q3]
    output = {
        "metadata": {"purpose": "Target-anchored synthetic diagnostic of direct parent-score dispersion and fixed narrative gains", "fixture": str(FIXTURE), "isolation": "in-memory exact cosine ranking; no production writes", "important_limit": "Synthetic variants share 20 reviewed targets and likely inflate direct retrieval; do not interpret as independent or production evidence."},
        "cases": outputs,
        "summary": {"case_count": len(outputs), "unique_target_count": len({case["expected_parent_id"] for case in outputs}), "valid_dispersion_count": len(valid), "best_of_three_oracle": {"mean_mrr_delta": float(np.mean(best_gain)), "improved_count": sum(any_help), "spearman_dispersion_vs_best_gain": corr(dispersion, best_gain, spearmanr), "point_biserial_dispersion_vs_any_improvement": corr(dispersion, any_help, pointbiserialr), "low_quartile": {"maximum_dispersion": float(q1), "count": len(low), "mean_best_mrr_delta": float(np.mean(low)), "improved_count": sum(gain > 0 for gain in low)}, "high_quartile": {"minimum_dispersion": float(q3), "count": len(high), "mean_best_mrr_delta": float(np.mean(high)), "improved_count": sum(gain > 0 for gain in high)}}, "per_single_angle": per_angle}
    }
    RESULTS.write_text(json.dumps(output, indent=2))
    print(json.dumps(output["summary"], indent=2))


if __name__ == "__main__":
    main()
