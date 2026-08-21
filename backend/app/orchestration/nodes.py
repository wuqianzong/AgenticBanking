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
    MODEL_B_AUDITOR, 
    MODEL_C_PLANNER,
    MODEL_D_PROMPT_GUARD,
    MODEL_E_TRIAGE,
)
from backend.app.core.compliance import MAS_REGULATIONS_CONTEXT, PRECISION_REFERENCE_CONTRACT
from backend.app.core.system_logger import log_event

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
    "decline_tasks": [],
    "search_tasks": [],
    "task_results": [],
    "task_retry_counts": {},
    "task_feedback": {},
    "task_sufficiency_history": {},
    "task_partial_evidence": {},
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
}

MAX_VERIFIED_TASK_HISTORY_TURNS = 10
ANSWERABLE_TASK_STATUSES = {"SUFFICIENT", "PARTIALLY_SUFFICIENT"}
MAX_RAG_ATTEMPTS = 4


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
            "instruction": "Keep the exact product filter. Retain every prior angle term and intent, then add one or more additional valid intents for that same product when they can improve recall of the missing detail.",
        },
        3: {
            "stage": "PRODUCT_EXPANSION",
            "instruction": "Retain every prior angle term, intent, and product. Add only a clearly plausible, matrix-valid related product as an additional product tag; never replace the original product.",
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
    anchor through filter widening.  Multi-angle RRF is reserved for the
    final, feedback-guided angle-expansion attempt.
    """
    retry_count = retry_counts.get(task.get("task_id"), 0)
    return "ANGLE_GROUP_RRF" if retry_count >= 3 else "DIRECT"


def _merge_retry_task(prior_task: dict, revision: dict, expansion: dict) -> dict:
    """Deterministically preserve cumulative V2.5 retry state.

    The LLM proposes a refinement, but it must never be able to erase prior
    angle groups, products, intents, task identity, or the resolved subquery.
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
        prior_intents = prior_task.get("target_intent", [])
        prior_intents = [prior_intents] if isinstance(prior_intents, str) else prior_intents
        proposed_intents = revision.get("target_intent", [])
        proposed_intents = [proposed_intents] if isinstance(proposed_intents, str) else proposed_intents
        merged["angle_groups"] = prior_groups
        merged["target_commercial_product"] = prior_task.get("target_commercial_product")
        merged["target_intent"] = list(dict.fromkeys([*prior_intents, *proposed_intents]))
    elif stage == "PRODUCT_EXPANSION":
        prior_products = _normalized_product_tags(prior_task.get("target_commercial_product"))
        proposed_products = _normalized_product_tags(revision.get("target_commercial_product"))
        prior_intents = prior_task.get("target_intent", [])
        prior_intents = [prior_intents] if isinstance(prior_intents, str) else prior_intents
        products = list(dict.fromkeys([*prior_products, *proposed_products]))
        merged["angle_groups"] = prior_groups
        merged["target_commercial_product"] = products[0] if len(products) == 1 else products
        # Product expansion widens only the product boundary. Intent widening
        # belongs exclusively to the preceding INTENT_EXPANSION stage.
        merged["target_intent"] = prior_intents
    else:
        merged["angle_groups"] = prior_groups

    return _normalize_search_task_scope(merged) or prior_task

_DECLINE_CATEGORY_REASONS = {
    "non_compliant": "it requires guidance a licensed advisor should give",
    "non_financial": "it's outside what a banking assistant can help with",
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
        reason = _DECLINE_CATEGORY_REASONS.get(next(iter(categories)))
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

def _parse_json_robustly(raw_text: str) -> dict:
    clean_text = raw_text.strip()
    if clean_text.startswith("```json"): clean_text = clean_text[7:]
    elif clean_text.startswith("```"): clean_text = clean_text[3:]
    if clean_text.endswith("```"): clean_text = clean_text[:-3]
    try: return json.loads(clean_text.strip())
    except Exception as e:
        print(f"🚨 [JSON PARSE ERROR]: {e} \nRaw text was: {raw_text}")
        raise e


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
    feedback: str,
    reason: str,
    unapproved_parent_ids: list[str],
    resolution_status: str = "CLARIFICATION_REQUIRED",
) -> dict:
    """Keep terminal retrieval context internal until clarification generation."""
    return {
        "task_id": task_id,
        "type": "TERMINAL_RETRIEVAL_FEEDBACK",
        "feedback": feedback,
        "reason": reason,
        "resolution_status": resolution_status,
        # IDs are retained rather than model-visible parent text. They may be
        # hydrated only for grounded terminal clarification suggestions.
        "unapproved_parent_ids": unapproved_parent_ids,
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
) -> tuple[str, list[str]]:
    """Create a concise welcome and optional, memory-grounded continuation chips.

    Durable memory provides conversational context only; it never restores a
    graph workflow. First-time customers see a clean greeting with no
    artificial suggested prompts.
    """
    if not last_session_summary.strip() and not user_baseline.strip():
        return "Hello! I am Horizon. How can I assist you today?", []

    memory_context = _build_greeting_memory_context(last_session_summary, user_baseline)

    prompt = f"""
    You are Horizon, a banking assistant welcoming a returning customer.
    Write a warm, concise greeting of at most two sentences based only on the
    conversation context below. Frame the prior session as FAQ guidance that
    Horizon previously explained, not as an application or banking process
    Horizon is carrying out. If the context identifies a particular explained
    step or topic, you may mention it as a reminder. Then neutrally invite the
    customer to continue the discussion or ask about something new.

    Important: this is a brand-new session. Do not claim that a workflow,
    application, transfer, plan, approval, or pending action is still active.
    Do not ask for age, income, residency, identity, documents, account data,
    or any other customer information. Do not tell the customer to complete a
    banking action or imply Horizon can complete one for them. Do not invent
    facts, provide banking advice, or use markdown.

    Good style: "Welcome back. Last time, I shared the FAQ steps for applying
    for a personal loan, including the information needed at the next stage.
    Would you like to continue that discussion or explore something else?"

    Durable memory context:
    {memory_context}

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

    return "Welcome back. I can use our previous conversation to help you continue, or we can start with a new question.", []


# --- PHASE 0: INGESTION & INGEST GUARDS ---

def node_00_context_hydrator_and_greeting(state: AgentState):
    print("🟢 [NODE EXECUTING] Node 00: Context Hydrator & Greeting")
    customer_id = state.get("user_id", "USR_999")
    summary = LongTermMemoryManager.fetch_compact_footprint(customer_id)
    user_baseline = LongTermMemoryManager.fetch_user_baseline(customer_id)
    greeting, greeting_options = _generate_returning_customer_greeting(summary, user_baseline)

    return {
        # A new login begins a clean graph.  Only this compact, durable
        # conversation context crosses the session boundary; no plan, task
        # evidence, retry data, or other execution state does.
        **VOLATILE_STATE_RESET,
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
    """n2d: safety-preserving triage. Decomposes a valid request into subqueries
    and screens each for compliance and financial relevance ONLY (no product/intent
    matrix here — that judgment belongs to n2e, which has matrix visibility).
    UX selection happens after retrieval."""
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
    ROLE ALIGNMENT:
    You are an impartial, read-only Horizon Bank UI routing agent. You do NOT give financial advice, and you CANNOT make product choices for the user.

    MAS COMPLIANCE CONTEXT (for screening only; never quote this to the user):
    {MAS_REGULATIONS_CONTEXT}

    Recent Conversation Context: {history_str}
    User Baseline (advisory only; do not treat it as a current request): {user_baseline}
    Current Graph Context: active_plan={has_active_plan}, retrieved_knowledge={has_retrieved_knowledge}
    Recalled Prior-Session Context Is Available: {bool(recalled_context)}
    Latest User Input: "{user_msg}"

    STEP 1 — TRIAGE A BANKING REQUEST for the whole turn.
    1. ESCALATION: User explicitly demands a human agent or manager.
    2. CLARIFICATION: The input is PURELY chitchat, greetings, or completely unrelated to banking (e.g., "Hello", "How are you", "What is the weather").
    3. VALID_REQUEST: Anything else — a banking-relevant question, follow-up, or request, even if part of it turns out to be non-compliant or off-topic once decomposed.

    STEP 2 — ONLY when triage_status is VALID_REQUEST, decompose the LATEST USER INPUT into its distinct customer-request components. A component is one question, request, or independently answerable topic. Do not split one coherent question into artificial fragments; do not impose a numerical cap.
    - Resolve pronouns, ellipsis, or short continuations ("yes", "that one", "proceed") into a standalone, self-contained rephrasing using the Recent Conversation Context.
    - CONVERSATIONAL CONTINUATION: Produce ZERO components when the user is simply providing information requested in a previous step (e.g., "I have MyInfo ready", "Next", "Proceed") AND Current Graph Context confirms an active plan or retrieved knowledge exists. If recalled prior-session context is available but the current graph has neither an active plan nor retrieved knowledge, treat a continuation such as "yes, continue" as one new standalone component describing the recalled topic.

    STEP 3 — For EACH component, assign a resolution:
    - VALID: the component is financial/banking in nature AND does not, on its face, require Horizon to violate the MAS Compliance Context above.
    - DECLINE: the component either (a) is NOT a financial/banking request at all — decline_reason_category="non_financial" — or (b) would require Horizon to violate the MAS Compliance Context, e.g. personalized investment/trading advice, helping bypass KYC/AML checks, or other prohibited conduct — decline_reason_category="non_compliant".

    Do NOT judge whether Horizon offers the specific product or service — that is a separate, later step with its own matrix access. Only judge compliance and financial relevance here. When genuinely unsure whether a component is financial, prefer VALID and let the later step decide.

    Output JSON ONLY:
    {{"triage_status": "ESCALATION" | "CLARIFICATION" | "VALID_REQUEST", "components": [{{"component_id":"component_1","customer_component":"increase my credit card limit","resolution":"VALID"}},{{"component_id":"component_2","customer_component":"which stock should I buy this month","resolution":"DECLINE","decline_reason_category":"non_compliant","decline_detail":"Requests personalized investment advice, prohibited under the Financial Advisers Act without a CMS license."}}]}}
    """
    try:
        res = _parse_json_robustly(call_groq_lpu(prompt, model_id=MODEL_E_TRIAGE))
        triage_status = res.get("triage_status", "CLARIFICATION")
        components = res.get("components", []) if isinstance(res.get("components"), list) else []
    except Exception as e:
        print(f"🚨 [NODE 02d EXCEPTION]: {e}")
        triage_status = "CLARIFICATION"
        components = []
    if triage_status not in {"ESCALATION", "CLARIFICATION", "VALID_REQUEST"}:
        triage_status = "CLARIFICATION"

    print(f"   🧠 [TRIAGE STATUS]: {triage_status}")

    valid_subqueries = []
    decline_tasks = []
    if triage_status == "VALID_REQUEST":
        for index, component in enumerate(components, start=1):
            if not isinstance(component, dict):
                continue
            customer_component = str(component.get("customer_component", "")).strip()
            if not customer_component:
                continue
            resolution = str(component.get("resolution", "")).strip().upper()
            if resolution == "DECLINE":
                reason_category = component.get("decline_reason_category", "non_compliant")
                if reason_category not in {"non_compliant", "non_financial"}:
                    reason_category = "non_compliant"
                decline_tasks.append({
                    "task_id": f"decline_triage_{index}",
                    "subquery": customer_component,
                    "reason_category": reason_category,
                    "detail": str(component.get("decline_detail", "")).strip(),
                    "source": "triage",
                })
            else:
                valid_subqueries.append({
                    "component_id": str(component.get("component_id", "")).strip() or f"component_{index}",
                    "customer_component": customer_component,
                })

    if triage_status == "VALID_REQUEST" and components and not valid_subqueries:
        # Every decomposed component was declined; nothing reaches the formulator.
        mapped_preflight = "declined"
    else:
        mapped_preflight = "intent_clarification_needed" if triage_status == "CLARIFICATION" else triage_status

    print(f"   🔎 [TRIAGE DECOMPOSITION]: {len(valid_subqueries)} valid subquery(ies) | {len(decline_tasks)} declined")

    return {
        "triage_status": triage_status,
        "preflight_status": mapped_preflight,
        "triaged_subqueries": valid_subqueries,
        "decline_tasks": decline_tasks,
    }

def node_02e_intent_classifier_and_query_formulator(state: AgentState):
    """n2e: formulate retrieval, clarification, or scope-decline resolutions for
    the components n2d already decomposed and compliance/relevance-screened.
    Owns matrix-dependent judgment only; never re-decomposes raw input."""
    print("🟢 [NODE 02e] Executing Multi-Task Query Formulator")
    user_msg = state.get("user_input", "")
    triaged_subqueries = state.get("triaged_subqueries", [])
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
    retry_context = ""
    if retry_tasks:
        retry_context = f"""
        RETRY MODE: Rewrite ONLY these existing tasks. Preserve each task_id exactly.
        Failed tasks: {retry_tasks}
        Task-specific feedback: {retry_feedback}
        """
    retry_expansion_plan = _retry_expansion_plan(retry_tasks, state.get("task_retry_counts", {}))
    
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

    CRITICAL RULES FOR VECTOR DENSITY:
    - NEVER use conversational filler (e.g., "Provide a guide", "Explain how to", "What is").
    - NEVER use complete sentences.
    - Extract ONLY the core nouns, target verbs, and specific constraints.
    - Format each angle as a concise, comma-separated string of keywords.

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
    4. For EACH SEARCH component, formulate exactly one base Angle Group with three distinct semantic angles based strictly on the density rules below. An angle group is a complete alternative semantic description; never concatenate unrelated wording into one angle:
       - "procedural": Only the action verbs and tool nouns (e.g., "Horizon App, navigation, setup MyInfo, verification process").
       - "policy": Only the rule nouns and constraints (e.g., "eligibility rules, required documents, security policy, restrictions").
       - "conceptual": Only the definition nouns (e.g., "MyInfo service, personal data sharing, Horizon Bank").
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
    if retry_tasks:
        prompt = f"""
        PRODUCT/INTENT MATRIX (Valid Nouns and Verbs):
        {PRODUCT_INTENT_MATRIX}

        INTENT DEFINITION GUIDELINE:
        {INTENT_GUIDELINE}

        RETRIEVAL RETRY MODE
        You are a Decoupled Database Search Architect refining failed retrieval
        tasks. This is NOT a new decomposition of the customer's request.

        Latest User Input: "{user_msg}"
        Failed Retrieval Tasks (the only components in scope): {retry_tasks}
        Task-Specific Candidate Evidence Delta: {retry_feedback}
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
          complete angle group with group_id "retry_<next_attempt>". The new
          group's three concise angles must target the missing detail described
          in the feedback. Do not append terms into an existing angle.
        - INTENT_EXPANSION: keep the exact prior product filter; retain all
          prior angle groups and intents, then add valid intent filters that
          can improve recall of the missing detail. Do not remove prior
          intents or alter any angle group.
        - PRODUCT_EXPANSION: retain all prior angle terms, intents, and
          product tags. Keep angle groups unchanged. Output
          target_commercial_product as an ordered array containing the
          original product first plus only a clearly plausible, matrix-valid
          related product. Never replace the original product.
        - The revised task must remain about the same customer component as its
          supplied failed task.
        - Every revised Product/Intent pair must be valid in the matrix.
        - NEVER use conversational filler or complete sentences in semantic
          angles. Extract only core nouns, verbs, and constraints as concise,
          comma-separated keywords.

        Output JSON ONLY:
        {{"components": [{{"component_id":"retry_task_1","customer_component":"same component as failed task","resolution":"SEARCH","search_task":{{"task_id":"exact failed task_id","angle_groups":[{{"group_id":"base","procedural":"unchanged","policy":"unchanged","conceptual":"unchanged"}},{{"group_id":"retry_2","procedural":"...","policy":"...","conceptual":"..."}}],"target_commercial_product":"valid matrix product or ordered product array during PRODUCT_EXPANSION","target_intent":["valid matrix intent"]}},"clarification":null}}]}}
        """
    
    try:
        res = _parse_json_robustly(call_groq_lpu(prompt, model_id=MODEL_E_TRIAGE))
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
        replacements = {task.get("task_id"): task for task in formulated_tasks if task.get("task_id") in retry_task_ids}
        expansion_by_task_id = {
            expansion.get("task_id"): expansion
            for expansion in retry_expansion_plan
        }
        search_tasks = []
        for prior_task in prior_tasks:
            task_id = prior_task.get("task_id")
            replacement = replacements.get(task_id)
            if replacement and task_id in retry_task_ids:
                search_tasks.append(_merge_retry_task(
                    prior_task,
                    replacement,
                    expansion_by_task_id.get(task_id, {}),
                ))
            else:
                # Successful sibling tasks are immutable while only failed
                # task IDs re-enter retrieval.
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
        if not intent_clarification_tasks and decline_tasks:
            # Every component the formulator received resolved to DECLINE
            # (out-of-scope product). Nothing valid remains for this turn.
            return {
                "preflight_status": "declined",
                "search_tasks": [],
                "intent_clarification_tasks": [],
                "task_results": [],
                "decline_tasks": decline_tasks,
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
            }
        if active_plan.get("steps") or has_retrieved_knowledge:
            return {"preflight_status": "continue_plan", "search_tasks": [], "decline_tasks": decline_tasks}
        return {"preflight_status": "intent_clarification_needed", "search_tasks": [], "decline_tasks": decline_tasks}

    # Every task enters live direct retrieval. Search Strategy v2.7 may add
    # matching verified-session parents as a separate RRF list, but no broad
    # product/intent cache may bypass the current subquery's retrieval.
    print("   🎯 [TASK ROUTER]: Live retrieval required; verified session evidence may join RRF when task scope overlaps.")
    log_event("task.retrieval.route", task_count=len(search_tasks), retry_mode=bool(retry_tasks), strategy="v2.7_verified_session_rrf")
    return {
        "preflight_status": "retrieve",
        "search_tasks": search_tasks,
        "intent_clarification_tasks": intent_clarification_tasks,
        "task_results": task_results if retry_tasks else [],
        "active_product_intents": new_matrix,
        "active_plan": {} if not retry_tasks else active_plan,
        "completed_steps": [] if not retry_tasks else state.get("completed_steps", []),
        "decline_tasks": decline_tasks,
    }

# --- PHASE 2: BOUNDED RAG ARCHITECTURE ---

def node_03a_knowledge_blueprint_planner(state: AgentState):
    print("🟢 [NODE 03a] Preparing Task-Level RAG Retrieval")
    prior_results = {result.get("task_id"): result for result in state.get("task_results", [])}
    tasks_to_retrieve = [
        task for task in state.get("search_tasks", [])
        if prior_results.get(task.get("task_id"), {}).get("status") not in {*ANSWERABLE_TASK_STATUSES, "UNAVAILABLE"}
    ]
    routes = ["n17_rag"] if tasks_to_retrieve else []
        
    print(f"   🔀 [SUPERVISOR DISPATCH]: Fanning out to -> {routes}")
    return {"active_tool_routes": routes, "rag_payload": "", "api_payload": "", "deeplink_payload": ""}

async def node_03b_rag_worker(state: AgentState):
    print("🟢 [NODE 03b_1] Executing Task-Level RAG Worker (Direct-First Retrieval)")
    tasks = state.get("search_tasks", [])
    prior_results = {result.get("task_id"): result for result in state.get("task_results", [])}
    tasks_to_retrieve = [
        task for task in tasks
        if prior_results.get(task.get("task_id"), {}).get("status") not in {*ANSWERABLE_TASK_STATUSES, "UNAVAILABLE"}
    ]
    if not tasks_to_retrieve:
        return {"rag_payload": "", "task_results": list(prior_results.values())}

    engine = RAGEngine()
    async def retrieve_task(task: dict) -> dict:
        scope_pairs = _valid_product_intent_scope_pairs(task)
        retrieval_mode = _retrieval_mode_for_task(task, state.get("task_retry_counts", {}))
        verified_session_parent_ids = _verified_session_parent_ids_for_task(state, task)
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
            )
        else:
            parents = await engine.execute_multi_angle_search(
                blueprint=task,
                scope_pairs=scope_pairs,
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

    # A partly answerable task may accumulate evidence across retries.  The
    # auditor must see retained parents together with the latest candidates so
    # it can promote the combined set when the remaining gap is filled.
    evaluation_inputs = []
    candidate_ids_by_task = {}
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
        evaluation_inputs.append({
            "task_id": task_id,
            "task": tasks_by_id.get(task_id, {}),
            "retained_partial_parent_ids": retained_ids,
            "prior_sufficiency_audit_history": sufficiency_history.get(str(task_id), []),
            "parents": all_parents,
        })
    auditor_results = {}
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
          backend determines terminal clarification after four attempts.
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
            auditor_results = {
                result.get("task_id"): result
                for result in _parse_json_robustly(call_groq_lpu(prompt, model_id=MODEL_B_AUDITOR)).get("task_results", [])
            }
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
        if requested_status == "SUFFICIENT" and approved_ids:
            # The sufficiency model is permitted to select identifiers only.  Do
            # not retain any model-visible parent text: re-read this exact
            # allow-listed ID set from SQLite, the authoritative FAQ source.
            approved_parents = RAGEngine().hydrate_parent_ids(approved_ids)
            hydrated_ids = {parent.get("parent_id") for parent in approved_parents}
            approved_ids = [parent_id for parent_id in approved_ids if parent_id in hydrated_ids]
            if not approved_ids:
                requested_status = "RETRY"
                reason = "The approved FAQ parent could not be re-hydrated from the knowledge database."
            else:
                current_results[task_id] = {
                    "task_id": task_id,
                    "status": "SUFFICIENT",
                    "origin": result.get("origin", "RAG"),
                    "approved_parent_ids": approved_ids,
                    "reason": reason,
                }
                feedback.pop(task_id, None)
                partial_evidence.pop(str(task_id), None)
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
        # fourth attempt. At the boundary it is answerable for the supported
        # part and carries a clarification only for the unresolved part.
        if attempt_count >= MAX_RAG_ATTEMPTS:
            unapproved_ids = [
                parent.get("parent_id") for parent in result.get("parents", [])
                if parent.get("parent_id") and parent.get("parent_id") not in partial_ids
            ]
            unapproved_parents = RAGEngine().hydrate_parent_ids(unapproved_ids)
            if partial_ids:
                current_results[task_id] = {
                    "task_id": task_id,
                    "status": "PARTIALLY_SUFFICIENT",
                    "approved_parent_ids": partial_ids,
                    "reason": reason,
                    "terminal_feedback": refinement,
                    "unapproved_parent_ids": [parent.get("parent_id") for parent in unapproved_parents if parent.get("parent_id")],
                }
            else:
                current_results[task_id] = {
                    "task_id": task_id,
                    "status": "CLARIFICATION_REQUIRED",
                    "approved_parent_ids": [],
                    "reason": reason,
                    "terminal_feedback": refinement,
                    "unapproved_parent_ids": [parent.get("parent_id") for parent in unapproved_parents if parent.get("parent_id")],
                }
            feedback.pop(task_id, None)
        else:
            current_results[task_id] = {
                "task_id": task_id,
                "status": "RETRY",
                "approved_parent_ids": [],
                "reason": reason,
            }
            feedback[task_id] = refinement

    unresolved = [result for result in current_results.values() if result.get("status") == "RETRY"]
    task_resolution_instructions = [
        _terminal_retrieval_feedback(
            result.get("task_id"),
            result.get("terminal_feedback", result.get("reason", "")),
            result.get("reason", ""),
            result.get("unapproved_parent_ids", []),
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
    return {
        "preflight_status": "retry_tasks" if unresolved else "task_resolution_ready",
        "task_results": list(current_results.values()),
        "task_retry_counts": retry_counts,
        "task_feedback": feedback,
        "task_sufficiency_history": sufficiency_history,
        "task_partial_evidence": partial_evidence,
        "task_resolution_instructions": task_resolution_instructions,
    }


# --- PHASE 3: RESOLUTION BLUEPRINT & STEP-BY-STEP RESPONSE ---

def node_04a_resolution_planner(state: AgentState):
    """n19: create optional UX guidance without imposing visible structure."""
    print("🟢 [NODE 04a] Executing Flexible User Journey Planner")
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

    prompt = f"""
    FLEXIBLE USER EXPERIENCE PLANNER
    You are an internal communication designer for a read-only Horizon Bank FAQ assistant.
    Produce guidance for a natural response; you do NOT write customer-facing
    text and you do NOT prescribe a fixed response structure.

    Customer request: {state.get("user_input", "")}
    Answerable evidence by retrieval task: {json.dumps(planner_tasks, ensure_ascii=False)}
    Intent clarification inputs: {json.dumps(state.get("intent_clarification_tasks", []), ensure_ascii=False)}
    Declined inputs: {json.dumps(state.get("decline_tasks", []), ensure_ascii=False)}

    {PRECISION_REFERENCE_CONTRACT}

    Principle: presentation should visualize the mental model the customer
    needs now. Use prose to understand, a list to scan, options to choose,
    progress to understand a genuine process, and no UI at all when it would
    not lower cognitive load.

    Rules:
    - Search tasks are evidence-retrieval units, never mandatory visible
      sections. Do not create a block plan or one UI element per task.
    - Recommend only optional visual hints. A response may be entirely text,
      may group several tasks in one explanation, or may use several visuals
      only when each materially helps comprehension.
    - Preserve the customer's priorities and make the most useful information
      easy to understand first. Group related facts naturally; do not impose
      headings, introductions, closings, titles, lists, or progress payloads
      merely because they exist.
    - A progress hint is appropriate only for a genuinely sequential process.
      A list hint is appropriate only for independently scannable items.
    - Keep clarification outside any suggested process/list and place it after
      useful supported information when both exist.
    - coverage_expectation must include every supplied answerable task_id, but
      it is an internal completeness reminder, not a demand for separate UI.
    - Never invent facts, banking actions, products, steps, or customer text.

    Output JSON only:
    {{"primary_user_need":"...","narrative_focus":"...","grouping_guidance":"...","visual_hints":[{{"type":"LIST|PROGRESS|OPTIONS","purpose":"..."}}],"clarification_guidance":"...","coverage_expectation":["task_1"]}}
    """
    try:
        guidance = _parse_json_robustly(call_groq_lpu(prompt, model_id=MODEL_C_PLANNER))
    except Exception as error:
        print(f"🚨 [FLEXIBLE JOURNEY PLANNER ERROR]: {error}")
        guidance = {}

    visual_hints = [
        {"type": str(hint.get("type", "")).upper(), "purpose": str(hint.get("purpose", "")).strip()}
        for hint in guidance.get("visual_hints", []) if isinstance(hint, dict)
        and str(hint.get("type", "")).upper() in {"LIST", "PROGRESS", "OPTIONS"}
        and str(hint.get("purpose", "")).strip()
    ]
    planned_coverage = [task_id for task_id in guidance.get("coverage_expectation", []) if task_id in answerable_task_ids]
    ux_guidance = {
        "primary_user_need": str(guidance.get("primary_user_need", "Provide a clear, useful answer.")).strip(),
        "narrative_focus": str(guidance.get("narrative_focus", "Answer naturally and directly using approved evidence.")).strip(),
        "grouping_guidance": str(guidance.get("grouping_guidance", "Group related facts only when it improves clarity.")).strip(),
        "visual_hints": visual_hints,
        "clarification_guidance": str(guidance.get("clarification_guidance", "Place unresolved information after supported information.")).strip(),
        "coverage_expectation": planned_coverage or answerable_task_ids,
    }
    print(f"   🧭 [UX GUIDANCE PLANNED]: {len(visual_hints)} optional visual hint(s) | Coverage: {len(ux_guidance['coverage_expectation'])} task(s)")
    log_event("journey.ux_guidance.planned", coverage_task_ids=ux_guidance["coverage_expectation"], visual_hints=visual_hints)
    return {
        "preflight_status": "presentation_ready",
        "selected_ux_journey": "FLEXIBLE",
        "presentation_plan": {"ux_guidance": ux_guidance},
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
    terminal_feedback = state.get("task_resolution_instructions", [])
    decline_context = [
        {"subquery": task.get("subquery", ""), "category": task.get("reason_category", "")}
        for task in state.get("decline_tasks", [])
        if task.get("subquery", "")
    ]
    audit_feedback = state.get("draft_feedback_history", [])[-4:]
    prompt = f"""
    RESPONSE GENERATION V2.0
    You are Horizon, a digital customer service representative for Horizon
    Bank. Provide helpful, natural, read-only FAQ guidance: do not perform
    banking actions, imply account access, or introduce facts beyond approved
    evidence. Generate ONE coherent customer turn as several ordered response
    blocks. Each block renders separately, but together they must read as one
    considered response to the customer's complete request — never as
    disconnected mini-answers.

    Customer's complete request: {state.get("user_input", "")}
    Planned response blocks and their approved evidence: {json.dumps(block_inputs, ensure_ascii=False)}
    Intent clarification tasks: {json.dumps(intent_clarifications, ensure_ascii=False)}
    Terminal retrieval feedback: {json.dumps(terminal_feedback, ensure_ascii=False)}
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
    - Combine all unresolved components into one natural clarification field at
      the end, sourced ONLY from Intent clarification tasks, Terminal
      retrieval feedback, and Decline Inputs above. Explicitly name each
      unresolved customer term; do not invent a product or fact. Never
      introduce, reference, or apologize for any topic, product, or request
      component that is not listed in those three inputs — routing has
      already handled it separately. If all three inputs are empty, leave
      clarification as an empty string. Clarification is plain text, not a
      UI block.
    - EVERY entry across Intent clarification tasks, Terminal retrieval
      feedback, and Decline Inputs MUST appear in the clarification — never
      silently drop one to resolve a different entry's audit feedback. If
      Failed outbound-audit feedback flags one entry, correct only that
      entry (falling back to a brief, unembellished mention grounded only in
      its own listed fields if nothing more specific can be safely stated);
      every other entry must still be included exactly as before.
    - For EVERY entry in Decline Inputs, add one brief, polite sentence naming
      only that entry's subquery — never omit one, never invent a reason
      beyond its category. For category "non_compliant", state plainly that
      you are unable to help with that specific request and, if natural,
      suggest speaking with an advisor for that kind of need; never explain
      the specific regulation, never provide the advice or action being
      declined. For category "non_financial", state plainly that the topic
      is outside what a banking assistant can help with. Never ask a
      clarification question about a declined entry.
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
    intro_text = str(generated.get("intro_text", "")).strip()
    closing_text = str(generated.get("closing_text", "")).strip()
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
        context_text = str(item.get("context_text", "")).strip()
        answer = str(item.get("answer", "")).strip()
        extended_text = str(item.get("extended_text", item.get("supporting_text", ""))).strip()
        options = _normalize_ui_options(item.get("options", []))
        progress_steps = item.get("progress_steps", []) if isinstance(item.get("progress_steps", []), list) else []
        progress_steps = [
            {"title": str(step.get("title", "")).strip(), "detail": str(step.get("detail", "")).strip()}
            for step in progress_steps
            if isinstance(step, dict) and str(step.get("title", "")).strip() and str(step.get("detail", "")).strip()
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
            "progressTitle": str(item.get("progress_title", "")).strip() if ui_type == "stepper" else "",
            "stepDetails": [],
            "currentStep": 1 if ui_type == "stepper" else None,
            "totalSteps": len(progress_steps) if ui_type == "stepper" else None,
            "listTitle": str(item.get("list_title", "")).strip() if ui_type == "checklist" else "",
            "listIntro": answer if ui_type == "checklist" else "",
            "extendedText": extended_text,
        })
        if record:
            record_parts.append(record)
    if closing_text and len(block_plans) > 1:
        response_blocks.append({"blockId": "turn_closing", "type": "text", "text": closing_text})
        record_parts.append(closing_text)
    clarification = str(generated.get("clarification", "")).strip()
    if (intent_clarifications or terminal_feedback or decline_context) and not clarification:
        unresolved = ", ".join(item.get("unresolved_request", "") for item in intent_clarifications if item.get("unresolved_request", ""))
        if unresolved:
            clarification = f"Could you clarify what you mean by {unresolved}?"
        elif intent_clarifications or terminal_feedback:
            clarification = "Could you clarify what information you are looking for?"
        else:
            clarification = ""
        clarification = _append_decline_suffix(clarification, state.get("decline_tasks", [])).strip()
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
    """Generate freeform, ordered response elements from UX guidance.

    Elements are frontend transport units only. They do not correspond one-to-
    one with retrieval tasks: a natural element may combine several related
    evidence sets, or the response may be entirely plain text.
    """
    ux_guidance = state.get("presentation_plan", {}).get("ux_guidance", {})
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
    terminal_feedback = state.get("task_resolution_instructions", [])
    decline_context = [
        {"subquery": task.get("subquery", ""), "category": task.get("reason_category", "")}
        for task in state.get("decline_tasks", [])
        if task.get("subquery", "")
    ]
    audit_feedback = state.get("draft_feedback_history", [])[-4:]
    prompt = f"""
    FLEXIBLE RESPONSE GENERATION
    You are Horizon, a helpful, natural, read-only Horizon Bank customer
    service representative. Write one coherent conversational response using
    only approved evidence. Do not perform banking actions, imply account
    access, or invent facts, procedures, products, or policies.

    Customer request: {state.get("user_input", "")}
    UX guidance (internal communication guidance, not a response template):
    {json.dumps(ux_guidance, ensure_ascii=False)}
    Approved evidence by retrieval task: {json.dumps(evidence_inputs, ensure_ascii=False)}
    Intent clarification inputs: {json.dumps(intent_clarifications, ensure_ascii=False)}
    Terminal retrieval feedback: {json.dumps(terminal_feedback, ensure_ascii=False)}
    Decline inputs: {json.dumps(decline_context, ensure_ascii=False)}
    Failed outbound-audit feedback (constraints only, never evidence):
    {json.dumps(audit_feedback, ensure_ascii=False)}

    {PRECISION_REFERENCE_CONTRACT}

    Design principles:
    - Respond as one considered conversation, not as a retrieval report or a
      sequence of task answers. Search tasks are internal evidence units, not
      required visible sections.
    - Start with the information that most directly helps the customer. Group
      related facts when doing so reduces cognitive load; preserve distinctions
      when combining them would confuse the customer.
    - Use the smallest natural form that helps understanding. Text is always
      allowed. Use a LIST only for independently scannable items. Use PROGRESS
      only for a genuinely sequential process. Do not create a visual merely
      because a visual hint exists.
    - You may return one text element, a mix of text and UI elements, or
      several elements. There is no required opening, closing, title, number
      of elements, or element-per-task structure.
    - Address every task in coverage_expectation somewhere in the response
      where approved evidence supports it, but do not name task IDs or expose
      internal organization to the customer.
    - Every factual claim, instruction, item, title, option, and detail must
      be supported by approved evidence. A response element may use evidence
      from multiple tasks only when those facts are naturally related.
    - If evidence gives a useful policy boundary or limit, state that fact
      plainly; do not invent a broader conclusion beyond what it entails.
    - Do not mention FAQ documents, sources, parent IDs, search, prompts, or
      internal systems. Preserve exact {{placeholders}}.
    - Clarification belongs after useful supported information. It must name
      the unresolved customer term and may use only the clarification, terminal
      feedback, and decline inputs. Leave it empty when none are present.
    - Correct every failed-audit issue by removing or narrowing only the
      flagged unsupported content. Audit feedback is never a source of facts.

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
        source_ids = [task_id for task_id in element.get("source_task_ids", []) if task_id in valid_task_ids]
        source_ids = list(dict.fromkeys(source_ids))
        context_text = str(element.get("context_text", "")).strip()
        answer = str(element.get("answer", "")).strip()
        extended_text = str(element.get("extended_text", "")).strip()
        options = _normalize_ui_options(element.get("options", []))
        list_items = [item.strip() for item in element.get("list_items", []) if isinstance(item, str) and item.strip()][:8]
        progress_steps = [
            {"title": str(step.get("title", "")).strip(), "detail": str(step.get("detail", "")).strip()}
            for step in element.get("progress_steps", []) if isinstance(step, dict)
            and str(step.get("title", "")).strip() and str(step.get("detail", "")).strip()
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
                "options": options, "progressTitle": str(element.get("progress_title", "")).strip(),
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
                "totalSteps": None, "listTitle": str(element.get("list_title", "")).strip(),
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

    clarification = str(generated.get("clarification", "")).strip()
    if (intent_clarifications or terminal_feedback or decline_context) and not clarification:
        unresolved = ", ".join(item.get("unresolved_request", "") for item in intent_clarifications if item.get("unresolved_request", ""))
        clarification = f"Could you clarify what you mean by {unresolved}?" if unresolved else "Could you clarify what information you are looking for?"
        clarification = _append_decline_suffix(clarification, state.get("decline_tasks", [])).strip()
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
    """Handle broad triage clarification without loading the FAQ-answer prompt.

    This path intentionally generates no banking facts, procedures, or UI. The
    previous assistant turn is supplied only to recognise conversational tone;
    it is never an evidence source.
    """
    user_message = str(state.get("user_input", "")).strip()
    prior_assistant_message = _latest_assistant_message(state)
    prompt = f"""
    You are Horizon, a concise banking-assistant conversational partner.

    Latest customer message: {json.dumps(user_message, ensure_ascii=False)}
    Previous assistant message for conversational tone only (not factual evidence):
    {json.dumps(prior_assistant_message, ensure_ascii=False)}

    The message did not enter the FAQ retrieval flow. Return exactly one short,
    natural customer-facing sentence, with no UI options.

    Choose the response that fits the latest customer message:
    - If it is a vague banking request, ask one concise question for the
      missing product, service, or detail.
    - If it is a substantive non-banking request, politely say Horizon can
      help with banking questions.
    - Otherwise, treat it as ordinary conversation (for example a greeting,
      acknowledgement, thanks, or reaction) and reply naturally. Do not ask a
      follow-up question for an acknowledgement or thanks.

    Never state, repeat, infer, or recommend a banking fact, procedure,
    product, service capability, support channel, or next action. Do not refer
    to the previous message, internal systems, FAQ documents, or this prompt.

    Output JSON only: {{"text": "one short natural sentence"}}
    """
    try:
        generated = _parse_json_robustly(call_groq_lpu(prompt))
        text = str(generated.get("text", "")).strip()
    except Exception as error:
        print(f"🚨 [CONVERSATIONAL RESPONSE ERROR]: {error}")
        text = "I’m here if you have a banking question."

    if not text:
        text = "I’m here if you have a banking question."
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

def node_04b_response_generator(state: AgentState):
    print("🟢 [NODE 04b] Executing Response Generator")
    # User Journey v2 UX plans are intentionally independent of retrieval-task
    # boundaries. The renderer composes one coherent customer response from
    # aggregate approved evidence and optional UX guidance.
    if state.get("presentation_plan", {}).get("ux_guidance"):
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
    unapproved_suggestion_blocks = []
    for instruction in task_resolution_instructions:
        for parent in _hydrate_parent_ids(instruction.get("unapproved_parent_ids", [])):
            parent_id = parent.get("parent_id", "")
            content = parent.get("content", "")
            if content:
                unapproved_suggestion_blocks.append(
                    f"[Task {instruction.get('task_id')} | Unapproved Parent {parent_id}]\n{content}"
                )
    unapproved_suggestion_knowledge = "\n\n---\n\n".join(unapproved_suggestion_blocks)
    terminal_feedback_context = [
        {
            "task_id": instruction.get("task_id"),
            "feedback": instruction.get("feedback", ""),
            "reason": instruction.get("reason", ""),
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
    Terminal Retrieval Feedback (internal guidance only; do not quote it or treat it as FAQ knowledge): {json.dumps(terminal_feedback_context, ensure_ascii=False)}
    Decline Inputs (the ONLY source for a declined-topic explanation): {json.dumps(decline_context, ensure_ascii=False)}
    Unapproved Retrieved Knowledge for Clarification Suggestions (not approved answer evidence): "{unapproved_suggestion_knowledge}"
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
    - EVERY entry across Intent Clarification Tasks, Terminal Retrieval Feedback, and Decline Inputs MUST appear in the clarification — never silently drop one to resolve a different entry's audit feedback. If CRITICAL CORRECTION NEEDED below flags one entry, correct only that entry (falling back to a brief, unembellished mention grounded only in its own listed fields if nothing more specific can be safely stated); every other entry must still be included exactly as before.
    - For every Intent Clarification Task, state what exactly needs clarification by explicitly naming its unresolved_request. Never replace it with an ambiguous reference such as "that", "it", "the request", or "the remaining part". Do not guess a product, policy, or banking fact beyond the customer's wording.
    - For EVERY entry in Decline Inputs, add one brief, polite sentence to the clarification naming only that entry's subquery — never omit one, never invent a reason beyond its category. For category "non_compliant", state plainly that you are unable to help with that specific request and, if natural, suggest speaking with an advisor for that kind of need; never explain the specific regulation, never provide the advice or action being declined. For category "non_financial", state plainly that the topic is outside what a banking assistant can help with. Never ask a clarification question about a declined entry.
    - For a terminal retrieval feedback item, the clarification MUST begin with a brief apology/inability statement (for example, "Sorry, I couldn't find relevant FAQ details to answer that.").
    - When the user has multiple requests, explicitly name the unresolved term exactly as the user wrote it in the apology (for example, "Sorry, I couldn't find relevant FAQ details about ABGX loan."). Never use an ambiguous reference such as "that", "it", or "the remaining part" in its place.
    - You may then offer up to two potentially related FAQ topics, but ONLY when each topic is explicitly present in Unapproved Retrieved Knowledge for Clarification Suggestions. Make clear these are possible related topics, not an answer to the customer's request.
    - End with a general, natural invitation to clarify if those suggested topics are irrelevant. If no suitable suggestion exists, ask only the general clarification question.
    - Do not request specific details such as an amount, term, purpose, eligibility, or application information unless they are explicitly supported by Unapproved Retrieved Knowledge for Clarification Suggestions.
    - Treat all clarification guidance as uncertain internal guidance: do not quote internal reason text, turn it into a customer-facing product label, or treat it as a fact.
    - Do not offer UI options when any unresolved task requires clarification. Answer UI options are allowed only when every component is supported.
    - If you provide `options`, each must be an object with a concise visible `label` (2-6 words) and a complete, unambiguous `message` that will be sent when clicked. The message must be grounded in the same approved knowledge as the answer.
    - Never expose internal metadata to the customer, including task IDs, parent IDs such as `faq_abc12345`, retrieval labels, model names, audit results, or internal feedback. Parent-ID headers are evidence metadata only, not citations.
    - SOURCE-NEUTRAL CONVERSATION: FAQ records, policies, documents, database entries, search results, and retrieved knowledge are internal evidence only. Never mention or imply those internal sources in customer-facing text. Do not say "according to an FAQ", "another FAQ", "the FAQ says", "the document states", "the knowledge base", "the retrieved information", "the policy database", or similar wording.
    - Present approved facts directly and naturally as Horizon's banking guidance. When several records support one answer, synthesize them into one coherent answer instead of describing separate sources. `extended_text` is normal conversational continuation, not a source note: it may add relevant approved context but must not describe where the information came from.
    - Discuss provenance only when the customer explicitly asks about sources. In that case, refer only at a high level to "Horizon Bank's published information" and never reveal internal retrieval mechanics, FAQ records, parent IDs, model names, or database details.
    - ZERO HALLUCINATION: Rely EXCLUSIVELY on Selected Approved Knowledge for the answer. Intent clarification may rely only on the user's stated unresolved_request; terminal retrieval suggestions may additionally rely on Unapproved Retrieved Knowledge for Clarification Suggestions.
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
        answer = res.get("answer", res.get("response", "")).strip()
        clarification = res.get("clarification", "").strip()
        generated_step_details = res.get("step_details", []) or []
        generated_list_items = res.get("list_items", []) or []
        # `supporting_text` is accepted only for responses generated before
        # the extended-text contract rolled out. New responses use the
        # presentation-neutral `extended_text` field.
        extended_text = res.get("extended_text", res.get("supporting_text", "")).strip()
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
            "resolved_subquery": tasks_by_id.get(task_id, {}).get("customer_component", ""),
            "unapproved_parents": _hydrate_parent_ids(instruction.get("unapproved_parent_ids", [])),
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
    """Focused MAS policy audit of the complete customer-facing response."""
    print("🟢 [NODE 06a] Executing Outbound Compliance Audit")
    answer, clarification, rendered_payload = _outbound_response_content(state)
    prompt = f"""
    You are an independent Compliance Auditor for a Singaporean Bank.

    Regulatory Context:
    {MAS_REGULATIONS_CONTEXT}

    {PRECISION_REFERENCE_CONTRACT}

    Customer-Facing Answer:
    "{answer}"

    Customer-Facing Clarification:
    "{clarification}"

    Complete Customer-Facing UI Payload:
    "{rendered_payload}"

    TASK: Assess COMPLIANCE ONLY. Do not assess whether facts are supported by
    evidence; a separate auditor handles groundedness. Determine whether any
    customer-facing text, UI item, option, step, list entry, or extended text
    provides unauthorized personalized financial advice, subjective product
    recommendation, return forecast, transaction execution, deceptive claim,
    or assistance to bypass security, KYC, AML/CFT, or other controls.
    Neutral factual information, safe apologies, and neutral clarification
    requests are compliant. A polite decline of a non-compliant request is
    compliant ONLY if it declines without itself providing the disallowed
    advice, action, or information, and does not misstate what Horizon can
    or cannot do beyond the Regulatory Context above. Every visible field in
    the payload is in scope.

    Assess the Customer-Facing Answer (including its UI payload) and the
    Customer-Facing Clarification SEPARATELY, since a failure confined to
    one can be corrected without discarding the other.

    Output JSON only:
    {{"passed": true/false, "answer_compliant": true/false, "clarification_compliant": true/false, "reason": "...", "rewrite_instruction": "..."}}
    """
    try:
        result = _parse_json_robustly(call_groq_lpu(prompt, model_id=MODEL_B_AUDITOR))
        answer_compliant = bool(result.get("answer_compliant", False))
        clarification_compliant = bool(result.get("clarification_compliant", False))
        passed = bool(result.get("passed", False)) and answer_compliant and clarification_compliant
    except Exception as error:
        print(f"🚨 [COMPLIANCE AUDIT ERROR]: {error}")
        result, passed = {"reason": "Compliance audit failed.", "rewrite_instruction": "Rewrite the response as neutral, factual Horizon Bank information."}, False
        answer_compliant = clarification_compliant = False
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
    """Focused evidence-entailment audit of the complete customer-facing response."""
    print("🟢 [NODE 06b] Executing Outbound Groundedness Audit")
    answer, clarification, rendered_payload = _outbound_response_content(state)
    selected_knowledge, unapproved_knowledge, auxiliary_sources = _outbound_grounding_sources(state)
    is_initial_greeting = (
        state.get("user_input") in {"__INIT__", "**INIT__"}
        or state.get("preflight_status") == "initial_greeting"
    )
    prompt = f"""
    You are an evidence-groundedness auditor for a read-only Horizon Bank FAQ assistant.

    User Request: "{state.get('user_input', '')}"
    Is Initial Greeting: {is_initial_greeting}

    Selected Approved Knowledge for the Answer:
    "{selected_knowledge}"

    {PRECISION_REFERENCE_CONTRACT}

    Unapproved Retrieved Knowledge Permitted Only for Terminal Retrieval Clarification:
    "{unapproved_knowledge}"

    Intent-clarification and greeting sources:
    "{auxiliary_sources}"

    Customer-Facing Answer:
    "{answer}"

    Customer-Facing Clarification:
    "{clarification}"

    Complete Customer-Facing UI Payload:
    "{rendered_payload}"

    TASK: Assess GROUNDEDNESS ONLY. Do not assess MAS compliance, safety,
    advice, or whether the answer is helpful.

    - For a normal answer, every factual claim, instruction, option, step,
      list item, extended-text statement, and response-block field must be
      explicitly supported by Selected Approved Knowledge. Do not infer a
      detail merely because it is plausible or related.
    - For an initial greeting, assess personalized statements and options only
      against greeting_memory. A first-time generic greeting with no options
      is grounded.
    - For a terminal retrieval clarification, product references and factual
      suggestions must be supported by the Unapproved Retrieved Knowledge.
    - For an intent clarification, every named term must come from intent_tasks
      or the User Request. It must not infer a product, policy, or banking fact.
    - A generic apology or general request for clarification is grounded.
    - A clarification that follows an answer must identify its unresolved term
      rather than use an ambiguous reference such as "that" or "it".
    - Every visible field in the payload is in scope.

    Output JSON only:
    {{"passed": true/false, "answer_grounded": true/false, "clarification_grounded": true/false, "clarification_connected": true/false, "reason": "...", "rewrite_instruction": "..."}}
    """
    try:
        result = _parse_json_robustly(call_groq_lpu(prompt, model_id=MODEL_B_AUDITOR))
        answer_grounded = bool(result.get("answer_grounded", False))
        clarification_grounded = bool(result.get("clarification_grounded", False))
        clarification_connected = bool(result.get("clarification_connected", False))
        passed = bool(result.get("passed", False)) and answer_grounded and clarification_grounded and clarification_connected
    except Exception as error:
        print(f"🚨 [GROUNDEDNESS AUDIT ERROR]: {error}")
        result = {"reason": "Groundedness audit failed.", "rewrite_instruction": "Use only the permitted evidence sources."}
        passed = answer_grounded = clarification_grounded = clarification_connected = False
    audit = {
        "passed": passed,
        "answer_grounded": answer_grounded,
        "clarification_grounded": clarification_grounded,
        "clarification_connected": clarification_connected,
        "reason": result.get("reason", "Groundedness audit failed."),
        "rewrite_instruction": result.get("rewrite_instruction", "Use only the permitted evidence sources."),
    }
    log_event("outbound.groundedness.completed", passed=passed, reason=audit["reason"])
    return {"outbound_groundedness_result": audit}


def node_06_outbound_groundedness_gate(state: AgentState):
    """Audit only the evidence sources permitted for this response event."""
    print("🟢 [NODE 06b] Executing Outbound Groundedness Audit")
    answer, clarification, rendered_payload = _outbound_response_content(state)
    is_initial_greeting = _is_initial_greeting(state)

    if is_initial_greeting:
        greeting_memory = _build_greeting_memory_context(
            state.get("last_session_summary", state.get("working_context", "")),
            state.get("user_baseline_summary", ""),
        )
        prompt = f"""
        GREETING GROUNDEDNESS AUDIT
        You are an evidence-groundedness auditor for a read-only Horizon Bank FAQ assistant.

        Greeting Memory:
        "{greeting_memory}"

        Customer-Facing Greeting:
        "{answer}"

        Complete Customer-Facing UI Payload:
        "{rendered_payload}"

        TASK: Assess GROUNDEDNESS ONLY for this initialization greeting.
        - Every recalled statement and suggested chip must be supported by Greeting Memory.
        - A generic first-time greeting with no recalled facts or chips is grounded.
        - Do not assess banking-answer factuality, compliance, helpfulness, or a normal-turn clarification.
        - Every visible field in the greeting payload is in scope.

        Audit-Feedback Rules:
        - grounding_violations is internal validation data, never customer-facing text.
        - Report only unsupported visible content and the specific evidence that
          is missing. Do not draft a rewrite or recommend an alternative action,
          support channel, product, service, escalation path, or remedy.
        - The evidence_gap describes an absence in the permitted source. It is
          not evidence and must not be used to support a new customer claim.

        Output JSON only:
        {{"passed": true/false, "answer_grounded": true/false, "clarification_grounded": true, "clarification_connected": true, "reason": "...", "grounding_violations": [{{"task_id": "", "scope": "answer" | "ui_payload" | "clarification", "violation_type": "unsupported_claim" | "unsupported_instruction" | "wrong_task_evidence" | "unsupported_clarification_suggestion" | "missing_decline_reference" | "task_not_answered" | "other", "unsupported_content": ["verbatim customer-facing phrase"], "evidence_gap": "missing permitted evidence only; no proposed remedy"}}]}}
        """
        audit_scope = "greeting"
    else:
        grounding_context = _normal_outbound_grounding_context(state)
        prompt = f"""
        ANSWER GROUNDEDNESS V2 AUDIT
        You are an evidence-groundedness auditor for a read-only Horizon Bank FAQ assistant.

        Task-Level Approved Evidence (the only factual source for answers and answer UI):
        {json.dumps(grounding_context["approved_by_task"], ensure_ascii=False)}

        {PRECISION_REFERENCE_CONTRACT}

        Optional Internal Response-Element Provenance (not customer-facing):
        {json.dumps(grounding_context["response_block_map"], ensure_ascii=False)}

        Intent Clarification Inputs (the only source for intent clarification):
        {json.dumps(grounding_context["intent_clarifications"], ensure_ascii=False)}

        Terminal Retrieval Clarification Evidence (permitted only for factual related-topic suggestions in terminal clarification):
        {json.dumps(grounding_context["terminal_clarifications"], ensure_ascii=False)}

        Decline Inputs (the only source for a declined-topic explanation):
        {json.dumps(grounding_context["decline_inputs"], ensure_ascii=False)}

        Customer-Facing Answer:
        "{answer}"

        Customer-Facing Clarification:
        "{clarification}"

        Complete Customer-Facing UI Payload:
        "{rendered_payload}"

        TASK: Assess GROUNDEDNESS ONLY. Do not assess MAS compliance, safety,
        advice, helpfulness, completeness, or whether the response used every
        available source.

        ANSWER AUDIT: assess the customer's natural answer as one coherent
        response. Do NOT require it to be written in separate factual and
        inferential sections. Instead, distinguish the following two internal
        checks while reading the same natural answer and answer UI.

        1. Direct Factual Grounding (Evidence Check)
        - Every explicit factual assertion, parameter, eligibility rule,
          product capability, policy condition, limit, date, amount,
          placeholder, instruction, option, progress step, list item,
          extended-text statement, and answer response-block field must be
          directly supported by Task-Level Approved Evidence.
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

        Clarification Rules:
        - For an intent clarification, every named unresolved term must come
          only from Intent Clarification Inputs. Do not infer a product, policy,
          or banking fact.
        - For a terminal retrieval clarification, a factual related-topic
          suggestion must be supported by the matching task's Terminal Retrieval
          Clarification Evidence. Those records must never support an answer,
          instruction, UI action, or answer block.
        - A generic apology and a general request for clarification are grounded.
        - A brief, non-factual conversational acknowledgement, greeting, or
          banking-scope statement is grounded when it introduces no banking
          fact, procedure, product, service capability, or next action.
        - A clarification following an answer must identify its unresolved term
          instead of using an ambiguous reference such as "that" or "it".
        - For a decline explanation, only the exact subquery named in Decline
          Inputs may be referenced; it must not assert a specific Horizon
          product, policy, fee, or commitment as the reason, and must never
          provide the declined advice, action, or information itself. Every
          entry in Decline Inputs must be explicitly named somewhere in the
          clarification, not silently omitted — treat a missing entry as
          equivalent to an unidentified reference for clarification_connected.

        Audit-Feedback Rules:
        - grounding_violations is internal validation data, never customer-facing text.
        - For every material failure, identify the exact visible phrase in
          unsupported_content and set audit_layer to evidence or bridge.
        - For evidence failures, explain which direct factual support is
          missing. For bridge failures, explain the unsupported logical leap
          or added premise. If both layers fail, report separate entries.
        - If all of the answer is unsupported, say so explicitly in reason and
          include the material unsupported phrases. Do not use vague feedback
          such as "not grounded" without identifying why.
        - Do not draft a rewrite or recommend an alternative action, support
          channel, product, service, escalation path, or remedy.
        - The evidence_gap describes an absence in the permitted source. It is
          not evidence and must not be used to support a new customer claim.
        - If the approved evidence does not answer a task, record a
          task_not_answered violation. Do not suggest how Horizon should handle
          the task or where the customer should go next.

        Source-Boundary Rules:
        - Session memory, user baseline memory, chat history, rolling summaries,
          and the literal latest user message are intentionally absent and are
          not grounding sources for this audit.
        - Do not fail a response because it omits information from any available
          source. Assess whether visible claims use a permitted source only.
        - Every visible field in the payload is in scope.

        Output JSON only:
        {{"passed": true/false, "answer_grounded": true/false, "answer_evidence_grounded": true/false, "answer_bridge_grounded": true/false, "clarification_grounded": true/false, "clarification_connected": true/false, "reason": "...", "grounding_violations": [{{"task_id": "task_1", "scope": "answer" | "ui_payload" | "clarification", "audit_layer": "evidence" | "bridge" | "other", "violation_type": "unsupported_factual_claim" | "unsupported_bridge_inference" | "unsupported_instruction" | "wrong_task_evidence" | "unsupported_clarification_suggestion" | "missing_decline_reference" | "task_not_answered" | "other", "unsupported_content": ["verbatim customer-facing phrase"], "evidence_gap": "missing permitted evidence only; no proposed remedy"}}]}}
        """
        audit_scope = "normal_response"

    try:
        result = _parse_json_robustly(call_groq_lpu(prompt, model_id=MODEL_B_AUDITOR))
        answer_grounded = bool(result.get("answer_grounded", False))
        answer_evidence_grounded = bool(result.get("answer_evidence_grounded", answer_grounded))
        answer_bridge_grounded = bool(result.get("answer_bridge_grounded", answer_grounded))
        clarification_grounded = bool(result.get("clarification_grounded", False))
        clarification_connected = bool(result.get("clarification_connected", False))
        if audit_scope == "normal_response":
            answer_grounded = answer_grounded and answer_evidence_grounded and answer_bridge_grounded
        passed = bool(result.get("passed", False)) and answer_grounded and clarification_grounded and clarification_connected
    except Exception as error:
        print(f"🚨 [GROUNDEDNESS AUDIT ERROR]: {error}")
        result = {
            "reason": "Groundedness audit failed.",
            "grounding_violations": [{
                "task_id": "",
                "scope": "answer",
                "violation_type": "other",
                "unsupported_content": [],
                "evidence_gap": "The groundedness audit was unavailable; no additional customer-facing facts or actions may be added.",
            }],
        }
        passed = answer_grounded = answer_evidence_grounded = answer_bridge_grounded = clarification_grounded = clarification_connected = False

    grounding_violations = _normalize_grounding_violations(result.get("grounding_violations", []))

    audit = {
        "passed": passed,
        "answer_grounded": answer_grounded,
        "answer_evidence_grounded": answer_evidence_grounded,
        "answer_bridge_grounded": answer_bridge_grounded,
        "clarification_grounded": clarification_grounded,
        "clarification_connected": clarification_connected,
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
    return "The current banking conversation."


def _escalation_case_problem(state: AgentState) -> str:
    """Provide a concise deterministic handoff reason without exposing diagnostics."""
    if state.get("triage_status") == "ESCALATION":
        return "You asked to continue with a human agent."
    if state.get("safety_retry_count", 0) >= 3:
        return "The assistant could not safely complete the request after its validation checks."
    return "A human review has been requested for this conversation."


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
    prompt = f"""
    CURRENT SESSION ESCALATION CASE SUMMARY
    You create a concise handoff brief that saves a human banking agent time
    understanding the customer's unresolved case. This is NOT a chronological
    recap of the session.

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
                "title": "Confirm transfer to a human agent",
                "caseTitle": "Current case summary",
                "request": case_summary["request"],
                "problem": case_summary["problem"],
                "unresolvedIssues": case_summary["unresolvedIssues"],
                "notice": "This AI-generated summary helps a human agent quickly understand your case.",
                "confirmation": {
                    "label": "Confirm transfer",
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
        "error_malicious": "We cannot process your request. Please try rephrasing or ask another question.",
        "fail": "Sorry, I’m unable to respond to that right now. Please try again or rephrase your question."
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
