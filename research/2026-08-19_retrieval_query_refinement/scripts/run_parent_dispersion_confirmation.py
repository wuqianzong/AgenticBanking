"""Confirm the direct-parent-dispersion bridge trigger on unused BANKING77 rows.

This harness is isolated research code.  It creates an in-memory Chroma
collection from the historical generic child artifact and reads SQLite only for
canonical metadata.  It never writes to the production Chroma collection,
SQLite knowledge base, or application code.

The test deliberately reuses the eight previously reviewed BANKING77-to-
Horizon mappings, but excludes every Banking77 message already present in a
research fixture or result artifact.  It therefore validates fresh language
within these eight needs, not generalisation to new FAQ targets.
"""

from __future__ import annotations

import csv
import json
import random
import sqlite3
from collections import defaultdict
from pathlib import Path
from typing import Any

import numpy as np
from chromadb.utils import embedding_functions

from build_banking77_horizon_eval import MAPPINGS


PROJECT_ROOT = Path(__file__).resolve().parents[3]
RESEARCH_ROOT = Path(__file__).resolve().parents[1]
TEST_DATA = RESEARCH_ROOT / "data" / "banking77_test.csv"
FIXTURE = RESEARCH_ROOT / "fixtures" / "banking77_parent_dispersion_confirmation_v1.json"
RESULTS = RESEARCH_ROOT / "results" / "banking77_parent_dispersion_confirmation_v1.json"
HISTORICAL_CHILDREN = PROJECT_ROOT / "backend" / "data" / "etl" / "history" / "generic_chunking" / "artifacts" / "ready_for_ingestion.json"
KNOWLEDGE_DB = PROJECT_ROOT / "backend" / "data" / "knowledge_base.db"

SAMPLE_SEED = 20260822
SAMPLE_PER_CATEGORY = 15
# Locked from the discovery diagnostic's lower parent-dispersion quartile.
DISPERSION_THRESHOLD = 0.038
RRF_K = 60
TOP_K = 8
MAX_PARENTS = 4


# The bridge router is frozen before selecting fresh BANKING77 rows.  It is a
# deterministic semantic proxy, not a claim that the source label is available
# at runtime.  A future production evaluation must replace this with a
# task-formulation router that sees only the resolved customer task.
BRIDGE_ANGLE = {
    "activate_my_card": "procedural",
    "change_pin": "procedural",
    "card_arrival": "policy",
    "lost_or_stolen_card": "procedural",
    "transfer_timing": "policy",
    "cancel_transfer": "policy",
    "transfer_into_account": "procedural",
    "passcode_forgotten": "procedural",
}


def _all_prior_banking77_queries(rows_by_text: dict[str, str]) -> set[str]:
    """Find exact BANKING77 messages previously used by any research JSON."""
    used: set[str] = set()

    def walk(value: Any) -> None:
        if isinstance(value, dict):
            query = value.get("customer_query")
            if isinstance(query, str) and query.strip() in rows_by_text:
                used.add(query.strip())
            for child in value.values():
                walk(child)
        elif isinstance(value, list):
            for child in value:
                walk(child)

    for directory in (RESEARCH_ROOT / "fixtures", RESEARCH_ROOT / "results"):
        for path in directory.glob("*.json"):
            if path in {FIXTURE, RESULTS}:
                continue
            try:
                walk(json.loads(path.read_text(encoding="utf-8")))
            except json.JSONDecodeError:
                continue
    return used


def _build_fixture() -> list[dict[str, Any]]:
    with TEST_DATA.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    rows_by_text = {row["text"].strip(): row["category"] for row in rows}
    used = _all_prior_banking77_queries(rows_by_text)
    rng = random.Random(SAMPLE_SEED)
    cases = []
    for category, mapping in MAPPINGS.items():
        available = [
            row["text"].strip()
            for row in rows
            if row["category"] == category and row["text"].strip() not in used
        ]
        if len(available) < SAMPLE_PER_CATEGORY:
            raise ValueError(f"{category}: expected {SAMPLE_PER_CATEGORY} unused rows, found {len(available)}")
        for number, query in enumerate(rng.sample(available, SAMPLE_PER_CATEGORY), start=1):
            angle = BRIDGE_ANGLE[category]
            narrative_index = {"procedural": 0, "policy": 1, "conceptual": 2}[angle]
            cases.append({
                "id": f"parent_dispersion_banking77_{category}_{number:02d}",
                "source": "PolyAI BANKING77 test split (CC BY 4.0)",
                "source_intent": category,
                "customer_query": query,
                "scope_pairs": mapping["scope_pairs"],
                "acceptable_parent_ids": mapping["acceptable_parent_ids"],
                "bridge_angle": angle,
                "bridge_query": mapping["narratives"][narrative_index],
            })
    FIXTURE.write_text(json.dumps({
        "metadata": {
            "purpose": "Unused BANKING77 language confirmation for the parent-dispersion bridge trigger",
            "sample_seed": SAMPLE_SEED,
            "sample_per_category": SAMPLE_PER_CATEGORY,
            "excluded_prior_banking77_messages": len(used),
            "dispersion_threshold": DISPERSION_THRESHOLD,
            "bridge_router": "fixed deterministic semantic proxy documented in run_parent_dispersion_confirmation.py",
        },
        "cases": cases,
    }, indent=2), encoding="utf-8")
    return cases


def _load_taxonomy() -> dict[str, tuple[list[str], list[str]]]:
    connection = sqlite3.connect(KNOWLEDGE_DB)
    try:
        rows = connection.execute(
            "SELECT parent_id, product_tags, intent_families FROM knowledge_parents"
        ).fetchall()
    finally:
        connection.close()
    return {parent_id: (json.loads(products), json.loads(intents)) for parent_id, products, intents in rows}


def _scope_key(scope_pairs: list[dict]) -> str:
    return json.dumps(scope_pairs, sort_keys=True, separators=(",", ":"))


def _build_scope_documents(cases: list[dict]) -> dict[str, list[dict]]:
    """Build only the small metadata-filtered corpora used by this experiment.

    The original benchmark's full temporary Chroma collection is unnecessarily
    large for this isolated confirmation.  Exact cosine ranking over these
    small per-scope child sets preserves the same embedding, metadata lanes,
    candidate depth, and parent-level RRF contract without opening any
    persistent vector database.
    """
    staged = json.loads(HISTORICAL_CHILDREN.read_text(encoding="utf-8"))
    taxonomy = _load_taxonomy()
    children = []
    seen = set()
    for child in staged["child_data"]:
        metadata = child.get("metadata", {})
        parent_id = metadata.get("parent_id")
        if not parent_id or parent_id not in taxonomy:
            continue
        key = (parent_id, child.get("text", ""), metadata.get("chunk_type", "generic"))
        if key in seen:
            continue
        seen.add(key)
        products, intents = taxonomy[parent_id]
        for intent in intents:
            children.append({
                "text": child["text"],
                "metadata": {
                    "parent_id": parent_id,
                    "product_tag": products,
                    "intent_family": intent,
                    "chunk_type": metadata.get("chunk_type", "generic"),
                },
            })
    scoped = {}
    for scope_pairs in { _scope_key(case["scope_pairs"]): case["scope_pairs"] for case in cases }.values():
        matching = []
        for child in children:
            metadata = child["metadata"]
            if any(
                pair["product"] in metadata["product_tag"]
                and metadata["intent_family"] in pair["intents"]
                for pair in scope_pairs
            ):
                matching.append(child)
        if not matching:
            raise ValueError(f"No child vectors in confirmation scope: {scope_pairs}")
        scoped[_scope_key(scope_pairs)] = matching
    return scoped


def _embed_scope_documents(scope_documents: dict[str, list[dict]], embedder) -> dict[str, dict]:
    embedded = {}
    for key, documents in scope_documents.items():
        vectors = np.asarray(embedder([item["text"] for item in documents]), dtype=float)
        embedded[key] = {"documents": documents, "vectors": vectors}
    return embedded


def _retrieve(scope_index: dict, query_views: list[str], embedder) -> dict:
    parent_rrf: dict[str, float] = defaultdict(float)
    direct_parent_scores: dict[str, float] = {}
    raw_views = []
    for view_index, query in enumerate(query_views):
        query_vector = np.asarray(embedder([query])[0], dtype=float)
        vectors = scope_index["vectors"]
        scores = vectors @ query_vector / (np.linalg.norm(vectors, axis=1) * np.linalg.norm(query_vector))
        top_indices = np.argsort(-scores, kind="stable")[:TOP_K]
        hits = []
        seen_parents = set()
        for rank, document_index in enumerate(top_indices, start=1):
            metadata = scope_index["documents"][int(document_index)]["metadata"]
            parent_id = metadata["parent_id"]
            cosine = float(scores[int(document_index)])
            hits.append({"rank": rank, "parent_id": parent_id, "cosine": cosine})
            if view_index == 0:
                direct_parent_scores[parent_id] = max(direct_parent_scores.get(parent_id, 0.0), cosine)
            if parent_id not in seen_parents:
                seen_parents.add(parent_id)
                parent_rrf[parent_id] += 1.0 / (RRF_K + rank)
        raw_views.append(hits)

    selected = []
    previous_score = None
    for parent_id, rrf_score in sorted(parent_rrf.items(), key=lambda item: item[1], reverse=True):
        if len(selected) >= MAX_PARENTS:
            break
        if previous_score is not None and rrf_score / previous_score < 0.5:
            break
        selected.append(parent_id)
        previous_score = rrf_score
    selected_direct_scores = [direct_parent_scores[parent_id] for parent_id in selected if parent_id in direct_parent_scores]
    return {
        "selected_parent_ids": selected,
        "raw_views": raw_views,
        "parent_rrf_scores": dict(parent_rrf),
        "direct_selected_parent_cosines": selected_direct_scores,
        "parent_dispersion": float(np.std(selected_direct_scores)) if len(selected_direct_scores) >= 2 else None,
        "parent_range": float(max(selected_direct_scores) - min(selected_direct_scores)) if len(selected_direct_scores) >= 2 else None,
        "top_parent_gap": float(max(selected_direct_scores) - sorted(selected_direct_scores)[-2]) if len(selected_direct_scores) >= 2 else None,
    }


def _rank(selected_parent_ids: list[str], acceptable_parent_ids: list[str]) -> int | None:
    acceptable = set(acceptable_parent_ids)
    return next((rank for rank, parent_id in enumerate(selected_parent_ids, start=1) if parent_id in acceptable), None)


def _mrr(rank: int | None) -> float:
    return 0.0 if rank is None else 1.0 / rank


def _summary(cases: list[dict]) -> dict:
    def metric(arm: str) -> dict:
        ranks = [case["arms"][arm]["acceptable_rank"] for case in cases]
        return {
            "recall_at_4": sum(rank is not None for rank in ranks) / len(ranks),
            "mrr_at_4": sum(_mrr(rank) for rank in ranks) / len(ranks),
        }

    direct = metric("direct")
    always = metric("always_bridge")
    gated = metric("dispersion_gated")
    triggered = [case for case in cases if case["triggered"]]
    untriggered = [case for case in cases if not case["triggered"]]
    return {
        "case_count": len(cases),
        "triggered_count": len(triggered),
        "trigger_rate": len(triggered) / len(cases),
        "direct": direct,
        "always_bridge": always,
        "dispersion_gated": gated,
        "always_minus_direct": {key: always[key] - direct[key] for key in direct},
        "gated_minus_direct": {key: gated[key] - direct[key] for key in direct},
        "always_bridge_low_dispersion": {
            "count": len(triggered),
            "mean_mrr_delta": float(np.mean([
                _mrr(case["arms"]["always_bridge"]["acceptable_rank"]) - _mrr(case["arms"]["direct"]["acceptable_rank"])
                for case in triggered
            ])) if triggered else 0.0,
        },
        "always_bridge_high_dispersion": {
            "count": len(untriggered),
            "mean_mrr_delta": float(np.mean([
                _mrr(case["arms"]["always_bridge"]["acceptable_rank"]) - _mrr(case["arms"]["direct"]["acceptable_rank"])
                for case in untriggered
            ])) if untriggered else 0.0,
        },
        "by_source_intent": {
            intent: {
                "count": len(group),
                "triggered": sum(case["triggered"] for case in group),
                "direct_mrr_at_4": float(np.mean([_mrr(case["arms"]["direct"]["acceptable_rank"]) for case in group])),
                "always_bridge_mrr_at_4": float(np.mean([_mrr(case["arms"]["always_bridge"]["acceptable_rank"]) for case in group])),
                "gated_mrr_at_4": float(np.mean([_mrr(case["arms"]["dispersion_gated"]["acceptable_rank"]) for case in group])),
            }
            for intent, group in _group_by_intent(cases).items()
        },
    }


def _group_by_intent(cases: list[dict]) -> dict[str, list[dict]]:
    grouped: dict[str, list[dict]] = defaultdict(list)
    for case in cases:
        grouped[case["source_intent"]].append(case)
    return dict(grouped)


def main() -> None:
    cases = _build_fixture()
    embedder = embedding_functions.DefaultEmbeddingFunction()
    scope_indices = _embed_scope_documents(_build_scope_documents(cases), embedder)
    output_cases = []
    for index, case in enumerate(cases, start=1):
        scope_index = scope_indices[_scope_key(case["scope_pairs"])]
        direct = _retrieve(scope_index, [case["customer_query"]], embedder)
        always = _retrieve(scope_index, [case["customer_query"], case["bridge_query"]], embedder)
        dispersion = direct["parent_dispersion"]
        triggered = dispersion is not None and dispersion <= DISPERSION_THRESHOLD
        gated = always if triggered else direct
        for payload in (direct, always, gated):
            payload["acceptable_rank"] = _rank(payload["selected_parent_ids"], case["acceptable_parent_ids"])
        output_cases.append({
            **case,
            "triggered": triggered,
            "direct_parent_dispersion": dispersion,
            "arms": {"direct": direct, "always_bridge": always, "dispersion_gated": gated},
        })
        print(f"Retrieved {index}/{len(cases)}: {case['id']}")
    output = {
        "metadata": {
            "purpose": "Confirmation test for deterministic direct-parent-dispersion bridge trigger",
            "isolation": "in-memory exact cosine ranking over metadata-filtered child vectors; no production writes",
            "threshold": DISPERSION_THRESHOLD,
            "rrf_k": RRF_K,
            "top_k_per_view": TOP_K,
            "max_parents": MAX_PARENTS,
            "important_limit": "Fresh BANKING77 phrasing only; all cases remain within eight previously reviewed semantic targets.",
        },
        "cases": output_cases,
    }
    output["summary"] = _summary(output_cases)
    RESULTS.write_text(json.dumps(output, indent=2), encoding="utf-8")
    print(json.dumps(output["summary"], indent=2))


if __name__ == "__main__":
    main()
