"""Run repeatable, read-only retrieval checks against the active RAG index.

This is a retrieval benchmark, not an end-to-end chat test: it supplies
fixed Product×Intent scopes and three query angles so index/RRF performance
can be measured independently of LLM task formulation, memory, UI planning,
and outbound gates.  It never writes to SQLite, Chroma, chat history, or
memory. Results are written under this research directory by default.

Example:
    /Users/bensonwu/miniconda3/envs/agentic_banking/bin/python \
      research/2026-08-19_retrieval_query_refinement/scripts/benchmark_retrieval.py
"""

import argparse
import asyncio
import hashlib
import json
import sqlite3
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[3]
RESEARCH_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.app.orchestration.rag_engine import RAGEngine
import chromadb
from chromadb.utils import embedding_functions


DEFAULT_OUTPUT = RESEARCH_ROOT / "results" / "retrieval_benchmark_latest.json"
GENERIC_STAGING_FILE = PROJECT_ROOT / "backend" / "data" / "raw_sources" / "ready_for_ingestion.json"
# Deliberately versioned temporary store.  A benchmark run must never share a
# mutable collection with an older experiment or the active production store.
GENERIC_BENCHMARK_PATH = Path(tempfile.gettempdir()) / "agentic_banking_generic_benchmark_chroma_v5"
GENERIC_COLLECTION_NAME = "generic_benchmark_child_chunks_v5"


CASES = [
    {
        "id": "instant_loan_eligibility",
        "customer_query": "What are the eligibility requirements for an Instant Loan?",
        "expected_parent_id": "faq_0ae0bb51",
        "scope_pairs": [{"product": "Personal_Loan", "intents": ["Eligibility"]}],
        "angle_groups": [{
            "group_id": "base",
            "procedural": "check Instant Loan eligibility, age, income, residency",
            "policy": "Instant Loan eligibility requirements, income threshold, age limit, work pass",
            "conceptual": "Horizon Instant Loan qualification criteria",
        }],
    },
    {
        "id": "deposit_interest_crediting",
        "customer_query": "When is interest credited to my Horizon deposit account?",
        "expected_parent_id": "faq_fc5a7921",
        "scope_pairs": [{"product": "Deposit_Account", "intents": ["Interest And Rates"]}],
        "angle_groups": [{
            "group_id": "base",
            "procedural": "check deposit account interest credit date and schedule",
            "policy": "deposit account interest crediting timing and payout rules",
            "conceptual": "Horizon deposit account interest credit schedule",
        }],
    },
    {
        "id": "debit_card_activation",
        "customer_query": "How do I activate my newly received Horizon card?",
        "expected_parent_id": "faq_74abafeb",
        "scope_pairs": [{"product": "Debit_Card", "intents": ["Card Logistics"]}],
        "angle_groups": [{
            "group_id": "base",
            "procedural": "activate newly received Horizon debit card",
            "policy": "debit card activation requirements and verification",
            "conceptual": "Horizon debit card activation",
        }],
    },
    {
        "id": "stock_autoinvest_setup",
        "customer_query": "How do I set up automatic investments for stocks?",
        "expected_parent_id": "faq_2f201863",
        "scope_pairs": [{"product": "Stocks", "intents": ["Wealth And Insurance"]}],
        "angle_groups": [{
            "group_id": "base",
            "procedural": "set up automatic stock investment, choose stock or ETF, schedule amount",
            "policy": "AutoInvest stock investment setup requirements and funding rules",
            "conceptual": "Horizon AutoInvest automatic equities investment",
        }],
    },
    {
        "id": "stock_autoinvest_multiple_plans",
        "customer_query": "Can I create more than one automated stock investment plan?",
        "expected_parent_id": "faq_9a88fadb",
        "scope_pairs": [{"product": "Stocks", "intents": ["Wealth And Insurance"]}],
        "angle_groups": [{
            "group_id": "base",
            "procedural": "create multiple automatic stock investment plans",
            "policy": "AutoInvest multiple plan limits and rules",
            "conceptual": "Horizon AutoInvest multiple equities plans",
        }],
    },
    {
        "id": "deposit_bonus_interest_timing",
        "customer_query": "When will I receive bonus interest in my savings account?",
        "expected_parent_id": "faq_871bd177",
        "scope_pairs": [{"product": "Deposit_Account", "intents": ["Interest And Rates"]}],
        "angle_groups": [{
            "group_id": "base",
            "procedural": "check savings account bonus interest credit timing",
            "policy": "bonus interest qualification and crediting schedule",
            "conceptual": "Horizon savings account bonus interest payment date",
        }],
    },
    {
        "id": "debit_card_delivery",
        "customer_query": "When should I receive my Horizon debit card after applying?",
        "expected_parent_id": "faq_9d7a4b83",
        "scope_pairs": [{"product": "Debit_Card", "intents": ["Card Logistics"]}],
        "angle_groups": [{
            "group_id": "base",
            "procedural": "track newly applied Horizon debit card delivery",
            "policy": "debit card delivery timeframe and mailing rules",
            "conceptual": "Horizon debit card delivery timing",
        }],
    },
    {
        "id": "debit_card_pin_update",
        "customer_query": "How can I change the PIN for my Horizon card?",
        "expected_parent_id": "faq_6dae126c",
        "scope_pairs": [{"product": "Debit_Card", "intents": ["Card Logistics"]}],
        "angle_groups": [{
            "group_id": "base",
            "procedural": "change Horizon debit card PIN",
            "policy": "debit card PIN update and security requirements",
            "conceptual": "Horizon card personal identification number management",
        }],
    },
    {
        "id": "credit_card_balance_transfer_fee",
        "customer_query": "What does a credit-card balance transfer cost?",
        "expected_parent_id": "faq_f7412d3a",
        "scope_pairs": [{"product": "Credit_Card", "intents": ["Fees And Charges"]}],
        "angle_groups": [{
            "group_id": "base",
            "procedural": "review credit card balance transfer charges",
            "policy": "balance transfer fee and additional cost rules",
            "conceptual": "Horizon credit card balance transfer pricing",
        }],
    },
    {
        "id": "horizon_app_obtain",
        "customer_query": "Where can I get the Horizon App?",
        "expected_parent_id": "faq_ec3376a6",
        "scope_pairs": [{"product": "Horizon_APP", "intents": ["General Inquiries"]}],
        "angle_groups": [{
            "group_id": "base",
            "procedural": "download and obtain Horizon App",
            "policy": "Horizon App availability and download requirements",
            "conceptual": "Horizon mobile banking app",
        }],
    },
    {
        "id": "rewards_membership_connection",
        "customer_query": "Will my existing rewards membership connect automatically to my Horizon card?",
        "expected_parent_id": "faq_da866599",
        "scope_pairs": [{"product": "Rewards_Program", "intents": ["Rewards And Promotions"]}],
        "angle_groups": [{
            "group_id": "base",
            "procedural": "link existing rewards membership to Horizon card",
            "policy": "rewards membership automatic card connection rules",
            "conceptual": "Horizon card rewards membership linking",
        }],
    },
    {
        "id": "life_insurance_products",
        "customer_query": "What life insurance products does Horizon Bank offer?",
        "expected_parent_id": "faq_88e75264",
        "scope_pairs": [{"product": "Insurance", "intents": ["Wealth And Insurance"]}],
        "angle_groups": [{
            "group_id": "base",
            "procedural": "review available Horizon life insurance products",
            "policy": "life insurance product availability and coverage options",
            "conceptual": "Horizon Bank life insurance offerings",
        }],
    },
    {
        "id": "mutual_fund_products",
        "customer_query": "Which mutual funds are available through Horizon Bank?",
        "expected_parent_id": "faq_85546eac",
        "scope_pairs": [{"product": "Mutual_Funds", "intents": ["Wealth And Insurance"]}],
        "angle_groups": [{
            "group_id": "base",
            "procedural": "view available Horizon mutual fund products",
            "policy": "mutual fund availability and investment product rules",
            "conceptual": "Horizon mutual fund offerings",
        }],
    },
    {
        "id": "mutual_fund_costs",
        "customer_query": "What costs are involved when investing in Horizon mutual funds?",
        "expected_parent_id": "faq_eb2caf90",
        "scope_pairs": [{"product": "Mutual_Funds", "intents": ["Wealth And Insurance"]}],
        "angle_groups": [{
            "group_id": "base",
            "procedural": "review mutual fund investment costs",
            "policy": "mutual fund fees charges and investment costs",
            "conceptual": "Horizon mutual fund pricing",
        }],
    },
    {
        "id": "verify_bank_email",
        "customer_query": "How can I check whether an email claiming to be from Horizon Bank is genuine?",
        "expected_parent_id": "faq_ed92e41d",
        "scope_pairs": [{"product": "ALL", "intents": ["Authentication And Security"]}],
        "angle_groups": [{
            "group_id": "base",
            "procedural": "verify whether Horizon Bank email is authentic",
            "policy": "bank email authenticity scam and fraud verification guidance",
            "conceptual": "Horizon Bank genuine email verification",
        }],
    },
    {
        "id": "credit_card_verification",
        "customer_query": "Why do I need to verify a newly issued Horizon card before I use it?",
        "expected_parent_id": "faq_26561f81",
        "scope_pairs": [{"product": "Credit_Card", "intents": ["Card Logistics"]}],
        "angle_groups": [{
            "group_id": "base",
            "procedural": "verify newly issued Horizon credit card before use",
            "policy": "new credit card verification requirement before first use",
            "conceptual": "Horizon newly issued card verification",
        }],
    },
    {
        "id": "deposit_account_funding",
        "customer_query": "How can I add money to my Horizon savings account?",
        "expected_parent_id": "faq_7d49c937",
        "scope_pairs": [{"product": "Deposit_Account", "intents": ["Payments And Transfers"]}],
        "angle_groups": [{
            "group_id": "base",
            "procedural": "fund Horizon savings account and add money",
            "policy": "savings account funding methods and transfer rules",
            "conceptual": "Horizon savings account deposits and funding",
        }],
    },
    {
        "id": "horizon_key_setup",
        "customer_query": "How do I set up my Horizon Key?",
        "expected_parent_id": "faq_b074c2e1",
        "scope_pairs": [{"product": "Horizon_APP", "intents": ["Authentication And Security"]}],
        "angle_groups": [{
            "group_id": "base",
            "procedural": "set up Horizon Key",
            "policy": "Horizon Key setup security and registration requirements",
            "conceptual": "Horizon Key authentication security feature",
        }],
    },
    {
        "id": "life_insurance_policy_access",
        "customer_query": "Where can I view the details of my life insurance policy?",
        "expected_parent_id": "faq_598df143",
        "scope_pairs": [{"product": "Insurance", "intents": ["Wealth And Insurance"]}],
        "angle_groups": [{
            "group_id": "base",
            "procedural": "view life insurance policy details in digital banking",
            "policy": "life insurance policy information access rules",
            "conceptual": "Horizon life insurance policy details",
        }],
    },
    {
        "id": "personal_loan_borrowing_cost",
        "customer_query": "How does Horizon Bank calculate the cost of borrowing?",
        "expected_parent_id": "faq_98ed4759",
        "scope_pairs": [{"product": "Personal_Loan", "intents": ["Interest And Rates"]}],
        "angle_groups": [{
            "group_id": "base",
            "procedural": "understand Horizon borrowing cost calculation",
            "policy": "personal loan borrowing cost and rate calculation rules",
            "conceptual": "Horizon personal loan cost of borrowing",
        }],
    },
]


async def _run_case(engine: RAGEngine, case: dict) -> dict:
    parents = await engine.execute_multi_angle_search(
        {"angle_groups": case["angle_groups"]},
        case["scope_pairs"],
    )
    selected_parent_ids = [parent["parent_id"] for parent in parents]
    expected_parent_id = case["expected_parent_id"]
    expected_rank = (
        selected_parent_ids.index(expected_parent_id) + 1
        if expected_parent_id in selected_parent_ids
        else None
    )
    return {
        "id": case["id"],
        "customer_query": case["customer_query"],
        "expected_parent_id": expected_parent_id,
        "scope_pairs": case["scope_pairs"],
        "angle_groups": case["angle_groups"],
        "selected_parent_ids": selected_parent_ids,
        "expected_parent_rank": expected_rank,
        "passed": expected_rank is not None,
    }


async def _run_single_query_case(engine: RAGEngine, case: dict, query_text: str) -> dict:
    """Evaluate one cleaned or raw query with the same scope and top-four cap.

    This deliberately avoids RRF: one query produces one candidate ranking.
    Duplicate child hits are collapsed to their first (best) parent occurrence.
    """
    hits = await engine._query_single_angle(
        query_text,
        engine._build_metadata_filter(case["scope_pairs"]),
    )
    parent_ids = []
    for hit in hits:
        if hit["parent_id"] not in parent_ids:
            parent_ids.append(hit["parent_id"])
        if len(parent_ids) == 4:
            break
    expected_parent_id = case["expected_parent_id"]
    expected_rank = parent_ids.index(expected_parent_id) + 1 if expected_parent_id in parent_ids else None
    return {
        "id": case["id"],
        "customer_query": case["customer_query"],
        "query_text": query_text,
        "expected_parent_id": expected_parent_id,
        "selected_parent_ids": parent_ids,
        "expected_parent_rank": expected_rank,
        "passed": expected_rank is not None,
    }


def _combined_clean_query(case: dict) -> str:
    """A strong single-query baseline built from the exact same task content.

    It intentionally gives direct retrieval all semantic terms supplied to the
    three-angle method, but as one vector query rather than three rankings.
    """
    supplied_direct_query = str(case.get("direct_query", "")).strip()
    if supplied_direct_query:
        return supplied_direct_query
    return "; ".join(
        value
        for group in case["angle_groups"]
        for value in (group["procedural"], group["policy"], group["conceptual"])
        if value
    )


def _narrative_three_query_blueprint(case: dict) -> dict:
    """Create three same-task query views without changing the task boundary.

    Every query preserves the complete cleaned task anchor.  The existing
    procedural/policy/conceptual terms are only additional narrative emphasis,
    not separate user requests or expanded Product×Intent filters.
    """
    anchor = _combined_clean_query(case)
    base = case["angle_groups"][0]
    return {
        "angle_groups": [{
            "group_id": "narrative_views",
            "procedural": f"{anchor}; {base['procedural']}",
            "policy": f"{anchor}; {base['policy']}",
            "conceptual": f"{anchor}; {base['conceptual']}",
        }]
    }


FAQ_INTENT_PATTERNS = {
    "Application": "How to apply for {product}, including the application process and information required",
    "Eligibility": "Eligibility criteria and qualification requirements for {product}",
    "Card Logistics": "Activation, delivery, replacement, or other card handling guidance for {product}",
    "Features And Limits": "Features, use, benefits, restrictions, and limits of {product}",
    "Fees And Charges": "Fees, charges, waivers, and costs for {product}",
    "Interest And Rates": "Interest, rates, qualification criteria, and crediting timing for {product}",
    "Account Management": "Managing, maintaining, linking, or closing {product}",
    "Profile Updates": "Updating registered personal details for {product}",
    "Payments And Transfers": "Payments, transfers, deposits, and money movement using {product}",
    "Transaction Disputes": "Disputing, investigating, or resolving a transaction involving {product}",
    "Loan And Credit Management": "Repayment, instalments, balances, and other credit management for {product}",
    "Wealth And Insurance": "Investment, insurance, portfolio, or policy information for {product}",
    "Rewards And Promotions": "Earning, using, or managing rewards and promotions for {product}",
    "Authentication And Security": "Login, authentication, access, security, fraud, or credential support for {product}",
    "Branch And ATM Locator": "Finding a branch, ATM, or available in-person service for {product}",
    "General Inquiries": "General product information, navigation, availability, or support for {product}",
}


def _product_label(product: str) -> str:
    return product.replace("_", " ").replace("ALL", "Horizon Bank services")


def _adaptive_product_intent_query_views(case: dict) -> list[str]:
    """Build evaluation-only FAQ narratives from a task's valid scope pairs.

    Each view preserves the complete cleaned task anchor.  Product×Intent is
    used only to select an FAQ-shaped phrasing; it does not add a new product,
    expand an intent boundary, or split a multi-part user request.
    """
    anchor = _combined_clean_query(case)
    views = [anchor]
    for pair in case["scope_pairs"]:
        product = _product_label(str(pair["product"]))
        for intent in pair["intents"]:
            pattern = FAQ_INTENT_PATTERNS.get(str(intent))
            if pattern:
                views.append(f"{anchor}; {pattern.format(product=product)}")
    # Keep only distinct complete views.  This matters for a task whose scope
    # happens to contain an equivalent label through more than one branch.
    return list(dict.fromkeys(view.strip() for view in views if view.strip()))


def _faq_distilled_pattern_query_views(case: dict) -> list[str]:
    """Return complete anchor-preserving views distilled from mapped FAQ text.

    This is evaluation-only.  Unlike ``FAQ_INTENT_PATTERNS``, the supplied
    patterns are written from the reviewed Horizon parent records for the
    evaluated customer need.  They test corpus-language alignment, not merely
    the broad Product x Intent taxonomy label.
    """
    anchor = _combined_clean_query(case)
    patterns = case.get("faq_distilled_patterns", [])
    return list(dict.fromkeys([
        anchor,
        *[f"{anchor}; {pattern}" for pattern in patterns if str(pattern).strip()],
    ]))


async def _run_query_view_rrf_case(engine: RAGEngine, case: dict, query_views: list[str]) -> dict:
    """Fuse arbitrary complete query narratives with the production RRF math.

    This is deliberately benchmark-local.  It validates the proposed
    Product×Intent narrative strategy without altering the active RAG request
    contract, collection, or production retrieval code.
    """
    metadata_filter = engine._build_metadata_filter(case["scope_pairs"])
    result_lists = await asyncio.gather(*[
        engine._query_single_angle(query_view, metadata_filter)
        for query_view in query_views
    ])
    scores = {}
    for hit_list in result_lists:
        seen_parents = set()
        for rank, hit in enumerate(hit_list, start=1):
            parent_id = hit["parent_id"]
            if parent_id not in seen_parents:
                seen_parents.add(parent_id)
                scores[parent_id] = scores.get(parent_id, 0.0) + 1.0 / (60 + rank)

    ranked = sorted(scores.items(), key=lambda item: item[1], reverse=True)
    selected_parent_ids = []
    previous_score = None
    for parent_id, score in ranked:
        if len(selected_parent_ids) >= 4:
            break
        if previous_score is not None and score / previous_score < 0.5:
            break
        selected_parent_ids.append(parent_id)
        previous_score = score

    expected_parent_id = case["expected_parent_id"]
    expected_rank = (
        selected_parent_ids.index(expected_parent_id) + 1
        if expected_parent_id in selected_parent_ids
        else None
    )
    return {
        "id": case["id"],
        "customer_query": case["customer_query"],
        "query_views": query_views,
        "expected_parent_id": expected_parent_id,
        "selected_parent_ids": selected_parent_ids,
        "expected_parent_rank": expected_rank,
        "passed": expected_rank is not None,
    }


def _select_cases(case_ids: set[str] | None, available_cases: list[dict]) -> list[dict]:
    selected_cases = [case for case in available_cases if not case_ids or case["id"] in case_ids]
    unknown_ids = case_ids.difference(case["id"] for case in available_cases) if case_ids else set()
    if unknown_ids:
        raise ValueError(f"Unknown benchmark case IDs: {', '.join(sorted(unknown_ids))}")
    if not selected_cases:
        raise ValueError("No benchmark cases selected.")
    return selected_cases


def _load_active_taxonomy() -> dict[str, tuple[list[str], list[str]]]:
    """Read active parent labels to remove taxonomy as an A/B confounder."""
    db_path = PROJECT_ROOT / "backend" / "data" / "knowledge_base.db"
    connection = sqlite3.connect(db_path)
    try:
        rows = connection.execute(
            "SELECT parent_id, product_tags, intent_families FROM knowledge_parents"
        ).fetchall()
    finally:
        connection.close()
    return {
        parent_id: (json.loads(product_tags), json.loads(intent_families))
        for parent_id, product_tags, intent_families in rows
    }


def _generic_source_hash(taxonomy: dict[str, tuple[list[str], list[str]]]) -> str:
    digest = hashlib.sha256(GENERIC_STAGING_FILE.read_bytes())
    digest.update(json.dumps(taxonomy, sort_keys=True).encode("utf-8"))
    return digest.hexdigest()


def _get_or_build_generic_collection(rebuild: bool):
    """Create an isolated generic index; never use or change the active store."""
    taxonomy = _load_active_taxonomy()
    source_hash = _generic_source_hash(taxonomy)
    client = chromadb.PersistentClient(path=str(GENERIC_BENCHMARK_PATH))
    try:
        collection = client.get_collection(GENERIC_COLLECTION_NAME)
        if not rebuild and (collection.metadata or {}).get("source_sha256") == source_hash:
            return collection
        client.delete_collection(GENERIC_COLLECTION_NAME)
    except Exception:
        pass

    staged = json.loads(GENERIC_STAGING_FILE.read_text(encoding="utf-8"))
    children = staged.get("child_data", [])
    if not children:
        raise ValueError("Historical generic staging file has no child_data.")
    collection = client.create_collection(
        name=GENERIC_COLLECTION_NAME,
        embedding_function=embedding_functions.DefaultEmbeddingFunction(),
        metadata={
            "hnsw:space": "cosine",
            "schema_version": "generic_benchmark_v2_canonical_metadata",
            "source_sha256": source_hash,
        },
    )
    # The historic generic artifact is duplicated under obsolete intent labels.
    # Deduplicate its underlying chunks, then reproduce every active canonical
    # intent lane. This keeps filters identical between the generic and
    # tri-angle indexes while preserving their different vector text.
    normalized_children = []
    seen_chunks = set()
    for child in children:
        metadata = child.get("metadata", {})
        parent_id = metadata.get("parent_id")
        canonical = taxonomy.get(parent_id)
        if not parent_id or not canonical:
            continue
        products, intents = canonical
        chunk_key = (parent_id, child.get("text", ""), metadata.get("chunk_type", ""))
        if chunk_key in seen_chunks:
            continue
        seen_chunks.add(chunk_key)
        for intent in intents:
            normalized_children.append({
                "text": child["text"],
                "metadata": {
                    "parent_id": parent_id,
                    "product_tag": products,
                    "intent_family": intent,
                    "chunk_type": metadata.get("chunk_type", "generic"),
                },
            })

    for start in range(0, len(normalized_children), 500):
        batch = normalized_children[start:start + 500]
        collection.add(
            ids=[f"generic_{index:05d}" for index in range(start, start + len(batch))],
            documents=[child["text"] for child in batch],
            metadatas=[child["metadata"] for child in batch],
        )
    return collection


async def _run_engine_benchmark(engine: RAGEngine, selected_cases: list[dict]) -> dict:
    results = [await _run_case(engine, case) for case in selected_cases]
    return {
        "collection_schema": (engine.collection.metadata or {}).get("schema_version", "unknown"),
        "case_count": len(results),
        "passed_count": sum(result["passed"] for result in results),
        "results": results,
    }


async def _run_query_strategy_benchmark(generic_engine: RAGEngine, selected_cases: list[dict]) -> dict:
    strategies = {
        "combined_clean_query": [
            await _run_single_query_case(generic_engine, case, _combined_clean_query(case))
            for case in selected_cases
        ],
        "narrative_three_query_rrf": [
            await _run_case(
                generic_engine,
                {
                    **case,
                    "angle_groups": _narrative_three_query_blueprint(case)["angle_groups"],
                },
            )
            for case in selected_cases
        ],
        "adaptive_product_intent_narrative_rrf": [
            await _run_query_view_rrf_case(
                generic_engine,
                case,
                _adaptive_product_intent_query_views(case),
            )
            for case in selected_cases
        ],
        "faq_distilled_pattern_narrative_rrf": [
            await _run_query_view_rrf_case(
                generic_engine,
                case,
                _faq_distilled_pattern_query_views(case),
            )
            for case in selected_cases
        ],
    }
    return {
        name: {
            "case_count": len(results),
            "passed_count": sum(result["passed"] for result in results),
            "evidence_coverage": _summarize_evidence_coverage(results, selected_cases),
            "results": results,
        }
        for name, results in strategies.items()
    }


def _summarize_evidence_coverage(results: list[dict], cases: list[dict]) -> dict:
    """Score whether a selected parent *set* covers each requested component.

    A conventional MRR metric has one expected parent.  That is insufficient
    for a customer task such as "apply, documents, and eligibility", where
    useful evidence can legitimately be ranked second or third.  Components
    supply independently reviewed acceptable parent IDs for this evaluation.
    Existing one-target regression cases are represented as one component.
    """
    cases_by_id = {case["id"]: case for case in cases}
    component_total = component_covered = full_task_count = 0
    first_relevant_ranks = []
    precisions = []
    case_details = []

    for result in results:
        case = cases_by_id[result["id"]]
        selected = result["selected_parent_ids"]
        components = case.get("expected_components") or [{
            "name": "expected parent",
            "acceptable_parent_ids": [case["expected_parent_id"]],
        }]
        relevant_ids = {
            parent_id
            for component in components
            for parent_id in component["acceptable_parent_ids"]
        }
        scored_components = []
        for component in components:
            acceptable = set(component["acceptable_parent_ids"])
            rank = next(
                (index for index, parent_id in enumerate(selected, start=1) if parent_id in acceptable),
                None,
            )
            component_total += 1
            if rank is not None:
                component_covered += 1
                first_relevant_ranks.append(rank)
            scored_components.append({
                "name": component["name"],
                "covered": rank is not None,
                "first_relevant_rank": rank,
            })
        fully_covered = all(component["covered"] for component in scored_components)
        full_task_count += fully_covered
        precisions.append(
            len(set(selected).intersection(relevant_ids)) / len(selected) if selected else 0.0
        )
        case_details.append({
            "id": result["id"],
            "full_task_coverage": fully_covered,
            "selected_parent_ids": selected,
            "components": scored_components,
        })

    return {
        "component_coverage": f"{component_covered}/{component_total}",
        "component_recall_at_4": component_covered / component_total if component_total else 0.0,
        "full_task_coverage_at_4": f"{full_task_count}/{len(results)}",
        "full_task_recall_at_4": full_task_count / len(results) if results else 0.0,
        "mean_first_relevant_rank": (
            sum(first_relevant_ranks) / len(first_relevant_ranks)
            if first_relevant_ranks else None
        ),
        "mean_evidence_precision_at_4": sum(precisions) / len(precisions) if precisions else 0.0,
        "case_details": case_details,
    }


async def run_benchmark(
    case_ids: set[str] | None,
    mode: str,
    rebuild_generic: bool,
    additional_cases: list[dict] | None = None,
    include_engine_baseline: bool = True,
) -> dict:
    available_cases = CASES + (additional_cases or [])
    duplicate_ids = {
        case["id"] for case in available_cases
        if sum(candidate["id"] == case["id"] for candidate in available_cases) > 1
    }
    if duplicate_ids:
        raise ValueError(f"Duplicate benchmark case IDs: {', '.join(sorted(duplicate_ids))}")
    selected_cases = _select_cases(case_ids, available_cases)
    runs = {}
    if mode in {"tri", "both"}:
        runs["tri_angle"] = await _run_engine_benchmark(RAGEngine(), selected_cases)
    if mode in {"generic", "both"}:
        generic_collection = _get_or_build_generic_collection(rebuild_generic)
        generic_engine = RAGEngine(collection=generic_collection)
        if include_engine_baseline:
            runs["generic"] = await _run_engine_benchmark(generic_engine, selected_cases)
        runs["generic_query_strategies"] = await _run_query_strategy_benchmark(
            generic_engine, selected_cases
        )

    comparison = []
    if "tri_angle" in runs and "generic" in runs:
        tri_by_id = {result["id"]: result for result in runs["tri_angle"]["results"]}
        generic_by_id = {result["id"]: result for result in runs["generic"]["results"]}
        for case in selected_cases:
            case_id = case["id"]
            tri_rank = tri_by_id[case_id]["expected_parent_rank"]
            generic_rank = generic_by_id[case_id]["expected_parent_rank"]
            if tri_rank is None and generic_rank is None:
                outcome = "both_missed"
            elif tri_rank is None:
                outcome = "generic_better"
            elif generic_rank is None:
                outcome = "tri_angle_better"
            elif tri_rank < generic_rank:
                outcome = "tri_angle_better"
            elif generic_rank < tri_rank:
                outcome = "generic_better"
            else:
                outcome = "tie"
            comparison.append({
                "id": case_id,
                "expected_parent_id": case["expected_parent_id"],
                "tri_angle_rank": tri_rank,
                "generic_rank": generic_rank,
                "outcome": outcome,
            })
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "mode": mode,
        "runs": runs,
        "comparison": comparison,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Read-only active-RAG retrieval benchmark.")
    parser.add_argument("--case", action="append", dest="case_ids", help="Run only this case ID; repeatable.")
    parser.add_argument("--mode", choices=("tri", "generic", "both"), default="both", help="Index or indexes to benchmark.")
    parser.add_argument("--rebuild-generic", action="store_true", help="Rebuild the isolated generic benchmark collection.")
    parser.add_argument(
        "--cases-file",
        type=Path,
        help="Optional JSON array of additional frozen benchmark cases; appended to built-in cases.",
    )
    parser.add_argument(
        "--replace-default-cases",
        action="store_true",
        help="Use only --cases-file cases instead of appending the built-in labelled regression cases.",
    )
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT, help="JSON report path.")
    parser.add_argument(
        "--query-strategies-only",
        action="store_true",
        help="Skip the legacy engine baseline and evaluate only complete-task query strategies.",
    )
    args = parser.parse_args()

    additional_cases = []
    if args.cases_file:
        additional_cases = json.loads(args.cases_file.read_text(encoding="utf-8"))
        if not isinstance(additional_cases, list):
            raise ValueError("--cases-file must contain a JSON array of benchmark cases.")
    if args.replace_default_cases:
        if not args.cases_file:
            raise ValueError("--replace-default-cases requires --cases-file.")
        original_cases = CASES[:]
        try:
            CASES[:] = additional_cases
            report = asyncio.run(
                run_benchmark(
                    set(args.case_ids or []),
                    args.mode,
                    args.rebuild_generic,
                    include_engine_baseline=not args.query_strategies_only,
                )
            )
        finally:
            CASES[:] = original_cases
    else:
        report = asyncio.run(
            run_benchmark(
                set(args.case_ids or []),
                args.mode,
                args.rebuild_generic,
                additional_cases,
                include_engine_baseline=not args.query_strategies_only,
            )
        )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps({
        "mode": report["mode"],
        "runs": {
            name: (
                {"schema": run["collection_schema"], "passed": run["passed_count"], "total": run["case_count"]}
                if "collection_schema" in run
                else {
                    strategy: {"passed": result["passed_count"], "total": result["case_count"]}
                    for strategy, result in run.items()
                }
            )
            for name, run in report["runs"].items()
        },
        "output": str(args.output),
    }, indent=2))


if __name__ == "__main__":
    main()
