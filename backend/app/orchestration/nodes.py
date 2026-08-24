import json
import re
import sqlite3
import os
import asyncio
from typing import Any
from langchain_core.messages import HumanMessage, AIMessage
from backend.app.orchestration.state import AgentState
from backend.app.orchestration.rag_engine import RAGEngine
from backend.app.services.memory_service import LongTermMemoryManager
from backend.app.core.llm_service import (
    call_groq_lpu,
    parse_json_robustly as _parse_json_robustly,
    MODEL_B_AUDITOR,
    MODEL_C_PLANNER,
    MODEL_D_PROMPT_GUARD,
    MODEL_E_TRIAGE,
)
from backend.app.core.groundedness import check_evidence_groundedness, check_conduct_compliance
from backend.app.core.compliance import REGULATORY_LICENSING_CONTEXT, PRECISION_REFERENCE_CONTRACT
from backend.app.core.translations import get_message
from backend.app.core.system_logger import log_event
from backend.app.orchestration.tool_registry import enabled_tool_capabilities, is_registered_operation, TOOL_REGISTRY
from backend.app.orchestration.service_request_registry import get_service_request_contract
from backend.app.services import service_request_service

# Define the DB Path (adjust relative path if necessary based on your working directory)
SQLITE_DB_PATH = "backend/data/knowledge_base.db"
ESCALATION_CONFIRMATION_MESSAGE = "Yes, transfer me to a human agent."

# --- 🧹 VOLATILE STATE RESET DICTIONARY ---
# Applied at the start of every turn to prevent cross-turn memory leakage
VOLATILE_STATE_RESET = {
    "safety_retry_count": 0,
    "search_feedback_history": [],
    "draft_feedback_history": [],
    "triaged_subqueries": [],
    "financial_subqueries": [],
    "has_non_financial_content": False,
    "non_financial_context": [],
    "tool_tasks": [],
    "tool_results": [],
    "pending_tool_task_ids": [],
    "tool_task_fingerprints": [],
    "tool_planning_round": 0,
    "tool_plan_status": "continue",
    "tool_gaps": [],
    "decline_tasks": [],
    "search_tasks": [],
    "task_results": [],
    "task_retry_counts": {},
    "deferred_retry_task_ids": [],
    "task_feedback": {},
    "task_sufficiency_history": {},
    "task_partial_evidence": {},
    "task_rejected_parent_ids": {},
    "intent_clarification_tasks": [],
    "task_resolution_instructions": [],
    "safe_answer_for_fallback": "",
    "clarification_fallback_required": False,
    "final_answer": "",
    "final_clarification": "",
    "selected_ux_journey": "TEXT",
    "presentation_plan": {},
    "outbound_compliance_result": {},
    "outbound_groundedness_result": {},
    "active_tool_routes": [],
    "pending_advance": False,
    "service_request_event": {},
}

MAX_VERIFIED_TASK_HISTORY_TURNS = 10
ANSWERABLE_TASK_STATUSES = {"SUFFICIENT", "PARTIALLY_SUFFICIENT"}
# One initial attempt plus three refinement stages (INTENT_EXPANSION,
# PRODUCT_EXPANSION, ANGLE_EXPANSION — see _retry_expansion_plan). Must stay
# in sync with the highest attempt number keyed in that function's stages
# dict, or the final stage becomes unreachable.
MAX_RAG_ATTEMPTS = 4
MAX_TOOL_PLANNING_ROUNDS = 3
MAX_TOOL_TASKS_PER_TURN = 8


def _normalized_product_tags(product_value: Any) -> list[str]:
    """Return unique, matrix-valid product tags while preserving their order."""
    raw_tags = product_value if isinstance(product_value, list) else [product_value]
    tags = []
    for raw_tag in raw_tags:
        tag = str(raw_tag).strip()
        if tag and tag in PRODUCT_INTENT_MATRIX and tag not in tags:
            tags.append(tag)
    return tags


_ANGLE_FIELDS = ("procedural", "policy", "conceptual")


def _normalize_angle_groups(task: dict) -> list[dict]:
    """Return V2.5 angle groups, accepting legacy flat task fields.

    Every group is a complete alternative semantic representation.  Terms are
    never concatenated into an existing angle because that would dilute vector
    similarity and blur the evidence represented by an RRF rank list.
    """
    groups = []
    seen_ids = set()
    for index, raw_group in enumerate(task.get("angle_groups", []) or [], start=1):
        if not isinstance(raw_group, dict):
            continue
        group_id = str(raw_group.get("group_id", "")).strip() or f"group_{index}"
        if group_id in seen_ids:
            continue
        group = {
            "group_id": group_id,
            **{field: str(raw_group.get(field, "")).strip() for field in _ANGLE_FIELDS},
        }
        if any(group[field] for field in _ANGLE_FIELDS):
            groups.append(group)
            seen_ids.add(group_id)

    if groups:
        return groups
    legacy = {
        "group_id": "base",
        **{field: str(task.get(field, "")).strip() for field in _ANGLE_FIELDS},
    }
    return [legacy] if any(legacy[field] for field in _ANGLE_FIELDS) else []


def _valid_product_intent_scope_pairs(task: dict) -> list[dict]:
    """Build valid metadata pair scopes without creating a cross-product.

    The RAG engine uses these pairs as one shared Chroma filter for every
    angle group.  They are constraints, not independent retrieval branches.
    """
    products = _normalized_product_tags(task.get("target_commercial_product"))
    raw_intents = task.get("target_intent", [])
    raw_intents = [raw_intents] if isinstance(raw_intents, str) else raw_intents
    intents = []
    for raw_intent in raw_intents:
        intent = str(raw_intent).strip()
        if intent and intent not in intents:
            intents.append(intent)

    return [
        {
            "product": product,
            "intents": [intent for intent in intents if intent in PRODUCT_INTENT_MATRIX[product]],
        }
        for product in products
        if any(intent in PRODUCT_INTENT_MATRIX[product] for intent in intents)
    ]


def _verified_session_parent_ids_for_task(state: AgentState, task: dict) -> list[str]:
    """Return prior verified parents whose existing task scope overlaps this task.

    The history retains the original search-task snapshots, not copied FAQ
    text.  A parent is merely a candidate for the current RRF branch: it is
    re-ranked against the current direct subquery and must still pass the
    current turn's sufficiency audit before it becomes approved evidence.
    """
    current_products = set(_normalized_product_tags(task.get("target_commercial_product")))
    current_intents = {
        str(intent).strip()
        for intent in (task.get("target_intent", []) if isinstance(task.get("target_intent", []), list) else [task.get("target_intent", "")])
        if str(intent).strip()
    }
    if not current_products or not current_intents:
        return []

    matched_ids = []
    seen_ids = set()
    # Recency is the tie-breaker after the required product-and-intent overlap.
    for record in reversed(state.get("verified_task_history", [])):
        for verified_task in reversed(record.get("tasks", []) if isinstance(record, dict) else []):
            prior_task = verified_task.get("search_task", {}) if isinstance(verified_task, dict) else {}
            prior_products = set(_normalized_product_tags(prior_task.get("target_commercial_product")))
            prior_raw_intents = prior_task.get("target_intent", [])
            prior_raw_intents = prior_raw_intents if isinstance(prior_raw_intents, list) else [prior_raw_intents]
            prior_intents = {str(intent).strip() for intent in prior_raw_intents if str(intent).strip()}
            if not (current_products & prior_products and current_intents & prior_intents):
                continue
            for parent_id in verified_task.get("approved_parent_ids", []):
                if isinstance(parent_id, str) and parent_id and parent_id not in seen_ids:
                    seen_ids.add(parent_id)
                    matched_ids.append(parent_id)
    return matched_ids


def _append_verified_task_history(state: AgentState) -> list[dict]:
    """Append this passed turn's existing task objects and approved parent IDs.

    It intentionally stores no hydrated knowledge, chat text, drafts, or
    unapproved candidates.  The history is session-scoped and bounded by
    verified turns, so later retrieval can rehydrate authoritative parents on
    demand without increasing prompt state materially.
    """
    results_by_task = {
        result.get("task_id"): result
        for result in state.get("task_results", [])
        if result.get("status") in ANSWERABLE_TASK_STATUSES and result.get("approved_parent_ids")
    }
    verified_tasks = []
    for task in state.get("search_tasks", []):
        result = results_by_task.get(task.get("task_id"), {})
        parent_ids = list(dict.fromkeys(
            parent_id for parent_id in result.get("approved_parent_ids", [])
            if isinstance(parent_id, str) and parent_id
        ))
        if parent_ids:
            verified_tasks.append({
                "search_task": dict(task),
                "approved_parent_ids": parent_ids,
            })

    history = list(state.get("verified_task_history", []))
    if not verified_tasks:
        return history

    history.append({
        "turn_number": state.get("turn_count", 0),
        "tasks": verified_tasks,
    })
    return history[-MAX_VERIFIED_TASK_HISTORY_TURNS:]


def _normalize_search_task_scope(task: dict) -> dict | None:
    """Validate an LLM task's product and intent filters before retrieval."""
    normalized = dict(task)
    angle_groups = _normalize_angle_groups(normalized)
    if not angle_groups:
        return None
    product_tags = _normalized_product_tags(normalized.get("target_commercial_product"))
    intents = normalized.get("target_intent", [])
    if isinstance(intents, str):
        intents = [intents]
    intent_tags = []
    for raw_intent in intents:
        intent = str(raw_intent).strip()
        if intent and intent not in intent_tags:
            intent_tags.append(intent)

    # Keep only products that support at least one requested intent, then keep
    # only intents supported by at least one remaining product. This permits a
    # controlled multi-product expansion without accepting an invalid matrix
    # scope such as ALL + Application.
    product_tags = [
        product for product in product_tags
        if any(intent in PRODUCT_INTENT_MATRIX[product] for intent in intent_tags)
    ]
    intent_tags = [
        intent for intent in intent_tags
        if any(intent in PRODUCT_INTENT_MATRIX[product] for product in product_tags)
    ]
    if not product_tags or not intent_tags:
        return None

    normalized["target_commercial_product"] = product_tags[0] if len(product_tags) == 1 else product_tags
    normalized["target_intent"] = intent_tags
    normalized["angle_groups"] = angle_groups
    direct_query = str(
        normalized.get("direct_query")
        or normalized.get("customer_component")
        or normalized.get("subquery")
        or ""
    ).strip()
    if direct_query:
        normalized["direct_query"] = direct_query
    # Keep the base flat fields during the migration for log readers and any
    # historical code paths, but retrieval reads angle_groups exclusively.
    base_group = angle_groups[0]
    for field in _ANGLE_FIELDS:
        normalized[field] = base_group[field]
    return normalized


def _retry_expansion_plan(retry_tasks: list[dict], retry_counts: dict[str, int]) -> list[dict]:
    """Describe the deterministic precision-to-recall stage for each retry."""
    stages = {
        2: {
            "stage": "INTENT_EXPANSION",
            "instruction": "Deterministic: the backend expands intent scope to every valid intent for the task's current product(s). No model judgment is used for this stage.",
        },
        3: {
            "stage": "PRODUCT_EXPANSION",
            "instruction": "Evaluate the feedback status. If INSUFFICIENT, the previous search failed across every intent already valid for the current product; consider adding one plausible, matrix-valid related product. If PARTIALLY_SUFFICIENT, be conservative: add a product only if it has a direct, meaningful relationship to the specific remaining gap. Never remove the original product. Do not specify intents for an added product — the backend expands its full intent scope automatically.",
        },
        4: {
            "stage": "ANGLE_EXPANSION",
            "instruction": "Keep the exact product and intent filters. Preserve every existing angle group unchanged and append one complete new angle group that targets the missing detail in the sufficiency feedback.",
        },
    }
    plan = []
    for task in retry_tasks:
        next_attempt = retry_counts.get(task.get("task_id"), 0) + 1
        plan.append({
            "task_id": task.get("task_id"),
            "next_attempt": next_attempt,
            **stages.get(next_attempt, stages[4]),
        })
    return plan


def _retrieval_mode_for_task(task: dict, retry_counts: dict[str, int]) -> str:
    """Choose retrieval representation independently of the LLM.

    A task keeps its resolved customer subquery as the default retrieval
    anchor through both filter-widening attempts (intent, then product).
    Multi-angle RRF is reserved for the final, feedback-guided attempt.
    """
    retry_count = retry_counts.get(task.get("task_id"), 0)
    return "ANGLE_GROUP_RRF" if retry_count >= 3 else "DIRECT"


def _full_intent_scope(products: list[str]) -> list[str]:
    """Union every matrix-valid intent across one or more products.

    Used both for deterministic intent expansion and for a product newly
    added at the PRODUCT_EXPANSION stage: once a product is in scope,
    opening its full intent list is free (Chroma's metadata filter costs
    nothing extra per value) and carries none of the cross-product
    relevance risk that a blind product-widening guess would.
    """
    full_intents = []
    for product in products:
        for intent in PRODUCT_INTENT_MATRIX.get(product, []):
            if intent not in full_intents:
                full_intents.append(intent)
    return full_intents


def _expand_intent_to_full(task: dict) -> dict:
    """Deterministically widen a task's intent scope to every intent valid
    for its current product(s) — no LLM judgment involved. A task carrying
    two products already (for example, from an ordered array) expands the
    union of both products' valid intents, not just the first one's."""
    products = _normalized_product_tags(task.get("target_commercial_product"))
    revision = dict(task)
    revision["target_intent"] = _full_intent_scope(products)
    return revision


def _merge_retry_task(prior_task: dict, revision: dict, expansion: dict) -> dict:
    """Deterministically preserve cumulative retry state.

    The LLM proposes a refinement only at the PRODUCT_EXPANSION and
    ANGLE_EXPANSION stages; it must never be able to erase prior angle
    groups, products, intents, task identity, or the resolved subquery.
    """
    stage = expansion.get("stage", "")
    prior_groups = _normalize_angle_groups(prior_task)
    revision_groups = _normalize_angle_groups(revision)
    merged = dict(prior_task)
    merged["task_id"] = prior_task.get("task_id")
    merged["customer_component"] = prior_task.get("customer_component", "")
    # The direct query is a stable, task-isolated subquery. Retry feedback may
    # alter filters or add angle groups, but it must not overwrite the
    # customer's original task wording.
    merged["direct_query"] = prior_task.get("direct_query", prior_task.get("customer_component", ""))

    if stage == "ANGLE_EXPANSION":
        prior_ids = {group["group_id"] for group in prior_groups}
        expected_id = f"retry_{expansion.get('next_attempt', 2)}"
        new_group = next((group for group in revision_groups if group["group_id"] == expected_id), None)
        if new_group is None:
            new_group = next((group for group in revision_groups if group["group_id"] not in prior_ids), None)
        if new_group is not None:
            new_group = {**new_group, "group_id": expected_id}
            merged["angle_groups"] = [*prior_groups, new_group]
        else:
            # Do not silently replace an established semantic representation
            # with a malformed retry response. The following retry can still
            # refine this task safely and the telemetry makes the omission
            # visible.
            merged["angle_groups"] = prior_groups
            print(f"   ⚠️ [ANGLE EXPANSION OMITTED]: Task '{merged['task_id']}' returned no distinct retry group.")
        merged["target_commercial_product"] = prior_task.get("target_commercial_product")
        merged["target_intent"] = prior_task.get("target_intent", [])
    elif stage == "INTENT_EXPANSION":
        # Fully deterministic: `revision` already carries the full valid
        # intent set for the current product(s) from _expand_intent_to_full.
        merged["angle_groups"] = prior_groups
        merged["target_commercial_product"] = prior_task.get("target_commercial_product")
        merged["target_intent"] = revision.get("target_intent", prior_task.get("target_intent", []))
    elif stage == "PRODUCT_EXPANSION":
        prior_products = _normalized_product_tags(prior_task.get("target_commercial_product"))
        proposed_products = _normalized_product_tags(revision.get("target_commercial_product"))
        prior_intents = prior_task.get("target_intent", [])
        prior_intents = [prior_intents] if isinstance(prior_intents, str) else prior_intents

        products = list(dict.fromkeys([*prior_products, *proposed_products]))
        new_products = [product for product in proposed_products if product not in prior_products]
        merged["angle_groups"] = prior_groups
        merged["target_commercial_product"] = products[0] if len(products) == 1 else products
        # A newly added product's own intent scope is opened in full too —
        # the LLM is not asked to guess intents for it (see instruction
        # above); a narrow intent chosen for the ORIGINAL product has no
        # guaranteed relevance to a different product.
        merged["target_intent"] = list(dict.fromkeys([*prior_intents, *_full_intent_scope(new_products)]))
    else:
        merged["angle_groups"] = prior_groups

    return _normalize_search_task_scope(merged) or prior_task

_DECLINE_CATEGORY_REASONS = {
    "non_compliant": "it is outside the assistance I can provide under banking compliance requirements",
    "non_financial": "I can only help with Horizon Bank-related questions",
    "out_of_scope_product": "it's not a product or service Horizon currently offers",
}


def _decline_message(decline_tasks: list, scope: str) -> str:
    """Deterministic, category-aware decline sentence. No LLM involved, so
    it cannot itself fail an audit. `scope` is "your request" for an
    all-declined turn (node_10) or "the rest of your request" for a mixed
    turn falling back after repeated audit failure (node_11). Falls back to
    a generic reason when a turn mixes more than one decline category."""
    categories = {task.get("reason_category") for task in decline_tasks if task.get("reason_category")}
    if len(categories) == 1:
        category = next(iter(categories))
        reason = _DECLINE_CATEGORY_REASONS.get(category)
        if reason:
            return f"Sorry, for {scope}, I can't provide an answer — {reason}."
    return f"Sorry, for {scope}, I can't provide an answer due to policy."


def _append_decline_suffix(response: str, decline_tasks: list) -> str:
    """Terminal safety net (node_11) when an LLM-drafted decline explanation
    fails outbound audit repeatedly. The normal path lets node_04b draft the
    decline explanation directly, so this only ever fires after that
    attempt has already been discarded."""
    if not decline_tasks:
        return response
    suffix = _decline_message(decline_tasks, "the rest of your request")
    return f"{response}\n\n{suffix}" if response else suffix


def fetch_placeholders_from_db(placeholders: list) -> dict:
    """Fetches exact values for a list of placeholders from the SQLite Precision_Reference_Table."""
    if not placeholders:
        return {}
    
    conn = sqlite3.connect(SQLITE_DB_PATH)
    cursor = conn.cursor()
    
    # NOTE: Adjust 'placeholder_id' and 'value' if your Database Engineer used different column names.
    placeholders_str = ",".join("?" * len(placeholders))
    query = f"SELECT placeholder, exact_value FROM Precision_Reference_Table WHERE placeholder IN ({placeholders_str})"
    
    try:
        cursor.execute(query, placeholders)
        rows = cursor.fetchall()
        return {row[0]: str(row[1]) for row in rows}
    except Exception as e:
        print(f"🚨 [DB ERROR] Precision table query failed: {e}")
        return {}
    finally:
        conn.close()

# --- V2.1: DECOUPLED MATRIX GUIDELINES ---
PRODUCT_INTENT_MATRIX = {
    "ALL": ["Account Management", "Authentication And Security", "Features And Limits", "General Inquiries", "Payments And Transfers", "Rewards And Promotions"],
    "Credit_Card": ["Account Management", "Application", "Authentication And Security", "Card Logistics", "Eligibility", "Features And Limits", "Fees And Charges", "General Inquiries", "Interest And Rates", "Loan And Credit Management", "Payments And Transfers", "Profile Updates", "Rewards And Promotions", "Transaction Disputes"],
    "Debit_Card": ["Account Management", "Application", "Authentication And Security", "Card Logistics", "Eligibility", "Features And Limits", "Fees And Charges", "General Inquiries", "Payments And Transfers", "Profile Updates", "Rewards And Promotions", "Transaction Disputes"],
    "Deposit_Account": ["Account Management", "Application", "Authentication And Security", "Branch And ATM Locator", "Card Logistics", "Eligibility", "Features And Limits", "Fees And Charges", "General Inquiries", "Interest And Rates", "Payments And Transfers", "Profile Updates", "Rewards And Promotions", "Transaction Disputes"],
    "Horizon_APP": ["Account Management", "Authentication And Security", "Features And Limits", "General Inquiries", "Payments And Transfers", "Profile Updates", "Rewards And Promotions"],
    "Insurance": ["Features And Limits", "General Inquiries", "Rewards And Promotions", "Wealth And Insurance"],
    "Mutual_Funds": ["Fees And Charges", "General Inquiries", "Wealth And Insurance"],
    "Personal_Loan": ["Account Management", "Application", "Authentication And Security", "Eligibility", "Features And Limits", "Fees And Charges", "General Inquiries", "Interest And Rates", "Loan And Credit Management", "Rewards And Promotions"],
    "Rewards_Program": ["Authentication And Security", "Features And Limits", "General Inquiries", "Payments And Transfers", "Rewards And Promotions"],
    "Stocks": ["Fees And Charges", "General Inquiries", "Wealth And Insurance"]
}

INTENT_GUIDELINE = """
1. Acquisition & Setup (Getting Started)
Application: How to apply, steps involved, document submission, or checking status. 
Eligibility: Qualify for a product, income/age requirements, existing relationships. 
Card Logistics: Physical handling. Activation, delivery, reporting lost/stolen, replacement. 
2. Product Knowledge (Understanding the Asset)
Features And Limits: General mechanics, benefits, usage limits, overseas use, digital wallets. 
Fees And Charges: Costs, annual fees, fall-below fees, penalties, fee waivers. 
Interest And Rates: Interest accumulation, base rates, bonus criteria, payout timing. 
3. Daily Servicing (Account & Money Movement)
Account Management: Checking balances, dormancy, linking accounts, account cancellation. 
Profile Updates: Address, phone number, and email updates. 
Payments And Transfers: PayNow, FAST, GIRO, bill payments, cheque deposits, transfer errors. 
Transaction Disputes: Unrecognized charges, double billing, refunds, declined transactions. 
4. Specialized Financial Services
Loan And Credit Management: Repayment schedules, installments, balance transfers, hardship. 
Wealth And Insurance: Investments, stocks, mutual funds, auto-invest, insurance premiums, claims. 
Rewards And Promotions: Earning points, cashback, merchant coupons, referral bonuses. 
5. Technical & General Support
Authentication And Security: Login issues, password/PIN resets, OTP failures, tokens, fraud. 
Branch And ATM Locator: Finding physical infrastructure. 
General Inquiries: Catch-all. App navigation, UI bugs, compliance, generic notifications.
"""

def _ui_option_texts(options: list) -> list[str]:
    """Return every customer-visible and submitted value in chip payloads.

    Legacy payloads use a string, while structured chips use ``label`` for the
    compact visible text and ``message`` for the clearer input sent on click.
    Both values are customer-facing and must pass the same safety checks.
    """
    texts = []
    for option in options:
        if isinstance(option, str):
            texts.append(option)
        elif isinstance(option, dict):
            for field in ("label", "message"):
                value = option.get(field, "")
                if isinstance(value, str) and value.strip():
                    texts.append(value)
    return texts


def _llm_text(source: dict, key: str, fallback_key: str | None = None, default: str = "") -> str:
    """Read an optional string field from an LLM JSON response.

    dict.get(key, default) only applies its default when the key is
    entirely absent — if the model explicitly returns `"key": null` for an
    optional field, .get() still returns None, and str(None) silently
    produces the literal text "None" in customer-facing output instead of
    an empty string. This treats an explicit null the same as a missing key.
    """
    value = source.get(key)
    if value is None and fallback_key:
        value = source.get(fallback_key)
    if value is None:
        value = default
    return str(value).strip()


def _llm_list(source: dict, key: str) -> list:
    """Read an optional list field from an LLM JSON response.

    Same null-vs-missing gap as _llm_text: an explicit `"key": null` must
    not be iterated directly, or it raises TypeError instead of behaving
    like an empty list.
    """
    value = source.get(key)
    return value if isinstance(value, list) else []


def _normalize_ui_options(options: list) -> list:
    """Keep valid structured chips and legacy string chips, at most two."""
    normalized = []
    for option in options if isinstance(options, list) else []:
        if isinstance(option, str) and option.strip():
            normalized.append(option.strip())
        elif isinstance(option, dict):
            label = option.get("label", "")
            message = option.get("message", "")
            if isinstance(label, str) and label.strip() and isinstance(message, str) and message.strip():
                normalized.append({"label": label.strip(), "message": message.strip()})
    return normalized[:2]


def _terminal_retrieval_feedback(
    task_id: str,
    resolved_subquery: str,
    resolution_status: str = "CLARIFICATION_REQUIRED",
) -> dict:
    """Create a non-evidential marker for a terminally unresolved task.

    Rejected retrieval candidates and evidence-delta facts may inform a retry,
    but they never cross this boundary into planning, generation, or outbound
    groundedness.  The resolved customer request is sufficient to produce a
    safe, non-factual availability notice when the retry budget is exhausted.
    """
    return {
        "task_id": task_id,
        "type": "TERMINAL_RETRIEVAL_FEEDBACK",
        "resolved_subquery": resolved_subquery,
        "resolution_status": resolution_status,
    }


def _hydrate_parent_ids(parent_ids: list[str]) -> list[dict]:
    """Read exact allow-listed parent records from the authoritative FAQ DB."""
    ordered_ids = [parent_id for parent_id in parent_ids if isinstance(parent_id, str) and parent_id]
    return RAGEngine().hydrate_parent_ids(ordered_ids) if ordered_ids else []


def _response_evidence_manifest(state: AgentState) -> list[dict]:
    """Return the exact parent-ID selections permitted for outbound answers.

    The manifest is the single evidence contract shared by response generation
    and normal-response groundedness. It deliberately contains identifiers,
    not hydrated text or stale turn state.
    """
    approved_by_task = {
        result.get("task_id"): set(result.get("approved_parent_ids", []))
        for result in state.get("task_results", [])
        if result.get("status") in ANSWERABLE_TASK_STATUSES
    }
    # Presentation is intentionally decoupled from retrieval-task boundaries.
    # The response and outbound groundedness audit therefore share the full
    # approved evidence set. Optional element-level source_task_ids narrow
    # provenance for logs and audits but never decide what may be displayed.
    return [
        {"task_id": task_id, "approved_parent_ids": sorted(parent_ids)}
        for task_id, parent_ids in approved_by_task.items()
        if task_id and parent_ids
    ]


def _hydrate_response_evidence(state: AgentState) -> list[dict]:
    """Hydrate the shared outbound evidence manifest for a single consumer."""
    return [
        {**entry, "approved_parents": _hydrate_parent_ids(entry["approved_parent_ids"])}
        for entry in _response_evidence_manifest(state)
    ]


def _build_conversation_context(state: AgentState) -> str:
    """Label the distinct advisory context layers available to a turn."""
    rolling_summary = state.get("compacted_history_summary", "").strip()
    # `working_context` is retained as a compatibility fallback for graph
    # sessions created before last_session_summary was introduced.
    last_session_summary = state.get("last_session_summary", state.get("working_context", "")).strip()
    history = state.get("chat_history", [])[-20:]
    trusted_history = [
        message for message in history
        if not isinstance(message, HumanMessage)
        or message.additional_kwargs.get("trust_status") == "trusted"
    ]
    recent_history = "\n".join(
        f"{'User' if isinstance(message, HumanMessage) else 'AI'}: {message.content}"
        for message in trusted_history
    ) or "None"
    return (
        f"Last Session Recall (advisory only; not current state):\n{last_session_summary or 'None'}\n\n"
        f"Current-Session Rolling Context (advisory only):\n{rolling_summary or 'None'}\n\n"
        f"Current Session Trusted History (latest 10 turns):\n{recent_history}"
    )


def _build_user_baseline_context(state: AgentState) -> str:
    """Return optional personalization context for selected non-safety prompts."""
    baseline = state.get("user_baseline_summary", "").strip()
    return baseline or "None"


def _build_greeting_memory_context(last_session_summary: str, user_baseline: str) -> str:
    """Label durable sources used to personalize a new-session greeting."""
    return (
        f"Latest Session Memory:\n{last_session_summary.strip() or 'None'}\n\n"
        f"User Baseline Memory:\n{user_baseline.strip() or 'None'}"
    )


def _generate_returning_customer_greeting(
    last_session_summary: str,
    user_baseline: str,
    user_profile: dict = None,
) -> tuple[str, list[str]]:
    """Create a concise welcome and optional, memory-grounded continuation chips.

    Durable memory provides conversational context only; it never restores a
    graph workflow. First-time customers see a clean greeting with no
    artificial suggested prompts.
    """
    memory_context = ""
    if last_session_summary.strip() or user_baseline.strip():
        memory_context = _build_greeting_memory_context(last_session_summary, user_baseline)

    user_profile = user_profile or {}
    literacy_level = user_profile.get("financial_literacy", "intermediate")
    if literacy_level == "beginner":
        literacy_instruction = "Beginner (Use simple financial terms. Explain financial terms when they appear in the response.)"
    elif literacy_level == "professional":
        literacy_instruction = "Professional (Use professional financial terms freely. No explanation of financial terms necessary.)"
    else:
        literacy_instruction = "Intermediate (Use normal financial terms. Explain only complex financial terms when they appear in the response.)"

    prompt = f"""
    You are Horizon, a banking assistant welcoming a customer.
    Write a warm, concise greeting of at most two sentences. 
    If conversation context is provided below, frame the prior session as FAQ guidance that
    Horizon previously explained, and neutrally invite the customer to continue the discussion. 
    If no conversation context is provided, write a generic, welcoming opening greeting.

    STYLE & PERSONALIZATION INSTRUCTIONS:
    - User Nickname: {user_profile.get("nickname") or "Not provided"} (Use naturally where socially appropriate; do not mechanically force it into every response)
    - Natively generate response in: {user_profile.get("language") or "English"}
    - Tone: {user_profile.get("tone", "professional")}
    - Financial Literacy Depth: {literacy_instruction}
    - Emojis: {user_profile.get("emoji", "enabled")}

    Important: this is a brand-new session. Do not claim that a workflow,
    application, transfer, plan, approval, or pending action is still active.
    Do not ask for age, income, residency, identity, documents, account data,
    or any other customer information. Do not tell the customer to complete a
    banking action or imply Horizon can complete one for them. Do not invent
    facts, provide banking advice, or use markdown.

    Conversation Context:
    {memory_context or "None"}

    Good style: "Welcome back. Last time, I shared the FAQ steps for applying
    for a personal loan, including the information needed at the next stage.
    Would you like to continue that discussion or explore something else?"

    Suggested actions:
    - Provide zero to two only when the prior context supports a concrete FAQ
      question that Horizon can answer after retrieval.
    - Each option MUST be an object with two values: a concise visible `label`
      (2-6 words) and a complete `message` that will be sent when clicked.
      The message must be a specific banking FAQ question that names both a
      concrete product/topic from the prior context and a factual aspect to
      explain, such as label "Instant Loan application" with message "How do
      I apply for an Instant Loan?".
    - Do NOT return generic conversational labels such as "Continue",
      "Continue discussing...", "Ask another question", "Explore something
      else", or "Learn more". They are not useful customer inputs.
    - Do not ask the customer to provide personal data, documents, income,
      residency, identity, or an application status. Do not invent a product,
      process, or pending task.
    - Return an empty array when the prior context does not identify a concrete
      answerable product/topic and FAQ aspect. An empty array is preferred to a
      vague suggestion.

    Output JSON only: {{"greeting": "your greeting", "options": [{{"label": "short topic", "message": "complete FAQ question"}}]}}.
    """
    try:
        raw_response = call_groq_lpu(prompt).strip()
        parsed = _parse_json_robustly(raw_response)
        greeting = parsed.get("greeting", "").strip()
        if greeting:
            raw_options = parsed.get("options", [])
            if not isinstance(raw_options, list):
                raw_options = []
            options = [option for option in _normalize_ui_options(raw_options) if isinstance(option, dict)]
            return greeting, options
    except Exception as error:
        print(f"⚠️ [MEMORY SERVICE] Returning-customer greeting generation failed: {error}")

    return get_message(state.get("user_profile", {}).get("language", "English"), "greeting_fallback"), []


# --- PHASE 0: INGESTION & INGEST GUARDS ---

def node_00_context_hydrator_and_greeting(state: AgentState):
    print("🟢 [NODE EXECUTING] Node 00: Context Hydrator & Greeting")
    customer_id = state.get("user_id", "USR_999")
    summary = LongTermMemoryManager.fetch_compact_footprint(customer_id)
    user_baseline = LongTermMemoryManager.fetch_user_baseline(customer_id)
    user_profile = LongTermMemoryManager.get_user_preferences(customer_id)

    greeting, greeting_options = _generate_returning_customer_greeting(summary, user_baseline, user_profile)

    return {
        # A new login begins a clean graph.  Only this compact, durable
        # conversation context crosses the session boundary; no plan, task
        # evidence, retry data, or other execution state does.
        **VOLATILE_STATE_RESET,
        "user_profile": user_profile,
        "chat_history": [],
        "history_sequence_offset": 0,
        "compacted_history_summary": "",
        "last_session_summary": summary,
        "user_baseline_summary": user_baseline,
        "active_product_intents": [],
        "active_plan": {},
        "completed_steps": [],
        "process_slots": {},
        "verified_task_history": [],
        "final_draft": greeting,
        "final_answer": greeting,
        "final_clarification": "",
        "working_context": summary,
        "ui_action_payload": {
            "uiType": "chips" if greeting_options else "text",
            "options": greeting_options,
        },
        "preflight_status": "initial_greeting",
    }

def node_01_deterministic_ingest_gate(state: AgentState):
    print("🟢 [NODE 01] Executing Deterministic Ingest Gate")
    user_msg = state.get("user_input", "")
    history = list(state.get("chat_history", []))
    
    if user_msg and user_msg not in {"__INIT__", "**INIT**"}:
        # Keep every customer message for the visible transcript, but do not
        # allow it into summarized prompt context until the semantic gate
        # explicitly promotes it.
        history.append(HumanMessage(content=user_msg, additional_kwargs={"trust_status": "untrusted"}))
        print(f"💾 [MEMORY] User input stored as untrusted. History length: {len(history)}")

    # The confirmation control is a deterministic UI action, not a new banking
    # request. Route it directly to the shared escalation handler after keeping
    # the customer's confirmation in the visible conversation transcript.
    if user_msg.strip().casefold() == ESCALATION_CONFIRMATION_MESSAGE.casefold():
        print("   🤝 [ESCALATION]: Human-transfer confirmation accepted.")
        log_event("escalation.transfer.confirmed", confirmation_source="ui")
        return {
            **VOLATILE_STATE_RESET,
            "preflight_status": "escalation_confirmation_accepted",
            "chat_history": history,
        }

    # 1. Sweep for hardcoded threats (Garbled text, SQL drops, XSS)
    if "<script>" in user_msg.lower() or "DROP TABLE" in user_msg.upper():
        print("   🚫 [INGEST VETO]: Malicious string detected.")
        return {
            **VOLATILE_STATE_RESET, # 🧹 Wipe state
            "preflight_status": "error_inbound_violation",
            "chat_history": history,
        }

    # 2. Sweep for placeholder injections
    if "{{" in user_msg or "}}" in user_msg:
        print("   🚫 [INGEST VETO]: Placeholder injection detected.")
        return {
            **VOLATILE_STATE_RESET, # 🧹 Wipe state
            "preflight_status": "error_inbound_violation",
            "chat_history": history,
        }

    # Pass the gate and cleanly initialize the turn
    return {
        **VOLATILE_STATE_RESET, # 🧹 Wipe state for the new turn
        "preflight_status": "pass",
        "chat_history": history,
    }


def node_01b_load_user_profile(state: AgentState):
    print("🟢 [NODE 01b] Loading User Preferences")
    user_id = state.get("user_id")
    if not user_id:
        return {"user_profile": {}}
    
    try:
        from backend.app.services.memory_service import LongTermMemoryManager
        profile = LongTermMemoryManager.get_user_preferences(user_id)
        print(f"   👤 [PROFILE]: Loaded preferences for {user_id}")
        return {"user_profile": profile}
    except Exception as e:
        print(f"⚠️ [PROFILE ERROR]: {str(e)}")
        return {"user_profile": {}}

# --- PHASE 1: INBOUND SAFETY, TRIAGE, AND TASK DECOMPOSITION ---
def node_02c_inbound_semantic_gate(state: AgentState):
    print("🟢 [NODE 02c] Executing Inbound Semantic Gate (Prompt Guard)")
    user_input = state.get("user_input", "")
    
    try:
        # ONLY pass the user_input string.
        res = call_groq_lpu(user_input, model_id=MODEL_D_PROMPT_GUARD)

        # 🐛 FIX: Parse the raw float probability from the model
        try:
            probability_score = float(res.strip())
            # A message must remain below the 10% risk threshold to be trusted.
            is_safe = probability_score < 0.10
        except ValueError:
            # Fallback just in case the API ever returns text instead of a number
            is_safe = "safe" in str(res).lower()
            
    except Exception as e:
        print(f"🚨 [INBOUND GATE ERROR]: {e}")
        # Fail-closed security design
        return {"preflight_status": "error_malicious"} 
        
    if not is_safe:
        print(f"   🚫 [INBOUND VETO]: Malicious intent detected by Prompt Guard. Attack Probability: {res}")
        return {"preflight_status": "error_malicious"}
        
    print(f"   ✅ [INBOUND PASS]: User input is SAFE from injections. Attack Probability: {res}")
    history = list(state.get("chat_history", []))
    if history and isinstance(history[-1], HumanMessage):
        latest_message = history[-1]
        history[-1] = HumanMessage(
            content=latest_message.content,
            additional_kwargs={**latest_message.additional_kwargs, "trust_status": "trusted"},
        )
    return {"preflight_status": "pass", "chat_history": history}


def node_02d_user_journey_classifier_and_triage(state: AgentState):
    """Tool Calling v2.0 whole-turn gate and financial-subquery extractor.

    Non-compliance is a whole-turn veto.  Only after that check does this node
    recognise escalation, then emit *financial-only* subqueries.  The intent
    formulator therefore never sees unrelated or non-compliant wording.
    """
    print("🟢 [NODE 02d] Executing Request Triage & Decomposition")
    user_msg = state.get("user_input", "")
    history_str = _build_conversation_context(state)
    user_baseline = _build_user_baseline_context(state)
    active_plan = state.get("active_plan", {})
    has_active_plan = bool(active_plan.get("steps"))
    has_retrieved_knowledge = any(
        result.get("status") in ANSWERABLE_TASK_STATUSES and result.get("approved_parent_ids")
        for result in state.get("task_results", [])
    )
    recalled_context = state.get("last_session_summary", state.get("working_context", "")).strip()

    prompt = f"""
    WHOLE-TURN FINANCIAL TRIAGE FOR A READ-ONLY HORIZON BANK ASSISTANT

    MAS COMPLIANCE CONTEXT (screening only; never quote it):
    {REGULATORY_LICENSING_CONTEXT}

    Trusted Conversation Context: {history_str}
    User Baseline (advisory only): {user_baseline}
    Current Graph Context: active_plan={has_active_plan}, retrieved_knowledge={has_retrieved_knowledge}
    Recalled Prior-Session Context Is Available: {bool(recalled_context)}
    Latest User Input: "{user_msg}"

    First, resolve the customer's TRUE REQUEST internally. Interpret the
    Latest User Input together with Trusted Conversation Context. A short
    reply, confirmation, correction, reason, preference, pronoun, or ellipsis
    may continue the immediately relevant prior request. For example, "yes"
    can confirm a prior escalation or prohibited action; "the card is ugly"
    after a card-replacement discussion can continue that banking request.
    Do not invent a banking fact, product capability, eligibility, procedure,
    or customer objective that is not supported by the latest input and trusted
    context. Keep the original Latest User Input as the authoritative wording
    for any later customer-facing response.

    Apply these rules to the resolved TRUE REQUEST in this exact order:
    1. NON_COMPLIANT — Assess the complete resolved TRUE REQUEST exclusively
       against the MAS COMPLIANCE CONTEXT above. Return NON_COMPLIANT only when
       the customer asks Horizon or this assistant to perform, process,
       facilitate, provide tactics for, or advise on an action whose fulfilment
       would conflict with that shared context. Return no components. Do not
       preserve an otherwise valid portion for later retrieval. Do not create a
       second or abbreviated definition of prohibited conduct.
       Do NOT return NON_COMPLIANT merely because the customer asks whether an
       action is allowed, describes a possible improper action, expresses
       uncertainty, corrects or denies a premise, or mentions an action that
       Horizon itself could not fulfil. When such a question is banking-related,
       classify it as VALID_REQUEST and let the evidence-bound downstream path
       answer only what approved information supports. Never turn that route
       into help, tactics, or advice for deception, evasion, or other prohibited
       conduct.
    2. ESCALATION — Only if compliant: if the resolved TRUE REQUEST is an
       explicit request or contextual confirmation to transfer to a human
       agent or manager, return ESCALATION with no components.
    3. VALID_REQUEST — Only if compliant and not escalation: if the resolved
       TRUE REQUEST contains at least one financial/banking need, decompose
       that meaning into distinct components. Emit ONLY FINANCIAL components.
       IMPORTANT LANGUAGE RULE: You MUST translate the semantic meaning of each
       financial component into English, regardless of the language the user
       typed in. This ensures downstream retrieval matches our English knowledge base.
       A continuation of a preceding financial request remains financial even
       when the latest wording supplies only its reason, preference, or
       correction. Omit any genuinely casual or unrelated wording, set
       has_non_financial_content=true when any was omitted, and never emit an
       unrelated component. Preserve only a short `non_financial_context`
       attention cue for a customer preference, feeling, greeting, thanks, or
       other conversational aside when it would help the eventual reply feel
       natural. It is not customer-facing source text: the original Latest
       User Input remains the authoritative wording for any reply. This context
       is never a banking request, tool input, factual evidence, or basis for a
       banking claim.
    4. NON_FINANCIAL — Otherwise, when the resolved TRUE REQUEST contains no
       compliant banking need and no escalation. Return no components.

    For VALID_REQUEST components:
    - A component is one independently answerable banking topic; do not split
      a coherent topic artificially and do not impose a numerical cap.
    - Express each component as a self-contained statement of the resolved
      TRUE REQUEST. Resolve pronouns and short continuations from Trusted
      Conversation Context into that wording.
    - A casual greeting accompanying a financial request is not a component.
    - Do not determine product, intent, availability, or a tool. Those are
      downstream responsibilities.

    Output JSON only:
    {{"triage_status":"NON_COMPLIANT" | "ESCALATION" | "VALID_REQUEST" | "NON_FINANCIAL", "financial_subqueries":[{{"subquery_id":"subquery_1","customer_component":"..."}}], "has_non_financial_content":true | false, "non_financial_context":["short internal attention cue"]}}
    """
    try:
        res = _parse_json_robustly(call_groq_lpu(prompt, model_id=MODEL_E_TRIAGE))
        triage_status = str(res.get("triage_status", "NON_FINANCIAL")).upper()
        components = res.get("financial_subqueries", []) if isinstance(res.get("financial_subqueries"), list) else []
        has_non_financial_content = bool(res.get("has_non_financial_content", False))
        non_financial_context = [
            str(item).strip() for item in res.get("non_financial_context", [])
            if isinstance(item, str) and str(item).strip()
        ][:3]
    except Exception as e:
        print(f"🚨 [NODE 02d EXCEPTION]: {e}")
        triage_status = "NON_FINANCIAL"
        components = []
        has_non_financial_content = False
        non_financial_context = []
    if triage_status not in {"NON_COMPLIANT", "ESCALATION", "VALID_REQUEST", "NON_FINANCIAL"}:
        triage_status = "NON_FINANCIAL"

    print(f"   🧠 [TRIAGE STATUS]: {triage_status}")

    financial_subqueries = []
    if triage_status == "VALID_REQUEST":
        for index, component in enumerate(components, start=1):
            if not isinstance(component, dict):
                continue
            customer_component = str(component.get("customer_component", "")).strip()
            if not customer_component:
                continue
            financial_subqueries.append({
                "subquery_id": str(component.get("subquery_id", "")).strip() or f"subquery_{index}",
                "customer_component": customer_component,
            })
    if triage_status == "VALID_REQUEST" and not financial_subqueries:
        # A malformed VALID_REQUEST cannot be allowed to reach tools.
        triage_status = "NON_FINANCIAL"
        has_non_financial_content = False
        non_financial_context = []

    mapped_preflight = "tool_planning" if triage_status == "VALID_REQUEST" else triage_status.lower()
    print(f"   🔎 [TRIAGE DECOMPOSITION]: {len(financial_subqueries)} financial subquery(ies) | non-financial content={has_non_financial_content}")
    log_event(
        "triage.financial_boundary",
        triage_status=triage_status,
        financial_subquery_ids=[item["subquery_id"] for item in financial_subqueries],
        has_non_financial_content=has_non_financial_content,
        non_financial_context=non_financial_context,
    )

    return {
        "triage_status": triage_status,
        "preflight_status": mapped_preflight,
        "triaged_subqueries": financial_subqueries,
        "financial_subqueries": financial_subqueries,
        "has_non_financial_content": has_non_financial_content,
        "non_financial_context": non_financial_context,
        "decline_tasks": [],
    }

def node_02e_intent_classifier_and_query_formulator(state: AgentState):
    """n2e: formulate retrieval, clarification, or scope-decline resolutions for
    the components n2d already decomposed and compliance/relevance-screened.
    Owns matrix-dependent judgment only; never re-decomposes raw input."""
    print("🟢 [NODE 02e] Executing Multi-Task Query Formulator")
    user_msg = state.get("user_input", "")
    all_financial_subqueries = state.get("financial_subqueries", state.get("triaged_subqueries", []))
    active_rag_subquery_ids = {
        task.get("subquery_id")
        for task in state.get("tool_tasks", [])
        if isinstance(task, dict)
        and task.get("tool_name") == "rag"
        and task.get("status") in {"pending", "running"}
    }
    # Node 02e is now internal to the RAG executor. It receives only the
    # financial components assigned to a live RAG tool task.
    triaged_subqueries = [
        {
            "component_id": item.get("subquery_id"),
            "customer_component": item.get("customer_component", ""),
        }
        for item in all_financial_subqueries
        if isinstance(item, dict) and item.get("subquery_id") in active_rag_subquery_ids
    ]
    active_matrix = state.get("active_product_intents", [])
    active_plan = state.get("active_plan", {})
    has_active_plan = bool(active_plan.get("steps"))
    has_retrieved_knowledge = any(
        result.get("status") in ANSWERABLE_TASK_STATUSES and result.get("approved_parent_ids")
        for result in state.get("task_results", [])
    )
    recalled_context = state.get("last_session_summary", state.get("working_context", "")).strip()
    
    history_str = _build_conversation_context(state)
    user_baseline = _build_user_baseline_context(state)
    
    prior_tasks = state.get("search_tasks", [])
    task_results = state.get("task_results", [])
    retry_feedback = state.get("task_feedback", {})
    retry_task_ids = [
        result.get("task_id") for result in task_results
        if result.get("status") == "RETRY" and result.get("task_id")
    ]
    retry_tasks = [task for task in prior_tasks if task.get("task_id") in retry_task_ids]
    # A retry barrier may defer only the current metadata no-op attempt. The
    # next n2e pass always recomputes this list for its own stage.
    fast_forward_task_ids = []
    new_retry_counts = dict(state.get("task_retry_counts", {}))
    retry_expansion_plan = _retry_expansion_plan(retry_tasks, state.get("task_retry_counts", {}))
    expansion_by_task_id = {expansion.get("task_id"): expansion for expansion in retry_expansion_plan}
    # INTENT_EXPANSION is fully deterministic (see _expand_intent_to_full):
    # widening intent within an already-confirmed product needs no semantic
    # judgment and costs nothing extra at the Chroma filter layer, so these
    # tasks never enter the retry-mode prompt at all. Only tasks that still
    # need a genuine judgment call (PRODUCT_EXPANSION, ANGLE_EXPANSION) are
    # sent to the model.
    deterministic_task_ids = {
        task_id for task_id, expansion in expansion_by_task_id.items()
        if expansion.get("stage") == "INTENT_EXPANSION"
    }
    llm_retry_tasks = [task for task in retry_tasks if task.get("task_id") not in deterministic_task_ids]
    if retry_expansion_plan:
        for expansion in retry_expansion_plan:
            print(f"   🪜 [RETRY STAGE]: Task '{expansion.get('task_id')}' → {expansion.get('stage')} (attempt {expansion.get('next_attempt')})")
        if not llm_retry_tasks:
            print(f"   ⚡ [RETRY MODEL SKIPPED]: All {len(retry_tasks)} retry task(s) resolved deterministically this cycle; no LLM call.")
        elif len(llm_retry_tasks) < len(retry_tasks):
            print(f"   🧠 [RETRY MODEL SCOPED]: Calling model for {len(llm_retry_tasks)} of {len(retry_tasks)} retry task(s); {len(retry_tasks) - len(llm_retry_tasks)} resolved deterministically.")
        log_event(
            "task.retry.stage_plan",
            stages={item.get("task_id"): item.get("stage") for item in retry_expansion_plan},
            llm_task_ids=[task.get("task_id") for task in llm_retry_tasks],
            deterministic_task_ids=list(deterministic_task_ids),
        )
    retry_context = ""
    if llm_retry_tasks:
        llm_retry_feedback = {
            task_id: feedback for task_id, feedback in retry_feedback.items()
            if task_id in {task.get("task_id") for task in llm_retry_tasks}
        }
        retry_context = f"""
        RETRY MODE: Rewrite ONLY these existing tasks. Preserve each task_id exactly.
        Failed tasks: {llm_retry_tasks}
        Task-specific feedback: {llm_retry_feedback}
        """
    
    prompt = f"""
    PRE-DECOMPOSED CUSTOMER COMPONENTS (from Triage; already screened for financial relevance and compliance): {triaged_subqueries}
    This list is the complete, final partition of the customer's request. Do NOT merge, split, drop, or invent components — process every one exactly as given.

    PRODUCT/INTENT MATRIX (Valid Nouns and Verbs):
    {PRODUCT_INTENT_MATRIX}

    INTENT DEFINITION GUIDELINE:
    {INTENT_GUIDELINE}

    Active Context Matrix: {active_matrix}
    Current Graph Context: active_plan={has_active_plan}, retrieved_knowledge={has_retrieved_knowledge}
    Recalled Prior-Session Context Is Available: {bool(recalled_context)}
    Recent Conversation: {history_str}
    User Baseline (advisory only; do not treat it as a current request): {user_baseline}
    Latest User Input (context only; the components above are authoritative): "{user_msg}"
    TASK: Act as a Decoupled Database Search Architect.

    CRITICAL RULES FOR ANGLE QUERIES:
    - NEVER use conversational filler (e.g., "Provide a guide", "Explain how to", "What is").
    - NEVER paraphrase the customer's original question in an angle.
    - Each angle must be a short natural-language PHRASE (8-20 words) that reads like a sentence fragment from an FAQ answer.
    - Use common banking vocabulary only. Do not invent specific product names, amounts, dates, or branch details.

    1. CONTEXTUAL INHERITANCE: Read each component's customer_component text first. A Product or Intent may be inherited only when it is unambiguously established by the current-session trusted history, Active Context Matrix, or an active graph plan. Do not use the user baseline or prior-session recall to fill a missing Product or Intent, unless the component explicitly continues that recalled topic (for example, "yes, continue").
    2. FOR EACH supplied component, evaluate the two mandatory retrieval foundations:
       - PRODUCT: a specific commercial product or valid universal scope ("ALL").
       - INTENT: a specific request type that maps to an intent in the PRODUCT/INTENT MATRIX.
       Determine these foundations from the component and, only when unambiguous, the permitted current-session context in Rule 1.
    3. Assign EVERY component exactly one resolution:
       - SEARCH: use only when BOTH Product and Intent are available and form a valid pair in the Product/Intent Matrix.
       - CLARIFY: use when either mandatory foundation is missing or ambiguous, but the component could plausibly become searchable with more detail from the user. A generic process or policy word alone—such as "application", "fee", "eligibility", "limit", "rate", "requirements", or "processing time"—is an INTENT only. It does not identify a PRODUCT and MUST be CLARIFY unless a product is explicit or unambiguously inherited from permitted context. Likewise, a product name alone without a discernible request type MUST be CLARIFY.
       - DECLINE: use only when the component clearly and confidently names a specific product, account type, or service that does NOT exist anywhere in the PRODUCT/INTENT MATRIX (e.g., margin trading, cryptocurrency trading, or any product with no matrix entry at all). Never use DECLINE merely because wording is vague or underspecified — that is CLARIFY. DECLINE means no amount of user clarification would ever produce a valid matrix pair.
       A component, its wording, a substring of its wording, or a paraphrase of it MUST NOT appear under more than one resolution.
    4. For EACH SEARCH component, formulate exactly one base Angle Group with three distinct semantic angles. Each angle must be a SHORT NATURAL-LANGUAGE PHRASE (8-20 words) that resembles a sentence fragment likely found in an FAQ answer TO THAT COMPONENT'S TOPIC — NOT an FAQ question. The direct customer query is searched separately; your angles must approach from the ANSWER side of the knowledge base.
       - "procedural": A phrase describing the actions, steps, or navigation a customer or bank performs to address this specific component. Should sound like it comes from an FAQ answer's step-by-step instructions for this topic.
       - "policy": A phrase describing the rules, conditions, eligibility criteria, limits, fees, or restrictions that apply to this specific component. Should sound like it comes from an FAQ answer's requirements or conditions section for this topic.
       - "conceptual": A phrase describing what the product, feature, or service in this specific component is and how it works. Should sound like it comes from an FAQ answer's explanatory introduction for this topic.
       DO NOT use comma-separated keywords. DO NOT paraphrase the customer's original question. Use common banking vocabulary only — do not invent specific product names, amounts, dates, or branch details. Each angle MUST target a structurally different part of a hypothetical FAQ answer about the component's topic.
    5. For EACH SEARCH task, extract the exact "target_commercial_product" (Noun) and a list of 1-3 "target_intent" (Verbs) from the Matrix above. Each search task MUST contain task_id. On a new request use task_1, task_2, task_3 and continue numbering as needed. In RETRY MODE preserve the supplied task_id and use its task-specific sufficiency feedback to change the search terms, product, or intent.
    6. INTENT CLARIFICATION: For a CLARIFY component, copy only the ambiguous customer term into clarification.unresolved_request and explain internally whether Product, Intent, or both are missing or ambiguous. Do not guess a product or create a generic task merely to fill the array.
    7. SCOPE DECLINE: For a DECLINE component, set decline.reason to a short, factual statement naming the product/service Horizon does not offer. Only decline when the matrix confirms no such product exists — never guess.
    8. Use "ALL" only when a component is genuinely universal and its Intent is a valid intent listed under "ALL" in the Product/Intent Matrix. "ALL" is not a fallback for an unknown product.

    Output JSON ONLY:
    {{"components": [{{"component_id":"component_1","customer_component":"open an account","resolution":"SEARCH","search_task":{{"task_id":"task_1","angle_groups":[{{"group_id":"base","procedural":"...","policy":"...","conceptual":"..."}}],"target_commercial_product":"Deposit_Account","target_intent":["Application"]}},"clarification":null,"decline":null}},{{"component_id":"component_2","customer_component":"the fee","resolution":"CLARIFY","search_task":null,"clarification":{{"clarification_id":"intent_clarification_1","unresolved_request":"the fee","reason":"No product or service is identified."}},"decline":null}},{{"component_id":"component_3","customer_component":"margin trading account","resolution":"DECLINE","search_task":null,"clarification":null,"decline":{{"reason":"Horizon does not offer margin trading accounts."}}}}]}}
    {retry_context}
    """

    # A retrieval retry is not a fresh interpretation of the whole customer
    # request.  Isolate it in a dedicated prompt so the model cannot reorder
    # components or overwrite a failed task ID with another successful task.
    if llm_retry_tasks:
        prompt = f"""
        PRODUCT/INTENT MATRIX (Valid Nouns and Verbs):
        {PRODUCT_INTENT_MATRIX}

        INTENT DEFINITION GUIDELINE:
        {INTENT_GUIDELINE}

        RETRIEVAL RETRY MODE
        You are a Decoupled Database Search Architect refining failed retrieval
        tasks. This is NOT a new decomposition of the customer's request.

        Latest User Input: "{user_msg}"
        Failed Retrieval Tasks (the only components in scope): {llm_retry_tasks}
        Task-Specific Candidate Evidence Delta: {llm_retry_feedback}
        Deterministic Expansion Plan: {retry_expansion_plan}

        REQUIRED CONTRACT:
        - Return EXACTLY one SEARCH component for EACH Failed Retrieval Task,
          and return NO other components.
        - Preserve every supplied task_id exactly. Do not renumber, reuse, or
          assign a failed task_id to a different topic.
        - Do NOT re-partition the latest user input.
        - Do NOT recreate successful tasks, intent-clarification tasks, or
          unrelated topics from the original request.
        - Candidate Evidence Delta contains only internally observed facts from
          unapproved parents. Use it only as an optional clue when refining
          within the deterministic stage; never quote parent IDs or treat it
          as a customer request or approved answer evidence.
        - Do NOT output CLARIFY in retry mode. The retrieval pipeline will
          decide whether the task becomes unavailable after its retry limit.
        - Follow the Deterministic Expansion Plan for EACH task exactly. The
          plan's stage controls which filters may expand on this attempt.
        - ANGLE_EXPANSION: keep the exact prior product and intent filters.
          Return every prior angle group unchanged and append exactly one new,
          complete angle group with group_id "retry_<next_attempt>". Each of
          the new group's three angles must be a SHORT NATURAL-LANGUAGE PHRASE
          (8-20 words) that resembles a sentence fragment likely found in an
          FAQ answer ABOUT THE FAILED TASK'S TOPIC, guided by the missing
          detail described in the feedback.
          Use common banking vocabulary only — do not invent specific product
          names, amounts, dates, or branch details. Do not paraphrase the
          customer's original question or repeat wording from the prior base
          group. Each angle must target a structurally different part of a
          hypothetical FAQ answer about this task's topic (steps,
          rules/conditions, or explanation).
          If the feedback does not describe a specific missing detail, vary
          the structural perspective (e.g., switch from customer-facing steps
          to bank-internal processing steps) rather than inventing new facts.
          Do not append terms into an existing angle.
        - PRODUCT_EXPANSION: retain all prior angle terms and intents. Every
          task reaching this stage has already been searched across its full
          existing intent scope without success. Evaluate the provided
          feedback status. If INSUFFICIENT, consider adding one plausible,
          matrix-valid related product as an ordered array with the original
          product first — but only when it has a direct, meaningful
          relationship to this task's customer_component; never add a product
          merely because its FAQ wording might be similar. If
          PARTIALLY_SUFFICIENT, be conservative: add a product only if it
          targets the specific remaining gap. Never replace the original
          product. Do NOT specify target_intent for an added product — the
          backend expands its full valid intent scope automatically; just
          echo the existing target_intent unchanged.
        - The revised task must remain about the same customer component as its
          supplied failed task.
        - Every revised Product/Intent pair must be valid in the matrix.
        - NEVER use conversational filler in semantic angles. Each angle must
          be a short natural-language PHRASE (8-20 words) that reads like a
          sentence fragment from an FAQ answer. Use common banking vocabulary
          only — do not invent specific product names, amounts, dates, or
          branch details. Do not paraphrase the customer's original question.

        Output JSON ONLY:
        {{"components": [{{"component_id":"retry_task_1","customer_component":"same component as failed task","resolution":"SEARCH","search_task":{{"task_id":"exact failed task_id","angle_groups":[{{"group_id":"base","procedural":"unchanged","policy":"unchanged","conceptual":"unchanged"}},{{"group_id":"retry_2","procedural":"...","policy":"...","conceptual":"..."}}],"target_commercial_product":"valid matrix product or ordered product array during PRODUCT_EXPANSION","target_intent":["existing target_intent, unchanged"]}},"clarification":null}}]}}
        """

    try:
        res = _parse_json_robustly(call_groq_lpu(prompt, model_id=MODEL_E_TRIAGE)) if (not retry_tasks or llm_retry_tasks) else {"components": []}
        # The model returns a mutually exclusive component partition. Derive
        # the existing downstream task lists from it so retrieval, caching,
        # retries, and clarification handling retain their current contracts.
        formulated_tasks = []
        formulated_clarifications = []
        formulated_declines = []
        for index, component in enumerate(res.get("components", []) or [], start=1):
            if not isinstance(component, dict):
                continue
            resolution = str(component.get("resolution", "")).strip().upper()
            customer_component = str(component.get("customer_component", "")).strip()
            if resolution == "SEARCH" and isinstance(component.get("search_task"), dict):
                # `customer_component` is the formulator's already-resolved
                # sub-query.  Preserve it on the retrieval task so every
                # downstream stage evaluates this task's scope—not the whole
                # customer turn—without requiring another LLM interpretation.
                search_task = dict(component["search_task"])
                if customer_component:
                    search_task["customer_component"] = customer_component
                    # Preserve the resolved task-level subquery as the direct
                    # retrieval anchor. It remains immutable across retries.
                    search_task["direct_query"] = customer_component
                search_task["subquery_id"] = str(component.get("component_id", "")).strip() or f"subquery_{index}"
                formulated_tasks.append(search_task)
            elif resolution == "CLARIFY" and isinstance(component.get("clarification"), dict):
                formulated_clarifications.append(component["clarification"])
            elif resolution == "DECLINE":
                decline = component.get("decline") if isinstance(component.get("decline"), dict) else {}
                formulated_declines.append({
                    "task_id": f"decline_formulator_{index}",
                    "subquery": customer_component,
                    "reason_category": "out_of_scope_product",
                    "detail": str(decline.get("reason", "")).strip(),
                    "source": "formulator",
                })
    except Exception as e:
        print(f"🚨 [NODE 02e EXCEPTION]: {e}")
        formulated_tasks = []
        formulated_clarifications = []
        formulated_declines = []

    decline_tasks = list(state.get("decline_tasks", [])) + formulated_declines

    validated_tasks = []
    for task in formulated_tasks:
        if not isinstance(task, dict):
            continue
        validated_task = _normalize_search_task_scope(task)
        if validated_task:
            validated_tasks.append(validated_task)
        else:
            print(f"   ⚠️ [TASK SCOPE REJECTED]: Invalid product/intent matrix pair for task '{task.get('task_id', '')}'.")
    formulated_tasks = validated_tasks

    if retry_tasks:
        llm_replacements = {task.get("task_id"): task for task in formulated_tasks if task.get("task_id") in retry_task_ids}
        search_tasks = []

        for prior_task in prior_tasks:
            task_id = prior_task.get("task_id")
            expansion = expansion_by_task_id.get(task_id, {})
            if task_id in deterministic_task_ids:
                # INTENT_EXPANSION never touches the LLM: compute the widened
                # scope directly from the matrix.
                replacement = _expand_intent_to_full(prior_task)
            else:
                replacement = llm_replacements.get(task_id)
            if replacement and task_id in retry_task_ids:
                merged = _merge_retry_task(prior_task, replacement, expansion)
                stage_label = expansion.get("stage", "")

                if stage_label in {"INTENT_EXPANSION", "PRODUCT_EXPANSION"}:
                    old_prod = _normalized_product_tags(prior_task.get("target_commercial_product"))
                    new_prod = _normalized_product_tags(merged.get("target_commercial_product"))
                    old_int = prior_task.get("target_intent", [])
                    new_int = merged.get("target_intent", [])
                    old_int = [old_int] if isinstance(old_int, str) else old_int
                    new_int = [new_int] if isinstance(new_int, str) else new_int
                    if old_prod == new_prod and set(old_int) == set(new_int):
                        print(f"   ⏩ [RETRY BARRIER]: {stage_label} produced no changes for '{task_id}'. Deferring duplicate RAG until the next stage.")
                        fast_forward_task_ids.append(task_id)
                        new_retry_counts[task_id] = new_retry_counts.get(task_id, 0) + 1
                    else:
                        print(f"   🔧 [{stage_label}]: Task '{task_id}' | product {old_prod} → {new_prod} | intent {old_int} → {new_int}")
                    log_event(
                        "task.expansion.applied",
                        task_id=task_id,
                        stage=stage_label,
                        old_product=old_prod,
                        new_product=new_prod,
                        old_intent=old_int,
                        new_intent=new_int,
                        no_op=(old_prod == new_prod and set(old_int) == set(new_int)),
                    )
                elif stage_label == "ANGLE_EXPANSION":
                    prior_group_count = len(_normalize_angle_groups(prior_task))
                    merged_groups = _normalize_angle_groups(merged)
                    if len(merged_groups) > prior_group_count:
                        new_group = merged_groups[-1]
                        print(f"   🔧 [ANGLE_EXPANSION]: Task '{task_id}' added group '{new_group.get('group_id')}' | procedural='{new_group.get('procedural')}' | policy='{new_group.get('policy')}' | conceptual='{new_group.get('conceptual')}'")
                        log_event(
                            "task.expansion.applied",
                            task_id=task_id,
                            stage=stage_label,
                            new_angle_group=new_group,
                            angle_group_count=len(merged_groups),
                        )

                search_tasks.append(merged)
            else:
                search_tasks.append(prior_task)
    else:
        search_tasks = formulated_tasks

    # Retries refine only failed retrieval tasks. Intent-level ambiguity was
    # already identified from the original request and remains in state while
    # the searchable components complete their independent RAG loop.
    intent_clarification_tasks = state.get("intent_clarification_tasks", []) if retry_tasks else formulated_clarifications
    if not retry_tasks:
        normalized_clarifications = []
        for index, task in enumerate(intent_clarification_tasks, start=1):
            unresolved_request = str(task.get("unresolved_request", "")).strip()
            if not unresolved_request:
                continue
            normalized_clarifications.append({
                "clarification_id": str(task.get("clarification_id", "")).strip() or f"intent_clarification_{index}",
                "source": "intent_formulator",
                "unresolved_request": unresolved_request,
                "reason": str(task.get("reason", "The request component is too ambiguous to search safely.")).strip(),
            })
        intent_clarification_tasks = normalized_clarifications

    # Keep task identity deterministic even if a model response omits or
    # duplicates an identifier. Retries always retain the original IDs.
    if not retry_tasks:
        seen_task_ids = set()
        for index, task in enumerate(search_tasks, start=1):
            task_id = str(task.get("task_id", "")).strip() or f"task_{index}"
            if task_id in seen_task_ids:
                task_id = f"task_{index}"
            task["task_id"] = task_id
            seen_task_ids.add(task_id)

    # Compile the new relational matrix to track drift
    new_matrix = []
    for task in search_tasks:
        prod = task.get("target_commercial_product")
        ints = task.get("target_intent", [])
        if isinstance(ints, str): ints = [ints]
        if prod and ints:
            new_matrix.append({"product": prod, "intents": ints})
            
    print(f"   🔎 [SEARCH TASKS]: {len(search_tasks)} tasks | Matrix: {new_matrix} | Intent clarifications: {len(intent_clarification_tasks)}")
    log_event("task.formulated", task_ids=[task.get("task_id") for task in search_tasks], task_count=len(search_tasks), intent_clarification_count=len(intent_clarification_tasks), retry_mode=bool(retry_tasks))

    if not search_tasks:
        # RAG was requested for these financial subqueries, but the intent
        # formulator could not safely create a retrieval task. Close those
        # generic RAG tasks rather than leaving a running ledger entry while
        # clarification proceeds directly to resolution planning.
        terminal_tool_tasks, terminal_tool_results = _finalize_rag_tool_results(state, [])
        if not intent_clarification_tasks and decline_tasks:
            # Every component the formulator received resolved to DECLINE
            # (out-of-scope product). Nothing valid remains for this turn.
            return {
                "preflight_status": "declined",
                "search_tasks": [],
                "intent_clarification_tasks": [],
                "task_results": [],
                "decline_tasks": decline_tasks,
                "tool_tasks": terminal_tool_tasks,
                "tool_results": terminal_tool_results,
                "pending_tool_task_ids": [],
            }
        if not intent_clarification_tasks and not (active_plan.get("steps") or has_retrieved_knowledge):
            # A valid request reached the formulator but no usable structured
            # output was produced. Abstain safely instead of allowing a later
            # generation node to infer a product or invent a search task.
            intent_clarification_tasks = [{
                "clarification_id": "intent_clarification_1",
                "source": "intent_formulator",
                "unresolved_request": user_msg.strip() or "your banking request",
                "reason": "No valid retrieval task could be formed from the request.",
            }]
        if intent_clarification_tasks:
            return {
                "preflight_status": "intent_clarification",
                "search_tasks": [],
                "intent_clarification_tasks": intent_clarification_tasks,
                "task_results": [],
                "decline_tasks": decline_tasks,
                "tool_tasks": terminal_tool_tasks,
                "tool_results": terminal_tool_results,
                "pending_tool_task_ids": [],
            }
        if active_plan.get("steps") or has_retrieved_knowledge:
            return {"preflight_status": "continue_plan", "search_tasks": [], "decline_tasks": decline_tasks, "tool_tasks": terminal_tool_tasks, "tool_results": terminal_tool_results, "pending_tool_task_ids": []}
        return {"preflight_status": "intent_clarification_needed", "search_tasks": [], "decline_tasks": decline_tasks, "tool_tasks": terminal_tool_tasks, "tool_results": terminal_tool_results, "pending_tool_task_ids": []}

    # Every task enters live direct retrieval. Search Strategy v2.8 may add
    # matching verified-session parents as a separate RRF list, but no broad
    # product/intent cache may bypass the current subquery's retrieval.
    print("   🎯 [TASK ROUTER]: Live retrieval required; verified session evidence may join RRF when task scope overlaps.")
    live_retrieval_count = len(search_tasks) - len(fast_forward_task_ids)
    log_event(
        "task.retrieval.route",
        task_count=len(search_tasks),
        live_retrieval_task_count=live_retrieval_count,
        deferred_retry_task_ids=fast_forward_task_ids,
        retry_mode=bool(retry_tasks),
        strategy="v2.8_verified_session_rrf",
    )
    for task_id in fast_forward_task_ids:
        log_event(
            "task.retry.deferred",
            task_id=task_id,
            retry_count=new_retry_counts.get(task_id, 0),
            reason="Intent or product expansion produced no valid scope change; waiting for the next expansion stage.",
        )

    return {
        "preflight_status": "retrieve",
        "search_tasks": search_tasks,
        "intent_clarification_tasks": intent_clarification_tasks,
        "task_results": task_results if retry_tasks else [],
        "task_retry_counts": new_retry_counts if retry_tasks else {},
        "deferred_retry_task_ids": fast_forward_task_ids,
        "active_product_intents": new_matrix,
        "active_plan": {} if not retry_tasks else active_plan,
        "completed_steps": [] if not retry_tasks else state.get("completed_steps", []),
        "decline_tasks": decline_tasks,
    }

# --- PHASE 2: TOOL CALLING v2.0 ---

def _tool_task_fingerprint(tool_name: str, operation: str, subquery_id: str, payload: dict) -> str:
    """Stable idempotency key for an incremental tool task."""
    normalized_payload = json.dumps(payload or {}, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return f"{tool_name}|{operation}|{subquery_id}|{normalized_payload}"


def _tool_result_by_task_id(results: list[dict]) -> dict[str, dict]:
    return {
        result.get("tool_task_id"): result
        for result in results
        if isinstance(result, dict) and result.get("tool_task_id")
    }


def node_03a_knowledge_blueprint_planner(state: AgentState):
    """Create incremental generic tool tasks from clean financial subqueries.

    The first round turns every financial subquery into a registered RAG task.
    Later rounds may add a task only from a Resolution Planner capability gap.
    A task ledger and fingerprints prevent duplicate calls across the loop.
    """
    print("🟢 [NODE 03a] Executing Tool-Calling Planner")
    existing_tasks = [dict(task) for task in state.get("tool_tasks", []) if isinstance(task, dict)]
    existing_fingerprints = set(state.get("tool_task_fingerprints", []))
    existing_fingerprints.update(
        str(task.get("fingerprint", "")) for task in existing_tasks if task.get("fingerprint")
    )
    result_by_task = _tool_result_by_task_id(state.get("tool_results", []))
    planning_round = int(state.get("tool_planning_round", 0)) + 1
    financial_subqueries = [
        item for item in state.get("financial_subqueries", state.get("triaged_subqueries", []))
        if isinstance(item, dict) and item.get("subquery_id") and item.get("customer_component")
    ]
    tool_gaps = [item for item in state.get("tool_gaps", []) if isinstance(item, dict)]

    if planning_round > MAX_TOOL_PLANNING_ROUNDS or len(existing_tasks) >= MAX_TOOL_TASKS_PER_TURN:
        print("   ⛔ [TOOL LOOP EXHAUSTED]: Planning budget reached.")
        log_event("tool.loop.exhausted", planning_round=planning_round, task_count=len(existing_tasks))
        return {
            "preflight_status": "tool_results_ready",
            "tool_plan_status": "exhausted",
            "pending_tool_task_ids": [],
            "tool_gaps": [],
        }

    known_subqueries = {item["subquery_id"]: item for item in financial_subqueries}
    candidates = []
    if not existing_tasks:
        # RAG is the only enabled executor in this release. This explicit task
        # creation is the safe fallback if the planner is unavailable.
        candidates = [
            {
                "subquery_id": subquery_id,
                "tool_name": "rag",
                "operation": "retrieve_faq_evidence",
                "input": {"customer_component": item["customer_component"]},
            }
            for subquery_id, item in known_subqueries.items()
        ]
    elif tool_gaps:
        prompt = f"""
        INCREMENTAL TOOL-CALLING PLANNER
        You may add tool tasks only when an unresolved, non-factual capability
        gap can be materially addressed by a registered enabled tool.

        Financial subqueries: {json.dumps(financial_subqueries, ensure_ascii=False)}
        Validated terminal tool results: {json.dumps(state.get("tool_results", []), ensure_ascii=False)}
        Resolution capability gaps: {json.dumps(tool_gaps, ensure_ascii=False)}
        Enabled tool registry: {json.dumps(enabled_tool_capabilities(), ensure_ascii=False)}

        Rules:
        - Create only additive tasks. Never recreate a completed, unavailable,
          failed, blocked, or pending task.
        - Every task must reference exactly one supplied subquery_id.
        - Use only an enabled tool_name and one of its listed operations.
        - Do not create a task merely to restate, search again, or rewrite a
          completed result. Do not invent facts, customer data, or capabilities.
        - RAG's internal retries are not outer tool tasks. Add RAG only when
          no RAG task for that subquery has already reached a terminal state.
        - Return no task if no registered enabled capability can close a gap.
        - For tool_name "service_request", input must use the literal key
          "service_request_id" -- NOT "customer_component" (that key belongs
          only to rag tasks; do not copy it here even though it appears
          elsewhere in this prompt). Example of the exact shape required:
          {{"service_request_id": "profile-address-update"}}. Choose the
          single best-fitting id from that tool's available_requests in the
          enabled tool registry; never invent an id, and never include form
          field values (the customer has not filled anything in yet).
        - Read the full description of each available_requests entry before
          choosing one -- a description can scope out specific reasons, not
          just name a general topic. "Request a replacement physical card
          for a non-fraud reason (worn or damaged)" does not cover a lost or
          stolen card, even though both are card replacement in general
          terms. If the customer's actual situation falls outside every
          candidate's stated scope, do not create a task for it at all.

        Output JSON only:
        {{"tool_tasks":[{{"subquery_id":"...","tool_name":"...","operation":"...","input":{{}}}}]}}
        """
        try:
            candidates = _parse_json_robustly(call_groq_lpu(prompt, model_id=MODEL_C_PLANNER)).get("tool_tasks", [])
        except Exception as error:
            print(f"⚠️ [TOOL PLANNER ERROR]: {error}")
            candidates = []

    new_tasks = []
    for candidate in candidates if isinstance(candidates, list) else []:
        if not isinstance(candidate, dict):
            continue
        subquery_id = str(candidate.get("subquery_id", "")).strip()
        tool_name = str(candidate.get("tool_name", "")).strip().lower()
        operation = str(candidate.get("operation", "")).strip()
        if (
            subquery_id not in known_subqueries
            or not is_registered_operation(tool_name, operation)
            or not TOOL_REGISTRY.get(tool_name, {}).get("enabled", False)
        ):
            continue
        payload = candidate.get("input", {}) if isinstance(candidate.get("input"), dict) else {}
        if tool_name == "rag":
            payload = {"customer_component": known_subqueries[subquery_id]["customer_component"]}
        elif tool_name == "service_request":
            # The planner is told to key this "service_request_id", but the
            # prompt also shows customer_component under Financial subqueries
            # and rag tasks -- in practice the model sometimes copies that
            # key by pattern-matching instead. The registered id is a closed,
            # validated set, so accept the correct value under any key rather
            # than silently dropping an otherwise-correct choice for a
            # cosmetic key mismatch.
            requested_id = str(payload.get("service_request_id", "")).strip()
            if get_service_request_contract(requested_id) is None:
                requested_id = next(
                    (str(value).strip() for value in payload.values() if get_service_request_contract(str(value).strip()) is not None),
                    "",
                )
            if get_service_request_contract(requested_id) is None:
                log_event("tool.task.invalid_service_request_id", subquery_id=subquery_id, requested_id=requested_id, raw_input=payload, level="WARNING")
                continue
            payload = {"service_request_id": requested_id}
        fingerprint = _tool_task_fingerprint(tool_name, operation, subquery_id, payload)
        if fingerprint in existing_fingerprints:
            log_event("tool.task.deduplicated", subquery_id=subquery_id, tool_name=tool_name, operation=operation)
            continue
        task_id = f"tool_{tool_name}_{len(existing_tasks) + len(new_tasks) + 1}"
        new_tasks.append({
            "tool_task_id": task_id,
            "subquery_id": subquery_id,
            "tool_name": tool_name,
            "operation": operation,
            "input": payload,
            "fingerprint": fingerprint,
            "status": "pending",
            "planning_round": planning_round,
        })
        existing_fingerprints.add(fingerprint)

    pending_ids = [task["tool_task_id"] for task in new_tasks]
    for task in new_tasks:
        log_event("tool.task.created", **{key: task[key] for key in ("tool_task_id", "subquery_id", "tool_name", "operation", "planning_round")})
    print(f"   🧰 [TOOL PLAN]: round {planning_round} added {len(new_tasks)} task(s): {pending_ids}")
    log_event("tool.plan.created", planning_round=planning_round, pending_tool_task_ids=pending_ids, capability_count=len(enabled_tool_capabilities()))
    return {
        "preflight_status": "tool_dispatch" if pending_ids else "tool_results_ready",
        "tool_tasks": [*existing_tasks, *new_tasks],
        "pending_tool_task_ids": pending_ids,
        "tool_task_fingerprints": sorted(existing_fingerprints),
        "tool_planning_round": planning_round,
        "tool_plan_status": "continue" if pending_ids else "exhausted",
        "tool_gaps": [],
        "rag_payload": "",
        "api_payload": "",
        "deeplink_payload": "",
    }


def _dispatch_service_request_task(state: AgentState, task: dict) -> dict:
    """Register a service_request form synchronously -- a deterministic DB
    write, no LLM call. Mirrors deeplink's allow-list discipline: the task's
    service_request_id was already validated against the registry when the
    task was created (node_03a); this function re-validates defensively and
    is the only place a registration row actually gets created.
    """
    service_request_id = str(task.get("input", {}).get("service_request_id", "")).strip()
    contract = get_service_request_contract(service_request_id)
    subquery_id = task.get("subquery_id")
    resolved_component = next(
        (item.get("customer_component", "") for item in state.get("financial_subqueries", []) if item.get("subquery_id") == subquery_id),
        "",
    )
    session_id = state.get("session_id", "")

    if contract is None:
        return {"status": "unavailable", "reason": "That service request is not registered."}

    record = service_request_service.register(
        session_id=session_id,
        user_id=state.get("user_id", ""),
        service_request_id=service_request_id,
        resolved_customer_component=resolved_component,
        product_tags=contract.get("allowed_products", []),
        intent_tags=contract.get("allowed_intents", []),
        risk_level=contract["risk_level"],
        requires_step_up=contract["requires_step_up"],
    )
    return {
        "status": "completed",
        "approved_result": {
            "special_id": record["special_id"],
            "service_request_id": service_request_id,
            "description": contract["description"],
            "form_schema": contract["form_schema"],
        },
    }


def _pending_service_requests(state: AgentState) -> list[dict]:
    """Completed service_request tool_results for this turn only.

    Exposes only special_id, service_request_id, and description -- never
    the form_schema. The LLM's job is to write one honest sentence inviting
    the customer to complete a form it cannot see the fields of; the actual
    form-opening UI action is attached deterministically in node_07, not by
    anything the model generates.
    """
    return [
        {
            "special_id": result.get("approved_result", {}).get("special_id"),
            "service_request_id": result.get("approved_result", {}).get("service_request_id"),
            "description": result.get("approved_result", {}).get("description"),
        }
        for result in state.get("tool_results", [])
        if isinstance(result, dict)
        and result.get("tool_name") == "service_request"
        and result.get("status") == "completed"
        and result.get("approved_result", {}).get("special_id")
    ]


def _unavailable_service_requests(state: AgentState) -> list[dict]:
    """service_request tool_results this turn that could not be registered.

    Without this, a blocked attempt (e.g. the one-active-request-per-session
    guard) silently disappears from the response with no acknowledgment at
    all -- the customer's second request vanishes and nothing explains why.
    Only the deterministic reason recorded at dispatch time is exposed; nothing
    here is model-authored.
    """
    return [
        {"reason": result.get("availability_reason", "")}
        for result in state.get("tool_results", [])
        if isinstance(result, dict)
        and result.get("tool_name") == "service_request"
        and result.get("status") == "unavailable"
        and result.get("availability_reason")
    ]


def node_03b_tool_dispatcher(state: AgentState):
    """Dispatch pending registered tasks without hard-coding graph fan-out.

    RAG is handed to its existing internal retrieval subgraph -- it needs
    retries and stays async. service_request is a one-shot, synchronous,
    LLM-free DB write, so it resolves inline here rather than needing its
    own graph node. Tools without an enabled executor terminate as
    unavailable and cannot block the turn.
    """
    print("🟢 [NODE 03b] Executing Generic Tool Dispatcher")
    pending_ids = set(state.get("pending_tool_task_ids", []))
    tasks = [dict(task) for task in state.get("tool_tasks", []) if isinstance(task, dict)]
    tool_results = [dict(result) for result in state.get("tool_results", []) if isinstance(result, dict)]
    pending_tasks = [task for task in tasks if task.get("tool_task_id") in pending_ids and task.get("status") == "pending"]
    rag_task_ids = []
    for task in pending_tasks:
        tool_name = task.get("tool_name")
        if tool_name == "rag":
            task["status"] = "running"
            rag_task_ids.append(task["tool_task_id"])
            log_event("tool.dispatch.started", tool_task_id=task["tool_task_id"], subquery_id=task.get("subquery_id"), tool_name=tool_name)
            continue
        if tool_name == "service_request":
            outcome = _dispatch_service_request_task(state, task)
            task["status"] = outcome["status"]
            tool_results.append({
                "tool_task_id": task["tool_task_id"],
                "subquery_id": task.get("subquery_id"),
                "tool_name": tool_name,
                "status": outcome["status"],
                "approved_result": outcome.get("approved_result", {}),
                "customer_safe_summary": "",
                "availability_reason": outcome.get("reason", ""),
            })
            log_event(
                "tool.result.service_request",
                tool_task_id=task["tool_task_id"], subquery_id=task.get("subquery_id"),
                status=outcome["status"],
                special_id=outcome.get("approved_result", {}).get("special_id"),
            )
            continue
        task["status"] = "unavailable"
        tool_results.append({
            "tool_task_id": task["tool_task_id"],
            "subquery_id": task.get("subquery_id"),
            "tool_name": tool_name,
            "status": "unavailable",
            "approved_result": {},
            "customer_safe_summary": "",
            "availability_reason": "This tool is not enabled in the current deployment.",
        })
        log_event("tool.result.unavailable", tool_task_id=task["tool_task_id"], subquery_id=task.get("subquery_id"), tool_name=tool_name)

    return {
        "preflight_status": "rag_tool_active" if rag_task_ids else "tool_results_ready",
        "tool_tasks": tasks,
        "tool_results": tool_results,
        "pending_tool_task_ids": rag_task_ids,
    }


_SERVICE_REQUEST_RESULT_TEMPLATES = {
    "profile-address-update": lambda result: f"Your address has been updated to {result.get('new_address', 'the address you provided')}.",
    "card-replacement-request": lambda result: f"Your replacement card has been ordered and will arrive by mail. Reference: {result.get('reference', 'unavailable')}.",
    "kyc-document-resubmission": lambda result: (
        f"Your updated identification document has been received. Reference: {result.get('reference', 'unavailable')}. "
        f"Verification will be completed within {result.get('business_days', 'a few')} business days."
    ),
}
# Every clause a template above states must have an explicit, matching
# counterpart here -- not just the raw data field it was built from. Two
# separate live-trace failures (an "our compliance team will..." claim, then
# a denial saying "identity" when the evidence only said "denied") were both
# the auditor correctly rejecting a template whose wording outran what a
# bare data field actually asserts. Spelling out the same claims as explicit
# facts closes that gap by construction instead of guessing what phrasing
# the auditor will accept next time.
_SERVICE_REQUEST_RESULT_FACTS = {
    "profile-address-update": lambda result: [
        f"The customer's profile address was updated to: {result.get('new_address', 'the address provided')}.",
    ],
    "card-replacement-request": lambda result: [
        "A replacement card was ordered for the customer.",
        "The replacement card will be sent to the customer by mail.",
        f"The reference number for this replacement request is {result.get('reference', 'unavailable')}.",
    ],
    "kyc-document-resubmission": lambda result: [
        "The customer's updated identification document was received.",
        f"The reference number for this submission is {result.get('reference', 'unavailable')}.",
        f"Verification of the submitted document is expected to be completed within {result.get('business_days', 'a few')} business days.",
    ],
}
_SERVICE_REQUEST_DENIAL_MESSAGE = (
    "We weren't able to verify your identity for this request. Please try again or visit a branch."
)
_SERVICE_REQUEST_DENIAL_FACTS = [
    "The identity verification check for this request was not successful.",
    "The customer may retry the request, or visit a branch for assistance.",
]


def node_svc_service_request_resolver(state: AgentState):
    """The WP-7 'special route': turns a form-submission or credential-check
    outcome into an audited chat response, without going through node_02d
    triage. See docs/SERVICE_REQUEST_DESIGN_v1.0.md Section 8.

    Deterministic templates, not LLM generation, produce the customer-facing
    text -- the facts here (an address, a reference number) come directly
    from the mocked downstream result, never from a model's own wording of
    them. The templates still pass through the same evidence-groundedness
    and conduct-compliance gates as any other customer-facing content, per
    this project's "everything that reaches a customer goes through the same
    checks" principle, even though nothing here was generated.
    """
    print("🟢 [NODE SVC] Executing Service Request Result Resolver")
    event = state.get("service_request_event", {}) or {}
    outcome = event.get("outcome")
    service_request_id = event.get("service_request_id", "")
    description = event.get("description", "this request")

    if outcome == "denied":
        content_block = _SERVICE_REQUEST_DENIAL_MESSAGE
        evidence_bundle = {"outcome": "denied", "facts": _SERVICE_REQUEST_DENIAL_FACTS}
    else:
        template = _SERVICE_REQUEST_RESULT_TEMPLATES.get(service_request_id)
        facts_fn = _SERVICE_REQUEST_RESULT_FACTS.get(service_request_id)
        result_payload = event.get("result_payload", {}) or {}
        content_block = template(result_payload) if template else "Your request has been received."
        evidence_bundle = {
            "outcome": "completed",
            "facts": facts_fn(result_payload) if facts_fn else [],
        }

    evidence_audit = check_evidence_groundedness(
        content_block,
        evidence_bundle=evidence_bundle,
        evidence_description="Service Request Downstream Result",
        route_specific_rules=(
            "The \"facts\" list is a set of pre-verified statements about "
            "this specific request's outcome; the content may state any of "
            "them directly, in its own natural phrasing, without needing "
            "further support. The content must not state or imply anything "
            "beyond what the facts list says -- no approval, no guarantee, "
            "no outcome or actor the facts do not name."
        ),
    )
    conduct_audit = check_conduct_compliance(content_block)
    if not (evidence_audit.get("evidence_grounded") and conduct_audit.get("conduct_compliant")):
        log_event(
            "service_request.result_response.rejected",
            level="WARNING",
            special_id=event.get("special_id"),
            evidence_reason=evidence_audit.get("reason"),
            conduct_reason=conduct_audit.get("reason"),
        )
        content_block = (
            "There has been an update to your request, but we can't display the details right now. "
            "Please check back shortly or contact support."
        )

    history = list(state.get("chat_history", []))
    history.append(HumanMessage(content=f"[Completed: {description}]"))
    history.append(AIMessage(content=content_block))

    return {
        "final_response": content_block,
        "final_draft": content_block,
        "final_answer": content_block,
        "ui_action_payload": {"uiType": "none", "options": []},
        "chat_history": history,
        "service_request_event": {},
    }


def _finalize_rag_tool_results(state: AgentState, task_results: list[dict]) -> tuple[list[dict], list[dict]]:
    """Convert terminal RAG search-task outcomes into generic tool results."""
    search_tasks_by_subquery: dict[str, list[dict]] = {}
    for task in state.get("search_tasks", []):
        if isinstance(task, dict) and task.get("subquery_id"):
            search_tasks_by_subquery.setdefault(task["subquery_id"], []).append(task)
    results_by_task_id = {result.get("task_id"): result for result in task_results if isinstance(result, dict)}
    tool_tasks = [dict(task) for task in state.get("tool_tasks", []) if isinstance(task, dict)]
    previous_results = [
        dict(result) for result in state.get("tool_results", [])
        if isinstance(result, dict) and result.get("tool_name") != "rag"
    ]

    for tool_task in tool_tasks:
        if tool_task.get("tool_name") != "rag" or tool_task.get("status") not in {"pending", "running"}:
            continue
        subquery_id = tool_task.get("subquery_id")
        linked_results = [
            results_by_task_id.get(task.get("task_id"), {})
            for task in search_tasks_by_subquery.get(subquery_id, [])
        ]
        approved = [
            {
                "task_id": result.get("task_id"),
                "status": result.get("status"),
                "approved_parent_ids": list(result.get("approved_parent_ids", [])),
            }
            for result in linked_results
            if result.get("status") in ANSWERABLE_TASK_STATUSES and result.get("approved_parent_ids")
        ]
        terminal_status = "completed" if approved else "unavailable"
        tool_task["status"] = terminal_status
        tool_result = {
            "tool_task_id": tool_task.get("tool_task_id"),
            "subquery_id": subquery_id,
            "tool_name": "rag",
            "status": terminal_status,
            "approved_result": {"search_task_results": approved},
            "customer_safe_summary": "",
            "availability_reason": "" if approved else "No approved FAQ evidence resolved this financial subquery.",
        }
        previous_results.append(tool_result)
        log_event(
            "tool.result.validated",
            tool_task_id=tool_result["tool_task_id"],
            subquery_id=subquery_id,
            tool_name="rag",
            status=terminal_status,
            approved_search_task_ids=[item["task_id"] for item in approved],
        )
    return tool_tasks, previous_results

async def node_03b_rag_worker(state: AgentState):
    print("🟢 [NODE 03b_1] Executing Task-Level RAG Worker (Direct-First Retrieval)")
    tasks = state.get("search_tasks", [])
    prior_results = {result.get("task_id"): result for result in state.get("task_results", [])}
    deferred_task_ids = set(state.get("deferred_retry_task_ids", []))
    tasks_to_retrieve = [
        task for task in tasks
        if task.get("task_id") not in deferred_task_ids
        and prior_results.get(task.get("task_id"), {}).get("status") not in {*ANSWERABLE_TASK_STATUSES, "UNAVAILABLE"}
    ]
    if not tasks_to_retrieve:
        return {"rag_payload": "", "task_results": list(prior_results.values())}

    engine = RAGEngine()
    task_rejected_parent_ids = state.get("task_rejected_parent_ids", {})
    task_partial_evidence = state.get("task_partial_evidence", {})
    async def retrieve_task(task: dict) -> dict:
        scope_pairs = _valid_product_intent_scope_pairs(task)
        retrieval_mode = _retrieval_mode_for_task(task, state.get("task_retry_counts", {}))
        verified_session_parent_ids = _verified_session_parent_ids_for_task(state, task)
        # Exclude both outright-rejected parents and already-approved partial
        # evidence from the fresh candidate pool. Partial evidence is
        # re-attached from SQLite regardless of whether Chroma resurfaces it
        # (see node_03c), so letting it also occupy a fresh selection slot
        # only wastes that slot on evidence the auditor already has — a slot
        # a widened retry needs for genuinely unseen candidates instead.
        task_id = task.get("task_id")
        excluded_parent_ids = list(dict.fromkeys([
            *task_rejected_parent_ids.get(task_id, []),
            *task_partial_evidence.get(task_id, []),
        ]))
        log_event(
            "verified_session_evidence.matched",
            task_id=task.get("task_id"),
            retrieval_mode=retrieval_mode,
            parent_ids=verified_session_parent_ids,
            candidate_count=len(verified_session_parent_ids),
        )
        if retrieval_mode == "DIRECT":
            parents = await engine.execute_direct_search(
                query_text=task.get("direct_query", task.get("customer_component", "")),
                scope_pairs=scope_pairs,
                verified_session_parent_ids=verified_session_parent_ids,
                excluded_parent_ids=excluded_parent_ids,
            )
        else:
            parents = await engine.execute_multi_angle_search(
                blueprint=task,
                scope_pairs=scope_pairs,
                excluded_parent_ids=excluded_parent_ids,
            )
        return {
            "task_id": task.get("task_id"),
            "status": "RETRIEVED",
            "origin": "RAG",
            "retrieval_mode": retrieval_mode,
            "verified_session_parent_ids": verified_session_parent_ids,
            "parents": parents,
        }

    retrieved_results = await asyncio.gather(*(retrieve_task(task) for task in tasks_to_retrieve))
    merged_results = {**prior_results, **{result["task_id"]: result for result in retrieved_results}}
    print(f"   🔎 [TASK RAG]: Retrieved {len(retrieved_results)} task result set(s) in parallel.")
    log_event(
        "task.rag.completed",
        task_ids=[result.get("task_id") for result in retrieved_results],
        retrieval_modes={result.get("task_id"): result.get("retrieval_mode") for result in retrieved_results},
        verified_session_parent_ids={
            result.get("task_id"): result.get("verified_session_parent_ids", [])
            for result in retrieved_results
        },
        selected_parent_ids={
            result.get("task_id"): [parent.get("parent_id") for parent in result.get("parents", [])]
            for result in retrieved_results
        },
    )
    return {"rag_payload": "", "task_results": list(merged_results.values())}

def node_03b_api_worker(state: AgentState):
    return {"api_payload": "[API DATA]:\nNo API data requested for this MVP."}

def node_03b_deeplink_worker(state: AgentState):
    return {"deeplink_payload": "[DEEPLINK DATA]:\nNo Deep-links requested for this MVP."}

def node_03c_sufficiency_auditor(state: AgentState):
    print("🟢 [NODE 03c] Executing Task-Level Sufficiency & Evidence Selection")
    tasks_by_id = {task.get("task_id"): task for task in state.get("search_tasks", [])}
    current_results = {result.get("task_id"): result for result in state.get("task_results", [])}
    evidence_candidates = [
        result for result in current_results.values()
        if result.get("status") == "RETRIEVED"
    ]
    retry_counts = dict(state.get("task_retry_counts", {}))
    feedback = dict(state.get("task_feedback", {}))
    sufficiency_history = {
        str(task_id): list(entries)
        for task_id, entries in state.get("task_sufficiency_history", {}).items()
        if isinstance(entries, list)
    }
    partial_evidence = {
        str(task_id): list(dict.fromkeys(parent_ids))
        for task_id, parent_ids in state.get("task_partial_evidence", {}).items()
        if isinstance(parent_ids, list)
    }
    rejected_parent_ids = {
        str(task_id): list(dict.fromkeys(parent_ids))
        for task_id, parent_ids in state.get("task_rejected_parent_ids", {}).items()
        if isinstance(parent_ids, list)
    }

    # A partly answerable task may accumulate evidence across retries.  The
    # auditor must see retained parents together with the latest candidates so
    # it can promote the combined set when the remaining gap is filled.
    evaluation_inputs = []
    candidate_ids_by_task = {}
    auditor_results = {}
    for result in evidence_candidates:
        task_id = result.get("task_id")
        fresh_parents = list(result.get("parents", []))
        fresh_ids = {parent.get("parent_id") for parent in fresh_parents if parent.get("parent_id")}
        retained_ids = partial_evidence.get(str(task_id), [])
        retained_parents = [
            parent for parent in _hydrate_parent_ids(retained_ids)
            if parent.get("parent_id") not in fresh_ids
        ]
        all_parents = fresh_parents + retained_parents
        candidate_ids_by_task[task_id] = {
            parent.get("parent_id") for parent in all_parents if parent.get("parent_id")
        }
        if not all_parents:
            # Rejected-parent exclusion (or a genuinely narrow scope) can
            # leave nothing left to judge. There is no evidence to compare
            # against the customer_component, so the verdict is deterministic
            # — a model call here would only spend a call to restate what is
            # already certain.
            print(f"   ⚡ [SUFFICIENCY SKIPPED]: Task '{task_id}' has no candidate evidence this attempt; treating as INSUFFICIENT without a model call.")
            log_event(
                "task.sufficiency.skipped",
                task_id=task_id,
                excluded_parent_ids=rejected_parent_ids.get(str(task_id), []),
            )
            auditor_results[task_id] = {
                "task_id": task_id,
                "status": "INSUFFICIENT",
                "approved_parent_ids": [],
                "reason": "No candidate evidence was retrieved for this component.",
                "evidence_delta": [],
            }
            continue
        evaluation_inputs.append({
            "task_id": task_id,
            "task": tasks_by_id.get(task_id, {}),
            "retained_partial_parent_ids": retained_ids,
            "prior_sufficiency_audit_history": sufficiency_history.get(str(task_id), []),
            "parents": all_parents,
        })
    if evaluation_inputs:
        prompt = f"""
        TASK-LEVEL SUFFICIENCY AND EVIDENCE SELECTION
        You are a groundedness auditor for a read-only Horizon Bank FAQ assistant.
        Evaluate EACH supplied task independently for whether its supplied
        parents are response-ready: they must contain enough materially
        relevant, grounded information for the assistant to give the customer
        a useful FAQ response.

        Original User Message (advisory context only; never use it as an
        individual task's completeness target): {state.get("user_input", "")}
        Trusted Conversation Context: { _build_conversation_context(state) }
        Task Inputs: {json.dumps(evaluation_inputs, ensure_ascii=False)}

        Rules:
        - Each Task Input contains `task.customer_component`: the intent
          formulator's resolved sub-query for that task. Evaluate a task ONLY
          against its own `customer_component` and that task's candidate
          parents.
        - Each Task Input may include `prior_sufficiency_audit_history` from
          earlier attempts for the same task in this turn. Read it before
          judging the current candidates and remain consistent with its prior
          sufficiency judgments. This history is internal audit context only;
          it is not customer-facing evidence and does not authorize any new
          factual claim.
        - Classify each task as SUFFICIENT, PARTIALLY_SUFFICIENT, or
          INSUFFICIENT. Judge whether the supplied parents provide useful,
          materially relevant information for the task—not whether they repeat
          the customer's wording or explicitly resolve every possible
          interpretation of the customer's question.
        - PARTIAL-EVIDENCE DECISION ORDER: First identify whether any supplied
          parent contains a factual proposition directly relevant to a
          meaningful aspect of the customer's resolved request. If one does,
          do not return INSUFFICIENT merely because the parent cannot determine
          the customer's requested outcome, permission, eligibility,
          exception, legality, preferred action, or other broader conclusion.
          Retain that parent as PARTIALLY_SUFFICIENT when its supported fact
          can be truthfully presented as useful, bounded Horizon guidance
          while another material aspect remains unresolved. Continue retrieval
          for the unresolved aspect.
        - Return INSUFFICIENT only when no supplied parent contains any
          factual proposition that can be truthfully and materially presented
          in relation to the resolved request. A parent is not material merely
          because it concerns the same product: its supported fact must
          directly concern the same topic, condition, process, boundary, or
          factual premise in the customer's request.
        - SUFFICIENT: the selected parents contain enough grounded information
          for a useful response to the `customer_component`, even when that
          response is an explicit policy boundary, limit, condition,
          restriction, or other related factual information rather than a
          literal yes/no answer. The response generator will state only what
          the evidence supports and must not invent a broader conclusion.
        {PRECISION_REFERENCE_CONTRACT}
        - PARTIALLY_SUFFICIENT: selected parents provide grounded, material
          information for one meaningful part of the `customer_component`,
          while another material part has no useful related evidence. Select
          only the supporting parent IDs; the backend will retain them while
          retrieval continues.
        - INSUFFICIENT: the supplied parents provide no grounded, materially
          relevant information that could be usefully presented in response to
          the `customer_component`.
        - Use the ordinary meaning of explicit policy boundaries, quantities,
          conditions, and restrictions when deciding whether a task is
          response-ready. Do not require a parent to restate the customer's
          question or explicitly state the customer's requested conclusion.
          For example, an explicit statutory maximum or fixed policy limit is
          response-ready for a question about raising that limit, even if the
          parent does not separately say "no" or describe every possible
          external arrangement.
          Preserve each parent's scope and do not infer facts beyond it.
        - Do NOT require a task's evidence to answer the complete original
          user message, another task's customer_component, or another
          product/intent component.
        - The original user message and trusted conversation context help
          explain references or continuations only; they are never the
          completeness benchmark for an individual task.
        - Return one result for every supplied task_id.
        - For SUFFICIENT or PARTIALLY_SUFFICIENT, return only parent IDs from
          that exact task that materially support the answer. At least one ID
          is required for either status.
        - For PARTIALLY_SUFFICIENT or INSUFFICIENT, return an `evidence_delta`
          list. This is internal evidence-only retrieval context for the intent
          formulator, not a query, diagnosis, or rewrite instruction. The
          backend determines terminal clarification after the configured retry limit.
        - Include an evidence_delta item only when a supplied candidate parent
          contains a materially task-relevant factual proposition that is NOT
          already explicit in that task's customer_component. Each item must
          be exactly {{"parent_id": "a supplied candidate parent ID", "fact":
          "one concise proposition faithfully supported by that parent"}}.
        - Never include a restatement, synonym, or reformulation of the
          customer_component; what candidate parents do not contain; proposed
          metadata filters, retry stages, angles, query terms, sources,
          services, or next actions; or any fact outside the supplied parent.
        - Never use "search", "find", "look up", "list", or other imperative
          wording in evidence_delta facts.
        - If no new materially relevant candidate fact exists, return an empty
          evidence_delta list. Do not substitute a generic sentence.
        - evidence_delta is internal diagnostic context only. It is never
          approved answer evidence.
        - Never invent a parent ID, fact, or instruction.

        Output JSON only: {{"task_results": [{{"task_id": "...", "status": "SUFFICIENT" | "PARTIALLY_SUFFICIENT" | "INSUFFICIENT", "approved_parent_ids": ["..."], "reason": "...", "evidence_delta": [{{"parent_id": "...", "fact": "..."}}]}}]}}
        """
        try:
            auditor_results.update({
                result.get("task_id"): result
                for result in _parse_json_robustly(call_groq_lpu(prompt, model_id=MODEL_B_AUDITOR)).get("task_results", [])
            })
        except Exception as error:
            print(f"🚨 [NODE 03c EXCEPTION]: {error}")

    for result in evidence_candidates:
        task_id = result.get("task_id")
        available_ids = candidate_ids_by_task.get(task_id, set())
        audit = auditor_results.get(task_id, {})
        approved_ids = [parent_id for parent_id in audit.get("approved_parent_ids", []) if parent_id in available_ids]
        requested_status = str(audit.get("status", "INSUFFICIENT")).upper()
        # Accept legacy values during an in-flight backend restart, but never
        # let them shape the three-way state model.
        if requested_status in {"RETRY", "UNAVAILABLE"}:
            requested_status = "INSUFFICIENT"
        if requested_status not in {"SUFFICIENT", "PARTIALLY_SUFFICIENT", "INSUFFICIENT"}:
            requested_status = "INSUFFICIENT"
        reason = audit.get("reason", "No sufficient grounded FAQ evidence was selected.")
        history_delta = [
            {
                "parent_id": str(item.get("parent_id", "")).strip(),
                "fact": str(item.get("fact", "")).strip(),
            }
            for item in audit.get("evidence_delta", [])
            if isinstance(item, dict)
            and str(item.get("parent_id", "")).strip() in available_ids
            and str(item.get("fact", "")).strip()
        ]
        sufficiency_history.setdefault(str(task_id), []).append({
            "attempt": retry_counts.get(task_id, 0) + 1,
            "status": requested_status,
            "approved_parent_ids": approved_ids,
            "reason": reason,
            "evidence_delta": history_delta,
        })
        # Candidates shown this round but not approved are explicitly judged
        # not relevant. Excluding them from future candidate pools stops a
        # widened retry filter from re-surfacing known-bad evidence and
        # wasting a selection slot on it.
        newly_rejected = available_ids - set(approved_ids)
        if newly_rejected:
            rejected_parent_ids[str(task_id)] = list(set(rejected_parent_ids.get(str(task_id), [])) | newly_rejected)
        if requested_status == "SUFFICIENT" and approved_ids:
            # Parent IDs were already constrained to `available_ids`, the
            # candidate set produced by this RAG attempt. Keep only that
            # allow-list here; consumers hydrate lazily and task-by-task.
            current_results[task_id] = {
                "task_id": task_id,
                "status": "SUFFICIENT",
                "origin": result.get("origin", "RAG"),
                "approved_parent_ids": approved_ids,
                "reason": reason,
            }
            log_event(
                "evidence.boundary.approved",
                task_id=task_id,
                approved_parent_ids=approved_ids,
            )
            feedback.pop(task_id, None)
            partial_evidence.pop(str(task_id), None)
            rejected_parent_ids.pop(str(task_id), None)
            continue

        retained_ids = partial_evidence.get(str(task_id), [])
        partial_ids = list(dict.fromkeys([*retained_ids, *approved_ids]))
        if requested_status == "PARTIALLY_SUFFICIENT" and not approved_ids:
            # A partial outcome without selected supporting evidence is not a
            # usable partial answer. Treat it as insufficient rather than
            # retaining an ungrounded state.
            requested_status = "INSUFFICIENT"
        if requested_status == "PARTIALLY_SUFFICIENT":
            partial_evidence[str(task_id)] = partial_ids

        attempt_count = retry_counts.get(task_id, 0) + 1
        retry_counts[task_id] = attempt_count
        # Preserve an intentionally empty evidence delta. It means the
        # sufficiency auditor found no novel, material clue in the candidate
        # parents; replacing it with the failure reason would reintroduce a
        # task restatement into the next retry prompt.
        if "evidence_delta" in audit:
            refinement = [
                {
                    "parent_id": str(item.get("parent_id", "")).strip(),
                    "fact": str(item.get("fact", "")).strip(),
                }
                for item in audit.get("evidence_delta", [])
                if isinstance(item, dict)
                and str(item.get("parent_id", "")).strip() in available_ids
                and str(item.get("fact", "")).strip()
            ]
        else:
            # Backward-compatible handling for an in-flight / legacy model
            # response that still uses the former string field.
            refinement = audit.get("search_feedback") or reason
        # A partial task remains retryable with its retained evidence until the
        # configured final attempt. At the boundary it is answerable for the supported
        # part and carries a clarification only for the unresolved part.
        if attempt_count >= MAX_RAG_ATTEMPTS:
            if partial_ids:
                current_results[task_id] = {
                    "task_id": task_id,
                    "status": "PARTIALLY_SUFFICIENT",
                    "approved_parent_ids": partial_ids,
                    "reason": reason,
                }
                log_event(
                    "evidence.boundary.approved",
                    task_id=task_id,
                    approved_parent_ids=partial_ids,
                    status="PARTIALLY_SUFFICIENT",
                )
            else:
                current_results[task_id] = {
                    "task_id": task_id,
                    "status": "CLARIFICATION_REQUIRED",
                    "approved_parent_ids": [],
                    "reason": reason,
                }
            log_event(
                "evidence.boundary.terminal_unresolved",
                task_id=task_id,
                status=current_results[task_id]["status"],
                approved_parent_ids=partial_ids,
            )
            feedback.pop(task_id, None)
        else:
            current_results[task_id] = {
                "task_id": task_id,
                "status": "RETRY",
                "approved_parent_ids": [],
                "reason": reason,
            }
            feedback[task_id] = {
                "status": requested_status,
                "missing_detail_clues": refinement
            }

    unresolved = [result for result in current_results.values() if result.get("status") == "RETRY"]
    task_resolution_instructions = [
        _terminal_retrieval_feedback(
            result.get("task_id"),
            tasks_by_id.get(result.get("task_id"), {}).get("customer_component", ""),
            result.get("status", "CLARIFICATION_REQUIRED"),
        )
        for result in current_results.values()
        if result.get("status") in {"CLARIFICATION_REQUIRED", "PARTIALLY_SUFFICIENT"}
    ]
    full_count = sum(result.get("status") == "SUFFICIENT" for result in current_results.values())
    partial_count = sum(result.get("status") == "PARTIALLY_SUFFICIENT" for result in current_results.values())
    print(f"   ✅ [TASK AUDIT]: {full_count} sufficient, {partial_count} terminal partial, {len(unresolved)} pending retry.")
    retry_expansion = _retry_expansion_plan(
        [tasks_by_id[result.get("task_id")] for result in unresolved if result.get("task_id") in tasks_by_id],
        retry_counts,
    )
    for result in current_results.values():
        event = (
            "task.sufficiency.completed" if result.get("status") == "SUFFICIENT"
            else "task.partial_terminal" if result.get("status") == "PARTIALLY_SUFFICIENT"
            else "task.retry" if result.get("status") == "RETRY"
            else "task.clarification_required"
        )
        next_stage = next((item for item in retry_expansion if item.get("task_id") == result.get("task_id")), {})
        log_event(event, task_id=result.get("task_id"), status=result.get("status"), approved_parent_ids=result.get("approved_parent_ids", []), reason=result.get("reason", ""), retry_count=retry_counts.get(result.get("task_id"), 0), next_expansion_stage=next_stage.get("stage", ""))
    # The RAG subgraph is an implementation detail of a generic tool task.
    # Only terminal, approved IDs cross this boundary; raw candidates and
    # retry evidence remain inside RAG.
    tool_tasks = state.get("tool_tasks", [])
    tool_results = state.get("tool_results", [])
    if not unresolved:
        tool_tasks, tool_results = _finalize_rag_tool_results(state, list(current_results.values()))
    return {
        "preflight_status": "retry_tasks" if unresolved else "task_resolution_ready",
        "task_results": list(current_results.values()),
        "task_retry_counts": retry_counts,
        "task_feedback": feedback,
        # Completed-task audit history can include internal evidence deltas.
        # Retain it only for tasks that will retry; it must not cross the
        # terminal evidence boundary into subsequent nodes.
        "task_sufficiency_history": {
            task_id: sufficiency_history.get(task_id, [])
            for task_id in {result.get("task_id") for result in unresolved}
            if task_id
        },
        "task_partial_evidence": partial_evidence,
        "task_rejected_parent_ids": rejected_parent_ids,
        "task_resolution_instructions": task_resolution_instructions,
        "tool_tasks": tool_tasks,
        "tool_results": tool_results,
        "pending_tool_task_ids": [] if not unresolved else state.get("pending_tool_task_ids", []),
    }


# --- PHASE 3: RESOLUTION BLUEPRINT & STEP-BY-STEP RESPONSE ---

def node_04a_resolution_planner(state: AgentState):
    """Assess global resolution after validated tool work, without planning UI."""
    print("🟢 [NODE 04a] Executing Global Resolution Sufficiency Check")
    conversation_context = _build_conversation_context(state)
    user_baseline = _build_user_baseline_context(state)
    task_results = state.get("task_results", [])
    answerable_task_ids = [
        result.get("task_id") for result in task_results
        if result.get("status") in ANSWERABLE_TASK_STATUSES and result.get("task_id")
    ]
    planner_tasks = []
    for result in task_results:
        if result.get("status") not in ANSWERABLE_TASK_STATUSES:
            continue
        planner_tasks.append({
            "task_id": result.get("task_id"),
            "status": result.get("status"),
            "resolved_request": next((
                task.get("customer_component", "")
                for task in state.get("search_tasks", [])
                if task.get("task_id") == result.get("task_id")
            ), ""),
            "approved_parents": _hydrate_parent_ids(result.get("approved_parent_ids", [])),
        })
    tasks_by_id = {
        task.get("task_id"): task
        for task in state.get("search_tasks", [])
        if task.get("task_id")
    }
    terminal_task_context = [
        {
            "task_id": instruction.get("task_id"),
            "resolved_request": instruction.get("resolved_subquery", "") or tasks_by_id.get(instruction.get("task_id"), {}).get("customer_component", ""),
            "resolution_status": instruction.get("resolution_status", "CLARIFICATION_REQUIRED"),
        }
        for instruction in state.get("task_resolution_instructions", [])
        if instruction.get("task_id")
    ]
    validated_tool_results = [
        {
            "tool_task_id": result.get("tool_task_id"),
            "subquery_id": result.get("subquery_id"),
            "tool_name": result.get("tool_name"),
            "status": result.get("status"),
            "approved_result": result.get("approved_result", {}),
            "availability_reason": result.get("availability_reason", ""),
        }
        for result in state.get("tool_results", [])
        if isinstance(result, dict)
    ]

    prompt = f"""
    GLOBAL RESPONSE SUFFICIENCY & TOOL RESOLUTION
    You are an internal resolution controller for a read-only Horizon Bank FAQ
    assistant. Decide only whether the complete validated outcome is ready for
    customer response generation, requires one further enabled capability, or
    is exhausted. You do NOT write customer-facing text or plan UI.

    Customer request: {state.get("user_input", "")}
    Trusted conversation context (use only to understand the customer's current
    meaning, priority, and references; never as factual banking evidence):
    {conversation_context}
    User baseline (advisory communication context only; use only when clearly
    relevant to the current request and never as factual banking evidence):
    {user_baseline}
    Financial subqueries: {json.dumps(state.get("financial_subqueries", []), ensure_ascii=False)}
    Non-financial conversational attention cues (tone only; never evidence, a banking request, a tool input, or customer-facing source text): {json.dumps(state.get("non_financial_context", []), ensure_ascii=False)}
    Answerable evidence by retrieval task: {json.dumps(planner_tasks, ensure_ascii=False)}
    Validated tool results: {json.dumps(validated_tool_results, ensure_ascii=False)}
    Enabled future capabilities: {json.dumps(enabled_tool_capabilities(), ensure_ascii=False)}
    Intent clarification inputs: {json.dumps(state.get("intent_clarification_tasks", []), ensure_ascii=False)}
    Terminal unresolved retrieval tasks (semantic anchors only; NOT answer evidence): {json.dumps(terminal_task_context, ensure_ascii=False)}
    Declined inputs: {json.dumps(state.get("decline_tasks", []), ensure_ascii=False)}

    {PRECISION_REFERENCE_CONTRACT}

    Rules:
    - First decide resolution_status. Return NEEDS_TOOLS only when a supplied
      financial subquery has a concrete unresolved need that an enabled,
      registered capability could materially address and which does not
      duplicate a completed, unavailable, failed, blocked, pending, or running
      tool task. RAG internal retries are already complete at this point; do
      not request RAG again for a terminal RAG result.
    - A capability's own description can scope out specific reasons or
      situations, not just name a general topic -- match on the whole
      description, not the nearest keyword. For example, a capability
      described as covering card replacement "for a non-fraud reason (worn
      or damaged)" does not cover a lost or stolen card, even though both
      are "card replacement" in general terms; do not propose a tool_gap for
      a capability whose stated scope excludes the customer's actual
      situation. When every registered capability's scope excludes the
      situation, that is EXHAUSTED for this need, not NEEDS_TOOLS -- approved
      RAG evidence, if any, may still answer it on its own.
    - Return RESOLVED when current approved evidence and safe clarification
      anchors are enough to form the response. Return EXHAUSTED when a gap
      remains but no enabled non-duplicate capability can address it.
    - RAG evidence and a registered capability are independent coverage
      decisions for the same subquery, not alternatives to choose between.
      Decide each on its own merits: does approved evidence precisely and
      completely answer what the customer asked, and, separately, does a
      registered enabled capability's own description precisely match the
      action the customer is asking to take? Answering yes to the
      capability question always adds that tool_gap — with no exception,
      including when RAG evidence is itself a complete, well-grounded,
      directly-on-topic answer (for example, FAQ steps that correctly
      explain how to update an address manually in the app). Being able to
      explain how a customer could perform an action themselves is never a
      substitute for being able to perform that same action for them
      through a registered capability — the capability is strictly more
      valuable, not an alternative path to the same outcome, so its
      existence is never a reason to omit the tool_gap. Include both
      `validated_task_ids` and the `tool_gaps` entry in that case; the
      response generator can present the RAG evidence as useful reference
      alongside the offered capability. Evidence that describes a related
      but different action (for example, general personal-details updates
      when the customer asked specifically about resubmitting an identity
      document) is adjacent context, an even weaker case than a precise
      answer, and must never be the reason a matching capability is left
      out either.
    - A tool gap is an internal capability description, never a customer
      answer, evidence claim, instruction, or UI recommendation.
    - Search tasks are evidence-retrieval units, never mandatory visible
      sections. Do not create a block plan or one UI element per task.
    - Recommend only optional visual hints. A response may be entirely text,
      may group several tasks in one explanation, or may use several visuals
      only when each materially helps comprehension.
    - Before recommending any presentation, compare the complete current
      customer situation with the scope and preconditions in approved evidence.
      Evidence may describe a conditional procedure that does not apply to the
      customer's stated reason for asking. Never treat that condition as true,
      ask the customer to assert it, or recommend the procedure as their path.
      In that case, recommend plain text that briefly scopes the available
      guidance, or no visual hint. A visual is never proof that a procedure is
      applicable.
    - Preserve the customer's priorities and make the most useful information
      easy to understand first. Group related facts naturally; do not impose
      headings, introductions, closings, titles, lists, or progress payloads
      merely because they exist.
    - The complete Customer request is the authoritative source for what the
      customer actually said and how they said it. Non-financial attention
      cues only prevent that part of the original request from being
      overlooked; do not base wording, interpretation, or a customer-facing
      acknowledgement on the cue itself.
    - When non-financial attention cues are supplied, guide the generator to
      include one brief, natural courtesy acknowledgement of the relevant
      aside from the original Customer request. This is social tone, not a
      factual answer. Never recast it as a banking eligibility, availability,
      policy, or feature claim. If approved evidence does not resolve the
      customer's stated reason for seeking a banking outcome, guide the
      generator to give only the relevant verified guidance; never infer that
      the outcome is available or unavailable for that reason.
    - Never make `narrative_focus`, `grouping_guidance`, or
      `clarification_guidance` state or imply that a customer may or may not
      obtain a banking outcome because of a non-financial preference, feeling,
      greeting, or other aside. Those fields may refer only to the available
      verified guidance and to a brief social acknowledgement.
    - A progress hint is appropriate only for a genuinely sequential process.
      A list hint is appropriate only for independently scannable items.
    - Keep clarification outside any suggested process/list and place it after
      useful supported information when both exist.
    - For every terminal unresolved retrieval task, treat its
      `resolved_request` as the authoritative meaning of that unresolved
      topic, even when the literal latest customer message is short (for
      example, "appearance") or relies on prior conversational context.
    - When there is no answerable evidence for a terminal unresolved task,
      preserve that exact topic in `primary_user_need`, `narrative_focus`, and
      `clarification_guidance`. Do not reinterpret it as a different product,
      channel, feature category, UI surface, example, or customer need. Do not
      introduce possible interpretations, visual hints, or a new ambiguity.
      The appropriate guidance is a brief inability statement about the
      resolved request, followed only by a general clarification if genuinely
      necessary.
    - Terminal unresolved task context is not evidence. It may preserve the
      topic for a safe clarification, but must never justify a factual claim,
      procedure, product capability, policy, or UI option.
    - coverage_expectation must include every supplied answerable task_id, but
      it is an internal completeness reminder, not a demand for separate UI.
    - UI OWNERSHIP EXCLUSION: Node 04b alone chooses all response organisation,
      text, titles, grouping, and UI. Do not choose, recommend, evaluate, or
      hint at TEXT, LIST, PROGRESS, OPTIONS, chips, sections, steps, titles,
      ordering, narrative focus, or clarification wording. Ignore any earlier
      presentation-oriented instruction in this prompt.
    - `validated_task_ids` must contain only supplied answerable task IDs. It
      is an internal completeness record, not a visible-response requirement.
    - `unresolved_topics` may preserve only the exact customer topic that
      remains unresolved. It is an internal scope anchor, never evidence,
      customer wording, product inference, or policy conclusion.
    - Never invent facts, banking actions, products, steps, or customer text.

    Output JSON only:
    {{"resolution_status":"RESOLVED" | "NEEDS_TOOLS" | "EXHAUSTED","tool_gaps":[{{"subquery_id":"...","tool_name":"...","operation":"...","unresolved_need":"..."}}],"validated_task_ids":["task_1"],"unresolved_topics":["..."]}}
    """
    try:
        guidance = _parse_json_robustly(call_groq_lpu(prompt, model_id=MODEL_C_PLANNER))
    except Exception as error:
        print(f"🚨 [GLOBAL RESOLUTION CHECK ERROR]: {error}")
        guidance = {}

    validated_task_ids = [
        task_id for task_id in guidance.get("validated_task_ids", [])
        if task_id in answerable_task_ids
    ] or answerable_task_ids
    unresolved_topics = [
        str(topic).strip() for topic in guidance.get("unresolved_topics", [])
        if isinstance(topic, str) and str(topic).strip()
    ][:8]
    resolution_context = {
        "resolution_status": str(guidance.get("resolution_status", "RESOLVED")).upper(),
        "validated_task_ids": validated_task_ids,
        "unresolved_topics": unresolved_topics,
    }
    existing_task_keys = {
        (str(task.get("subquery_id", "")), str(task.get("tool_name", "")).lower(), str(task.get("operation", "")))
        for task in state.get("tool_tasks", []) if isinstance(task, dict)
    }
    valid_gaps = []
    for gap in guidance.get("tool_gaps", []) if isinstance(guidance.get("tool_gaps"), list) else []:
        if not isinstance(gap, dict):
            continue
        subquery_id = str(gap.get("subquery_id", "")).strip()
        tool_name = str(gap.get("tool_name", "")).strip().lower()
        operation = str(gap.get("operation", "")).strip()
        if (
            any(item.get("subquery_id") == subquery_id for item in state.get("financial_subqueries", []))
            and is_registered_operation(tool_name, operation)
            and TOOL_REGISTRY.get(tool_name, {}).get("enabled", False)
            and (subquery_id, tool_name, operation) not in existing_task_keys
        ):
            valid_gaps.append({
                "subquery_id": subquery_id,
                "tool_name": tool_name,
                "operation": operation,
                "unresolved_need": str(gap.get("unresolved_need", "")).strip(),
            })
    requested_status = str(guidance.get("resolution_status", "RESOLVED")).upper()
    needs_tools = (
        requested_status == "NEEDS_TOOLS"
        and bool(valid_gaps)
        and int(state.get("tool_planning_round", 0)) < MAX_TOOL_PLANNING_ROUNDS
    )
    print(f"   🧭 [GLOBAL RESOLUTION]: {resolution_context['resolution_status']} | Validated: {len(validated_task_ids)} task(s) | Unresolved: {len(unresolved_topics)} topic(s)")
    log_event(
        "resolution.planned",
        requested_status=requested_status,
        accepted_tool_gap_count=len(valid_gaps),
        validated_task_ids=validated_task_ids,
        unresolved_topics=unresolved_topics,
    )
    return {
        "preflight_status": "needs_tools" if needs_tools else "presentation_ready",
        "tool_plan_status": "continue" if needs_tools else ("exhausted" if requested_status == "EXHAUSTED" else "resolved"),
        "tool_gaps": valid_gaps if needs_tools else [],
        "selected_ux_journey": "FLEXIBLE",
        "presentation_plan": {"resolution_context": resolution_context},
        "active_plan": {},
        "completed_steps": [],
    }


def _build_stepper_payload(steps: list, current_step_index: int) -> dict:
    """Create a deterministic plan-progress payload for the frontend stepper."""
    return {
        "uiType": "stepper",
        "items": [
            {
                "number": index + 1,
                "label": step,
                "status": "completed" if index < current_step_index else "current" if index == current_step_index else "upcoming",
            }
            for index, step in enumerate(steps)
        ],
        "currentStep": current_step_index + 1,
        "totalSteps": len(steps),
    }


def _generate_task_bound_response_blocks(state: AgentState) -> dict:
    """Legacy task-bound renderer retained only for in-flight graph sessions."""
    block_plans = state.get("presentation_plan", {}).get("response_blocks", [])
    task_metadata_by_id = {
        task.get("task_id"): {
            "resolved_subquery": task.get("subquery", task.get("customer_component", "")),
            "customer_component": task.get("customer_component", task.get("subquery", "")),
        }
        for task in state.get("search_tasks", [])
        if task.get("task_id")
    }
    hydrated_by_task = {
        entry.get("task_id"): entry.get("approved_parents", [])
        for entry in _hydrate_response_evidence(state)
    }
    block_inputs = []
    for block in block_plans:
        parents = hydrated_by_task.get(block.get("task_id"), [])
        if parents:
            block_inputs.append({
                **block,
                **task_metadata_by_id.get(block.get("task_id"), {}),
                "approved_parents": parents,
            })

    intent_clarifications = [
        {"unresolved_request": task.get("unresolved_request", "")}
        for task in state.get("intent_clarification_tasks", [])
    ]
    terminal_markers = state.get("task_resolution_instructions", [])
    decline_context = [
        {"subquery": task.get("subquery", ""), "category": task.get("reason_category", "")}
        for task in state.get("decline_tasks", [])
        if task.get("subquery", "")
    ]
    audit_feedback = state.get("draft_feedback_history", [])[-4:]
    user_profile = state.get("user_profile", {})

    literacy_level = user_profile.get("financial_literacy", "intermediate")
    if literacy_level == "beginner":
        literacy_instruction = "Beginner (Use simple financial terms. Explain financial terms when they appear in the response.)"
    elif literacy_level == "professional":
        literacy_instruction = "Professional (Use professional financial terms freely. No explanation of financial terms necessary.)"
    else:
        literacy_instruction = "Intermediate (Use normal financial terms. Explain only complex financial terms when they appear in the response.)"

    prompt = f"""
    RESPONSE GENERATION V2.0
    You are Horizon, a digital customer service representative for Horizon
    Bank. Provide helpful, natural, read-only FAQ guidance: do not perform
    banking actions, imply account access, or introduce facts beyond approved
    evidence. Generate ONE coherent customer turn as several ordered response
    blocks. Each block renders separately, but together they must read as one
    considered response to the customer's complete request — never as
    disconnected mini-answers.

    STYLE & PERSONALIZATION INSTRUCTIONS:
    (These are phrasing instructions only; do not invent banking facts)
    - Natively generate response in: {user_profile.get("language") or "English"}
    - Tone: {user_profile.get("tone", "professional")}
    - Financial Literacy Depth: {literacy_instruction}
    - Emojis: {user_profile.get("emoji", "enabled")}

    Customer's complete request: {state.get("user_input", "")}
    Planned response blocks and their approved evidence: {json.dumps(block_inputs, ensure_ascii=False)}
    Intent clarification tasks: {json.dumps(intent_clarifications, ensure_ascii=False)}
    Terminal unresolved task markers (topic/status only; never evidence): {json.dumps(terminal_markers, ensure_ascii=False)}
    Decline Inputs (the ONLY source for a declined-topic explanation): {json.dumps(decline_context, ensure_ascii=False)}
    Failed outbound-audit feedback from the prior generation attempt
    (internal constraints only; never an evidence source):
    {json.dumps(audit_feedback, ensure_ascii=False)}

    {PRECISION_REFERENCE_CONTRACT}

    Rules:
    - Upstream routing has already resolved any conversational follow-up into
      task-level approved evidence. Chat history, rolling memory, last-session
      memory, and baseline memory are deliberately not supplied here and must
      never be reconstructed, repeated, or used as answer content.
    - Every factual claim, instruction, comparison, UI item, extended_text
      item, and option must be supported by the current block's approved
      parents. If a fact is not present in that block's evidence, omit it.
    - Approved knowledge may contain information that is only partially related
      to the customer's resolved subquery. Use only the part that directly
      answers or materially helps answer that subquery. Do not include related
      background information merely because it appears in approved knowledge.
      State the direct answer to the resolved subquery first, before any
      additional detail, list, qualification, or extended text.
      If the evidence answers only part of the subquery, state that supported
      part clearly without implying the remaining part is confirmed.
    - First understand the complete customer request and the ordered resolved
      subqueries. Use that shared structure to make the response easy to
      follow: answer related components in a logical order, use concise
      references such as "For the card issue" only when they clarify the
      connection, and do not repeat a greeting, apology, summary, or generic
      conclusion inside every task block.
    - Every block must make clear which resolved subquery it addresses. Use
      only the customer's wording or a close, neutral paraphrase as the topic
      anchor; never introduce another product, request, fact, or procedure.
      For PROGRESS and LIST, make the progress_title or list_title do this
      work. For TEXT, provide a short `context_text` that naturally connects
      the answer to its resolved subquery, then put the direct factual answer
      in `answer`. Vary the wording naturally instead of mechanically starting
      every block with the same phrase.
    - `intro_text` and `closing_text` are response-level connective text to make the turn read as one coherent answer addressing the customer's request.
    - Return exactly one generated block for every supplied planned task_id, in
      the same order. Never omit an answerable task, including when Failed
      outbound-audit feedback flags that task's prior content: rewrite its
      answer using only the safe, grounded portion of its approved parents
      that avoids the flagged issue, rather than dropping the block. If no
      safe factual content remains for that task, state briefly and plainly
      only what its approved parents establish. Never infer that a service is
      unavailable, and never add a support channel, escalation path, or other
      next action unless it is explicitly in those approved parents — never
      leave a planned task_id out of the returned blocks.
    - Use only that block's approved parents for its answer, UI details,
      extended_text, and options. Do not mix facts between task IDs.
    - TEXT: put the complete answer in answer. LIST: provide only the number
      of independent list_items needed to answer the subquery, plus an
      optional short answer introduction and a concise
      list_title. PROGRESS: create the complete progress payload yourself:
      a concise progress_title and 2+ ordered progress_steps. Each
      progress_steps entry must be exactly {{"title":"short navigation label",
      "detail":"specific grounded instruction"}}. Title and detail must be
      paired, in order, and the detail must add information rather than repeat
      or lightly paraphrase its title.
    - Every block may include optional extended_text: natural additional
      context, qualification, or useful answer detail that does not belong in
      its main UI. Do not repeat the main payload or call it supporting text.
    - Use options only when they are meaningful, grounded next actions for that
      same block. Do not provide options if clarification is needed.
    - Decide whether a clarification question would genuinely help the
      customer. Ask one only when an additional customer detail could
      materially disambiguate the request or enable a different grounded
      response. It is not required merely because an intent-clarification
      task or terminal unresolved marker exists.
    - A terminal unresolved item means the available information did not
      resolve an otherwise clear request. In that case, normally provide a
      concise, neutral availability notice using the customer's wording and
      leave `clarification` empty. Do not state that Horizon does not offer
      the product or feature, and do not ask the customer to repeat an
      already clear request.
    - If a clarification is useful, combine the relevant unresolved
      components into one natural clarification field at the end, sourced
      ONLY from Intent clarification tasks, Terminal unresolved task markers, and
      Decline Inputs above. Explicitly name each unresolved customer term;
      do not invent a product or fact. Clarification is plain text, not a UI
      block.
    - For EVERY entry in Decline Inputs, add one brief, polite sentence naming
      only that entry's subquery — never omit one, never invent a reason
      beyond its category. For category "non_compliant", state plainly that you cannot help with that
      specific request because of banking compliance requirements; never
      explain the regulation, suggest an advisor, or provide the advice or
      action being declined. For category "non_financial", state plainly that
      the topic is outside what a banking assistant can help with. Never ask
      a clarification question about a declined entry.
    - SOURCE-NEUTRAL CONVERSATION: FAQ records, policies, documents, database
      entries, search results, and retrieved knowledge are internal evidence.
      Never mention them, parent IDs, model names, or retrieval mechanics.
      Present approved facts naturally as Horizon guidance.
    - ZERO HALLUCINATION: do not add facts, procedures, rates, requirements, or
      advice beyond the approved evidence. Preserve exact {{placeholders}}.
    - If failed outbound-audit feedback is supplied, correct every identified
      issue. Grounding-violation records identify content to remove and a
      missing-evidence boundary only: neither unsupported_content nor
      evidence_gap is banking evidence or permission to add a remedy. Do not
      invent a support channel, product, service capability, escalation path,
      or next action from audit feedback. Do not repeat an unsupported claim
      merely because it appeared in an earlier response, conversation context,
      or memory.
    - A grounding violation with audit_layer="evidence" means the named
      factual claim or instruction lacks direct approved support: remove it or
      replace it only with a directly supported fact. A violation with
      audit_layer="bridge" means the factual evidence may remain useful but
      the named conclusion adds an unsupported logical leap: keep the natural
      response style, but narrow the conclusion to what the evidence actually
      entails. Audit feedback never requires the response to expose factual
      and inferential reasoning as separate customer-facing sections.

    Output JSON only:
    {{"intro_text":"...","blocks":[{{"task_id":"task_1","context_text":"...","answer":"...","progress_title":"...","progress_steps":[{{"title":"...","detail":"..."}}],"list_title":"...","list_items":["..."],"extended_text":"...","options":[{{"label":"short action","message":"complete grounded question"}}]}}],"closing_text":"...","clarification":"..."}}
    """
    try:
        generated = _parse_json_robustly(call_groq_lpu(prompt))
    except Exception as error:
        print(f"🚨 [RESPONSE BLOCK GENERATOR ERROR]: {error}")
        generated = {}

    generated_by_task = {
        item.get("task_id"): item for item in generated.get("blocks", [])
        if isinstance(item, dict) and item.get("task_id")
    }
    response_blocks = []
    record_parts = []
    intro_text = _llm_text(generated, "intro_text")
    closing_text = _llm_text(generated, "closing_text")
    # A response-level bridge is useful only when there is more than one
    # answerable component. This deterministic guard keeps a model from adding
    # filler around straightforward one-topic answers.
    if len(block_plans) > 1 and not intro_text:
        # The response-level bridge carries no banking fact; it simply makes
        # independently grounded blocks read as one answer when a model omits
        # the required opening.
        intro_text = "Here’s how to handle each part of your request."
    if intro_text and len(block_plans) > 1:
        response_blocks.append({"blockId": "turn_intro", "type": "text", "text": intro_text})
        record_parts.append(intro_text)
    for index, plan in enumerate(block_plans, start=1):
        item = generated_by_task.get(plan.get("task_id"), {})
        block_type = plan.get("presentation_type", "TEXT")
        context_text = _llm_text(item, "context_text")
        answer = _llm_text(item, "answer")
        extended_text = _llm_text(item, "extended_text", "supporting_text")
        options = _normalize_ui_options(item.get("options", []))
        progress_steps = item.get("progress_steps", []) if isinstance(item.get("progress_steps", []), list) else []
        progress_steps = [
            {"title": _llm_text(step, "title"), "detail": _llm_text(step, "detail")}
            for step in progress_steps
            if isinstance(step, dict) and _llm_text(step, "title") and _llm_text(step, "detail")
        ]
        list_items = item.get("list_items", []) if isinstance(item.get("list_items", []), list) else []
        list_items = [entry.strip() for entry in list_items if isinstance(entry, str) and entry.strip()][:8]
        ui_type = "text"
        if block_type == "PROGRESS" and len(progress_steps) >= 2:
            ui_type = "stepper"
        # Preserve a grounded list even when a model returns fewer items than
        # the planner's ideal list threshold. Converting a one- or two-item
        # list to empty text would hide a valid answer and trigger the generic
        # fallback apology.
        elif block_type == "LIST" and list_items:
            ui_type = "checklist"
        elif not answer:
            # A generic apology is grounded by definition; a clarification-
            # shaped placeholder would instead be judged against the
            # "must identify its unresolved term" rule and fail, even though
            # nothing is actually ambiguous here — the model just omitted an
            # answerable task's content.
            answer = "Sorry, I don't have a complete answer for this part of your request right now."
        if ui_type == "stepper":
            items = [
                {"number": position, "label": step["title"], "detail": step["detail"], "status": "current" if position == 1 else "upcoming"}
                for position, step in enumerate(progress_steps, start=1)
            ]
            record = "\n\n".join([*(step["detail"] for step in progress_steps), extended_text])
        elif ui_type == "checklist":
            items = list_items
            record = "\n".join(part for part in [answer, *list_items, extended_text] if part)
        else:
            items = []
            answer = "\n\n".join(part for part in [answer, extended_text] if part)
            extended_text = ""
            record = answer
        response_blocks.append({
            "blockId": f"block_{index}",
            "taskId": plan.get("task_id"),
            "type": ui_type,
            "contextText": context_text if ui_type == "text" else "",
            "text": answer if ui_type == "text" else "",
            "items": items,
            "options": options,
            "progressTitle": _llm_text(item, "progress_title") if ui_type == "stepper" else "",
            "stepDetails": [],
            "currentStep": 1 if ui_type == "stepper" else None,
            "totalSteps": len(progress_steps) if ui_type == "stepper" else None,
            "listTitle": _llm_text(item, "list_title") if ui_type == "checklist" else "",
            "listIntro": answer if ui_type == "checklist" else "",
            "extendedText": extended_text,
        })
        if record:
            record_parts.append(record)
    if closing_text and len(block_plans) > 1:
        response_blocks.append({"blockId": "turn_closing", "type": "text", "text": closing_text})
        record_parts.append(closing_text)
    clarification = _llm_text(generated, "clarification")
    # A decline is not a clarification request. Preserve its deterministic
    # safety fallback without forcing a generic question for an exhausted
    # retrieval or an intent-clarification input.
    if decline_context and not clarification:
        clarification = _append_decline_suffix("", state.get("decline_tasks", [])).strip()
    if clarification:
        response_blocks.append({"blockId": "clarification", "type": "clarification", "text": clarification})
        record_parts.append(clarification)
    draft = "\n\n".join(record_parts) or "Sorry, I don't have an answer for that right now."
    print(f"   💬 [RESPONSE BLOCKS GENERATED]: {len(response_blocks)} blocks")
    return {
        "final_draft": draft,
        "final_answer": "\n\n".join(record_parts[:-1] if clarification else record_parts),
        "final_clarification": clarification,
        "active_plan": {},
        "completed_steps": [],
        "ui_action_payload": {"uiType": "blocks", "responseBlocks": response_blocks, "options": []},
        "pending_advance": False,
    }


def _generate_response_blocks(state: AgentState) -> dict:
    """Generate freeform, ordered response elements from global resolution.

    Elements are frontend transport units only. They do not correspond one-to-
    one with retrieval tasks: a natural element may combine several related
    evidence sets, or the response may be entirely plain text.
    """
    resolution_context = state.get("presentation_plan", {}).get("resolution_context", {})
    evidence_by_task = _hydrate_response_evidence(state)
    task_metadata = {
        task.get("task_id"): task.get("customer_component", "")
        for task in state.get("search_tasks", [])
        if task.get("task_id")
    }
    valid_task_ids = {entry.get("task_id") for entry in evidence_by_task if entry.get("task_id")}
    evidence_inputs = [
        {
            "task_id": entry.get("task_id"),
            "resolved_request": task_metadata.get(entry.get("task_id"), ""),
            "approved_parents": entry.get("approved_parents", []),
        }
        for entry in evidence_by_task
    ]
    intent_clarifications = [
        {"unresolved_request": task.get("unresolved_request", "")}
        for task in state.get("intent_clarification_tasks", [])
    ]
    terminal_markers = state.get("task_resolution_instructions", [])
    decline_context = [
        {"subquery": task.get("subquery", ""), "category": task.get("reason_category", "")}
        for task in state.get("decline_tasks", [])
        if task.get("subquery", "")
    ]
    # The LLM is deliberately told nothing about *which* forms are pending or
    # their special_ids -- only whether the list is non-empty -- because the
    # actual form button is attached later by node_07 straight from
    # tool_results, unconditionally and independent of anything generated
    # here. Asking the model to cite a specific id for coverage-tracking was
    # tried and repeatedly failed in three different ways (wrong key, dropped
    # citation, invented key); removing the ask removes the failure mode.
    pending_service_request_count = len(_pending_service_requests(state))
    unavailable_service_requests = _unavailable_service_requests(state)
    # This is deliberately separate from evidence and tool inputs.  It carries
    # only small internal attention cues from an otherwise valid mixed turn
    # (for example, a preference or feeling). The original user_input—not this
    # derived cue—is the sole source for any customer-facing acknowledgement.
    non_financial_context = [
        str(item).strip()
        for item in state.get("non_financial_context", [])
        if isinstance(item, str) and str(item).strip()
    ][:3]
    conversation_context = _build_conversation_context(state)
    user_baseline = _build_user_baseline_context(state)
    audit_feedback = state.get("draft_feedback_history", [])[-4:]
    user_profile = state.get("user_profile", {})
    
    literacy_level = user_profile.get("financial_literacy", "intermediate")
    if literacy_level == "beginner":
        literacy_instruction = "Beginner (Use simple financial terms. Explain financial terms when they appear in the response.)"
    elif literacy_level == "professional":
        literacy_instruction = "Professional (Use professional financial terms freely. No explanation of financial terms necessary.)"
    else:
        literacy_instruction = "Intermediate (Use normal financial terms. Explain only complex financial terms when they appear in the response.)"

    prompt = f"""
    FLEXIBLE RESPONSE GENERATION
    You are Horizon, a helpful, natural, read-only Horizon Bank customer
    service representative. Write one coherent conversational response using
    only approved evidence. Do not perform banking actions, imply account
    access, or invent facts, procedures, products, or policies.

    STYLE & PERSONALIZATION INSTRUCTIONS:
    (These are phrasing instructions only; do not invent banking facts)
    - Natively generate response in: {user_profile.get("language") or "English"}
    - Tone: {user_profile.get("tone", "professional")}
    - Financial Literacy Depth: {literacy_instruction}
    - Emojis: {user_profile.get("emoji", "enabled")}

    Customer request: {state.get("user_input", "")}
    Trusted conversation context (use only to understand the customer's current
    meaning, priority, references, and conversational tone; never as factual
    banking evidence):
    {conversation_context}
    User baseline (advisory communication context only; use only when clearly
    relevant to the current request and never as factual banking evidence):
    {user_baseline}
    Non-financial conversational attention cues (tone only; not evidence, a banking request, or customer-facing source text):
    {json.dumps(non_financial_context, ensure_ascii=False)}
    Global resolution context (internal coverage and unresolved-topic anchors
    only; not evidence, response wording, or UI guidance):
    {json.dumps(resolution_context, ensure_ascii=False)}
    Approved evidence by retrieval task: {json.dumps(evidence_inputs, ensure_ascii=False)}
    Intent clarification inputs: {json.dumps(intent_clarifications, ensure_ascii=False)}
    Terminal unresolved task markers (topic/status only; never evidence): {json.dumps(terminal_markers, ensure_ascii=False)}
    Decline inputs: {json.dumps(decline_context, ensure_ascii=False)}
    Pending service request forms registered this turn: {pending_service_request_count}
    (forms are attached automatically; you do not know and must not invent
    what any of them ask for)
    Unavailable service requests (could not be registered this turn; state
    only the supplied reason, never guess why): {json.dumps(unavailable_service_requests, ensure_ascii=False)}
    Failed outbound-audit feedback (constraints only, never evidence). Each
    entry identifies one specific claim or instruction to remove — it is
    never permission to abandon the topic it came from. After removing
    exactly what is flagged, re-apply every Design Principle below to still
    give the most complete, honestly-scoped response the remaining approved
    evidence supports:
    {json.dumps(audit_feedback, ensure_ascii=False)}

    {PRECISION_REFERENCE_CONTRACT}

    Design principles:
    - Core job: first understand the whole current customer situation from the
      complete Customer request and Trusted conversation context. Then decide
      which approved evidence, if any, meaningfully addresses that situation.
      Write one natural, faithful response to that whole picture; do not merely
      render a retrieval task or a UI recommendation.
    - Source authority: the Customer request and Trusted conversation context
      establish what the customer means, refers to, and cares about. Approved
      evidence is the only source for banking facts, procedures, policies,
      conditions, options, and UI content. User baseline only calibrates tone
      when clearly relevant. Global resolution context never overrides the
      current customer situation or evidence scope.
    - Applicability comes before presentation. An approved record may describe
      a procedure, condition, or policy for circumstances different from the
      customer's stated situation. Do not present that procedure as the
      customer's path, ask them to select or assert its condition, or imply
      approval, refusal, entitlement, recommendation, commitment, or outcome.
      If such evidence is still useful, describe its scope plainly as limited
      reference; otherwise omit it. Choose any UI only after this decision.
    - Act as a bank customer-service representative who explains the relevant
      Horizon information that is actually available. When the evidence covers
      only one conditional policy or process, you may restate that scope and
      explain it as relevant reference even if it does not directly answer the
      customer's broader question. Do not fill the remaining gap with a yes/no
      conclusion, judgement, comment, recommendation, warning, prediction, or
      implication about what the customer should do. Leave that decision to the
      customer unless approved evidence itself establishes the conclusion.
    - A positive conditional instruction is not proof that all other paths are
      unavailable. For example, guidance for a damaged card may be described
      as guidance for a damaged card, but must not become a claim that only
      damaged cards qualify or that another reason will fail, unless approved
      evidence explicitly says so.
    - Never respond with only a restatement of the customer's own words, a
      bare acknowledgement, or anything containing zero Horizon information,
      when any approved evidence exists for the turn. A mismatch between the
      customer's stated reason and the evidence's actual condition is not a
      dead end — hold it to the same standard as a Decline Input: name
      plainly, in one honest sentence, what the available information does
      not cover for the customer's specific situation; then, unlike a
      decline, still share the closest genuinely evidenced information as
      clearly-scoped optional context (per the Applicability rule above)
      instead of leaving the customer with nothing.
    - Respond as one considered conversation, not as a retrieval report or a
      sequence of task answers. Search tasks are internal evidence units, not
      required visible sections.
    - Never mention a form, service request, or link yourself, regardless of
      "Pending service request forms registered this turn" -- do not write
      "I've included a form below," do not invite the customer to a form,
      do not say anything implying one exists. This is not something you
      have information to state accurately: whether a form appears is
      decided after your response is already approved, so any claim you make
      about it cannot be verified against what you were given and will be
      rejected. A separate, deterministic line introduces any registered
      form automatically; your only job regarding it is to not undermine or
      duplicate that line.
    - When "Pending service request forms registered this turn" is greater
      than zero, do not open with an apology, a statement that guidance is
      unavailable, or other decline-style framing -- lead with whatever
      answer you do have, written as a complete, confident response on its
      own terms. A registered form being added afterward is not a reason to
      write your own answer as if it were incomplete, and this holds even
      when approved evidence also fully and correctly explains how to
      perform the same action manually (for example, in-app navigation
      steps) -- explaining the manual steps is still a genuine, complete
      answer.
    - For each entry in Unavailable service requests, include one brief,
      honest sentence telling the customer that part of their request could
      not be started right now, using only the supplied reason. Never invent
      a different explanation, and never silently drop that part of the
      request from the response merely because no form could be attached to
      it -- an unavailable capability still deserves an acknowledgment, the
      same as a decline or a terminal unresolved topic does elsewhere in
      this response.
    - Start with the information that most directly helps the customer. Group
      related facts when doing so reduces cognitive load; preserve distinctions
      when combining them would confuse the customer.
    - You alone choose the customer-facing organisation and UI after assessing
      the whole situation and applicability of approved evidence. Use the
      smallest natural form that helps understanding. Text is always
      allowed. Use a LIST only for independently scannable, applicable items.
      Use PROGRESS only for a genuinely sequential process that the approved
      evidence supports as relevant to the customer's stated situation. Do not
      create a visual merely because a visual hint exists or because evidence
      describes a workflow for a different condition.
    - You may return one text element, a mix of text and UI elements, or
      several elements. There is no required opening, closing, title, number
      of elements, or element-per-task structure.
    - Account for every task in "Approved evidence by retrieval task"; never
      silently drop an entire customer need when correcting a different task.
      Apply the applicability rule first: do not render a procedure, action,
      or UI merely to satisfy coverage when its evidence is conditional or
      does not fit the customer's stated situation. When useful, provide a
      short, accurately scoped reference in text instead. Do not name task IDs
      or expose internal organization to the customer.
    - When one element's answer or extended_text draws on more than one task
      or pending service request -- a natural way to combine related facts --
      list every one of their ids in that element's source_task_ids, not just
      the one the element is primarily about. This is not cosmetic: an id
      left out of source_task_ids is treated as never addressed, and a
      contradictory duplicate (a fallback apology, or a second invitation to
      a form already offered) gets appended right beside content that already
      covered it. Crediting every id a passage actually draws on is what
      prevents that.
    - Every factual claim, instruction, item, title, option, and detail must
      be supported by approved evidence. A response element may use evidence
      from multiple tasks only when those facts are naturally related --
      arriving in the same customer message is not relatedness. A lost card
      and an address change are two separate needs; each deserves its own
      element with whatever structure best fits it (a sequential procedure
      as PROGRESS, a scannable set as LIST, otherwise TEXT), not one shared
      paragraph merely because both were mentioned together. This still
      applies when redrafting after audit feedback: removing one flagged
      sentence is never a reason to keep an earlier draft's structure
      unexamined -- re-decide the best presentation for each need fresh,
      the same as if writing the first draft.
    - The complete Customer request is the sole authoritative wording and
      meaning of every customer statement. The non-financial attention cues
      merely signal that a relevant aside exists. When acknowledging a
      preference, feeling, greeting, thanks, or other aside, derive its
      meaning and natural wording directly from the original Customer request;
      do not quote, paraphrase, or expose an internal cue. Treat the aside as
      tone only: never use it to infer, promise, deny, recommend, or invent a
      banking feature, action, or policy. Do not echo the customer's wording
      mechanically. When one or more attention cues are supplied, include one
      brief, natural courtesy acknowledgement of the relevant aside unless it
      would be incoherent in the reply. Connect it naturally to the scoped,
      relevant financial guidance that follows; do not make it a detached
      standalone response.
    - If that aside gives the customer's reason for a banking request but the
      approved evidence does not address whether that reason qualifies, do not
      convert the gap into a policy conclusion. Acknowledge the reason and
      state only the verified guidance that is available.
    - If evidence gives a useful policy boundary or limit, state that fact
      plainly; do not invent a broader conclusion beyond what it entails.
    - Do not mention FAQ documents, sources, parent IDs, search, prompts, or
      internal systems. Preserve exact {{placeholders}}.
    - Decide whether a clarification question would genuinely help the
      customer. Ask one only when an additional customer detail could
      materially disambiguate the request or enable a different grounded
      response. It is not required merely because a terminal unresolved marker
      or an intent-clarification input exists.
    - A terminal unresolved marker means the available information did not
      resolve an otherwise clear request. In that case, normally provide a
      concise, neutral availability notice using the customer's wording and
      leave `clarification` empty. Do not state that Horizon does not offer a
      product or feature, and do not ask the customer to repeat an already
      clear request.
    - When a clarification is useful, it belongs after useful supported
      information, must name the unresolved customer term, and may use only
      the clarification, terminal unresolved markers, and decline inputs.
    - Correct every failed-audit issue by removing or narrowing only the
      flagged unsupported content. Audit feedback is never a source of facts.
    - If Failed outbound-audit feedback contains
      `NARROW_TO_EVIDENCE_SCOPE`, treat it as mandatory for this rewrite:
      return only the explicit, verified scope of the relevant approved
      evidence. Do not directly answer the broader customer question; do not
      draw an exclusivity conclusion; do not render a list, progress flow,
      option, or conditional procedure; do not recommend, warn, comment, or
      ask a further question. A short plain-text scope statement is sufficient.
    - If feedback contains `REMOVE_UNSUPPORTED_CONTENT`, preserve only the
      independently supported remainder. If it contains
      `CLARIFY_FROM_ALLOWED_CONTEXT`, ask only the permitted clarification.

    Output JSON only:
    {{"elements":[{{"type":"TEXT|LIST|PROGRESS","source_task_ids":["task_1"],"context_text":"optional natural connector","answer":"grounded prose","list_title":"only for LIST","list_items":["only for LIST"],"progress_title":"only for PROGRESS","progress_steps":[{{"title":"...","detail":"..."}}],"extended_text":"optional additional grounded prose","options":[{{"label":"short action","message":"complete grounded question"}}]}}],"clarification":""}}
    """
    try:
        generated = _parse_json_robustly(call_groq_lpu(prompt))
    except Exception as error:
        print(f"🚨 [FLEXIBLE RESPONSE GENERATOR ERROR]: {error}")
        generated = {}

    response_blocks = []
    answer_parts = []
    element_sources = []
    for index, element in enumerate(generated.get("elements", []), start=1):
        if not isinstance(element, dict):
            continue
        requested_type = str(element.get("type", "TEXT")).upper()
        source_ids = [task_id for task_id in _llm_list(element, "source_task_ids") if task_id in valid_task_ids]
        source_ids = list(dict.fromkeys(source_ids))
        context_text = _llm_text(element, "context_text")
        answer = _llm_text(element, "answer")
        extended_text = _llm_text(element, "extended_text")
        options = _normalize_ui_options(element.get("options", []))
        list_items = [item.strip() for item in _llm_list(element, "list_items") if isinstance(item, str) and item.strip()][:8]
        progress_steps = [
            {"title": _llm_text(step, "title"), "detail": _llm_text(step, "detail")}
            for step in _llm_list(element, "progress_steps") if isinstance(step, dict)
            and _llm_text(step, "title") and _llm_text(step, "detail")
        ]
        block_type = "text"
        if requested_type == "PROGRESS" and len(progress_steps) >= 2:
            block_type = "stepper"
        elif requested_type == "LIST" and list_items:
            block_type = "checklist"
        elif not any([context_text, answer, extended_text, list_items, progress_steps]):
            continue

        if block_type == "stepper":
            lead_text = "\n\n".join(part for part in [context_text, answer] if part)
            items = [
                {"number": position, "label": step["title"], "detail": step["detail"], "status": "current" if position == 1 else "upcoming"}
                for position, step in enumerate(progress_steps, start=1)
            ]
            rendered_text = "\n\n".join(
                part for part in [lead_text, "\n\n".join(step["detail"] for step in progress_steps), extended_text] if part
            )
            block = {
                "blockId": f"element_{index}", "sourceTaskIds": source_ids,
                "type": "stepper", "contextText": lead_text, "text": "", "items": items,
                "options": options, "progressTitle": _llm_text(element, "progress_title"),
                "stepDetails": [], "currentStep": 1, "totalSteps": len(progress_steps),
                "listTitle": "", "listIntro": "", "extendedText": extended_text,
            }
        elif block_type == "checklist":
            list_intro = "\n\n".join(part for part in [context_text, answer] if part)
            rendered_text = "\n".join([part for part in [list_intro, *list_items, extended_text] if part])
            block = {
                "blockId": f"element_{index}", "sourceTaskIds": source_ids,
                "type": "checklist", "contextText": "", "text": "", "items": list_items,
                "options": options, "progressTitle": "", "stepDetails": [], "currentStep": None,
                "totalSteps": None, "listTitle": _llm_text(element, "list_title"),
                "listIntro": list_intro, "extendedText": extended_text,
            }
        else:
            rendered_text = "\n\n".join(part for part in [context_text, answer, extended_text] if part)
            block = {
                "blockId": f"element_{index}", "sourceTaskIds": source_ids,
                "type": "text", "contextText": context_text, "text": "\n\n".join(part for part in [answer, extended_text] if part),
                "items": [], "options": options, "progressTitle": "", "stepDetails": [],
                "currentStep": None, "totalSteps": None, "listTitle": "", "listIntro": "", "extendedText": "",
            }
        response_blocks.append(block)
        answer_parts.append(rendered_text)
        element_sources.append(source_ids)

    # The model can regenerate one flagged task's element during a redraft and
    # simply forget to re-emit an already-correct task's element alongside it.
    # Since nothing is left to flag, outbound audits pass on the truncated
    # draft and the omission would otherwise reach the customer silently.
    covered_task_ids = {task_id for ids in element_sources for task_id in ids}
    for entry in evidence_by_task:
        task_id = entry.get("task_id")
        if not task_id or task_id in covered_task_ids:
            continue
        component = task_metadata.get(task_id, "").strip()
        fallback_text = (
            f"Sorry, I don't have a complete answer for the part about {component} right now."
            if component else
            "Sorry, I don't have a complete answer for one part of your request right now."
        )
        print(f"   ⚠️ [TASK OMITTED IN DRAFT]: {task_id} missing from generated elements — inserting fallback")
        log_event("response.flexible.task_omitted", task_id=task_id, component=component)
        response_blocks.append({
            "blockId": f"element_fallback_{task_id}", "sourceTaskIds": [task_id],
            "type": "text", "contextText": "", "text": fallback_text,
            "items": [], "options": [], "progressTitle": "", "stepDetails": [],
            "currentStep": None, "totalSteps": None, "listTitle": "", "listIntro": "", "extendedText": "",
        })
        answer_parts.append(fallback_text)
        element_sources.append([task_id])

    # No equivalent fallback is needed for a pending service request: unlike
    # a RAG task's answer, node_07 attaches every registered form's button
    # unconditionally and independently of this draft, and each button is
    # now self-labeled with the action it performs ("Change Address",
    # "Replace Card", ...). A missing or imperfect generic framing sentence
    # here is a minor style miss, not a lost capability -- the customer
    # still sees a clearly-named, actionable button regardless. This is the
    # deliberate trade this design made: no LLM-side coverage tracking for
    # service requests at all, since every prior attempt at it (citing a
    # special_id for detection) became its own source of bugs.

    clarification = _llm_text(generated, "clarification")
    # A decline is not a clarification request. Preserve its deterministic
    # safety fallback without forcing a generic question for an exhausted
    # retrieval or an intent-clarification input.
    if decline_context and not clarification:
        clarification = _append_decline_suffix("", state.get("decline_tasks", [])).strip()
    if clarification:
        response_blocks.append({"blockId": "clarification", "sourceTaskIds": [], "type": "clarification", "text": clarification})

    answer_text = "\n\n".join(part for part in answer_parts if part)
    draft = "\n\n".join(part for part in [answer_text, clarification] if part) or "Sorry, I don't have an answer for that right now."
    print(f"   💬 [FLEXIBLE RESPONSE GENERATED]: {len(response_blocks)} element(s)")
    log_event("response.flexible.generated", element_types=[block.get("type") for block in response_blocks], element_source_task_ids=element_sources)
    return {
        "final_draft": draft,
        "final_answer": answer_text,
        "final_clarification": clarification,
        "active_plan": {},
        "completed_steps": [],
        "ui_action_payload": {"uiType": "blocks", "responseBlocks": response_blocks, "options": []},
        "pending_advance": False,
    }


def _latest_assistant_message(state: AgentState) -> str:
    """Return one trusted prior assistant turn for conversational tone only."""
    for message in reversed(state.get("chat_history", [])):
        if isinstance(message, AIMessage) and str(message.content).strip():
            return str(message.content).strip()
    return ""


def _generate_conversational_response(state: AgentState) -> dict:
    """Handle a non-financial turn without loading the FAQ-answer prompt.

    This path intentionally generates no banking facts, procedures, or UI. The
    previous assistant turn is supplied only to recognise conversational tone;
    it is never an evidence source.
    """
    user_message = str(state.get("user_input", "")).strip()
    prior_assistant_message = _latest_assistant_message(state)
    user_profile = state.get("user_profile", {})
    prompt = f"""
    You are Horizon, a concise banking-assistant conversational partner.

    STYLE & PERSONALIZATION INSTRUCTIONS:
    - Natively generate response in: {user_profile.get("language") or "English"}
    - Tone: {user_profile.get("tone", "professional")}
    - Emojis: {user_profile.get("emoji", "enabled")}

    Latest customer message: {json.dumps(user_message, ensure_ascii=False)}
    Previous assistant message for conversational tone only (not factual evidence):
    {json.dumps(prior_assistant_message, ensure_ascii=False)}

    The resolved customer request did not enter the FAQ retrieval flow. Return
    exactly one short, natural customer-facing sentence, with no UI options.
    This is a social, conversational response—not an information response.

    Choose the response that fits the latest customer message:
    - Treat it as ordinary conversation (for example a greeting,
      acknowledgement, thanks, reaction, or unrelated request) and reply
      politely and naturally.
    - Do not ask a follow-up question, offer to explore something, or suggest
      an alternative. Do not imply that any product, feature, option, design,
      process, service, or action exists or is available.
    - A brief, generic statement that Horizon can help with Horizon Bank
      questions is allowed, but do not provide, promise, or invite any
      material information.

    Never state, repeat, infer, or recommend a banking fact, procedure,
    product, service capability, support channel, or next action. Do not refer
    to the previous message, internal systems, FAQ documents, or this prompt.

    Output JSON only: {{"text": "one short natural sentence"}}
    """
    try:
        generated = _parse_json_robustly(call_groq_lpu(prompt))
        text = _llm_text(generated, "text")
    except Exception as error:
        print(f"🚨 [CONVERSATIONAL RESPONSE ERROR]: {error}")
        text = get_message(state.get("user_profile", {}).get("language", "English"), "non_financial_fallback")

    if not text:
        text = get_message(state.get("user_profile", {}).get("language", "English"), "non_financial_fallback")
    print(f"   💬 [CONVERSATIONAL RESPONSE]: {text}")
    return {
        "final_draft": text,
        "final_answer": text,
        "final_clarification": "",
        "active_plan": {},
        "completed_steps": [],
        "ui_action_payload": {"uiType": "none", "options": []},
        "pending_advance": False,
    }


def node_12_non_financial_response(state: AgentState):
    """Return a natural, non-factual reply for a non-financial turn.

    This is intentionally distinct from Node 10: a compliant acknowledgement,
    greeting, reaction, or unrelated request is not a security rejection.
    """
    print("🟢 [NODE 12] Executing Non-Financial Conversational Response")
    if int(state.get("safety_retry_count", 0)) >= 3:
        fallback = get_message(state.get("user_profile", {}).get("language", "English"), "non_financial_retry")
        print("   🛟 [NON-FINANCIAL FALLBACK]: Returning deterministic conversational reply")
        log_event("non_financial.response.fallback", retry_count=state.get("safety_retry_count", 0))
        return {
            "final_draft": fallback,
            "final_answer": fallback,
            "final_clarification": "",
            "ui_action_payload": {"uiType": "none", "options": []},
            "pending_advance": False,
        }
    return _generate_conversational_response(state)

def node_04b_response_generator(state: AgentState):
    print("🟢 [NODE 04b] Executing Response Generator")
    # Node 04b owns all customer-facing organisation and UI. Node 04a provides
    # global resolution status only; it does not prescribe presentation.
    if state.get("presentation_plan", {}).get("resolution_context") is not None:
        return _generate_response_blocks(state)
    # Keep the retired renderer only for graph state created before the UX
    # decoupling deployment, so an in-flight session can still complete.
    if state.get("presentation_plan", {}).get("response_blocks"):
        return _generate_task_bound_response_blocks(state)
    plan = state.get("active_plan", {})
    idx = plan.get("current_step_index", 0)
    steps = plan.get("steps", [])
    routing_status = state.get("preflight_status", "")
    presentation_type = state.get("selected_ux_journey", "TEXT")
    presentation_plan = state.get("presentation_plan", {})
    comparison_request = bool(re.search(
        r"\b(compare|comparison|same|different|difference|versus|vs\.?|better)\b",
        state.get("user_input", "").lower(),
    ))
    task_resolution_instructions = state.get("task_resolution_instructions", [])
    intent_clarification_tasks = state.get("intent_clarification_tasks", [])
    primary_task_id = presentation_plan.get("primary_task_id", "")
    primary_list_evidence = []
    extended_evidence = []
    presentation_by_task = {
        item.get("task_id"): set(item.get("evidence_parent_ids", []))
        for item in presentation_plan.get("presentation_tasks", [])
    }
    hydrated_by_task = {
        entry.get("task_id"): entry.get("approved_parents", [])
        for entry in _hydrate_response_evidence(state)
    }
    selected_approved_knowledge = []
    for task_id, parents in hydrated_by_task.items():
        selected_ids = presentation_by_task.get(task_id, set())
        for parent in parents:
            if parent.get("parent_id") not in selected_ids:
                continue
            block = f"[Task {task_id} | Parent {parent.get('parent_id')}]\n{parent.get('content', '')}"
            selected_approved_knowledge.append(block)
            if presentation_type == "LIST" and task_id == primary_task_id:
                primary_list_evidence.append(block)
            elif task_id != primary_task_id:
                extended_evidence.append(block)
    primary_list_knowledge = "\n\n---\n\n".join(primary_list_evidence)
    extended_knowledge = "\n\n---\n\n".join(extended_evidence)
    selected_approved_knowledge_text = "\n\n---\n\n".join(selected_approved_knowledge)
    has_terminal_retrieval_feedback = bool(task_resolution_instructions)
    has_intent_clarification = bool(intent_clarification_tasks)
    has_any_clarification = has_terminal_retrieval_feedback or has_intent_clarification
    terminal_feedback_context = [
        {
            "task_id": instruction.get("task_id"),
            "resolved_subquery": instruction.get("resolved_subquery", ""),
            "resolution_status": instruction.get("resolution_status", "CLARIFICATION_REQUIRED"),
        }
        for instruction in task_resolution_instructions
    ]
    intent_clarification_context = [
        {
            "clarification_id": task.get("clarification_id", ""),
            "unresolved_request": task.get("unresolved_request", ""),
            "reason": task.get("reason", ""),
        }
        for task in intent_clarification_tasks
    ]
    decline_tasks = state.get("decline_tasks", [])
    decline_context = [
        {"subquery": task.get("subquery", ""), "category": task.get("reason_category", "")}
        for task in decline_tasks
        if task.get("subquery", "")
    ]
    has_decline = bool(decline_context)
    completed_steps = state.get("completed_steps", [])
    is_conclusion = bool(steps and idx >= len(steps))
    is_progress_mode = presentation_type == "PROGRESS" and len(steps) > 1 and not is_conclusion

    if (
        routing_status in ["intent_clarification_needed", "CLARIFICATION"]
        and not has_any_clarification
        and not has_decline
    ):
        return _generate_conversational_response(state)
    
    if has_any_clarification or has_decline:
        current_step = "Answer supported tasks using selected approved FAQ evidence. For every unresolved component, briefly explain what needs clarification and ask a concise, general clarification question. For every declined component, briefly and politely explain you are unable to help with that specific request, using only its Decline Input description; never ask a clarification question about a declined component and never provide the declined advice, action, or information itself."
    elif not steps:
        if presentation_type == "ESCALATION":
            current_step = "State that a human agent can assist with the request."
        else:
            current_step = "Provide a complete, direct answer using only the selected approved FAQ evidence. Group distinct task topics under their supplied section titles."
    elif is_conclusion:
        current_step = "State that this guided FAQ is complete. Offer at most two general next actions."
    else:
        current_step = steps[idx]
        
    print(f"   📍 [CURRENT TARGET STEP]: {current_step} (Index {idx})")
    
    feedback = state.get("draft_feedback_history", [])
    feedback_str = f"CRITICAL CORRECTION NEEDED: {feedback[-1]}" if feedback else ""

    prompt = f"""
    ROLE ALIGNMENT:
    You are Horizon, a Read-Only FAQ Navigation Bot. You exist SOLELY to read static FAQ documents.
    
    Current Plan: {steps}
    Current Step to Execute: "{current_step}"
    Selected Presentation Type: {presentation_type}
    Comparison Request: {comparison_request}
    Presentation Plan: {presentation_plan}
    Selected Approved Knowledge: "{selected_approved_knowledge_text}"
    Primary LIST Evidence (use ONLY this evidence for list_items): "{primary_list_knowledge}"
    Additional Selected Evidence (available for extended_text, never for primary list_items): "{extended_knowledge}"
    Intent Clarification Tasks (user-derived internal guidance only; do not quote reason text): {json.dumps(intent_clarification_context, ensure_ascii=False)}
    Terminal Unresolved Task Markers (topic/status only; never evidence): {json.dumps(terminal_feedback_context, ensure_ascii=False)}
    Decline Inputs (the ONLY source for a declined-topic explanation): {json.dumps(decline_context, ensure_ascii=False)}
    Failed outbound-audit feedback (internal constraints only; never evidence):
    {feedback_str}

    Rules for Text Generation:
    - Conversation history, rolling memory, last-session memory, and user
      baseline memory are intentionally unavailable in this node. Do not
      reconstruct, repeat, or infer facts from an earlier message or memory.
    - Answer every independently answerable component of the customer's latest request. The selected UI visualizes the main mental model; it does not limit the rest of the response.
    - Selected Approved Knowledge may contain information that is only partially related to a customer's resolved subquery. Use only the part that directly answers or materially helps answer that subquery. Do not include related background information merely because it appears in approved knowledge. If the evidence answers only part of the subquery, state that supported part clearly without implying the remaining part is confirmed.
    - `extended_text` is optional, natural conversational continuation shown below a visual payload. Use it for relevant approved information that does not belong inside the selected UI, including other answerable components of a multi-part request, useful qualifications, or context. Do not repeat the UI payload or label this text as "supporting" or as a separate internal section.
    - For PROGRESS with multiple steps, write one concise, grounded detail for EVERY item in Current Plan, in the same order. A detail must provide the concrete instruction behind its short step title; do not merely repeat or lightly paraphrase the title. These details will appear in an expandable progress payload, so each one must explain only its matching step and may not rely on an earlier or later detail being visible. Put approved information that is outside this single process in `extended_text`.
    - For LIST, provide 3-8 concise, independent `list_items` derived strictly from Primary LIST Evidence only. Each item must belong to the primary task named by the list title. Do not use a list for ordered instructions. Put other relevant approved material in `extended_text`; never mix it into `list_items`. Supply a brief `answer` introduction only when it improves comprehension.
    - For TEXT, group answers by presentation-plan section title when more than one task is present. Use `extended_text` only when a separate natural continuation improves readability.
    - If Comparison Request is true, begin with a direct, concise comparison conclusion that answers whether the products are the same or different using only the approved evidence. Then explain the relevant similarities and differences under clearly named product headings. Do not merely place two unrelated product summaries next to each other. Put the complete comparison in `answer`.
    - Address every Intent Clarification Task, Terminal Unresolved Task Marker, and Decline Input naturally when it remains relevant. Never expose internal audit feedback or silently turn an unresolved topic into a different product or request.
    - For every Intent Clarification Task, state what exactly needs clarification by explicitly naming its unresolved_request. Never replace it with an ambiguous reference such as "that", "it", "the request", or "the remaining part". Do not guess a product, policy, or banking fact beyond the customer's wording.
    - For EVERY entry in Decline Inputs, add one brief, polite sentence to the clarification naming only that entry's subquery — never omit one, never invent a reason beyond its category. For category "non_compliant", state plainly that you cannot help with that specific request because of banking compliance requirements; never explain the regulation, suggest an advisor, or provide the advice or action being declined. For category "non_financial", state plainly that the topic is outside what a banking assistant can help with. Never ask a clarification question about a declined entry.
    - For a terminal unresolved task marker, provide only a brief, neutral availability notice naming its resolved subquery. Do not suggest potentially related topics, products, procedures, or specific details unless they are supported by Selected Approved Knowledge.
    - Treat all clarification guidance as uncertain internal guidance: do not quote internal reason text, turn it into a customer-facing product label, or treat it as a fact.
    - Do not offer UI options when any unresolved task requires clarification. Answer UI options are allowed only when every component is supported.
    - If you provide `options`, each must be an object with a concise visible `label` (2-6 words) and a complete, unambiguous `message` that will be sent when clicked. The message must be grounded in the same approved knowledge as the answer.
    - Never expose internal metadata to the customer, including task IDs, parent IDs such as `faq_abc12345`, retrieval labels, model names, audit results, or internal feedback. Parent-ID headers are evidence metadata only, not citations.
    - SOURCE-NEUTRAL CONVERSATION: FAQ records, policies, documents, database entries, search results, and retrieved knowledge are internal evidence only. Never mention or imply those internal sources in customer-facing text. Do not say "according to an FAQ", "another FAQ", "the FAQ says", "the document states", "the knowledge base", "the retrieved information", "the policy database", or similar wording.
    - Present approved facts directly and naturally as Horizon's banking guidance. When several records support one answer, synthesize them into one coherent answer instead of describing separate sources. `extended_text` is normal conversational continuation, not a source note: it may add relevant approved context but must not describe where the information came from.
    - Discuss provenance only when the customer explicitly asks about sources. In that case, refer only at a high level to "Horizon Bank's published information" and never reveal internal retrieval mechanics, FAQ records, parent IDs, model names, or database details.
    - ZERO HALLUCINATION: Rely EXCLUSIVELY on Selected Approved Knowledge for factual answer content. Intent clarification may rely only on the user's stated unresolved_request; terminal notices may rely only on their named resolved subquery and must remain non-factual.
    - If failed outbound feedback contains Grounding violations, use
      unsupported_content only to identify text that must be removed, and use
      evidence_gap only to understand what the approved evidence does not
      establish. Neither field permits a new customer-facing fact, support
      channel, escalation path, service capability, or next action.
      When audit_layer is "evidence", remove or replace the named factual
      claim only with directly approved content. When audit_layer is
      "bridge", retain a natural answer but narrow the named conclusion to
      the logical implication actually supported by approved evidence; never
      expose those internal audit layers to the customer.
    - If Selected Approved Knowledge is empty, do not state any specific
      product, service, policy, feature, fee, account, or historical fact.
      Produce only a concise, neutral clarification or refusal based on the
      current user wording, with empty extended_text and options.
    - If quoting exact numerical rates or fees, you MUST ONLY use the exact {{placeholders}} provided in the 'Retrieved Knowledge'. Do NOT invent, alter, or hallucinate placeholders.
    
    Output JSON ONLY:
    {{"answer": "Grounded answer text only, or an empty string", "step_details": ["Grounded detail for Current Plan step 1"], "list_items": ["Grounded independent list item"], "extended_text": "Natural grounded continuation outside the main UI payload, or empty", "clarification": "Natural clarification question only, or an empty string", "options": [{{"label": "short action", "message": "complete grounded next-action question"}}], "advance_step": true}}
    """
    
    try:
        res = _parse_json_robustly(call_groq_lpu(prompt))
        answer = _llm_text(res, "answer", "response")
        clarification = _llm_text(res, "clarification")
        generated_step_details = res.get("step_details", []) or []
        generated_list_items = res.get("list_items", []) or []
        # `supporting_text` is accepted only for responses generated before
        # the extended-text contract rolled out. New responses use the
        # presentation-neutral `extended_text` field.
        extended_text = _llm_text(res, "extended_text", "supporting_text")
        if presentation_type == "TEXT" and extended_text:
            # TEXT has no separate visual payload, so its continuation belongs
            # directly in the normal answer bubble.
            answer = "\n\n".join(part for part in [answer, extended_text] if part)
            extended_text = ""
        step_details = []
        list_items = []
        list_intro = answer
        if is_progress_mode:
            existing_step_details = plan.get("step_details", [])
            # Reuse saved details only for a later customer interaction. A
            # safety retry must regenerate them; otherwise the semantic gate
            # keeps reviewing the first rejected details despite a corrected
            # model response.
            if not feedback and isinstance(existing_step_details, list) and len(existing_step_details) == len(steps) and all(isinstance(detail, str) and detail.strip() for detail in existing_step_details):
                step_details = [detail.strip() for detail in existing_step_details]
            elif isinstance(generated_step_details, list) and len(generated_step_details) == len(steps) and all(isinstance(detail, str) and detail.strip() for detail in generated_step_details):
                step_details = [detail.strip() for detail in generated_step_details]
            else:
                # The planner's grounded labels are a safe deterministic
                # fallback if the generator returns a malformed detail array.
                step_details = list(steps)
            plan = {**plan, "step_details": step_details}
            # The complete set is also retained as the answer so semantic
            # review, chat history, and memory receive the same grounded text
            # that the expandable UI presents.
            answer = "\n\n".join(step_details)
        elif presentation_type == "LIST" and isinstance(generated_list_items, list):
            list_items = [item.strip() for item in generated_list_items if isinstance(item, str) and item.strip()][:8]
        if (has_any_clarification or has_decline) and not clarification:
            unresolved_terms = [task.get("unresolved_request", "").strip() for task in intent_clarification_tasks if task.get("unresolved_request", "").strip()]
            named_terms = ", ".join(unresolved_terms)
            fallback_parts = []
            if named_terms:
                fallback_parts.append(f"Sorry, I need a little more detail about {named_terms}. Could you clarify what information you are looking for?")
            elif has_any_clarification:
                fallback_parts.append("Sorry, I couldn't find relevant FAQ details to answer that. Could you clarify what information you are looking for?")
            if has_decline:
                fallback_parts.append(_append_decline_suffix("", decline_tasks).strip())
            clarification = " ".join(fallback_parts)
        if has_terminal_retrieval_feedback and not selected_approved_knowledge:
            answer = ""
        if list_items:
            answer_for_record = "\n".join(part for part in [list_intro, *list_items, extended_text] if part)
        elif is_progress_mode:
            answer_for_record = "\n\n".join(part for part in [*step_details, extended_text] if part)
        else:
            answer_for_record = answer
        draft = "\n\n".join(part for part in [answer_for_record, clarification] if part)
        if not draft:
            draft = "Sorry, I don't have an answer for that right now."
            answer = draft
            answer_for_record = draft
        print(f"   💬 [DRAFT RESPONSE]: {draft}")
        
        # V4.1 FIX: Do NOT advance the step here! The text might fail the safety gates.
        # Instead, set a volatile flag indicating this step *should* advance if it passes.
        pending_advance = res.get("advance_step") and steps and idx < len(steps) and routing_status != "intent_clarification_needed"
            
        options = _normalize_ui_options(res.get("options", []))
        if not options:
            options = _normalize_ui_options(presentation_plan.get("options", []))
        ui_items = []
        use_progress_mode = is_progress_mode
        use_list_mode = presentation_type == "LIST" and len(list_items) >= 3
        ui_type = "stepper" if use_progress_mode else "none"

        if is_conclusion:
            ui_type = "chips"
            if not options or len(options) < 2: options = ["That's all, thank you.", "I have another question."]
            plan = {}
            completed_steps = []
            print(f"   🧹 [STATE CLEANUP]: Wiped active plan and completed steps.")
        elif has_any_clarification or has_decline:
            ui_type = "none"
            options = []
        elif presentation_type == "ESCALATION":
            ui_type = "chips" if presentation_plan.get("options") else "none"
            options = presentation_plan.get("options", [])
        elif presentation_type == "TEXT" and options:
            ui_type = "chips"
        elif ui_type == "none" and presentation_type != "LIST":
            options = []

        # Only the auditor-approved multi-step path receives the deterministic
        # progress payload; simple/direct generation remains standard text.
        if use_progress_mode:
            stepper_payload = _build_stepper_payload(steps, idx)
            ui_type = stepper_payload["uiType"]
            ui_items = stepper_payload["items"]
            current_step_number = stepper_payload["currentStep"]
            total_steps = stepper_payload["totalSteps"]
            print(f"   🧭 [STEPPER PAYLOAD]: Step {current_step_number}/{total_steps}")
        elif use_list_mode:
            ui_type = "checklist"
            ui_items = list_items
            print(f"   📋 [LIST PAYLOAD]: {len(list_items)} items")
        else:
            print(
                f"   📝 [PRESENTATION RESPONSE MODE]: Type={presentation_type} | "
                f"Active progress plan={use_progress_mode}"
            )
        
        return {
            "final_draft": draft,
            "final_answer": answer_for_record,
            "final_clarification": clarification,
            "active_plan": plan,
            "completed_steps": completed_steps,
            "ui_action_payload": {
                "uiType": ui_type,
                "options": options,
                "items": ui_items,
                "progressTitle": next(
                    (
                        item.get("section_title", "")
                        for item in presentation_plan.get("presentation_tasks", [])
                        if item.get("task_id") == presentation_plan.get("primary_task_id")
                    ),
                    "Horizon Bank guided process",
                ) if ui_type == "stepper" else "",
                "stepDetail": "",
                "stepDetails": step_details if ui_type == "stepper" else [],
                # Clarification is intentionally kept outside the progress
                # payload so it cannot look like part of the procedure.
                "clarificationText": clarification if ui_type == "stepper" else "",
                "listTitle": presentation_plan.get("list_title", "Key information") if ui_type == "checklist" else "",
                "listIntro": list_intro if ui_type == "checklist" else "",
                "extendedText": extended_text if ui_type in {"stepper", "checklist"} else "",
                "listClarificationText": clarification if ui_type == "checklist" else "",
                "currentStep": idx + 1 if ui_type == "stepper" else None,
                "totalSteps": len(steps) if ui_type == "stepper" else None,
            },
            "pending_advance": pending_advance,
        }
    except Exception as e:
        return {"final_draft": "I encountered an error. Please try again.", "preflight_status": "fail"}

# --- PHASE 4: OUTPUT GUARDRAILS & ESCALATION HANDLER ---

def node_05_outbound_deterministic_gate(state: AgentState):
    print("🟢 [NODE 05] Executing Outbound Deterministic Gate")
    draft = state.get("final_draft", "")
    payload = state.get("ui_action_payload", {})
    options = payload.get("options", [])
    step_details = payload.get("stepDetails", [])
    list_items = payload.get("items", []) if payload.get("uiType") == "checklist" else []
    list_title = payload.get("listTitle", "")
    list_intro = payload.get("listIntro", "")
    extended_text = payload.get("extendedText", "")
    list_clarification = payload.get("listClarificationText", "")
    response_blocks = payload.get("responseBlocks", [])
    
    # 1. Combine draft text AND UI options to sweep globally
    combined_text = " ".join([draft, *_ui_option_texts(options), *step_details, *list_items, list_title, list_intro, extended_text, list_clarification, json.dumps(response_blocks, ensure_ascii=False)])
    placeholders_in_draft = re.findall(r"\{\{(.*?)\}\}", combined_text)
    # Parent IDs identify internal FAQ records. They are allowed inside a
    # placeholder until Node 07 substitutes its exact value, but must never
    # appear as customer-facing citations or prose.
    exposed_parent_ids = re.findall(r"(?<!\{)faq_[0-9a-f]{8}(?![_}])", combined_text, flags=re.IGNORECASE)
    
    illegal_detected = []
    if placeholders_in_draft:
        valid_map = fetch_placeholders_from_db(placeholders_in_draft)
        for p in placeholders_in_draft:
            if p not in valid_map:
                illegal_detected.append(p)
    
    # 2. Trigger Safety Loop if any placeholder is invalid
    if illegal_detected or exposed_parent_ids or "ILLEGAL_WORD" in combined_text:
        print(f"   🚫 [HARD GATE VETO]: Invalid placeholders: {illegal_detected} | Exposed parent IDs: {exposed_parent_ids}")
        count = state.get("safety_retry_count", 0) + 1
        history = state.get("draft_feedback_history", [])
        
        history.append(f"Draft or UI payload contained invalid placeholders: {illegal_detected} or exposed internal parent IDs: {exposed_parent_ids}. Never display FAQ parent IDs; use the supported facts without citations.")
        
        return {
            "safety_retry_count": count, 
            "draft_feedback_history": history, 
            "preflight_status": "fail",
            "pending_advance": False # Enforce Atomic State
        }
        
    print("   ✅ [HARD GATE PASS]: All placeholders in text and UI validated against DB.")
    return {"preflight_status": "pass"}


def _outbound_response_content(state: AgentState) -> tuple[str, str, str]:
    """Return answer, clarification, and the full customer-facing payload."""
    draft = state.get("final_draft", "")
    answer_text = state.get("final_answer", draft)
    clarification_text = state.get("final_clarification", "")
    payload = state.get("ui_action_payload", {})
    visible_response_blocks = [
        {
            key: value
            for key, value in block.items()
            if key not in {"blockId", "taskId", "sourceTaskIds"}
        }
        for block in payload.get("responseBlocks", [])
        if isinstance(block, dict)
    ]
    rendered_payload = {
        "uiType": payload.get("uiType", "none"),
        "options": payload.get("options", []),
        "stepDetails": payload.get("stepDetails", []),
        "listTitle": payload.get("listTitle", ""),
        "listIntro": payload.get("listIntro", ""),
        "items": payload.get("items", []) if payload.get("uiType") == "checklist" else [],
        "extendedText": payload.get("extendedText", ""),
        "listClarificationText": payload.get("listClarificationText", ""),
        # Block and task IDs are transport metadata, not customer-facing text.
        "responseBlocks": visible_response_blocks,
    }
    clarification = "\n".join(
        part for part in [clarification_text, rendered_payload["listClarificationText"]] if part
    )
    return answer_text, clarification, json.dumps(rendered_payload, ensure_ascii=False)


def _is_initial_greeting(state: AgentState) -> bool:
    """Identify greeting events without asking an LLM to infer the event type."""
    return (
        state.get("user_input") in {"__INIT__", "**INIT**"}
        or state.get("preflight_status") == "initial_greeting"
    )


def _normal_outbound_grounding_context(state: AgentState) -> dict:
    """Return only evidence sources permitted for a non-greeting audit.

    Conversation, rolling memory, last-session memory, and baseline memory are
    deliberately excluded. They may assist upstream interpretation but are
    never factual grounding sources for a normal customer response.
    """
    tasks_by_id = {task.get("task_id"): task for task in state.get("search_tasks", [])}
    approved_by_task = []
    for evidence in _hydrate_response_evidence(state):
        task_id = evidence.get("task_id")
        task = tasks_by_id.get(task_id, {})
        approved_by_task.append({
            "task_id": task_id,
            "resolved_subquery": task.get("customer_component", ""),
            "approved_parents": evidence.get("approved_parents", []),
        })

    response_block_map = []
    for block in state.get("ui_action_payload", {}).get("responseBlocks", []):
        if not isinstance(block, dict):
            continue
        source_task_ids = block.get("sourceTaskIds")
        if not isinstance(source_task_ids, list):
            source_task_ids = [block.get("taskId")] if block.get("taskId") else []
        source_task_ids = [task_id for task_id in source_task_ids if task_id in tasks_by_id]
        if source_task_ids:
            response_block_map.append({
                "block_id": block.get("blockId"),
                "source_task_ids": list(dict.fromkeys(source_task_ids)),
            })

    terminal_context = []
    for instruction in state.get("task_resolution_instructions", []):
        task_id = instruction.get("task_id")
        terminal_context.append({
            "task_id": task_id,
            "resolved_subquery": instruction.get("resolved_subquery", "") or tasks_by_id.get(task_id, {}).get("customer_component", ""),
            "resolution_status": instruction.get("resolution_status", "CLARIFICATION_REQUIRED"),
        })

    intent_context = [
        {"unresolved_request": task.get("unresolved_request", "")}
        for task in state.get("intent_clarification_tasks", [])
    ]
    decline_context = [
        {"subquery": task.get("subquery", ""), "category": task.get("reason_category", "")}
        for task in state.get("decline_tasks", [])
        if task.get("subquery", "")
    ]
    return {
        "approved_by_task": approved_by_task,
        "response_block_map": response_block_map,
        "terminal_clarifications": terminal_context,
        "intent_clarifications": intent_context,
        "decline_inputs": decline_context,
        "pending_service_request_count": len(_pending_service_requests(state)),
        "unavailable_service_requests": _unavailable_service_requests(state),
    }


def _normalize_grounding_violations(raw_violations: Any) -> list[dict]:
    """Keep grounding-audit feedback structured and explicitly non-evidential.

    The response generator consumes these records only as removal constraints;
    none of their text is permitted to become a customer-facing fact or action.
    """
    if not isinstance(raw_violations, list):
        return []

    allowed_types = {
        "unsupported_claim",
        "unsupported_factual_claim",
        "unsupported_bridge_inference",
        "unsupported_instruction",
        "wrong_task_evidence",
        "unsupported_clarification_suggestion",
        "missing_decline_reference",
        "task_not_answered",
        "other",
    }
    normalized = []
    for item in raw_violations:
        if not isinstance(item, dict):
            continue
        unsupported_content = item.get("unsupported_content", [])
        if isinstance(unsupported_content, str):
            unsupported_content = [unsupported_content]
        if not isinstance(unsupported_content, list):
            unsupported_content = []
        violation_type = str(item.get("violation_type", "other")).strip()
        normalized.append({
            "task_id": str(item.get("task_id", "")).strip(),
            "scope": str(item.get("scope", "answer")).strip() or "answer",
            "audit_layer": (
                str(item.get("audit_layer", "other")).strip()
                if str(item.get("audit_layer", "other")).strip() in {"evidence", "bridge", "other"}
                else "other"
            ),
            "violation_type": violation_type if violation_type in allowed_types else "other",
            "unsupported_content": [str(value).strip() for value in unsupported_content if str(value).strip()],
            "evidence_gap": str(item.get("evidence_gap", "")).strip(),
        })
    return normalized


def node_06_outbound_compliance_gate(state: AgentState):
    """Groundedness v2.0 conduct axis: bank conduct + MAS compliance audit."""
    print("🟢 [NODE 06a] Executing Outbound Compliance Audit")
    answer, clarification, rendered_payload = _outbound_response_content(state)
    content_block = f"""
    Customer-Facing Answer:
    "{answer}"

    Customer-Facing Clarification:
    "{clarification}"

    Complete Customer-Facing UI Payload:
    "{rendered_payload}"
    """
    route_specific_rules = """
    Determine whether any customer-facing text, UI item, option, step, list
    entry, or extended text provides unauthorized personalized financial
    advice, subjective product recommendation, return forecast, transaction
    execution, deceptive claim, or assistance to bypass security, KYC,
    AML/CFT, or other controls. Neutral factual information, safe apologies,
    and neutral clarification requests are compliant. A polite decline of a
    non-compliant request is compliant ONLY if it declines without itself
    providing the disallowed advice, action, or information, and does not
    misstate what Horizon can or cannot do beyond the Bank Conduct Rules and
    Regulatory Context above. Every visible field in the payload is in scope.

    Assess the Customer-Facing Answer (including its UI payload) and the
    Customer-Facing Clarification SEPARATELY, since a failure confined to
    one can be corrected without discarding the other.
    """
    result = check_conduct_compliance(
        content_block,
        route_specific_rules=route_specific_rules,
        extra_output_fields=', "answer_compliant": true/false, "clarification_compliant": true/false, "rewrite_instruction": "..."',
    )
    answer_compliant = bool(result.get("answer_compliant", False))
    clarification_compliant = bool(result.get("clarification_compliant", False))
    passed = bool(result.get("conduct_compliant", False)) and answer_compliant and clarification_compliant
    audit = {
        "passed": passed,
        "answer_compliant": answer_compliant,
        "clarification_compliant": clarification_compliant,
        "reason": result.get("reason", "Compliance audit failed."),
        "rewrite_instruction": result.get("rewrite_instruction", "Rewrite the response to comply with banking regulations."),
    }
    log_event("outbound.compliance.completed", passed=passed, reason=audit["reason"])
    return {"outbound_compliance_result": audit}


def _legacy_node_06_outbound_groundedness_gate(state: AgentState):
    """Compatibility alias for in-flight callers of the retired audit path."""
    return node_06_outbound_groundedness_gate(state)


def node_06_outbound_groundedness_gate(state: AgentState):
    """Groundedness v2.0 evidence axis: audit only the evidence permitted for this response event."""
    print("🟢 [NODE 06b] Executing Outbound Groundedness Audit")
    answer, clarification, rendered_payload = _outbound_response_content(state)
    is_initial_greeting = _is_initial_greeting(state)
    is_non_financial_response = state.get("triage_status") == "NON_FINANCIAL"

    audit_feedback_rules = """
    Audit-Feedback Rules:
    - grounding_violations is internal validation data, never customer-facing text.
    - Set response_recovery_strategy to one of these internal constraints:
      * REMOVE_UNSUPPORTED_CONTENT when supported response content can be
        retained after removing the exact unsupported claim or instruction.
      * NARROW_TO_EVIDENCE_SCOPE when approved evidence is relevant but
        establishes only a conditional, partial, or narrower policy/process
        than the customer's broader question. The next response must
        restate only that explicit evidence scope and must not answer the
        broader question, infer exclusivity, offer the conditional process
        as the customer's path, add steps, recommend an action, or ask a
        further question unless independently supported.
      * CLARIFY_FROM_ALLOWED_CONTEXT only when a permitted clarification
        source genuinely contains an unresolved term whose answer could
        materially change a grounded response.
      * NO_STRATEGY when no special recovery constraint is needed.
    - The strategy is never evidence, a rewrite, customer-facing wording,
      a proposed product, service, action, or remedy.
    - Report only unsupported visible content and the specific evidence that
      is missing. Do not draft a rewrite or recommend an alternative action,
      support channel, product, service, escalation path, or remedy.
    - The evidence_gap describes an absence in the permitted source. It is
      not evidence and must not be used to support a new customer claim.
    """
    recovery_strategy_field = ', "response_recovery_strategy":"NO_STRATEGY" | "REMOVE_UNSUPPORTED_CONTENT" | "NARROW_TO_EVIDENCE_SCOPE" | "CLARIFY_FROM_ALLOWED_CONTEXT"'

    if is_initial_greeting:
        greeting_memory = _build_greeting_memory_context(
            state.get("last_session_summary", state.get("working_context", "")),
            state.get("user_baseline_summary", ""),
        )
        content_block = f"""
        Customer-Facing Greeting:
        "{answer}"

        Complete Customer-Facing UI Payload:
        "{rendered_payload}"
        """
        route_specific_rules = f"""
        - Every recalled statement and suggested chip must be supported by the
          Permitted Evidence (Greeting Memory) above.
        - A generic first-time greeting with no recalled facts or chips is grounded.
        - Addressing the user by their preferred nickname, name, or speaking in a specific language/tone does not require evidence support.
        - Do not assess banking-answer factuality, compliance, helpfulness, or a normal-turn clarification.
        - Every visible field in the greeting payload is in scope.

        {audit_feedback_rules}
        """
        result = check_evidence_groundedness(
            content_block,
            evidence_bundle=greeting_memory,
            evidence_description="Greeting Memory",
            route_specific_rules=route_specific_rules,
            extra_output_fields=f', "clarification_grounded": true, "clarification_connected": true{recovery_strategy_field}',
        )
        audit_scope = "greeting"
    elif is_non_financial_response:
        content_block = f"""
        Original Customer Message (courtesy-context only; never a source for a banking fact):
        {json.dumps(state.get("user_input", ""), ensure_ascii=False)}

        Customer-Facing Answer:
        {json.dumps(answer, ensure_ascii=False)}

        Customer-Facing Clarification:
        {json.dumps(clarification, ensure_ascii=False)}

        Complete Customer-Facing UI Payload:
        {rendered_payload}
        """
        route_specific_rules = """
        This route is for politeness and natural interaction, never for
        providing information. Pass only when every visible field is a brief,
        natural, non-material acknowledgement or a generic banking-assistant
        scope statement. It may respond politely to the customer's tone,
        feeling, greeting, thanks, or unrelated remark. It must not state or
        imply any banking fact, product, feature, option, design, service,
        eligibility, policy, procedure, availability, commitment,
        recommendation, material suggestion, next action, support channel, or
        question that invites exploration of a specific offering. It must
        have no substantive UI, chips, list, steps, progress content, or
        options.

        The Original Customer Message supports only a courteous acknowledgement
        of what the customer expressed; it never supports a material claim.
        Do not assess helpfulness or whether the response answers a banking question.
        """
        result = check_evidence_groundedness(
            content_block,
            evidence_bundle="None — this route permits no factual evidence; only a courteous, non-material acknowledgement is grounded.",
            evidence_description="Non-Financial Conversation (no factual evidence permitted)",
            route_specific_rules=route_specific_rules,
            extra_output_fields=', "answer_bridge_grounded": true/false, "clarification_grounded": true/false, "clarification_connected": true/false',
        )
        audit_scope = "non_financial_response"
    else:
        grounding_context = _normal_outbound_grounding_context(state)
        content_block = f"""
        Original Customer Message (courtesy-context only; never a source for a
        banking fact, instruction, product capability, policy, or outcome):
        {json.dumps(state.get("user_input", ""), ensure_ascii=False)}

        Optional Internal Response-Element Provenance (not customer-facing):
        {json.dumps(grounding_context["response_block_map"], ensure_ascii=False)}

        Intent Clarification Inputs (the only source for intent clarification):
        {json.dumps(grounding_context["intent_clarifications"], ensure_ascii=False)}

        Terminal Unresolved Task Markers (topic/status only; never evidence):
        {json.dumps(grounding_context["terminal_clarifications"], ensure_ascii=False)}

        Decline Inputs (the only source for a declined-topic explanation):
        {json.dumps(grounding_context["decline_inputs"], ensure_ascii=False)}

        Customer-Facing Answer:
        "{answer}"

        Customer-Facing Clarification:
        "{clarification}"

        Complete Customer-Facing UI Payload:
        "{rendered_payload}"
        """
        route_specific_rules = f"""
        ANSWER AUDIT: assess the customer's natural answer as one coherent
        response. Do NOT require it to be written in separate factual and
        inferential sections. Instead, distinguish the following two internal
        checks while reading the same natural answer and answer UI.

        1. Direct Factual Grounding (Evidence Check)
        - Every explicit factual assertion, parameter, eligibility rule,
          product capability, policy condition, limit, date, amount,
          placeholder, instruction, option, progress step, list item,
          extended-text statement, and answer response-block field must be
          directly supported by the Permitted Evidence (Task-Level Approved
          Evidence) above.
        - Assess the answer and answer UI as one coherent customer response
          against the aggregate Task-Level Approved Evidence. The response does
          not need a visible section, element, or title for each task.
        - When an optional response element is mapped to source_task_ids, its
          factual content may use the union of approved evidence for those
          tasks. An unmapped element may use any Task-Level Approved Evidence.
        2. Inferential Grounding (Bridge Check)
        - Natural conclusions that connect approved facts to the resolved
          sub-query are allowed when they are a reasonable, bounded logical
          implication of the approved evidence. Do not require verbatim FAQ
          wording for such a conclusion.
        - Reject a bridge only when it adds an unstated premise, actor,
          policy, procedure, reason, certainty, exception, or consequence; or
          when the conclusion is not entailed by the approved evidence.
        - Example: evidence that explicitly sets a maximum coverage cap can
          support the bounded conclusion that coverage cannot be increased
          above that cap. It does NOT support attributing control of the cap
          to a particular bank, regulator, or process unless that attribution
          is explicit in the evidence.

        Courtesy Context Rule (applies to the answer, clarification, and UI):
        - First isolate any brief conversational courtesy acknowledgement,
          greeting, thanks, apology, or response to the customer's stated
          feeling or preference. It is grounded when it faithfully responds to
          the Original Customer Message and introduces no banking fact,
          procedure, product/service capability, policy, eligibility,
          commitment, recommendation, availability conclusion, or next action.
          Do not fail such a courtesy sentence merely because it is absent from
          Task-Level Approved Evidence.
        - Assess every remaining financial statement and UI field only against
          Task-Level Approved Evidence. The Original Customer Message can
          never support, soften, or justify a financial claim.

        Clarification Rules:
        - For an intent clarification, every named unresolved term must come
          only from Intent Clarification Inputs. Do not infer a product, policy,
          or banking fact.
        - For a terminal unresolved task, a neutral statement that available
          information does not resolve its named request is grounded. It must
          not state, suggest, or imply a banking fact, procedure, product
          alternative, policy, or next action unless supported by Task-Level
          Approved Evidence.
        - A generic apology and a general request for clarification are grounded.
        - A clarification following an answer must identify its unresolved term
          instead of using an ambiguous reference such as "that" or "it".
        - For a decline explanation, only the exact subquery named in Decline
          Inputs may be referenced; it must not assert a specific Horizon
          product, policy, fee, or commitment as the reason, and must never
          provide the declined advice, action, or information itself. Every
          entry in Decline Inputs must be explicitly named somewhere in the
          clarification, not silently omitted — treat a missing entry as
          equivalent to an unidentified reference for clarification_connected.

        {audit_feedback_rules}
        - For every material failure, identify the exact visible phrase in
          unsupported_content and set audit_layer to evidence or bridge.
        - For evidence failures, explain which direct factual support is
          missing. For bridge failures, explain the unsupported logical leap
          or added premise. If both layers fail, report separate entries.
        - If all of the answer is unsupported, say so explicitly in reason and
          include the material unsupported phrases. Do not use vague feedback
          such as "not grounded" without identifying why.
        - If the approved evidence does not answer a task, record a
          task_not_answered violation. Do not suggest how Horizon should handle
          the task or where the customer should go next.

        Source-Boundary Rules:
        - Session memory, user baseline memory, chat history, and rolling
          summaries are intentionally absent and are not grounding sources.
          The Original Customer Message is supplied only for the narrow
          courtesy-context rule above; it is never evidence for financial
          content.
        - Do not fail a response because it omits information from any available
          source. Assess whether visible claims use a permitted source only.
        - Every visible field in the payload is in scope.
        - "pending_service_request_count" is not a permitted source for any
          customer-facing statement at all -- it exists only to tell the
          response whether to avoid apology/decline framing, per the route
          rules above. Any mention of a form, service request, or link,
          in any wording, is unsupported regardless of the count's value,
          because whether a form actually appears is decided after this
          audit runs.
        - "unavailable_service_requests" entries are a permitted source only
          for a brief sentence stating that part of the request could not be
          started, using only the supplied reason. Any different explanation
          is unsupported.
        """
        result = check_evidence_groundedness(
            content_block,
            evidence_bundle={
                "approved_by_task": grounding_context["approved_by_task"],
                "pending_service_request_count": grounding_context["pending_service_request_count"],
                "unavailable_service_requests": grounding_context["unavailable_service_requests"],
            },
            evidence_description="Task-Level Approved Evidence, Pending Service Request Count, and Unavailable Service Requests — the only factual sources for answers and answer UI",
            route_specific_rules=route_specific_rules,
            extra_output_fields=f', "answer_evidence_grounded": true/false, "answer_bridge_grounded": true/false, "clarification_grounded": true/false, "clarification_connected": true/false{recovery_strategy_field}, "grounding_violations": [{{"task_id": "task_1", "scope": "answer" | "ui_payload" | "clarification", "audit_layer": "evidence" | "bridge" | "other", "violation_type": "unsupported_factual_claim" | "unsupported_bridge_inference" | "unsupported_instruction" | "wrong_task_evidence" | "unsupported_clarification_suggestion" | "missing_decline_reference" | "task_not_answered" | "other", "unsupported_content": ["verbatim customer-facing phrase"], "evidence_gap": "missing permitted evidence only; no proposed remedy"}}]',
        )
        audit_scope = "normal_response"

    answer_grounded = bool(result.get("evidence_grounded", False))
    answer_evidence_grounded = bool(result.get("answer_evidence_grounded", answer_grounded))
    answer_bridge_grounded = bool(result.get("answer_bridge_grounded", answer_grounded))
    clarification_grounded = bool(result.get("clarification_grounded", False))
    clarification_connected = bool(result.get("clarification_connected", False))
    if audit_scope == "normal_response":
        answer_grounded = answer_grounded and answer_evidence_grounded and answer_bridge_grounded
    passed = answer_grounded and clarification_grounded and clarification_connected

    grounding_violations = _normalize_grounding_violations(result.get("grounding_violations", result.get("evidence_violations", [])))
    response_recovery_strategy = str(result.get("response_recovery_strategy", "NO_STRATEGY")).upper()
    if response_recovery_strategy not in {
        "NO_STRATEGY",
        "REMOVE_UNSUPPORTED_CONTENT",
        "NARROW_TO_EVIDENCE_SCOPE",
        "CLARIFY_FROM_ALLOWED_CONTEXT",
    }:
        response_recovery_strategy = "NO_STRATEGY"

    audit = {
        "passed": passed,
        "answer_grounded": answer_grounded,
        "answer_evidence_grounded": answer_evidence_grounded,
        "answer_bridge_grounded": answer_bridge_grounded,
        "clarification_grounded": clarification_grounded,
        "clarification_connected": clarification_connected,
        "response_recovery_strategy": response_recovery_strategy,
        "reason": result.get("reason", "Groundedness audit failed."),
        "grounding_violations": grounding_violations,
    }
    log_event(
        "outbound.groundedness.completed",
        scope=audit_scope,
        passed=passed,
        reason=audit["reason"],
        answer_evidence_grounded=answer_evidence_grounded,
        answer_bridge_grounded=answer_bridge_grounded,
        grounding_violation_count=len(grounding_violations),
        response_recovery_strategy=response_recovery_strategy,
    )
    return {"outbound_groundedness_result": audit}


def node_06_outbound_audit_join(state: AgentState):
    """Join focused audits and retain the existing retry/degradation semantics."""
    print("🟢 [NODE 06] Joining Outbound Compliance & Groundedness Audits")
    compliance = state.get("outbound_compliance_result", {})
    groundedness = state.get("outbound_groundedness_result", {})
    compliance_passed = bool(compliance.get("passed", False))
    groundedness_passed = bool(groundedness.get("passed", False))
    answer_text = state.get("final_answer", state.get("final_draft", ""))
    answer_compliant = bool(compliance.get("answer_compliant", False))
    answer_grounded = bool(groundedness.get("answer_grounded", False))
    clarification_failed = (
        not groundedness.get("clarification_grounded", False)
        or not groundedness.get("clarification_connected", False)
        or not bool(compliance.get("clarification_compliant", False))
    )
    is_initial_greeting = (
        state.get("user_input") in {"__INIT__", "**INIT__"}
        or state.get("preflight_status") == "initial_greeting"
    )

    if not (compliance_passed and groundedness_passed):
        if is_initial_greeting:
            print("   🚫 [GREETING AUDIT VETO]: Falling back to generic greeting without suggested actions.")
            generic_greeting = "Hello! I am Horizon. How can I assist you today?"
            return {
                "preflight_status": "pass",
                "final_draft": generic_greeting,
                "final_answer": generic_greeting,
                "final_clarification": "",
                "ui_action_payload": {"uiType": "text", "options": []},
                "pending_advance": False,
            }
        count = state.get("safety_retry_count", 0) + 1
        if state.get("triage_status") == "NON_FINANCIAL" and count >= 3:
            fallback = get_message(state.get("user_profile", {}).get("language", "English"), "non_financial_retry")
            print("   🛟 [NON-FINANCIAL AUDIT FALLBACK]: Returning deterministic conversational reply")
            log_event(
                "non_financial.response.audit_fallback",
                retry_count=count,
                compliance_passed=compliance_passed,
                groundedness_passed=groundedness_passed,
            )
            return {
                "preflight_status": "pass",
                "final_draft": fallback,
                "final_answer": fallback,
                "final_clarification": "",
                "ui_action_payload": {"uiType": "none", "options": []},
                "pending_advance": False,
            }
        failed_feedback = []
        if not compliance_passed:
            failed_feedback.append(f"Compliance audit failed: {compliance.get('rewrite_instruction', compliance.get('reason', 'Rewrite as neutral, compliant banking information.'))}")
        if not groundedness_passed:
            violations = groundedness.get("grounding_violations", [])
            if violations:
                failed_feedback.append(
                    "Grounding violations (internal constraints only; never evidence): "
                    + json.dumps(violations, ensure_ascii=False)
                )
            else:
                # Fail closed if an auditor response is malformed: do not
                # create a natural-language rewrite suggestion that could be
                # misread as a new source of customer-facing facts.
                failed_feedback.append(
                    "Grounding violations (internal constraints only; never evidence): "
                    + json.dumps([{
                        "task_id": "",
                        "scope": "answer",
                        "violation_type": "other",
                        "unsupported_content": [],
                        "evidence_gap": "The groundedness audit did not return usable violation data; add no facts or actions beyond approved evidence.",
                    }], ensure_ascii=False)
                )
            recovery_strategy = str(groundedness.get("response_recovery_strategy", "NO_STRATEGY")).upper()
            if recovery_strategy != "NO_STRATEGY":
                failed_feedback.append(
                    "Groundedness recovery strategy (internal behavior constraint; never evidence or customer wording): "
                    + recovery_strategy
                )
        history = list(state.get("draft_feedback_history", []))
        history.extend(failed_feedback)
        unresolved_clarification = bool(state.get("task_resolution_instructions") or state.get("intent_clarification_tasks") or state.get("decline_tasks"))
        preserve_answer = (
            unresolved_clarification
            and clarification_failed
            and answer_compliant
            and answer_grounded
        )
        safe_answer = answer_text if preserve_answer and answer_text.strip() else state.get("safe_answer_for_fallback", "")
        print(f"   🚫 [OUTBOUND AUDIT VETO]: Compliance={compliance_passed} | Groundedness={groundedness_passed}")
        return {
            "safety_retry_count": count,
            "draft_feedback_history": history,
            "preflight_status": "fail",
            "pending_advance": False,
            "safe_answer_for_fallback": safe_answer,
            "clarification_fallback_required": preserve_answer,
        }

    print("   ✅ [OUTBOUND AUDIT PASS]: Response is MAS-compliant and evidence-grounded.")
    plan = state.get("active_plan", {})
    completed_steps = state.get("completed_steps", [])
    if state.get("pending_advance"):
        idx = plan.get("current_step_index", 0)
        steps = plan.get("steps", [])
        if steps and idx < len(steps):
            completed_steps.append(steps[idx])
            plan["current_step_index"] = idx + 1
            print(f"   ⏩ [ATOMIC LEDGER UPDATE]: Safely advanced to step index {plan['current_step_index']}")

    verified_task_history = _append_verified_task_history(state)
    if verified_task_history:
        latest_record = verified_task_history[-1]
        log_event(
            "verified_session_evidence.recorded",
            turn_number=latest_record.get("turn_number"),
            task_ids=[item.get("search_task", {}).get("task_id") for item in latest_record.get("tasks", [])],
            parent_ids=[
                parent_id
                for item in latest_record.get("tasks", [])
                for parent_id in item.get("approved_parent_ids", [])
            ],
            retained_turns=len(verified_task_history),
        )

    return {
        "preflight_status": "pass",
        "active_plan": plan,
        "completed_steps": completed_steps,
        "pending_advance": False,
        "verified_task_history": verified_task_history,
        "safe_answer_for_fallback": "",
        "clarification_fallback_required": False,
    }


def node_07_precision_substitution(state: AgentState):
    print("🟢 [NODE 07] Executing Precision Substitution")
    draft = state.get("final_draft", "")
    payload = state.get("ui_action_payload", {})
    options = payload.get("options", [])
    step_details = payload.get("stepDetails", [])
    list_items = payload.get("items", []) if payload.get("uiType") == "checklist" else []
    list_title = payload.get("listTitle", "")
    list_intro = payload.get("listIntro", "")
    extended_text = payload.get("extendedText", "")
    list_clarification = payload.get("listClarificationText", "")
    response_blocks = payload.get("responseBlocks", [])
    
    # Extract unique placeholders globally
    combined_text = " ".join([draft, *_ui_option_texts(options), *step_details, *list_items, list_title, list_intro, extended_text, list_clarification, json.dumps(response_blocks, ensure_ascii=False)])
    placeholders_in_draft = list(set(re.findall(r"\{\{(.*?)\}\}", combined_text)))
    precision_substituted = False
    
    if placeholders_in_draft:
        valid_map = fetch_placeholders_from_db(placeholders_in_draft)
        precision_substituted = bool(valid_map)
        
        # 1. Substitute in the main draft
        for p_id, p_val in valid_map.items():
            draft = draft.replace(f"{{{{{p_id}}}}}", p_val)
            print(f"   🔢 [SUBSTITUTION]: Replaced {p_id} -> {p_val} in Draft")
            
        # 2. Substitute in the UI options array
        updated_options = []
        for opt in options:
            if isinstance(opt, str):
                updated_opt = opt
                for p_id, p_val in valid_map.items():
                    updated_opt = updated_opt.replace(f"{{{{{p_id}}}}}", p_val)
                updated_options.append(updated_opt)
            elif isinstance(opt, dict):
                updated_opt = dict(opt)
                for field in ("label", "message"):
                    value = updated_opt.get(field, "")
                    if not isinstance(value, str):
                        continue
                    for p_id, p_val in valid_map.items():
                        value = value.replace(f"{{{{{p_id}}}}}", p_val)
                    updated_opt[field] = value
                updated_options.append(updated_opt)
            
        payload["options"] = updated_options
        updated_step_details = []
        for detail in step_details:
            updated_detail = detail
            for p_id, p_val in valid_map.items():
                updated_detail = updated_detail.replace(f"{{{{{p_id}}}}}", p_val)
            updated_step_details.append(updated_detail)
        payload["stepDetails"] = updated_step_details
        updated_list_items = []
        for item in list_items:
            updated_item = item
            for p_id, p_val in valid_map.items():
                updated_item = updated_item.replace(f"{{{{{p_id}}}}}", p_val)
            updated_list_items.append(updated_item)
        if payload.get("uiType") == "checklist":
            payload["items"] = updated_list_items
        for field, value in {
            "listTitle": list_title,
            "listIntro": list_intro,
            "extendedText": extended_text,
            "listClarificationText": list_clarification,
        }.items():
            updated_value = value
            for p_id, p_val in valid_map.items():
                updated_value = updated_value.replace(f"{{{{{p_id}}}}}", p_val)
            payload[field] = updated_value
        def substitute_nested(value):
            if isinstance(value, str):
                for p_id, p_val in valid_map.items():
                    value = value.replace(f"{{{{{p_id}}}}}", p_val)
                return value
            if isinstance(value, list):
                return [substitute_nested(item) for item in value]
            if isinstance(value, dict):
                return {key: substitute_nested(item) for key, item in value.items()}
            return value
        payload["responseBlocks"] = substitute_nested(response_blocks)

    payload["precisionSubstituted"] = precision_substituted

    # Deterministic, never model-authored: attach the real form-opening
    # action for any service_request registered this turn. This runs
    # regardless of how (or whether) the LLM's prose mentioned it -- the
    # actual actionable UI element must never depend on generation quality.
    # Every registered request gets a button, not just the first -- multiple
    # service_request registrations in one turn are fully supported (the
    # planner already loops across rounds to create them; see
    # SERVICE_REQUEST_DESIGN_v1.0.md Section 9). This list is built straight
    # from tool_results, independent of the draft text entirely.
    service_request_actions = []
    for pending_request in _pending_service_requests(state):
        contract = get_service_request_contract(pending_request.get("service_request_id", ""))
        if contract is None:
            continue
        service_request_actions.append({
            "label": contract.get("action_label", "Complete Form"),
            "specialId": pending_request.get("special_id"),
            "serviceRequestId": pending_request.get("service_request_id"),
            "description": pending_request.get("description"),
            "formSchema": contract["form_schema"],
        })
    if service_request_actions:
        payload["serviceRequestActions"] = service_request_actions
        # The intro line lives here, not in the LLM's draft: it runs after
        # node_06's audit gates, so it is never subject to an evidence check
        # that (correctly) cannot verify a UI element attached after it ran.
        # Appended to both the visible blocks (what renders) and the stored
        # draft (what a future turn's conversation context will see), so the
        # transcript stays an accurate record of what the customer was shown.
        language = state.get("user_profile", {}).get("language") or "English"
        intro_key = "service_request_intro_single" if len(service_request_actions) == 1 else "service_request_intro_plural"
        intro_text = get_message(language, intro_key)
        if payload.get("uiType") == "blocks":
            response_blocks = payload.get("responseBlocks", [])
            response_blocks.append({
                "blockId": "service_request_intro", "sourceTaskIds": [],
                "type": "text", "contextText": "", "text": intro_text,
                "items": [], "options": [], "progressTitle": "", "stepDetails": [],
                "currentStep": None, "totalSteps": None, "listTitle": "", "listIntro": "", "extendedText": "",
            })
            payload["responseBlocks"] = response_blocks
        draft = "\n\n".join(part for part in [draft, intro_text] if part)

    return {"final_response": draft, "ui_action_payload": payload}


def node_08_soft_escalation_analyzer(state: AgentState):
    print("🟢 [NODE 08] Executing Soft Escalation Analyzer")
    payload = state.get("ui_action_payload", {})
    # Clarification responses intentionally have no UI actions. Do not let the
    # generic soft-escalation chip reintroduce one after Node 04b removed it.
    if state.get("turn_count", 0) >= 5 and not state.get("final_clarification", "").strip():
        payload["options"].append("Talk to Agent")

    history = list(state.get("chat_history", []))
    if state.get("final_response", ""):
        history.append(AIMessage(content=state.get("final_response", "")))
    return {"ui_action_payload": payload, "chat_history": history}


def _escalation_case_request(state: AgentState) -> str:
    """Return a compact current-request description without echoing the UI confirmation."""
    current_input = str(state.get("user_input", "")).strip()
    if current_input and current_input.casefold() != ESCALATION_CONFIRMATION_MESSAGE.casefold():
        return current_input[:280]

    for message in reversed(state.get("chat_history", [])):
        if not isinstance(message, HumanMessage):
            continue
        content = str(message.content or "").strip()
        if content and content.casefold() != ESCALATION_CONFIRMATION_MESSAGE.casefold():
            return content[:280]
    return get_message(state.get("user_profile", {}).get("language", "English"), "esc_fallback_desc")


def _escalation_case_problem(state: AgentState) -> str:
    """Provide a concise deterministic handoff reason without exposing diagnostics."""
    if state.get("triage_status") == "ESCALATION":
        return get_message(state.get("user_profile", {}).get("language", "English"), "esc_user_asked")
    if state.get("safety_retry_count", 0) >= 3:
        return get_message(state.get("user_profile", {}).get("language", "English"), "esc_safety_retry")
    return get_message(state.get("user_profile", {}).get("language", "English"), "esc_default_review")


def _build_current_session_escalation_context(state: AgentState) -> str:
    """Expose only trusted current-session material to the handoff summarizer."""
    rolling_summary = str(state.get("compacted_history_summary", "")).strip()
    trusted_history = []
    for message in state.get("chat_history", []):
        if isinstance(message, HumanMessage) and message.additional_kwargs.get("trust_status") != "trusted":
            continue
        content = str(message.content or "").strip()
        if not content or content.casefold() == ESCALATION_CONFIRMATION_MESSAGE.casefold():
            continue
        role = "Customer" if isinstance(message, HumanMessage) else "Assistant"
        trusted_history.append(f"{role}: {content}")

    transcript = "\n".join(trusted_history) or "None"
    return (
        f"Current-session rolling memory (older trusted turns):\n{rolling_summary or 'None'}\n\n"
        f"Current-session trusted transcript:\n{transcript}"
    )


def _build_escalation_case_summary(state: AgentState) -> dict:
    """Create a bounded, source-only handoff summary for the confirmation UI."""
    fallback_request = _escalation_case_request(state)
    fallback_problem = _escalation_case_problem(state)
    unresolved_inputs = {
        "intent_clarifications": [
            str(item.get("unresolved_request", "")).strip()
            for item in state.get("intent_clarification_tasks", [])
            if isinstance(item, dict) and str(item.get("unresolved_request", "")).strip()
        ],
        "terminal_retrieval_reasons": [
            str(item.get("reason", "")).strip()
            for item in state.get("task_resolution_instructions", [])
            if isinstance(item, dict) and str(item.get("reason", "")).strip()
        ],
    }
    user_profile = state.get("user_profile", {})
    preferred_language = user_profile.get("language") or "English"

    prompt = f"""
    CURRENT SESSION ESCALATION CASE SUMMARY
    You create a concise handoff brief that saves a human banking agent time
    understanding the customer's unresolved case. This is NOT a chronological
    recap of the session.

    IMPORTANT LANGUAGE RULE: You MUST output the case summary strictly in {preferred_language}, as this summary will be presented directly to the user in the frontend UI.

    Source material (use only this material):
    {_build_current_session_escalation_context(state)}

    Current unresolved-state signals (use only when they match the source material):
    {json.dumps(unresolved_inputs, ensure_ascii=False)}

    Rules:
    - First distinguish the banking topics in the supplied CURRENT-session
      source:
      * RESOLVED: the assistant gave a substantive answer and no later customer
        message shows that the topic remains open.
      * UNRESOLVED: the customer explicitly says it is unanswered, asks to
        continue it, disputes the prior answer, or the assistant explicitly
        could not provide the requested information.
      * CONTEXT ONLY: an earlier topic that is necessary to understand an
        unresolved issue.
    - Identify the handoff case as the customer's most recent UNRESOLVED
      banking goal. The main_pursuit must focus on that goal, not on the
      session as a whole.
    - Include a resolved topic only when it is necessary context for a human
      agent to understand or act on the unresolved issue. Omit completed,
      unrelated, or merely chronological topics.
    - An explicit request for a human agent is a routing request, not an unresolved
      banking issue. Do NOT include "customer wants to talk to a human" or similar
      routing requests in the unresolved_issues list.
    - Identify only unresolved banking issues that are explicit in the supplied source.
    - If the assistant previously provided a policy or answer related to the
      unresolved issue, briefly summarize what the assistant already told the
      customer so the human agent does not repeat it.
    - Do not invent products, policies, account details, actions, outcomes,
      customer preferences, or reasons for the escalation.
    - Do not quote internal parent IDs, diagnostics, model names, or safety
      rules. Do not provide advice or instructions.
    - If there is no unresolved banking issue, use the latest substantive
      banking request. If there is no clear substantive banking request, use
      the general phrase "The current banking conversation.".
    - Return an empty unresolved_issues array when there is no explicit issue.

    Output JSON only:
    {{"main_pursuit": "...", "unresolved_issues": ["..."]}}
    """
    try:
        result = _parse_json_robustly(call_groq_lpu(prompt, model_id=MODEL_C_PLANNER))
        main_pursuit = str(result.get("main_pursuit", "")).strip()[:280]
        unresolved_issues = [
            str(item).strip()[:280]
            for item in result.get("unresolved_issues", [])
            if str(item).strip()
        ][:3]
        if main_pursuit:
            # Groundedness v2.0: this summary reaches the customer directly in
            # the escalation confirmation UI, so it is held to the same two
            # checks as any other customer-facing content before use.
            summary_text = json.dumps({"main_pursuit": main_pursuit, "unresolved_issues": unresolved_issues}, ensure_ascii=False)
            evidence_audit = check_evidence_groundedness(
                summary_text,
                evidence_bundle=_build_current_session_escalation_context(state),
                evidence_description="Current-Session Trusted Transcript",
                route_specific_rules="""
                Every stated topic, unresolved issue, and prior-assistant
                summary must be directly supported by the Permitted Evidence.
                Do not invent products, policies, account details, actions,
                outcomes, customer preferences, or reasons for the escalation.
                """,
            )
            conduct_audit = check_conduct_compliance(summary_text)
            if evidence_audit.get("evidence_grounded") and conduct_audit.get("conduct_compliant"):
                log_event(
                    "escalation.case_summary.generated",
                    main_pursuit=main_pursuit,
                    unresolved_issues=unresolved_issues,
                    source="trusted_current_session",
                )
                return {
                    "request": main_pursuit,
                    "problem": fallback_problem,
                    "unresolvedIssues": unresolved_issues,
                    "source": "current_session_summary",
                }
            log_event(
                "escalation.case_summary.rejected",
                level="WARNING",
                evidence_grounded=bool(evidence_audit.get("evidence_grounded")),
                conduct_compliant=bool(conduct_audit.get("conduct_compliant")),
                evidence_reason=evidence_audit.get("reason", ""),
                conduct_reason=conduct_audit.get("reason", ""),
            )
    except Exception as error:
        print(f"⚠️ [ESCALATION SUMMARY] Current-session summarization failed: {error}")
        log_event("escalation.case_summary.failed", level="WARNING", error=str(error))

    return {
        "request": fallback_request,
        "problem": fallback_problem,
        "unresolvedIssues": [],
        "source": "deterministic_fallback",
    }


def node_09_hard_escalation_broker(state: AgentState):
    print("🟢 [NODE 09] Executing Hard Escalation Broker")
    history = list(state.get("chat_history", []))
    if state.get("preflight_status") == "escalation_confirmation_accepted":
        response = "Your transfer to a human agent is in progress. Please wait."
        history.append(AIMessage(content=response))
        log_event("escalation.transfer.mock_started", transfer_mode="mock")
        return {
            "final_response": response,
            "final_answer": response,
            "final_clarification": "",
            "ui_action_payload": {"uiType": "none", "options": []},
            "chat_history": history,
        }

    case_summary = _build_escalation_case_summary(state)
    response = "I can prepare a summary for a human agent to review your request."
    history.append(AIMessage(content=response))
    log_event(
        "escalation.confirmation.presented",
        trigger_status=state.get("preflight_status", ""),
        triage_status=state.get("triage_status", ""),
        request_summary=case_summary["request"],
        problem_summary=case_summary["problem"],
        unresolved_issues=case_summary["unresolvedIssues"],
        summary_source=case_summary["source"],
    )
    return {
        "final_response": response,
        "final_answer": response,
        "final_clarification": "",
        "ui_action_payload": {
            "uiType": "blocks",
            "options": [],
            "responseBlocks": [{
                "blockId": "escalation-confirmation",
                "type": "escalation_confirmation",
                "request": case_summary["request"],
                "problem": case_summary["problem"],
                "unresolvedIssues": case_summary["unresolvedIssues"],
                "confirmation": {
                    "message": ESCALATION_CONFIRMATION_MESSAGE,
                },
            }],
        },
        "chat_history": history,
    }

def node_10_standard_rejection(state: AgentState):
    status = state.get("preflight_status", "fail")
    print(f"🔴 [NODE 10] DETERMINISTIC MESSAGE ROUTER TRIGGERED. Code: '{status}'")
    
    # MAS-Approved Deterministic Message Dictionary
    MESSAGE_MAP = {
        "error_malicious": get_message(state.get("user_profile", {}).get("language", "English"), "error_malicious"),
        "non_compliant": get_message(state.get("user_profile", {}).get("language", "English"), "non_compliant"),
        "non_financial": get_message(state.get("user_profile", {}).get("language", "English"), "non_financial_rejection"),
        "fail": get_message(state.get("user_profile", {}).get("language", "English"), "fail"),
    }

    # Fetch the mapped message, default to "fail" if the code isn't explicitly defined
    if status == "declined":
        draft = _decline_message(state.get("decline_tasks", []), "your request")
    else:
        draft = MESSAGE_MAP.get(status, MESSAGE_MAP["fail"])
    
    # Append to Chat History
    history = list(state.get("chat_history", []))
    history.append(AIMessage(content=draft))
    
    return {
        "final_response": draft, 
        "ui_action_payload": {"uiType": "none", "options": []}, 
        "chat_history": history
    }


def node_11_deterministic_clarification_fallback(state: AgentState):
    """Safe terminal path when three generated clarifications fail review."""
    print("🟡 [NODE 11] Executing Deterministic Clarification Fallback")
    answer = state.get("safe_answer_for_fallback", "").strip()
    intent_tasks = state.get("intent_clarification_tasks", [])
    unresolved_terms = [
        str(task.get("unresolved_request", "")).strip()
        for task in intent_tasks
        if str(task.get("unresolved_request", "")).strip()
    ]
    named_terms = ", ".join(unresolved_terms)
    all_intent_unclear = bool(intent_tasks) and not state.get("search_tasks", []) and not answer

    if all_intent_unclear:
        clarification = (
            f"Sorry, I’m not yet sure what banking information you need about {named_terms}. "
            "Could you tell me the product or service you’re asking about and what you would like to know?"
        )
    elif answer and named_terms:
        clarification = (
            f"Sorry, I need a little more detail about {named_terms} to help with that part of your request. "
            "Could you clarify the product, service, or specific information you mean?"
        )
    elif answer:
        clarification = (
            "Sorry, I couldn't find relevant FAQ details for the remaining part of your request. "
            "Could you clarify what information you are looking for?"
        )
    else:
        clarification = "Sorry, I couldn't find relevant FAQ details to answer that. Could you clarify what information you are looking for?"
    response = "\n\n".join(part for part in [answer, clarification] if part)
    response = _append_decline_suffix(response, state.get("decline_tasks", []))
    payload = {"uiType": "none", "options": []}
    history = list(state.get("chat_history", []))
    history.append(AIMessage(content=response))
    log_event(
        "clarification.deterministic_fallback",
        retained_answer=bool(answer),
        intent_clarification_count=len(intent_tasks),
        all_intent_unclear=all_intent_unclear,
        safety_retry_count=state.get("safety_retry_count", 0),
    )
    return {
        "final_response": response,
        "final_answer": answer,
        "final_clarification": clarification,
        "ui_action_payload": payload,
        "chat_history": history,
        "pending_advance": False,
    }
