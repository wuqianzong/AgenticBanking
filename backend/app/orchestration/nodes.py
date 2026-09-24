import copy
import json
import re
import secrets
import sqlite3
import os
import asyncio
from datetime import datetime, timezone
from typing import Any, Literal, Optional
from pydantic import create_model
from langchain_core.messages import HumanMessage, AIMessage
from backend.app.orchestration.state import AgentState
from backend.app.orchestration.rag_engine import RAGEngine
from backend.app.orchestration.answer_cache import get_answer_cache
from backend.app.services.memory_service import LongTermMemoryManager
from backend.app.core.llm_service import (
    call_groq_lpu,
    parse_json_robustly as _parse_json_robustly,
    ClarificationGuess,
    TerritoryBPlan,
    TurnConsistencyEvaluation,
    MODEL_B_AUDITOR,
    MODEL_C_PLANNER,
    MODEL_D_PROMPT_GUARD,
    MODEL_E_TRIAGE,
)
from backend.app.core.groundedness import check_evidence_groundedness, check_conduct_compliance
from backend.app.core.compliance import REGULATORY_LICENSING_CONTEXT, BANK_CONDUCT_CONTEXT, PRECISION_REFERENCE_CONTRACT, PERSONAL_DATA_REFERENCE_CONTRACT
from backend.app.core.translations import get_message
from backend.app.core.system_logger import log_event
from backend.app.orchestration.tool_registry import enabled_tool_capabilities, is_registered_operation, TOOL_REGISTRY
from backend.app.orchestration.service_request_registry import get_service_request_contract
from backend.app.orchestration.deeplink_destination_registry import DEEPLINK_DESTINATION_REGISTRY
from backend.app.orchestration.api_field_registry import API_FIELD_REGISTRY
from backend.app.services import service_request_service
from backend.app.services import deeplink_service
from backend.app.services.inbound_safety_service import find_sensitive_word_rule_ids

# Define the DB Path (adjust relative path if necessary based on your working directory)
SQLITE_DB_PATH = "backend/data/knowledge_base.db"
ESCALATION_CONFIRMATION_MESSAGE = "Yes, transfer me to a human agent."

# --- 🧹 VOLATILE STATE RESET DICTIONARY ---
# Applied at the start of every turn to prevent cross-turn memory leakage
VOLATILE_STATE_RESET = {
    "safety_retry_count": 0,
    "search_feedback_history": [],
    "draft_feedback_history": [],
    "consistency_suppressed_ids": [],
    "generation_obligations": {},
    "triaged_subqueries": [],
    "financial_subqueries": [],
    "conversational_tasks": [],
    "tool_planning_round": 0,
    "tool_plan_status": "continue",
    "decline_tasks": [],
    "unresolvable_search_tasks": [],
    "search_tasks": [],
    "task_results": [],
    "task_retry_counts": {},
    "deferred_retry_task_ids": [],
    "task_feedback": {},
    "task_sufficiency_history": {},
    "task_partial_evidence": {},
    "task_partial_guidance": {},
    "task_rejected_parent_ids": {},
    "task_cache_injected_parent_ids": {},
    "clarification_tasks": [],
    "task_resolution_instructions": [],
    "safe_answer_for_fallback": "",
    "final_answer": "",
    "final_clarification": "",
    "selected_ux_journey": "TEXT",
    "presentation_plan": {},
    "outbound_compliance_result": {},
    "outbound_groundedness_result": {},
    "approved_response_blocks": [],
    "pending_task_ids": [],
    "response_wide_violation_this_round": False,
    "task_violation_history": {},
    "active_tool_routes": [],
    "pending_advance": False,
    "service_request_event": {},
    "last_session_transcript": [],
    "personal_data_reveal_map": {},
    "repeated_need_detected_this_turn": False,
    "low_confidence_mode": False,
    "live_task_confidence": None,
}


def _fresh_volatile_state_reset() -> dict:
    """A per-turn copy of VOLATILE_STATE_RESET with its own list/dict objects.

    Spreading the module-level dict directly (`**VOLATILE_STATE_RESET`)
    copies only references, so every turn's reset shared the same `[]`/`{}`
    objects -- one in-place `.append` anywhere then leaked into every later
    turn in the process (BUG-032: a gate veto's feedback reached 171 later
    sessions). A deep copy per turn means no code path can reintroduce that,
    whatever it does to the lists it is handed.
    """
    return copy.deepcopy(VOLATILE_STATE_RESET)

MAX_VERIFIED_TASK_HISTORY_TURNS = 10
# Soft escalation (node_08): rolling window for the confidence and
# repeated-need signals. Deliberately the same size as
# MAX_VERIFIED_TASK_HISTORY_TURNS -- a repeat or a confidence dip that
# hasn't recurred within this many turns is no longer an active pattern.
MAX_ESCALATION_HISTORY_TURNS = 10
# RAG's own effort-gating hypothesis test (node_03c) needs a longer sample
# than node_08's 10-turn recency window -- 30 is the sample size this
# test's design was specified against, and the cap the rolling sample
# never grows past (a t-test, unlike the z-approximation this constant
# originally justified, is valid at any sample size -- see
# CONFIDENCE_TEST_T_CRITICAL below -- but a sample doesn't need to be
# infinite for the test to be meaningful, and 30 recent data points is
# already enough to detect a real, sustained quality change). The test's
# own sampling unit is one SUBQUERY's 1.0/0.5/0.0 grade, not a turn's
# averaged grade (see _historical_subquery_scores) -- a turn with several
# subqueries contributes several points toward this cap, not one.
# turn_escalation_history itself is still stored turn-by-turn at
# HYPOTHESIS_TEST_SAMPLE_SIZE turns of length (comfortably more raw
# subquery scores than the cap in nearly every real case) so both this
# window and node_08's unrelated one can be read from the same underlying
# list -- node_08 explicitly slices back down to MAX_ESCALATION_HISTORY_TURNS
# for its own purpose.
HYPOTHESIS_TEST_SAMPLE_SIZE = 30
# One-tailed critical t-values, keyed by [alpha][degrees_of_freedom],
# standard published table values (df = n - 1). Used instead of a normal
# (z) approximation: the population standard deviation is never known
# here, only estimated from the sample, which is exactly the situation a
# t-test -- not a z-test -- is the correct tool for, at ANY sample size,
# not just once n is large. The t-distribution's own critical value
# shrinks toward the z-value as df grows (converging by df=30), so this
# is a strict generalization, not a separate small-sample correction
# bolted onto the earlier z-only version: using the t-table's own
# asymptotic values in place of a hardcoded z-table would give the
# identical answer for a large sample, while a small one now gets an
# appropriately wider, more conservative critical value instead of the
# same 1.645 regardless of how little data actually supports it.
# Covers df=1..29; MIN_SCORED_SUBQUERIES_FOR_CONFIDENCE_TRUST guards
# against relying on the low end of this table, and CONFIDENCE_TEST_Z_CRITICAL
# is the asymptotic (df>=30) fallback once a sample fills HYPOTHESIS_TEST_SAMPLE_SIZE.
CONFIDENCE_TEST_T_CRITICAL = {
    0.10: {
        1: 3.078, 2: 1.886, 3: 1.638, 4: 1.533, 5: 1.476, 6: 1.440, 7: 1.415, 8: 1.397, 9: 1.383, 10: 1.372,
        11: 1.363, 12: 1.356, 13: 1.350, 14: 1.345, 15: 1.341, 16: 1.337, 17: 1.333, 18: 1.330, 19: 1.328, 20: 1.325,
        21: 1.323, 22: 1.321, 23: 1.319, 24: 1.318, 25: 1.316, 26: 1.315, 27: 1.314, 28: 1.313, 29: 1.311,
    },
    0.05: {
        1: 6.314, 2: 2.920, 3: 2.353, 4: 2.132, 5: 2.015, 6: 1.943, 7: 1.895, 8: 1.860, 9: 1.833, 10: 1.812,
        11: 1.796, 12: 1.782, 13: 1.771, 14: 1.761, 15: 1.753, 16: 1.746, 17: 1.740, 18: 1.734, 19: 1.729, 20: 1.725,
        21: 1.721, 22: 1.717, 23: 1.714, 24: 1.711, 25: 1.708, 26: 1.706, 27: 1.703, 28: 1.701, 29: 1.699,
    },
    0.01: {
        1: 31.821, 2: 6.965, 3: 4.541, 4: 3.747, 5: 3.365, 6: 3.143, 7: 2.998, 8: 2.896, 9: 2.821, 10: 2.764,
        11: 2.718, 12: 2.681, 13: 2.650, 14: 2.624, 15: 2.602, 16: 2.583, 17: 2.567, 18: 2.552, 19: 2.539, 20: 2.528,
        21: 2.518, 22: 2.508, 23: 2.500, 24: 2.492, 25: 2.485, 26: 2.479, 27: 2.473, 28: 2.467, 29: 2.462,
    },
}
# Asymptotic (df -> infinity) fallback for any df past the table above --
# i.e. once a sample reaches HYPOTHESIS_TEST_SAMPLE_SIZE (30), this is
# what the t-critical value has already converged to in the table's own
# final few rows (1.699 at df=29 vs 1.645 here -- close, and would keep
# closing further past df=29 if the table extended).
CONFIDENCE_TEST_Z_CRITICAL = {0.10: 1.282, 0.05: 1.645, 0.01: 2.326}
CONFIDENCE_TEST_ALPHA = 0.05
# Business decision, not a statistical one -- but grounded in this
# system's own real, achievable baseline rather than an aspirational round
# number. RAG_RETRY_EFFORT_v1.0.md §2's historical funnel data (333 real
# outcomes) shows rounds 1-2 alone resolve roughly two-thirds of tasks
# outright and capture 93-94% of every eventual success; a 0.9 target
# (tried first) sat so far above what this system actually produces that
# the test rejected H0 almost unconditionally for a normally-performing
# customer, making it a near-constant "extend" signal rather than a real
# discriminator between typical and genuinely degraded quality (confirmed
# live: a real customer's session showed mean~0.68-0.75 turn after turn,
# always rejecting against 0.9). 0.75 sits at this system's actual typical
# operating point instead, so the test can tell "performing normally" from
# "performing worse than normal" again. Also changes small-sample
# calibration for the better, since 0.75 is further from the 1.0 ceiling
# every subquery score is bounded by than 0.9 was -- see
# MIN_SCORED_SUBQUERIES_FOR_CONFIDENCE_TRUST below and
# RAG_RETRY_EFFORT_v1.0.md §4e.
CONFIDENCE_TEST_POPULATION_MEAN_H0 = 0.75
SOFT_ESCALATION_SESSION_DURATION_SECONDS = 10 * 60
SOFT_ESCALATION_SAME_INTENT_STREAK = 20
SOFT_ESCALATION_CONFIDENCE_THRESHOLD = 0.5
SOFT_ESCALATION_REPEATED_NEED_TURN_COUNT = 3
# Used by node_08's own _rolling_bot_confidence gate (a plain average, not
# a hypothesis test -- node_03c's own gate is MIN_SCORED_SUBQUERIES_FOR_
# CONFIDENCE_TRUST below, a different constant for a different sampling
# unit): don't trust the rolling average until at least this many scored
# turns exist. Confirmed live that 1-2 turns of history swings the average
# by a full 0.5 on a single result, which is noise, not a track record.
# Below this count, node_08 never fires its confidence rule, regardless of
# what the raw (unreliable) average says.
MIN_SCORED_TURNS_FOR_CONFIDENCE_TRUST = 4
# node_03c's hypothesis test floor, in SUBQUERY-scored data points, not
# turns. Re-verified by Monte Carlo simulation each time
# CONFIDENCE_TEST_POPULATION_MEAN_H0 changes (RAG_RETRY_EFFORT_v1.0.md
# §4e), since a subquery score only ever takes one of three discrete
# values (1.0/0.5/0.0) and how close H0 sits to the 1.0 ceiling directly
# controls how badly a small sample of such scores miscalibrates the
# t-test. At the current H0 (0.75, comfortably inside the scale rather
# than pinned near its ceiling), the worst plausible score distribution's
# empirical false-positive rate falls to roughly 4-7% (against a nominal
# 5% alpha) by n=15 and stays in that range through n=30 -- so 15 is used
# here rather than requiring a full 30-point window, which an earlier,
# much higher H0 (0.9) did require. Below this floor, only
# has_repeated_need_this_turn can unlock extra effort past the
# unconditional NORMAL_EFFORT_RAG_ATTEMPTS floor -- the same "not enough
# track record to trust a statistic, but a customer visibly repeating
# themselves is still worth trying harder for" stance node_08 takes with
# its own, separate floor above.
MIN_SCORED_SUBQUERIES_FOR_CONFIDENCE_TRUST = 15
# Deliberately higher than SOFT_ESCALATION_CONFIDENCE_THRESHOLD, not the same
# value: an extra RAG round is cheap and invisible to the customer, while
# offering human escalation is not, so the cheap lever should trigger
# earlier (at a less severe confidence dip) than the expensive/visible one.
# This guarantees extra effort is always already active by the time
# escalation is offered (0.7 > 0.5), never the other way around, and gives
# the bot a silent chance to recover in the 0.5-0.7 band before anything
# customer-facing changes.
EXTRA_EFFORT_CONFIDENCE_THRESHOLD = 0.7
# A turn's own (unwindowed) confidence lands in this band exactly when
# Tool Calling v3.0's own schema is itself ambiguous about whether the
# response was good enough -- not confidently RESOLVED, not confidently
# empty. That is precisely the case worth asking the customer to break the
# tie on, rather than a turn the schema is already fairly sure about.
TURN_FEEDBACK_SCORE_LOWER_BOUND = 0.5
TURN_FEEDBACK_SCORE_UPPER_BOUND = 0.85
# TEMPORARY: show the thumbs prompt on every scored turn, bypassing the band
# above, for broader feedback collection during testing. Set back to False to
# restore the band-gated behavior -- TURN_FEEDBACK_SCORE_LOWER_BOUND/
# UPPER_BOUND are left untouched below so reverting is a one-line change.
TURN_FEEDBACK_SHOW_ON_ANY_SCORE = True
ANSWERABLE_TASK_STATUSES = {"SUFFICIENT", "RELEVANT"}
# Extra Effort v2.1: three named effort tiers, each with its own explicit
# gate -- "always" (unconditional) or "statistical" (requires
# extend_if_optional, node_03c's confidence hypothesis test). Moving a
# tier's gate (e.g. if production data someday shows Territory A needs
# gating too) or adding a fourth tier is a change to this table alone,
# never a restructuring of the should_retry logic that reads it. See
# RAG_RETRY_EFFORT_v1.0.md §8c/§8d.
#   Tier 1 (Wave 1): narrow product/intent, all 4 representations searched
#     (procedural/policy/conceptual/direct) as two independent tracks --
#     semantic angle-group RRF, and direct query on its own -- unioned, not
#     RRF-fused together. See execute_multi_angle_search.
#   Tier 2 (Territory A): SAME query representation, filter widened to
#     include alternate_products (reasoned once, at fresh-formulation time,
#     never re-derived here -- see SearchTask.alternate_products) and full
#     intent scope across every included product. Deterministic, no LLM
#     call. Unconditional: this tier's own cost dropped enough (no retry-time
#     reasoning needed) that it no longer needs the statistical gate. This
#     is also a task_id's own hard ceiling -- MAX_RAG_ATTEMPTS below --
#     since a task never gets a further attempt on ITSELF past this tier.
#   Tier 3 (Territory B): NOT a further attempt on the SAME task_id -- it
#     always spawns a brand-new, separately-tracked companion task_id
#     (f"{source}_scope"), constructed alongside Tier 2 in the SAME retry
#     pass (node_02e), not a later, sequential round -- a task that
#     terminates after Tier 2 has no live "RETRY" status left for a later
#     round to hang off of. MAXIMUM_EFFORT_ATTEMPT therefore exists purely
#     as an EFFORT_TIER_GATE key, checked directly via
#     _attempt_unlocked(MAXIMUM_EFFORT_ATTEMPT, extend_if_optional) --
#     never compared against any task's own attempt_count. The only tier
#     that still needs an LLM call, and only for a task with no
#     direct-adopt companion already covering it (see node_03c's
#     direct-adopt block, which handles the evidence-grounded case for
#     free, outside this tier system entirely, since it costs nothing).
NORMAL_EFFORT_ATTEMPT = 1
EXTRA_EFFORT_ATTEMPT = 2
MAXIMUM_EFFORT_ATTEMPT = 3
MAX_RAG_ATTEMPTS = EXTRA_EFFORT_ATTEMPT
EFFORT_TIER_GATE = {
    NORMAL_EFFORT_ATTEMPT: "always",
    EXTRA_EFFORT_ATTEMPT: "always",
    MAXIMUM_EFFORT_ATTEMPT: "statistical",
}


def _attempt_unlocked(next_attempt_number: int, extend_if_optional: bool) -> bool:
    """Whether a task currently at (next_attempt_number - 1) attempts may
    advance to next_attempt_number, per EFFORT_TIER_GATE above."""
    gate = EFFORT_TIER_GATE.get(next_attempt_number, "statistical")
    return gate == "always" or (gate == "statistical" and extend_if_optional)
MAX_TOOL_PLANNING_ROUNDS = 3
MAX_TOOL_TASKS_PER_TURN = 8


# --- 🔧 TOOL CALLING v3.0: SUBQUERY-OWNED ATTEMPT SCHEMA PRIMITIVES ---
# See docs/TOOL_CALLING_v3.0.md. Every financial_subquery entry owns a
# `tool_attempts` list; these are the only primitives allowed to interpret
# an attempt's status. No other code should re-derive these judgments.

def _build_attempt_id(subquery_id: str, tool_name: str, round_num: int, sequence: int) -> str:
    return f"{subquery_id}::{tool_name}::r{round_num}::{sequence}"


def _fully_covers(attempt: dict) -> bool:
    """Whether one attempt, on its own, fully answers its owning subquery.

    `api`/`deeplink`/`service_request` are atomic and binary by construction
    -- `completed` always means fully resolved. `rag` is the one tool whose
    `completed` is coarser than that: it only means "the retry loop ended
    with some approved evidence," which may be SUFFICIENT or merely
    RELEVANT. Only SUFFICIENT counts as full coverage here.
    """
    if not isinstance(attempt, dict):
        return False
    if attempt.get("tool_name") == "rag":
        return attempt.get("result", {}).get("retrieval_state") == "SUFFICIENT"
    return attempt.get("status") == "completed"


def _resolved_by_other_tool(subquery: dict) -> bool:
    """Whether a NON-rag attempt on this subquery already fully covers it.

    Deliberately narrower than _compute_subquery_status: this is called
    from within RAG's own execution for this exact subquery (its
    formulation in node_02e, or its sufficiency audit in node_03c), where
    RAG's own attempt is still "pending"/"running" by construction --
    _compute_subquery_status would see that pending attempt and return
    PENDING before ever checking whether something else already resolved
    it, which is exactly the wrong answer here (confirmed live: a
    completed "kyc-document-resubmission" service_request sitting right
    next to RAG's still-pending attempt was reported as unresolved because
    of this). Excluding "rag" here removes that self-reference entirely.
    """
    if not isinstance(subquery, dict):
        return False
    return any(
        _fully_covers(attempt)
        for attempt in subquery.get("tool_attempts", [])
        if isinstance(attempt, dict) and attempt.get("tool_name") != "rag"
    )


def _compute_subquery_status(subquery: dict) -> str:
    """Deterministic rollup -- never a second LLM judgment.

    PENDING: at least one attempt is still pending/running this turn.
    RESOLVED: at least one attempt fully covers the subquery on its own.
    OPEN: every attempt is terminal, but none fully covers it. By the time
    response generation runs, the tool-planning loop has already ended, so
    OPEN at that point functionally means "exhausted" -- there is no
    separate stored EXHAUSTED value, since nothing would ever read it
    differently from OPEN once the loop is over.
    """
    attempts = subquery.get("tool_attempts", []) if isinstance(subquery, dict) else []
    if any(isinstance(a, dict) and a.get("status") in {"pending", "running"} for a in attempts):
        return "PENDING"
    if any(_fully_covers(a) for a in attempts):
        return "RESOLVED"
    return "OPEN"


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
    """Extra Effort v2.1: only one retry pass exists now (attempt 2 -- see
    MAX_RAG_ATTEMPTS). Territory A (deterministic filter-widen of the
    existing task, using its own pre-computed alternate_products) is
    applied directly via _expand_intent_to_full, never requested from the
    model; the model's only retry-mode job, when node_03c's
    territory_b_unlocked says it's worth it, is constructing a task's
    Territory B (scope-expansion) companion -- see node_02e's retry-mode
    prompt."""
    plan = []
    for task in retry_tasks:
        next_attempt = retry_counts.get(task.get("task_id"), 0) + 1
        plan.append({"task_id": task.get("task_id"), "next_attempt": next_attempt, "stage": "EXTRA_EFFORT"})
    return plan


def _retrieval_mode_for_task(task: dict, retry_counts: dict[str, int]) -> str:
    """Extra Effort v2.0: always angle-group RRF, from the first attempt.

    A standalone "DIRECT" mode used to run for the first three attempts,
    testing only one query representation (the resolved subquery text)
    while varying the metadata filter (intent, then product) -- meaning the
    other three representations node_02e already authors every task with
    (procedural/policy/conceptual) sat unused until the fourth and final
    attempt. Now every attempt -- including the first -- tests all four
    representations, under whatever scope that attempt's filter currently
    allows. They no longer land in one fused ranking, though: the direct
    query used to be folded in as a fourth angle of the same RRF sum as
    the three semantic angles, but that let a strong literal-match parent
    get diluted by the other three and then pruned by the 50%-drop-off
    rule. `execute_multi_angle_search` now runs the semantic angle-group
    RRF and the direct query as two independent tracks, each selected on
    its own scores, then unions the two selections. See
    RAG_RETRY_EFFORT_v1.0.md S8f.
    """
    return "ANGLE_GROUP_RRF"


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


_DECLINE_CATEGORY_REASONS = {
    "non_compliant": "it is outside the assistance I can provide under banking compliance requirements",
    "non_financial": "I can only help with Horizon Bank-related questions",
    "out_of_scope_product": "it's not a product or service Horizon currently offers",
    "individual_customers_only": "this assistant's information covers individual, personal banking products only — business or corporate banking questions aren't covered here",
}

# Standalone, complete sentences for the SAME two categories -- deliberately
# a separate dict from _DECLINE_CATEGORY_REASONS above, whose entries are
# sentence fragments meant only for that dict's own "Sorry, for {scope}, I
# can't provide an answer — {reason}." template. These are used as
# "evidence" fed to response generation (see node_02e's DECLINE resolution
# and _generate_response_blocks/_generate_evidence_only_response): the
# wording is deliberately never LLM-generated, since nothing downstream
# re-verifies evidence text itself for accuracy, only that the final
# response stays faithful to it. Each reads as a complete, ready-to-relay,
# first-person assistant statement -- not a fragment, and not phrased as an
# internal status note -- specifically so a response-generation or
# groundedness-audit model has nothing to interpret or reword beyond
# quoting or lightly connecting it.
_DECLINE_CATEGORY_EVIDENCE = {
    "out_of_scope_product": "This is not a product or service Horizon offers.",
    "individual_customers_only": "This assistant's information covers individual, personal banking products only; business or corporate banking questions aren't covered here.",
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


def _decline_evidence_text(category: str) -> str:
    """The deterministic, standalone decline sentence for a given category
    -- see _DECLINE_CATEGORY_EVIDENCE. Never LLM-generated."""
    return _DECLINE_CATEGORY_EVIDENCE.get(
        category, "This is not a product or service Horizon offers."
    )


def _clarification_evidence_text(unresolved_request: str) -> str:
    """A deterministic, standalone clarifying question for an unresolved
    term. Never LLM-generated -- unresolved_request is already a trusted,
    extracted term (not free prose), so this is exactly as safe as a fixed
    template.

    NOT used as the primary customer-facing text for a normal turn -- see
    _clarification_fallback_text below. Generation composes its own
    question from unresolved_request/scope/clarification_guesses instead,
    so the customer's own language, tone, and style-personalization
    actually apply, rather than every clarification reading identically
    regardless of context. This template exists only as the deterministic
    safety net (node_11's final fallback, and the completeness net for a
    turn whose generation step omitted or malformed an entry) -- reachable
    without any LLM call, exactly like decline/no-info evidence.
    """
    return f"Could you clarify what you mean by \"{unresolved_request}\"?"


def _evidence_gap_clarification_text(unresolved_term: str) -> str:
    """The deterministic fallback question for node_03c's evidence-authored
    clarifications -- deliberately NOT the same template as
    _clarification_evidence_text above, and subject to the same "fallback
    only" scoping described there.

    unresolved_term names a gap the sufficiency auditor identified while
    reviewing evidence (e.g. "documents for online submission," "which
    loan type you mean") -- it is the auditor's own characterization of
    what's missing, not necessarily a term the customer ever said. Wrapping
    it in "Could you clarify what you mean by X?" (node_02e's template)
    would falsely imply the customer said something vague; this phrasing
    instead asks for the missing detail directly, which reads correctly
    whether or not the customer's own wording resembles unresolved_term.
    """
    return f"Could you tell us more about {unresolved_term}?"


def _clarification_fallback_text(entry: dict) -> str:
    """The deterministic customer-facing sentence for a clarification entry,
    computed on demand -- never precomputed or stored on the persisted
    `clarification_tasks` contract itself. Used only by node_11's final
    fallback and the deterministic completeness nets in
    `_generate_response_blocks`/`_generate_evidence_only_response`, when
    generation genuinely produced nothing usable for this entry.

    Keeping this out of the stored contract (and out of every generation
    prompt) is deliberate: a ready-made sentence sitting in the prompt
    invites the model to just parrot it, which is exactly why clarification
    used to read identically ("Could you clarify what you mean by X?")
    regardless of context or the customer's own style. The normal path
    composes fresh from `unresolved_request`/`scope`/`clarification_guesses`
    instead; this function exists only for the rare case nothing was
    composed at all.
    """
    unresolved_request = entry.get("unresolved_request", "")
    if entry.get("originator") == "sufficiency_auditor":
        return _evidence_gap_clarification_text(unresolved_request)
    return _clarification_evidence_text(unresolved_request)


def _normalize_clarification_guesses(raw_guesses: list) -> list[dict]:
    """Validate and cap a model-supplied clarification_guesses list to at
    most 3 entries, each requiring label + message; reason survives
    (internal only -- see ClarificationGuess in llm_service.py) so node_06's
    audit can check it, but every customer-facing or generation-prompt-
    facing site that reads a normalized entry rebuilds {label, message}
    explicitly and discards reason at that point, never passing this dict
    through as-is. Shared by every node that can author a clarification
    (node_02e, node_03c) so the same validation can't quietly diverge
    between them.
    """
    clarification_guesses = []
    for guess in raw_guesses or []:
        if not isinstance(guess, dict):
            continue
        label = str(guess.get("label", "")).strip()
        message = str(guess.get("message", "")).strip()
        reason = str(guess.get("reason", "")).strip()
        if label and message:
            clarification_guesses.append({"label": label, "message": message, "reason": reason})
    return clarification_guesses[:3]


_CLARIFICATION_SCOPE_BY_ORIGINATOR = {
    # Fixed, one-line statement of the originating node's own job -- shown
    # to the response generator and node_06's audit so both can see exactly
    # what task the ambiguity is blocking, without needing their own
    # hardcoded originator -> scope mapping.
    "triage": "Classifying whether this specific part of the request is a genuine banking need or a non-financial, tone-only aside.",
    "intent_formulator": "Identifying the correct product and intent for this request, before any evidence retrieval was attempted.",
    "task_dispatcher": "Choosing which registered capability (app navigation, a submitted request, or an account lookup) actually addresses this request.",
    "sufficiency_auditor": "Reviewing whether retrieved evidence fully answers this request.",
}


def _no_info_evidence_text(resolved_subquery: str, need: str = "") -> str:
    """The deterministic statement for a search that was genuinely
    attempted but found nothing usable (see _terminal_retrieval_feedback,
    resolution_status == CLARIFICATION_REQUIRED). Never LLM-generated --
    same reasoning as decline/clarification evidence above. Phrased as a
    complete, ready-to-relay, first-person assistant statement.

    BUG-035: it names the owning subquery's `need` (triage's short noun
    phrase, e.g. "the current balance of your savings account") when one
    exists, instead of quoting `resolved_subquery` -- which can be the
    customer's whole message and read back as "I don't have information
    available regarding: I need to know about my savings account. Would you
    please check my current balance?.". Both inputs are the same triage
    output, so this is no less deterministic than before. Without a need
    (older state, or the model omitted it) the old form is kept, minus the
    doubled end punctuation.
    """
    need = str(need or "").strip().rstrip(" .?!")
    if need:
        return f"I don't have information available on {need}."
    request = str(resolved_subquery or "").strip().rstrip(" .?!")
    if request:
        return f"I don't have information available regarding: {request}."
    return "I don't have information available for this specific request."


def _build_clarification_context(state: AgentState) -> list[dict]:
    """Clarification entries shared verbatim by the response generator and
    node_06's audit context (_normal_outbound_grounding_context) -- one
    definition so the two can never silently diverge on what an entry
    looks like.

    Single source, `clarification_tasks`: one standard contract regardless
    of which node authored the entry --

    - subquery_id: the sole link back to the originating subquery (mutual
      exclusivity in triage's own TOOL_ELIGIBLE/NON_FINANCIAL/CLARIFICATION
      split, node_02e's SEARCH/CLARIFY/DECLINE resolution, and node_03c's
      per-task terminal decision guarantees at most one clarification per
      subquery_id, so this alone is already unique). A triage-originated
      entry uses that same component's own real subquery_id -- triage
      decomposes the whole turn into components before tagging any of
      them, so a CLARIFICATION-tagged component always has one.
    - originator: one of four, one per genuine judgment-making node in the
      pipeline -- "triage" (node_02d, classifying one component of the
      turn as a genuine banking need vs. a non-financial aside, when that
      category is itself genuinely undecidable for that component),
      "intent_formulator" (node_02e, before any retrieval
      was attempted -- the request itself was too ambiguous to form a
      search task), "task_dispatcher" (node_03a, choosing which registered
      tool capability actually addresses an otherwise well-formed
      subquery), or "sufficiency_auditor" (node_03c, after reviewing
      retrieved evidence -- either a RELEVANT task's remaining
      gap was judged worth asking about, or an IRRELEVANT task's total
      failure was judged to be genuine ambiguity rather than a coverage
      gap). Every other node in the pipeline is deliberately excluded --
      node_04b/node_11 relay only, node_06 audits someone else's output,
      node_04a/node_08/node_09 make no judgment call about what the
      customer wants.
    - scope: fixed one-line statement of the originator's own job (see
      _CLARIFICATION_SCOPE_BY_ORIGINATOR) -- where in the pipeline the
      ambiguity is blocking fulfilment.
    - unresolved_request: the specific ambiguous/unresolved part, in the
      originating node's own words -- what generation composes its
      question about.
    - clarification_reason: authoring-time-only. This is purely a forcing
      function on the AUTHORING node ("if you cannot state a concrete
      reason, don't offer this at all" -- see node_02e/node_03c's own
      prompts) -- it is never shown to generation or to node_06's audit
      (see _clarification_prompt_view below). Kept on the full entry only
      for logs/observability/debugging.
    - clarification_guesses: 0-3 grounded guesses at the likely customer
      intent, each with its own internal reason.

    No `clarification_evidence` field is stored here -- see
    _clarification_fallback_text.
    """
    return list(state.get("clarification_tasks", []))


def _clarification_prompt_view(entries: list) -> list[dict]:
    """The subset of a clarification entry any LLM call is ever allowed to
    see -- used identically for the response generator's prompt and
    node_06's audit context, so the two can never drift onto different
    views of the same entry.

    Deliberately excludes `originator` and `clarification_reason`:
    `clarification_reason` is an authoring-time-only forcing function (see
    _build_clarification_context), never anything generation composed
    from -- an auditor checking the final text against information
    generation never had access to would be judging against an unfair,
    invisible ground truth, and could veto an otherwise correct response
    over a mismatch that was never generation's to get right.
    `originator` is dropped too, on the same principle: generation was
    never shown it, so the audit shouldn't privilege it either -- `scope`'s
    own descriptive text already carries whatever distinction matters
    (e.g. whether Product/Intent Matrix grounding is appropriate for a
    guess's reason).
    """
    return [
        {
            "subquery_id": entry.get("subquery_id"),
            "scope": entry.get("scope", ""),
            "unresolved_request": entry.get("unresolved_request", ""),
            "clarification_guesses": entry.get("clarification_guesses", []),
        }
        for entry in entries
    ]


def _build_decline_context(state: AgentState) -> list[dict]:
    """Decline-input entries shared verbatim by the response generator and
    node_06's audit context. A decline_task with an empty subquery is
    dropped here -- every current producer always populates it, so this
    filter should never actually trigger; if it ever does, that decline
    silently disappears from both generation and the audit rather than
    surfacing malformed data, which is worth knowing if a future producer
    of decline_tasks is added without this guarantee."""
    return [
        {
            "task_id": f"decline_{task.get('task_id')}",
            "subquery": task.get("subquery", ""),
            "category": task.get("reason_category", ""),
            "decline_evidence": task.get("decline_evidence", ""),
        }
        for task in state.get("decline_tasks", [])
        if task.get("subquery", "")
    ]


def _build_conversational_context(state: AgentState) -> list[dict]:
    """Conversational-input entries shared verbatim by the response
    generator and node_06's audit context -- triage's NON_FINANCIAL-tagged
    components (see node_02d). Unlike a Decline/No-Info Input, there is no
    fixed evidence field to relay: `customer_component` is only a resolved
    restatement of the aside (a greeting, thanks, feeling, or off-topic
    remark), and generation composes its own brief, natural reply from it,
    the same "compose, don't relay" treatment a Clarification Input already
    gets."""
    return [
        {"subquery_id": task.get("subquery_id"), "customer_component": task.get("customer_component", "")}
        for task in state.get("conversational_tasks", [])
        if task.get("subquery_id") and task.get("customer_component")
    ]


def _is_pure_conversational_turn(state: AgentState) -> bool:
    """True only when this turn is entirely non-financial content -- zero
    TOOL_ELIGIBLE subqueries, zero clarifications, zero declines, and at
    least one conversational entry. Replaces the old whole-turn
    `triage_status == "NON_FINANCIAL"` check (that status no longer exists
    now that triage decomposes per-component): a *mixed* turn (real
    evidence or a clarification alongside a conversational aside) must run
    the normal audit path with the Conversational Rules carve-out instead
    of this narrower one, so this check only fires for a turn that is
    non-financial content and nothing else."""
    return (
        not state.get("financial_subqueries")
        and not state.get("clarification_tasks")
        and not state.get("decline_tasks")
        and bool(state.get("conversational_tasks"))
    )


def _no_info_entries(
    task_resolution_instructions: list, task_metadata: dict, unresolvable_search_tasks: list | None = None
) -> list[dict]:
    """The no-info entries of this turn, shared by the response generator,
    the reviser, and node_06's audit context so all of them compute the
    exact same no_info_evidence text for the same task -- previously the
    generator lacked the audit's task_metadata fallback for a missing
    resolved_subquery, so the two could disagree on wording for the same
    entry and the audit could reject a response that correctly matched
    what the generator actually saw.

    A task whose retries exhausted with zero usable evidence
    (CLARIFICATION_REQUIRED) gets the deterministic no_info_evidence
    treatment. A RELEVANT task is not an entry here: it is evidence, and
    its evidence entry carries `resolution_status` itself (see
    _response_evidence_manifest). It used to also get a separate
    "terminal unresolved task marker" from this function -- a second
    pointer to the same task that outlived its evidence whenever a guard
    removed the evidence, telling the generator evidence existed when it
    didn't (BUG-038).

    A CLARIFICATION_REQUIRED task the sufficiency auditor judged genuinely
    ambiguous never reaches this function at all -- node_03c routes it into
    its own clarification_tasks entry instead (see node_03c's docstring).

    unresolvable_search_tasks (node_02e's own scope-rejected fresh SEARCH
    resolutions, see the scope-rejection handling there) are already in
    this exact no_info_context shape and are folded in here so every
    no_info consumer -- generator and audit alike -- sees them the same
    way it sees a genuinely retry-exhausted RAG task, with no separate
    merge step for callers to forget.
    """
    no_info_context = list(unresolvable_search_tasks or [])
    for instruction in task_resolution_instructions:
        if not isinstance(instruction, dict):
            continue
        if instruction.get("resolution_status", "CLARIFICATION_REQUIRED") != "CLARIFICATION_REQUIRED":
            continue
        task_id = instruction.get("task_id")
        resolved_subquery = instruction.get("resolved_subquery", "") or task_metadata.get(task_id, "")
        need = instruction.get("need", "")
        no_info_context.append({
            "task_id": task_id,
            "resolved_subquery": resolved_subquery,
            "need": need,
            "no_info_evidence": _no_info_evidence_text(resolved_subquery, need),
        })
    return no_info_context


def _append_decline_suffix(response: str, decline_tasks: list) -> str:
    """Terminal safety net (node_11) when an LLM-drafted decline explanation
    fails outbound audit repeatedly. The normal path lets node_04b draft the
    decline explanation directly, so this only ever fires after that
    attempt has already been discarded."""
    if not decline_tasks:
        return response
    suffix = _decline_message(decline_tasks, "the rest of your request")
    return f"{response}\n\n{suffix}" if response else suffix


# BUG-014. A well-formed precision placeholder, and any wrapping of the same
# id at all: bare ("within faq_5ad80c63_001-faq_5ad80c63_002 days"), one,
# three, four... braces on either side, uneven ({{faq_..._001}), or
# whitespace inside the braces ({{ faq_..._001 }}). Whitespace is only
# consumed when it touches a brace, so a bare id in a sentence keeps the
# spaces around it. The lookarounds make every match start at the first
# leading brace and end at the last trailing one (never mid-run), and never
# match a longer token (faq_..._0012) or an id embedded in a word.
_WELL_FORMED_PRECISION_PLACEHOLDER = re.compile(r"\{\{(faq_[0-9a-f]{8}_\d{3})\}\}")
_ANY_WRAPPED_PRECISION_PLACEHOLDER = re.compile(r"(?<![{\w])(?:\{+\s*)?(faq_[0-9a-f]{8}_\d{3})(?:\s*\}+)?(?![}\w])")


def _evidence_precision_placeholders(state: AgentState) -> set:
    """Every precision placeholder id present, well-formed, in this turn's
    approved evidence -- the exact set the generator was given to copy.
    Read from the same hydrated evidence the generator itself read
    (_hydrate_response_evidence), so an id is repairable only if the model
    actually had it in front of it this turn."""
    return {
        placeholder_id
        for entry in _hydrate_response_evidence(state)
        for parent in entry.get("approved_parents", [])
        if isinstance(parent, dict)
        for placeholder_id in _WELL_FORMED_PRECISION_PLACEHOLDER.findall(str(parent.get("content", "")))
    }


def _repair_precision_placeholders(value, evidence_ids: set, repaired: set):
    """Normalize any wrapping of a precision placeholder to exactly
    {{faq_..._NNN}} when its exact id appears in this turn's approved
    evidence (BUG-014), recursively through str/list/dict.

    Repair only: an exact id match against evidence_ids is the sole
    condition -- the brace count around it (zero, one, three, uneven, with
    inner whitespace) never matters. Nothing is guessed (a truncated or
    altered id is left as is), and an id not in the evidence is left
    untouched -- that is an unauthorized placeholder, not a malformed one,
    and is a separate check's job. `repaired` collects each id whose shape
    actually changed (an already well-formed one is not counted), for
    logging."""
    if isinstance(value, str):
        def _fix(match):
            placeholder_id = match.group(1)
            if placeholder_id not in evidence_ids:
                return match.group(0)
            well_formed = "{{" + placeholder_id + "}}"
            if match.group(0) != well_formed:
                repaired.add(placeholder_id)
            return well_formed
        return _ANY_WRAPPED_PRECISION_PLACEHOLDER.sub(_fix, value)
    if isinstance(value, list):
        return [_repair_precision_placeholders(item, evidence_ids, repaired) for item in value]
    if isinstance(value, dict):
        return {key: _repair_precision_placeholders(item, evidence_ids, repaired) for key, item in value.items()}
    return value


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
    need: str = "",
) -> dict:
    """Create a non-evidential marker for a terminally unresolved task.

    Rejected retrieval candidates and evidence-delta facts may inform a retry,
    but they never cross this boundary into planning, generation, or outbound
    groundedness.  The resolved customer request is sufficient to produce a
    safe, non-factual availability notice when the retry budget is exhausted.

    A task the sufficiency auditor already turned into its own evidence-
    authored clarification (see node_03c's clarification_tasks entries)
    never reaches this function at all -- it's fully represented by that
    clarification instead, with nothing left for a terminal marker to add.
    """
    return {
        "task_id": task_id,
        "type": "TERMINAL_RETRIEVAL_FEEDBACK",
        "resolved_subquery": resolved_subquery,
        "resolution_status": resolution_status,
        # The owning triage subquery's need (BUG-035), so every consumer of
        # this marker can name it instead of quoting resolved_subquery.
        "need": need,
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
    # Extra Effort v2.0: a subquery can now own two task_ids (Territory A +
    # a Territory B companion) that both end up ANSWERABLE. Each stays its
    # own manifest entry (never silently merged) but is tagged with its own
    # search_territory, so generation and the outbound audit both know
    # which evidence is the literal answer and which is only related
    # information -- without this tag, a companion's evidence would look
    # identical to a direct answer to whichever node reads it next.
    tasks_by_id = {task.get("task_id"): task for task in state.get("search_tasks", []) if isinstance(task, dict)}
    approved_by_task = {
        result.get("task_id"): (
            set(result.get("approved_parent_ids", [])),
            str(result.get("generation_guidance", "")).strip(),
            result.get("status"),
        )
        for result in state.get("task_results", [])
        if result.get("status") in ANSWERABLE_TASK_STATUSES
    }
    # Cross-tool contradiction guard: a subquery whose need an `api` data
    # fetch has already answered (a personal-data token or a verified fact
    # -- the answer itself, ground truth, not a judgment call) makes a
    # same-subquery RAG task's own RELEVANT verdict (real but incomplete
    # evidence, never a full answer) actively dangerous rather than merely
    # superfluous. Confirmed live (BUG-018): a RELEVANT adjacent_scope
    # task's own required framing ("no direct evidence for this, but here's
    # related information") sat in the same generation_content as a real,
    # already-fetched personal_data_placeholder for the exact same request;
    # the outbound audit's own NARROW_TO_EVIDENCE_SCOPE recovery reasoned
    # from the RAG side alone ("no data for account balances") without
    # checking the tool side, and its resulting generation_guidance
    # instructed denying a capability the turn had already used. A
    # SUFFICIENT verdict is exempt -- it is a real, complete, standalone
    # answer to (a different angle of) the same subquery, genuinely
    # additive rather than a hedge that only makes sense when nothing else
    # answered the subquery at all.
    #
    # `api` only -- never `deeplink` or `service_request` (BUG-037). A
    # redirect (a navigation shortcut or a form) is a path to the
    # resolution, not the resolution itself: it still counts as RESOLVED in
    # the turn's resolution status (_fully_covers, unchanged -- it is
    # different from unresolved), but it delivers no information, so it
    # must never suppress information RAG found. Counting it here removed
    # the correct "6-7 business days" evidence from "When will my physical
    # card arrive?" because a Manage Card shortcut had completed, and the
    # generator, left with no evidence, invented "7 to 10 business days".
    consistency_suppressed = set(state.get("consistency_suppressed_ids", []))
    subqueries_answered_by_data_fetch = {
        subquery.get("subquery_id")
        for subquery in state.get("financial_subqueries", state.get("triaged_subqueries", []))
        if isinstance(subquery, dict)
        for attempt in subquery.get("tool_attempts", [])
        if isinstance(attempt, dict) and attempt.get("tool_name") == "api" and attempt.get("status") == "completed"
    }
    # Presentation is intentionally decoupled from retrieval-task boundaries.
    # The response and outbound groundedness audit therefore share the full
    # approved evidence set. Optional element-level source_task_ids narrow
    # provenance for logs and audits but never decide what may be displayed.
    return [
        {
            "task_id": task_id,
            "approved_parent_ids": sorted(parent_ids),
            "search_territory": tasks_by_id.get(task_id, {}).get("search_territory", "in_scope"),
            # Extra Effort v2.0: node_02e's own stated reason for pivoting
            # to this specific product/topic (SearchTask.mapping_reason).
            # For an adjacent_scope entry this is the ONLY place the actual
            # bridge -- e.g. "buying a car often means saving for a
            # downpayment" -- exists anywhere in the pipeline; without
            # surfacing it, the generator has no way to explain WHY the
            # related evidence is being offered and either invents a
            # weaker connection or drops the evidence in with no
            # connecting sentence at all, reading as a non sequitur to the
            # customer. Present for every entry (not just adjacent_scope)
            # for a uniform manifest shape, but only meaningful as a
            # customer-facing bridge basis for adjacent_scope ones -- an
            # in_scope entry's mapping_reason is just its own internal
            # product/intent justification, never customer-facing.
            "mapping_reason": tasks_by_id.get(task_id, {}).get("mapping_reason", ""),
            # node_03c's own general-purpose writing directive for THIS
            # specific evidence (TaskSufficiencyItem.generation_guidance) --
            # unlike mapping_reason (a formulation-time, scope-expansion-
            # specific justification), this is an audit-time note the
            # sufficiency auditor sets whenever an approved parent's own
            # stated premise or circumstance may not match what the
            # customer actually described. Empty in the common case.
            "generation_guidance": guidance,
            # SUFFICIENT or RELEVANT, carried on the entry itself so every
            # reader (generator, reviser, audit) sees how completely this
            # evidence answers its request from the entry alone. This
            # replaces the separate "terminal unresolved task marker" a
            # RELEVANT task used to get -- a second pointer to the same task
            # that outlived its evidence when a guard removed the evidence,
            # and led the generator to invent the missing fact (BUG-038).
            "resolution_status": status,
        }
        for task_id, (parent_ids, guidance, status) in approved_by_task.items()
        # RELEVANT REQUIRES A REAL PARENT: approved_parent_ids is never
        # empty for a task included here anymore (enforced in
        # node_03c_sufficiency_auditor) -- guidance is always a caveat on a
        # real parent, never a standalone basis for a manifest entry.
        if task_id and parent_ids
        # Cross-tool contradiction guard (see above): drop a RELEVANT-only
        # task whose own subquery an `api` data fetch already answered --
        # Territory B's companion shares its source task's subquery_id
        # (see the "Territory B" comment in node_02e's scope-expansion
        # handling), so this also correctly suppresses a companion task
        # built for a subquery a tool has already resolved.
        if not (status != "SUFFICIENT" and tasks_by_id.get(task_id, {}).get("subquery_id") in subqueries_answered_by_data_fetch)
        # node_04a's turn consistency check: evidence the checker chose to
        # drop from a semantic conflict with another result this turn.
        if task_id not in consistency_suppressed
    ]


def _hydrate_response_evidence(state: AgentState) -> list[dict]:
    """Hydrate the shared outbound evidence manifest for a single consumer."""
    return [
        {**entry, "approved_parents": _hydrate_parent_ids(entry["approved_parent_ids"])}
        for entry in _response_evidence_manifest(state)
    ]


# Unified Response Obligation Contract (see docs/RESPONSE_OBLIGATION_CONTRACT_
# DESIGN.md). `content_mode` is a purely internal, deterministic classification
# -- never shown to any LLM -- fixed by which existing state field an entry
# came from, not by any per-entry judgment. It is used for exactly two things:
# picking which one of these fixed strings to attach as an entry's
# `generation_guidance`, and (a later stage) the completeness net's per-mode
# coverage/fallback dispatch. Once attached, `generation_guidance` already
# fully expresses the instruction in the only form the model reads it in, so
# `content_mode` itself is never serialized into a prompt alongside it --
# doing so would hand the model the same fact twice.
_GENERATION_GUIDANCE_BY_MODE = {
    "compose": (
        "Compose a natural answer from this. Never add a fact, procedure, "
        "or product not present in it."
    ),
    # RELEVANT evidence: real parents that connect to the request without
    # fully resolving it. What it covers is answered like any evidence; the
    # rest gets a neutral availability notice (formerly carried by a
    # separate terminal marker -- see _response_evidence_manifest).
    "compose_partial": (
        "Compose a natural answer from what this covers of the customer's "
        "request -- anything it offers only as related context gets its most "
        "useful point in one or two sentences -- then give a concise, "
        "neutral notice, in the customer's own wording, that the available "
        "information does not cover the rest of their request. Never add a "
        "fact, procedure, or product not present in it, and never fill the "
        "uncovered part yourself."
    ),
    "relay": (
        "This is already the final statement. Rephrase it naturally; never "
        "elaborate on it or treat it as material to reason further from."
    ),
    "ask": (
        "Compose a natural clarifying question about this. Follow "
        "CLARIFICATION RULES below exactly for scope, tone, and how to "
        "work in clarification_guesses -- never assert, assume, or answer "
        "on the customer's behalf."
    ),
    "state": (
        "State or mention this using its own wording; the real UI/action is "
        "attached automatically regardless of your text."
    ),
}

# A second, independent lookup, deliberately NOT keyed by content_mode.
# content_mode groups by "how much creative freedom" (a few buckets) and
# several genuinely different subtask types share one bucket (a shortcut,
# a personal-data token, an unavailable-request reason, and a verified fact
# are all "state" mode) -- but they need different, specific rule sections,
# not one vague shared pointer. This table names, per subtask TYPE, the
# exact section an entry's own `generation_rule` field points to, so every
# entry says precisely which rule section governs it. Two types share a
# name only where their governing text is genuinely identical today
# (decline and no_info; verified facts and evidence, per the existing
# "same footing as approved evidence" instruction) -- never because they
# share a content_mode.
_GENERATION_RULE_BY_TYPE = {
    "evidence": "EVIDENCE RULES",
    "verified_fact": "EVIDENCE RULES",
    "conversational": "CONVERSATIONAL RULES",
    "decline": "DECLINE/NO-INFO RULES",
    "no_info": "DECLINE/NO-INFO RULES",
    "clarification": "CLARIFICATION RULES",
    "shortcut": "SHORTCUT RULES",
    "unavailable_service_request": "UNAVAILABLE SERVICE REQUEST RULES",
    "personal_data": "PERSONAL DATA RULES",
}

# The only obligation kinds _generate_evidence_only_response's narrow
# sub-prompt handles. A turn whose obligations all fall in these routes
# there; any other obligation (evidence, verified fact, shortcut,
# unavailable request, personal data -- or a kind added later) makes the
# turn substantive and keeps it on the full generator (BUG-017).
_EVIDENCE_ONLY_GENERATION_RULES = {
    _GENERATION_RULE_BY_TYPE["clarification"],
    _GENERATION_RULE_BY_TYPE["decline"],
    _GENERATION_RULE_BY_TYPE["no_info"],
    _GENERATION_RULE_BY_TYPE["conversational"],
}

# The seven named sections an entry's own generation_rule field points to.
# Each is fully static -- no per-turn interpolation -- so these are computed
# once, not rebuilt on every call. Every one of them is rendered, when
# rendered at all, strictly after every OBLIGATIONS data section (see
# _generate_response_blocks), and only when at least one obligation of the
# kind(s) it governs exists this turn -- an unused section (e.g.
# CLARIFICATION RULES on a turn with no clarification) is omitted entirely,
# not merely irrelevant filler the model has to read past.
# Prompt-caching layout (DeepSeek bills a repeated prompt PREFIX at ~1/50
# of the normal input price, in 64-token blocks, from the first token up to
# the first difference). Every LLM prompt therefore puts its fixed rules
# first and this turn's data last, separated by this one line so the model
# knows what to expect -- identical wording in every prompt, so it never
# breaks a cached prefix itself.
_DATA_FOLLOWS = """THE RULES ABOVE ARE FIXED. EVERYTHING BELOW IS SPECIFIC TO THIS CALL -- its data, and any rules that apply only to this call. Apply all of them together."""

# Shared by the response generator and the reviser. Added with the move to
# DeepSeek, which -- unlike the terse Gemini flash-lite -- recited every
# related parent in full (three card procedures and a phone number for "I
# can't get my card back"): nothing in the prompt said how much of the
# evidence to use, and several rules only pushed towards including more.
_RESPONSE_PROPORTIONALITY_RULE = """RESPONSE LENGTH: make the reply proportionate to what the evidence
      actually answers. Lead with what directly answers the customer, and
      give that as fully as the customer needs -- a real multi-step
      procedure keeps all its steps. Everything shared only as related
      context (content that does not answer the customer's own situation,
      e.g. from a RELEVANT or adjacent_scope entry) gets its single most
      useful point in one or two sentences, never its full procedure or
      every detail -- the customer can ask for more. State each fact, step,
      contact channel, or caveat once; never repeat what the reply has
      already said."""

_EVIDENCE_RULES_TEXT = """EVIDENCE RULES: every evidence entry's `generation_content.resolution_status`
    says how completely its approved parents answer its request.
    SUFFICIENT: they answer it. RELEVANT: they connect to the request
    without fully resolving it -- state what they do support for the
    customer's request (anything they offer only as related context gets
    its most useful point in one or two sentences, per RESPONSE LENGTH),
    then give a concise, neutral availability notice, in the customer's
    own wording, for the part they don't cover. Do not ask a clarifying question about
    that part or guess what the customer might mean; that judgment belongs
    to the sufficiency auditor. When it judged a question worthwhile, it
    is a separate Clarification Input for the same subquery, which you
    must still render as its own question -- the availability notice
    never replaces it. Never state that Horizon does not offer a product
    or feature, and never ask the customer to repeat an already clear
    request.
    An entry whose `generation_content.search_territory` is
    "adjacent_scope" (Extra Effort v2.0) was retrieved under a different
    product/intent than what the customer's own request was resolved to.
    When its resolution_status is RELEVANT, treat it as RELATED information the
    customer did not literally ask about, surfaced only because their exact
    request wasn't documented -- share only its most useful point, in one
    or two sentences (RESPONSE LENGTH) -- never present it as a direct answer to
    their request, and never blend its claims into the same sentence as an
    "in_scope" (or absent search_territory) entry's direct answer without
    making the distinction clear. That entry's own
    `generation_content.mapping_reason` states WHY this specific related
    topic was chosen (e.g. "buying a car often means saving for a
    downpayment") -- use it as the basis for a natural, specific bridging
    sentence explaining the connection, instead of a generic "here's
    related information" with no stated reason, which reads as a non
    sequitur to the customer. Paraphrase mapping_reason's own connection in
    plain, customer-facing language and visibly hedge it as the assistant's
    own suggestion, never a stated bank fact or policy (e.g. "you may want
    to..." / "since you might be..." / "this could help if..."), since it
    is the assistant's own reasoned inference, not something the customer
    said or something the evidence itself asserts -- never state or imply
    it more strongly than mapping_reason itself supports, and never invent
    a different or additional connection of your own. When this task's own
    literal request also has a paired no-info entry (same
    `generation_content.resolved_request`), open with that limitation
    first, then bridge into the adjacent_scope information in one coherent
    reply -- e.g. "I don't have information on auto financing, but since
    you might be saving for a car's downpayment, here's some information
    about our Savings Account that may help:" -- rather than presenting the
    two as unrelated, separate statements. Sharing a subquery with an
    "in_scope" entry is otherwise only permitted when both are cited
    together AND the adjacent_scope framing above is preserved in the text
    itself. When an adjacent_scope entry's resolution_status is instead
    SUFFICIENT, the sufficiency auditor has judged this different
    product/intent to genuinely be the right match for what the customer
    needs -- most often because the original in_scope mapping turned out to
    be wrong. Present its evidence as a real, direct answer, not a hedged
    suggestion -- but still name the product/intent it actually concerns,
    so the customer understands why the answer is framed that way (e.g.
    "this falls under our Instant Loan product, which covers..." rather
    than silently answering as if it matched their literal wording). A
    "Verified customer-specific facts" entry may be stated directly and
    confidently, exactly like approved retrieval evidence -- it is already
    verified for this specific customer. State only what its
    `generation_content.fact` text says; do not extrapolate beyond it."""

_CONVERSATIONAL_RULES_TEXT = """CONVERSATIONAL RULES: never invent or imply a banking fact, product,
    policy, commitment, or next action for a Conversational Input, and
    never treat it as a question to answer -- compose one brief, natural,
    tone-appropriate reply from its own `generation_content.customer_component`
    alone. When it shares a turn with a real evidence-based answer, you may
    connect it naturally into the same element as that answer, exactly like
    a Clarification Input."""

_CLARIFICATION_RULES_TEXT = """CLARIFICATION RULES (every clarification obligation's own
    generation_guidance names this section -- its full rules live here
    once, not repeated per entry): never for you to judge WHETHER to ask
    (that decision is already made); your only job is composing the actual
    question. `generation_content.scope` tells you WHERE in the pipeline the
    ambiguity is blocking progress (e.g. identifying the right product/intent
    before any retrieval, versus reviewing whether retrieved evidence answers
    the request) -- context only, never to be quoted.
    `generation_content.unresolved_request` is the specific thing that's
    unclear -- compose a natural clarifying question asking specifically
    about it, in the customer's own language/tone/style, considering the
    Customer request and conversation context for what topic this
    continues. There is no fixed sentence to relay here -- write your own.
    `generation_content.clarification_guesses` holds 0-3 tappable guesses at
    the likely customer intent -- these are PREDICTIONS offered to unblock
    the task if correct, never a report of anything the customer actually
    said. A Clarification Input's own subquery commonly also has real
    Approved Evidence for the same underlying request -- a RELEVANT task
    with genuine partial evidence, not a full answer, is exactly what
    leaves a detail unresolved in the first place. That evidence being
    present, and being worth sharing, is never a substitute for composing
    the clarifying question -- do not let a real, useful partial answer
    silently absorb or replace the question you are required to ask. Both
    belong in the response together: share what the evidence does show
    (hedged, since it does not resolve which specific case applies), AND
    separately ask the clarifying question, in the same or an adjoining
    statement. An answer that reads as complete on its own, with no
    question anywhere in it, for a subquery that has a Clarification Input,
    is always missing something required. Stay within its own
    `generation_content.scope` when composing -- never overstepping into a
    different job's judgment, and never answering the request in its place
    -- and never assert a new product, policy, or fact, or offer an option
    beyond its own `generation_content.clarification_guesses`. When a
    Clarification Input's own `generation_content.clarification_guesses`
    is non-empty, don't leave your restatement of it as a bare question --
    naturally work a brief mention of what those options actually are
    into the same sentence or the one right after it (e.g. "...are you
    asking about applying, checking eligibility, or the fees involved?"),
    using each guess's own `label`/`message` (inside
    `generation_content.clarification_guesses`) as the TOPIC to mention,
    never inventing an option not listed there. A guess's `message` is
    written in the CUSTOMER's own voice (what they would send if they
    tapped it) -- never copy or closely paraphrase its sentence
    structure into your own question, which is YOUR voice speaking to
    the customer ("I"/"you" reversed from the guess's own phrasing).
    For example, a guess message "Can you list the other banking
    services you can help me with?" becomes, in your voice, "...are you
    looking for other banking services I can help you with...", never
    reused with its own "you"/"me" left as-is. Tappable chips for these
    same options render automatically right after your text -- without
    this, they appear with nothing in the message explaining what
    they're for. When `clarification_guesses` is empty, ask a plain
    open question as before; there is nothing to mention. This is the end
    of CLARIFICATION RULES."""

_DECLINE_NO_INFO_RULES_TEXT = """DECLINE/NO-INFO RULES: each Decline Input carries its own
    `generation_content.decline_evidence`, and each No-info Input carries
    its own `generation_content.no_info_evidence`. Unlike Approved evidence
    (raw material you compose an answer FROM), each of these two fields is
    already the complete, final statement for that entry -- your job for it
    is only to relay or lightly connect it, never to compose, research, or
    reason from it as if it were a fact to build an answer on top of.
    Rephrase it naturally in the customer's own language/tone/style from
    the STYLE & PERSONALIZATION INSTRUCTIONS above, the same as every
    other element in this response -- a bare copy-paste of the evidence
    field's exact wording is a last resort only, never the default, and
    never acceptable merely because the rest of the response happens to
    need more effort to personalize than this entry does. However you
    phrase it, faithfully convey only what its own evidence field says --
    never add a product, policy, fact, or suggestion beyond it, and never
    answer the request in its place."""

_SHORTCUT_RULES_TEXT = """SHORTCUT RULES: each shortcut is attached automatically and
    unconditionally; you do not control whether it appears, only whether
    your text acknowledges it, and your wording must match which one it is
    (see the CRITICAL rule below). First give the complete, faithful
    answer from approved evidence, written as a confident response on its
    own terms -- never open with an apology or decline-style framing
    merely because a shortcut is also available. When approved evidence
    fully explains how to do something manually (for example, in-app
    navigation steps), state those steps in full; a shortcut existing does
    not make the manual explanation incomplete or something to abbreviate.
    A shortcut's `generation_content.for_request` names, in
    the customer's own words, exactly which request it was registered to
    address. When a subquery has no approved evidence to answer it at all,
    check whether any shortcut's `generation_content.for_request` matches
    that same subquery (the Customer request and that subquery's own
    wording, not a loose topic guess). If one does, do not write "I don't
    have information," an apology, or any decline-style framing for that
    subquery -- present the shortcut(s) themselves, confidently, as the
    direct answer.
    * CRITICAL, for every shortcut you mention anywhere in the response:
      quote its `generation_content.label` field verbatim, character for
      character, inside the sentence -- never paraphrase, summarize,
      rename, or describe it in different words -- AND phrase the action
      to match its `kind`, never generic "click"/"use the option below"
      language for both: a "navigation" shortcut is an actual clickable
      button, so "click"/"use"/"tap the ___ button below" is correct for
      it; a "form" shortcut renders as an embedded, fillable form card
      directly in the chat, not a button -- the customer fills it in and
      submits it right there, so the only correct phrasing is "fill in and
      submit the ___ form below" (or equivalent), never "click"/"tap"/
      "use the option below," which describes a button that isn't there.
      If your sentence uses the wrong label or the wrong action for the
      kind, the customer cannot tell what you mean or what to actually do.
      Wrong: a shortcut labeled "Update Profile" described as "visit the
      Profile section in your account settings" (wrong label); a "form"
      shortcut labeled "Change Address" described as "click the option
      below" (wrong action -- there is no button to click). Right: "using
      the Update Profile button below" (navigation) / "fill in and submit
      the Change Address form below" (form).
    * Exactly one matching shortcut: name what it lets the customer do,
      quoting its exact `label` and phrasing the action for its `kind`
      (for example, a "navigation" shortcut labeled "Update Profile":
      "You can update your residential address directly using the
      Update Profile button below." A "form" shortcut labeled "Change
      Address": "You can fill in and submit the Change Address form
      below.").
    * More than one matching shortcut (this happens often: a "form" and
      a "navigation" shortcut both registered for the same request):
      distinguish them explicitly by `kind`, in one coherent sentence,
      rather than presenting them as an undifferentiated pair. A
      "navigation" shortcut is a button that takes the customer to a
      screen where they manage it themselves; a "form" shortcut is an
      embedded card in the chat that they fill in and submit right
      there. For example, if a "navigation" shortcut labeled "Update
      Profile" and a "form" shortcut labeled "Change Address" both
      matched: "You can use the Update Profile button below to go
      directly to your profile and update it yourself, or fill in and
      submit the Change Address form below instead if you'd rather have
      it updated for you." Never invent a mechanism or distinction
      beyond kind, label, and topic -- and never substitute either exact
      label with a paraphrase, or a form's "fill in and submit" action
      with button language.
    This is that subquery's complete answer -- do not also add a separate
    acknowledgment sentence for these same shortcuts elsewhere; the
    one-sentence-per-shortcut rule below is for shortcuts offered
    *alongside* a real evidence-based answer, not ones already presented
    as the answer here. Reserve apology/decline framing only for a
    subquery that neither approved evidence nor any shortcut's
    `for_request` addresses.
    Every "Available shortcuts this turn" entry not already covered by
    the rule above still needs its own one-sentence acknowledgment, placed
    immediately after the answer content it relates to (or as its own
    element when it answers a subquery by itself) -- never combined with a
    different shortcut into one shared sentence, and never held back to
    the end. Treat this the same as answering every task: covering
    multiple topics, fixing a flagged issue on a retry, running out of
    room, or the response already feeling complete is never a reason to
    drop one -- it is required whenever such a shortcut exists, not an
    optional addition. Use only each shortcut's supplied `label`/`topic`,
    phrased for its `kind` (see the CRITICAL rule above: "click"/"use the
    ___ button below" for "navigation," "fill in and submit the ___ form
    below" for "form" -- never the reverse). Good style, "navigation":
    "If you'd rather skip these steps, you can also do this directly
    using the ___ button below." Good style, "form": "If you'd rather
    skip these steps, you can also fill in and submit the ___ form
    below." Each such sentence must:
    * Make clear the approved-evidence method you just described still
      works -- each shortcut is an alternative or faster path, never a
      replacement or a claim that the manual method no longer applies.
    * Quote a shortcut's `label` verbatim, character for character --
      never paraphrase, rename, or describe it in different words (see
      the CRITICAL rule above; it applies here too). Never invent a
      step, field, screen layout, count, url, or any detail beyond what
      was supplied.
    * Never claim you are attaching, opening, sending, starting, or
      completing anything yourself -- a "navigation" button or a "form"
      card is attached separately and automatically regardless of this
      sentence; you are only introducing that it exists.
    * Never fabricate a shortcut that is not in the list, and never
      mention one when the list is empty.
    Do not let a shortcut's acknowledgment sentence replace the
    substantive answer for a task with no shortcut. Every task in
    "Approved evidence by retrieval task" still needs its own real answer
    regardless of whether any shortcut exists for it."""

_UNAVAILABLE_SERVICE_REQUEST_RULES_TEXT = """UNAVAILABLE SERVICE REQUEST RULES: for each entry, include one brief,
    honest sentence telling the customer that part of their request could
    not be started right now, using only the supplied
    `generation_content.reason`. Never invent a different explanation, and
    never silently drop that part of the request from the response merely
    because no form could be attached to it -- an unavailable capability
    still deserves an acknowledgment, the same as a decline or a terminal
    unresolved topic does elsewhere in this response."""

_PERSONAL_DATA_RULES_TEXT = """PERSONAL DATA RULES: a "Personal data references" entry's
    `generation_content.for_request` names, in the customer's own words,
    exactly which request its data was fetched for. When a subquery
    matches, weave that entry's `generation_content.insert_token` verbatim
    into one natural sentence (for example: "Your address on file is
    {{data-a1b2c3d4}}."). Never alter, abbreviate, translate, or omit any
    character of the token. Never guess, paraphrase, or state the real
    value yourself -- you are never shown it. Never claim this information
    is unavailable, inaccessible, or something the customer must look up
    elsewhere when a matching entry exists. Never fabricate a token not
    present in the list."""


def _build_response_obligations(
    evidence_by_task: list,
    verified_api_context: list,
    decline_context: list,
    no_info_context: list,
    conversational_context: list,
    intent_clarifications: list,
    available_shortcuts: list,
    unavailable_service_requests: list,
    personal_data_placeholders: list,
    task_metadata: dict | None = None,
) -> list[dict]:
    """Fold every already-computed, already-typed obligation list into one
    `{id, generation_content, generation_guidance}` entry per item -- the
    single list the response generator prompt reads (Stage 1 of the unified
    contract). Each caller keeps computing its typed lists exactly as it
    does today (nothing about how any of these is derived changes here);
    this only changes how they are packaged for generation. A later stage
    moves the typed-list construction itself into this function, read
    directly from state, once node_04c and node_06 also consume it -- kept
    separate for now so the prompt change and the net/reviser change land
    as independently reviewable, independently rollback-able commits.

    Every id here is one already used elsewhere in the pipeline today
    (task_id, a decline/no-info entry's own synthetic task_id, a
    clarification's subquery_id, or a nav_N/svc_N/data_N/fact_N/unavail_N
    handle) -- this function mints no new id scheme, it only unions the
    four that already coexist safely in `valid_task_ids`/`source_task_ids`.

    NOTE on naming: a `task_results` entry's own `generation_guidance` field
    (node_03c's audit-time caveat on a piece of evidence) is CONTENT under
    this contract, not the envelope's `generation_guidance` -- it describes
    what was found, not how to use it. It is folded into this entry's
    `generation_content` as `auditor_note`, never mistaken for the mode-level
    instruction string above.
    """
    obligations = []
    task_metadata = task_metadata or {}

    for entry in evidence_by_task:
        obligations.append({
            "id": entry.get("task_id"),
            "generation_content": {
                "resolved_request": task_metadata.get(entry.get("task_id"), ""),
                "search_territory": entry.get("search_territory", "in_scope"),
                "mapping_reason": entry.get("mapping_reason", ""),
                "approved_parents": entry.get("approved_parents", []),
                "auditor_note": entry.get("generation_guidance", ""),
                "resolution_status": entry.get("resolution_status", "SUFFICIENT"),
            },
            "generation_guidance": _GENERATION_GUIDANCE_BY_MODE[
                "compose_partial" if entry.get("resolution_status") == "RELEVANT" else "compose"
            ],
            "generation_rule": _GENERATION_RULE_BY_TYPE["evidence"],
        })

    for entry in verified_api_context:
        obligations.append({
            "id": entry.get("handle"),
            "generation_content": {"for_request": entry.get("for_request", ""), "fact": entry.get("fact", "")},
            "generation_guidance": _GENERATION_GUIDANCE_BY_MODE["compose"],
            "generation_rule": _GENERATION_RULE_BY_TYPE["verified_fact"],
        })

    for entry in conversational_context:
        obligations.append({
            "id": entry.get("subquery_id"),
            "generation_content": {"customer_component": entry.get("customer_component", "")},
            "generation_guidance": _GENERATION_GUIDANCE_BY_MODE["compose"],
            "generation_rule": _GENERATION_RULE_BY_TYPE["conversational"],
        })

    for entry in decline_context:
        obligations.append({
            "id": entry.get("task_id"),
            "generation_content": {
                "subquery": entry.get("subquery", ""),
                "decline_evidence": entry.get("decline_evidence", ""),
            },
            "generation_guidance": _GENERATION_GUIDANCE_BY_MODE["relay"],
            "generation_rule": _GENERATION_RULE_BY_TYPE["decline"],
        })

    for entry in no_info_context:
        obligations.append({
            "id": entry.get("task_id"),
            "generation_content": {
                "resolved_subquery": entry.get("resolved_subquery", ""),
                "no_info_evidence": entry.get("no_info_evidence", ""),
            },
            "generation_guidance": _GENERATION_GUIDANCE_BY_MODE["relay"],
            "generation_rule": _GENERATION_RULE_BY_TYPE["no_info"],
        })

    for entry in _clarification_prompt_view(intent_clarifications):
        unresolved_request = entry.get("unresolved_request", "")
        # Unlike the other three modes' fixed guidance string, ask-mode's
        # guidance is built per entry: it names the actual unresolved_request
        # directly, deterministically (string formatting only, no LLM
        # authorship, no new call) -- stating outright "this is a question
        # about X" rather than leaving the model to separately notice
        # generation_content.unresolved_request and connect the two itself.
        guidance = _GENERATION_GUIDANCE_BY_MODE["ask"]
        if unresolved_request:
            guidance = (
                f'This is a clarifying question about "{unresolved_request}". '
                + guidance
            )
        obligations.append({
            "id": entry.get("subquery_id"),
            "generation_content": {
                "scope": entry.get("scope", ""),
                "unresolved_request": unresolved_request,
                "clarification_guesses": entry.get("clarification_guesses", []),
            },
            "generation_guidance": guidance,
            "generation_rule": _GENERATION_RULE_BY_TYPE["clarification"],
        })

    for entry in available_shortcuts:
        obligations.append({
            "id": entry.get("handle"),
            "generation_content": {k: v for k, v in entry.items() if k != "handle"},
            "generation_guidance": _GENERATION_GUIDANCE_BY_MODE["state"],
            "generation_rule": _GENERATION_RULE_BY_TYPE["shortcut"],
        })

    for entry in unavailable_service_requests:
        obligations.append({
            "id": entry.get("handle"),
            "generation_content": {"reason": entry.get("reason", "")},
            "generation_guidance": _GENERATION_GUIDANCE_BY_MODE["state"],
            "generation_rule": _GENERATION_RULE_BY_TYPE["unavailable_service_request"],
        })

    for entry in personal_data_placeholders:
        obligations.append({
            "id": entry.get("handle"),
            "generation_content": {
                "for_request": entry.get("for_request", ""),
                "field_description": entry.get("field_description", ""),
                "insert_token": entry.get("insert_token", ""),
            },
            "generation_guidance": _GENERATION_GUIDANCE_BY_MODE["state"],
            "generation_rule": _GENERATION_RULE_BY_TYPE["personal_data"],
        })

    return obligations


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
    # The most recent AI turn is marked distinctly (not just "AI:") so a
    # consuming prompt can tell it apart from an older AI turn sitting
    # earlier in the same flat transcript -- confirmed live that without
    # this, a vague reply ("i am not sure") got attributed to a stale,
    # more topically specific AI turn from several exchanges back instead
    # of the assistant's own immediately preceding (but topic-neutral)
    # turn, since nothing distinguished "most recent" from "mentioned
    # somewhere in history" for the model to weigh.
    last_ai_index = max(
        (i for i, message in enumerate(trusted_history) if not isinstance(message, HumanMessage)),
        default=None,
    )
    recent_history = "\n".join(
        f"User: {message.content}" if isinstance(message, HumanMessage)
        else f"AI (most recent assistant turn, immediately before the Latest User Input): {message.content}" if i == last_ai_index
        else f"AI: {message.content}"
        for i, message in enumerate(trusted_history)
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


def _natural_join(items: list[str]) -> str:
    """Join 2+ strings as "A and B" or "A, B, and C" -- never used for a
    single item, so no 1-item case is handled."""
    if len(items) == 2:
        return f"{items[0]} and {items[1]}"
    return f"{', '.join(items[:-1])}, and {items[-1]}"


def _build_deterministic_greeting(customer_id: str, user_profile: dict) -> str:
    """Assemble a login greeting from on-demand, deterministic checks only.

    The project does not require carrying conversation content across
    sessions, so no LLM call and no outbound audit is needed here: every
    fact used (returning-customer status, pending service-request count) is
    a direct, verifiable database read, not a generated claim.
    """
    language = user_profile.get("language") or "English"
    nickname = (user_profile.get("nickname") or "").strip()

    is_returning = LongTermMemoryManager.has_prior_session(customer_id)
    base_key = "greeting_returning" if is_returning else "greeting_new"
    if nickname:
        greeting = get_message(language, f"{base_key}_named").format(nickname=nickname)
    else:
        greeting = get_message(language, base_key)

    pending_requests = [
        record for record in service_request_service.list_registrations(customer_id)
        if record.get("status") == service_request_service.PENDING
    ]
    # This is a short text reminder pointing at the header's requests icon,
    # not an attached serviceRequestActions entry: the embedded service-
    # request form (see renderEmbeddedServiceRequest in the frontend) was
    # changed from a compact shortcut into a full inline form, which would
    # otherwise occupy the entire chat screen immediately after login, on
    # every single session. The pending count/badge is already refreshed
    # independently via fetchServiceRequestList, so nothing is lost by not
    # attaching the form here -- the customer opens it deliberately instead.
    #
    # The true total is reported, not just the single latest one: a
    # customer can easily accumulate several pending requests (repeated
    # address-update submissions, plus a separate KYC resubmission, etc.),
    # and silently naming only the most recent one understates what is
    # actually outstanding. Distinct request *types* are named (deduplicated
    # by service_request_id), not each individual pending row -- naming
    # nine near-identical "Change Address" entries separately would be far
    # less readable than a single count.
    if pending_requests:
        labels_by_type = {}
        for record in pending_requests:
            service_request_id = record.get("service_request_id", "")
            if service_request_id in labels_by_type:
                continue
            contract = get_service_request_contract(service_request_id)
            if contract is not None:
                labels_by_type[service_request_id] = contract.get("action_label", "your request")
        distinct_labels = list(labels_by_type.values())
        total_count = len(pending_requests)

        if distinct_labels:
            if total_count == 1:
                reminder = get_message(language, "service_request_reminder_single").format(label=distinct_labels[0])
            elif len(distinct_labels) == 1:
                reminder = get_message(language, "service_request_reminder_plural_same").format(
                    count=total_count, label=distinct_labels[0]
                )
            else:
                reminder = get_message(language, "service_request_reminder_plural_mixed").format(
                    count=total_count, labels=_natural_join(distinct_labels)
                )
            greeting = f"{greeting} {reminder}"

    return greeting


# --- PHASE 0: INGESTION & INGEST GUARDS ---

def node_00_context_hydrator_and_greeting(state: AgentState):
    print("🟢 [NODE EXECUTING] Node 00: Context Hydrator & Greeting")
    customer_id = state.get("user_id", "USR_999")
    user_profile = LongTermMemoryManager.get_user_preferences(customer_id)

    greeting = _build_deterministic_greeting(customer_id, user_profile)

    ui_action_payload = {"uiType": "text", "options": []}

    # Read fresh, not stored: the raw last-session transcript is only ever
    # used by n00b (a follow-up check straight after this node), never
    # carried forward past this turn. If there's nothing to check (no prior
    # session beyond the last login's own greeting), n00b is skipped
    # entirely -- see route_n00 in graph.py. A pending service request no
    # longer factors in here: the greeting's own deterministic reminder
    # (see _build_deterministic_greeting) already covers it without an LLM
    # call, so it's never a reason on its own to run n00b's generated,
    # audited follow-up.
    last_session_transcript = LongTermMemoryManager.fetch_last_session_transcript(customer_id)
    has_followup_candidate = bool(last_session_transcript)

    # The hypothesis test's population is this customer's turns generally
    # -- past, present, and future -- not just the ones that happen to
    # fall inside whichever login session is currently open, so its
    # sample shouldn't restart empty every time the customer logs back
    # in. Seeded here from the customer's durably-logged history
    # (Turn_Resolution_Feedback, across every past session), reserving
    # one slot for the current, still-forming turn to occupy live once
    # node_03c runs (see RAG_RETRY_EFFORT_v1.0.md §4) -- a customer who
    # never sends 30 turns in a single sitting still gets a real,
    # standing-history-informed test from their very first message this
    # session, rather than one perpetually starting from scratch.
    recent_turn_resolutions = LongTermMemoryManager.fetch_recent_turn_resolutions(
        customer_id, HYPOTHESIS_TEST_SAMPLE_SIZE - 1
    )
    turn_escalation_history = [
        {
            "turn_number": resolution["turn_number"],
            # Not persisted in Turn_Resolution_Feedback at all -- see
            # fetch_recent_turn_resolutions' own docstring for why this
            # narrow gap is accepted rather than fixed here.
            "had_repeated_need": False,
            "subqueries": [
                {"text": sq.get("customer_component", ""), "score": _score_subquery(sq)}
                for sq in resolution["financial_subqueries"]
                if isinstance(sq, dict)
            ],
            "user_rating": resolution["user_rating"],
        }
        for resolution in recent_turn_resolutions
    ]

    return {
        # A new login begins a clean graph. No plan, task evidence, retry
        # data, or other execution state crosses the session boundary, and
        # -- per the project's scope -- no conversation content does either.
        # The greeting itself is the one exception: it must be the sole
        # entry in chat_history (not left empty) so the per-turn archive
        # step below actually has something to persist. Without this, the
        # greeting is never written to Persistent_Chat_Message, and the
        # frontend's post-INIT history sync then overwrites the just-shown
        # greeting with an empty page -- the login looks like it shows
        # nothing.
        **_fresh_volatile_state_reset(),
        "user_profile": user_profile,
        "chat_history": [AIMessage(content=greeting)],
        "history_sequence_offset": 0,
        "compacted_history_summary": "",
        "active_product_intents": [],
        "active_plan": {},
        "completed_steps": [],
        "process_slots": {},
        "verified_task_history": [],
        # Soft escalation (node_08): session-scoped, reset only here, never
        # per-turn -- see node_08_soft_escalation_analyzer's docstring.
        "session_started_at": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
        "same_intent_streak_count": 0,
        "turn_escalation_history": turn_escalation_history,
        "final_draft": greeting,
        "final_answer": greeting,
        "final_response": greeting,
        "final_clarification": "",
        "ui_action_payload": ui_action_payload,
        "last_session_transcript": last_session_transcript,
        "preflight_status": "greeting_followup_check" if has_followup_candidate else "initial_greeting",
    }


def node_00b_followup_check(state: AgentState):
    """On-demand login follow-up: only runs when n00 found a prior session's
    transcript worth checking. A pending service request is no longer a
    reason to run this node -- n00's own deterministic greeting reminder
    already covers it without an LLM call (see _build_deterministic_greeting)
    -- so this is purely about whether last session left a conversational
    topic unresolved. Unlike the deterministic greeting itself, this content
    is generated -- it has to read and interpret last session's actual
    transcript -- so unlike the greeting, it goes through the full
    evidence-groundedness and conduct-compliance audit before ever reaching
    the customer. On any audit failure, or when the model correctly finds
    nothing worth following up on, this silently falls back to exactly n00's
    original deterministic greeting -- a login must never hang, retry, or
    degrade waiting on this; it can only ever add to the greeting, never
    replace or block it.
    """
    print("🟢 [NODE 00b] Executing Login Follow-Up Check")
    greeting = state.get("final_draft", "")
    transcript = state.get("last_session_transcript", [])

    language = state.get("user_profile", {}).get("language") or "English"
    prompt = f"""
    LOGIN FOLLOW-UP CHECK
    You are Horizon, a banking assistant. The customer has just logged back
    in. Below is the raw transcript of their most recently completed
    session, if any. Decide whether a brief, natural follow-up is warranted.

    Last session's transcript (trusted messages only; empty if this is the
    customer's first session, or if it was itself just a login greeting with
    nothing else in it):
    {json.dumps(transcript, ensure_ascii=False)}

    Natively generate response in: {language}
    "Horizon" and every "Horizon <Name>" branded term (Horizon Bank,
    Horizon App, Horizon Key, etc.) is a proper brand name -- write it
    exactly as-is, in the original Latin script, regardless of the response
    language. Never translate, transliterate, or localize it.

    Rules:
    - Only identify a topic as unresolved if the transcript clearly shows
      one: a question the assistant never actually answered, a clarification
      the assistant asked for that the customer never gave, or an escalation
      that never concluded. A session that ended with a complete answer, or
      that was only ever a greeting with nothing else in it, has nothing
      unresolved -- do not invent a topic to follow up on.
    - If a topic is unresolved, name it in your own words (never quote
      internal ids) and ask, in one sentence, whether the customer would
      like to continue it.
    - Do not repeat or restate the login greeting itself.
    - If there is no unresolved topic, or the transcript is empty, return an
      empty string -- an empty follow-up is the normal, expected outcome for
      most logins, not a failure.
    - Keep it to at most two sentences. Do not invent any fact, banking
      procedure, or policy not present above.

    Output JSON only: {{"followup": "one or two sentences, or an empty string"}}
    """
    try:
        followup = _parse_json_robustly(call_groq_lpu(prompt)).get("followup", "").strip()
    except Exception as error:
        print(f"⚠️ [NODE 00b] Follow-up generation failed: {error}")
        followup = ""

    if not followup:
        return {"last_session_transcript": []}

    evidence_bundle = {
        "last_session_transcript": transcript,
    }
    route_specific_rules = """
    This is a login follow-up, not an answer to a customer question. It is
    grounded only if every claim about "what happened last time" is directly
    supported by "last_session_transcript". It must not state or imply that
    anything is being executed, attached, or completed by this message
    itself -- it may only ask whether the customer wants to continue.
    Reject it if it describes an unresolved topic not actually visible in
    the transcript, or claims the last session concluded differently than
    the transcript shows.
    """
    evidence_audit = check_evidence_groundedness(
        followup,
        evidence_bundle=evidence_bundle,
        evidence_description="Last Session Transcript",
        route_specific_rules=route_specific_rules,
    )
    conduct_audit = check_conduct_compliance(followup)

    if not (evidence_audit.get("evidence_grounded") and conduct_audit.get("conduct_compliant")):
        log_event(
            "greeting.followup.rejected",
            level="WARNING",
            evidence_reason=evidence_audit.get("reason"),
            conduct_reason=conduct_audit.get("reason"),
        )
        return {"last_session_transcript": []}

    combined = f"{greeting}\n\n{followup}"
    log_event("greeting.followup.attached", followup_length=len(followup))
    return {
        "final_draft": combined,
        "final_answer": combined,
        "final_response": combined,
        "chat_history": [AIMessage(content=combined)],
        "last_session_transcript": [],
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
            **_fresh_volatile_state_reset(),
            "preflight_status": "escalation_confirmation_accepted",
            "chat_history": history,
        }

    # 1. Sweep for hardcoded threats (Garbled text, SQL drops, XSS)
    if "<script>" in user_msg.lower() or "DROP TABLE" in user_msg.upper():
        print("   🚫 [INGEST VETO]: Malicious string detected.")
        return {
            **_fresh_volatile_state_reset(), # 🧹 Wipe state
            "preflight_status": "error_inbound_violation",
            "chat_history": history,
        }

    # 2. Sweep for placeholder injections
    if "{{" in user_msg or "}}" in user_msg:
        print("   🚫 [INGEST VETO]: Placeholder injection detected.")
        return {
            **_fresh_volatile_state_reset(), # 🧹 Wipe state
            "preflight_status": "error_inbound_violation",
            "chat_history": history,
        }

    # 3. Database-maintained sensitive-word veto.  This is intentionally
    # deterministic and runs before Prompt Guard, so a matched message never
    # enters an LLM prompt.  Only opaque rule IDs are logged.
    sensitive_rule_ids = find_sensitive_word_rule_ids(user_msg)
    if sensitive_rule_ids:
        print(f"   🚫 [INGEST VETO]: Sensitive-word rule matched: {sensitive_rule_ids}")
        log_event(
            "inbound.sensitive_word.rejected",
            rule_ids=sensitive_rule_ids,
            match_count=len(sensitive_rule_ids),
        )
        return {
            **_fresh_volatile_state_reset(),
            "preflight_status": "error_sensitive_word",
            "chat_history": history,
        }

    # Pass the gate and cleanly initialize the turn
    return {
        **_fresh_volatile_state_reset(), # 🧹 Wipe state for the new turn
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
    """Whole-turn compliance/escalation gate, then a per-component subquery
    decomposition. Non-compliance and escalation remain whole-turn vetoes
    (returning zero components) -- folding those into per-component lanes
    changes customer-visible compliance/escalation behavior and is a
    deliberately deferred, separate decision. Everything else always
    decomposes into one or more components, each independently tagged
    TOOL_ELIGIBLE / NON_FINANCIAL / CLARIFICATION -- the same "exhaustive,
    mutually exclusive partition" principle node_02e's own SEARCH/CLARIFY/
    DECLINE split already uses one node downstream, applied one node
    earlier. Only TOOL_ELIGIBLE components ever reach node_02e/RAG/tool-
    calling; NON_FINANCIAL and CLARIFICATION bypass that loop entirely
    (see conversational_tasks/clarification_tasks) and rejoin at final
    response generation and audit as one complete schema, the same way a
    node_02e-authored DECLINE already does. See
    docs/TOOL_CALLING_v3.0.md for the full rationale."""
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

    The rules come first; the Trusted Conversation Context and Latest User
    Input they refer to follow at the end.

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
    RECENCY ANCHOR: when a short/vague reply could plausibly continue more
    than one earlier topic, the default anchor is the turn marked "AI (most
    recent assistant turn, immediately before the Latest User Input)" --
    never an older AI turn sitting further back in Trusted Conversation
    Context, even one that is more specific or topically substantive. Only
    treat an older topic as still live if the most recent assistant turn
    itself was already continuing or re-raising it. A generic, topic-neutral
    most-recent turn (e.g. a plain greeting, or an open-ended "how can I
    help you") means the customer's vague reply responds to THAT turn, not
    to whatever earlier topic the conversation had previously touched on --
    the assistant's own neutral turn has already moved past it. For example,
    if an earlier turn mentioned a pending question about a card fee, but
    the assistant's most recent turn was a plain "Hello! How can I help you
    with your banking today?", a following "I am not sure" answers that
    greeting (i.e. the customer doesn't yet know what they need help with)
    -- it does not reach back to reactivate the earlier card-fee question.
    CONTEXT FILLS GAPS, IT NEVER OVERRIDES: Trusted Conversation Context
    exists only to resolve what the Latest User Input itself leaves
    genuinely open -- a pronoun, an ellipsis, a missing target. When the
    Latest User Input already states a reason, detail, or preference
    explicitly and unambiguously on its own, that statement is final --
    never replace or contradict it with something from Trusted Conversation
    Context instead, even the assistant's own prior phrasing of a pending
    topic (which is only a paraphrase, never the customer's own words, and
    may itself be generic or imprecise). For example, "my card is ugly, I
    want to replace it" is already fully self-contained -- it names the
    card, the reason, and the action -- so even if a prior turn described
    the same pending request as being about a damaged card, that prior
    description must never overwrite "ugly" here. Only reach for context at
    all when the Latest User Input genuinely does not already say it.

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
    3. DECOMPOSED — Only if compliant and not escalation: decompose the
       resolved TRUE REQUEST by the customer's distinct NEEDS, not by its
       sentences -- one standalone component per distinct need, together
       covering everything the customer asked. Never silently drop a part:
       every part of the resolved TRUE REQUEST belongs to exactly one
       component, but several sentences that serve the same need belong to
       that one component together (see NEED-BASED DECOMPOSITION below).
       Each component is tagged with its own `resolution`:

       - TOOL_ELIGIBLE: the component names or clearly implies an
         actionable banking need -- including one that is vague,
         ambiguous, or incompletely specified (e.g. a reference to an
         unspecified earlier question, or a request whose exact target
         isn't yet clear). Any real ask belongs here no matter how unclear
         its specifics are; resolving what it specifically means is a
         downstream node's job, never yours. A vague sentence stands as its
         own component only when nothing else in this turn states the same
         need more specifically -- when another sentence does, the vague one
         is part of that component (see NEED-BASED DECOMPOSITION below),
         not a component of its own. A continuation of a preceding
         financial request remains TOOL_ELIGIBLE even when the latest
         wording supplies only its reason, preference, or correction.
         IMPORTANT LANGUAGE RULE: translate a TOOL_ELIGIBLE component's
         `customer_component` into English regardless of the language the
         user typed in, since downstream retrieval matches our English
         knowledge base.
       - NON_FINANCIAL: the component is tone-only content with no ask
         that could ever become an actionable banking need on its own --
         a greeting, thanks, acknowledgement, reaction, an expressed
         feeling or preference with nothing to act on, or wording clearly
         and only about something outside what this assistant addresses.
         Do not use this merely because a component is vague -- a vague
         but real ask is TOOL_ELIGIBLE (see above). Still express it as a
         self-contained `customer_component` (e.g. "Thanking the assistant
         for the help", "Greeting the assistant") so a natural reply can
         be composed for it later -- this is a real component like any
         other, never discarded wording.
       - CLARIFICATION: reserved narrowly for when you cannot tell whether
         THIS component itself is TOOL_ELIGIBLE or NON_FINANCIAL -- not
         merely because its content or target is unclear (that case is
         TOOL_ELIGIBLE, see above), only when the category itself, for
         this one component, is genuinely undecidable -- e.g. a remark
         that could be read as either an unrelated aside or a genuine, if
         oblique, banking question, with no way to tell which from Latest
         User Input or Trusted Conversation Context. Set
         `clarification.unresolved_request` by reasoning directly against
         Latest User Input and Trusted Conversation Context: name the
         specific tension. Set `clarification.clarification_reason`: name
         the specific detail that makes it genuinely undecidable -- not a
         generic restatement. Set `clarification.clarification_guesses`:
         0-3 guesses, each a complete, natural sentence the customer might
         actually send back, most likely first.

       For a TOOL_ELIGIBLE component, your job is faithful decomposition and
       noise reduction -- nothing more:
       - NEED-BASED DECOMPOSITION: a component is one distinct customer
         NEED -- one thing the customer wants done or answered -- never one
         sentence. Several sentences often serve the same need: one that
         introduces or frames the topic ("I have a question about my
         card.", "I need to know about my savings account."), one that
         restates it, or one that adds its reason, detail, or constraint.
         All of these belong to the same component as the sentence that
         states the need, combined into one self-contained
         `customer_component`. Start a new component only when the customer
         asks for something additional in its own right -- often marked by
         "and also", "another thing", or a second, different question.
         Components must never overlap: no two components may ask for the
         same thing, and no component may restate or contain another.
         Tone-only content (a greeting, thanks) is not part of any need --
         it stays its own NON_FINANCIAL component, as above. Do not impose
         a numerical cap. Name each component's `need` FIRST, before writing
         its `customer_component`: a short noun phrase for the one thing the
         customer needs, written to the customer ("the current balance of
         your savings account", "freezing your card", "opening a new savings
         account"), in English, with no leading imperative verb and no
         trailing punctuation. If two candidate components would get the
         same need, they are one component. For example:
         * "I need to know about my savings account. Would you please check
           my current balance?" is ONE need -> one component: "Check the
           current balance of my savings account."
         * "My card was stolen yesterday at the mall. I need to block it
           right away." is ONE need -> one component: "Block my card, which
           was stolen yesterday."
         * "What's my current balance? Also, how do I open a new savings
           account?" is TWO needs -> two components: "Check my current
           balance." and "How to open a new savings account."
       - Express it as a self-contained statement of that need from the TRUE
         REQUEST in `customer_component`. "Resolving" it means exactly two
         things, both mechanical, neither interpretive: (a) replace a
         pronoun, ellipsis, or vague reference with what it actually refers
         to, using Trusted Conversation Context (e.g. "the card is ugly"
         after a card-replacement discussion becomes a self-contained
         statement naming that same card); (b) strip conversational noise
         that carries no semantic content and would otherwise dilute
         retrieval matching -- greetings, filler ("umm", "so", "I was
         wondering if"), pleasantries, throat-clearing.
       - This is NOT license to infer a more specific financial need,
         product, or objective than the customer actually stated -- that is
         a downstream node's job, and it needs your output to still contain
         the genuine ambiguity for it to work with. Preserve the customer's
         own stated goal exactly, even when that goal names no product or
         service at all. For example, "Ah, hello, umm, so I was wondering
         -- I want to buy a car" strips down to "I want to buy a car" --
         it never becomes "I would like to apply for a car loan" or any
         other invented financial framing; buying a car is a real-world
         goal, not itself a stated banking product or action, and deciding
         whether/which banking need it implies is not your call to make.
       - Do not determine product, intent, availability, or a tool -- this
         includes not assuming WHICH banking need, if any, a stated
         real-world goal or life event implies. Those are downstream
         responsibilities, and they depend on the ambiguity still being
         visible in what you hand them.

    4. REPETITIVE ISSUE SIGNAL — Independent of triage_status above: look at
       the Trusted Conversation Context's recent turns (not just the Latest
       User Input) and set repetitive_issue_detected=true only when the
       customer has been repeatedly raising substantially the same
       underlying banking need or issue across multiple recent turns,
       especially if it still appears unresolved. Judge by meaning, not
       wording -- phrasing may differ completely between turns. For example,
       "what's my address on file", "can you confirm the address you have
       for me", and "just to double check, is my address up to date" raised
       across separate turns are the same recurring need; a customer who
       asks about their address once and later, separately, asks how to
       update it is not repeating -- those are different needs about the
       same topic. A single occurrence, even worded differently from the
       Latest User Input, is never enough on its own.

    Output JSON only:
    {{"triage_status":"NON_COMPLIANT" | "ESCALATION" | "DECOMPOSED", "components":[{{"subquery_id":"subquery_1","need":"...","customer_component":"...","resolution":"TOOL_ELIGIBLE" | "NON_FINANCIAL" | "CLARIFICATION","clarification":null | {{"unresolved_request":"...", "clarification_reason":"...", "clarification_guesses":[{{"label":"...","message":"...","reason":"..."}}]}}}}], "repetitive_issue_detected":true | false}}

    {_DATA_FOLLOWS}

    Trusted Conversation Context: {history_str}
    User Baseline (advisory only): {user_baseline}
    Current Graph Context: active_plan={has_active_plan}, retrieved_knowledge={has_retrieved_knowledge}
    Recalled Prior-Session Context Is Available: {bool(recalled_context)}
    Latest User Input: "{user_msg}"
    """
    try:
        res = _parse_json_robustly(call_groq_lpu(prompt, model_id=MODEL_E_TRIAGE))
        triage_status = str(res.get("triage_status", "DECOMPOSED")).upper()
        raw_components = res.get("components", []) if isinstance(res.get("components"), list) else []
        repeated_need_detected_this_turn = bool(res.get("repetitive_issue_detected", False))
    except Exception as e:
        print(f"🚨 [NODE 02d EXCEPTION]: {e}")
        triage_status = "DECOMPOSED"
        raw_components = []
        repeated_need_detected_this_turn = False
    if triage_status not in {"NON_COMPLIANT", "ESCALATION", "DECOMPOSED"}:
        triage_status = "DECOMPOSED"

    print(f"   🧠 [TRIAGE STATUS]: {triage_status}")

    # Exhaustive, mutually exclusive partition of this turn's components --
    # the same principle node_02e's own SEARCH/CLARIFY/DECLINE split already
    # uses one node downstream, applied here one node earlier. Only
    # TOOL_ELIGIBLE components ever reach financial_subqueries (and, from
    # there, node_02e/RAG/tool-calling); NON_FINANCIAL and CLARIFICATION
    # bypass that loop entirely.
    financial_subqueries = []
    conversational_tasks = []
    clarification_tasks = []
    if triage_status == "DECOMPOSED":
        for index, component in enumerate(raw_components, start=1):
            if not isinstance(component, dict):
                continue
            customer_component = str(component.get("customer_component", "")).strip()
            if not customer_component:
                continue
            subquery_id = str(component.get("subquery_id", "")).strip() or f"subquery_{index}"
            resolution = str(component.get("resolution", "")).strip().upper()
            if resolution == "TOOL_ELIGIBLE":
                financial_subqueries.append({"subquery_id": subquery_id, "customer_component": customer_component, "need": str(component.get("need", "")).strip()})
            elif resolution == "CLARIFICATION":
                clarification_data = component.get("clarification") if isinstance(component.get("clarification"), dict) else {}
                unresolved_request = str(clarification_data.get("unresolved_request", "")).strip()
                if unresolved_request:
                    clarification_tasks.append({
                        "subquery_id": subquery_id,
                        "originator": "triage",
                        "scope": _CLARIFICATION_SCOPE_BY_ORIGINATOR["triage"],
                        "unresolved_request": unresolved_request,
                        "clarification_reason": str(clarification_data.get("clarification_reason", "The component's category is genuinely ambiguous.")).strip(),
                        "clarification_guesses": _normalize_clarification_guesses(clarification_data.get("clarification_guesses", [])),
                    })
                else:
                    # A malformed CLARIFICATION component with nothing to
                    # actually ask cannot be allowed through -- fall back to
                    # NON_FINANCIAL rather than silently dropping it.
                    conversational_tasks.append({"subquery_id": subquery_id, "customer_component": customer_component})
            else:
                # NON_FINANCIAL, or an unrecognized resolution -- default to
                # the safest bucket rather than dropping the component.
                conversational_tasks.append({"subquery_id": subquery_id, "customer_component": customer_component})

    if triage_status == "DECOMPOSED" and not (financial_subqueries or conversational_tasks or clarification_tasks):
        # A malformed/empty decomposition cannot be allowed through with
        # nothing to act on -- same fail-safe pattern as the old malformed
        # VALID_REQUEST guard: fall back to one neutral NON_FINANCIAL
        # component so the customer still gets a natural reply instead of a
        # silent dead end.
        conversational_tasks = [{
            "subquery_id": "subquery_1",
            "customer_component": "General conversational remark with no specific banking request.",
        }]

    mapped_preflight = (
        "tool_planning" if financial_subqueries
        else "intent_clarification" if clarification_tasks
        else "non_financial" if triage_status == "DECOMPOSED"
        else triage_status.lower()
    )
    print(f"   🔎 [TRIAGE DECOMPOSITION]: {len(financial_subqueries)} tool-eligible | {len(conversational_tasks)} non-financial | {len(clarification_tasks)} clarification")
    log_event(
        "triage.financial_boundary",
        triage_status=triage_status,
        financial_subquery_ids=[item["subquery_id"] for item in financial_subqueries],
        conversational_subquery_ids=[item["subquery_id"] for item in conversational_tasks],
        repeated_need_detected_this_turn=repeated_need_detected_this_turn,
        clarification_needed=bool(clarification_tasks),
    )

    return {
        "triage_status": triage_status,
        "preflight_status": mapped_preflight,
        "triaged_subqueries": financial_subqueries,
        "financial_subqueries": financial_subqueries,
        "conversational_tasks": conversational_tasks,
        "decline_tasks": [],
        "clarification_tasks": clarification_tasks,
        "repeated_need_detected_this_turn": repeated_need_detected_this_turn,
    }

def node_02e_intent_classifier_and_query_formulator(state: AgentState):
    """n2e: formulate retrieval, clarification, or scope-decline resolutions for
    the components n2d already decomposed and compliance/relevance-screened.
    Owns matrix-dependent judgment only; never re-decomposes raw input."""
    print("🟢 [NODE 02e] Executing Multi-Task Query Formulator")
    user_msg = state.get("user_input", "")
    all_financial_subqueries = state.get("financial_subqueries", state.get("triaged_subqueries", []))
    subqueries_by_id = {
        subquery.get("subquery_id"): subquery
        for subquery in all_financial_subqueries
        if isinstance(subquery, dict) and subquery.get("subquery_id")
    }
    active_rag_subquery_ids = {
        subquery.get("subquery_id")
        for subquery in all_financial_subqueries
        if isinstance(subquery, dict)
        for attempt in subquery.get("tool_attempts", [])
        if isinstance(attempt, dict) and attempt.get("tool_name") == "rag" and attempt.get("status") in {"pending", "running"}
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
    if retry_expansion_plan:
        for expansion in retry_expansion_plan:
            print(f"   🪜 [RETRY STAGE]: Task '{expansion.get('task_id')}' → {expansion.get('stage')} (attempt {expansion.get('next_attempt')})")
        log_event(
            "task.retry.stage_plan",
            stages={item.get("task_id"): item.get("stage") for item in retry_expansion_plan},
            llm_task_ids=[task.get("task_id") for task in retry_tasks],
        )
    # Extra Effort v2.1: Territory A (deterministic filter-widen of the
    # ORIGINAL task, using its own pre-computed alternate_products -- see
    # SearchTask.alternate_products) is applied directly in code further
    # below via _expand_intent_to_full, with no LLM involvement, for EVERY
    # retrying task, unconditionally. Territory B (a genuinely different
    # companion task) is constructed alongside it, in this SAME retry pass,
    # only for a task that (a) node_03c's hypothesis test says is worth it
    # this round (territory_b_unlocked) and (b) doesn't already have a
    # companion from node_03c's own free, always-on direct-adopt path (an
    # evidence-grounded lead already found and adopted -- see node_03c's
    # direct-adopt block, which needs no LLM call at all). This LLM call is
    # skipped entirely whenever no retrying task actually needs it.
    territory_b_unlocked = bool(state.get("territory_b_unlocked", False))
    existing_companion_source_ids = {
        task.get("source_task_id") for task in prior_tasks
        if task.get("search_territory") == "adjacent_scope" and task.get("source_task_id")
    }
    territory_b_needed_task_ids = [
        task_id for task_id in retry_task_ids
        if territory_b_unlocked and task_id not in existing_companion_source_ids
    ]
    retry_context = ""
    if territory_b_needed_task_ids:
        territory_b_needed_tasks = [task for task in retry_tasks if task.get("task_id") in territory_b_needed_task_ids]
        scoped_retry_feedback = {
            task_id: feedback for task_id, feedback in retry_feedback.items()
            if task_id in territory_b_needed_task_ids
        }
        retry_context = f"""
        For EACH task in Failed Tasks below, construct at most ONE new
        scope-expansion companion task -- never a revision of the failed
        task itself, which the backend already widens deterministically to
        its full intent scope regardless of what you do here. Set the
        companion's own `source_task_id` to repeat the exact failed
        task_id it was constructed for.
        Failed tasks: {territory_b_needed_tasks}
        Task-specific feedback: {scoped_retry_feedback}

        Reason from the Product/Intent Matrix and Trusted Conversation
        Context to a plausible, genuinely different but related
        product/intent -- never the same pairing the failed task already
        searched under (Territory A already covers that), and never a
        guess you cannot state a concrete basis for. Territory B exists to
        catch a genuinely plausible alternate reading of the request that
        you can state a real, specific reason for -- it does not exist to
        guarantee every failed task gets a companion. If you cannot reason
        to a genuinely different product/intent with a concrete, statable
        basis for a given failed task, construct NO companion for it at
        all -- skip it entirely and move to the next one. A forced, remote
        guess constructed only because a companion was expected is worse
        than none: it sends real retrieval effort toward a topic with no
        genuine connection to what the customer asked, and everything
        built on top of whatever it happens to find (sufficiency judgment,
        generation guidance, the eventual response) inherits that same
        remoteness, compounding rather than correcting it. This is ONE
        committed guess when you do make one, not a hedge -- write exactly
        one angle group (`group_id: "scope"`) with all three angles
        targeting that one new topic, never multiple groups. Set
        `search_task.search_territory` to "adjacent_scope" on every
        companion you do construct (never "in_scope") and
        `search_task.source_task_id` to the same failed task_id as the
        entry's own `source_task_id` above.
        """

    prompt = f"""
    TASK: Act as a Decoupled Database Search Architect.
    Each PRE-DECOMPOSED CUSTOMER COMPONENT (given at the end, from Triage; already screened for financial relevance and compliance) has `customer_component`, the retrieval-ready, context-resolved
    wording -- use this for SEARCH tasks. For a CLARIFY component, do not
    quote `customer_component` back to the customer (it may already be
    elaborated with retrieval-oriented wording they never actually said) --
    author `clarification.unresolved_request` by reasoning directly against
    Latest User Input and Recent Conversation instead (see Rule 6 below).
    This list is the complete, final partition of the customer's request. Do NOT merge, split, drop, or invent components — process every one exactly as given.

    PRODUCT/INTENT MATRIX (Valid Nouns and Verbs):
    {PRODUCT_INTENT_MATRIX}

    INTENT DEFINITION GUIDELINE:
    {INTENT_GUIDELINE}


    CRITICAL RULES FOR ANGLE QUERIES:
    - NEVER use conversational filler (e.g., "Provide a guide", "Explain how to", "What is").
    - NEVER paraphrase the customer's original question in an angle.
    - Each angle must be a short natural-language PHRASE (8-20 words) that reads like a sentence fragment from an FAQ answer.
    - Use common banking vocabulary only. Do not invent specific product names, amounts, dates, or branch details.

    1. CONTEXTUAL INHERITANCE: Read each component's customer_component text first. A Product or Intent may be inherited only when it is unambiguously established by the current-session trusted history, Active Context Matrix, or an active graph plan. Do not use the user baseline or prior-session recall to fill a missing Product or Intent, unless the component explicitly continues that recalled topic (for example, "yes, continue"). The Active Context Matrix reflects whichever turn last actually searched something -- it is never cleared just because a later turn happened to be conversational, so its mere presence here does not by itself mean the customer's current topic is still that one. Before inheriting from it, check the turn marked "AI (most recent assistant turn, immediately before the Latest User Input)" in Recent Conversation: if that turn itself was already continuing the matrix's product/intent, inheritance is safe; if it was a generic, topic-neutral turn (a plain greeting, an open-ended "how can I help you," or similar) that did not reference that topic, treat the Active Context Matrix as stale for this component and do not inherit from it.
    2. FOR EACH supplied component, evaluate the two mandatory retrieval foundations -- PRODUCT and INTENT matter equally; a clearly-named product with no discernible request type is exactly as unresolved as a request type with no product:
       - PRODUCT: Identify the component's BASE product family by stripping any
         descriptive qualifier, tier name, customer-segment word, or marketing
         brand from the wording (e.g., "business credit card" -> base family
         "credit card"; "Flexible Loan"/"Instant Loan" -> base family "loan";
         "joint savings account" -> base family "savings account"). Match that
         base family, not the qualified name, to the closest Matrix noun.
         - If the qualifier names a product tier, plan, or marketing brand
           (e.g., "Flexible Loan", "Gold Card", "joint savings account") --
           anything other than a non-individual customer segment -- use the
           matched base Matrix noun as PRODUCT; a qualifier is never grounds
           to withhold PRODUCT here. Preserve the qualifier and every other
           original word in this component's own customer_component output
           field unchanged; base-product matching affects only PRODUCT/
           target_commercial_product, never the wording shown to the customer
           or judged by the evidence audit downstream. Whether that specific
           qualified variant is actually covered is decided later, against
           real retrieved evidence -- not here.
         - If the qualifier instead names a non-individual customer segment
           (business, corporate, institutional) rather than an individual
           retail customer, do not set PRODUCT for SEARCH purposes at all --
           this resolves as DECLINE (case b in Rule 3), since this
           assistant's information is scoped exclusively to individual
           banking regardless of whether the underlying base product exists.
       - INTENT: a specific request type that maps to an intent in the PRODUCT/INTENT MATRIX, derived from the component's own wording and, only when unambiguous, the permitted context in Rule 1.
       Determine these foundations from the component and, only when unambiguous, the permitted current-session context in Rule 1.
    3. Assign EVERY component exactly one resolution:
       - SEARCH: use only when BOTH Product and Intent are identified with genuine confidence and form a valid pair in the Product/Intent Matrix.
       - CLARIFY: your job in this rule is identifying a product and intent that form one genuine, well-supported Matrix pair. This is a single test, not a checklist of named patterns: before writing target_commercial_product/target_intent, could you honestly state, in mapping_reason, a specific and real basis for that exact pairing -- not "this seems plausible" while something else is equally plausible? If ambiguity anywhere in the request or context prevents that, for any reason, your job here is blocked -- use CLARIFY. The patterns below all fail this same test; they illustrate it, they are not an exhaustive list to pattern-match against instead of applying the test itself: a generic process/policy word with no discernible product ("the fee", "eligibility"); a named product with no discernible request type ("something about my credit card"); several genuinely different intents equally plausible for the same product with no basis to prefer one ("help with my loan" -- Application, Repayment, or Eligibility?); or a life-event/goal that names no product at all and could become any of several genuinely different ones ("I want to buy a car" -- financing? using existing savings? a credit card?). Never default to the most common or most recently-discussed pairing merely to avoid asking.
       - DECLINE: use in either of two cases, and set decline.category accordingly:
         (a) decline.category="out_of_scope_product" -- the component's BASE product family, after stripping every qualifier, tier, or brand name per the PRODUCT rule above, still matches no Matrix noun at all (a fundamentally different financial instrument, e.g., margin trading, cryptocurrency trading, forex trading, or any product with no matrix entry at all). DECLINE means no amount of user clarification would ever produce a valid matrix pair.
         (b) decline.category="individual_customers_only" -- the BASE product family DOES exist in the Matrix, but the component names a non-individual customer segment (business, corporate, institutional) rather than an individual retail customer. This assistant's information is scoped exclusively to individual/personal banking.
         Never decline solely because of an unfamiliar qualifier, tier name, or marketing brand attached to an existing base product for an individual customer (e.g., "Flexible Loan", "Gold Card") -- that is SEARCH, not DECLINE. Only a non-individual customer segment triggers case (b).
       A component, its wording, a substring of its wording, or a paraphrase of it MUST NOT appear under more than one resolution.
    4. For EACH SEARCH component, formulate exactly one base Angle Group with three distinct semantic angles. Each angle must be a SHORT NATURAL-LANGUAGE PHRASE (8-20 words) that resembles a sentence fragment likely found in an FAQ answer TO THAT COMPONENT'S TOPIC — NOT an FAQ question. The direct customer query is searched separately; your angles must approach from the ANSWER side of the knowledge base.
       - "procedural": A phrase describing the actions, steps, or navigation a customer or bank performs to address this specific component. Should sound like it comes from an FAQ answer's step-by-step instructions for this topic.
       - "policy": A phrase describing the rules, conditions, eligibility criteria, limits, fees, or restrictions that apply to this specific component. Should sound like it comes from an FAQ answer's requirements or conditions section for this topic.
       - "conceptual": A phrase describing what the product, feature, or service in this specific component is and how it works. Should sound like it comes from an FAQ answer's explanatory introduction for this topic.
       DO NOT use comma-separated keywords. DO NOT paraphrase the customer's original question. Use common banking vocabulary only — do not invent specific product names, amounts, dates, or branch details. Each angle MUST target a structurally different part of a hypothetical FAQ answer about the component's topic.
    5. For EACH SEARCH task, write "mapping_reason" as two explicit, ordered steps -- product before intent, never combined into one pass or decided in the other order:
       STEP A (PRODUCT): first identify every Matrix PRODUCT (the Matrix's own top-level keys, e.g. Deposit_Account, Credit_Card, Horizon_APP -- normally one, or more than one only when the request genuinely spans multiple products at once) this component concerns, and state the concrete basis for each. When the customer's own wording names no product noun at all -- only an action, operation, or constraint (e.g. "transfer," "limit," "payment") -- you must still explicitly reason about which underlying product an ordinary retail customer would use for that action (e.g. transfers and their daily limits live on the customer's deposit/current account) -- never leave this step implicit, skip it, or defer it merely because the intent seems obvious.
       STEP B (INTENT): only once STEP A is settled, identify every Matrix INTENT (1-3) that applies to this component under the product(s) just identified. Ranking or prioritizing among several plausible intents happens here, in this step -- it is never a substitute for STEP A, and settling it first is never grounds to skip STEP A.
       Both PRODUCT and INTENT may be a single value or a short list, exactly as many as the request genuinely calls for -- never pad either to look thorough, and never collapse a genuine multi-product or multi-intent request into just one to keep the reasoning simple.
       Grounded in the Product/Intent Matrix and, where relevant, Recent Conversation. Decide the reasoning first; do not write a pairing and then back-justify it afterward.
       ANTI-PATTERN WARNING: "target_commercial_product" must always be drawn from the Matrix's PRODUCT keys (the left-hand side), never one of its INTENT values (the right-hand side, e.g. "Payments And Transfers," "Features And Limits"). If you catch yourself about to write an intent-shaped phrase into target_commercial_product, that is a direct sign STEP A was skipped, not completed -- stop and actually identify the product first.
       VERIFICATION ITEMS FIELD: if the component's own wording implies a specific, checkable claim that PRODUCT/INTENT classification does not need to resolve but real evidence must confirm before the request is fully answered, record it as its own entry in "verification_items" (a list of short, precise, self-contained sentences; empty when no such claim exists). This is general-purpose, not limited to product identity: a named brand or network ("the card network is Visa"), a premise or condition an evidence-backed answer would need to actually match ("the replacement procedure covers cosmetic damage, not just physical damage"), a scope boundary ("the stated limit applies per application, not per customer"), or any other specific detail the customer's own wording turns on. Never changes which PRODUCT/INTENT you choose -- you genuinely cannot know from here whether the bank's evidence will confirm any of these, since no evidence has been retrieved yet. This field exists so the sufficiency auditor downstream, who DOES see the retrieved evidence, is required to separately check each one against it before ever calling the task fully answered -- do not fold this into mapping_reason's prose instead, and do not leave it empty merely because the claim seems minor.
       Each search task MUST contain task_id. On a new request use task_1, task_2, task_3 and continue numbering as needed.
       After committing to target_commercial_product/target_intent, ALSO write "alternate_product_reason": could this exact same literal request ALSO plausibly be served by a genuinely different commercial product if your primary classification's own documentation turns out not to cover it? Strongly prefer a cross-cutting FUNCTIONAL product (Horizon_APP, Rewards_Program) over another genuine financial-instrument product (Credit_Card, Deposit_Account, Personal_Loan, Insurance, Mutual_Funds, Stocks, Debit_Card) as the alternate -- a request genuinely spanning two financial products should already be reflected in target_commercial_product or split into a separate component, never deferred to this fallback. Only name a second financial-instrument product when you can state a real, specific reason the SAME literal request is ALSO plausibly filed under it, never merely because it feels topically nearby. Then set "alternate_products" to 0-2 Matrix product nouns that reason justifies, distinct from target_commercial_product -- empty when nothing is genuinely plausible; a forced alternate is worse than none. This is a fallback the backend only ever uses later, if a search under the primary classification finds nothing -- it never affects this turn's own search.
    6. INTENT CLARIFICATION: You are one of several nodes in this pipeline authorized to raise a clarification, and the standing rule is who raises it, who provides the evidence and reason -- scoped strictly to YOUR OWN job here (identifying the correct Product and Intent for this request, before any evidence retrieval is even attempted). Never author a clarification about something outside that scope (e.g. whether retrieved evidence answers the question -- that is the sufficiency auditor's job later in the pipeline, not yours).
       For a CLARIFY component, set clarification.unresolved_request by reasoning directly against Latest User Input and Recent Conversation yourself -- never by quoting `customer_component`, which may already be elaborated with retrieval-oriented wording the customer never actually said. If Latest User Input itself names the ambiguous part clearly, use that wording directly. If it's a short continuation (e.g. "yes") whose own ambiguity is really about what it's confirming, use Recent Conversation to identify specifically what was being confirmed and name that (e.g. "whether you meant the personal loan or the credit card discussed earlier," grounded in an actual prior turn) rather than a generic "which product this refers to" -- and never a guessed product name of your own invention. Also set `clarification.clarification_reason`: name the specific detail in Latest User Input or Recent Conversation that makes Product, Intent, or both missing/ambiguous (e.g. "customer named no product; Recent Conversation has no prior product either") -- never a generic restatement like "the request is too vague," and never reference the Matrix alone without tying it to what the customer actually said or didn't say. Do not guess a product or create a generic task merely to fill the array. The customer-facing question itself is generated deterministically downstream from unresolved_request -- do not draft one here.
       Also set clarification.clarification_guesses: 0-3 entries, each a reasonable, grounded PREDICTION of what the customer likely means -- never a report of anything the customer has actually said -- offered as a tappable option that would unblock this task if correct. Order most likely first. Populate this only when genuinely inferable (e.g. an unresolved term with only one plausible matrix match given the customer's wording); leave it empty when you have no real basis to guess (e.g. a term identifying no plausible product or intent at all). Each entry needs a short "label" (e.g. "Personal Loan"), a "message" that is a complete, natural sentence a customer would actually send (e.g. "I'm asking about a personal loan.") -- never a bare word or phrase as the message -- and a "reason": a concise, internal-only justification naming the specific Matrix fact or conversation detail that makes this guess plausible (e.g. "Personal_Loan is the only loan-type Matrix product" or "customer named credit cards two turns ago"). A guess you cannot state a concrete reason for is not genuinely inferable -- do not offer it; this is not a customer-facing field, but writing it forces the same discipline as populating clarification_guesses at all.
    7. SCOPE DECLINE: For a DECLINE component, set decline.reason to a short, factual internal statement and decline.category per Rule 3's two cases. For category="out_of_scope_product", only decline when the matrix confirms no matching BASE product family exists at all — never guess, and never decline merely because a qualified or branded variant of an existing base product is unfamiliar. For category="individual_customers_only", name the non-individual segment (e.g., business, corporate) in decline.reason without asserting whether Horizon does or does not serve that segment in reality — only that this assistant's information does not cover it. The customer-facing decline sentence itself is generated deterministically downstream from decline.category -- do not draft one here.
    8. Use "ALL" only when a component is genuinely universal and its Intent is a valid intent listed under "ALL" in the Product/Intent Matrix. "ALL" is not a fallback for an unknown product.

    Output JSON ONLY:
    {{"components": [{{"component_id":"component_1","customer_component":"open an account","resolution":"SEARCH","search_task":{{"task_id":"task_1","mapping_reason":"Customer named 'open an account' -- 'account' with no qualifier maps to the base Deposit_Account family, and 'open' is the Application intent verb in the Matrix.","angle_groups":[{{"group_id":"base","procedural":"...","policy":"...","conceptual":"..."}}],"target_commercial_product":"Deposit_Account","target_intent":["Application"],"verification_items":[],"alternate_product_reason":"If Deposit_Account documentation doesn't cover this, opening an account is also a core Horizon_APP onboarding flow.","alternate_products":["Horizon_APP"]}},"clarification":null,"decline":null}},{{"component_id":"component_7","customer_component":"I want to increase my daily transfer limit","resolution":"SEARCH","search_task":{{"task_id":"task_2","mapping_reason":"STEP A (PRODUCT): the request names no product noun at all, only the action 'transfer' and constraint 'limit' -- but daily transfer limits are configured on the customer's own deposit/current account, so PRODUCT is Deposit_Account. STEP B (INTENT): 'limit' maps to Features And Limits, and 'transfer' maps to Payments And Transfers, both valid intents under Deposit_Account.","angle_groups":[{{"group_id":"base","procedural":"...","policy":"...","conceptual":"..."}}],"target_commercial_product":"Deposit_Account","target_intent":["Features And Limits","Payments And Transfers"],"verification_items":[],"alternate_product_reason":"Since transfer limits are also managed via digital banking, Horizon_APP may contain the same setting.","alternate_products":["Horizon_APP"]}},"clarification":null,"decline":null}},{{"component_id":"component_8","customer_component":"How do I get a visa card?","resolution":"SEARCH","search_task":{{"task_id":"task_3","mapping_reason":"STEP A (PRODUCT): the request names a 'visa card' -- 'Visa' is a card network/brand name, not a product; the underlying product is Credit_Card. STEP B (INTENT): 'How do I get' maps to Application.","angle_groups":[{{"group_id":"base","procedural":"...","policy":"...","conceptual":"..."}}],"target_commercial_product":"Credit_Card","target_intent":["Application"],"verification_items":["the card network is Visa"],"alternate_product_reason":"","alternate_products":[]}},"clarification":null,"decline":null}},{{"component_id":"component_9","customer_component":"my credit card is ugly, can I replace it?","resolution":"SEARCH","search_task":{{"task_id":"task_4","mapping_reason":"STEP A (PRODUCT): the request concerns the customer's own credit card, so PRODUCT is Credit_Card. STEP B (INTENT): 'replace it' maps to Card Logistics.","angle_groups":[{{"group_id":"base","procedural":"...","policy":"...","conceptual":"..."}}],"target_commercial_product":"Credit_Card","target_intent":["Card Logistics"],"verification_items":["the card replacement procedure covers cosmetic/appearance dissatisfaction, not only damage, loss, or theft"],"alternate_product_reason":"","alternate_products":[]}},"clarification":null,"decline":null}},{{"component_id":"component_2","customer_component":"the fee","resolution":"CLARIFY","search_task":null,"clarification":{{"unresolved_request":"the fee","clarification_reason":"No product or service is identified.","clarification_guesses":[]}},"decline":null}},{{"component_id":"component_5","customer_component":"how to apply for a loan","resolution":"CLARIFY","search_task":null,"clarification":{{"unresolved_request":"loan","clarification_reason":"Personal_Loan is the only loan-type matrix product, but the customer's intended type is not confirmed.","clarification_guesses":[{{"label":"Personal Loan","message":"I'm asking about a personal loan.","reason":"Personal_Loan is the only loan-type Matrix product."}}]}},"decline":null}},{{"component_id":"component_6","customer_component":"I have a question about my loan","resolution":"CLARIFY","search_task":null,"clarification":{{"unresolved_request":"a question about my loan","clarification_reason":"Product (Personal_Loan) is unambiguous, but no verb, action, or outcome is given -- Application, Repayment, and Eligibility are all equally plausible intents with no basis to prefer one.","clarification_guesses":[{{"label":"Applying","message":"I want to know how to apply for a loan.","reason":"Application is a valid Matrix intent for Personal_Loan."}},{{"label":"Repayment","message":"I want to know about repaying my loan.","reason":"Repayment is a valid Matrix intent for Personal_Loan."}},{{"label":"Eligibility","message":"I want to know if I'm eligible for a loan.","reason":"Eligibility is a valid Matrix intent for Personal_Loan."}}]}},"decline":null}},{{"component_id":"component_3","customer_component":"margin trading account","resolution":"DECLINE","search_task":null,"clarification":null,"decline":{{"reason":"Horizon does not offer margin trading accounts.","category":"out_of_scope_product"}}}},{{"component_id":"component_4","customer_component":"business credit card annual fee","resolution":"DECLINE","search_task":null,"clarification":null,"decline":{{"reason":"This assistant's information covers individual credit cards only, not business credit cards.","category":"individual_customers_only"}}}}]}}

    {_DATA_FOLLOWS}

    PRE-DECOMPOSED CUSTOMER COMPONENTS: {triaged_subqueries}
    Active Context Matrix: {active_matrix}
    Current Graph Context: active_plan={has_active_plan}, retrieved_knowledge={has_retrieved_knowledge}
    Recalled Prior-Session Context Is Available: {bool(recalled_context)}
    Recent Conversation: {history_str}
    User Baseline (advisory only; do not treat it as a current request): {user_baseline}
    Latest User Input (context only; the components above are authoritative): "{user_msg}"
    {retry_context}
    """

    # A retrieval retry is not a fresh interpretation of the whole customer
    # request. Isolate it in a dedicated prompt so the model cannot reorder
    # components or overwrite a failed task ID with another successful task.
    # Extra Effort v2.1: this call's ONLY job is constructing a Territory B
    # companion for a task that has no direct-adopt lead already covering
    # it (see territory_b_needed_task_ids above) -- it never revises the
    # failed task itself, and is skipped entirely when no task needs it.
    retry_prompt = None
    if territory_b_needed_task_ids:
        retry_prompt = f"""
        PRODUCT/INTENT MATRIX (Valid Nouns and Verbs):
        {PRODUCT_INTENT_MATRIX}

        INTENT DEFINITION GUIDELINE:
        {INTENT_GUIDELINE}

        EXTRA EFFORT v2.1 -- TERRITORY B COMPANION CONSTRUCTION
        You are a Decoupled Database Search Architect. Every task in Failed
        Tasks already searched its own literal topic across its full valid
        intent scope, including any plausible alternate product, without
        success (Territory A -- handled entirely by the backend, not your
        job here), and no candidate connection was found worth adopting
        directly. Your only job is constructing ONE new, genuinely
        different but related search task per failed task (Territory B) --
        reasoning through mapping_reason FIRST, before choosing its
        product/intent or writing its angles, exactly as that field requires.

        Latest User Input: "{user_msg}"
        {retry_context}

        REQUIRED CONTRACT:
        - Exactly one companion per failed task. Never a revision of the
          failed task_id itself, never a second companion for the same task,
          never a companion for a task not listed in Failed Tasks.
        - The companion's product/intent pairing must be DIFFERENT from the
          failed task's own current pairing -- Territory A already covers
          "same product (plus any plausible alternate), every intent"; a
          companion proposing the identical pairing adds nothing.
        - Every companion pairing must be valid in the Product/Intent Matrix.
        - Write exactly one angle group (group_id "scope") with three angles,
          each a SHORT NATURAL-LANGUAGE PHRASE (8-20 words) that resembles a
          sentence fragment from an FAQ answer about the NEW topic -- not the
          failed task's topic, not the customer's original wording. Use
          common banking vocabulary only; do not invent specific product
          names, amounts, dates, or branch details.
        - This is one committed guess, not a hedge: do not write more than
          one angle group, and do not propose more than one candidate
          product/intent pairing per companion.

        Output JSON ONLY:
        {{"companions": [{{"source_task_id":"exact failed task_id","search_task":{{"task_id":"<source_task_id>_scope","mapping_reason":"reasoned first: name the concrete Matrix/context basis for this genuinely different but related pairing","angle_groups":[{{"group_id":"scope","procedural":"...","policy":"...","conceptual":"..."}}],"target_commercial_product":"a valid matrix product, different from the failed task's own","target_intent":["1-3 valid intents for that product"],"search_territory":"adjacent_scope","source_task_id":"<same as the entry's own source_task_id>"}}}}]}}
        """

    # Set wherever a component's SEARCH/DECLINE resolution is rejected or
    # withheld for a subquery that a non-rag tool (e.g. a deeplink) already
    # fully answered this same round -- see _resolved_by_other_tool. A
    # subquery already fully resolved elsewhere should never also grow a
    # failure-shaped entry (no_info, decline, or the generic "please
    # clarify" fallback further below); the response generator would
    # otherwise be handed both the correct answer and a contradiction of it
    # for the exact same request.
    any_subquery_resolved_by_other_tool = False

    formulated_tasks = []
    formulated_clarifications = []
    formulated_declines = []
    prior_tasks_by_id = {task.get("task_id"): task for task in prior_tasks}
    try:
        if retry_tasks:
            # Extra Effort v2.1: this call, when it runs at all, only ever
            # produces Territory B companions -- a brand new task per
            # failed task, never a revision. It's skipped entirely
            # (formulated_tasks stays empty; Territory A's own
            # deterministic widen still applies below regardless) whenever
            # no retrying task actually needs one this round -- see
            # territory_b_needed_task_ids above.
            if retry_prompt is not None:
                # Explicit response_schema_override rather than relying on
                # marker-string auto-detection in call_llm_router, since
                # this prompt's own text doesn't match any of that
                # function's hardcoded markers (confirmed -- the prior
                # version of this prompt didn't either, so retry-mode
                # calls were never actually schema-enforced before this).
                res = _parse_json_robustly(call_groq_lpu(retry_prompt, model_id=MODEL_E_TRIAGE, response_schema_override=TerritoryBPlan))
                for companion in res.get("companions", []) or []:
                    if not isinstance(companion, dict) or not isinstance(companion.get("search_task"), dict):
                        continue
                    source_task_id = str(companion.get("source_task_id", "")).strip()
                    source_task = prior_tasks_by_id.get(source_task_id)
                    if not source_task or source_task_id not in territory_b_needed_task_ids:
                        # A companion for a task not actually asked for this
                        # round (invented, malformed, stale, or already
                        # covered by direct-adopt) has nothing to attach to
                        # -- drop it rather than guess.
                        continue
                    search_task = dict(companion["search_task"])
                    # Identity fields are never trusted from the model: the
                    # companion belongs to the SAME subquery as its source task,
                    # searches for the SAME underlying customer need (just a
                    # different, adjacent scope), and gets a deterministic task_id
                    # -- none of that is the model's call to make.
                    search_task["subquery_id"] = source_task.get("subquery_id")
                    search_task["customer_component"] = source_task.get("customer_component", "")
                    search_task["direct_query"] = source_task.get("direct_query", source_task.get("customer_component", ""))
                    search_task["task_id"] = f"{source_task_id}_scope"
                    search_task["search_territory"] = "adjacent_scope"
                    search_task["source_task_id"] = source_task_id
                    formulated_tasks.append(search_task)
        else:
            res = _parse_json_robustly(call_groq_lpu(prompt, model_id=MODEL_E_TRIAGE))
            # The model returns a mutually exclusive component partition. Derive
            # the existing downstream task lists from it so retrieval, caching,
            # retries, and clarification handling retain their current contracts.
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
                    clarification = dict(component["clarification"])
                    clarification["subquery_id"] = str(component.get("component_id", "")).strip() or f"subquery_{index}"
                    formulated_clarifications.append(clarification)
                elif resolution == "DECLINE":
                    # A component the formulator declined as out-of-scope can
                    # still be the same subquery a different tool (deeplink,
                    # service_request, api) already fully resolved this same
                    # round -- the formulator's own SEARCH/DECLINE judgment
                    # doesn't see other tools' results. Skip manufacturing a
                    # decline for a subquery that's already answered, same
                    # guard as the SEARCH-side scope-rejection below.
                    owning_subquery = subqueries_by_id.get(str(component.get("component_id", "")).strip())
                    if _resolved_by_other_tool(owning_subquery):
                        any_subquery_resolved_by_other_tool = True
                        continue
                    decline = component.get("decline") if isinstance(component.get("decline"), dict) else {}
                    # Only these two categories are ever valid from this node's own
                    # DECLINE resolution -- non_compliant/non_financial belong to
                    # triage's separate decline vocabulary, never this one.
                    category = str(decline.get("category", "")).strip()
                    if category not in {"out_of_scope_product", "individual_customers_only"}:
                        category = "out_of_scope_product"
                    formulated_declines.append({
                        "task_id": f"decline_formulator_{index}",
                        "subquery": customer_component,
                        "reason_category": category,
                        "detail": str(decline.get("reason", "")).strip(),
                        "decline_evidence": _decline_evidence_text(category),
                        "source": "formulator",
                    })
    except Exception as e:
        print(f"🚨 [NODE 02e EXCEPTION]: {e}")
        formulated_tasks = []
        formulated_clarifications = []
        formulated_declines = []

    decline_tasks = list(state.get("decline_tasks", [])) + formulated_declines
    unresolvable_search_tasks = list(state.get("unresolvable_search_tasks", []))

    validated_tasks = []
    for task in formulated_tasks:
        if not isinstance(task, dict):
            continue
        validated_task = _normalize_search_task_scope(task)
        if validated_task:
            validated_tasks.append(validated_task)
        else:
            task_id = str(task.get("task_id", "")).strip()
            print(f"   ⚠️ [TASK SCOPE REJECTED]: Invalid product/intent matrix pair for task '{task_id}'.")
            log_event("task.scope_rejected", task_id=task_id, customer_component=task.get("customer_component", ""))
            if not retry_tasks:
                # A fresh SEARCH resolution whose product/intent pairing the
                # matrix itself rejects has nowhere else to go: it never
                # becomes a live search_task, so it would otherwise vanish
                # with no compensating entry at all, silently reaching
                # node_04b with zero evidence of any kind for this subquery.
                #
                # This is deliberately rendered as a no_info entry, NEVER a
                # decline: an earlier version of this fix used decline_tasks
                # here, which (a) asserts the false, stronger claim that
                # Horizon doesn't offer the service at all -- the matrix
                # rejecting a RAG formulation only proves RAG can't answer
                # it from FAQ text, never that the underlying service
                # doesn't exist (this exact case: "change my address" has a
                # real, working service_request registered for it) -- and
                # (b) routes the whole turn straight to node_10's hard
                # rejection path whenever it's the only thing produced,
                # which is the wrong node for "temporarily couldn't answer
                # this," not "we don't do this." A retry-mode rejection (an
                # expansion stage proposing an invalid pairing) is not
                # recorded here at all -- the merge logic below already
                # falls back to the task's prior state and lets the
                # remaining retry budget continue.
                #
                # This RAG-formulation rejection is scoped to the RAG
                # attempt alone -- it says nothing about the subquery's
                # OTHER tool attempts this same round. A subquery is
                # commonly planned with both "rag" (tagged "complementary")
                # and a real tool like "service_request" in the same
                # round; when that other attempt already fully resolves
                # the subquery, a no_info entry here would tell the
                # customer there's no information available about
                # something a working shortcut was just registered for in
                # this exact turn -- confirmed live: "How to update KYC
                # information" produced both a completed "Resubmit KYC
                # Document" service request AND this no_info entry for the
                # same wording, and the generator surfaced the false
                # "no info" claim instead of the real shortcut. Uses
                # _resolved_by_other_tool, not _compute_subquery_status:
                # this subquery's OWN rag attempt is still "pending" at
                # this exact point (node_02e is inside that attempt's own
                # formulation step), which would make the status rollup
                # report PENDING and never reach RESOLVED regardless of
                # what else already covers it -- confirmed live, this was
                # the reason the first version of this same fix did not
                # actually change anything.
                owning_subquery = subqueries_by_id.get(task.get("subquery_id"))
                if _resolved_by_other_tool(owning_subquery):
                    any_subquery_resolved_by_other_tool = True
                else:
                    resolved_subquery = task.get("customer_component", "") or task.get("direct_query", "")
                    need = str((owning_subquery or {}).get("need", "")).strip()
                    unresolvable_search_tasks.append({
                        "task_id": f"unresolvable_{task_id or 'unknown'}",
                        "resolved_subquery": resolved_subquery,
                        "need": need,
                        "no_info_evidence": _no_info_evidence_text(resolved_subquery, need),
                    })
    formulated_tasks = validated_tasks

    if retry_tasks:
        # Extra Effort v2.1: formulated_tasks now holds only Territory B
        # companions (see the retry_prompt branch above, only ever
        # non-empty for territory_b_needed_task_ids), each already carrying
        # its own source_task_id. Territory A is applied here, directly, to
        # every retrying task_id -- deterministic, no LLM involvement --
        # using each task's own pre-computed alternate_products (reasoned
        # once, at fresh-formulation time -- see SearchTask.alternate_products)
        # appended to its existing product(s) before widening intent, so a
        # single failed classification can now also test a genuinely
        # different product's own full intent scope, not just its original
        # product's.
        territory_b_by_source = {}
        for task in formulated_tasks:
            source_id = task.get("source_task_id")
            if source_id and source_id not in territory_b_by_source:
                territory_b_by_source[source_id] = task

        search_tasks = []
        for prior_task in prior_tasks:
            task_id = prior_task.get("task_id")
            if task_id not in retry_task_ids:
                search_tasks.append(prior_task)
                continue

            alternate_products = (prior_task.get("alternate_products") or [])[:2]
            widened_task = dict(prior_task)
            widened_task["target_commercial_product"] = _normalized_product_tags(
                _normalized_product_tags(prior_task.get("target_commercial_product")) + alternate_products
            )
            expanded = _expand_intent_to_full(widened_task)
            old_prod = _normalized_product_tags(prior_task.get("target_commercial_product"))
            new_prod = _normalized_product_tags(expanded.get("target_commercial_product"))
            old_int = prior_task.get("target_intent", [])
            old_int = [old_int] if isinstance(old_int, str) else old_int
            new_int = expanded.get("target_intent", [])
            no_op = old_prod == new_prod and set(old_int) == set(new_int)
            if no_op:
                # No valid alternate product, and already at full intent
                # scope for its own product -- nothing left for Territory A
                # to try. Defer its own duplicate RAG re-run; Territory B's
                # companion below is unaffected and always still retrieves.
                print(f"   ⏩ [TERRITORY A NO-OP]: Task '{task_id}' already covers its full product/intent scope.")
                fast_forward_task_ids.append(task_id)
                new_retry_counts[task_id] = new_retry_counts.get(task_id, 0) + 1
                search_tasks.append(prior_task)
            else:
                print(f"   🔧 [TERRITORY A]: Task '{task_id}' | product {old_prod} → {new_prod} | intent {old_int} → {new_int}")
                search_tasks.append(expanded)
            log_event(
                "task.expansion.applied",
                task_id=task_id,
                stage="TERRITORY_A_FILTER_WIDEN",
                old_product=old_prod,
                new_product=new_prod,
                old_intent=old_int,
                new_intent=new_int,
                no_op=no_op,
            )

            # Territory B: a brand-new companion task, appended alongside
            # the original -- never merged with it. Both share the same
            # subquery_id, so _finalize_rag_attempts already combines both
            # territories' results into one subquery-level rag attempt.
            companion = territory_b_by_source.get(task_id)
            if companion:
                print(f"   🔭 [TERRITORY B]: Task '{task_id}' → companion '{companion.get('task_id')}' | product={companion.get('target_commercial_product')} intent={companion.get('target_intent')}")
                log_event(
                    "task.scope_expansion.created",
                    source_task_id=task_id,
                    companion_task_id=companion.get("task_id"),
                    target_commercial_product=companion.get("target_commercial_product"),
                    target_intent=companion.get("target_intent"),
                    mapping_reason=companion.get("mapping_reason", ""),
                )
                search_tasks.append(companion)
            elif task_id in territory_b_needed_task_ids:
                # A companion was genuinely expected this round (unlocked,
                # no existing lead) but didn't come back -- malformed or
                # dropped model response.
                print(f"   ⚠️ [TERRITORY B OMITTED]: Task '{task_id}' has no companion this round (malformed or dropped model response).")
    else:
        search_tasks = formulated_tasks

    # Retries refine only failed retrieval tasks. Intent-level ambiguity was
    # already identified from the original request and remains in state while
    # the searchable components complete their independent RAG loop.
    clarification_tasks = state.get("clarification_tasks", []) if retry_tasks else formulated_clarifications
    if not retry_tasks:
        normalized_clarifications = []
        for index, task in enumerate(clarification_tasks, start=1):
            unresolved_request = str(task.get("unresolved_request", "")).strip()
            if not unresolved_request:
                continue
            normalized_clarifications.append({
                "subquery_id": str(task.get("subquery_id", "")).strip() or f"subquery_{index}",
                "originator": "intent_formulator",
                "scope": _CLARIFICATION_SCOPE_BY_ORIGINATOR["intent_formulator"],
                "unresolved_request": unresolved_request,
                "clarification_reason": str(task.get("clarification_reason", "The request component is too ambiguous to search safely.")).strip(),
                "clarification_guesses": _normalize_clarification_guesses(task.get("clarification_guesses", [])),
            })
        clarification_tasks = normalized_clarifications

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

    # Soft escalation (node_08): the same-product-intent-streak signal. This
    # is the one place that already sees both the prior turn's active_matrix
    # and this turn's freshly computed new_matrix, so it owns the transition
    # rather than a shadow field elsewhere. A turn that resolves no
    # product/intent at all (pure CLARIFY or DECLINE) doesn't confirm any
    # topic, so it neither extends nor breaks an existing streak.
    def _matrix_signature(matrix):
        # target_commercial_product may be a single product or, during a
        # PRODUCT_EXPANSION retry, a list of products -- normalize both
        # forms the same way the rest of this module does before hashing,
        # otherwise a list entry here raises "unhashable type: 'list'".
        return frozenset(
            (tuple(sorted(_normalized_product_tags(item.get("product")))), tuple(sorted(item.get("intents", []))))
            for item in matrix if isinstance(item, dict) and item.get("product")
        )
    new_signature = _matrix_signature(new_matrix)
    if not new_signature:
        same_intent_streak_count = state.get("same_intent_streak_count", 0)
    elif new_signature == _matrix_signature(active_matrix):
        same_intent_streak_count = state.get("same_intent_streak_count", 0) + 1
    else:
        same_intent_streak_count = 1

    print(f"   🔎 [SEARCH TASKS]: {len(search_tasks)} tasks | Matrix: {new_matrix} | Intent clarifications: {len(clarification_tasks)}")
    log_event("task.formulated", task_ids=[task.get("task_id") for task in search_tasks], task_count=len(search_tasks), intent_clarification_count=len(clarification_tasks), retry_mode=bool(retry_tasks))

    if not search_tasks:
        # RAG was requested for these financial subqueries, but the intent
        # formulator could not safely create a retrieval task. Close those
        # generic RAG tasks rather than leaving a running ledger entry while
        # clarification proceeds directly to resolution planning.
        terminal_financial_subqueries = _finalize_rag_attempts(state, [])
        # BUG-001 fix: "every component THIS call formulated declined" is not
        # the same claim as "the whole turn has nothing to show the
        # customer" -- a sibling subquery dispatched to a non-RAG tool this
        # same round (deeplink/service_request/api) may already have
        # completed successfully. any_subquery_resolved_by_other_tool above
        # is scoped per-declining-component (does THIS subquery specifically
        # have another resolved attempt), which is too narrow here: a
        # genuinely different subquery (e.g. "activate my card" resolved via
        # deeplink) sharing this turn with an unrelated declined subquery
        # (e.g. "open a margin trading account") must not have its already-
        # completed action silently discarded by short-circuiting straight
        # to Standard Rejection. Checked turn-wide, not component-scoped.
        any_sibling_subquery_resolved_by_other_tool = any(
            _resolved_by_other_tool(sq) for sq in all_financial_subqueries
        )
        if not clarification_tasks and decline_tasks and not any_sibling_subquery_resolved_by_other_tool:
            # Every component the formulator received resolved to DECLINE
            # (out-of-scope product), and no sibling subquery this turn was
            # already resolved by a different tool. Nothing valid remains
            # for this turn.
            return {
                "preflight_status": "declined",
                "search_tasks": [],
                "clarification_tasks": [],
                "task_results": [],
                "decline_tasks": decline_tasks,
                "unresolvable_search_tasks": unresolvable_search_tasks,
                "financial_subqueries": terminal_financial_subqueries,
                "same_intent_streak_count": same_intent_streak_count,
            }
        if (
            not clarification_tasks
            and not unresolvable_search_tasks
            and not any_subquery_resolved_by_other_tool
            and not any_sibling_subquery_resolved_by_other_tool
            and not (active_plan.get("steps") or has_retrieved_knowledge)
        ):
            # A valid request reached the formulator but no usable structured
            # output was produced -- and no scope-rejection above already
            # queued a more specific no_info entry for the same gap, which
            # would make this generic fallback a redundant second, weaker
            # message about the same missing answer. Abstain safely instead
            # of allowing a later generation node to infer a product or
            # invent a search task. Also skipped when a scope-rejected
            # subquery was already fully answered by another tool (e.g. a
            # deeplink) -- that isn't "no usable output," it's already
            # resolved, and asking the customer to clarify a request the
            # bot just answered is exactly the self-contradiction this
            # guard exists to prevent. BUG-001: this now also covers a
            # genuinely different sibling subquery resolved by another tool
            # this same round, not just this exact subquery.
            fallback_unresolved_request = user_msg.strip() or "your banking request"
            fallback_subquery_id = (
                all_financial_subqueries[0]["subquery_id"] if all_financial_subqueries else "subquery_1"
            )
            clarification_tasks = [{
                "subquery_id": fallback_subquery_id,
                "originator": "intent_formulator",
                "scope": _CLARIFICATION_SCOPE_BY_ORIGINATOR["intent_formulator"],
                "unresolved_request": fallback_unresolved_request,
                "clarification_reason": "No valid retrieval task could be formed from the request.",
                "clarification_guesses": [],
            }]
        if clarification_tasks:
            return {
                "preflight_status": "intent_clarification",
                "search_tasks": [],
                "clarification_tasks": clarification_tasks,
                "task_results": [],
                "decline_tasks": decline_tasks,
                "unresolvable_search_tasks": unresolvable_search_tasks,
                "financial_subqueries": terminal_financial_subqueries,
                "same_intent_streak_count": same_intent_streak_count,
            }
        if any_subquery_resolved_by_other_tool or any_sibling_subquery_resolved_by_other_tool or active_plan.get("steps") or has_retrieved_knowledge:
            return {"preflight_status": "continue_plan", "search_tasks": [], "decline_tasks": decline_tasks, "unresolvable_search_tasks": unresolvable_search_tasks, "financial_subqueries": terminal_financial_subqueries, "same_intent_streak_count": same_intent_streak_count}
        return {"preflight_status": "intent_clarification_needed", "search_tasks": [], "decline_tasks": decline_tasks, "unresolvable_search_tasks": unresolvable_search_tasks, "financial_subqueries": terminal_financial_subqueries, "same_intent_streak_count": same_intent_streak_count}

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
        "clarification_tasks": clarification_tasks,
        "task_results": task_results if retry_tasks else [],
        "task_retry_counts": new_retry_counts if retry_tasks else {},
        "deferred_retry_task_ids": fast_forward_task_ids,
        "active_product_intents": new_matrix,
        "same_intent_streak_count": same_intent_streak_count,
        "active_plan": {} if not retry_tasks else active_plan,
        "completed_steps": [] if not retry_tasks else state.get("completed_steps", []),
        "decline_tasks": decline_tasks,
        "unresolvable_search_tasks": unresolvable_search_tasks,
    }

# --- PHASE 2: TOOL CALLING v3.0 ---

def _tool_calling_plan_schema(capabilities: list[dict]) -> type:
    """Build a structured-output schema for the tool-calling planner whose
    id fields are constrained to exactly this call's registered ids.

    ToolTaskInput's static fields (llm_service.py) only guarantee the
    model can decode the *right field name* for a given tool_name -- they
    can't also guarantee the *value* is a real id, since the registry is
    dynamic (loaded per request, not known at class-definition time). This
    builds that guarantee fresh per call: each id field's type is a
    Literal of this turn's actual registered ids, wrapped Optional so a
    task for a different tool_name can leave it null, so an invented,
    malformed, or copied-from-elsewhere value is not just rejected after
    the fact by node_03a's allow-list check -- it is impossible for the
    model to decode in the first place, the same guarantee structured
    output already gives tool_name/operation.

    The "not this tool_name" sentinel is null, not "": Gemini's structured
    output rejects an empty string as an enum member outright
    (400 INVALID_ARGUMENT, "enum[n]: cannot be empty") -- confirmed against
    the live API, not a hypothetical.
    """
    service_request_ids: list[str] = []
    destination_ids: list[str] = []
    field_ids: list[str] = []
    for capability in capabilities:
        tool_name = capability.get("tool_name")
        if tool_name == "service_request":
            service_request_ids = [
                str(entry.get("service_request_id", "")).strip()
                for entry in capability.get("available_requests", [])
                if str(entry.get("service_request_id", "")).strip()
            ]
        elif tool_name == "deeplink":
            destination_ids = [
                str(entry.get("destination_id", "")).strip()
                for entry in capability.get("available_destinations", [])
                if str(entry.get("destination_id", "")).strip()
            ]
        elif tool_name == "api":
            field_ids = [
                str(entry.get("field_id", "")).strip()
                for entry in capability.get("available_fields", [])
                if str(entry.get("field_id", "")).strip()
            ]

    def enum_type(ids: list[str]):
        return Optional[Literal[tuple(sorted(set(ids)))]] if ids else Optional[str]

    tool_task_input = create_model(
        "DynamicToolTaskInput",
        customer_component=(str, ""),
        service_request_id=(enum_type(service_request_ids), None),
        destination_id=(enum_type(destination_ids), None),
        field_id=(enum_type(field_ids), None),
    )
    tool_calling_task = create_model(
        "DynamicToolCallingTask",
        subquery_id=(str, ...),
        tool_name=(str, ...),
        operation=(str, ...),
        input=(tool_task_input, ...),
        rag_role=(str, ""),
    )
    tool_selection_clarification = create_model(
        "DynamicToolSelectionClarification",
        subquery_id=(str, ...),
        unresolved_request=(str, ...),
        clarification_reason=(str, ...),
        clarification_guesses=(list[ClarificationGuess], []),
    )
    # Domain classification, declared before tool_tasks below -- same
    # reasoning-before-verdict discipline as TaskSufficiencyItem's reason-
    # before-status field order (GROUNDEDNESS_v2.0.md): forcing the model to
    # commit to WHAT KIND of need a subquery represents before it matches a
    # specific registry entry, rather than jumping straight to keyword-level
    # capability matching. See the domain framework in the prompt itself for
    # why each tool maps to exactly one of these.
    subquery_domain = create_model(
        "DynamicSubqueryDomain",
        subquery_id=(str, ...),
        domain_reasoning=(str, ...),
        domains=(list[Literal["information", "high_risk_transaction", "personal_data_retrieval", "service_form_submission"]], []),
    )
    return create_model(
        "DynamicToolCallingPlan",
        subquery_domains=(list[subquery_domain], []),
        tool_tasks=(list[tool_calling_task], []),
        clarifications=(list[tool_selection_clarification], []),
    )


def node_03a_knowledge_blueprint_planner(state: AgentState):
    """Create incremental tool attempts nested under each financial subquery.

    Every round applies the exact same judgment, whether it's the first or
    the third: for each subquery still OPEN (_compute_subquery_status),
    decide which registered, enabled tools are genuinely relevant -- RAG
    included as an ordinary candidate, not auto-created. This is also the
    single place the "does an untried capability's scope actually match
    this subquery" semantic judgment happens; nothing downstream re-derives
    it (Tool Calling v3.0).

    Relevance is the model's job; cost is not. Every "rag" candidate is
    tagged needed/complementary/redundant by the model, and a small,
    deterministic pass afterward decides whether a "redundant" one survives
    -- it does only when some other subquery already needs a RAG batch
    this round anyway, since node_02e/node_03c batch every pending rag
    attempt into one model call per round regardless of count, so riding
    along on an already-committed batch is nearly free while initiating
    one solely for a droppable subquery is not.
    """
    print("🟢 [NODE 03a] Executing Tool-Calling Planner")
    financial_subqueries = [
        {**item, "tool_attempts": [dict(a) for a in item.get("tool_attempts", []) if isinstance(a, dict)]}
        for item in state.get("financial_subqueries", state.get("triaged_subqueries", []))
        if isinstance(item, dict) and item.get("subquery_id") and item.get("customer_component")
    ]
    known_subqueries = {item["subquery_id"]: item for item in financial_subqueries}
    existing_attempts = [attempt for item in financial_subqueries for attempt in item["tool_attempts"]]
    existing_keys = {
        (a.get("subquery_id"), a.get("tool_name"), a.get("operation"), json.dumps(a.get("input", {}), sort_keys=True))
        for a in existing_attempts
    }
    sequence_counter: dict[tuple, int] = {}
    for attempt in existing_attempts:
        seq_key = (attempt.get("subquery_id"), attempt.get("tool_name"))
        sequence_counter[seq_key] = sequence_counter.get(seq_key, 0) + 1
    planning_round = int(state.get("tool_planning_round", 0)) + 1
    # A subquery any node has already flagged for clarification this turn
    # is excluded from further planning -- asking the customer a second,
    # unrelated question about the same subquery (or re-litigating this
    # node's own already-decided tool-selection ambiguity every round)
    # would be confusing, and re-evaluating it risks the exact spurious
    # match this exclusion exists to prevent (see
    # DECLINE_CLARIFICATION_DESIGN_v1.0.md's tool-dispatcher section).
    already_clarified_subquery_ids = {
        entry.get("subquery_id") for entry in state.get("clarification_tasks", [])
        if isinstance(entry, dict)
    }
    open_subquery_ids = [
        item["subquery_id"] for item in financial_subqueries
        if _compute_subquery_status(item) == "OPEN"
        and item["subquery_id"] not in already_clarified_subquery_ids
    ]

    if planning_round > MAX_TOOL_PLANNING_ROUNDS or len(existing_attempts) >= MAX_TOOL_TASKS_PER_TURN:
        print("   ⛔ [TOOL LOOP EXHAUSTED]: Planning budget reached.")
        log_event("tool.loop.exhausted", planning_round=planning_round, task_count=len(existing_attempts))
        return {
            "preflight_status": "tool_results_ready",
            "tool_plan_status": "exhausted",
        }

    candidates = []
    clarification_candidates = []
    if not existing_attempts or open_subquery_ids:
        validated_attempts_view = [
            {
                "subquery_id": a.get("subquery_id"),
                "tool_name": a.get("tool_name"),
                "status": a.get("status"),
                "result": a.get("result", {}),
                "availability_reason": a.get("availability_reason", ""),
            }
            for a in existing_attempts
            if a.get("status") in {"completed", "unavailable"}
        ]
        financial_subqueries_view = [
            {"subquery_id": item["subquery_id"], "customer_component": item["customer_component"]}
            for item in financial_subqueries
        ]
        capabilities = enabled_tool_capabilities()

        prompt = f"""
        INCREMENTAL TOOL-CALLING PLANNER
        OVERVIEW: you decide which tools to run for the customer's banking
        needs this turn. This prompt gives, in order: what your plan is for;
        what each tool is for; the decision procedure; the format rules; the
        output format. This call's data -- the enabled tool registry, the
        customer's financial subqueries, which of them still have a gap this
        round, and the tool results already validated -- comes at the end.

        WHAT YOUR PLAN IS FOR
        Each task you plan gathers something the customer's reply needs:
        evidence to answer from, the customer's own data, an app screen to
        send them to, or a form for the bank to process. A subquery you plan
        nothing for gets nothing: the reply can then only tell the customer
        that no information is available. You never see the FAQ's content --
        whether the FAQ covers a need is found out by the FAQ search itself
        and judged later by the sufficiency auditor, never by you.

        WHAT EACH TOOL IS FOR (domain -> tool)
        - "information" -> "rag" (FAQ search): the customer needs to know
          something -- how the bank works (policies, eligibility, rules,
          limits, fees, "can I...", "how does X work") or what they can do
          in their situation -- whether they ask it as a question, report it
          as a problem ("the ATM gave me the wrong amount"), or phrase it as
          a request that no other tool below can carry out. FAQ search is
          the default tool: any banking need that no more specific tool
          fully covers is an information need.
        - "personal_data_retrieval" -> "api": the customer asks for one
          specific, verified value of their own data already on file (e.g.
          "what's my address on file", "what's my loyalty tier") -- never a
          general policy, procedure, or "how do I..." question.
        - "high_risk_transaction" -> "deeplink": the customer wants to
          perform, or be taken to where they would themselves perform, a
          high-risk action (money movement, security changes) in the bank's
          own app screens.
        - "service_form_submission" -> "service_request": the customer wants
          the bank to process an approved low- or intermediate-risk service
          on their behalf through a guided form.
        A subquery can contain more than one domain. A stated personal
        REASON attached to a service or transaction ("I want to replace my
        card because I don't like its appearance") also carries an
        information need -- whether the bank recognises that reason -- even
        when phrased as a want: "Can I replace my card because I don't like
        its appearance?" is an eligibility question AND a service request;
        both domains apply.

        DECISION PROCEDURE -- for each subquery that still has a gap:
        1. Domains first: fill in its "subquery_domains" entry, naming every
           domain above that the subquery contains, with your reasoning.
        2. Specific tools (api / deeplink / service_request): for each of
           those domains, choose the registry entry whose WHOLE description
           covers the customer's actual situation -- match on the whole
           description, not the nearest keyword. A description can scope out
           specific reasons or situations: one covering card replacement
           "for a non-fraud reason (worn or damaged)" does not cover a lost
           or stolen card, even though both are "card replacement". If no
           entry covers the situation, plan no task for that tool -- what
           the customer can do instead is then an information need (step 3).
        3. FAQ search ("rag"): plan one whenever the subquery has an
           information need -- including every need step 2 found no tool
           for, and a stated reason whose validity no selected tool's
           description confirms -- unless a FAQ search for that subquery has
           already reached a terminal state (its internal retries are not
           your tasks). Set "rag_role":
           * "needed" -- no other tool covers this subquery.
           * "complementary" -- another tool also covers it, but explaining
             the underlying policy or procedure has its own value to the
             customer (the manual method is never made obsolete just because
             a shortcut exists), or a stated reason's validity is still
             unconfirmed.
           * "redundant" -- another tool fully covers it and the FAQ would
             only restate what that tool provides.
           Tag honestly. The FAQ search is the only tool with real cost (it
           can take several rounds); the orchestration, not you, decides
           afterwards whether a "redundant" task is still worth running --
           never weigh cost yourself.
        4. Tie between specific tools: if two or more registered
           capabilities are each independently plausible for the SAME
           subquery, their outcomes for the customer are materially
           different (e.g. self-service in the app vs. a request for staff
           to action), and nothing in the subquery's wording lets you prefer
           one, add a "clarifications" entry for that subquery INSTEAD of
           tool_tasks for it (never both). This is only for a genuine tie
           between real options -- an imperfect or missing match is handled
           by steps 2 and 3, never by a clarification.
           Set "unresolved_request": reasoning directly against the
           subquery's own wording, the specific choice that is unclear
           (e.g. "whether you want to update this yourself in the app or
           have us submit a request for you"). Set "clarification_reason":
           the capabilities in tension and why neither is clearly
           preferable. Set "clarification_guesses": one per plausible
           option, each a complete, natural sentence the customer might
           send, grounded in that option's own registered description --
           never inventing an outcome it does not have.
        A subquery gets no task only when every tool that fits it has
        already completed or reached a terminal state for it.

        GENERAL RULES
        - Plan only additive tasks: never recreate a completed, unavailable,
          or pending task, and never plan a task merely to restate, search
          again, or rewrite a completed result.
        - Every task references exactly one subquery_id from "Subqueries
          with a remaining gap this round" -- never any other.
        - Use only an enabled tool_name and one of its listed operations.
          Never invent facts, customer data, or capabilities.

        FORMAT RULES FOR "input"
        - "rag": {{"customer_component": <that subquery's customer_component>}}.
        - "service_request": {{"service_request_id": <an id from that tool's
          available_requests>}}. Never include form fields.
        - "deeplink": {{"destination_id": <an id from that tool's
          available_destinations>}}, e.g. {{"destination_id": "card-management"}}.
          A deeplink is shown to the customer as a button.
        - "api": {{"field_id": <an id from that tool's available_fields>}},
          e.g. {{"field_id": "profile-address"}}.
        - CRITICAL: "customer_component" is valid only in a "rag" task's
          input. For "service_request", "deeplink" and "api", the value must
          be copied character for character from that tool's own registry
          list -- never the customer's wording, a description, or a restated
          subquery. If no listed id fits, plan no task for that tool.
          WRONG: {{"tool_name":"service_request","input":{{"customer_component":"How can I change my registered address?"}}}}
          RIGHT: {{"tool_name":"service_request","input":{{"service_request_id":"profile-address-update"}}}}
          The same applies to "deeplink" (destination_id) and "api" (field_id).

        Output JSON only:
        {{"subquery_domains":[{{"subquery_id":"...","domain_reasoning":"...","domains":["information"|"high_risk_transaction"|"personal_data_retrieval"|"service_form_submission"]}}], "tool_tasks":[{{"subquery_id":"...","tool_name":"...","operation":"...","input":{{}},"rag_role":"needed"|"complementary"|"redundant"}}], "clarifications":[{{"subquery_id":"...","unresolved_request":"...","clarification_reason":"...","clarification_guesses":[{{"label":"...","message":"...","reason":"..."}}]}}]}}

        {_DATA_FOLLOWS}

        Enabled tool registry: {json.dumps(capabilities, ensure_ascii=False)}
        Financial subqueries: {json.dumps(financial_subqueries_view, ensure_ascii=False)}
        Subqueries with a remaining gap this round: {json.dumps(open_subquery_ids, ensure_ascii=False)}
        Validated terminal tool results: {json.dumps(validated_attempts_view, ensure_ascii=False)}
        """
        try:
            planner_result = _parse_json_robustly(
                call_groq_lpu(prompt, model_id=MODEL_C_PLANNER, response_schema_override=_tool_calling_plan_schema(capabilities))
            )
            candidates = planner_result.get("tool_tasks", [])
            clarification_candidates = planner_result.get("clarifications", [])
            subquery_domains = planner_result.get("subquery_domains", [])
            if isinstance(subquery_domains, list) and subquery_domains:
                log_event("tool.plan.subquery_domains", planning_round=planning_round, subquery_domains=subquery_domains)
        except Exception as error:
            print(f"⚠️ [TOOL PLANNER ERROR]: {error}")
            candidates = []
            clarification_candidates = []

    new_attempts_by_subquery: dict[str, list[dict]] = {}
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
        rag_role = None
        if tool_name == "rag":
            payload = {"customer_component": known_subqueries[subquery_id]["customer_component"]}
            # Missing/malformed tags default to "needed" -- the safest
            # assumption, since RAG is the catch-all fallback source; an
            # under-tagged candidate should never be the one that
            # accidentally gets dropped by the cost tiebreak below.
            rag_role = str(candidate.get("rag_role", "")).strip().lower()
            if rag_role not in {"needed", "complementary", "redundant"}:
                rag_role = "needed"
        elif tool_name == "service_request":
            # The planner is told to key this "service_request_id", but the
            # prompt also shows customer_component under Financial subqueries
            # and rag tasks -- in practice the model sometimes copies that
            # key by pattern-matching instead. The registered id is a closed,
            # validated set, so accept the correct value under any key rather
            # than silently dropping an otherwise-correct choice for a
            # cosmetic key mismatch.
            requested_id = str(payload.get("service_request_id") or "").strip()
            if get_service_request_contract(requested_id) is None:
                requested_id = next(
                    (str(value).strip() for value in payload.values() if value and get_service_request_contract(str(value).strip()) is not None),
                    "",
                )
            if get_service_request_contract(requested_id) is None:
                log_event("tool.task.invalid_service_request_id", subquery_id=subquery_id, requested_id=requested_id, raw_input=payload, level="WARNING")
                continue
            payload = {"service_request_id": requested_id}
        elif tool_name == "deeplink":
            # Same allow-list discipline as service_request above: accept the
            # registered destination_id under any key, reject anything that
            # doesn't resolve to a real registry entry. Only the planner
            # decides whether a destination is worth offering; the actual
            # dispatch (route lookup, attempt result) happens once in
            # node_03b_tool_dispatcher, not here.
            requested_destination = str(payload.get("destination_id") or "").strip()
            if requested_destination not in DEEPLINK_DESTINATION_REGISTRY:
                requested_destination = next(
                    (str(value).strip() for value in payload.values() if value and str(value).strip() in DEEPLINK_DESTINATION_REGISTRY),
                    "",
                )
            if requested_destination not in DEEPLINK_DESTINATION_REGISTRY:
                log_event("tool.task.invalid_deeplink_destination", subquery_id=subquery_id, requested_destination=requested_destination, raw_input=payload, level="WARNING")
                continue
            payload = {"destination_id": requested_destination}
        elif tool_name == "api":
            # Same allow-list discipline as service_request/deeplink above:
            # accept the registered field_id under any key, reject anything
            # that doesn't resolve to a real registry entry. The actual fetch
            # (and the placeholder/direct-value split) happens once in
            # node_03b_tool_dispatcher, not here.
            requested_field = str(payload.get("field_id") or "").strip()
            if requested_field not in API_FIELD_REGISTRY:
                requested_field = next(
                    (str(value).strip() for value in payload.values() if value and str(value).strip() in API_FIELD_REGISTRY),
                    "",
                )
            if requested_field not in API_FIELD_REGISTRY:
                log_event("tool.task.invalid_api_field", subquery_id=subquery_id, requested_field=requested_field, raw_input=payload, level="WARNING")
                continue
            payload = {"field_id": requested_field}
        key = (subquery_id, tool_name, operation, json.dumps(payload, sort_keys=True))
        if key in existing_keys:
            log_event("tool.task.deduplicated", subquery_id=subquery_id, tool_name=tool_name, operation=operation)
            continue
        seq_key = (subquery_id, tool_name)
        sequence_counter[seq_key] = sequence_counter.get(seq_key, 0) + 1
        attempt_id = _build_attempt_id(subquery_id, tool_name, planning_round, sequence_counter[seq_key])
        new_attempt = {
            "attempt_id": attempt_id,
            "subquery_id": subquery_id,
            "tool_name": tool_name,
            "operation": operation,
            "planning_round": planning_round,
            "status": "pending",
            "input": payload,
            "result": {},
            "availability_reason": "",
        }
        if tool_name == "rag":
            new_attempt["rag_role"] = rag_role
        new_attempts_by_subquery.setdefault(subquery_id, []).append(new_attempt)
        existing_keys.add(key)

    # Tool-selection clarifications: node_03a's own scope (which capability
    # applies), never product/intent or evidence sufficiency. A subquery
    # that got a real tool task this same round takes priority -- a
    # decisive answer beats asking, and the prompt already instructs the
    # model not to propose both, this is only a defensive backstop.
    subquery_ids_with_new_task_this_round = set(new_attempts_by_subquery.keys())
    new_tool_selection_clarifications = []
    for candidate in clarification_candidates if isinstance(clarification_candidates, list) else []:
        if not isinstance(candidate, dict):
            continue
        subquery_id = str(candidate.get("subquery_id", "")).strip()
        unresolved_request = str(candidate.get("unresolved_request", "")).strip()
        if (
            subquery_id not in known_subqueries
            or subquery_id in subquery_ids_with_new_task_this_round
            or subquery_id in already_clarified_subquery_ids
            or subquery_id in {c["subquery_id"] for c in new_tool_selection_clarifications}
            or not unresolved_request
        ):
            continue
        new_tool_selection_clarifications.append({
            "subquery_id": subquery_id,
            "originator": "task_dispatcher",
            "scope": _CLARIFICATION_SCOPE_BY_ORIGINATOR["task_dispatcher"],
            "unresolved_request": unresolved_request,
            "clarification_reason": str(candidate.get("clarification_reason", "Two or more registered capabilities are each plausible, with no basis to prefer one.")).strip(),
            "clarification_guesses": _normalize_clarification_guesses(candidate.get("clarification_guesses", [])),
        })
    clarification_tasks = list(state.get("clarification_tasks", [])) + new_tool_selection_clarifications

    # Deterministic cost tiebreak: a "redundant" rag candidate -- one a
    # cheaper tool already fully covers -- is only worth including if a RAG
    # batch is already happening this round anyway for some other "needed"
    # subquery. node_02e/node_03c batch every currently-pending rag attempt
    # into one model call per round regardless of how many subqueries are
    # in it, so riding along on an already-committed batch costs close to
    # nothing; initiating one solely for a droppable subquery pays the full
    # cost for no independent value. "complementary" and "needed" tags are
    # never dropped -- only "redundant" ever is, and only when nothing else
    # this round needs RAG at all.
    all_new_attempts = [a for attempts in new_attempts_by_subquery.values() for a in attempts]
    rag_batch_happening = any(a.get("tool_name") == "rag" and a.get("rag_role") == "needed" for a in all_new_attempts)
    if not rag_batch_happening:
        for subquery_id, attempts in new_attempts_by_subquery.items():
            kept, dropped = [], []
            for attempt in attempts:
                if attempt.get("tool_name") == "rag" and attempt.get("rag_role") == "redundant":
                    dropped.append(attempt)
                else:
                    kept.append(attempt)
            for attempt in dropped:
                log_event("tool.task.rag_redundant_dropped", tool_task_id=attempt["attempt_id"], subquery_id=subquery_id, planning_round=planning_round)
            new_attempts_by_subquery[subquery_id] = kept

    total_new = 0
    for subquery in financial_subqueries:
        additions = new_attempts_by_subquery.get(subquery["subquery_id"], [])
        if not additions:
            continue
        subquery["tool_attempts"] = subquery["tool_attempts"] + additions
        total_new += len(additions)
        for attempt in additions:
            log_event("tool.task.created", tool_task_id=attempt["attempt_id"], subquery_id=attempt["subquery_id"], tool_name=attempt["tool_name"], operation=attempt["operation"], planning_round=attempt["planning_round"])

    for entry in new_tool_selection_clarifications:
        log_event("task.dispatcher.clarification_raised", subquery_id=entry["subquery_id"], planning_round=planning_round)
    # A new clarification counts as real progress this round too, same as a
    # new tool attempt -- otherwise a round that clarifies subquery A but
    # finds nothing yet for a still-genuinely-open subquery B would read as
    # "the planner found nothing new" (node_04a's own exhaustion signal),
    # artificially cutting off B's remaining rounds over A's unrelated
    # outcome. preflight_status stays keyed on total_new alone: a
    # clarification is never dispatched to node_03b, only a real tool
    # attempt is.
    made_progress = bool(total_new or new_tool_selection_clarifications)
    print(f"   🧰 [TOOL PLAN]: round {planning_round} added {total_new} attempt(s), {len(new_tool_selection_clarifications)} clarification(s)")
    log_event("tool.plan.created", planning_round=planning_round, capability_count=len(enabled_tool_capabilities()))
    return {
        "preflight_status": "tool_dispatch" if total_new else "tool_results_ready",
        "financial_subqueries": financial_subqueries,
        "tool_planning_round": planning_round,
        "tool_plan_status": "continue" if made_progress else "exhausted",
        "clarification_tasks": clarification_tasks,
        "rag_payload": "",
        "api_payload": "",
        "deeplink_payload": "",
    }


def _dispatch_api_task(state: AgentState, task: dict) -> tuple[dict, dict]:
    """Fetch one registered `api` field synchronously -- a deterministic,
    LLM-free read. A placeholder-mode field's real value is never placed in
    the returned tool_result (which later gets dumped verbatim into more
    than one LLM prompt, e.g. node_04a's resolution planner) -- only a
    freshly minted, single-turn opaque token is. The real value is returned
    separately, keyed by that same token, for the caller to fold into the
    turn's personal_data_reveal_map, which no LLM prompt ever reads; only
    node_07 consumes it, to hand the value to the frontend alongside (never
    inside) the customer-facing text. A direct-value field has no such
    restriction -- it is not identity-exposing, so its value and a stated
    fact sentence go straight into the tool_result.

    Returns (outcome, reveal_map_additions).
    """
    field_id = str(task.get("input", {}).get("field_id", "")).strip()
    field = API_FIELD_REGISTRY.get(field_id)
    if field is None:
        return {"status": "unavailable", "reason": f"The field '{field_id}' is not registered."}, {}

    value = field["fetch"](state.get("user_id", ""))
    if field["disclosure_mode"] == "placeholder":
        token = f"{{{{data-{secrets.token_hex(4)}}}}}"
        return {
            "status": "completed",
            "approved_result": {
                "field_id": field_id,
                "disclosure_mode": "placeholder",
                "placeholder_token": token,
            },
        }, {token: str(value)}

    fact_template = field.get("fact_template")
    fact = fact_template(value) if fact_template else f"The customer's {field_id} is {value}."
    return {
        "status": "completed",
        "approved_result": {
            "field_id": field_id,
            "disclosure_mode": "direct_value",
            "value": value,
            "fact": fact,
        },
    }, {}


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


def _dispatch_deeplink_task(state: AgentState, task: dict) -> dict:
    """Register then centrally resolve a navigation request.

    The graph requests only a destination ID.  ``deeplink_service`` persists
    that request first and is the only layer that allow-lists a route.
    """
    destination_id = str(task.get("input", {}).get("destination_id", "")).strip()
    subquery_id = task.get("subquery_id")
    resolved_component = next(
        (item.get("customer_component", "") for item in state.get("financial_subqueries", []) if item.get("subquery_id") == subquery_id),
        "",
    )
    record = deeplink_service.register_and_resolve(
        session_id=state.get("session_id", ""),
        user_id=state.get("user_id", ""),
        destination_id=destination_id,
        resolved_customer_component=resolved_component,
    )
    if record is None:
        return {"status": "unavailable", "reason": "That navigation request could not be found."}
    if record.get("status") != deeplink_service.RESOLVED or not record.get("route"):
        return {"status": "unavailable", "reason": "That navigation destination is not available."}
    return {
        "status": "completed",
        "approved_result": {
            "deeplink_request_id": record["request_id"],
        },
    }


def _iter_tool_attempts(state: AgentState):
    """Yield every (attempt, for_request) pair across every subquery's
    tool_attempts -- `for_request` is that attempt's own owning subquery's
    customer_component text, free of any separate subquery_id join, since
    the attempt is already nested under it (Tool Calling v3.0)."""
    for subquery in state.get("financial_subqueries", state.get("triaged_subqueries", [])):
        if not isinstance(subquery, dict):
            continue
        for_request = subquery.get("customer_component", "")
        for attempt in subquery.get("tool_attempts", []):
            if isinstance(attempt, dict):
                yield attempt, for_request


def _pending_service_requests(state: AgentState) -> list[dict]:
    """Completed service_request attempts for this turn only.

    Exposes only special_id, service_request_id, and description -- never
    the form_schema. The LLM's job is to write one honest sentence inviting
    the customer to complete a form it cannot see the fields of; the actual
    form-opening UI action is attached deterministically in node_07, not by
    anything the model generates.
    """
    return [
        {
            "special_id": attempt.get("result", {}).get("special_id"),
            "service_request_id": attempt.get("result", {}).get("service_request_id"),
            "description": attempt.get("result", {}).get("description"),
        }
        for attempt, _ in _iter_tool_attempts(state)
        if attempt.get("tool_name") == "service_request"
        and attempt.get("status") == "completed"
        and attempt.get("result", {}).get("special_id")
    ]


def _unavailable_service_requests(state: AgentState) -> list[dict]:
    """service_request attempts this turn that could not be registered.

    Without this, a blocked attempt (e.g. the one-active-request-per-session
    guard) silently disappears from the response with no acknowledgment at
    all -- the customer's second request vanishes and nothing explains why.
    Only the deterministic reason recorded at dispatch time is exposed; nothing
    here is model-authored.
    """
    entries = [
        {"reason": attempt.get("availability_reason", "")}
        for attempt, _ in _iter_tool_attempts(state)
        if attempt.get("tool_name") == "service_request"
        and attempt.get("status") == "unavailable"
        and attempt.get("availability_reason")
    ]
    return [dict(entry, handle=f"unavail_{n}") for n, entry in enumerate(entries, start=1)]


def _resolved_deeplink_request_ids(state: AgentState) -> list[str]:
    """Completed opaque deep-link request IDs for this turn only.

    Node 07 persists only these IDs with the chat UI payload. The central
    navigation API retains the destination and route, re-authorizing them
    when the customer later clicks a historic button.
    """
    return [
        attempt.get("result", {}).get("deeplink_request_id")
        for attempt, _ in _iter_tool_attempts(state)
        if attempt.get("tool_name") == "deeplink"
        and attempt.get("status") == "completed"
        and attempt.get("result", {}).get("deeplink_request_id")
    ]


def _available_response_shortcuts(state: AgentState) -> list[dict]:
    """Sanitized, non-identifying view of this turn's shortcuts.

    Combines completed service_request registrations and resolved deeplink
    destinations into one list carrying a `kind`, a customer-facing `label`,
    a short `topic` description, and `for_request` -- the customer's own
    subquery text this shortcut was registered to address, verbatim from
    triage. Never a special_id, service_request_id, destination_id, route,
    or form_schema. `for_request` exists so the generator can recognize,
    by literal text match rather than inference, when a subquery it has zero
    RAG evidence for is nonetheless fully covered by a shortcut -- without
    it, the model has no way to know a given shortcut answers the exact
    question it just found no FAQ evidence for, and defaults to an apology
    before belatedly mentioning the shortcut as an afterthought. The
    response generator may use this only to write natural text; it is never
    asked to identify, place, or attach the actual UI itself. The real
    button ("navigation") or embedded fillable form card ("form") is built
    separately and deterministically in node_07 straight from
    tool_attempts, regardless of whether the generated text mentions it at
    all -- so unlike the id-based approaches tried before, nothing here is
    load-bearing for correctness, only for how natural the response reads.
    """
    shortcuts = []
    for shortcut, _action_key, _subquery_id in _iter_shortcut_entries(state):
        shortcuts.append(shortcut)
    # Per-turn alias, the same kind of handle a RAG task has (task_N). It is
    # NOT a real id -- just a positional label the generator cites in
    # source_task_ids so the shortcut is a tracked obligation like any task.
    # Positional, so every caller in one turn derives the same handle.
    for position, shortcut in enumerate(shortcuts, start=1):
        shortcut["handle"] = f"{'nav' if shortcut['kind'] == 'navigation' else 'svc'}_{position}"
    return shortcuts


def _iter_shortcut_entries(state: AgentState):
    """Yield (sanitized shortcut, action_key, subquery_id) per completed
    service_request / resolved deeplink, in tool order. `action_key` is what
    node_07 keys the real UI action by ("service_request", special_id) or
    ("deeplink", request_id) -- backend-only, never shown to a model."""
    for subquery in state.get("financial_subqueries", state.get("triaged_subqueries", [])):
        if not isinstance(subquery, dict):
            continue
        for_request = subquery.get("customer_component", "")
        for attempt in subquery.get("tool_attempts", []):
            if not isinstance(attempt, dict) or attempt.get("status") != "completed":
                continue
            result = attempt.get("result", {})
            if attempt.get("tool_name") == "service_request":
                contract = get_service_request_contract(result.get("service_request_id", ""))
                if contract is None:
                    continue
                yield ({
                    "kind": "form",
                    "label": contract.get("action_label", "Complete Form"),
                    "topic": contract.get("description", ""),
                    "for_request": for_request,
                }, ("service_request", result.get("special_id")), subquery.get("subquery_id"))
            elif attempt.get("tool_name") == "deeplink":
                request = deeplink_service.get(result.get("deeplink_request_id", ""))
                if request is None or request.get("status") != deeplink_service.RESOLVED:
                    continue
                contract = DEEPLINK_DESTINATION_REGISTRY.get(request.get("destination_id", ""))
                if contract is None:
                    continue
                yield ({
                    "kind": "navigation",
                    "label": contract.get("label", "Open"),
                    "topic": contract.get("description", ""),
                    "for_request": for_request,
                }, ("deeplink", result.get("deeplink_request_id")), subquery.get("subquery_id"))


def _shortcut_anchor_block_ids(state: AgentState, response_blocks: list) -> dict:
    """Map each shortcut's action_key to the blockId its card should follow.

    The anchor is the last block that cites the shortcut's handle (the
    sentence the model -- or the coverage net -- wrote for it). Purely
    positional bookkeeping over final, already-audited blocks; it never
    changes text. A shortcut with no citing block simply has no anchor, and
    the frontend then renders its card at the end of the message.
    """
    anchors = {}
    handles = {}
    for position, (shortcut, action_key, _subquery_id) in enumerate(_iter_shortcut_entries(state), start=1):
        handles[action_key] = f"{'nav' if shortcut['kind'] == 'navigation' else 'svc'}_{position}"
    for action_key, handle in handles.items():
        for block in response_blocks:
            if isinstance(block, dict) and handle in (block.get("sourceShortcutHandles") or []) and block.get("blockId"):
                anchors[action_key] = block["blockId"]
    return anchors


def _personal_data_placeholders(state: AgentState) -> list[dict]:
    """Sanitized view of this turn's completed placeholder-mode `api` fetches.

    Exposes only a field description, the customer's own subquery text this
    fetch was for, and the opaque insert_token -- never the real value,
    which lives only in personal_data_reveal_map, a state field no LLM
    prompt ever reads. The generator's only job is deciding whether and how
    to weave insert_token into a natural sentence; node_07 never resolves
    it -- the frontend does, from the reveal map delivered alongside (never
    inside) this turn's response text.
    """
    placeholders = []
    for attempt, for_request in _iter_tool_attempts(state):
        if attempt.get("tool_name") != "api" or attempt.get("status") != "completed":
            continue
        result = attempt.get("result", {})
        if result.get("disclosure_mode") != "placeholder" or not result.get("placeholder_token"):
            continue
        field = API_FIELD_REGISTRY.get(result.get("field_id", ""))
        if field is None:
            continue
        placeholders.append({
            "for_request": for_request,
            "field_description": field.get("description", ""),
            "insert_token": result["placeholder_token"],
            "handle": f"data_{len(placeholders) + 1}",
        })
    return placeholders


def _verified_api_context(state: AgentState) -> list[dict]:
    """Direct-value `api` facts safe to state directly (WP-5).

    Only disclosure_mode == "direct_value" entries qualify -- placeholder-mode
    facts stay confined to _personal_data_placeholders and are never treated
    as narratable evidence text, since that would let a raw personal-data
    value leak into the model's own generated sentence structure instead of
    staying a fixed, audited token substitution.
    """
    facts = []
    for attempt, for_request in _iter_tool_attempts(state):
        if attempt.get("tool_name") != "api" or attempt.get("status") != "completed":
            continue
        result = attempt.get("result", {})
        if result.get("disclosure_mode") != "direct_value" or not result.get("fact"):
            continue
        facts.append({
            "for_request": for_request,
            "fact": result["fact"],
            "handle": f"fact_{len(facts) + 1}",
        })
    return facts


def node_03b_tool_dispatcher(state: AgentState):
    """Dispatch pending attempts without hard-coding graph fan-out.

    RAG is handed to its existing internal retrieval subgraph -- it needs
    retries and stays async. service_request is a one-shot, synchronous,
    LLM-free DB write, so it resolves inline here rather than needing its
    own graph node. Tools without an enabled executor terminate as
    unavailable and cannot block the turn.
    """
    print("🟢 [NODE 03b] Executing Generic Tool Dispatcher")
    financial_subqueries = [
        {**item, "tool_attempts": [dict(a) for a in item.get("tool_attempts", []) if isinstance(a, dict)]}
        for item in state.get("financial_subqueries", state.get("triaged_subqueries", []))
        if isinstance(item, dict)
    ]
    personal_data_reveal_map = dict(state.get("personal_data_reveal_map", {}))
    has_running_rag = False

    for subquery in financial_subqueries:
        for attempt in subquery["tool_attempts"]:
            if attempt.get("status") != "pending":
                continue
            tool_name = attempt.get("tool_name")
            if tool_name == "rag":
                attempt["status"] = "running"
                has_running_rag = True
                log_event("tool.dispatch.started", tool_task_id=attempt["attempt_id"], subquery_id=attempt.get("subquery_id"), tool_name=tool_name)
                continue
            if tool_name == "service_request":
                outcome = _dispatch_service_request_task(state, attempt)
                attempt["status"] = outcome["status"]
                attempt["result"] = outcome.get("approved_result", {})
                attempt["availability_reason"] = outcome.get("reason", "")
                log_event(
                    "tool.result.service_request",
                    tool_task_id=attempt["attempt_id"], subquery_id=attempt.get("subquery_id"),
                    status=outcome["status"],
                    special_id=outcome.get("approved_result", {}).get("special_id"),
                )
                continue
            if tool_name == "deeplink":
                destination_id = attempt.get("input", {}).get("destination_id")
                outcome = _dispatch_deeplink_task(state, attempt)
                attempt["status"] = outcome["status"]
                attempt["result"] = outcome.get("approved_result", {})
                attempt["availability_reason"] = outcome.get("reason", "")
                log_event(
                    "tool.result.deeplink",
                    tool_task_id=attempt["attempt_id"], subquery_id=attempt.get("subquery_id"),
                    status=attempt["status"],
                    destination_id=destination_id,
                    deeplink_request_id=attempt["result"].get("deeplink_request_id"),
                )
                continue
            if tool_name == "api":
                outcome, reveal_additions = _dispatch_api_task(state, attempt)
                attempt["status"] = outcome["status"]
                attempt["result"] = outcome.get("approved_result", {})
                attempt["availability_reason"] = outcome.get("reason", "")
                personal_data_reveal_map.update(reveal_additions)
                log_event(
                    "tool.result.api",
                    tool_task_id=attempt["attempt_id"], subquery_id=attempt.get("subquery_id"),
                    status=outcome["status"],
                    field_id=attempt["result"].get("field_id", attempt.get("input", {}).get("field_id")),
                    disclosure_mode=attempt["result"].get("disclosure_mode"),
                )
                continue
            attempt["status"] = "unavailable"
            attempt["result"] = {}
            attempt["availability_reason"] = "This tool is not enabled in the current deployment."
            log_event("tool.result.unavailable", tool_task_id=attempt["attempt_id"], subquery_id=attempt.get("subquery_id"), tool_name=tool_name)

    return {
        "preflight_status": "rag_tool_active" if has_running_rag else "tool_results_ready",
        "financial_subqueries": financial_subqueries,
        "personal_data_reveal_map": personal_data_reveal_map,
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
    history.append(AIMessage(content=content_block))

    return {
        "final_response": content_block,
        "final_draft": content_block,
        "final_answer": content_block,
        "ui_action_payload": {"uiType": "none", "options": []},
        "chat_history": history,
        "service_request_event": {},
    }


def _finalize_rag_attempts(state: AgentState, task_results: list[dict]) -> list[dict]:
    """Project RAG's terminal, internal search-task outcome onto the owning
    subquery's `rag` attempt (Tool Calling v3.0's one integration point
    between RAG's own internal retry machinery -- search_tasks/task_results/
    task_retry_counts/etc., left entirely untouched by this migration -- and
    the subquery-owned tool_attempts schema everything else now reads).

    Called with the current turn's terminal `task_results` once every
    search task has settled, or with an empty list when no search task
    could even be formed (e.g. every component resolved to CLARIFY/DECLINE)
    -- either way, every subquery's pending/running `rag` attempt must be
    closed out so it never sits open forever.
    """
    search_tasks_by_subquery: dict[str, list[dict]] = {}
    for task in state.get("search_tasks", []):
        if isinstance(task, dict) and task.get("subquery_id"):
            search_tasks_by_subquery.setdefault(task["subquery_id"], []).append(task)
    results_by_task_id = {result.get("task_id"): result for result in task_results if isinstance(result, dict)}

    updated_subqueries = []
    for subquery in state.get("financial_subqueries", state.get("triaged_subqueries", [])):
        if not isinstance(subquery, dict):
            continue
        subquery = dict(subquery)
        subquery_id = subquery.get("subquery_id")
        new_attempts = []
        for attempt in subquery.get("tool_attempts", []):
            if not isinstance(attempt, dict):
                continue
            if attempt.get("tool_name") != "rag" or attempt.get("status") not in {"pending", "running"}:
                new_attempts.append(attempt)
                continue
            attempt = dict(attempt)
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
            if approved:
                combined_parent_ids = list(dict.fromkeys(
                    parent_id for item in approved for parent_id in item["approved_parent_ids"]
                ))
                retrieval_state = "SUFFICIENT" if any(item["status"] == "SUFFICIENT" for item in approved) else "RELEVANT"
                attempt["status"] = "completed"
                attempt["result"] = {"retrieval_state": retrieval_state, "approved_parent_ids": combined_parent_ids}
                attempt["availability_reason"] = ""
            else:
                attempt["status"] = "unavailable"
                attempt["result"] = {}
                attempt["availability_reason"] = "No approved FAQ evidence resolved this financial subquery."
            log_event(
                "tool.result.validated",
                tool_task_id=attempt.get("attempt_id"),
                subquery_id=subquery_id,
                tool_name="rag",
                status=attempt["status"],
                approved_search_task_ids=[item["task_id"] for item in approved],
            )
            new_attempts.append(attempt)
        subquery["tool_attempts"] = new_attempts
        updated_subqueries.append(subquery)
    return updated_subqueries

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
        # Extra Effort v2.0/2.1: every attempt searches all four
        # representations (procedural/policy/conceptual + the resolved
        # subquery text) as two independent tracks -- semantic angle-group
        # RRF, and direct query (with verified-session boosting) on its
        # own -- unioned before hydration rather than RRF-fused into one
        # ranking, so a strong literal match can't be diluted by the three
        # semantic angles or vice versa. See _retrieval_mode_for_task and
        # execute_multi_angle_search's docstring.
        parents = await engine.execute_multi_angle_search(
            blueprint=task,
            scope_pairs=scope_pairs,
            excluded_parent_ids=excluded_parent_ids,
            direct_query=task.get("direct_query", task.get("customer_component", "")),
            verified_session_parent_ids=verified_session_parent_ids,
        )

        # Fairness/consistency mitigation (BUG-023, BUG-025): a parent id
        # that has, historically, resolved a close enough question is
        # added as an *extra* candidate here -- never a replacement, never
        # a bypass of the sufficiency audit below, which independently
        # judges every candidate on its own merits regardless of origin.
        # See answer_cache.py's module docstring and
        # docs/FAIRNESS_ANSWER_CACHE_DESIGN.md for the full rationale.
        cache_hits = get_answer_cache().lookup(task.get("direct_query", task.get("customer_component", "")))
        if cache_hits:
            existing_ids = {p.get("parent_id") for p in parents if p.get("parent_id")}
            new_ids = [
                pid for pid in dict.fromkeys(pid for hit in cache_hits for pid in hit["parent_ids"])
                if pid not in existing_ids and pid not in excluded_parent_ids
            ]
            if new_ids:
                parents = parents + engine.hydrate_parent_ids(new_ids)

        return {
            "task_id": task.get("task_id"),
            "status": "RETRIEVED",
            "origin": "RAG",
            "retrieval_mode": retrieval_mode,
            "verified_session_parent_ids": verified_session_parent_ids,
            "parents": parents,
            "cache_hits": cache_hits,
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
        cache_injected_parent_ids={
            result.get("task_id"): list(dict.fromkeys(
                pid for hit in result.get("cache_hits", []) for pid in hit["parent_ids"]
            ))
            for result in retrieved_results
            if result.get("cache_hits")
        },
    )
    cache_injected_by_task = dict(state.get("task_cache_injected_parent_ids", {}))
    for result in retrieved_results:
        if result.get("cache_hits"):
            cache_injected_by_task[result["task_id"]] = result["cache_hits"]
    return {
        "rag_payload": "",
        "task_results": list(merged_results.values()),
        "task_cache_injected_parent_ids": cache_injected_by_task,
    }

def node_03b_deeplink_worker(state: AgentState):
    return {"deeplink_payload": "[DEEPLINK DATA]:\nNo Deep-links requested for this MVP."}

def _dynamic_task_confidence(result: dict, partial_evidence: dict) -> float:
    """Live, per-task confidence for the round just completed. Reuses
    _score_subquery's 0/0.5/1.0 tiers for consistency, but reflects this
    task's OWN current-round outcome, not a historical session average --
    RAG's retry depth now reads this instead of the rolling confidence
    _score_subquery otherwise feeds (see RAG_RETRY_EFFORT_v1.0.md). RETRY
    (still mid-ladder, will try again next round) scores 0.5 if it has
    already accumulated real partial evidence this turn, else 0.0 -- the
    same distinction _score_subquery draws between a genuinely partial
    answer and nothing at all."""
    status = result.get("status")
    if status == "SUFFICIENT":
        return 1.0
    if status == "RELEVANT":
        return 0.5
    if status == "RETRY":
        return 0.5 if partial_evidence.get(str(result.get("task_id"))) else 0.0
    return 0.0  # CLARIFICATION_REQUIRED


def _reject_confidence_null_hypothesis(
    sample: list[float],
    population_mean_h0: float = CONFIDENCE_TEST_POPULATION_MEAN_H0,
    alpha: float = CONFIDENCE_TEST_ALPHA,
) -> tuple[bool, dict]:
    """One-tailed one-sample hypothesis test (t-test) on whether recent
    subquery grades provide significant evidence the TRUE (population) mean
    has dropped below population_mean_h0 -- not just a raw
    sample-average-vs-threshold check, which can't distinguish "genuinely
    degraded" from "a couple of turns were noisy." H0: population mean
    >= population_mean_h0 (things are fine). H1: population mean <
    population_mean_h0 (evidence of real decline). Reject H0 -> engage
    extra effort.

    Uses an exact t-distribution critical value (CONFIDENCE_TEST_T_CRITICAL,
    keyed by degrees of freedom = n-1), not a normal (z) approximation: the
    population standard deviation is never known here, only estimated from
    the sample, which is exactly what a t-test is for, at any sample size
    -- not only once n is large. This is a strict improvement over an
    earlier version of this function that used a fixed z critical value
    regardless of n: that understated the true sampling uncertainty for a
    small sample (using the same 1.645 the Central Limit Theorem only
    really justifies near n=30), which skewed toward rejecting H0 too
    easily for a customer who simply didn't have much history yet. The
    t-distribution's own critical value shrinks toward that same z-value
    as degrees of freedom grow, converging by df=30
    (CONFIDENCE_TEST_Z_CRITICAL is the fallback once a sample exceeds the
    table), so this subsumes the old behavior for a full sample while
    being properly conservative for a partial one -- no separate
    small-sample special case needed.

    Below MIN_SCORED_SUBQUERIES_FOR_CONFIDENCE_TRUST the test doesn't run
    at all (assumes H0) -- not because the t-distribution is invalid below
    that in general, but because a subquery grade only ever takes one of
    three discrete values (1.0/0.5/0.0), and how close population_mean_h0
    sits to that scale's 1.0 ceiling directly controls how badly a small
    sample of such scores miscalibrates this test (confirmed by Monte
    Carlo -- see that constant's own comment for the current numbers,
    which change whenever population_mean_h0 does). A brand-new or
    thin-history customer gets the unconditional floor (rounds 1-2) and
    nothing past it from this mechanism until the floor's sample size
    accumulates, the same "not enough track record to judge" stance this
    codebase takes elsewhere.

    Returns (reject_h0, diagnostics) so the decision is always inspectable
    (n, mean, std_dev, t, critical_t, df), never just a bare boolean.
    """
    n = len(sample)
    if n < MIN_SCORED_SUBQUERIES_FOR_CONFIDENCE_TRUST:
        # Still report the raw sample mean even though the test itself
        # doesn't run -- callers displaying this as a live confidence
        # figure need a number for a thin-history customer too, just not
        # a reject/extend decision drawn from it.
        return False, {"n": n, "mean": sum(sample) / n if n else None, "reason": "insufficient_sample"}
    mean = sum(sample) / n
    variance = sum((x - mean) ** 2 for x in sample) / (n - 1) if n > 1 else 0.0
    std_dev = variance ** 0.5
    df = n - 1
    if std_dev == 0.0:
        # No variability in the sample at all (e.g. every recent turn
        # graded identically) -- the t statistic is undefined by division.
        # The only unambiguous call a variance-based test can make here is
        # whether that constant value is itself already below the null.
        return mean < population_mean_h0, {"n": n, "mean": mean, "std_dev": 0.0, "t": None, "df": df}
    standard_error = std_dev / (n ** 0.5)
    t = (mean - population_mean_h0) / standard_error
    critical_t = CONFIDENCE_TEST_T_CRITICAL.get(alpha, {}).get(df) or CONFIDENCE_TEST_Z_CRITICAL.get(
        alpha, CONFIDENCE_TEST_Z_CRITICAL[0.05]
    )
    reject = t < -critical_t
    return reject, {
        "n": n, "df": df, "mean": mean, "std_dev": std_dev, "t": t, "critical_t": critical_t, "alpha": alpha,
    }


def node_03c_sufficiency_auditor(state: AgentState):
    print("🟢 [NODE 03c] Executing Task-Level Sufficiency & Evidence Selection")
    # OLD (removed): a session's rolling historical confidence alone used
    # to cap this task's ladder at DIRECT+INTENT_EXPANSION ("normal
    # effort") before PRODUCT_EXPANSION/ANGLE_EXPANSION ever ran, betting
    # purely on a well-established track record from OTHER, unrelated past
    # turns rather than on how *this* task's own retrieval is actually
    # going -- a single real failure this turn couldn't override a
    # confident session's history at all, and a raw average-vs-threshold
    # check couldn't distinguish genuine decline from a couple of noisy
    # turns either way. Replaced with a formal one-tailed hypothesis test
    # (_reject_confidence_null_hypothesis, further below): H0: the
    # population mean of recent turn grades is >= 0.7 (things are fine);
    # H1: it has genuinely dropped below 0.7. The sample is up to
    # HYPOTHESIS_TEST_SAMPLE_SIZE (30) recent turn grades, each itself the
    # average of that turn's own subquery grades (see provisional_turn_grade
    # below) -- this turn's own in-progress grade occupies the sample's
    # newest slot, which is how current retrieval quality enters what is
    # otherwise a historical test. Rejecting H0 extends effort; static
    # rolling confidence is otherwise untouched and still exclusively
    # feeds node_08's soft-escalation chip. See RAG_RETRY_EFFORT_v1.0.md.
    # Extra Effort v2.1: which attempt transitions are unconditional vs
    # gated by extend_if_optional (this same Monte-Carlo-calibrated
    # hypothesis test) is now table-driven -- see EFFORT_TIER_GATE and
    # _attempt_unlocked above. Only the attempt-2->3 transition
    # (Territory B) is actually gated by it today.
    pending_decisions = []
    new_direct_adopt_tasks = []
    tasks_by_id = {task.get("task_id"): task for task in state.get("search_tasks", [])}
    subqueries_by_id = {
        subquery.get("subquery_id"): subquery
        for subquery in state.get("financial_subqueries", state.get("triaged_subqueries", []))
        if isinstance(subquery, dict) and subquery.get("subquery_id")
    }
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
    partial_guidance = {
        str(task_id): text
        for task_id, text in state.get("task_partial_guidance", {}).items()
        if isinstance(text, str) and text.strip()
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
            print(f"   ⚡ [SUFFICIENCY SKIPPED]: Task '{task_id}' has no candidate evidence this attempt; treating as IRRELEVANT without a model call.")
            log_event(
                "task.sufficiency.skipped",
                task_id=task_id,
                excluded_parent_ids=rejected_parent_ids.get(str(task_id), []),
            )
            auditor_results[task_id] = {
                "task_id": task_id,
                "status": "IRRELEVANT",
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
        a useful FAQ response. The rules come first; the Original User
        Message, Trusted Conversation Context and Task Inputs they refer to
        follow at the end.

        Rules:
        - Each Task Input contains `task.customer_component`: the intent
          formulator's resolved sub-query for that task. Evaluate a task ONLY
          against its own `customer_component` and that task's candidate
          parents. `customer_component` may already be elaborated with
          retrieval-oriented wording the customer never actually said, so
          if you set `unresolved_term` below, derive it by reasoning
          directly against Original User Message and Trusted Conversation
          Context instead -- never by quoting `customer_component` back --
          a clarifying question must quote something the customer will
          recognize as their own.
        - Each Task Input may include `prior_sufficiency_audit_history` from
          earlier attempts for the same task in this turn. Read it before
          judging the current candidates and remain consistent with its prior
          sufficiency judgments. This history is internal audit context only;
          it is not customer-facing evidence and does not authorize any new
          factual claim.
        - Classify each task as SUFFICIENT, RELEVANT, or
          IRRELEVANT. Judge whether the supplied parents provide useful,
          materially relevant information for the task—not whether they repeat
          the customer's wording or explicitly resolve every possible
          interpretation of the customer's question.
        - TERRITORY NOTE (Extra Effort v2.0): `task.search_territory` marks
          HOW this task's candidates were found, never how good they are.
          When it is `"adjacent_scope"`, this task is a deliberate scope-
          expansion companion whose own product/intent is DIFFERENT from
          what the customer literally asked about -- but judge it by the
          exact same SUFFICIENT/RELEVANT/IRRELEVANT test as any other task.
          Do not downgrade a genuinely complete answer merely because of
          which search found it: if the original task's own product/intent
          mapping turns out to have been wrong and this companion's
          different product is actually the right one, its evidence can be
          SUFFICIENT. Never set `unresolved_term` for an `"adjacent_scope"`
          task, though -- clarification-authoring for this subquery belongs
          exclusively to its `"in_scope"` sibling task, which reviews the
          same underlying request.
        - RELEVANCE DECISION ORDER: First identify whether any supplied
          parent contains a factual proposition directly relevant to a
          meaningful aspect of the customer's resolved request. If one does,
          do not return IRRELEVANT merely because the parent cannot determine
          the customer's requested outcome, permission, eligibility,
          exception, legality, preferred action, or other broader conclusion.
          Retain that parent as RELEVANT when its supported fact
          can be truthfully presented as useful, bounded Horizon guidance
          while another material aspect remains unresolved. Continue retrieval
          for the unresolved aspect.
        - Return IRRELEVANT only when no supplied parent contains any
          factual proposition that can be truthfully and materially presented
          in relation to the resolved request. A parent is not material merely
          because it concerns the same product: its supported fact must
          directly concern the same topic, condition, process, boundary, or
          factual premise in the customer's request.
        - RELEVANT REQUIRES A REAL PARENT: RELEVANT means you found at
          least one approved parent that connects to the request without
          fully resolving it -- never a status you reach through reasoning
          alone with nothing to show for it. A task with ZERO approved
          parents is always IRRELEVANT, full stop, no exception -- there is
          no "genuinely worth mentioning as a hedge" case that overrides
          this, regardless of how plausible or well-intentioned the
          observation feels. `generation_guidance` must be left empty
          whenever `approved_parent_ids` is empty; write it only when you
          have at least one real, approved parent to hang it on (typically
          a caveat on a parent whose own stated circumstance doesn't
          squarely match the customer's, per the GENERATION GUIDANCE rule
          below). Being able to compose a plausible-sounding hedge from
          your own general knowledge is never proof that a genuine,
          evidence-backed observation exists -- confirmed live: an
          adjacent-scope task with zero approved parents wrote a confident,
          specific-sounding `generation_guidance` ("fee waivers are
          evaluated based on account tenure and loyalty status") that
          traced to nothing it had actually retrieved. A genuinely
          empty-evidence task is fully and safely handled downstream by a
          fixed, deterministic "I don't have information available"
          statement -- never LLM-generated, so it carries zero risk of
          inventing anything. There is no benefit to `generation_guidance`
          worth that risk when no real parent backs it.
        - The reverse matters just as much: a parent whose own subject is a
          distinctly-named, different product or service is not material
          merely because it is credit/loan-adjacent, belongs to the same
          broader family, or is reached through the same in-app menu path as
          the customer's named product. For example, a balance transfer
          draws on loan credit and is filed under the app's "Loan" section
          alongside a personal loan application -- but a parent describing
          how to request a balance transfer does not materially answer a
          request about applying for a (personal/general) loan. Do not let
          shared category vocabulary or menu wording ("Loan section",
          "commence your application") substitute for the parent actually
          being about the specific product or process the customer named.
          Only treat a differently-named product's parent as material when
          it explicitly states it also applies to, or is the general form
          of, the customer's named product.
        - GENERATION GUIDANCE: `generation_guidance` is held to the exact
          same standard the final customer-facing response itself must pass
          before it ever reaches the customer. The Bank Conduct Rules and
          Regulatory/Licensing Constraints below govern not only what you
          approve as evidence, but the instruction you write about how it
          gets presented -- reason from these rules directly when deciding
          what an instruction may and may not say, rather than relying on
          a memorized list of forbidden phrases; any instruction that would
          cause the eventual response to violate one of them is itself a
          violation here, regardless of the specific wording used.

          {BANK_CONDUCT_CONTEXT}

          {REGULATORY_LICENSING_CONTEXT}

          {_GENERATION_GUIDANCE_SHAPE_RULE}

          A recurring instance of the "nothing confirmed" case above: you
          approve a parent whose own stated premise or circumstance does
          not match the customer's own stated situation (e.g. a card-
          replacement parent whose entire procedure assumes physical
          damage, approved for a customer who instead dislikes the card's
          appearance, with nothing in any parent stating whether the
          procedure is available for other reasons too). There, set
          `generation_guidance` to plainly admit no information resolves
          whether the procedure applies to the customer's actual stated
          reason, phrased around that reason, and stop there -- e.g. "There
          is no information here on whether card replacement is available
          for appearance-related reasons; admit that plainly, then you may
          still mention the standard replacement steps as something that
          may help, without claiming they resolve this." But first check
          whether any parent's own text actually is reason-agnostic or
          otherwise already covers the customer's stated reason -- in that
          case this is the "confirmed" case instead, and the correct
          instruction is to assert that coverage directly, not admit a gap
          that isn't really there.
          `generation_guidance` is never a new fact and never customer-
          facing wording itself, only a directive to the writer. Leave it
          empty for the common case where the approved evidence genuinely
          fits the customer's own stated situation with no caveat needed --
          do not manufacture a caveat where none is warranted.
        - MECHANISM-BRIDGE REASONING (progressive, work through in this
          order before falling back to IRRELEVANT): this is a genuinely
          different case from the category-adjacency rule just above --
          that rule blocks treating a DIFFERENT product as material merely
          for sharing a category, menu path, or vocabulary; this rule
          instead asks whether a parent already material to the CUSTOMER'S
          OWN NAMED PRODUCT describes an outcome, resource, or capability
          whose own ordinary, unrestricted nature would satisfy the
          customer's underlying need, even though no parent ever names that
          specific need. Never trigger this from shared category,
          vocabulary, or menu placement alone -- only from the OUTPUT's own
          stated, inherent nature.
          Product-identity note: a parent's own internal or marketing name
          for an offering (e.g. a specific product branded "Instant Loan")
          is never grounds on its own to doubt it concerns the customer's
          named product just because the two names read differently -- but
          verify this directly from that parent's own `product_tags` field
          (the knowledge base's real, stored classification for that
          specific piece of content, not a routing decision made about this
          task before any evidence existed): when `product_tags` itself
          names the customer's own product, that settles this parent's
          identity on real grounds, regardless of what its own marketing
          name happens to be. This is independent verification from actual
          data, not deference to how this task was routed -- if a parent's
          own `product_tags` does NOT confirm the customer's named product
          either, its marketing name reading differently is a real signal
          worth weighing, not something to explain away. The category-
          adjacency rule's stricter bar
          (an explicit statement that it also applies to, or is the general
          form of, the named product) is for a parent tagged under a
          genuinely DIFFERENT product than this task's own -- it does not
          apply merely because a parent's own brand name for the SAME
          product differs from the generic Matrix noun describing it.
          This concession is narrow and covers ONLY whether a parent's own
          product identity counts as the customer's named product -- it is
          never license to treat a customer-named brand, network, or other
          specific variant qualifier (e.g. Visa vs Mastercard) as already
          settled or resolved merely because the surrounding PRODUCT
          classification is settled. Two different products both existing
          under the same settled Credit_Card classification does not mean
          either one satisfies a request naming a specific one -- that
          qualifier still requires its own check against the parent's
          actual text, exactly as the VERIFICATION ITEMS CONFIRMATION rule
          below requires, regardless of how settled the product itself is.
          Step 1: Re-read the request one level more abstractly than its
          literal wording -- what underlying outcome is the customer
          actually after (e.g. a way to pay for something, a way to access
          funds, a way to complete an action), not just the specific
          scenario they used to describe it.
          Step 2: Check whether any parent already established as
          concerning the customer's own named product describes a
          resource, output, or capability that -- by its own ORDINARY,
          COMMON-KNOWLEDGE nature -- would ordinarily satisfy that more
          abstract outcome. A parent does NOT need to spell out that nature
          explicitly for you to rely on it: plain cash, once disbursed with
          no stated restriction, is fungible and usable for any purpose by
          the ordinary meaning of "cash" itself -- the same way you would
          never require an FAQ to state "you may spend your salary on
          anything" before treating salary as unrestricted. Requiring the
          word "unrestricted" (or equivalent) to appear before relying on
          this is over-literal and defeats the rule -- reason from the
          resource's ordinary real-world nature, exactly as you already
          reason from common banking vocabulary elsewhere in this task.
          The bridge itself must still rest on a parent's actual stated
          fact (that the product disburses cash, or grants some other
          describable capability) -- never on an assumption about the
          PRODUCT the parent never actually states.
          Step 3: Before relying on it, check every supplied parent for any
          stated condition, restriction, or limitation on that same
          resource, output, or capability (e.g. a required use, an
          eligibility condition, a repayment term tied to purpose) that
          would qualify or block the bridge. Absence of a restriction is
          not itself suspicious -- it is the default state -- so do not
          treat "no parent mentions a restriction" as a reason to doubt the
          bridge; only an actually-stated restriction qualifies or blocks
          it. When one exists, the bridge does not hold as clean
          RELEVANT guidance -- treat the restriction as part of
          what must be disclosed, not as grounds to discard the bridge
          entirely, unless it outright forecloses the
          customer's outcome.
          Step 4: If the bridge holds, approve the parent(s) it rests on as
          RELEVANT (never SUFFICIENT -- this is one bounded
          logical step removed from a literal answer, not a direct one) and
          state the bridge explicitly in `reason`, including any
          qualifying condition from Step 3 that the response must carry
          forward. If no such resource/capability is described anywhere in
          the supplied parents, this rule simply does not apply -- proceed
          to IRRELEVANT under the rules above as normal.
        - SUFFICIENT (governing principle): the customer's own question,
          exactly as they framed it, is fully and directly resolved -- every
          specific detail, qualifier, product, channel, or scope they
          actually named is either confirmed by a selected parent or
          NECESSARILY settled by it, with no further assumption or guess
          required (the way a stated fixed limit necessarily settles a
          question about exceeding it). No material part of the
          `customer_component` may be left ambiguous, unaddressed, or
          simply assumed to be covered because a parent is topically
          adjacent or shares vocabulary with it. THE TEST: could a careful
          reader of the selected parents alone state, with no guessing,
          exactly how every specific thing the customer named is addressed?
          If yes, SUFFICIENT -- and the response itself need not be a
          literal yes/no; an explicit policy boundary, limit, condition,
          restriction, or other related factual information is a fully
          adequate SUFFICIENT answer, as long as it leaves nothing the
          customer named still open. If any named detail's answer is
          genuinely undetermined by the evidence -- including a parent
          that is simply silent on a scope, channel, product variant, or
          other qualifier the customer explicitly used (e.g. they said
          "international," a specific card network, or another
          distinguishing detail, and no selected parent confirms or rules
          out whether that detail is covered) -- the task is capped at
          RELEVANT, never SUFFICIENT, no matter how confidently the parents
          answer the more generic version of the question. A parent's own
          topical adjacency, shared category, or use of the same general
          noun as the customer's more specific request (e.g. a generic
          "transfer limit" parent against a request specifically about the
          "international transfer limit") is never itself confirmation
          that it covers that specific case -- treat the named qualifier as
          material until some parent actually addresses it, one way or the
          other. The response generator will state only what the evidence
          supports and must not invent a broader conclusion.
        - Before settling on SUFFICIENT, also check every OTHER supplied
          candidate parent, not only the ones you are about to approve, for
          anything that contradicts or narrows the premise you are relying
          on -- e.g. a candidate stating the bank does not offer the
          specific service, channel, or scope the customer named at all. A
          candidate does not need to be the one you ultimately cite to be
          disqualifying: if any supplied parent directly speaks to whether
          the customer's specific named detail is or is not covered, reason
          from it rather than silently preferring a more generic,
          scope-silent parent instead.
        - VERIFICATION ITEMS CONFIRMATION (REQUIRED when this task's own
          `verification_items` is non-empty): the intent formulator flagged
          each entry in `verification_items` as a specific, checkable claim
          the customer's own wording implied -- a named brand/network, a
          premise or condition an answer would need to actually match, a
          scope boundary, or any other specific detail -- which it could
          not check itself since no evidence existed yet; that check is
          entirely your job now. For EVERY entry in `verification_items`,
          you MUST output a corresponding entry in `verification_results`:
          {{"item": "<the exact string>", "confirmed": true/false,
          "supporting_parent_id": "<parent_id or empty>"}}. Set `confirmed`
          true ONLY when some parent's own text actually states that exact
          claim, or explicitly states equivalent/universal coverage --
          never merely because a parent covers the general topic, is
          silent on this specific point, or states something different
          related to it. This is a narrow, factual check on THIS ONE
          ITEM, deliberately separate from your own overall `status`
          judgment for the task -- do not let a holistic sense that "the
          evidence generally answers this" substitute for actually finding
          the claim stated. An item left unconfirmed caps this task at
          RELEVANT, never SUFFICIENT, regardless of how complete the rest
          of the evidence is; this is enforced deterministically
          downstream, so an inaccurate `confirmed: true` will not make the
          response correct -- it will only misinform whoever later has to
          debug why a wrong answer reached the customer.
        {PRECISION_REFERENCE_CONTRACT}
        - RELEVANT: at least one selected parent is connected to the
          `customer_component` but does not fully resolve it under the
          SUFFICIENT test above -- a genuine partial answer (one
          meaningful part resolved, another material part, or a qualifier
          the customer specifically named, left genuinely undetermined by
          the evidence). Select the supporting parent ID(s); retrieval
          continues for what remains open. Zero selected parents is never
          RELEVANT -- see RELEVANT REQUIRES A REAL PARENT above.
        - IRRELEVANT: the supplied parents provide no grounded, materially
          relevant information that could be usefully presented in response
          to the `customer_component`, and there is nothing worth writing
          `generation_guidance` about either.
        - CLARIFICATION AUTHORING: you are the node closest to the actual
          evidence, so YOU independently decide whether a clarifying
          question is warranted and what to ask -- the backend only relays
          what you set here verbatim, it makes no separate judgment call of
          its own. Judge fresh from what the evidence actually shows --
          never assume the product/intent this task was routed under is
          correct merely because it already got you this far. It was
          chosen by the intent formulator before any evidence existed, from
          the customer's own words alone; that is a reasonable first guess,
          not a settled fact for you to defer to. You are never authoring a
          NEW product/intent classification of your own here (leave
          target_commercial_product/target_intent alone -- that vocabulary
          is not yours to assign, and this is not you second-guessing which
          Matrix category the request maps to in the abstract) -- but you
          ARE the right node to notice when the customer's actual request
          still isn't genuinely resolved by what was found, for whatever
          reason, and to ask about that in your own words, grounded in what
          you actually reviewed.
          Each supplied parent now also carries its own real, stored
          `product_tags`/`intent_families` (from the knowledge base itself,
          not a routing decision) -- use these directly: when a parent's own
          tags genuinely confirm it concerns the customer's named product or
          situation regardless of how its content happens to be worded
          (e.g. a parent's own tag says Personal_Loan even though its
          marketing name reads "Instant Loan"), that is real confirmation,
          not a re-litigation of routing -- no clarification is needed on
          that basis. But when NO supplied parent's own content or tags
          actually confirm which specific interpretation of the customer's
          request applies -- and the request was genuinely capable of more
          than one -- that is a real, independently-observed gap, not
          evidence to paper over.
          Set `unresolved_term` (else leave it as an empty string) only when
          BOTH of the following hold, regardless of whether this task's
          status is RELEVANT or IRRELEVANT:
          (1) a specific, real detail is genuinely missing from the
              customer's own words or the conversation history, AND no
              supplied parent's own content or tags resolve it either --
              never a gap in the evidence for an otherwise well-defined
              request, an undocumented topic, or a case where some parent's
              own tags or content already do settle it.
          (2) supplying that detail would plausibly change the outcome --
              either unlocking a materially different, fuller answer for a
              RELEVANT task, or turning an otherwise-unanswerable IRRELEVANT
              task into an answerable one.
          In other words: ask only when this genuinely blocks you from
          classifying against a well-defined target or from writing
          guidance you would actually stand behind -- never merely because a
          task didn't fully resolve, the evidence was messy or incomplete,
          or the topic is simply undocumented. A RELEVANT task with one
          clean, if incomplete, fact needs no clarification at all -- write
          its `generation_guidance` and move on; a real coverage gap is not
          an ambiguity, and the backend's own neutral availability notice
          already covers it.
          `unresolved_term` must be a concise phrase naming exactly the
          ambiguous or unresolved part, derived by reasoning directly
          against Original User Message and Trusted Conversation Context
          yourself -- never by quoting `task.customer_component` back (see
          above), which may already be elaborated with retrieval-oriented
          wording the customer never actually said. If the raw input/
          history reveals exactly which earlier turn the ambiguity traces
          back to, name that specifically -- you have the same conversation
          visibility node_02e does, so use it the same way. Never the whole
          `customer_component` restated unless the whole thing is genuinely
          what's unresolved. Phrase it as a noun phrase that reads naturally
          completing "Could you tell us more about ___?" (e.g. "which loan
          type you mean," "whether you're applying online or in person") --
          this is a request for a missing detail, not a claim that the
          customer used this exact wording, so never phrase it as if quoting
          the customer (e.g. not "the term X you used").
        - When `unresolved_term` is non-empty, also set
          `clarification_reason`: distinct from `reason` above (which
          explains the evidence verdict generally, for every status, not
          just this one) -- this field explains specifically why THIS part
          needs clarification, naming the specific detail in Original User
          Message or Trusted Conversation Context that makes it ambiguous
          or incomplete (e.g. "the customer asked about documents but never
          specified online vs in-person submission, and neither did prior
          turns"). Never a restatement of what the candidate parents
          contained -- that belongs in `reason`, not here. If you cannot
          point to a specific real detail in the user's own input or
          history, `unresolved_term` is not genuinely justified either --
          leave both empty instead.
        - When `unresolved_term` is non-empty, also set
          `clarification_guesses`: 0-3 entries, each a reasonable, grounded
          PREDICTION of what the customer likely means -- never a report of
          anything the customer has actually said -- offered as a tappable
          option that would unblock this task if correct. Order most likely
          first, using Trusted Conversation Context and the specific
          candidate parents you just reviewed as your grounding (e.g. two
          retrieved parents cover different sub-topics the customer might
          have meant; the customer named a related product two turns ago)
          -- never the Product/Intent Matrix directly, that is not this
          call's job. Leave empty when you have no real, statable basis to
          guess, even though the question itself is still worth asking.
          Each entry needs a short "label" (chip text), a "message" that is
          a complete, natural sentence a customer would actually send, and
          a "reason": a concise, internal-only justification naming the
          specific evidence or conversation detail behind that guess. A
          guess you cannot state a concrete reason for is not genuinely
          inferable -- leave it out.
        - Use the ordinary meaning of explicit policy boundaries, quantities,
          conditions, and restrictions when applying the SUFFICIENT test
          above. A parent does not need to restate the customer's question
          or spell out their requested conclusion in words when that
          conclusion is NECESSARILY settled by a stated fact with no
          further assumption needed -- e.g. an explicit statutory maximum
          or fixed policy limit necessarily settles a question about
          raising that limit, even though the parent never separately says
          "no" or enumerates every possible external arrangement. This
          allowance is narrow and covers only a conclusion that follows
          with nothing left to determine -- it never extends to a named
          detail, scope, channel, or qualifier a parent simply never
          addresses at all; that case is not "necessarily settled," it is
          silently unaddressed, and stays governed by the SUFFICIENT test
          above (capped at RELEVANT).
          Preserve each parent's scope and do not infer facts beyond it.
        - Do NOT require a task's evidence to answer the complete original
          user message, another task's customer_component, or another
          product/intent component.
        - The original user message and trusted conversation context help
          explain references or continuations only; they are never the
          completeness benchmark for an individual task.
        - Return one result for every supplied task_id.
        - For SUFFICIENT or RELEVANT, return only parent IDs from that exact
          task that materially support the answer -- at least one ID is
          required for both. An empty `approved_parent_ids` is only ever
          valid for IRRELEVANT.
        - For RELEVANT or IRRELEVANT, return an `evidence_delta`
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
        - SCOPE-EXPANSION LEAD (Extra Effort v2.1, for a task whose
          `search_territory` is absent or `"in_scope"` only -- never set
          this for an already-`"adjacent_scope"` task): reasoned through
          BEFORE naming the candidate. When this task's own status is
          RELEVANT or IRRELEVANT, look across its supplied
          candidate parents for ONE that -- while not materially answering
          this task's own `customer_component` -- has a genuine, real,
          content-based connection worth pursuing as a related topic if
          this task's literal request turns out undocumented (e.g. it
          covers a closely adjacent product or process). This candidate is
          adopted VERBATIM and shown to the customer immediately, exactly as
          you name it here -- no further review happens downstream, and it
          fires even on a turn where nothing else about this task ever
          retries. Only set this when you would stand behind it as
          genuinely useful related information right now, not merely as a
          hopeful lead for a future retry attempt. Set
          `scope_expansion_lead_reason` to the specific, concrete basis for
          that connection and `scope_expansion_lead_parent_id` to its exact
          parent ID. The connection must be real content, never merely
          shared vocabulary, category, or menu placement -- the same
          discipline `clarification_guesses` already requires. If no
          candidate has a real, statable connection, leave both fields
          empty -- a forced lead is worse than none, since it would be
          shown to the customer immediately instead of a genuinely reasoned
          connection.

        Output JSON only: {{"task_results": [{{"task_id": "...", "reason": "...", "status": "SUFFICIENT" | "RELEVANT" | "IRRELEVANT", "approved_parent_ids": ["..."], "generation_guidance": "", "evidence_delta": [{{"parent_id": "...", "fact": "..."}}], "verification_results": [{{"item": "...", "confirmed": true, "supporting_parent_id": "..."}}], "scope_expansion_lead_reason": "", "scope_expansion_lead_parent_id": "", "unresolved_term": "", "clarification_reason": "", "clarification_guesses": [{{"label": "...", "message": "...", "reason": "..."}}]}}]}}

        {_DATA_FOLLOWS}

        Original User Message (advisory context only; never use it as an
        individual task's completeness target): {state.get("user_input", "")}
        Trusted Conversation Context: { _build_conversation_context(state) }
        Task Inputs: {json.dumps(evaluation_inputs, ensure_ascii=False)}
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
        requested_status = str(audit.get("status", "IRRELEVANT")).upper()
        # Accept legacy values during an in-flight backend restart, but never
        # let them shape the three-way state model.
        if requested_status in {"RETRY", "UNAVAILABLE"}:
            requested_status = "IRRELEVANT"
        if requested_status not in {"SUFFICIENT", "RELEVANT", "IRRELEVANT"}:
            requested_status = "IRRELEVANT"
        # VERIFICATION ITEMS CONFIRMATION: enforced here in code, not left
        # to the model's own `status` field alone -- same discipline as the
        # RELEVANT REQUIRES A REAL PARENT enforcement below. A specific,
        # checkable claim (e.g. Visa vs Mastercard, or a premise/scope
        # condition) the intent formulator flagged as unconfirmable at
        # formulation time must be confirmed by SOME parent's own text
        # before this task can be SUFFICIENT; trusting the model to always
        # self-police this would silently reintroduce the exact "generally
        # covers the product family" rationalization this field exists to
        # stop (confirmed live: the model can notice a named brand
        # explicitly and still grant SUFFICIENT anyway).
        verification_items = [
            str(q).strip() for q in tasks_by_id.get(task_id, {}).get("verification_items", [])
            if str(q).strip()
        ]
        if verification_items and requested_status == "SUFFICIENT":
            confirmations = {
                str(item.get("item", "")).strip(): bool(item.get("confirmed"))
                for item in audit.get("verification_results", [])
                if isinstance(item, dict)
            }
            if not all(confirmations.get(q, False) for q in verification_items):
                requested_status = "RELEVANT"
        # search_territory ("adjacent_scope" vs "in_scope") is a provenance
        # marker, not a status ceiling -- an adjacent_scope task is judged by
        # the exact same SUFFICIENT/RELEVANT/IRRELEVANT test as any other
        # task. If the original in_scope task's own product/intent mapping
        # was actually wrong and this companion's different product is the
        # right one, its evidence can genuinely be SUFFICIENT; forcing a
        # downgrade here would deny the customer a correct, complete answer
        # purely because of which search happened to find it. search_territory
        # still governs presentation downstream (an adjacent_scope entry must
        # never be blurred into the in_scope task's own direct answer without
        # disclosing the different product/intent assumption), just not the
        # status itself.
        reason = audit.get("reason", "No sufficient grounded FAQ evidence was selected.")
        # RELEVANT REQUIRES A REAL PARENT: enforced here in code, not left to
        # the prompt instruction alone. Zero approved parents is always
        # IRRELEVANT, regardless of what status the model itself chose or
        # what it wrote in generation_guidance -- the old "no info is info"
        # exception (a guidance-only observation could earn RELEVANT with
        # empty approved_parent_ids) is deliberately removed: confirmed live
        # that a task with zero approved parents can still write a
        # confident, specific-sounding generation_guidance ("fee waivers are
        # evaluated based on account tenure and loyalty status") tracing to
        # nothing it actually retrieved. generation_guidance is discarded
        # entirely whenever there is no approved parent to hang it on --
        # the customer gets the safe, fixed, never-LLM-generated "no
        # information available" statement instead, which carries zero risk
        # of inventing anything.
        if not approved_ids:
            requested_status = "IRRELEVANT"
        generation_guidance = str(audit.get("generation_guidance", "")).strip() if approved_ids else ""
        effective_guidance = generation_guidance
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
            "generation_guidance": effective_guidance,
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
            # BUG-007: this round's own raw `generation_guidance` only --
            # never a value carried forward from an earlier, less-successful
            # round. A task that just became SUFFICIENT has real, approved
            # evidence this round; if its own audit call felt no fresh
            # caveat was needed, attaching a stale, possibly-contradictory
            # guidance string left over from an earlier round (e.g. "admit
            # there is no information...") would actively mislead whoever
            # reads this field, even though the generator correctly ignores
            # it in favor of the real evidence -- confirmed live.
            current_results[task_id] = {
                "task_id": task_id,
                "status": "SUFFICIENT",
                "origin": result.get("origin", "RAG"),
                "approved_parent_ids": approved_ids,
                "reason": reason,
                "generation_guidance": generation_guidance,
            }
            log_event(
                "evidence.boundary.approved",
                task_id=task_id,
                approved_parent_ids=approved_ids,
            )
            feedback.pop(task_id, None)
            partial_evidence.pop(str(task_id), None)
            partial_guidance.pop(str(task_id), None)
            rejected_parent_ids.pop(str(task_id), None)
            continue

        retained_ids = partial_evidence.get(str(task_id), [])
        partial_ids = list(dict.fromkeys([*retained_ids, *approved_ids]))
        # RELEVANT now always implies approved_ids is non-empty (enforced
        # above), so partial_ids is always non-empty here too -- no separate
        # "partial answer with nothing to show for it" downgrade is needed
        # anymore.
        if requested_status == "RELEVANT":
            partial_evidence[str(task_id)] = partial_ids
            if effective_guidance:
                # Retains a real, evidence-backed caveat across retry rounds
                # when a later round's raw output doesn't repeat it -- this
                # task has a real approved parent (RELEVANT requires one),
                # so this is a genuine caveat on real evidence, never a
                # guidance-only substitute for one.
                partial_guidance[str(task_id)] = effective_guidance

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
        # Extra Effort v2.1: a lead is only trustworthy when it names a
        # parent this exact attempt actually supplied -- same discipline as
        # evidence_delta above -- AND the audited task's own search_territory
        # is not already "adjacent_scope". Without that second check, an
        # adjacent_scope companion's own audit round flagging a lead would
        # synchronously spawn a "..._scope_scope" companion below,
        # reintroducing the exact unbounded-recursion class of bug
        # is_scope_expansion_task (further below) exists to stop -- that
        # guard alone isn't enough here because direct-adopt acts on the
        # lead immediately, in this same loop, before is_scope_expansion_task
        # ever gets a chance to apply.
        scope_expansion_lead_parent_id = str(audit.get("scope_expansion_lead_parent_id", "")).strip()
        scope_expansion_lead_reason = str(audit.get("scope_expansion_lead_reason", "")).strip()
        if (
            scope_expansion_lead_parent_id not in available_ids
            or not scope_expansion_lead_reason
            or tasks_by_id.get(task_id, {}).get("search_territory") == "adjacent_scope"
        ):
            scope_expansion_lead_parent_id = ""
            scope_expansion_lead_reason = ""

        # Direct-adopt (Extra Effort v2.1): a genuine lead is adopted
        # immediately as RELEVANT/adjacent_scope evidence --
        # free (no LLM call, no retrieval), and deliberately NOT part of
        # the effort-tier system above (see EFFORT_TIER_GATE): it must fire
        # even on a turn where extend_if_optional is False and this task is
        # about to terminate in this very same round, which is exactly why
        # this lives here rather than in node_02e's retry pass (that pass
        # only ever runs for a task whose status is "RETRY" this round).
        # First-lead-wins: a task can be audited across two attempts before
        # terminating, and a later round's audit could flag a different
        # candidate -- without this guard a later lead would silently
        # overwrite an already-terminal, already-scored companion, or (if
        # appended instead) produce two search_tasks entries sharing one
        # task_id.
        companion_task_id = f"{task_id}_scope"
        if scope_expansion_lead_parent_id and companion_task_id not in tasks_by_id:
            companion_stub = {
                "task_id": companion_task_id,
                "search_territory": "adjacent_scope",
                "source_task_id": task_id,
                "mapping_reason": scope_expansion_lead_reason,
                "subquery_id": tasks_by_id.get(task_id, {}).get("subquery_id"),
                "customer_component": tasks_by_id.get(task_id, {}).get("customer_component", ""),
                # target_commercial_product/target_intent deliberately left
                # unset -- there is no cheap way to look up the flagged
                # PARENT's own product/intent tags (hydrated parents carry
                # only parent_id+content), and copying the SOURCE task's own
                # product/intent would let a future turn's
                # _verified_session_parent_ids_for_task (which matches by
                # product+intent overlap) resurface this never-fully-vetted,
                # adjacent-scope parent as if it were verified in-scope
                # evidence -- exactly the cross-scope leakage the territory
                # ceiling exists to prevent. Left empty,
                # _normalized_product_tags(None) -> [] and that lookup's own
                # guard naturally excludes it.
            }
            # Local dict, kept in sync immediately so any LATER lookup this
            # same loop/call (e.g. rag_task_scores_by_subquery_id further
            # below) sees it -- state's own search_tasks is only updated
            # once, in this function's final return.
            tasks_by_id[companion_task_id] = companion_stub
            new_direct_adopt_tasks.append(companion_stub)
            current_results[companion_task_id] = {
                "task_id": companion_task_id,
                "status": "RELEVANT",
                "approved_parent_ids": [scope_expansion_lead_parent_id],
                "reason": scope_expansion_lead_reason,
                "origin": "DIRECT_ADOPT",
            }
            print(f"   🎯 [DIRECT ADOPT]: Task '{task_id}' → companion '{companion_task_id}' adopts parent '{scope_expansion_lead_parent_id}' verbatim.")
            log_event(
                "task.scope_expansion.direct_adopted",
                source_task_id=task_id,
                companion_task_id=companion_task_id,
                parent_id=scope_expansion_lead_parent_id,
                reason=scope_expansion_lead_reason,
            )

        # Not decided yet whether this task stops here or continues -- see
        # the dilution check right after this loop. A task below
        # EXTRA_EFFORT_ATTEMPT or at/past MAX_RAG_ATTEMPTS has no real
        # choice either way, but one in between depends on how the *whole
        # turn* is reading once every task's current-round result is known,
        # not on this task in isolation.
        pending_decisions.append({
            "task_id": task_id,
            "attempt_count": attempt_count,
            "partial_ids": partial_ids,
            "reason": reason,
            "refinement": refinement,
            "requested_status": requested_status,
            "generation_guidance": effective_guidance,
            "unresolved_term": str(audit.get("unresolved_term", "")).strip(),
            # Distinct from `reason` above: reason explains the evidence
            # verdict generally (used for every status); clarification_reason
            # is specific to why THIS part needs clarification, required to
            # name a real detail from the user's own input/history rather
            # than restate evidence coverage -- see TaskSufficiencyItem in
            # llm_service.py.
            "clarification_reason": str(audit.get("clarification_reason", "")).strip(),
            "clarification_guesses": _normalize_clarification_guesses(audit.get("clarification_guesses", [])),
        })

    # This turn's own predicted grade: every financial subquery in the
    # turn gets a 1.0/0.5/0.0 score, the same rule _score_subquery applies
    # everywhere else in this codebase -- not just the RAG tasks node_03c
    # happens to be auditing this round. A subquery resolved by a
    # different tool entirely (deeplink/service_request/api) previously
    # had no representation anywhere in this calculation: a turn with one
    # subquery resolved instantly via deeplink (1.0) and another still
    # being chased by RAG would have silently dropped the deeplink
    # subquery's contribution, understating the turn's true grade.
    #
    # A subquery RAG has touched at all this turn (this round or an
    # earlier one) must use the rollup-aware score here, never
    # _score_subquery directly: _finalize_rag_attempts (which pushes a
    # settled RAG result into financial_subqueries' own tool_attempts)
    # only runs once every task in the turn is fully terminal, so a
    # sibling task still mid-ladder would make _score_subquery read this
    # one's rag attempt as still "pending" even after it has actually
    # resolved -- the same staleness _resolved_by_other_tool exists to
    # work around elsewhere (DECLINE_CLARIFICATION_DESIGN_v1.0.md §6).
    # Extra Effort v2.0: a subquery can now own TWO task_ids (Territory A +
    # a Territory B companion) contributing to the same subquery_id here.
    # Take the best of the two, never the last one processed -- a blind
    # overwrite would silently discard whichever territory's contribution
    # didn't happen to be iterated last, understating (or overstating) the
    # turn's true grade. This mirrors _finalize_rag_attempts' own "best of
    # both territories" combination for the same reason.
    rag_task_scores_by_subquery_id = {}
    for result in current_results.values():
        subquery_id = tasks_by_id.get(result.get("task_id"), {}).get("subquery_id")
        if subquery_id:
            score = _dynamic_task_confidence(result, partial_evidence)
            rag_task_scores_by_subquery_id[subquery_id] = max(score, rag_task_scores_by_subquery_id.get(subquery_id, 0.0))
    for decision in pending_decisions:
        subquery_id = tasks_by_id.get(decision["task_id"], {}).get("subquery_id")
        if subquery_id:
            score = 0.5 if decision["partial_ids"] else 0.0
            rag_task_scores_by_subquery_id[subquery_id] = max(score, rag_task_scores_by_subquery_id.get(subquery_id, 0.0))

    all_subqueries = state.get("financial_subqueries", state.get("triaged_subqueries", []))
    subquery_scores = []
    for subquery in all_subqueries:
        if not isinstance(subquery, dict):
            continue
        subquery_id = subquery.get("subquery_id")
        if subquery_id in rag_task_scores_by_subquery_id:
            subquery_scores.append(rag_task_scores_by_subquery_id[subquery_id])
        else:
            # Never touched by RAG this turn -- resolved entirely by
            # another tool, whose tool_attempts are always accurately
            # terminal already (no mid-ladder "pending" state to worry
            # about), so _score_subquery reads it correctly and directly.
            subquery_scores.append(_score_subquery(subquery))
    provisional_turn_grade = sum(subquery_scores) / len(subquery_scores) if subquery_scores else None

    # A task that has finished NORMAL_EFFORT_RAG_ATTEMPTS without a full
    # answer only actually continues into rounds 3-4 if a formal
    # hypothesis test finds significant evidence that recent quality has
    # genuinely dropped below CONFIDENCE_TEST_POPULATION_MEAN_H0, rather
    # than a raw sample-average-vs-threshold check that can't distinguish
    # real decline from a couple of noisy data points. H0: population mean
    # >= population_mean_h0 (things are fine). H1: population mean <
    # population_mean_h0 (evidence of real decline) -- see
    # _reject_confidence_null_hypothesis.
    #
    # The sample's unit is one SUBQUERY's own 1.0/0.5/0.0 grade, not a
    # turn's averaged grade: this turn's own subquery_scores (computed
    # above) supply every one of THIS turn's own newest points, not just
    # one averaged value, and _historical_subquery_scores flattens every
    # prior turn's subqueries the same way. A subquery's own score is
    # already never touched by the customer's thumbs rating (only
    # _turn_confidence's turn-level average does that, for node_08's
    # unrelated escalation judgment), so no separate rating-free variant
    # is needed here the way a turn-level design would require -- a
    # customer could thumbs-down a turn for reasons that have nothing to
    # do with whether RAG found the right evidence, and that domain stays
    # entirely out of this sample already, at the source.
    turn_escalation_history = state.get("turn_escalation_history", [])
    historical_subquery_scores = _historical_subquery_scores(turn_escalation_history)
    hypothesis_test_sample = (historical_subquery_scores + subquery_scores)[-HYPOTHESIS_TEST_SAMPLE_SIZE:]
    reject_null_hypothesis, test_diagnostics = _reject_confidence_null_hypothesis(hypothesis_test_sample)
    # A repeated need this turn (triage's own repetitive_issue_detected,
    # already computed before node_03c runs) always unlocks extra effort
    # regardless of what the statistical test concludes -- a deliberate
    # satisfaction-driven boost, not a statistical one: a customer
    # visibly re-asking or rephrasing the same thing is worth trying
    # harder for on its own terms, independent of whether recent quality
    # has genuinely declined by the test's standard.
    has_repeated_need_this_turn = bool(state.get("repeated_need_detected_this_turn", False))
    extend_if_optional = reject_null_hypothesis or has_repeated_need_this_turn
    # Extra Effort v2.1: whether Territory B's companion may be constructed
    # this round, routed through the same EFFORT_TIER_GATE table every
    # other tier gate uses (rather than reading extend_if_optional
    # directly) so a future change to Territory B's own gate (see
    # EFFORT_TIER_GATE's own comment) takes effect here automatically.
    # Territory A and Territory B are decided/constructed together, in the
    # SAME node_02e retry pass -- a task that terminates after Territory A
    # has no live "RETRY" status left for a later, separate round to hang
    # off of, so this can't be deferred to a genuinely sequential 3rd round.
    territory_b_unlocked = _attempt_unlocked(MAXIMUM_EFFORT_ATTEMPT, extend_if_optional)
    # Logged every round regardless of extend/stop -- previously only
    # logged at the terminal boundary (evidence.boundary.terminal_unresolved),
    # which meant a task that kept extending across several rounds left no
    # record anywhere of what confidence was actually shown at each of
    # those rounds.
    log_event(
        "task.turn_confidence.computed",
        provisional_turn_grade=provisional_turn_grade,
        repeated_need_override=has_repeated_need_this_turn,
        reject_null_hypothesis=reject_null_hypothesis,
        **test_diagnostics,
    )

    for decision in pending_decisions:
        task_id = decision["task_id"]
        attempt_count = decision["attempt_count"]
        partial_ids = decision["partial_ids"]
        reason = decision["reason"]
        refinement = decision["refinement"]
        requested_status = decision["requested_status"]
        generation_guidance = decision["generation_guidance"]
        unresolved_term = decision["unresolved_term"]
        clarification_reason = decision["clarification_reason"]
        clarification_guesses = decision["clarification_guesses"]
        # A partial task remains retryable with its retained evidence until
        # the applicable boundary. At the boundary it is answerable for the
        # supported part and carries a clarification only for the
        # unresolved part.
        next_attempt = attempt_count + 1
        # Extra Effort v2.0: a Territory B companion is a single committed
        # guess, never a hedge -- it gets exactly one evaluation, always
        # terminal, regardless of the tier gating below. Without this, an
        # insufficient companion re-enters retry_task_ids on its own
        # brand-new task_id (its own retry_counts start fresh at 0, same as
        # any other new task), and node_02e would construct a companion
        # for the companion -- confirmed live as genuine unbounded
        # recursion (task_1_scope -> task_1_scope_scope -> ...) before this
        # guard existed.
        is_scope_expansion_task = tasks_by_id.get(task_id, {}).get("search_territory") == "adjacent_scope"
        should_retry = (
            not is_scope_expansion_task
            and next_attempt <= MAX_RAG_ATTEMPTS
            and _attempt_unlocked(next_attempt, extend_if_optional)
        )
        if is_scope_expansion_task:
            # Clarification-authoring for this subquery belongs exclusively
            # to its in_scope (Territory A) task -- a companion sharing the
            # same subquery_id must never also author one, which would
            # otherwise produce two clarification_tasks entries for one
            # subquery_id, violating the "at most one per subquery_id"
            # guarantee the rest of this design relies on.
            unresolved_term = ""
            clarification_reason = ""
            clarification_guesses = []
        if not should_retry:
            # RELEVANT REQUIRES A REAL PARENT: generation_guidance can only
            # be non-empty when approved_ids (and therefore partial_ids) was
            # also non-empty at some point this task was scored (see the
            # per-round enforcement above), so checking partial_ids alone is
            # sufficient here -- a task with genuinely nothing retained
            # falls through to CLARIFICATION_REQUIRED below.
            if partial_ids:
                current_results[task_id] = {
                    "task_id": task_id,
                    "status": "RELEVANT",
                    "approved_parent_ids": partial_ids,
                    "reason": reason,
                    "generation_guidance": generation_guidance,
                    "unresolved_term": unresolved_term,
                    "clarification_reason": clarification_reason,
                    "clarification_guesses": clarification_guesses,
                }
                log_event(
                    "evidence.boundary.approved",
                    task_id=task_id,
                    approved_parent_ids=partial_ids,
                    status="RELEVANT",
                )
            else:
                current_results[task_id] = {
                    "task_id": task_id,
                    "status": "CLARIFICATION_REQUIRED",
                    "approved_parent_ids": [],
                    "reason": reason,
                    "unresolved_term": unresolved_term,
                    "clarification_reason": clarification_reason,
                    "clarification_guesses": clarification_guesses,
                }
            log_event(
                "evidence.boundary.terminal_unresolved",
                task_id=task_id,
                status=current_results[task_id]["status"],
                approved_parent_ids=partial_ids,
                turn_confidence_if_stopped=provisional_turn_grade,
                unresolved_term=unresolved_term,
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
                "missing_detail_clues": refinement,
            }

    unresolved = [result for result in current_results.values() if result.get("status") == "RETRY"]

    def _rag_gap_still_matters(result: dict) -> bool:
        # CLARIFICATION_REQUIRED asserts zero information is available for
        # this task -- a claim only RAG's own exhausted retries can make.
        # A subquery is commonly planned with RAG (often "complementary")
        # alongside a real tool like service_request/deeplink/api in the
        # same round; when that other attempt already fully resolves the
        # subquery, surfacing RAG's own empty result as a no_info entry
        # would tell the customer no information exists for something a
        # working shortcut was just provided for in this same turn.
        # RELEVANT is exempt from this filter: it carries real,
        # approved partial evidence to keep, not an absence claim, so it's
        # never contradicted by another tool's success the way an empty
        # result is.
        if result.get("status") != "CLARIFICATION_REQUIRED":
            return True
        task_meta = tasks_by_id.get(result.get("task_id"), {})
        # Extra Effort v2.0: a failed Territory B companion never surfaces
        # its own no-info marker, unconditionally -- the customer never
        # asked about this adjacent territory, so its own failure is
        # silently absorbed rather than announced. Whatever should be said
        # about the LITERAL request is entirely its in_scope sibling's own
        # job. This must not be keyed off "did the sibling succeed":
        # confirmed live (a "buy a car" turn where Territory A genuinely
        # found nothing and Territory B found only adjacent Savings
        # Account content) that suppressing based on sibling success alone
        # wrongly swallows the in_scope task's own genuine "no information
        # for this literal request" marker merely because its adjacent-
        # scope sibling happened to find related-but-different content --
        # the exact honesty claim this whole redesign exists to preserve,
        # left standing on the generator's own prompt adherence alone
        # instead of this deterministic marker.
        if task_meta.get("search_territory") == "adjacent_scope":
            return False
        # _resolved_by_other_tool, not _compute_subquery_status: this
        # subquery's own rag attempt is still the one being finalized
        # right now (_finalize_rag_attempts runs after this point, at the
        # end of this same node), so the status rollup would see it as
        # still "pending"/unset and never reach RESOLVED regardless of
        # what else already covers it -- the same reason the first
        # version of the node_02e fix for this didn't change anything.
        owning_subquery = subqueries_by_id.get(task_meta.get("subquery_id"))
        return not _resolved_by_other_tool(owning_subquery)

    # Evidence-authored clarifications: the sufficiency auditor -- not the
    # response generator -- decides whether a clarifying question is
    # warranted and writes the actual question + guesses, since it's the
    # node that actually reviewed the evidence and knows precisely what's
    # missing or ambiguous ("who raises it, who asks it," the same
    # principle every clarification-raising node in this pipeline follows,
    # sharing one standard contract -- see _build_clarification_context).
    # Same _rag_gap_still_matters guard as no_info gets: a CLARIFICATION_
    # REQUIRED task another tool already resolved this round must not also
    # surface a clarifying question about something already answered.
    new_evidence_clarifications = [
        {
            "subquery_id": tasks_by_id.get(result.get("task_id"), {}).get("subquery_id"),
            "originator": "sufficiency_auditor",
            "scope": _CLARIFICATION_SCOPE_BY_ORIGINATOR["sufficiency_auditor"],
            # clarification_reason, never the general `reason`: reason
            # explains the evidence verdict (used for every status --
            # SUFFICIENT/RELEVANT/IRRELEVANT alike), not
            # specifically why this counts as needing clarification.
            # clarification_reason is required to name a real detail from
            # the customer's own input/history -- see TaskSufficiencyItem.
            "clarification_reason": result.get("clarification_reason") or result.get("reason", "The retrieved evidence left a materially unresolved gap."),
            # Contract field name is unresolved_request regardless of
            # originator, even though node_03c's own model-facing field is
            # named unresolved_term -- unresolved_term is the auditor's own
            # characterization of a gap in the evidence, not necessarily a
            # term the customer said, which is exactly why the fallback
            # text for this originator uses a different template (see
            # _clarification_fallback_text: "Could you tell us more about
            # X?", never node_02e's "Could you clarify what you mean by X?"
            # -- that would falsely accuse the customer of having said X).
            "unresolved_request": result.get("unresolved_term", ""),
            "clarification_guesses": result.get("clarification_guesses", []),
        }
        for result in current_results.values()
        if result.get("status") in {"CLARIFICATION_REQUIRED", "RELEVANT"}
        and result.get("unresolved_term")
        and _rag_gap_still_matters(result)
        and tasks_by_id.get(result.get("task_id"), {}).get("subquery_id")
    ]
    # De-duplicate by subquery_id, keeping the FIRST (earliest-round)
    # occurrence. new_evidence_clarifications is rebuilt from the FULL
    # current_results every time this node runs -- not scoped to only the
    # tasks decided this specific round -- so a task that already finalized
    # with a clarification in an earlier round (and sits unchanged in
    # current_results while a *different* task keeps retrying) would
    # otherwise be regenerated and re-appended identically on every
    # subsequent invocation. Mutual exclusivity (a subquery resolves to
    # exactly one of SEARCH/CLARIFY/DECLINE, and a SEARCH task terminates
    # with at most one clarification) guarantees subquery_id is already
    # unique across the whole clarification_tasks list, making it the
    # natural dedupe key -- no separate clarification_id needed at all.
    existing_clarification_subquery_ids = {
        entry.get("subquery_id") for entry in state.get("clarification_tasks", [])
        if isinstance(entry, dict)
    }
    clarification_tasks = list(state.get("clarification_tasks", [])) + [
        entry for entry in new_evidence_clarifications
        if entry["subquery_id"] not in existing_clarification_subquery_ids
    ]
    # A CLARIFICATION_REQUIRED task now fully represented by its own
    # evidence-authored clarification above needs no separate no_info
    # statement -- there's no partial answer to caveat and nothing else to
    # say about it, unlike RELEVANT (whose evidence entry carries
    # resolution_status RELEVANT regardless, for the neutral availability
    # notice covering whatever the clarifying question doesn't; its entry
    # here only feeds turn-level bookkeeping such as node_11's fallback --
    # generation, revision and audit read no-info entries only, via
    # _no_info_entries).
    clarified_task_ids = {
        result.get("task_id") for result in current_results.values()
        if result.get("status") == "CLARIFICATION_REQUIRED" and result.get("unresolved_term")
    }
    need_by_subquery_id = {
        subquery.get("subquery_id"): str(subquery.get("need", "")).strip()
        for subquery in state.get("financial_subqueries", state.get("triaged_subqueries", []))
        if isinstance(subquery, dict)
    }
    task_resolution_instructions = [
        _terminal_retrieval_feedback(
            result.get("task_id"),
            tasks_by_id.get(result.get("task_id"), {}).get("customer_component", ""),
            result.get("status", "CLARIFICATION_REQUIRED"),
            need_by_subquery_id.get(tasks_by_id.get(result.get("task_id"), {}).get("subquery_id"), ""),
        )
        for result in current_results.values()
        if result.get("status") in {"CLARIFICATION_REQUIRED", "RELEVANT"}
        and result.get("task_id") not in clarified_task_ids
        and _rag_gap_still_matters(result)
    ]
    full_count = sum(result.get("status") == "SUFFICIENT" for result in current_results.values())
    partial_count = sum(result.get("status") == "RELEVANT" for result in current_results.values())
    print(f"   ✅ [TASK AUDIT]: {full_count} sufficient, {partial_count} terminal partial, {len(unresolved)} pending retry.")
    retry_expansion = _retry_expansion_plan(
        [tasks_by_id[result.get("task_id")] for result in unresolved if result.get("task_id") in tasks_by_id],
        retry_counts,
    )
    for result in current_results.values():
        event = (
            "task.sufficiency.completed" if result.get("status") == "SUFFICIENT"
            else "task.partial_terminal" if result.get("status") == "RELEVANT"
            else "task.retry" if result.get("status") == "RETRY"
            else "task.clarification_required"
        )
        next_stage = next((item for item in retry_expansion if item.get("task_id") == result.get("task_id")), {})
        log_event(event, task_id=result.get("task_id"), status=result.get("status"), approved_parent_ids=result.get("approved_parent_ids", []), reason=result.get("reason", ""), retry_count=retry_counts.get(result.get("task_id"), 0), next_expansion_stage=next_stage.get("stage", ""))
    # Per-task dynamic confidence, logged individually for future analysis
    # (see RAG_RETRY_EFFORT_v1.0.md) -- each one already folded into
    # provisional_turn_grade above via rag_task_scores_by_subquery_id, so
    # this is purely an additional observability record, not a second,
    # independently-computed aggregate that could quietly drift apart
    # from the one that actually drove the retry/stop decision.
    for result in current_results.values():
        task_id = result.get("task_id")
        log_event(
            "task.dynamic_confidence",
            task_id=task_id,
            round=retry_counts.get(task_id, 0),
            status=result.get("status"),
            confidence=_dynamic_task_confidence(result, partial_evidence),
        )
    # The number displayed live to the customer as "confidence" is the
    # hypothesis test's OWN sample mean -- up to 29 historical turns' worth
    # of response quality plus this turn's own volatile, still-updating
    # grade as the sample's newest point -- not this turn's own grade in
    # isolation. Showing this turn's raw grade alone reads as "the bot has
    # no idea" the moment a single hard question fails, even when the
    # other ~29 samples are healthy; the aggregate is what the hypothesis
    # test itself reasons about, so displaying anything else would let the
    # number the customer watches drift apart from the number that
    # actually drives the retry/stop decision below. Recomputed fresh
    # every time this node runs within a turn (this turn's own grade keeps
    # changing as subqueries resolve round over round), so the customer
    # sees the sample mean move live as retrieval progresses. Falls back
    # to this turn's own grade only when there is no sample at all yet
    # (mean is None only when the sample itself is empty).
    live_task_confidence = test_diagnostics.get("mean")
    if live_task_confidence is None:
        live_task_confidence = provisional_turn_grade
    # "Thinking harder" reflects whether extra effort is ACTIVELY being
    # spent right now: both that the hypothesis test found significant
    # evidence to reject H0, AND that there's an actual pending task this
    # round for that finding to apply to -- a round where everything
    # already resolved SUFFICIENT has nothing left to extend, regardless
    # of what the test concludes from history. Correct as-is even under
    # Extra Effort v2.1's simultaneous-round design: node_02e decides
    # Territory A's (unconditional) widen AND Territory B's (conditional,
    # gated by this exact same extend_if_optional) companion construction
    # together, in the SAME retry pass -- so whenever extend_if_optional is
    # True here, Territory B genuinely IS being attempted this round
    # alongside Territory A, not merely eligible for a later one.
    is_low_confidence = bool(pending_decisions) and extend_if_optional
    # The RAG subgraph is an implementation detail of a generic tool
    # attempt. Only terminal, approved IDs cross this boundary; raw
    # candidates and retry evidence remain inside RAG.
    # Extra Effort v2.1: this node can now create new search_tasks entries
    # itself (direct-adopt companions, above) for the first time -- state's
    # own search_tasks has no LangGraph reducer (plain replace), so the
    # updated list is built once here and used for BOTH the
    # _finalize_rag_attempts call below and the final return. Using the raw
    # `state` parameter for _finalize_rag_attempts here would silently miss
    # a companion created in this exact call when its source task also
    # terminates in this same round (fully possible -- e.g. a single-
    # subquery turn with extend_if_optional=False finishes in one round),
    # finalizing that subquery's rag tool_attempt as "unavailable" even
    # though real, direct-adopted evidence exists.
    updated_search_tasks = state.get("search_tasks", []) + new_direct_adopt_tasks
    financial_subqueries = state.get("financial_subqueries", state.get("triaged_subqueries", []))
    if not unresolved:
        financial_subqueries = _finalize_rag_attempts(
            {**state, "search_tasks": updated_search_tasks}, list(current_results.values())
        )
    return {
        "preflight_status": "retry_tasks" if unresolved else "task_resolution_ready",
        "task_results": list(current_results.values()),
        "task_retry_counts": retry_counts,
        "task_feedback": feedback,
        "search_tasks": updated_search_tasks,
        # Completed-task audit history can include internal evidence deltas.
        # Retain it only for tasks that will retry; it must not cross the
        # terminal evidence boundary into subsequent nodes.
        "task_sufficiency_history": {
            task_id: sufficiency_history.get(task_id, [])
            for task_id in {result.get("task_id") for result in unresolved}
            if task_id
        },
        "task_partial_evidence": partial_evidence,
        "task_partial_guidance": partial_guidance,
        "task_rejected_parent_ids": rejected_parent_ids,
        "task_resolution_instructions": task_resolution_instructions,
        "clarification_tasks": clarification_tasks,
        "financial_subqueries": financial_subqueries,
        "low_confidence_mode": is_low_confidence,
        "live_task_confidence": live_task_confidence,
        "territory_b_unlocked": territory_b_unlocked,
    }


# --- PHASE 3: RESOLUTION BLUEPRINT & STEP-BY-STEP RESPONSE ---

# --- Turn consistency check (node_04a, after tool calling has settled) ---
# The checker decides semantically which side of a conflict to drop (per the
# user: a fixed priority order would make the LLM call nearly pointless --
# close to a purely mechanical rule). Code only validates its decision; the
# one hard rule is that the customer's own data is never dropped, since the
# checker is never shown its value and so has no basis to judge it wrong.


def _turn_consistency_items(state: AgentState) -> list[dict]:
    """Every result this turn that asserts something the response may say.

    Included: approved FAQ evidence (after the evidence guards), the
    customer's own data (personal-data tokens, verified facts), and no-info
    statements. Excluded: redirects (deeplink/service_request -- a path to
    act, never an answer, so there is nothing to contradict), and
    clarifications/declines (a question or a refusal, not a factual claim).
    Precision placeholders in FAQ evidence are resolved to their real
    values here: the checker compares facts, and two different placeholder
    ids can hide the same number (seen live -- two card-arrival FAQs, both
    6-7 days, were judged in conflict because they cited different ids).
    These are public FAQ figures, not personal data, and the resolved text
    exists only in this checker's view -- the evidence generation reads
    keeps its placeholders, so exact numbers still come only from node_07.
    The customer's own data stays a `{{data-*}}` token (its real value is
    never shown to any LLM); the prompt tells the checker to treat each
    token as the value it stands for. `_customer_data` marks items never
    dropped.
    """
    tasks_by_id = {t.get("task_id"): t for t in state.get("search_tasks", []) if isinstance(t, dict)}
    status_by_task = {r.get("task_id"): r.get("status") for r in state.get("task_results", []) if isinstance(r, dict)}
    need_by_subquery = {
        sq.get("subquery_id"): str(sq.get("need", "")).strip()
        for sq in state.get("financial_subqueries", state.get("triaged_subqueries", []))
        if isinstance(sq, dict)
    }
    evidence = _hydrate_response_evidence(state)
    placeholder_ids = sorted({
        pid for entry in evidence for p in entry.get("approved_parents", []) if isinstance(p, dict)
        for pid in re.findall(r"\{\{(faq_[0-9a-f]{8}_\d{3})\}\}", str(p.get("content", "")))
    })
    values = fetch_placeholders_from_db(placeholder_ids) if placeholder_ids else {}

    def _resolved(text: str) -> str:
        return re.sub(r"\{\{(faq_[0-9a-f]{8}_\d{3})\}\}", lambda m: values.get(m.group(1), m.group(0)), text)

    items = []
    for entry in evidence:
        task = tasks_by_id.get(entry["task_id"], {})
        items.append({
            "item_id": entry["task_id"],
            "kind": "bank FAQ evidence"
                    + (" (related topic, not a direct match)" if entry.get("search_territory") == "adjacent_scope" else "")
                    + (" -- partial answer" if status_by_task.get(entry["task_id"]) == "RELEVANT" else ""),
            "need": need_by_subquery.get(task.get("subquery_id"), "") or task.get("customer_component", ""),
            "content": [_resolved(str(p.get("content", ""))) for p in entry.get("approved_parents", []) if isinstance(p, dict)],
        })
    for entry in _personal_data_placeholders(state):
        items.append({
            "item_id": entry["handle"],
            "kind": "the customer's own data",
            "need": entry.get("for_request", ""),
            "content": [f"{str(entry.get('field_description', '')).rstrip('. ')}: {entry.get('insert_token', '')}"],
            "_customer_data": True,
        })
    for entry in _verified_api_context(state):
        items.append({
            "item_id": entry["handle"],
            "kind": "the customer's own verified data",
            "need": entry.get("for_request", ""),
            "content": [entry.get("fact", "")],
            "_customer_data": True,
        })
    task_metadata = {t: task.get("customer_component", "") for t, task in tasks_by_id.items()}
    no_info_context = _no_info_entries(
        state.get("task_resolution_instructions", []), task_metadata, state.get("unresolvable_search_tasks", [])
    )
    for entry in no_info_context:
        items.append({
            "item_id": entry.get("task_id"),
            "kind": "a statement that no information is available",
            "need": entry.get("need", "") or entry.get("resolved_subquery", ""),
            "content": [entry.get("no_info_evidence", "")],
        })
    return [item for item in items if item.get("item_id")]


def _run_turn_consistency_check(state: AgentState) -> dict:
    """Judge semantic conflicts between this turn's results and let the
    checker decide which side of each conflict to drop; code validates the
    decision and removes dropped items from the data response generation
    reads. Returns node_04a state updates (empty when nothing changes).

    Only runs when the turn has at least two asserting results -- a single
    result cannot contradict itself, so most turns cost nothing. A failed
    call suppresses nothing (the BUG-018 evidence guard remains the
    backstop).
    """
    items = _turn_consistency_items(state)
    if len(items) < 2:
        log_event("turn.consistency.skipped", reason="fewer than two asserting results", item_count=len(items))
        return {}
    by_id = {item["item_id"]: item for item in items}
    view = [{k: v for k, v in item.items() if not k.startswith("_")} for item in items]
    prompt = f"""
    TURN CONSISTENCY CHECK
    One customer turn has produced the results below, and a single response
    will be written from all of them together. Your job: find results that
    would conflict if presented in that one response, and for each conflict
    decide which side the customer should be shown.

    Two items CONFLICT when presenting both would contradict or undercut each
    other: one states, implies, or assumes something the other denies --
    including one saying information is unavailable, unknown, or cannot be
    provided while another item provides it, or two items giving different
    values, conditions, or procedures for the same thing.
    Items do NOT conflict when they concern different needs, or when they
    complement each other for the same need -- the customer's own data next
    to a general explanation, a procedure next to a related policy, general
    guidance next to a specific fact. Different is not conflicting.

    Two items that state the same fact in different words, or that give the
    same values, agree -- they do not conflict.

    PLACEHOLDERS ARE VALUES: a `{{{{data-*}}}}` token (or any `{{{{faq_*}}}}`
    token left in the text) stands for one real value you are never shown.
    Treat each token as that value: the information is present and known,
    and it is exactly what its item says it is (for example
    "Account balance: {{{{data-a1b2c3d4}}}}" is the customer's actual
    balance). Never read a token's name as its value, never treat two
    different tokens as different values, and never judge whether the
    hidden value is correct.

    For every item, first write `reason`: which other items (if any) concern
    the same need, and whether anything they say contradicts or undercuts
    this item. Then list `conflicts_with`: the ids of the items it conflicts
    with, or an empty list. Then set `decision`:
    - "keep" for every item that conflicts with nothing.
    - For each conflict, decide which side the customer should be shown and
      mark the other "drop": keep the item that answers the customer's need
      most accurately, directly, and certainly; drop the one that is less
      accurate, less direct, less certain, or that the other contradicts.
      Drop only what the conflict requires -- never both sides of one
      conflict, and never an item that conflicts with nothing.

    Output JSON only: {{"items":[{{"item_id":"...","reason":"...","conflicts_with":["..."],"decision":"keep" | "drop"}}]}}

    {_DATA_FOLLOWS}

    Customer message: {json.dumps(state.get("user_input", ""), ensure_ascii=False)}
    Items: {json.dumps(view, ensure_ascii=False)}
    """
    try:
        result = _parse_json_robustly(call_groq_lpu(prompt, model_id=MODEL_C_PLANNER, response_schema_override=TurnConsistencyEvaluation))
    except Exception as error:
        print(f"   ⚠️ [TURN CONSISTENCY]: check failed, suppressing nothing: {error}")
        log_event("turn.consistency.failed", level="WARNING", error=str(error), item_count=len(items))
        return {}

    pairs, reasons, dropped_by_model = set(), {}, set()
    for entry in (result.get("items", []) if isinstance(result, dict) else []):
        if not isinstance(entry, dict) or entry.get("item_id") not in by_id:
            continue
        reasons[entry["item_id"]] = str(entry.get("reason", ""))
        for other in entry.get("conflicts_with", []) or []:
            if other in by_id and other != entry["item_id"]:
                pairs.add(frozenset((entry["item_id"], other)))
        if str(entry.get("decision", "keep")).strip().lower() == "drop":
            dropped_by_model.add(entry["item_id"])

    # Validate the checker's decisions -- never override its semantic choice:
    # a drop counts only for an item it named in a conflict, never the
    # customer's own data, and never both sides of the same conflict.
    in_conflict = {i for p in pairs for i in p}
    rejected = []
    suppressed = set()
    for item_id in sorted(dropped_by_model):
        if item_id not in in_conflict:
            rejected.append([item_id, "drop without a named conflict"])
        elif by_id[item_id].get("_customer_data"):
            rejected.append([item_id, "customer's own data is never dropped"])
        else:
            suppressed.add(item_id)
    for pair in pairs:
        if pair <= suppressed:  # both sides dropped -- keep both rather than lose the need
            suppressed -= pair
            rejected.append([sorted(pair), "both sides of one conflict dropped"])
    ties = [sorted(p) for p in pairs if not (p & suppressed)]  # conflicts left with both sides kept

    print(f"   🔎 [TURN CONSISTENCY]: {len(items)} item(s), {len(pairs)} conflict(s), suppressed={sorted(suppressed)}, unresolved={ties}, rejected={rejected}")
    log_event(
        "turn.consistency.checked",
        item_ids=sorted(by_id), conflicts=[sorted(p) for p in pairs], suppressed=sorted(suppressed),
        unresolved_conflicts=ties, rejected_decisions=rejected,
        reasons={k: v for k, v in reasons.items() if k in in_conflict},
    )
    if not suppressed:
        return {}
    return {
        "consistency_suppressed_ids": sorted(suppressed),
        # No-info statements are dropped from the lists generation reads
        # directly (and node_11's fallback too), so every consumer agrees.
        "task_resolution_instructions": [
            m for m in state.get("task_resolution_instructions", [])
            if not (isinstance(m, dict) and m.get("task_id") in suppressed)
        ],
        "unresolvable_search_tasks": [
            e for e in state.get("unresolvable_search_tasks", [])
            if not (isinstance(e, dict) and e.get("task_id") in suppressed)
        ],
    }


def node_04a_resolution_planner(state: AgentState):
    """Assess global resolution after validated tool work, without planning UI.

    Tool Calling v3.0: resolution_status is a deterministic rollup
    (_compute_subquery_status) over each subquery's own tool_attempts --
    never a second, independently-fallible LLM judgment that could disagree
    with what the attempts themselves already show. The one genuinely
    semantic decision this node used to also make -- does an untried
    capability's scope actually match a subquery's specific situation -- is
    not re-derived here at all: it is node_03a's own planning call, the
    single place that judgment is made, every round, for every still-OPEN
    subquery (see node_03a's docstring). A subquery reaching this node still
    OPEN, with the tool-planning budget spent, is functionally EXHAUSTED --
    there is no separate stored value for that; OPEN plus "no more rounds
    will run" is exactly what EXHAUSTED means.
    """
    print("🟢 [NODE 04a] Executing Global Resolution Sufficiency Check")
    financial_subqueries = [
        dict(item) for item in state.get("financial_subqueries", state.get("triaged_subqueries", []))
        if isinstance(item, dict)
    ]
    planning_round = int(state.get("tool_planning_round", 0))
    total_attempts = sum(len(sq.get("tool_attempts", [])) for sq in financial_subqueries)
    # node_03a already computes, every round, whether that round's own full
    # relevance judgment added anything new for any subquery at all
    # (tool_plan_status "exhausted" when it didn't) -- this was previously
    # written but never read anywhere, so a subquery with genuinely nothing
    # left to try still burned 1-2 more full LLM round-trips reconfirming
    # that empty answer before the fixed MAX_TOOL_PLANNING_ROUNDS budget
    # caught up. Reading it here makes the round budget end as soon as
    # node_03a itself signals there is nothing left worth planning, instead
    # of always spending the full fixed budget regardless. Safe by
    # construction: node_03a's single call already covers every open
    # subquery together each round, so "nothing new this round" means
    # nothing new for anyone, not just the subquery that happens to be
    # checked here; VOLATILE_STATE_RESET defaults this to "continue" before
    # node_03a has run at all this turn, so a fresh turn is never affected.
    planner_found_nothing_new = state.get("tool_plan_status") == "exhausted"
    budget_exhausted = (
        planning_round >= MAX_TOOL_PLANNING_ROUNDS
        or total_attempts >= MAX_TOOL_TASKS_PER_TURN
        or planner_found_nothing_new
    )

    open_subquery_ids = []
    subquery_status = {}
    for subquery in financial_subqueries:
        status = _compute_subquery_status(subquery)
        subquery["resolution_status"] = status
        subquery_status[subquery.get("subquery_id")] = status
        if status == "OPEN":
            open_subquery_ids.append(subquery.get("subquery_id"))

    # A subquery already flagged for clarification (by node_02e or
    # node_03a) is reported as OPEN above for accurate observability, but
    # it must not trigger another tool-planning round on its own -- it's
    # parked pending the customer's answer, not genuinely still in
    # progress. Without this, a turn whose only open subquery just got
    # clarified with no new tool_attempts (so tool_plan_status stays
    # "continue") would burn a wasted extra round-trip back to node_03a,
    # which would immediately exclude it anyway.
    already_clarified_subquery_ids = {
        entry.get("subquery_id") for entry in state.get("clarification_tasks", [])
        if isinstance(entry, dict)
    }
    genuinely_open_subquery_ids = [
        subquery_id for subquery_id in open_subquery_ids
        if subquery_id not in already_clarified_subquery_ids
    ]
    needs_tools = bool(genuinely_open_subquery_ids) and not budget_exhausted
    preflight_status = "needs_tools" if needs_tools else "presentation_ready"

    print(f"   🧭 [GLOBAL RESOLUTION]: {len(open_subquery_ids)} open subquery(ies){' (budget exhausted' + (', planner found nothing new' if planner_found_nothing_new else '') + ')' if budget_exhausted and open_subquery_ids else ''} -> {preflight_status}")
    log_event(
        "resolution.planned",
        open_subquery_ids=open_subquery_ids,
        subquery_status=subquery_status,
        needs_tools=needs_tools,
        budget_exhausted=budget_exhausted,
        planner_found_nothing_new=planner_found_nothing_new,
        planning_round=planning_round,
    )
    # Turn consistency check: only once tool calling has settled (no further
    # retry -- a needs_tools pass loops back to node_03a and will come
    # through here again), so it judges the turn's final results exactly
    # once, right before response generation.
    consistency_updates = (
        _run_turn_consistency_check({**state, "financial_subqueries": financial_subqueries})
        if preflight_status == "presentation_ready" else {}
    )
    return {
        **consistency_updates,
        "preflight_status": preflight_status,
        "financial_subqueries": financial_subqueries,
        "tool_plan_status": "continue" if needs_tools else ("exhausted" if open_subquery_ids else "resolved"),
        "selected_ux_journey": "FLEXIBLE",
        "presentation_plan": {"resolution_context": {"subquery_status": subquery_status}},
        "active_plan": {},
        "completed_steps": [],
    }



def _style_personalization_block(user_profile: dict) -> str:
    """Shared STYLE & PERSONALIZATION block for every response-generation
    sub-prompt (both the flexible generator and the narrower evidence-only
    one) -- factored out so a turn resolved entirely by decline/clarification/
    no-info evidence still reads in the customer's own language, tone, and
    literacy depth instead of a bare recitation of the evidence text.
    """
    literacy_level = user_profile.get("financial_literacy", "intermediate")
    if literacy_level == "beginner":
        literacy_instruction = "Beginner (Use simple financial terms. Explain financial terms when they appear in the response.)"
    elif literacy_level == "professional":
        literacy_instruction = "Professional (Use professional financial terms freely. No explanation of financial terms necessary.)"
    else:
        literacy_instruction = "Intermediate (Use normal financial terms. Explain only complex financial terms when they appear in the response.)"
    return f"""
    STYLE & PERSONALIZATION INSTRUCTIONS:
    (These are phrasing instructions only; do not invent banking facts)
    - Natively generate response in: {user_profile.get("language") or "English"}
    - "Horizon" and every "Horizon <Name>" branded term (Horizon Bank,
      Horizon App, Horizon Key, Horizon Card, and any other product/feature
      name carrying the Horizon brand) is a proper brand name, never a
      description -- write it EXACTLY as it appears in the evidence, in the
      original Latin script, regardless of what language the rest of the
      response is written in. Never translate, transliterate, or localize
      it into another script or invented equivalent (e.g. never render
      "Horizon App" as a Chinese phonetic name) -- a brand name does not
      change with the customer's language.
    - Tone: {user_profile.get("tone", "professional")}
    - Financial Literacy Depth: {literacy_instruction}
    - Emojis: {user_profile.get("emoji", "enabled")}
    - Do not open with a greeting ("Hello", "Hi", a wave emoji, the
      customer's name as a salutation, etc.). The customer was already
      greeted once when this session started; every reply you write here
      is a continuation of an ongoing conversation, not a fresh
      introduction. A "friendly" tone means warm, natural word choice
      throughout the reply -- it does not mean re-greeting the customer on
      every turn, which reads as mechanical repetition rather than warmth.
      Start directly with the substance of the answer.
    """


# Shared verbatim guardrails for every response-generation sub-prompt, drawn
# from the exact same constants node_06_outbound_combined_audit_gate audits
# against (see BANK_CONDUCT_CONTEXT/REGULATORY_LICENSING_CONTEXT there). Writing the response within these
# boundaries from the start -- rather than relying solely on the downstream
# audit to catch a violation and force a redraft -- cuts the retry loop this
# turn's generation/audit exchange would otherwise need; the audit still runs
# unconditionally afterward as the actual enforcement mechanism, this only
# reduces how often it has something to reject.
_GENERATION_CONDUCT_GUARDRAILS = f"""
    CONDUCT & COMPLIANCE BOUNDARIES:
    Everything you write is independently audited against these bank conduct
    rules and regulatory restrictions before being shown to the customer.
    Write within them from the start.

    Bank Conduct Rules:
    {BANK_CONDUCT_CONTEXT}

    Regulatory Context:
    {REGULATORY_LICENSING_CONTEXT}
    """


# Same reasoning and precedent as _GENERATION_CONDUCT_GUARDRAILS above, now
# for the Inferential Grounding ("Bridge Check") rule node_06_outbound_
# combined_audit_gate already enforces post-hoc -- writing within this
# boundary from the start reduces how often that audit has a bridge
# violation to reject, the same way the conduct guardrails already do for
# conduct violations. Traced live: a card-replacement shortcut whose only
# supplied topic was "freeze, replace, enable overseas usage" got narrated
# as "get a new look for your card" -- an invented OUTCOME claim the
# shortcut's own fields never stated, and the audit's own reasoning missed
# it (it checked whether the shortcut matched the request, not whether this
# specific claim about it was itself supported). This constant is
# deliberately the stricter no-bridging bar the shortcut rule already
# uses, not the looser bounded-inference bar Task-Level Approved Evidence
# gets elsewhere in this prompt -- a shortcut's `label`/`topic` are a fixed,
# short, already-final description, not raw material to reason further from.
_GENERATION_BRIDGE_GUARDRAILS_BASE = """
    BRIDGE REASONING BOUNDARY:
    A natural, bounded inference connecting approved evidence to the
    customer's resolved request is fine -- evidence stating a maximum cap
    supports concluding coverage cannot exceed it, for instance. Never
    state or imply an unstated premise, actor, policy, procedure, reason,
    certainty, exception, capability, or outcome that the evidence itself
    does not establish.
    This applies to a shortcut's `label`/`topic` even more strictly than to
    retrieved evidence: naming a shortcut and what it lets the customer do
    is grounded, but implying WHAT completing it will produce or change --
    an outcome its own `topic` never states -- is not, even if it sounds
    like a natural, helpful inference. For example, a "Manage Card"
    shortcut whose `topic` is only "freeze, replace, enable overseas usage"
    supports telling the customer they can request a replacement; it does
    NOT support telling them the replacement will look different, since no
    supplied field says so.

    COURTESY ACKNOWLEDGMENT (do this, don't just avoid the violation above):
    A brief, warm, natural acknowledgment of the customer's own stated
    feeling, frustration, dissatisfaction, or reason (e.g. "I'm sorry to
    hear that" for a lost, damaged, or disliked card) is encouraged, not
    just tolerated -- it is never itself a banking claim, so it never
    counts against the boundary above, as long as it stays a genuine
    courtesy reaction to what the customer said and introduces no fact,
    capability, procedure, or outcome beyond what evidence actually
    supports. These are two separate moves, not one sentence trying to do
    both: acknowledge the feeling first, in your own natural words, with
    no evidence needed for that part at all; then state the actual
    capability separately, bounded strictly by what evidence supports.
    Do not solve the bridge-boundary problem by dropping warmth -- solve it
    by keeping the empathy and the capability claim in two distinct moves
    instead of fusing them into one overreaching sentence.
    """

# Two variants of the same evidence-caveat instruction, differing only in
# which field path actually carries the caveat for that consumer's own
# data shape -- kept as two named constants, not one paragraph plus a
# correction note, so each consumer's own prompt is simply correct on its
# own terms. FLAT is for a consumer still reading a flat evidence_by_task
# entry (task_id/approved_parents/generation_guidance all top-level):
# node_04c_response_reviser and _generate_evidence_only_response, neither
# migrated to the unified obligation contract yet (that migration is
# stage 2 of docs/RESPONSE_OBLIGATION_CONTRACT_DESIGN.md). UNIFIED is for
# _generate_response_blocks, the one consumer already on the new contract,
# where the caveat lives at generation_content.auditor_note and the parent
# list at generation_content.approved_parents -- generation_guidance at
# that entry's own top level is a different field entirely (the mode-level
# instruction shared by every obligation kind, not this caveat).
_GENERATION_GUIDANCE_PARAGRAPH_FLAT = """
    GENERATION GUIDANCE: an evidence entry's `generation_guidance` field
    (when non-empty) is a directive on how that specific piece of evidence
    must be framed or caveated -- never optional context, and never dropped
    merely because the underlying facts are otherwise correctly grounded.
    It is never itself a fact to relay verbatim, and never a substitute for
    stating the actual evidence -- it tells you HOW to write about the
    evidence, not WHAT to claim instead of it. For example, guidance noting
    that an approved procedure assumes a different circumstance than the
    customer described (a card-replacement procedure written for a damaged
    card, approved for a customer who dislikes the card's appearance)
    means you must say so plainly before presenting that procedure as the
    customer's path -- never present it as if it were written for their
    exact situation when it wasn't.

    An evidence entry whose `approved_parents` list is EMPTY (a task found
    genuinely nothing that answers the request, but the sufficiency
    auditor still had a hedged, guidance-worthy observation worth
    conveying) carries no citable fact at all -- `generation_guidance` is
    your ONLY basis for what to write about it. Follow it directly
    (typically: an honest admission that nothing confirms the answer,
    phrased around the customer's own stated situation, optionally
    followed by an unclaimed courtesy mention if the guidance itself
    offers one) -- never invent a fact, procedure, or figure to fill the
    gap merely because a manifest entry exists for this task_id.
    """

_GENERATION_GUIDANCE_PARAGRAPH_UNIFIED = """
    GENERATION GUIDANCE: an evidence entry's own caveat --
    `generation_content.auditor_note` (when non-empty) -- is a directive on
    how that specific piece of evidence must be framed or caveated -- never
    optional context, and never dropped merely because the underlying
    facts are otherwise correctly grounded. It is never itself a fact to
    relay verbatim, and never a substitute for stating the actual evidence
    -- it tells you HOW to write about the evidence, not WHAT to claim
    instead of it. For example, a caveat noting that an approved procedure
    assumes a different circumstance than the customer described (a
    card-replacement procedure written for a damaged card, approved for a
    customer who dislikes the card's appearance) means you must say so
    plainly before presenting that procedure as the customer's path --
    never present it as if it were written for their exact situation when
    it wasn't.

    An evidence entry whose `generation_content.approved_parents` list is
    EMPTY (a task found genuinely nothing that answers the request, but
    the sufficiency auditor still had a hedged, guidance-worthy observation
    worth conveying) carries no citable fact at all --
    `generation_content.auditor_note` is your ONLY basis for what to write
    about it. Follow it directly (typically: an honest admission that
    nothing confirms the answer, phrased around the customer's own stated
    situation, optionally followed by an unclaimed courtesy mention if the
    note itself offers one) -- never invent a fact, procedure, or figure to
    fill the gap merely because a manifest entry exists for this id. This
    is separate from the entry's own top-level `generation_guidance` field,
    which is the one-line instruction for what kind of obligation this is
    (shared across every kind: compose from evidence, relay a fixed
    statement, ask a question, or state a shortcut/token/fact) -- read
    both; they answer different questions.
    """

# Preserves the pre-split name/shape for _generate_evidence_only_response,
# which still reads a flat evidence_by_task entry and was never meant to
# change when _generate_response_blocks moved to the unified contract.
# node_04c_response_reviser used this too until its own Stage 2 migration
# to the unified obligation contract; it now uses the UNIFIED variant below.
_GENERATION_BRIDGE_GUARDRAILS = _GENERATION_BRIDGE_GUARDRAILS_BASE + _GENERATION_GUIDANCE_PARAGRAPH_FLAT

# For _generate_response_blocks and node_04c_response_reviser -- both read
# the unified `{id, generation_content, generation_guidance, generation_rule}`
# obligation shape.
_GENERATION_BRIDGE_GUARDRAILS_UNIFIED = _GENERATION_BRIDGE_GUARDRAILS_BASE + _GENERATION_GUIDANCE_PARAGRAPH_UNIFIED


# Shared by node_03c_sufficiency_auditor (writing generation_guidance
# proactively, before generation ever runs) and node_06_outbound_
# combined_audit_gate (writing it reactively, into a violation, after
# generation already got it wrong) -- one shape, two producers, so the
# writer (node_04b/node_04c, guided by _GENERATION_BRIDGE_GUARDRAILS_UNIFIED's
# own "GENERATION GUIDANCE" paragraph above) never has to reconcile two
# different vocabularies for the same field. Deliberately NOT a fixed
# "always admit you don't know" template: that shape is only correct for
# the one case where nothing about the customer's exact question is
# confirmed at all (case 2 below), and forcing it onto every mismatch would
# create a new failure mode -- discarding a genuinely evidence-supported
# answer (case 3) into a false "I don't know," which is exactly what the
# PARTIAL-EVIDENCE DECISION ORDER rule (node_03c's own prompt) already
# exists to prevent.
_GENERATION_GUIDANCE_SHAPE_RULE = """
    GENERATION GUIDANCE SHAPE RULE (governs both node_03c's proactive
    generation_guidance and any violation's own generation_guidance below):
    Guidance is always a direct instruction of what to write next, never a
    description of what's wrong or a list of forbidden phrasings -- naming
    a forbidden phrase tends to make a writer produce it, not avoid it, so
    a guidance field's job is to say what TO write, in full, every time.

    For the exact proposition in question, first separate what the
    evidence DOES confirm from what it DOES NOT:
    - If the evidence confirms it (even if worded differently than the
      customer's own phrasing, or only a bounded/conditional form of it),
      instruct asserting that confirmed fact plainly. No admission is
      warranted here -- manufacturing one would itself be a groundedness
      failure in the other direction, discarding a real answer.
    - If part of the same question is confirmed and a different, specific
      part of it is not, instruct asserting the confirmed part AND,
      separately, admitting the specific unconfirmed remainder. The two
      must never be merged into one sentence that lets the confirmed
      part's certainty bleed into the unconfirmed one.
    - If nothing about the customer's exact question is confirmed at all,
      instruct admitting that plainly -- phrased around the customer's own
      stated situation, not a paraphrase of it -- and stopping there. Only
      if topically-adjacent evidence exists, additionally instruct offering
      it as an unclaimed courtesy that may still help, never as an answer,
      accommodation, or resolution of the admitted gap.
    - If there is no evidence gap at all (a pure conduct/tone correction, or
      a composition omission such as a decline reference that must be
      relayed, or an invented clarification guess that must be dropped),
      instruct the specific correct neutral phrasing or required content
      directly -- there is nothing to partition in these cases.

    The "confirmed" case above is bounded by the exact same rule the
    generator's own composition is held to for this same evidence --
    borrowed here deliberately, not restated in different words, so the
    instruction you write and the response it produces are judged by one
    aligned standard rather than two that merely resemble each other: "Never
    add a fact, procedure, or product not present in it." The response
    generator trusts this instruction completely and composes from it
    without re-deriving or re-checking it against the evidence itself --
    that trust is the design, not a gap to defend against downstream, which
    is exactly why it must already hold here, at the one place this
    instruction is written. Confirming that SOME part of the evidence
    answers the question is never license to instruct a further specific
    fact, condition, or requirement that is not itself stated in that
    evidence's own text, even when it sounds like standard, plausible
    banking practice, and even when other, genuinely confirmed content from
    the same parent sits right next to it. Before instructing any such
    detail, point to the specific approved parent (or, for a violation's
    own guidance, the specific evidence entry) whose own text actually
    states it; if none does, leave that detail out of the instruction
    rather than include it.

    This is bounded by the Bank Conduct Rules and Regulatory Context
    already established elsewhere in this prompt -- an instruction that
    would itself cause a violation of those rules is invalid regardless of
    which case above it falls under.
    """


def _flatten_response_block_text(block: dict) -> str:
    """Reconstruct one block's rendered plain-text form from its own stored
    fields alone -- the same text `_generate_response_blocks` computes
    inline while first building each block, needed again wherever only the
    already-built block dict is available (the completeness nets, the
    per-task-revision loop's `final_answer` reconstruction, and the
    audit-join's `safe_answer_for_fallback`). Each type's formula below is
    copied verbatim from the corresponding branch in
    `_generate_response_blocks`'s element-building loop; keep them in sync.
    """
    block_type = block.get("type", "text")
    title = block.get("title", "")
    extended_text = block.get("extendedText", "")
    lead_text = block.get("contextText", "")
    if block_type == "comparison":
        rows = block.get("rows", [])
        parts = [lead_text, title, *[f"{r['feature']}: {', '.join(r['values'])}" for r in rows if isinstance(r, dict)], extended_text]
    elif block_type == "accordion":
        sections = block.get("sections", [])
        parts = [lead_text, title, *[f"{s['title']}: {s['content']}" for s in sections if isinstance(s, dict)], extended_text]
    elif block_type == "timeline":
        steps = block.get("steps", [])
        parts = [lead_text, title, block.get("subtitle", ""), *[f"{s['title']}: {s['description']}" for s in steps if isinstance(s, dict)], extended_text]
    elif block_type == "carousel":
        items = block.get("items", [])
        parts = [lead_text, title, *[f"{it['title']}: {it.get('highlight', '')}" for it in items if isinstance(it, dict)], extended_text]
    elif block_type == "alert":
        parts = [title, block.get("message", ""), extended_text]
        return "\n".join(p for p in parts if p).strip()
    elif block_type == "stepper":
        items = block.get("items", [])
        step_details = "\n\n".join(it.get("detail", "") for it in items if isinstance(it, dict))
        return "\n\n".join(p for p in [block.get("contextText", ""), step_details, extended_text] if p)
    elif block_type == "checklist":
        return "\n".join(p for p in [block.get("listIntro", ""), *block.get("items", []), extended_text] if p)
    else:
        return "\n\n".join(p for p in [block.get("contextText", ""), block.get("text", "")] if p)
    return "\n".join(p for p in parts if p).strip()


# Every stored response block (see _generate_response_blocks's block-
# building loop) always carries the FULL union of every type's fields, so
# unrelated types can share dict-shape code -- a checklist block, for
# instance, also carries "contextText"/"text"/"progressTitle"/"stepDetails"/
# "currentStep"/"totalSteps", none of which a checklist ever actually uses.
# That's fine internally, but showing all of it to node_04c_response_reviser
# is pure noise it has no way to know is irrelevant -- and worse, handing it
# a shape that doesn't match its own required output shape (a separate,
# differently-named generation schema) is exactly what caused a live-
# observed miss: the model echoed the block's own field names back
# (listTitle/listIntro) instead of the output schema's names (title/
# context_text), and a title/intro edit would have been silently dropped by
# a mapper that only recognized the schema names. One canonical vocabulary,
# used identically for both what the reviser is shown and what it must
# return, removes the mismatch instead of reconciling two of them after the
# fact.
_BLOCK_EDITABLE_FIELDS = {
    "text": ["contextText", "text", "extendedText"],
    "checklist": ["listTitle", "listIntro", "items", "extendedText"],
    "stepper": ["progressTitle", "contextText", "items", "extendedText"],
    "comparison": ["contextText", "title", "headers", "rows", "actions", "extendedText"],
    "accordion": ["contextText", "title", "sections", "extendedText"],
    "timeline": ["contextText", "title", "subtitle", "steps", "extendedText"],
    "carousel": ["contextText", "title", "items", "extendedText"],
    "alert": ["title", "message", "severity", "action", "extendedText"],
}


def _simplify_block_for_revision(block: dict) -> dict:
    """Strip a stored block down to only the fields meaningful for its own
    type, for node_04c_response_reviser's "Blocks Under Revision" input.
    This same simplified shape doubles as the exact output shape the
    reviser is asked to return -- one vocabulary, not two -- so its output
    can be merged back with a plain dict update, no field-name translation
    layer to keep in sync or fall behind."""
    block_type = block.get("type", "text")
    fields = _BLOCK_EDITABLE_FIELDS.get(block_type, ["contextText", "text", "extendedText"])
    return {
        "block_id": block.get("blockId"),
        "type": block_type,
        **{field: block.get(field) for field in fields},
    }


def _personal_data_fallback_sentence(field_description: str, insert_token: str) -> str:
    """Customer-facing sentence for the completeness net's personal-data
    fallback. field_description (API_FIELD_REGISTRY) is written for the
    model, in the third person ("The customer's current account balance.")
    -- shown verbatim it read "The customer's current account balance:
    {{data-x}}." to the customer (confirmed live, BUG-017 follow-up). The
    registry's own "The customer's <field>" shape becomes "Your <field> is
    <token>."; any description not in that shape keeps the old colon form,
    which is grammatical whatever the description says (embedding an
    arbitrary sentence after "Your" once produced "Your The customer's...").
    """
    token = str(insert_token or "")
    if not token:
        return ""  # nothing to show; the caller skips an empty fallback
    description = str(field_description or "").strip().rstrip(".").strip()
    if not description:
        return token
    prefix = "the customer's "
    if description.lower().startswith(prefix):
        field = description[len(prefix):].strip()
        if field:
            return f"Your {field} is {token}."
    return f"{description}: {token}."


def _net_block_shell(obligation_id: str, in_handle_namespace: bool, block_type: str = "text") -> dict:
    """The common frame every net-inserted fallback block shares -- only
    the id goes in the citation field matching its own namespace
    (sourceTaskIds for a real task_id/subquery_id, sourceShortcutHandles
    for a nav_N/svc_N/data_N/fact_N/unavail_N handle), the other stays
    empty. Never both -- a net-inserted block is always about exactly one
    obligation.
    """
    return {
        "blockId": f"net_fallback_{obligation_id}",
        "sourceTaskIds": [obligation_id] if not in_handle_namespace else [],
        "sourceShortcutHandles": [obligation_id] if in_handle_namespace else [],
        "type": block_type, "contextText": "", "text": "", "items": [], "options": [],
        "progressTitle": "", "stepDetails": [], "currentStep": None, "totalSteps": None,
        "listTitle": "", "listIntro": "", "extendedText": "",
    }


def _apply_response_completeness_nets(
    response_blocks: list,
    state: "AgentState",
    evidence_by_task: list,
    task_metadata: dict,
    intent_clarifications: list,
    decline_context: list,
    no_info_context: list,
    conversational_context: list,
    available_shortcuts: list,
) -> list:
    """Deterministic omission net: one loop over the unified obligation
    list (docs/RESPONSE_OBLIGATION_CONTRACT_DESIGN.md Stage 2), replacing
    the four separate passes this function used to run (task-omission /
    detection-only clarification-decline-no-info-conversational /
    shortcut-omission / other-tool-result-omission). Every obligation now
    gets a real, deterministic repair when uncovered -- previously only
    RAG tasks and shortcuts/tool-results did; a dropped clarification,
    decline, no-info, or conversational entry was only logged, never
    repaired, which is exactly the gap traced live (a clarification
    silently dropped by the model, missed by the audit, and never asked at
    all -- see docs/RESPONSE_OBLIGATION_CONTRACT_ADDENDUM_CLARIFICATION_
    AUTHORING.md and the BUG tracker).

    Signature is unchanged from before this rewrite -- every call site
    (_generate_response_blocks, node_04c_response_reviser,
    node_11_deterministic_clarification_fallback) needs no change. This
    function fetches the three typed lists it didn't already receive as
    parameters (unavailable_service_requests, verified_api_context,
    personal_data_placeholders) directly from state, exactly as the
    previous four-pass version did, then builds one unified obligation
    list internally via _build_response_obligations.

    Mutates and returns `response_blocks` (appends fallback blocks; never
    removes or edits an existing one), so it can be re-run against the
    FULL current block set on every node_04c retry round -- necessary
    because a revision to one pending block could still remove text an
    unrelated check depends on, or because the pending set for this round
    simply didn't include an obligation whose block the reviser (correctly)
    left untouched but which a PRIOR round had already dropped.
    """
    unavailable_service_requests = _unavailable_service_requests(state)
    verified_api_context = _verified_api_context(state)
    personal_data_placeholders = _personal_data_placeholders(state)

    obligations = _build_response_obligations(
        evidence_by_task, verified_api_context, decline_context, no_info_context,
        conversational_context, intent_clarifications, available_shortcuts,
        unavailable_service_requests, personal_data_placeholders, task_metadata,
    )
    # The same id/citation-field partition _generate_response_blocks'
    # parsing already uses -- a handle-shaped id (nav_N/svc_N/data_N/
    # fact_N/unavail_N) is checked via sourceShortcutHandles; every other
    # id (a real task_id or subquery_id) via sourceTaskIds. This is
    # independent of content_mode/generation_rule: a verified-fact entry
    # is EVIDENCE RULES (compose) but still handle-namespace, exactly as
    # it already was before this rewrite.
    handle_ids = {
        entry["handle"]
        for entry in (*available_shortcuts, *unavailable_service_requests, *personal_data_placeholders, *verified_api_context)
    }

    # RAG-task-only special case: a subquery fully RESOLVED via a
    # different tool attempt this round means its task_id going uncited
    # is not an omission -- see the original comment this preserves.
    subquery_id_by_task_id = {
        task.get("task_id"): task.get("subquery_id")
        for task in state.get("search_tasks", [])
        if task.get("task_id")
    }
    resolution_status_by_subquery_id = {
        subquery.get("subquery_id"): subquery.get("resolution_status") or _compute_subquery_status(subquery)
        for subquery in state.get("financial_subqueries", state.get("triaged_subqueries", []))
        if isinstance(subquery, dict)
    }
    # Shortcut placement (subquery-anchored insert position) and
    # clarification originator (which deterministic template applies) both
    # need one more lookup each, same as before this rewrite.
    shortcut_subquery_by_handle = {
        f"{'nav' if entry['kind'] == 'navigation' else 'svc'}_{position}": subquery_id
        for position, (entry, _key, subquery_id) in enumerate(_iter_shortcut_entries(state), start=1)
    }
    clarification_task_by_subquery_id = {
        task.get("subquery_id"): task for task in state.get("clarification_tasks", []) if isinstance(task, dict)
    }

    def _cited(block: dict, obligation_id: str, in_handle_namespace: bool) -> bool:
        field = "sourceShortcutHandles" if in_handle_namespace else "sourceTaskIds"
        return obligation_id in (block.get(field) or [])

    def _is_covered(obligation_id: str, in_handle_namespace: bool, required_text: str = "") -> bool:
        return any(
            _cited(block, obligation_id, in_handle_namespace)
            and (not required_text or required_text in _flatten_response_block_text(block))
            for block in response_blocks
        )

    def _insert_at_end(block: dict) -> None:
        response_blocks.append(block)

    def _insert_near_subquery(block: dict, handle: str) -> None:
        # Sit right after the last block answering the same subquery, so
        # the card that follows lands next to what it relates to;
        # otherwise the end -- unchanged from the shortcut-only version.
        related_task_ids = {t for t, sq in subquery_id_by_task_id.items() if sq and sq == shortcut_subquery_by_handle.get(handle)}
        insert_at = len(response_blocks)
        for position, existing in enumerate(response_blocks):
            if related_task_ids & set(existing.get("sourceTaskIds", [])):
                insert_at = position + 1
        response_blocks.insert(insert_at, block)

    for obligation in obligations:
        oid = obligation.get("id")
        if not oid:
            continue
        rule = obligation.get("generation_rule", "")
        content = obligation.get("generation_content", {})
        in_handle_namespace = oid in handle_ids

        if rule == "EVIDENCE RULES" and not in_handle_namespace:
            # A real RAG task. Skip entirely if its subquery was fully
            # RESOLVED via a different tool attempt this round.
            if resolution_status_by_subquery_id.get(subquery_id_by_task_id.get(oid)) == "RESOLVED":
                continue
            if _is_covered(oid, in_handle_namespace=False):
                continue
            component = str(content.get("resolved_request", "")).strip()
            fallback_text = (
                f"Sorry, I don't have a complete answer for the part about {component} right now."
                if component else "Sorry, I don't have a complete answer for one part of your request right now."
            )
            print(f"   ⚠️ [TASK OMITTED IN DRAFT]: {oid} missing from generated elements — inserting fallback")
            log_event("response.flexible.task_omitted", task_id=oid, component=component)
            block = _net_block_shell(oid, False)
            block["text"] = fallback_text
            _insert_at_end(block)

        elif rule == "EVIDENCE RULES":
            # A verified customer-specific fact (handle-namespace, citation
            # only -- unchanged strictness from before this rewrite).
            if _is_covered(oid, in_handle_namespace=True):
                continue
            fallback_text = str(content.get("fact", "")).strip()
            if not fallback_text:
                continue
            print(f"   ⚠️ [TOOL RESULT OMITTED IN DRAFT]: {oid} (verified_fact) not covered by a citing block — inserting deterministic fallback")
            log_event("response.flexible.tool_result_omitted", handle=oid, kind="verified_fact")
            block = _net_block_shell(oid, True)
            block["text"] = fallback_text
            _insert_at_end(block)

        elif rule == "CONVERSATIONAL RULES":
            if _is_covered(oid, in_handle_namespace=False):
                continue
            # Never the customer's own customer_component text -- that is
            # what THEY said, not something the bot can relay as its own
            # reply. A short, fixed, always-safe acknowledgment instead;
            # missing a warm reply to a non-financial aside is a minor
            # style gap, never a claim that needs to be specific.
            print(f"   ℹ️ [CONVERSATIONAL OMITTED IN DRAFT]: {oid} not covered by a citing block — inserting deterministic fallback")
            log_event("response.flexible.entry_omitted", task_id=oid, kind="conversational", fallback_inserted=True)
            block = _net_block_shell(oid, False)
            block["text"] = "Thank you for letting us know."
            _insert_at_end(block)

        elif rule == "DECLINE/NO-INFO RULES":
            if _is_covered(oid, in_handle_namespace=False):
                continue
            kind_label = "decline_evidence" if "decline_evidence" in content else "no_info_evidence"
            fallback_text = str(content.get("decline_evidence") or content.get("no_info_evidence") or "").strip()
            if not fallback_text:
                continue
            print(f"   ℹ️ [{kind_label.upper()} OMITTED IN DRAFT]: {oid} not covered by a citing block — inserting deterministic fallback")
            log_event("response.flexible.entry_omitted", task_id=oid, kind=kind_label, fallback_inserted=True)
            block = _net_block_shell(oid, False)
            block["text"] = fallback_text
            _insert_at_end(block)

        elif rule == "CLARIFICATION RULES":
            if _is_covered(oid, in_handle_namespace=False):
                continue
            source_task = clarification_task_by_subquery_id.get(oid, {})
            fallback_text = _clarification_fallback_text({
                "unresolved_request": content.get("unresolved_request", ""),
                "originator": source_task.get("originator", ""),
            })
            if not fallback_text:
                continue
            print(f"   ℹ️ [CLARIFICATION OMITTED IN DRAFT]: {oid} not covered by a citing block — inserting deterministic clarification")
            log_event("response.flexible.entry_omitted", task_id=oid, kind="clarification_evidence", fallback_inserted=True)
            block = _net_block_shell(oid, False, block_type="clarification")
            block["text"] = fallback_text
            block["options"] = [
                {"label": g["label"], "message": g["message"]}
                for g in content.get("clarification_guesses", []) or []
                if isinstance(g, dict) and g.get("label") and g.get("message")
            ]
            _insert_at_end(block)

        elif rule == "SHORTCUT RULES":
            label = str(content.get("label", "")).strip()
            if not label or _is_covered(oid, in_handle_namespace=True, required_text=label):
                continue
            fallback_text = (
                f'You can also fill in and submit the "{label}" form below.'
                if content.get("kind") == "form"
                else f'You can also use the "{label}" button below.'
            )
            print(f"   ⚠️ [SHORTCUT OMITTED IN DRAFT]: '{label}' ({oid}) not covered by a citing block — inserting deterministic fallback")
            log_event("response.flexible.shortcut_omitted", label=label, kind=content.get("kind"), handle=oid)
            block = _net_block_shell(oid, True)
            block["text"] = fallback_text
            _insert_near_subquery(block, oid)

        elif rule == "UNAVAILABLE SERVICE REQUEST RULES":
            if _is_covered(oid, in_handle_namespace=True):
                continue
            fallback_text = str(content.get("reason", "")).strip()
            if not fallback_text:
                continue
            print(f"   ⚠️ [TOOL RESULT OMITTED IN DRAFT]: {oid} (unavailable_service_request) not covered by a citing block — inserting deterministic fallback")
            log_event("response.flexible.tool_result_omitted", handle=oid, kind="unavailable_service_request")
            block = _net_block_shell(oid, True)
            block["text"] = fallback_text
            _insert_at_end(block)

        elif rule == "PERSONAL DATA RULES":
            insert_token = content.get("insert_token", "")
            if _is_covered(oid, in_handle_namespace=True, required_text=insert_token):
                continue
            fallback_text = _personal_data_fallback_sentence(content.get("field_description", ""), insert_token)
            if not fallback_text:
                continue
            print(f"   ⚠️ [TOOL RESULT OMITTED IN DRAFT]: {oid} (personal_data) not covered by a citing block — inserting deterministic fallback")
            log_event("response.flexible.tool_result_omitted", handle=oid, kind="personal_data")
            block = _net_block_shell(oid, True)
            block["text"] = fallback_text
            _insert_at_end(block)

    return response_blocks


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
    # Despite the variable name (kept for minimal diff churn across this
    # function), this now includes every clarification regardless of which
    # node raised it -- one standard contract keyed by subquery_id, see
    # _build_clarification_context. Every downstream use here (the prompt
    # section, the deterministic completeness net, clarification_guesses
    # chip attachment) already treats every entry uniformly regardless of
    # originator, so nothing else needed to change.
    intent_clarifications = _build_clarification_context(state)
    # A task whose retries are exhausted with zero usable evidence
    # (CLARIFICATION_REQUIRED) gets the same deterministic-evidence
    # treatment as decline/clarification -- there is nothing left to
    # synthesize, only a fixed "not found" statement to relay. A task with
    # SOME retained partial evidence (RELEVANT) is different: it is one
    # evidence obligation whose own resolution_status says it is partial,
    # and nothing else.
    no_info_context = _no_info_entries(
        state.get("task_resolution_instructions", []), task_metadata, state.get("unresolvable_search_tasks", [])
    )
    decline_context = _build_decline_context(state)
    conversational_context = _build_conversational_context(state)
    # Every id the model is allowed to cite in an element's
    # source_task_ids: real RAG evidence tasks, a clarification's own
    # subquery_id, plus every decline/no-info/conversational entry's own
    # synthetic task_id. Omitting these used to silently strip sourceTaskIds
    # down to [] for any element addressing them (they were never real
    # evidence tasks), which broke clarification-guess chip attachment below
    # whenever a clarification/decline/no-info entry shared a turn with
    # real evidence.
    valid_task_ids = (
        {entry.get("task_id") for entry in evidence_by_task if entry.get("task_id")}
        | {c.get("subquery_id") for c in intent_clarifications if c.get("subquery_id")}
        | {d.get("task_id") for d in decline_context if d.get("task_id")}
        | {n.get("task_id") for n in no_info_context if n.get("task_id")}
        | {v.get("subquery_id") for v in conversational_context if v.get("subquery_id")}
    )
    # The LLM never sees a special_id, service_request_id, destination_id,
    # route, or form_schema -- only a sanitized label/topic per shortcut plus
    # a short per-turn handle (nav_1, svc_1), via _available_response_shortcuts.
    # Citing the real opaque ids failed repeatedly (wrong key, dropped
    # citation, invented key); a short positional handle in the same
    # source_task_ids field as a RAG task is a different, much simpler thing
    # to copy, and it makes the shortcut a tracked obligation like a task.
    # The button itself is still attached by node_07 straight from
    # tool_attempts, unconditionally, so nothing about *whether* a shortcut
    # appears depends on this text; the handle only drives the coverage net
    # (and, later, where the card is placed).
    available_shortcuts = _available_response_shortcuts(state)
    unavailable_service_requests = _unavailable_service_requests(state)
    personal_data_placeholders = _personal_data_placeholders(state)
    verified_api_context = _verified_api_context(state)
    shortcut_handles = {
        entry["handle"]
        for entry in (
            *available_shortcuts, *unavailable_service_requests,
            *personal_data_placeholders, *verified_api_context,
        )
    }
    # Unified Response Obligation Contract (docs/RESPONSE_OBLIGATION_CONTRACT_
    # DESIGN.md, Stage 1): one builder call, one shared id/mode/guidance
    # source of truth. Each named section below still shows its own filtered
    # subset -- by looking up each already-computed typed entry's own id in
    # the single result -- so the prompt's per-type behavioral rules
    # (unchanged, deferred to a later stage) still sit next to the data they
    # describe; only the wrapping shape and field paths change.
    obligations = _build_response_obligations(
        evidence_by_task, verified_api_context, decline_context, no_info_context,
        conversational_context, intent_clarifications, available_shortcuts,
        unavailable_service_requests, personal_data_placeholders, task_metadata,
    )

    # A turn with no substantive content beyond a pending clarification/
    # decline/no-info/conversational reply gets a narrower, more constrained
    # sub-prompt instead of this open-ended one (see
    # _generate_evidence_only_response) -- the same reliability reasoning as
    # everywhere else in this module: an LLM given full discretion over what
    # to write, with nothing else pulling it toward the harder correct
    # answer, doesn't reliably choose it.
    #
    # BUG-017: "substantive content" is derived from the obligation list
    # itself -- any obligation whose rule is not one the narrow sub-prompt
    # handles -- rather than from a hand-maintained list of typed inputs.
    # That hand list had omitted personal_data_placeholders, so "Thanks,
    # what's my balance?" (a fetched balance plus a conversational part) was
    # judged to have nothing substantive and sent to the narrow sub-prompt,
    # which never receives the balance token -- the reply was just "You're
    # very welcome!". Deriving it here means a future obligation type is
    # counted automatically instead of silently forgotten the same way.
    has_real_evidence = any(
        obligation.get("generation_rule") not in _EVIDENCE_ONLY_GENERATION_RULES
        for obligation in obligations
    )
    if not has_real_evidence and (intent_clarifications or decline_context or no_info_context or conversational_context):
        return _generate_evidence_only_response(intent_clarifications, decline_context, no_info_context, conversational_context, state)
    conversation_context = _build_conversation_context(state)
    user_baseline = _build_user_baseline_context(state)
    audit_feedback = state.get("draft_feedback_history", [])[-4:]
    user_profile = state.get("user_profile", {})

    obligations_by_id = {o["id"]: o for o in obligations if o.get("id")}
    evidence_obligations = [obligations_by_id[e["task_id"]] for e in evidence_by_task if e.get("task_id") in obligations_by_id]
    verified_fact_obligations = [obligations_by_id[e["handle"]] for e in verified_api_context if e.get("handle") in obligations_by_id]
    conversational_obligations = [obligations_by_id[e["subquery_id"]] for e in conversational_context if e.get("subquery_id") in obligations_by_id]
    decline_obligations = [obligations_by_id[e["task_id"]] for e in decline_context if e.get("task_id") in obligations_by_id]
    no_info_obligations = [obligations_by_id[e["task_id"]] for e in no_info_context if e.get("task_id") in obligations_by_id]
    clarification_obligations = [obligations_by_id[c["subquery_id"]] for c in intent_clarifications if c.get("subquery_id") in obligations_by_id]
    shortcut_obligations = [obligations_by_id[e["handle"]] for e in available_shortcuts if e.get("handle") in obligations_by_id]
    unavailable_obligations = [obligations_by_id[e["handle"]] for e in unavailable_service_requests if e.get("handle") in obligations_by_id]
    personal_data_obligations = [obligations_by_id[e["handle"]] for e in personal_data_placeholders if e.get("handle") in obligations_by_id]

    obligation_sections = []
    if conversational_obligations:
        obligation_sections.append(
            "Conversational inputs (governed by CONVERSATIONAL RULES below):\n"
            + json.dumps(conversational_obligations, ensure_ascii=False)
        )
    if evidence_obligations:
        obligation_sections.append(
            "Approved evidence by retrieval task (governed by EVIDENCE RULES below):\n"
            + json.dumps(evidence_obligations, ensure_ascii=False)
        )
    if clarification_obligations:
        obligation_sections.append(
            "Clarification inputs (governed by CLARIFICATION RULES below):\n"
            + json.dumps(clarification_obligations, ensure_ascii=False)
        )
    if no_info_obligations:
        obligation_sections.append(
            "No-info inputs (governed by DECLINE/NO-INFO RULES below):\n"
            + json.dumps(no_info_obligations, ensure_ascii=False)
        )
    if decline_obligations:
        obligation_sections.append(
            "Decline inputs (governed by DECLINE/NO-INFO RULES below):\n"
            + json.dumps(decline_obligations, ensure_ascii=False)
        )
    if shortcut_obligations:
        obligation_sections.append(
            "Available shortcuts this turn (governed by SHORTCUT RULES below):\n"
            + json.dumps(shortcut_obligations, ensure_ascii=False)
        )
    if unavailable_obligations:
        obligation_sections.append(
            "Unavailable service requests (governed by UNAVAILABLE SERVICE "
            "REQUEST RULES below):\n" + json.dumps(unavailable_obligations, ensure_ascii=False)
        )
    if verified_fact_obligations:
        obligation_sections.append(
            "Verified customer-specific facts (governed by EVIDENCE RULES "
            "below, same footing as approved evidence):\n"
            + json.dumps(verified_fact_obligations, ensure_ascii=False)
        )
    if personal_data_obligations:
        obligation_sections.append(
            "Personal data references (governed by PERSONAL DATA RULES below):\n"
            + json.dumps(personal_data_obligations, ensure_ascii=False)
        )
    obligations_block = "\n".join(obligation_sections) if obligation_sections else "None this turn."

    # Every named rule section is included only when this turn actually has
    # an obligation it governs -- CLARIFICATION RULES never appears on a
    # turn with no clarification, and so on for every other type. Content
    # is never trimmed; whole sections are only present or absent.
    rule_sections = []
    if evidence_obligations or verified_fact_obligations:
        rule_sections.append(_EVIDENCE_RULES_TEXT)
    if conversational_obligations:
        rule_sections.append(_CONVERSATIONAL_RULES_TEXT)
    if clarification_obligations:
        rule_sections.append(_CLARIFICATION_RULES_TEXT)
    if decline_obligations or no_info_obligations:
        rule_sections.append(_DECLINE_NO_INFO_RULES_TEXT)
    if shortcut_obligations:
        rule_sections.append(_SHORTCUT_RULES_TEXT)
    if unavailable_obligations:
        rule_sections.append(_UNAVAILABLE_SERVICE_REQUEST_RULES_TEXT)
    if personal_data_obligations:
        rule_sections.append(_PERSONAL_DATA_RULES_TEXT)
    rules_block = (
        "\n\n".join(rule_sections) if rule_sections
        else "No type-specific rule section applies this turn (no shortcut, "
             "clarification, decline, no-info, unavailable-request, or "
             "personal-data obligation exists)."
    )

    prompt = f"""
    FLEXIBLE RESPONSE GENERATION
    You are Horizon, a helpful, natural, read-only Horizon Bank customer
    service representative. Write one coherent conversational response by
    addressing every obligation in OBLIGATIONS below: for each one, follow
    its own generation_guidance, using only its generation_content as
    material, and consult the named section its generation_rule points to
    (further below) for the full rules governing it -- this applies
    identically whether that means composing an answer, relaying a fixed
    statement, asking a clarifying question, or mentioning a shortcut,
    fact, or token; no kind of obligation is the default job and no kind is
    secondary to another. Do not perform banking actions, imply account
    access, or invent facts, procedures, products, or policies.

    {_style_personalization_block(user_profile)}

    {_GENERATION_CONDUCT_GUARDRAILS}

    {_GENERATION_BRIDGE_GUARDRAILS_UNIFIED}

    {PRECISION_REFERENCE_CONTRACT}

    {PERSONAL_DATA_REFERENCE_CONTRACT}

    GENERAL DESIGN PRINCIPLES (apply to every obligation, regardless of kind):
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
    - Act as a bank customer-service representative who explains the
      relevant Horizon information that is actually available. When the
      evidence covers only one conditional policy or process, you may
      restate that scope and explain it as relevant reference even if it
      does not directly answer the customer's broader question. Do not
      fill the remaining gap with a yes/no conclusion, judgement, comment,
      recommendation, warning, prediction, or implication about what the
      customer should do. Leave that decision to the customer unless
      approved evidence itself establishes the conclusion.
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
      clearly-scoped optional context (per the Applicability rule above),
      briefly (see RESPONSE LENGTH below), instead of leaving the customer
      with nothing.
    - {_RESPONSE_PROPORTIONALITY_RULE}
    - Respond as one considered conversation, not as a retrieval report or a
      sequence of task answers. Search tasks are internal evidence units, not
      required visible sections.
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
    - Account for every entry in every OBLIGATIONS list below; never
      silently drop an entire customer need when correcting a different one.
      Apply the applicability rule first: do not render a procedure, action,
      or UI merely to satisfy coverage when its evidence is conditional or
      does not fit the customer's stated situation. When useful, provide a
      short, accurately scoped reference in text instead. Do not name task IDs
      or expose internal organization to the customer.
    - When one element's answer or extended_text draws on more than one
      obligation -- a natural way to combine related facts -- list every one
      of their ids in that element's source_task_ids, not just the one the
      element is primarily about. This is not cosmetic: an id left out of
      source_task_ids is treated as never addressed, and a contradictory
      duplicate (a fallback apology) gets appended right beside content that
      already covered it. Crediting every id a passage actually draws on is
      what prevents that. source_task_ids holds every id an element
      addresses, mixed freely: a task's own id, a clarification's/decline's/
      no-info's id, and a shortcut's/tool-result's own id all sit in the
      exact same array, in any combination -- e.g. an element citing only a
      shortcut looks like "source_task_ids":["nav_1"], and one blending a
      task's evidence with a shortcut it substitutes for looks like
      "source_task_ids":["task_1","svc_1"]. Never leave this array empty for
      an element that addresses an entry from any OBLIGATIONS list below.
    - Every factual claim, instruction, item, title, option, and detail must
      be supported by approved evidence. A response element may use evidence
      from multiple tasks only when those facts are naturally related --
      arriving in the same customer message is not relatedness. A lost card
      and an address change are two separate needs; each deserves its own
      element with whatever structure best fits it (a sequential procedure
      as PROGRESS, a scannable set as LIST, otherwise TEXT), not one shared
      paragraph merely because both were mentioned together. This applies
      just as much to a shortcut used as a subquery's primary answer (see
      SHORTCUT RULES below, when present) as it does to two RAG-answered
      tasks: a shortcut whose `for_request` matches one subquery must never
      be folded into a different, unrelated task's element merely because a
      prior redraft was flagged for omitting it. "Resubmit KYC Document"
      (answering an unrelated KYC-update request) appended onto a
      credit-card-application paragraph reads as though KYC resubmission
      were a step of applying for a card, which is false and confusing --
      it needs its own element. This still applies when redrafting after
      audit feedback: removing one flagged sentence is never a reason to
      keep an earlier draft's structure unexamined -- re-decide the best
      presentation for each need fresh, the same as if writing the first
      draft; when fixing an omitted entry flagged by audit feedback, add
      its own separate element, do not attach it to whichever element
      happened to already exist.
    - If a Conversational Input gives the customer's reason for a banking
      request but the approved evidence does not address whether that
      reason qualifies, do not convert the gap into a policy conclusion.
      Acknowledge the reason and state only the verified guidance that is
      available.
    - If evidence gives a useful policy boundary or limit, state that fact
      plainly; do not invent a broader conclusion beyond what it entails.
    - Do not mention FAQ documents, sources, parent IDs, search, prompts, or
      internal systems. Preserve every `{{{{faq_*}}}}` and `{{{{data-*}}}}` token
      exactly as supplied, with both braces on each side.
    - Leave `clarification` an empty string. Every clarifying question is a
      Clarification Input, rendered as its own element per the
      account-for-every-entry rule above; the part of a RELEVANT entry's
      request its evidence doesn't cover gets a neutral availability notice
      per EVIDENCE RULES, never a question.
    - Correct every failed-audit issue by removing or narrowing only the
      flagged unsupported content. Audit feedback is never a source of facts.
    - If Failed outbound-audit feedback contains
      `NARROW_TO_EVIDENCE_SCOPE`, treat it as mandatory for this rewrite:
      follow that violation's own generation_guidance (Generation Guidance
      Shape Rule case 2) -- explicitly admit the specific broader element
      the evidence does not confirm, phrased around the customer's own
      question, THEN separately state what the evidence does confirm.
      Never merge the two into one sentence that lets the confirmed part's
      certainty bleed into the unconfirmed one, and never present the
      confirmed remainder as if it resolves the customer's broader,
      unconfirmed question. The confirmed remainder may keep whatever UI
      element type its own content naturally calls for -- a genuinely
      evidence-backed procedure may still render as a stepper; collapsing
      it to plain text is not required merely because this strategy fired.
      Do not draw an exclusivity conclusion, add an unsupported step,
      recommend an action, or ask a further question beyond the admission.
    - If feedback contains `REMOVE_UNSUPPORTED_CONTENT`, preserve only the
      independently supported remainder. If it contains
      `CLARIFY_FROM_ALLOWED_CONTEXT`, ask only the permitted clarification.

    UI ELEMENT TYPE SELECTION (apply in this priority order BEFORE
    falling back to TEXT -- at least one element MUST use a non-TEXT type
    when any trigger below matches):
    * COMPARISON (MANDATORY when the customer asks to compare, contrast,
      or choose between 2+ products, plans, rates, card types, or account
      features): structure the approved evidence into "headers" (product
      names) and "rows" (feature/value pairs). Never flatten a comparison
      into prose TEXT.
    * ACCORDION (PREFERRED when your response covers 2+ distinct policy
      sections, rule categories, eligibility criteria, or FAQ topics --
      each with its own heading and substantive detail): use "sections"
      with title/content pairs. Collapsing multi-section answers into
      one TEXT paragraph loses scannability.
    * TIMELINE (MANDATORY when showing application progress, delivery
      tracking, request status, or any milestone sequence with
      past/current/future stages): use "steps" with status fields.
    * CAROUSEL (PREFERRED when listing 2+ product options, card offers,
      or promotions that each have a title, highlight, and optional
      action button): use "items" with title/subtitle/details.
    * ALERT (MANDATORY for security warnings, scam notices, fraud alerts,
      compliance cautions, or urgent system notices): set "severity" and
      "message".
    * PROGRESS (use for sequential multi-step how-to procedures with 2+
      steps): use "progress_steps" with title/detail pairs.
    * LIST (use for independent, scannable items, features, or
      requirements that are NOT a comparison or FAQ sections).
    * TEXT (use ONLY when none of the above types match the content --
      short single-topic explanations, advice, or greetings).

    NEXT ACTION SUGGESTIONS (optional, 0-3): if the Approved evidence
    given to you for a task contains a distinct sub-topic, fact, or
    section you did NOT feature in your response text, you may suggest
    it as something the customer could naturally ask about next -- e.g.
    if the approved evidence covers both application steps and
    eligibility criteria but your response only used the application
    steps, suggest "What are the eligibility requirements?". Each
    suggestion MUST trace back to a specific piece of Approved evidence
    you were already given this turn -- never invent a topic beyond it,
    and never suggest something you have no evidence for; this is not a
    guess about what the customer might want, it is a pointer to real
    content already sitting in front of you that you chose not to use.
    This is optional: leave `next_actions` empty when everything
    materially relevant in your approved evidence was already covered,
    or there is no genuine unfeatured content to point to -- do not
    invent a suggestion merely to fill the array. Phrase each as a
    short, natural question or request in the customer's own voice, the
    way they would actually type it (never a bare topic label) -- e.g.
    "What are the eligibility requirements for this loan?" not
    "Eligibility".

    Output JSON only:
    {{"elements":[{{"type":"TEXT|LIST|PROGRESS|COMPARISON|ACCORDION|TIMELINE|CAROUSEL|ALERT","source_task_ids":["task_1","nav_1"],"context_text":"optional natural connector","answer":"grounded prose","title":"optional component title","list_title":"only for LIST","list_items":["only for LIST"],"progress_title":"only for PROGRESS","progress_steps":[{{"title":"...","detail":"..."}}],"headers":["for COMPARISON"],"rows":[{{"feature":"...","values":["..."]}}],"actions":[{{"label":"...","action":"..."}}],"sections":[{{"title":"...","content":"..."}}],"subtitle":"for TIMELINE","steps":[{{"title":"...","description":"...","status":"completed|active|pending","date":"..."}}],"items":[{{"title":"...","subtitle":"...","badge":"...","highlight":"...","details":["..."],"actionLabel":"...","actionMessage":"..."}}],"severity":"warning|security|info|success","message":"for ALERT","action":{{"label":"...","action":"..."}},"extended_text":"optional additional grounded prose","options":[{{"label":"short action","message":"complete grounded question"}}]}}],"clarification":"","next_actions":["short natural next question, grounded in unfeatured approved evidence"]}}

    {_DATA_FOLLOWS}

    {rules_block}

    Customer request: {state.get("user_input", "")}
    Trusted conversation context (use only to understand the customer's current
    meaning, priority, references, and conversational tone; never as factual
    banking evidence):
    {conversation_context}
    User baseline (advisory communication context only; use only when clearly
    relevant to the current request and never as factual banking evidence):
    {user_baseline}
    Global resolution context (internal coverage and unresolved-topic anchors
    only; not evidence, response wording, or UI guidance):
    {json.dumps(resolution_context, ensure_ascii=False)}

    OBLIGATIONS -- everything you must address this turn. Every entry below,
    of every kind, carries the same three fields: "id" (cite it, and only
    it, in that element's own source_task_ids -- never write it inside
    customer-facing text), "generation_content" (what you know), and
    "generation_guidance" (a one-line instruction that names the exact rule
    section governing this entry -- named sections appear above, just
    before this data, only for the kinds actually present this turn). Do
    not skip any entry, and do not address the same entry more than once.

    {obligations_block}

    Failed outbound-audit feedback (constraints only, never evidence). Each
    entry identifies one specific claim or instruction to remove -- it is
    never permission to abandon the topic it came from, and it is never
    permission to narrow your job to only the answer. Remove exactly what
    is flagged, then follow every rule above exactly as you would on a
    first attempt, in full -- every entry above still needs its own element
    citing its own top-level `id`, evidence still gets composed the same
    way, and nothing here is more important than anything else you were
    already asked to do:
    {json.dumps(audit_feedback, ensure_ascii=False)}
    """
    try:
        generated = _parse_json_robustly(call_groq_lpu(prompt))
    except Exception as error:
        print(f"🚨 [FLEXIBLE RESPONSE GENERATOR ERROR]: {error}")
        generated = {}

    response_blocks = []
    answer_parts = []
    element_sources = []
    unmatched_by_block = {}
    for index, element in enumerate(generated.get("elements", []), start=1):
        if not isinstance(element, dict):
            continue
        requested_type = str(element.get("type", "TEXT")).upper()
        cited_ids = _llm_list(element, "source_task_ids")
        source_ids = [task_id for task_id in cited_ids if task_id in valid_task_ids]
        source_ids = list(dict.fromkeys(source_ids))
        source_handles = list(dict.fromkeys(h for h in cited_ids if h in shortcut_handles))
        # Cited ids that were not obligations the generator received (e.g.
        # an id it picked up from elsewhere in the prompt). Kept on the
        # block, never silently dropped: the deterministic checks flag the
        # block, and a violation naming one of them still finds this block
        # (BUG-038 -- the dropped id is what let a flagged block be
        # approved).
        unmatched_by_block[f"element_{index}"] = list(dict.fromkeys(
            i for i in cited_ids if i and i not in valid_task_ids and i not in shortcut_handles
        ))
        context_text = _llm_text(element, "context_text")
        answer = _llm_text(element, "answer")
        extended_text = _llm_text(element, "extended_text")
        title = _llm_text(element, "title")
        options = _normalize_ui_options(element.get("options", []))
        list_items = [item.strip() for item in _llm_list(element, "list_items") if isinstance(item, str) and item.strip()][:8]
        progress_steps = [
            {"title": _llm_text(step, "title"), "detail": _llm_text(step, "detail")}
            for step in _llm_list(element, "progress_steps") if isinstance(step, dict)
            and _llm_text(step, "title") and _llm_text(step, "detail")
        ]

        if requested_type == "COMPARISON":
            # headers/values are lists of bare strings, not dicts -- unlike
            # every other _llm_text call here, which extracts a named field
            # from a dict element. _llm_text(h)/_llm_text(v) crashed with
            # "missing 1 required positional argument: 'key'" the moment a
            # real COMPARISON response actually exercised this path.
            headers = [str(h).strip() for h in _llm_list(element, "headers") if isinstance(h, str) and str(h).strip()]
            rows = []
            for r in _llm_list(element, "rows"):
                if isinstance(r, dict):
                    feat = _llm_text(r, "feature") or _llm_text(r, "label")
                    vals = [str(v).strip() for v in _llm_list(r, "values") if isinstance(v, str) and str(v).strip()]
                    if feat and vals:
                        rows.append({"feature": feat, "values": vals})
            actions = [
                {"label": _llm_text(a, "label"), "message": _llm_text(a, "message") or _llm_text(a, "action")}
                for a in _llm_list(element, "actions") if isinstance(a, dict) and _llm_text(a, "label")
            ]
            if rows:
                lead_text = "\n\n".join(part for part in [context_text, answer] if part)
                rendered_text = "\n".join([lead_text, title, *[f"{r['feature']}: {', '.join(r['values'])}" for r in rows], extended_text]).strip()
                block = {
                    "blockId": f"element_{index}", "sourceTaskIds": source_ids, "sourceShortcutHandles": source_handles,
                    "type": "comparison", "contextText": lead_text, "title": title, "headers": headers, "rows": rows, "actions": actions,
                    "options": options, "extendedText": extended_text,
                }
                response_blocks.append(block)
                answer_parts.append(rendered_text)
                element_sources.append(source_ids)
                continue

        elif requested_type == "ACCORDION":
            sections = []
            for s in (_llm_list(element, "sections") or _llm_list(element, "items")):
                if isinstance(s, dict):
                    st = _llm_text(s, "title") or _llm_text(s, "heading")
                    sc = _llm_text(s, "content") or _llm_text(s, "detail") or _llm_text(s, "text")
                    if st and sc:
                        sections.append({"title": st, "content": sc})
            if sections:
                lead_text = "\n\n".join(part for part in [context_text, answer] if part)
                rendered_text = "\n".join([lead_text, title, *[f"{s['title']}: {s['content']}" for s in sections], extended_text]).strip()
                block = {
                    "blockId": f"element_{index}", "sourceTaskIds": source_ids, "sourceShortcutHandles": source_handles,
                    "type": "accordion", "contextText": lead_text, "title": title, "sections": sections,
                    "options": options, "extendedText": extended_text,
                }
                response_blocks.append(block)
                answer_parts.append(rendered_text)
                element_sources.append(source_ids)
                continue

        elif requested_type == "TIMELINE":
            steps = []
            for st in (_llm_list(element, "steps") or _llm_list(element, "items")):
                if isinstance(st, dict):
                    st_title = _llm_text(st, "title")
                    st_desc = _llm_text(st, "description") or _llm_text(st, "detail")
                    st_status = str(st.get("status", "pending")).lower()
                    st_date = _llm_text(st, "date")
                    if st_title:
                        steps.append({"title": st_title, "description": st_desc, "status": st_status, "date": st_date})
            if steps:
                subtitle = _llm_text(element, "subtitle")
                lead_text = "\n\n".join(part for part in [context_text, answer] if part)
                rendered_text = "\n".join([lead_text, title, subtitle, *[f"{st['title']}: {st['description']}" for st in steps], extended_text]).strip()
                block = {
                    "blockId": f"element_{index}", "sourceTaskIds": source_ids, "sourceShortcutHandles": source_handles,
                    "type": "timeline", "contextText": lead_text, "title": title, "subtitle": subtitle, "steps": steps,
                    "options": options, "extendedText": extended_text,
                }
                response_blocks.append(block)
                answer_parts.append(rendered_text)
                element_sources.append(source_ids)
                continue

        elif requested_type == "CAROUSEL":
            c_items = []
            for it in _llm_list(element, "items"):
                if isinstance(it, dict):
                    it_title = _llm_text(it, "title")
                    it_sub = _llm_text(it, "subtitle")
                    it_badge = _llm_text(it, "badge")
                    it_hi = _llm_text(it, "highlight")
                    # "details" is a list of bare strings, not dicts -- same
                    # missing-'key'-argument mistake as the COMPARISON block's
                    # headers/values (BUG-012); fixed the same way here.
                    it_dets = [str(d).strip() for d in _llm_list(it, "details") if isinstance(d, str) and str(d).strip()]
                    it_act_lbl = _llm_text(it, "actionLabel") or _llm_text(it, "action_label") or "Select"
                    it_act_msg = _llm_text(it, "actionMessage") or _llm_text(it, "action_message") or it_title
                    if it_title:
                        c_items.append({
                            "title": it_title, "subtitle": it_sub, "badge": it_badge,
                            "highlight": it_hi, "details": it_dets,
                            "actionLabel": it_act_lbl, "actionMessage": it_act_msg,
                        })
            if c_items:
                lead_text = "\n\n".join(part for part in [context_text, answer] if part)
                rendered_text = "\n".join([lead_text, title, *[f"{it['title']}: {it['highlight']}" for it in c_items], extended_text]).strip()
                block = {
                    "blockId": f"element_{index}", "sourceTaskIds": source_ids, "sourceShortcutHandles": source_handles,
                    "type": "carousel", "contextText": lead_text, "title": title, "items": c_items,
                    "options": options, "extendedText": extended_text,
                }
                response_blocks.append(block)
                answer_parts.append(rendered_text)
                element_sources.append(source_ids)
                continue

        elif requested_type == "ALERT":
            a_msg = _llm_text(element, "message") or _llm_text(element, "text") or answer
            a_sev = str(element.get("severity", "warning")).lower()
            if a_sev not in {"warning", "security", "info", "success", "critical"}:
                a_sev = "warning"
            if a_sev == "critical":
                a_sev = "security"
            act_obj = element.get("action")
            action_data = None
            if isinstance(act_obj, dict) and _llm_text(act_obj, "label"):
                action_data = {
                    "label": _llm_text(act_obj, "label"),
                    "message": _llm_text(act_obj, "message") or _llm_text(act_obj, "action"),
                }
            if title or a_msg:
                rendered_text = "\n".join([title, a_msg, extended_text]).strip()
                block = {
                    "blockId": f"element_{index}", "sourceTaskIds": source_ids, "sourceShortcutHandles": source_handles,
                    "type": "alert", "title": title, "message": a_msg, "severity": a_sev, "action": action_data,
                    "options": options, "extendedText": extended_text,
                }
                response_blocks.append(block)
                answer_parts.append(rendered_text)
                element_sources.append(source_ids)
                continue

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
                "blockId": f"element_{index}", "sourceTaskIds": source_ids, "sourceShortcutHandles": source_handles,
                "type": "stepper", "contextText": lead_text, "text": "", "items": items,
                "options": options, "progressTitle": _llm_text(element, "progress_title") or title,
                "stepDetails": [], "currentStep": 1, "totalSteps": len(progress_steps),
                "listTitle": "", "listIntro": "", "extendedText": extended_text,
            }
        elif block_type == "checklist":
            list_intro = "\n\n".join(part for part in [context_text, answer] if part)
            rendered_text = "\n".join([part for part in [list_intro, *list_items, extended_text] if part])
            block = {
                "blockId": f"element_{index}", "sourceTaskIds": source_ids, "sourceShortcutHandles": source_handles,
                "type": "checklist", "contextText": "", "text": "", "items": list_items,
                "options": options, "progressTitle": "", "stepDetails": [], "currentStep": None,
                "totalSteps": None, "listTitle": _llm_text(element, "list_title") or title,
                "listIntro": list_intro, "extendedText": extended_text,
            }
        else:
            rendered_text = "\n\n".join(part for part in [context_text, answer, extended_text] if part)
            block = {
                "blockId": f"element_{index}", "sourceTaskIds": source_ids, "sourceShortcutHandles": source_handles,
                "type": "text", "contextText": context_text, "text": "\n\n".join(part for part in [answer, extended_text] if part),
                "items": [], "options": options, "progressTitle": "", "stepDetails": [],
                "currentStep": None, "totalSteps": None, "listTitle": "", "listIntro": "", "extendedText": "",
            }
        response_blocks.append(block)
        answer_parts.append(rendered_text)
        element_sources.append(source_ids)
    for block in response_blocks:
        block["sourceUnmatchedIds"] = unmatched_by_block.get(block.get("blockId"), [])

    # The model can regenerate one flagged task's element during a redraft and
    # simply forget to re-emit an already-correct task's element alongside it.
    # Since nothing is left to flag, outbound audits pass on the truncated
    # draft and the omission would otherwise reach the customer silently.
    # Task-omission, clarification/decline/no-info-omission detection, and
    # shortcut-omission are all handled by the shared
    # _apply_response_completeness_nets (also reused by node_04c_response_
    # reviser on every retry round) -- see that function for the full
    # reasoning on each net.
    response_blocks = _apply_response_completeness_nets(
        response_blocks, state, evidence_by_task, task_metadata,
        intent_clarifications, decline_context, no_info_context, conversational_context,
        available_shortcuts,
    )

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

    # `clarification` existed only for the old terminal unresolved markers,
    # which are now part of their RELEVANT evidence entry (see
    # _response_evidence_manifest); the prompt asks for it empty. Every
    # real clarifying question is a Clarification Input, rendered by the
    # same generic builder node_11 uses. Still read here so text the model
    # puts in it anyway is kept and audited rather than silently lost; such
    # a block has no source obligation.
    terminal_clarification = _llm_text(generated, "clarification")
    if terminal_clarification:
        response_blocks.append({
            "blockId": "clarification", "sourceTaskIds": [], "type": "clarification",
            "text": terminal_clarification, "options": [],
        })
    # Any "clarification"-typed block -- this one, or one
    # the completeness net above already inserted for a Clarification Input
    # the model dropped entirely -- feeds final_clarification/final_draft
    # the same way node_04c_response_reviser already aggregates them
    # (clarification_blocks below). Before this, a net-inserted repair
    # would render correctly in the UI (ui_action_payload.responseBlocks)
    # but never reach final_draft/chat_history, so a later turn's own
    # conversation-history context would have no record a question was
    # ever asked -- this closes that gap, not just the net's own insertion.
    clarification = "\n\n".join(
        b.get("text", "") for b in response_blocks if b.get("type") == "clarification" and b.get("text")
    )

    # Next-action suggestions: never a guess about what the customer might
    # want (that risks a dead-end tap the pipeline can't answer) -- each is
    # required by the prompt above to point at real, already-approved
    # evidence this turn that the composed answer simply didn't feature.
    # Optional by design; an empty list here is the expected common case,
    # not a fallback failure.
    next_actions = []
    if isinstance(generated.get("next_actions"), list):
        for item in generated["next_actions"]:
            text = str(item).strip() if isinstance(item, str) else ""
            if text and text not in next_actions:
                next_actions.append(text)
    next_actions = next_actions[:3]

    # Clarification-guess chips are deterministic UI data straight from
    # whichever node authored the clarification -- attach them to
    # whichever block ended up citing that clarification's subquery_id, so
    # a chip only ever appears alongside content actually addressing it.
    guesses_by_subquery_id = {
        c["subquery_id"]: [{"label": g["label"], "message": g["message"]} for g in c.get("clarification_guesses", [])]
        for c in intent_clarifications if c.get("clarification_guesses")
    }
    if guesses_by_subquery_id:
        for block in response_blocks:
            for task_id in block.get("sourceTaskIds", []):
                if task_id in guesses_by_subquery_id:
                    # A block that already blended the clarification into its
                    # own composed content may already carry equivalent
                    # options of its own (e.g. the model wrote its own
                    # Savings/Credit Card choice into a checklist it also
                    # cited subquery_id on) -- dedupe by (label, message) so
                    # the same guess never renders as two identical chips.
                    existing = list(block.get("options", []))
                    existing_keys = {
                        (opt.get("label"), opt.get("message")) for opt in existing if isinstance(opt, dict)
                    }
                    for guess in guesses_by_subquery_id[task_id]:
                        key = (guess.get("label"), guess.get("message"))
                        if key not in existing_keys:
                            existing.append(guess)
                            existing_keys.add(key)
                    block["options"] = existing

    # The shortcut-omission net already ran as part of
    # _apply_response_completeness_nets above, against the block set before
    # clarification/chip attachment -- matching this function's own
    # original ordering (the shortcut check's "combined so far" never
    # included the terminal clarification's text either way).
    answer_text = "\n\n".join(p for p in (_flatten_response_block_text(b) for b in response_blocks if b.get("type") != "clarification") if p)
    draft = "\n\n".join(part for part in [answer_text, clarification] if part) or "Sorry, I don't have an answer for that right now."
    print(f"   💬 [FLEXIBLE RESPONSE GENERATED]: {len(response_blocks)} element(s)")
    log_event("response.flexible.generated", element_types=[block.get("type") for block in response_blocks], element_source_task_ids=[block.get("sourceTaskIds", []) for block in response_blocks], element_shortcut_handles=[block.get("sourceShortcutHandles", []) for block in response_blocks], next_action_count=len(next_actions))
    return {
        "final_draft": draft,
        "final_answer": answer_text,
        "final_clarification": clarification,
        "active_plan": {},
        "completed_steps": [],
        "ui_action_payload": {"uiType": "blocks", "responseBlocks": response_blocks, "options": [], "nextActions": next_actions},
        "pending_advance": False,
        # What the generator received, keyed by id -- node_04c revises each
        # block from exactly the entries that block cited (BUG-038).
        "generation_obligations": {o["id"]: o for o in obligations if o.get("id")},
    }


def _generate_evidence_only_response(intent_clarifications: list, decline_context: list, no_info_context: list, conversational_context: list, state: AgentState) -> dict:
    """The same response-generator role as _generate_response_blocks, just
    dispatched to a narrower sub-prompt for a turn with no real evidence at
    all -- only clarification/decline_evidence/no_info_evidence/
    conversational entries (see node_02d/node_02e and
    _terminal_retrieval_feedback). Its only job is
    composing these already-decided statements into one coherent customer
    message; its schema has no alternative field to substitute a different
    answer into, which is what removes the reliability risk a fully
    open-ended prompt would have here (unlike node_02e's own resolution
    decision, or node_04b's flexible answer composition, there is no
    judgment call left to make -- only phrasing already-decided content,
    and for clarification specifically, composing the actual question from
    already-decided ingredients rather than a pre-phrased sentence).

    Elements may cite more than one statement's task_id (`source_task_ids`,
    a list, mirroring _generate_response_blocks) -- the customer-facing
    text must read as one coherent message free from the internal schema's
    shape, never as a mechanical one-block-per-entry transcript, so two
    genuinely related statements (e.g. two clarifications about the same
    underlying ambiguity) may be composed together in a single element when
    that reads more naturally than two separate ones.

    Deliberately omits PRECISION_REFERENCE_CONTRACT and
    PERSONAL_DATA_REFERENCE_CONTRACT, unlike _generate_response_blocks:
    _decline_evidence_text/_no_info_evidence_text never emit a `{{faq_*}}`
    or `{{data-*}}` token, so neither contract has anything to apply to
    here -- not an oversight to "fix" by adding them.

    Shares node_06's outbound audit with the main flexible-generation path
    (see _normal_outbound_grounding_context's evidence fields and the
    "Clarification Rules"/"Conversational Rules" it audits against) -- this
    function makes no audit call of its own.
    """
    entries = [
        {
            "task_id": c["subquery_id"], "kind": "clarification",
            "scope": c.get("scope", ""), "unresolved_request": c.get("unresolved_request", ""),
            "clarification_guesses": [{"label": g["label"], "message": g["message"]} for g in c.get("clarification_guesses", [])],
            # Never shown to the model -- only used by the completeness net
            # below if generation produces nothing usable for this entry.
            "fallback_text": _clarification_fallback_text(c),
        }
        for c in intent_clarifications if c.get("unresolved_request")
    ] + [
        {"task_id": d["task_id"], "kind": "decline", "evidence": d["decline_evidence"], "fallback_text": d["decline_evidence"]}
        for d in decline_context if d.get("decline_evidence")
    ] + [
        {"task_id": n["task_id"], "kind": "no_info", "evidence": n["no_info_evidence"], "fallback_text": n["no_info_evidence"]}
        for n in no_info_context if n.get("no_info_evidence")
    ] + [
        {
            "task_id": v["subquery_id"], "kind": "conversational",
            "customer_component": v["customer_component"],
            # Never shown to the model -- a generic, tone-neutral last
            # resort, exactly as trustworthy as any other deterministic
            # fallback here, used only if generation produces nothing
            # usable for this entry.
            "fallback_text": "Got it!",
        }
        for v in conversational_context if v.get("customer_component")
    ]
    if not entries:
        # Defensive only -- node_02d/node_02e already validated this turn
        # has a resolution, so this should not happen in practice.
        text = "Sorry, I couldn't find relevant FAQ details to answer that. Could you clarify what information you are looking for?"
        return {
            "final_draft": text, "final_answer": "", "final_clarification": text,
            "active_plan": {}, "completed_steps": [],
            "ui_action_payload": {"uiType": "none", "options": []},
            "pending_advance": False,
        }

    user_profile = state.get("user_profile", {})
    conversation_context = _build_conversation_context(state)
    # Never send fallback_text to the model -- it's the deterministic
    # safety net used only by the completeness net below if this call
    # produces nothing usable, not something to be seen or parroted.
    model_entries = [{k: v for k, v in entry.items() if k != "fallback_text"} for entry in entries]

    prompt = f"""
    PRE-DECIDED STATEMENT COMPOSITION
    You are Horizon, a Horizon Bank customer service representative. This
    turn's entire request resolves only to the pre-decided statements
    below -- there is no other approved information to answer with this
    turn.

    {_style_personalization_block(user_profile)}

    {_GENERATION_CONDUCT_GUARDRAILS}

    {_GENERATION_BRIDGE_GUARDRAILS}

    Customer request: {state.get("user_input", "")}
    Trusted conversation context (use only to understand what topic this
    continues, and the customer's own language/tone; never as factual
    banking evidence):
    {conversation_context}

    A "kind" of "decline" is a scope boundary to state; "no_info" is an
    availability notice to state -- each of these two carries its own
    "evidence" field, already the complete, final content for it, not a
    fact to research, verify, or build a new answer from. Your job for it
    is only to relay it, in your own words and in the style above, never
    to add to, verify, question, resolve, or improve upon what it already
    says.

    A "kind" of "clarification" is different: it has no fixed sentence to
    relay. Compose your own natural question asking specifically about its
    "unresolved_request", grounded in the Customer request and
    conversation context above for what topic this continues -- never
    answer the request in its place, never assert a new product, policy,
    or fact, and stay within its "scope" (never overstepping into a
    different job's judgment than the one named there).

    A "kind" of "conversational" also has no fixed sentence to relay: it is
    a non-financial part of this turn (a greeting, thanks, feeling, or an
    off-topic remark), already identified as needing no banking fact.
    Compose one brief, natural, tone-appropriate reply from its own
    "customer_component" -- never invent or imply a banking fact, product,
    policy, commitment, or next action for it, and never treat it as a
    question to answer.

    Pre-decided statements (the ONLY permitted content; combine them into
    ONE short, natural, coherent customer message):
    {json.dumps(model_entries, ensure_ascii=False)}

    Rules:
    - Address every statement listed, using its task_id ONLY in the
      separate "source_task_ids" JSON field below -- never write "task_id",
      "subquery_id", the literal id value, or any bracketed/internal
      identifier inside "text" itself. "text" is customer-facing prose
      only; the id is structural metadata the customer never sees. Do not
      skip any statement.
    - "source_task_ids" is a list: when two or more statements are
      genuinely related and reads more naturally composed together (most
      commonly two "clarification" statements about the same underlying
      ambiguity), write ONE element covering all of them and list every
      one of their task_ids in "source_task_ids" -- not one the element is
      primarily about. An id left out of every element's "source_task_ids"
      is treated as never addressed. Keep unrelated statements (e.g. a
      clarification alongside an unrelated decline) in separate elements
      rather than forcing them into one.
    - For "decline"/"no_info" kinds: rephrase naturally in the customer's
      own language/tone/style from the STYLE & PERSONALIZATION
      INSTRUCTIONS above. Copying the "evidence" text word-for-word is a
      last resort only, never the default. Do not add a new claim,
      apology, product, policy, or suggestion beyond what it says.
    - You may add brief natural connecting language between statements, but
      never change what any statement means.
    - Never mention "the matrix," an internal schema, a tool, or a process
      name.
    - When a "clarification" statement's own "clarification_guesses" is
      non-empty, don't just ask the bare question -- naturally work a
      brief mention of what those options actually are into the same
      sentence or the one right after it (e.g. "...are you asking about
      applying, checking eligibility, or the fees involved?"), using each
      guess's own "label" or "message" as the TOPIC to mention, never
      inventing an option not listed there. A guess's "message" is written
      in the CUSTOMER's own voice (what they would send if they tapped
      it) -- never copy or closely paraphrase its sentence structure into
      your own text, which is YOUR voice speaking to the customer
      ("I"/"you" reversed from the guess's own phrasing). For example, a
      guess message "Can you list the other banking services you can help
      me with?" becomes, in your voice, "...are you looking for other
      banking services I can help you with...", never reused with its own
      "you"/"me" left as-is. Tappable chips for these same options render
      automatically right after your text -- without this, the chips
      appear with nothing in the message explaining what they're for,
      which reads as abrupt and unnatural. When "clarification_guesses" is
      empty, ask a plain open question as before; there is nothing to
      mention.

    Output JSON only:
    {{"elements": [{{"source_task_ids": ["the exact task_id(s) from the statement(s) above this element addresses"], "text": "a warm, natural statement or question, in the style above"}}]}}
    """
    try:
        result = _parse_json_robustly(call_groq_lpu(prompt))
        generated_elements = result.get("elements", []) if isinstance(result.get("elements"), list) else []
    except Exception as e:
        print(f"🚨 [EVIDENCE-ONLY RESPONSE EXCEPTION]: {e}")
        generated_elements = []

    entries_by_id = {entry["task_id"]: entry for entry in entries}
    covered_ids = set()
    response_blocks = []
    parts = []
    for element in generated_elements:
        if not isinstance(element, dict):
            continue
        source_ids = list(dict.fromkeys(
            tid for tid in (str(raw).strip() for raw in _llm_list(element, "source_task_ids"))
            if tid in entries_by_id and tid not in covered_ids
        ))
        text = str(element.get("text", "")).strip()
        if not source_ids or not text:
            continue
        covered_ids.update(source_ids)
        block_type = "clarification" if any(entries_by_id[tid]["kind"] == "clarification" for tid in source_ids) else "text"
        response_blocks.append({"blockId": f"element_{'_'.join(source_ids)}", "sourceTaskIds": source_ids, "type": block_type, "text": text, "options": []})
        parts.append(text)
    model_covered_count = len(covered_ids)

    # Completeness check -- detection only, the same tradeoff already made
    # for _generate_response_blocks (see DECLINE_CLARIFICATION_DESIGN_v1.0.md
    # §4g): a coherent element may legitimately blend two related statements
    # into one "source_task_ids" list, so an id absent from any element is
    # not automatically a dropped statement, and mechanically inserting a
    # duplicate fallback for it risks the same false-positive double-
    # clarification found in that other function. Insertion is reserved
    # for the genuine defensive case below, not for every individually-
    # uncited entry.
    for entry in entries:
        if entry["task_id"] in covered_ids:
            continue
        print(f"   ℹ️ [ENTRY NOT EXPLICITLY CITED]: {entry['task_id']} ({entry['kind']}) not found in generated elements' source_task_ids -- no fallback inserted (bypassed)")
        log_event("response.evidence_only.entry_omitted", task_id=entry["task_id"], kind=entry["kind"], fallback_inserted=False)

    # Defensive only: a malformed/empty model response (parse failure, or a
    # response that covered nothing at all) still needs every statement
    # represented -- there is no coherent draft here to protect from
    # mechanical duplication in the first place, so this is the one case
    # insertion remains for.
    if not parts:
        for entry in entries:
            block_type = "clarification" if entry["kind"] == "clarification" else "text"
            response_blocks.append({"blockId": entry["task_id"], "sourceTaskIds": [entry["task_id"]], "type": block_type, "text": entry["fallback_text"], "options": []})
            parts.append(entry["fallback_text"])
        covered_ids = {entry["task_id"] for entry in entries}

    # Clarification-guess chips are deterministic UI data straight from
    # whichever node authored the clarification -- attach to whichever
    # block ended up citing that clarification's subquery_id, combining
    # guesses from every id a block cites (a block may cite more than one
    # clarification now) and deduping in case the same guess is reachable
    # from more than one cited id.
    guesses_by_subquery_id = {
        c["subquery_id"]: [{"label": g["label"], "message": g["message"]} for g in c.get("clarification_guesses", [])]
        for c in intent_clarifications if c.get("clarification_guesses")
    }
    for block in response_blocks:
        combined, seen_keys = [], set()
        for task_id in block.get("sourceTaskIds", []):
            for guess in guesses_by_subquery_id.get(task_id, []):
                key = (guess.get("label"), guess.get("message"))
                if key not in seen_keys:
                    combined.append(guess)
                    seen_keys.add(key)
        if combined:
            block["options"] = combined

    final_text = "\n\n".join(parts)
    log_event(
        "response.evidence_only.generated",
        entry_count=len(entries),
        model_covered_count=model_covered_count,
        fallback_count=len(entries) - len(covered_ids),
    )
    return {
        "final_draft": final_text,
        "final_answer": "",
        "final_clarification": final_text,
        "active_plan": {},
        "completed_steps": [],
        "ui_action_payload": {"uiType": "blocks", "responseBlocks": response_blocks, "options": []},
        "pending_advance": False,
    }


def node_04b_response_generator(state: AgentState):
    print("🟢 [NODE 04b] Executing Response Generator")
    # Node 04b owns all customer-facing organisation and UI. Node 04a provides
    # global resolution status only; it does not prescribe presentation.
    # node_04a_resolution_planner has exactly one return path, and it always
    # sets presentation_plan.resolution_context -- so this is the only
    # reachable branch for any state produced by the current graph. The
    # pre-UX-decoupling renderer this used to fall back to (_generate_task_
    # bound_response_blocks) was removed once that stopped being true: the
    # checkpointer (MemorySaver) is in-process only and is wiped on every
    # restart, so no session state old enough to need it can exist anymore.
    return _generate_response_blocks(state)


def node_04c_response_reviser(state: AgentState) -> dict:
    """Revise only this round's pending blocks in place -- never a full
    redraft (Per-Task Outbound Response Revision Loop). Invoked only on
    retry from node_06_outbound_audit_join for a normal_response-scope
    turn (see route_n21 in graph.py); the non-financial/evidence-only
    paths keep routing failures to node_04b unchanged.

    No UI-structure job: it may not change a block's `type`, add, remove,
    or reorder blocks, or touch `next_actions` -- only the natural-language
    leaf fields (answer/context_text/extended_text/title/items/steps/
    sections/message/etc.) of the specific blocks it is handed.

    The model is shown and asked to return the FULL block skeleton (every
    block, tagged `revision_status: "approved"|"pending"`), not just the
    pending subset -- "edit this document and return the whole thing" is
    the task shape most instruct-tuned models handle reliably; asking for
    only a keyed delta patch of some of it was traced live returning a bare
    list instead of the requested wrapper, silently losing a whole retry
    round. Safety is unaffected by showing/returning the full skeleton:
    whatever the model writes for an `"approved"` entry, or for any
    `block_id` outside `pending_block_ids`, is never read -- the
    deterministic `_reassemble_response_blocks` splice, run before the next
    audit call, reads the output strictly as a keyed lookup restricted to
    `pending_block_ids`, and a pending block it silently drops keeps its
    prior content unchanged rather than vanishing. This is what makes "the
    reviser cannot change what's approved" a structural guarantee rather
    than a prompt instruction.
    """
    print("🟢 [NODE 04c] Executing Response Reviser (partial retry)")
    payload = state.get("ui_action_payload", {})
    original_blocks = payload.get("responseBlocks", [])
    approved_blocks = state.get("approved_response_blocks", [])
    approved_ids = {block.get("blockId") for block in approved_blocks}
    pending_task_ids = set(state.get("pending_task_ids", []))
    response_wide = bool(state.get("response_wide_violation_this_round"))

    pending_blocks = (
        list(original_blocks) if response_wide
        else [block for block in original_blocks if block.get("blockId") not in approved_ids]
    )
    pending_block_ids = {block.get("blockId") for block in pending_blocks}

    # The model is shown and asked to return the FULL block skeleton, not
    # just the pending subset -- "edit this document and return the whole
    # thing" is the task shape most instruct-tuned models actually handle
    # reliably; "return only a keyed delta patch of some of it" is a less
    # natural ask and was traced live returning a bare list instead of the
    # requested wrapper. Safety is unaffected either way: below, only
    # entries whose block_id is in pending_block_ids are ever read from
    # this output -- an approved block's entry, however the model chooses
    # to render it, is always discarded and the stored approved copy is
    # used instead, exactly as before this change.
    full_skeleton = [
        {
            **_simplify_block_for_revision(block),
            "revision_status": "pending" if block.get("blockId") in pending_block_ids else "approved",
        }
        for block in original_blocks
    ]

    violation_history = state.get("task_violation_history", {})
    scoped_violations = (
        [v for entries in violation_history.values() for v in entries] if response_wide
        else [v for task_id in pending_task_ids for v in violation_history.get(task_id, [])]
             + violation_history.get("", [])
    )

    # Typed contexts, rebuilt from state: they order the prompt sections
    # below and feed the completeness net; which entries the reviser sees
    # is decided by the pending blocks' own citations (see
    # recorded_obligations below).
    evidence_by_task = _hydrate_response_evidence(state)
    task_metadata = {
        task.get("task_id"): task.get("customer_component", "")
        for task in state.get("search_tasks", [])
        if task.get("task_id")
    }
    intent_clarifications = _build_clarification_context(state)
    decline_context = _build_decline_context(state)
    conversational_context = _build_conversational_context(state)
    no_info_context = _no_info_entries(
        state.get("task_resolution_instructions", []), task_metadata, state.get("unresolvable_search_tasks", [])
    )
    available_shortcuts = _available_response_shortcuts(state)
    unavailable_service_requests = _unavailable_service_requests(state)
    verified_api_context = _verified_api_context(state)
    personal_data_placeholders = _personal_data_placeholders(state)

    # Unified Response Obligation Contract (docs/RESPONSE_OBLIGATION_CONTRACT_
    # DESIGN.md, Stage 2): the same builder _generate_response_blocks uses,
    # used below only as the fallback when the generator's recorded copy
    # (generation_obligations) is absent; that fallback keeps the old
    # `pending_task_ids` filter, with handle-keyed obligations (shortcuts,
    # unavailable-requests, verified-facts, personal-data) exempt from it.
    obligations = _build_response_obligations(
        evidence_by_task, verified_api_context, decline_context, no_info_context,
        conversational_context, intent_clarifications, available_shortcuts,
        unavailable_service_requests, personal_data_placeholders, task_metadata,
    )
    obligations_by_id = {o["id"]: o for o in obligations if o.get("id")}
    handle_ids = {
        entry["handle"]
        for entry in (
            *available_shortcuts, *unavailable_service_requests,
            *personal_data_placeholders, *verified_api_context,
        )
    }
    # BUG-038: the reviser sees what the generator saw when it wrote each
    # block -- the obligation entries that block cited, taken from the copy
    # the generator recorded (generation_obligations), not rebuilt from the
    # flagged task ids. A response-wide round covers every block, so every
    # entry the generator received. The rebuilt list above is kept only as
    # the fallback for a state without the recorded copy.
    recorded_obligations = state.get("generation_obligations") or {}
    if recorded_obligations:
        if response_wide:
            allowed_ids = set(recorded_obligations)
        else:
            allowed_ids = {
                oid
                for block in pending_blocks
                for oid in (*(block.get("sourceTaskIds") or []), *(block.get("sourceShortcutHandles") or []))
            }
        obligations_by_id = {oid: recorded_obligations[oid] for oid in allowed_ids if oid in recorded_obligations}
    elif not response_wide:
        obligations_by_id = {
            oid: o for oid, o in obligations_by_id.items()
            if oid in handle_ids or oid in pending_task_ids
        }
    evidence_obligations = [obligations_by_id[e["task_id"]] for e in evidence_by_task if e.get("task_id") in obligations_by_id]
    verified_fact_obligations = [obligations_by_id[e["handle"]] for e in verified_api_context if e.get("handle") in obligations_by_id]
    conversational_obligations = [obligations_by_id[e["subquery_id"]] for e in conversational_context if e.get("subquery_id") in obligations_by_id]
    decline_obligations = [obligations_by_id[e["task_id"]] for e in decline_context if e.get("task_id") in obligations_by_id]
    no_info_obligations = [obligations_by_id[e["task_id"]] for e in no_info_context if e.get("task_id") in obligations_by_id]
    clarification_obligations = [obligations_by_id[c["subquery_id"]] for c in intent_clarifications if c.get("subquery_id") in obligations_by_id]
    shortcut_obligations = [obligations_by_id[e["handle"]] for e in available_shortcuts if e.get("handle") in obligations_by_id]
    unavailable_obligations = [obligations_by_id[e["handle"]] for e in unavailable_service_requests if e.get("handle") in obligations_by_id]
    personal_data_obligations = [obligations_by_id[e["handle"]] for e in personal_data_placeholders if e.get("handle") in obligations_by_id]

    obligation_sections = []
    if conversational_obligations:
        obligation_sections.append(
            "Conversational inputs for these blocks only (governed by "
            "CONVERSATIONAL RULES below):\n" + json.dumps(conversational_obligations, ensure_ascii=False)
        )
    if evidence_obligations:
        obligation_sections.append(
            "Approved evidence for these blocks' task_ids only (governed by "
            "EVIDENCE RULES below):\n" + json.dumps(evidence_obligations, ensure_ascii=False)
        )
    if clarification_obligations:
        obligation_sections.append(
            "Clarification inputs for these blocks only (governed by "
            "CLARIFICATION RULES below):\n" + json.dumps(clarification_obligations, ensure_ascii=False)
        )
    if decline_obligations:
        obligation_sections.append(
            "Decline inputs for these blocks only (governed by DECLINE/"
            "NO-INFO RULES below):\n" + json.dumps(decline_obligations, ensure_ascii=False)
        )
    if no_info_obligations:
        obligation_sections.append(
            "No-info inputs for these blocks only (governed by DECLINE/"
            "NO-INFO RULES below):\n" + json.dumps(no_info_obligations, ensure_ascii=False)
        )
    if shortcut_obligations:
        obligation_sections.append(
            "Available shortcuts this turn (governed by SHORTCUT RULES "
            "below):\n" + json.dumps(shortcut_obligations, ensure_ascii=False)
        )
    if unavailable_obligations:
        obligation_sections.append(
            "Unavailable service requests (governed by UNAVAILABLE SERVICE "
            "REQUEST RULES below):\n" + json.dumps(unavailable_obligations, ensure_ascii=False)
        )
    if verified_fact_obligations:
        obligation_sections.append(
            "Verified customer-specific facts (governed by EVIDENCE RULES "
            "below, same footing as approved evidence):\n"
            + json.dumps(verified_fact_obligations, ensure_ascii=False)
        )
    if personal_data_obligations:
        obligation_sections.append(
            "Personal data references (governed by PERSONAL DATA RULES "
            "below):\n" + json.dumps(personal_data_obligations, ensure_ascii=False)
        )
    obligations_block = "\n".join(obligation_sections) if obligation_sections else "None this round."

    # Every named rule section is included only when this round's pending
    # blocks actually have an obligation it governs -- same discipline as
    # _generate_response_blocks, and the exact same shared section text, so
    # a reviser round is held to precisely the rules the original generation
    # round was, with no separately-maintained (and driftable) copy.
    rule_sections = []
    if evidence_obligations or verified_fact_obligations:
        rule_sections.append(_EVIDENCE_RULES_TEXT)
    if conversational_obligations:
        rule_sections.append(_CONVERSATIONAL_RULES_TEXT)
    if clarification_obligations:
        rule_sections.append(_CLARIFICATION_RULES_TEXT)
    if decline_obligations or no_info_obligations:
        rule_sections.append(_DECLINE_NO_INFO_RULES_TEXT)
    if shortcut_obligations:
        rule_sections.append(_SHORTCUT_RULES_TEXT)
    if unavailable_obligations:
        rule_sections.append(_UNAVAILABLE_SERVICE_REQUEST_RULES_TEXT)
    if personal_data_obligations:
        rule_sections.append(_PERSONAL_DATA_RULES_TEXT)
    rules_block = (
        "\n\n".join(rule_sections) if rule_sections
        else "No type-specific rule section applies this round (no shortcut, "
             "clarification, decline, no-info, unavailable-request, or "
             "personal-data obligation among this round's pending blocks)."
    )

    approved_task_ids = sorted({task_id for block in approved_blocks for task_id in block.get("sourceTaskIds", [])})
    whole_response_text = state.get("final_answer", state.get("final_draft", ""))
    user_profile = state.get("user_profile", {})

    prompt = f"""
    FLEXIBLE RESPONSE REVISION (partial retry -- edit in place only)
    You are Horizon, revising a customer response that already partly
    passed a compliance/groundedness audit. Below is the FULL current
    response as one ordered list of blocks, "Full Response Skeleton." Each
    entry's own "revision_status" tells you your job on it:
    - "approved": ALREADY LOCKED IN and will be shown to the customer
      exactly as-is regardless of anything you write for it. Repeat it
      unchanged in your output, or leave its content fields empty -- either
      way, nothing you write for an "approved" entry is ever used. Do not
      attempt to repeat, restate, contradict, or duplicate its content
      inside a "pending" entry either.
    - "pending": apply the violation fixes listed below to this entry only,
      and return the corrected version.
    Never change any entry's "block_id" or "type," never add or remove an
    entry, never reorder the list -- your output is the same list, in the
    same order, with only "pending" entries' content actually changed.

    {_style_personalization_block(user_profile)}

    {_GENERATION_CONDUCT_GUARDRAILS}

    {_GENERATION_BRIDGE_GUARDRAILS_UNIFIED}

    {_RESPONSE_PROPORTIONALITY_RULE}

    Full current response, as natural-language text (READ-ONLY, for tone/
    coherence only -- never repeat or restate its content, and never treat
    it as evidence; the structured skeleton below is what you actually
    edit):
    {whole_response_text}

    Already-Locked Task IDs This Turn (informational only -- these already
    passed audit; do not re-address them):
    {json.dumps(approved_task_ids, ensure_ascii=False)}

    Full Response Skeleton (each entry already shows only the fields that
    apply to its own "type" -- for a "pending" entry, return corrected
    values using EXACTLY those same field names, never a field not present
    in the entry you were given for it):
    {json.dumps(full_skeleton, ensure_ascii=False)}

    Violations you must fix for these blocks (internal constraints only,
    never evidence; "" task_id means a response-wide issue such as overall
    tone). Each entry's own generation_guidance is the direct instruction
    for what to write instead -- follow it as the primary directive for
    fixing that entry; violation/unsupported_content/evidence_gap are
    supporting context for WHY, not a separate instruction to reconcile
    against it:
    {json.dumps(scoped_violations, ensure_ascii=False)}

    OBLIGATIONS covered by this round's pending blocks -- every entry below,
    of every kind, carries the same three fields: "id" (already cited in
    that block's own source_task_ids -- never write it inside customer-
    facing text), "generation_content" (what you know), and
    "generation_guidance" (a one-line instruction that names the exact rule
    section governing this entry -- named sections appear further below,
    after all the data, only for the kinds actually present among this
    round's pending blocks). This is the same shape, and the same named
    rule sections, the original response was generated under -- apply them
    identically now, whether that means composing an answer, relaying a
    fixed statement, asking a clarifying question, or mentioning a
    shortcut, fact, or token:

    {obligations_block}

    {rules_block}

    {PRECISION_REFERENCE_CONTRACT}

    {PERSONAL_DATA_REFERENCE_CONTRACT}

    Output JSON only -- the SAME shape as "Full Response Skeleton" above: a
    single top-level object with one "response_blocks" key holding the
    FULL list, one entry per skeleton entry in the same order, "approved"
    entries repeated unchanged (or with empty content -- never read
    either way) and "pending" entries corrected, each using precisely that
    entry's own field names and nothing else. Never a bare list on its own:
    {{"response_blocks":[{{"block_id":"...","type":"...","revision_status":"approved"|"pending", "<only the fields that entry showed you, corrected values for a pending entry>": "..."}}]}}
    """
    try:
        generated = _parse_json_robustly(call_groq_lpu(prompt))
    except Exception as error:
        print(f"🚨 [RESPONSE REVISER ERROR]: {error}")
        generated = {}

    # Tolerate multiple shapes for the same content -- the model is asked
    # for the full skeleton back under "response_blocks", but has been
    # observed deviating to a bare list or the old "revised_blocks" key.
    # Whatever the wrapper, only entries whose block_id is in
    # pending_block_ids are ever read below -- an "approved" entry (or an
    # unrecognized block_id) is always discarded regardless of what the
    # model wrote for it, and the stored approved copy is used instead.
    if isinstance(generated, list):
        raw_revisions = generated
    elif isinstance(generated, dict):
        raw_revisions = generated.get("response_blocks", generated.get("revised_blocks", []))
    else:
        raw_revisions = []
    revised_by_block_id = {}
    for entry in raw_revisions:
        if not isinstance(entry, dict):
            continue
        block_id = entry.get("block_id")
        if block_id not in pending_block_ids:
            continue
        # _simplify_block_for_revision showed this block using exactly its
        # own stored field names as both the input AND the required output
        # shape -- one vocabulary, so no field-name translation is needed
        # here at all; _reassemble_response_blocks's own merge already
        # ignores blockId/type/sourceTaskIds and any empty value.
        # "revision_status" is this prompt's own bookkeeping tag, added by
        # full_skeleton above and never a real block field -- stripped here
        # so it can never leak into the merged, customer-facing block.
        revised_by_block_id[block_id] = {k: v for k, v in entry.items() if k not in ("block_id", "type", "revision_status")}

    spliced = _reassemble_response_blocks(approved_blocks, original_blocks, pending_block_ids, revised_by_block_id)
    spliced = _apply_response_completeness_nets(
        spliced, state, evidence_by_task=_hydrate_response_evidence(state), task_metadata=task_metadata,
        intent_clarifications=_build_clarification_context(state), decline_context=_build_decline_context(state),
        no_info_context=_no_info_entries(state.get("task_resolution_instructions", []), task_metadata, state.get("unresolvable_search_tasks", [])),
        conversational_context=_build_conversational_context(state), available_shortcuts=available_shortcuts,
    )

    answer_text = "\n\n".join(p for p in (_flatten_response_block_text(b) for b in spliced if b.get("type") != "clarification") if p)
    clarification_blocks = [b for b in spliced if b.get("type") == "clarification"]
    clarification = "\n\n".join(b.get("text", "") for b in clarification_blocks if b.get("text"))
    draft = "\n\n".join(p for p in [answer_text, clarification] if p) or "Sorry, I don't have an answer for that right now."

    print(f"   💬 [RESPONSE REVISED]: {len(pending_block_ids)} block(s) sent for revision, {len(revised_by_block_id)} returned")
    log_event("response.reviser.completed", pending_block_ids=sorted(pending_block_ids), revised_block_ids=sorted(revised_by_block_id.keys()), response_wide=response_wide)
    return {
        "final_draft": draft,
        "final_answer": answer_text,
        "final_clarification": clarification,
        "ui_action_payload": {**payload, "responseBlocks": spliced},
        "pending_advance": False,
    }


# --- PHASE 4: OUTPUT GUARDRAILS & ESCALATION HANDLER ---

# Deterministic outbound checks -- formerly node_05_outbound_deterministic_
# gate, a separate graph node that ran BEFORE the LLM audit and, on any hit,
# forced a full node_04b regeneration of un-audited text (discarding every
# already-approved block), after which the audit could still reject the
# regenerated text and need another round. Now these same checks run inside
# node_06_outbound_audit_join, alongside the audit's own findings, and are
# expressed in the reviser's existing violation format: every problem --
# probabilistic (audit) or deterministic (here) -- is collected first, and
# the affected subqueries are revised together in one reviser call.
_DETERMINISTIC_FINDING_TEXT = {
    "invalid_placeholder": (
        "This `{{faq_*}}` token is not a valid exact-value reference.",
        "Write each exact value only with a `{{faq_*}}` token that appears, character for character, in this task's approved evidence; where the evidence gives no token for a value, state only what the evidence says in words.",
    ),
    "exposed_internal_id": (
        "An internal FAQ record identifier appears in customer-facing text.",
        "State the supported fact directly, with no source document, reference, or internal identifier attached to it.",
    ),
    "illegal_word": (
        "A blocked internal term appears in customer-facing text.",
        "Rewrite this content in plain customer-facing language.",
    ),
    # BUG-038: every block must be based on the obligations the generator
    # received. These two are raised once, on a block as generated (see
    # `revised` in _reassemble_response_blocks); the evidence gap names the
    # ids so the reviser knows which citation was not an obligation.
    "non_obligation_citation": (
        "This block cites {ids}, which are not obligations given for this response, so any content based on them has no permitted source.",
        "Rewrite this block using only the obligations listed for it; remove any content they do not support.",
    ),
    "no_obligation": (
        "This block is not based on any obligation given for this response.",
        "Remove every banking fact, procedure, product, policy, or next step from it; keep at most a brief courtesy or connecting sentence.",
    ),
}
_BLOCK_METADATA_KEYS = {"blockId", "taskId", "sourceTaskIds", "sourceShortcutHandles", "sourceUnmatchedIds", "revised"}


def _deterministic_outbound_findings(state: AgentState, check_citations: bool = False) -> list[dict]:
    """The former node_05 checks, unchanged in what they detect: an invalid
    `{{faq_*}}` placeholder (no row in Precision_Reference_Table), an
    exposed FAQ parent id, and the ILLEGAL_WORD sentinel.

    Returned as violation records in the reviser's own format (task_id,
    unsupported_content, evidence_gap, generation_guidance), so
    _partition_audited_response and node_04c consume them exactly like the
    audit's. Each response block is scanned separately and a finding is
    attributed to that block's own first sourceTaskIds entry, making just
    that block (and every id it names) pending; a block with no task id,
    or a hit found only outside the blocks, is task_id "" -- response-wide,
    the same convention the audit's own violations use.

    With `check_citations` (normal_response turns, which have the per-block
    revision loop), also the BUG-038 citation check: a generated block that
    cites an id that was not one of the generator's obligations, or cites
    no obligation at all, is flagged. Such a finding's task_id is the
    block's own blockId -- the block is known exactly, and
    _partition_audited_response matches a block by its blockId too.
    """
    payload = state.get("ui_action_payload", {})
    blocks = [block for block in payload.get("responseBlocks", []) if isinstance(block, dict)]
    other_text = " ".join([
        state.get("final_draft", ""),
        *_ui_option_texts(payload.get("options", [])),
        *payload.get("stepDetails", []),
        *(payload.get("items", []) if payload.get("uiType") == "checklist" else []),
        payload.get("listTitle", ""),
        payload.get("listIntro", ""),
        payload.get("extendedText", ""),
        payload.get("listClarificationText", ""),
    ])
    block_texts = [
        json.dumps({k: v for k, v in block.items() if k not in _BLOCK_METADATA_KEYS}, ensure_ascii=False)
        for block in blocks
    ]

    def _faq_placeholders(text: str) -> list:
        # `{{data-*}}` tokens are never rows in Precision_Reference_Table --
        # the frontend resolves them, not the backend -- so checking them
        # here would reject every correct one. Their legitimacy is the
        # groundedness audit's job.
        return [p for p in re.findall(r"\{\{(.*?)\}\}", text) if not p.startswith("data-")]

    candidates = sorted({p for text in [other_text, *block_texts] for p in _faq_placeholders(text)})
    valid = fetch_placeholders_from_db(candidates) if candidates else {}

    def _scan(text: str) -> dict:
        return {
            "invalid_placeholder": sorted({"{{" + p + "}}" for p in _faq_placeholders(text) if p not in valid}),
            # Parent ids identify internal FAQ records: allowed inside a
            # placeholder until node_07 substitutes it, never as prose.
            "exposed_internal_id": sorted(set(re.findall(r"(?<!\{)faq_[0-9a-f]{8}(?![_}])", text, flags=re.IGNORECASE))),
            "illegal_word": ["ILLEGAL_WORD"] if "ILLEGAL_WORD" in text else [],
        }

    def _finding(task_id: str, kind: str, content: list, ids: str = "") -> dict:
        evidence_gap, guidance = _DETERMINISTIC_FINDING_TEXT[kind]
        evidence_gap = evidence_gap.replace("{ids}", ids)
        return {
            "task_id": task_id,
            "scope": "answer",
            "audit_layer": "deterministic",
            "violation_type": kind,
            "unsupported_content": content,
            "evidence_gap": evidence_gap,
            "generation_guidance": guidance,
        }

    findings, attributed = [], set()
    for block, text in zip(blocks, block_texts):
        task_ids = [t for t in (block.get("sourceTaskIds") or []) if t]
        for kind, content in _scan(text).items():
            if content:
                findings.append(_finding(task_ids[0] if task_ids else "", kind, content))
                attributed.update(content)
    if check_citations:
        for block in blocks:
            block_id = str(block.get("blockId") or "")
            # Only blocks the generator wrote (element_N, and the free-text
            # `clarification` field's block) -- net-inserted and fallback
            # blocks are built by code from one obligation each -- and only
            # before their first revision.
            if not (block_id.startswith("element_") or block_id == "clarification") or block.get("revised"):
                continue
            text = _flatten_response_block_text(block)
            unmatched = [i for i in (block.get("sourceUnmatchedIds") or []) if i]
            if unmatched:
                findings.append(_finding(block_id, "non_obligation_citation", [text], ", ".join(unmatched)))
            elif not (block.get("sourceTaskIds") or block.get("sourceShortcutHandles")):
                findings.append(_finding(block_id, "no_obligation", [text]))
    # final_draft is flattened from the blocks, so most hits here are the
    # same ones already attributed above; only a genuinely new one (from a
    # non-block field) becomes a response-wide finding.
    for kind, content in _scan(other_text).items():
        unattributed = [item for item in content if item not in attributed]
        if unattributed:
            findings.append(_finding("", kind, unattributed))
    return findings


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
            if key not in _BLOCK_METADATA_KEYS
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
        "nextActions": payload.get("nextActions", []),
        # Block and task IDs are transport metadata, not customer-facing text.
        "responseBlocks": visible_response_blocks,
    }
    clarification = "\n".join(
        part for part in [clarification_text, rendered_payload["listClarificationText"]] if part
    )
    return answer_text, clarification, json.dumps(rendered_payload, ensure_ascii=False)


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
            "search_territory": evidence.get("search_territory", "in_scope"),
            "mapping_reason": evidence.get("mapping_reason", ""),
            # SUFFICIENT or RELEVANT -- a fact about the evidence (the
            # audit's own rules branch on it), not generation guidance.
            "resolution_status": evidence.get("resolution_status", "SUFFICIENT"),
            # Deliberately NOT "generation_guidance": that field is a
            # generation-side writing aid only (node_04b/node_04c), never
            # audit-visible. node_03c's own free text is unaudited -- if the
            # audit were told to enforce compliance WITH it, the audit would
            # stop being independent and would instead be rubber-stamping
            # whatever an unverified upstream string said. The Evidence
            # Applicability Rule below gives the audit its own, independent
            # way to catch the same class of problem directly from
            # approved_parents' own text and resolved_subquery above --
            # never trusting node_03c's summary of that comparison.
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

    # Same no-info entries _generate_response_blocks uses
    # (_no_info_entries): a task with zero usable evidence gets the
    # deterministic no_info_evidence treatment; a RELEVANT task is audited
    # as evidence via approved_by_task, whose entry carries its
    # resolution_status.
    task_metadata = {task_id: task.get("customer_component", "") for task_id, task in tasks_by_id.items()}
    no_info_context = _no_info_entries(
        state.get("task_resolution_instructions", []), task_metadata, state.get("unresolvable_search_tasks", [])
    )
    # _clarification_prompt_view, the exact same projection the generator's
    # own prompt uses -- so the audit can never check the response against
    # information (originator, clarification_reason) generation never saw.
    intent_context = _clarification_prompt_view(_build_clarification_context(state))
    decline_context = _build_decline_context(state)
    conversational_context = _build_conversational_context(state)
    return {
        "approved_by_task": approved_by_task,
        "response_block_map": response_block_map,
        "clarification_inputs": intent_context,
        "decline_inputs": decline_context,
        "no_info_inputs": no_info_context,
        "conversational_inputs": conversational_context,
        "available_shortcuts": _available_response_shortcuts(state),
        "unavailable_service_requests": _unavailable_service_requests(state),
        "verified_api_context": _verified_api_context(state),
        "personal_data_placeholders": _personal_data_placeholders(state),
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
            "generation_guidance": str(item.get("generation_guidance", "")).strip(),
        })
    return normalized


def _normalize_conduct_violations(raw_violations: Any) -> list[dict]:
    """Keep conduct-audit feedback structured, the same discipline
    `_normalize_grounding_violations` already applies to the evidence axis.
    `task_id` is new (Per-Task Outbound Response Revision Loop): "" means a
    genuinely response-wide conduct issue (e.g. overall persuasive tone) not
    traceable to a single claim -- see _partition_audited_response, which
    treats an empty task_id as forcing every block back to pending.
    """
    if not isinstance(raw_violations, list):
        return []
    normalized = []
    for item in raw_violations:
        if not isinstance(item, dict):
            continue
        unsupported_content = item.get("unsupported_content", [])
        if isinstance(unsupported_content, str):
            unsupported_content = [unsupported_content]
        if not isinstance(unsupported_content, list):
            unsupported_content = []
        normalized.append({
            "task_id": str(item.get("task_id", "")).strip(),
            "rule": str(item.get("rule", "bank_conduct")).strip() or "bank_conduct",
            "violation": str(item.get("violation", "")).strip(),
            "unsupported_content": [str(value).strip() for value in unsupported_content if str(value).strip()],
            "generation_guidance": str(item.get("generation_guidance", "")).strip(),
        })
    return normalized


def _partition_audited_response(
    response_blocks: list,
    conduct_violations: list,
    grounding_violations: list,
) -> tuple:
    """Pure, deterministic partition of one audit round's response_blocks --
    the sole authority over what's locked in vs. still open (Per-Task
    Outbound Response Revision Loop). Returns
    (approved_response_blocks, pending_task_ids, response_wide).

    A block's own `sourceTaskIds` list IS its addressability unit: every id
    it names is coupled for revision purposes. This is what keeps a
    scope-expansion companion pair (`task_1`/`task_1_scope`, sharing one
    block because the Scope-Expansion Honesty Rule requires them woven into
    one sentence) always revising together, and is also the deterministic
    backstop for any block that still blends two otherwise-independent
    task_ids: one violation on either id pulls the whole block -- and
    therefore every id it names -- back into pending_task_ids.

    A violation with an empty task_id can't be attributed to any single
    block (a genuinely response-wide issue): treat the whole round as
    response-wide -- nothing approved, every task_id pending.

    BUG-009: a violation whose task_id IS known but has no corresponding
    block at all (e.g. response generation itself failed and returned zero
    response_blocks) must also count as pending -- there is genuinely
    nothing approved for that task, regardless of whether a block exists to
    attribute the violation to. Without this, a real, correctly-attributed
    violation silently vanishes (the per-block loop below has nothing to
    iterate), pending_task_ids comes back empty, and the caller reads that
    as a full PASS despite the audit explicitly having found a problem --
    confirmed live during a Gemini high-demand spike that made generation
    return an empty response.

    BUG-038: a block is matched by every id it is known by -- its own
    blockId (deterministic per-block findings use it), its sourceTaskIds,
    its sourceShortcutHandles, and any id it cited that was not an
    obligation (sourceUnmatchedIds). A violation that still matches no
    block used to approve every block, including the one holding the
    flagged text, while the reviser was handed nothing; it is now treated
    as response-wide, so a flag can never approve the text it flags.
    (With no blocks at all -- BUG-009 -- that is the same outcome as
    before: nothing approved, the flagged ids pending.)
    """
    def _block_ids(block: dict) -> set:
        return {
            i for i in (
                block.get("blockId"),
                *(block.get("sourceTaskIds") or []),
                *(block.get("sourceShortcutHandles") or []),
                *(block.get("sourceUnmatchedIds") or []),
            ) if i
        }

    all_violations = [*conduct_violations, *grounding_violations]
    every_block_id = {i for block in response_blocks for i in _block_ids(block)}
    if any(not str(v.get("task_id", "")).strip() for v in all_violations):
        return [], every_block_id, True

    flagged = {v["task_id"] for v in all_violations if str(v.get("task_id", "")).strip()}
    approved, pending, matched = [], set(), set()
    for block in response_blocks:
        ids = _block_ids(block)
        if ids & flagged:
            pending.update(ids)
            matched.update(ids & flagged)
        else:
            approved.append(block)
    if flagged - matched:
        return [], every_block_id | flagged, True
    return approved, pending, False


def _group_violations_by_task_id(conduct_violations: list, grounding_violations: list, deterministic_findings: list | None = None) -> dict:
    """Group every axis's violations by task_id ("" = response-wide), each
    tagged with its own axis -- the audit's two probabilistic axes (conduct,
    grounding) and the deterministic output checks -- and feeds
    `task_violation_history`, the scoped feedback node_04c_response_reviser's
    prompt reads instead of draft_feedback_history's raw strings."""
    grouped: dict = {}
    for axis, violations in (("conduct", conduct_violations), ("grounding", grounding_violations), ("deterministic", deterministic_findings or [])):
        for violation in violations:
            task_id = str(violation.get("task_id", "")).strip()
            grouped.setdefault(task_id, []).append({**violation, "axis": axis})
    return grouped


def _reassemble_response_blocks(
    approved_response_blocks: list,
    original_response_blocks: list,
    pending_block_ids: set,
    revised_by_block_id: dict,
) -> list:
    """Deterministic splice, run BEFORE the next audit call -- this is what
    makes "the reviser cannot change what's approved" a structural
    guarantee rather than a prompt instruction. Walks
    original_response_blocks in their existing order (preserving reading
    order across approved/revised content); keeps an approved block
    verbatim; for a pending block, shallow-merges only the fields the
    reviser actually returned onto a COPY of the prior block -- a block_id
    the reviser silently dropped keeps its prior-round content unchanged
    rather than vanishing, exactly the omission failure mode this whole
    redesign exists to prevent. `blockId`/`type`/`sourceTaskIds` are
    identity/type-locked and are never taken from the reviser's output even
    if present (nor are the other citation fields). A block the reviser
    returned content for is marked `revised`: its citations were already
    checked when it was generated, and it has since been rewritten from
    exactly its own obligations, so the citation check is not repeated on
    it -- the reviser cannot change citations, so a repeated check could
    never pass and would only exhaust the retries.
    """
    approved_ids = {block.get("blockId") for block in approved_response_blocks}
    locked_fields = {"blockId", "type", "sourceTaskIds", "sourceShortcutHandles", "sourceUnmatchedIds", "revised"}
    result = []
    for block in original_response_blocks:
        block_id = block.get("blockId")
        if block_id in approved_ids or block_id not in pending_block_ids:
            result.append(block)
            continue
        revision = revised_by_block_id.get(block_id)
        if not revision:
            result.append(block)
            continue
        merged = dict(block)
        for key, value in revision.items():
            if key in locked_fields or value in (None, "", [], {}):
                continue
            merged[key] = value
        merged["revised"] = True
        result.append(merged)
    return result


def node_06_outbound_combined_audit_gate(state: AgentState):
    """Groundedness v2.0, both axes in one model call.

    Was two separate node_06 gates (compliance, groundedness), dispatched
    in parallel but each an independent call to the same underlying model
    (MODEL_B_AUDITOR) on the same draft. Combining them into one call
    removes a real correctness gap the parallel-but-separate design had,
    not just latency: a redraft only ever sees "what failed," never "what
    already passed," so fixing a flagged compliance violation could
    silently reintroduce a groundedness problem the previous draft had
    already cleared (or vice versa), with neither audit aware the other
    axis had regressed -- risking the safety-retry budget being spent
    oscillating between the two failure types rather than ever landing on
    a draft that satisfies both at once. One combined call judges both
    axes against the same draft simultaneously, so a rewrite instructed by
    this audit's feedback addresses both at once instead of trading one
    off against the other across turns.

    Every rule, evidence bundle, and output field below is copied
    verbatim from the two gates this replaces -- nothing about what is
    checked or how results are used downstream changed, only that it now
    happens in one call instead of two. Returns both
    outbound_compliance_result and outbound_groundedness_result in the
    same shape as before, so node_06_outbound_audit_join needed no changes
    at all.
    """
    print("🟢 [NODE 06] Executing Combined Outbound Compliance & Groundedness Audit")
    answer, clarification, rendered_payload = _outbound_response_content(state)
    is_non_financial_response = _is_pure_conversational_turn(state)

    conduct_route_specific_rules = """
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
    conduct_extra_output_fields = ', "answer_compliant": true/false, "clarification_compliant": true/false, "rewrite_instruction": "..."'

    audit_feedback_rules = """
    Audit-Feedback Rules:
    - grounding_violations is internal validation data, never customer-facing text.
    - Set a conduct_violations entry's task_id to the specific task_id/
      subquery_id this conduct issue traces back to, whenever it concerns a
      specific claim tied to one task -- use "" only for a genuinely
      response-wide issue (e.g. overall persuasive tone) not traceable to
      any single claim.
    - Set response_recovery_strategy to one of these internal constraints:
      * REMOVE_UNSUPPORTED_CONTENT when supported response content can be
        retained after removing the exact unsupported claim or instruction.
      * NARROW_TO_EVIDENCE_SCOPE when approved evidence is relevant but
        establishes only a conditional, partial, or narrower policy/process
        than the customer's broader question. Set this violation's own
        generation_guidance per the Generation Guidance Shape Rule's
        confirmed/unconfirmed split (case 2): explicitly instruct admitting,
        in the customer's own words, the specific broader element the
        evidence does NOT confirm (e.g. whether a named narrower procedure
        also covers the specific product/scenario the customer actually
        asked about) -- never silently drop it -- and, separately,
        instruct stating what the evidence DOES confirm. The confirmed
        remainder may keep whatever presentation its own content naturally
        calls for (a genuine evidence-backed procedure may still be
        instructed as a stepper; a policy fact as a plain statement) --
        what must never happen is presenting that confirmed content AS IF
        it resolves the customer's broader, unconfirmed question, inferring
        exclusivity, or inventing a new follow-up question unless
        independently supported -- this constrains only the answer's own
        content, never whether an already-authored Clarification Input for
        a different entry still gets relayed, which remains required
        exactly as on any other turn.
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
    - rewrite_instruction must name only the specific flagged claim or
      instruction and how to correct or remove it -- nothing more. Never
      restate, summarize, narrow, or imply the response's overall scope,
      completeness, or purpose; the next generation attempt's standing
      duties (addressing every Clarification/Decline/No-info entry,
      citation, style, etc.) are unaffected by this feedback and are not
      this field's job to reassert or redefine.
    - In addition to (never instead of) the diagnostic fields above, set
      every conduct_violations and evidence_violations entry's own
      generation_guidance per the Generation Guidance Shape Rule below --
      this is the one field on the entry that IS allowed to instruct what
      the next draft should write, scoped strictly to this entry's own
      flagged content.
    """
    recovery_strategy_field = ', "response_recovery_strategy":"NO_STRATEGY" | "REMOVE_UNSUPPORTED_CONTENT" | "NARROW_TO_EVIDENCE_SCOPE" | "CLARIFY_FROM_ALLOWED_CONTEXT"'

    def _run_combined_audit(evidence_bundle, evidence_description, evidence_route_specific_rules, evidence_extra_output_fields):
        """One call, both axes -- prompt bodies copied verbatim from
        check_conduct_compliance/check_evidence_groundedness (groundedness.py),
        just concatenated instead of issued as two separate calls."""
        evidence_text = evidence_bundle if isinstance(evidence_bundle, str) else json.dumps(evidence_bundle, ensure_ascii=False)
        prompt = f"""
        GROUNDEDNESS v2.0 — COMBINED CONDUCT & EVIDENCE AUDIT
        You are an independent conduct, regulatory compliance, and
        evidence-groundedness auditor for a Singaporean Bank, assessing a
        read-only Horizon Bank assistant's customer-facing content against
        TWO independent axes in this same pass. Assess each axis strictly
        on its own terms; a pass or failure on one axis never excuses,
        explains away, or is evidence toward the other axis's verdict.

        ============================================================
        AXIS 1 — CONDUCT & COMPLIANCE (evidence-independent)
        ============================================================
        Bank Conduct Rules:
        {BANK_CONDUCT_CONTEXT}

        Regulatory Context:
        {REGULATORY_LICENSING_CONTEXT}

        Language and emoji usage are outside the scope of this conduct
        axis. Evaluate only the semantic meaning and behavioral intent of
        the content, regardless of what language it is written in.

        {conduct_route_specific_rules}

        ============================================================
        AXIS 2 — EVIDENCE GROUNDEDNESS (factual axis)
        ============================================================
        {PRECISION_REFERENCE_CONTRACT}

        Every factual claim, instruction, figure, or suggestion in the
        Customer-Facing Content must be directly supported by the Permitted
        Evidence; nothing outside it may be used as a factual source. This
        axis never assesses conduct, tone, or regulatory compliance -- that
        is Axis 1 above, assessed independently. The Permitted Evidence,
        the rules that apply to it for this call, and the Customer-Facing
        Content are given at the end.

        Write each axis's own "_reason" field BEFORE its verdict field below,
        and reason it through there first -- a verdict decided first and
        explained afterward is a rationalization, not a judgment; reasoning
        that precedes the verdict is what actually shapes it.

        Write "conduct_reason" as compact shorthand, not full sentences.
        Every check must still be made -- brevity comes from wording only,
        never from skipping or merging a check: one short clause per Bank
        Conduct Rule and per regulatory point, naming the offending phrase
        when one exists and "none" when it clearly passes (e.g.
        "commitments: none; advice: none; ..."). Do not recite rule text or
        restate the answer.

        {_GENERATION_GUIDANCE_SHAPE_RULE}

        Output JSON only:
        {{"conduct_reason": "...", "conduct_compliant": true/false, "conduct_violations": [{{"rule": "bank_conduct" | "regulation", "task_id": "task_1" | "", "violation": "...", "unsupported_content": ["verbatim phrase"], "generation_guidance": "..."}}]{conduct_extra_output_fields}, "evidence_reason": "...", "evidence_grounded": true/false, "evidence_violations": [{{"scope": "...", "unsupported_content": ["verbatim phrase"], "evidence_gap": "...", "generation_guidance": "..."}}]{evidence_extra_output_fields}}}

        {_DATA_FOLLOWS}

        Axis 2 rules for this call:
        {evidence_route_specific_rules}

        Permitted Evidence ({evidence_description}):
        {evidence_text}

        ============================================================
        Customer-Facing Content (assess against BOTH axes):
        {content_block}
        """
        try:
            result = _parse_json_robustly(call_groq_lpu(prompt, model_id=MODEL_B_AUDITOR))
            result.setdefault("conduct_compliant", False)
            result.setdefault("conduct_violations", [])
            result.setdefault("conduct_reason", "Conduct compliance audit did not return a reason.")
            result.setdefault("evidence_grounded", False)
            result.setdefault("evidence_violations", [])
            result.setdefault("evidence_reason", "Evidence groundedness audit did not return a reason.")
            return result
        except Exception as error:
            print(f"🚨 [COMBINED OUTBOUND AUDIT ERROR]: {error}")
            return {
                "conduct_compliant": False,
                "conduct_reason": "Conduct compliance audit failed.",
                "conduct_violations": [],
                "evidence_grounded": False,
                "evidence_reason": "Evidence groundedness audit failed.",
                "evidence_violations": [{
                    "scope": "answer",
                    "unsupported_content": [],
                    "evidence_gap": "The groundedness audit was unavailable; no additional customer-facing facts or actions may be added.",
                }],
            }

    if is_non_financial_response:
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
        evidence_route_specific_rules = """
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
        result = _run_combined_audit(
            evidence_bundle="None — this route permits no factual evidence; only a courteous, non-material acknowledgement is grounded.",
            evidence_description="Non-Financial Conversation (no factual evidence permitted)",
            evidence_route_specific_rules=evidence_route_specific_rules,
            evidence_extra_output_fields=', "answer_bridge_grounded": true/false, "clarification_grounded": true/false, "clarification_connected": true/false',
        )
        audit_scope = "non_financial_response"
    else:
        grounding_context = _normal_outbound_grounding_context(state)
        approved_by_task = grounding_context["approved_by_task"]
        clarification_inputs = grounding_context["clarification_inputs"]
        decline_inputs = grounding_context["decline_inputs"]
        no_info_inputs = grounding_context["no_info_inputs"]
        conversational_inputs = grounding_context["conversational_inputs"]
        available_shortcuts = grounding_context["available_shortcuts"]
        unavailable_service_requests = grounding_context["unavailable_service_requests"]
        verified_api_context = grounding_context["verified_api_context"]
        personal_data_placeholders = grounding_context["personal_data_placeholders"]
        has_adjacent_scope_evidence = any(e.get("search_territory") == "adjacent_scope" for e in approved_by_task)
        has_clarification_family = bool(clarification_inputs or decline_inputs or no_info_inputs)

        # Response Obligation Contract Design, Stage 3: data sections and
        # their governing rule paragraphs are shown only when this turn
        # actually has an entry of that kind -- the same discipline
        # _generate_response_blocks' Stage 1/2 restructuring already
        # established on the generation side, applied here with NO change
        # to any verdict criteria, evidence source, or independence
        # guarantee: an absent section never changes what would have been
        # checked, it only stops an entirely-unused rule from padding every
        # audit call regardless of what the turn actually contains. This is
        # a pure prose-organization change -- deliberately does NOT adopt
        # the design doc's original Stage 3 sketch (checking consistency
        # against an entry's own generation_guidance), which would have
        # reopened the audit-independence gap _normal_outbound_grounding_
        # context's own comment above already explains and rejects.
        content_sections = []
        if clarification_inputs:
            content_sections.append(
                "Clarification Inputs (the only source for what a clarifying "
                "question may be about -- exactly what the generator itself "
                "was given, nothing more: \"scope\" names where in the "
                "pipeline the ambiguity blocks progress, \"unresolved_request\" "
                "is the specific thing that's unclear -- the generator "
                "composed its own question from this, there is no fixed "
                "sentence to compare against -- and 0-3 \"clarification_guesses\" "
                "are PREDICTIONS of likely customer intent, never reports of "
                "anything actually said):\n" + json.dumps(clarification_inputs, ensure_ascii=False)
            )
        if decline_inputs:
            content_sections.append(
                "Decline Inputs (the only source for a declined-topic "
                "explanation):\n" + json.dumps(decline_inputs, ensure_ascii=False)
            )
        if no_info_inputs:
            content_sections.append(
                "No-Info Inputs (the only source for an unavailable-"
                "information statement):\n" + json.dumps(no_info_inputs, ensure_ascii=False)
            )
        if conversational_inputs:
            content_sections.append(
                "Conversational Inputs (each is a non-financial part of this "
                "turn -- a greeting, thanks, feeling, or off-topic remark -- "
                "the generator composed its own brief reply from its "
                "\"customer_component\", there is no fixed sentence to "
                "compare against):\n" + json.dumps(conversational_inputs, ensure_ascii=False)
            )
        content_sections_block = "\n\n".join(content_sections) if content_sections else "None this turn."

        content_block = f"""
        Original Customer Message (courtesy-context only; never a source for a
        banking fact, instruction, product capability, policy, or outcome):
        {json.dumps(state.get("user_input", ""), ensure_ascii=False)}

        Optional Internal Response-Element Provenance (not customer-facing):
        {json.dumps(grounding_context["response_block_map"], ensure_ascii=False)}

        {content_sections_block}

        Customer-Facing Answer:
        "{answer}"

        Customer-Facing Clarification:
        "{clarification}"

        Complete Customer-Facing UI Payload:
        "{rendered_payload}"
        """

        _core_grounding_rules = """
        ANSWER AUDIT: assess the customer's natural answer as one coherent
        response. Do NOT require it to be written in separate factual and
        inferential sections. Instead, distinguish the following two internal
        checks while reading the same natural answer and answer UI.

        1. Direct Factual Grounding (Evidence Check)
        - Every explicit factual assertion, parameter, eligibility rule,
          product capability, policy condition, limit, date, amount,
          placeholder, instruction, option, progress step, list item,
          extended-text statement, and answer response-block field must be
          directly supported by the Permitted Evidence below: Task-Level
          Approved Evidence, or one of the tool-derived entries listed with
          it (available shortcuts, unavailable service requests, verified
          customer-specific facts, personal data references), within exactly
          what the Source-Boundary Rules below allow that entry to support.
          A statement a tool-derived entry supports on those terms is
          grounded even though no Task-Level Approved Evidence mentions it.
        - Assess the answer and answer UI as one coherent customer response
          against the aggregate Task-Level Approved Evidence. The response does
          not need a visible section, element, or title for each task.
        - When an optional response element is mapped to source_task_ids, its
          factual content may use the union of approved evidence for those
          tasks. An unmapped element -- including one that cites only a
          tool-derived entry (e.g. a personal data reference), which never
          appears in the task mapping -- may use any Permitted Evidence, on
          the same Source-Boundary terms.
        - Every Task-Level Approved Evidence entry has a non-empty
          "approved_parents" (RELEVANT and SUFFICIENT both require at least
          one real approved parent; a task with none is IRRELEVANT and
          never appears in this bundle at all) -- so an entry's mere
          presence here is a genuine signal that real evidence exists for
          it, not something to independently doubt.
        - Each entry's "resolution_status" says how completely its approved
          parents answer its request: SUFFICIENT (they answer it) or
          RELEVANT (they connect to it without fully resolving it). For a
          RELEVANT entry, a neutral statement that the available
          information does not fully resolve its named request is
          grounded; it must not state, suggest, or imply a banking fact,
          procedure, product alternative, policy, or next action unless
          supported by Task-Level Approved Evidence.
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
        """

        _courtesy_context_rule = """
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
          the Permitted Evidence, exactly as Direct Factual Grounding defines
          it. The Original Customer Message can never support, soften, or
          justify a financial claim.
        """

        _scope_expansion_honesty_rule = """
        Scope-Expansion Honesty Rule (Extra Effort v2.0):
        - A Task-Level Approved Evidence entry whose "search_territory" is
          "adjacent_scope" was retrieved from a deliberately different
          product/topic than the customer's own task. When its
          "resolution_status" is RELEVANT, by construction it is related information, not a
          literal answer to that task's `customer_component`. Its facts are
          a permitted source, but only for content that is visibly
          introduced as related or additional information (e.g. "I don't
          have that exact detail, but here's related information about X
          that may help:") -- never phrased as if it directly answers the
          customer's stated request.
        - When an "adjacent_scope" entry's "resolution_status" is instead SUFFICIENT,
          the sufficiency auditor has judged this different product/intent
          to genuinely be the right match for the customer's need (typically
          because the original in_scope mapping was wrong) -- its facts may
          be presented as a real, direct answer, provided the response names
          the product/intent it actually concerns rather than silently
          answering as if it matched the customer's literal wording.
        - Reject as a `wrong_task_evidence` violation any visible claim that
          draws on a RELEVANT "adjacent_scope" entry's facts while
          presenting them as the direct, literal answer to that task, or
          that blends them into the same sentence as an "in_scope" (or
          absent search_territory) entry's answer without preserving the
          related-information framing. The underlying facts may still be
          correct and traceable -- the violation is the missing or
          erased distinction, not a fabricated fact. This does not apply to
          a SUFFICIENT "adjacent_scope" entry presented per the rule above.
        - That entry's own "mapping_reason" is the ONLY permitted basis for
          a bridging sentence explaining WHY this related topic is being
          offered (e.g. connecting a car purchase to a downpayment to a
          Savings Account) -- a bridge is grounded only when it is a
          bounded restatement of that entry's own mapping_reason, visibly
          hedged as the assistant's own suggestion/possibility ("you may
          want to...", "since you might be...") rather than asserted as a
          stated bank fact or policy. Reject as an
          `unsupported_bridge_inference` violation a bridge that states a
          different or stronger connection than mapping_reason itself
          supports, or that drops the hedge and asserts the connection as
          fact. A response is not required to include a bridge sentence at
          all -- absence of one is never itself a violation -- but a
          missing connecting sentence next to an unexplained topic switch
          should be weighed under the general answer-quality expectations,
          not this rule specifically.
        - An entry with "search_territory" absent or "in_scope" is graded
          exactly as before this rule; this rule only ever narrows how an
          "adjacent_scope" entry may be used, never how the others are.
        """

        _evidence_applicability_rule = """
        Evidence Applicability Rule (judge this yourself, directly from
        approved_parents' own text and resolved_subquery below -- never
        from any writer-side note, since nothing upstream of you here has
        itself been audited):
        - An approved parent's own stated premise, question, or
          circumstance can differ from the customer's own stated situation
          even when its content is otherwise directly relevant -- e.g. a
          card-replacement parent whose own question and steps are written
          entirely for a PHYSICALLY DAMAGED card, approved for a customer
          who instead stated they dislike the card's appearance. Compare
          the evidence's own stated circumstance against the customer's own
          stated circumstance (Original Customer Message / this task's
          resolved_subquery) directly -- never assume they match merely
          because the evidence was approved for this task.
        - Reject as a `task_not_answered` violation any visible content
          that presents such a procedure, condition, or step as the
          customer's own direct path with no acknowledgment of the
          mismatch -- e.g. instructing the customer to declare a
          circumstance (such as damage) their own stated situation does
          not support. The underlying steps may be entirely correct and
          traceable to evidence -- the violation is presenting them as a
          clean match to THIS customer's situation when the evidence's own
          premise says otherwise.
        - ACKNOWLEDGING THE MISMATCH DOES NOT FIX IT -- do not treat mere
          acknowledgment as satisfying this rule. Also reject as a
          `wrong_task_evidence` violation any visible content that, after
          noting the mismatch, goes on to assert, imply, or reassure the
          customer that the SAME procedure "can be used," "works," "still
          applies," or "is the same" for their actual different reason --
          that claim of accommodation is itself a new, unsupported
          procedural fact no parent here states, not a caveat. A
          compliant response states the mismatch plainly and stops there
          (e.g. "this process is documented for a damaged card; if that
          doesn't match your situation, you can still use the form below
          and note your actual reason") -- it never goes on to declare the
          gap resolved.
        - This rule imposes no requirement when the evidence's own stated
          circumstance already matches, or is neutral/generic with respect
          to, the customer's own stated situation -- only an actual,
          evidenced mismatch triggers it.
        - When this rule fires, set the violation's generation_guidance per
          the Generation Guidance Shape Rule above: since the mismatch means
          nothing here confirms the procedure applies to the customer's
          actual stated reason, the instruction is the "nothing confirmed"
          case -- admit that plainly, phrased around the customer's own
          stated reason, then instruct offering the procedure only as an
          unclaimed courtesy, never as a resolution.
        """

        _clarification_family_rules = """
        Clarification Rules:
        - Each Decline Input and No-Info Input carries its own
          `decline_evidence`/`no_info_evidence` field -- the sole permitted
          source for that entry's content, and already the complete, final
          statement for it. Unlike Task-Level Approved Evidence (which the
          answer is composed FROM), these two are never raw material to
          reason from or build a new conclusion on top of -- a visible
          statement about one of these entries must faithfully convey only
          what its own evidence field says (verbatim or a natural
          paraphrase preserving its exact meaning), never inferring a
          product, policy, or banking fact beyond it.
        - A Clarification Input has no fixed evidence field -- the
          generator composed its own question from `unresolved_request`
          and `scope`. Grade it the way you would a bridge inference from
          real evidence, not a paraphrase-of-a-fixed-sentence: it is
          grounded when it clearly asks about `unresolved_request` (or
          something the customer would recognize as the same ambiguity),
          stays inside its own `scope` (never a judgment call that belongs
          to a different job), and does not assert a new product, policy,
          or fact, or offer an option beyond that entry's own
          `clarification_guesses`.
        - A Clarification Input's statement must not attempt to
          answer the request. A Decline Input's statement must never
          provide the declined advice, action, or information itself. A
          No-Info Input's statement must not imply a banking fact,
          procedure, product alternative, policy, or next action beyond
          what its own `no_info_evidence` says.
        - A generic apology and a general request for clarification are grounded.
        - A clarification following an answer must identify its unresolved term
          instead of using an ambiguous reference such as "that" or "it".
        - A Clarification Input's own clarification_guesses each carry their
          own internal `reason` (never customer-facing) -- check that
          reason itself, not just the label/message: it must state why
          this is a plausible customer intention given the current
          context -- Trusted Conversation Context, the Original Customer
          Message, or -- when `scope` describes reviewing retrieved
          evidence rather than identifying a product/intent -- Task-Level
          Approved Evidence / the candidate parents reviewed for that task.
          A guess may additionally ground in a Product/Intent Matrix fact
          only when `scope` describes product/intent identification; when
          `scope` describes evidence review, a guess may never invent a
          Matrix fact of its own, since that job is evidence review, not
          product classification. Each guess is a PREDICTION, never a
          report of anything the customer actually said -- a guess that
          does not fit the current context is unsupported content, exactly
          as if the guess itself were ungrounded, and so is the guess's
          label/message if it introduces a new product, policy, fact, or
          answer beyond what its reason actually supports.
        """

        _conversational_rules = """
        Conversational Rules:
        - A Conversational Input has no evidence-backed content -- it is
          grounded when it faithfully and naturally responds to its own
          `customer_component` (a greeting, thanks, feeling, or off-topic
          remark) without asserting a new banking fact, procedure, product,
          policy, commitment, or next action. Never fail it merely for
          being absent from Task-Level Approved Evidence -- the same
          principle as the Courtesy Context Rule above, now keyed to a
          real entry instead of the raw Original Customer Message.
        - A Conversational Input's statement must not answer, or appear to
          answer, a banking question -- if it does, treat it as an
          unsupported claim, the same as any other invented fact.
        """

        # Not folded into either family-specific block above because it
        # spans both -- worded so it stays correct regardless of which of
        # the two (or neither, or both) actually rendered this turn.
        _cross_type_coverage_rule = """
        Coverage Rule: every entry among this turn's Decline Inputs,
        Clarification Inputs, No-Info Inputs, and Conversational Inputs
        (whichever are present below) must be explicitly, faithfully
        represented somewhere in the response, not silently omitted --
        treat a missing entry the same as an unsupported claim for
        grounding purposes.
        """

        _audit_feedback_reporting_mechanics = """
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
        """

        _source_boundary_core_rules = """
        Source-Boundary Rules:
        - Session memory, user baseline memory, chat history, and rolling
          summaries are intentionally absent and are not grounding sources.
          The Original Customer Message is supplied only for the narrow
          courtesy-context rule above; it is never evidence for financial
          content.
        - Do not fail a response because it omits information from any available
          source. Assess whether visible claims use a permitted source only.
        - Every visible field in the payload is in scope.
        - "nextActions" entries (0-3 short next-question suggestions) must
          each be traceable to a specific fact or sub-topic actually present
          in Task-Level Approved Evidence for this turn -- reject one only
          if it names a product, policy, or topic with no basis anywhere in
          that evidence. Unlike the answer itself, a next-action is
          intentionally speculative about the customer's *interest*, never
          about whether the underlying content exists -- so it is not held
          to the same standard as an assertion of fact, only to "is this
          genuinely something the approved evidence could answer."
        """

        _shortcut_source_rule = """
        - "available_shortcuts" entries are a permitted source for a
          statement, in either of two roles, that: names a shortcut solely
          by its supplied `label`/`topic`; and never claims the assistant is
          executing, attaching, opening, or completing anything itself
          (whether a button actually appears is decided after this audit
          runs, but the statement itself only asserts that an option
          exists, which the supplied entry does support).
          * Closing role: a brief sentence, alongside an evidence-based
            answer, framing the shortcut as an alternative or faster path
            that does not replace or invalidate that answer.
          * Primary-answer role: when a task/subquery has no approved
            evidence at all, a shortcut whose supplied `for_request` matches
            that same subquery may be presented as the direct, confident
            answer to it -- this is not required to be brief or closing, and
            is not a decline needing apology framing.
          Reject a statement only if it names a shortcut not present in
          "available_shortcuts", invents a detail beyond the supplied
          label/topic/for_request (a step, field, screen layout, url, count,
          or id), claims a shortcut replaces or invalidates a *different*
          evidence-based answer than the one its own for_request names, or
          claims execution/completion. Do not reject a compliant statement
          merely for existing, and do not require decline/apology framing
          for a subquery whose only coverage is a matching shortcut.
        """

        _unavailable_source_rule = """
        - "unavailable_service_requests" entries are a permitted source only
          for a brief sentence stating that part of the request could not be
          started, using only the supplied reason. Any different explanation
          is unsupported.
        """

        _verified_api_source_rule = """
        - "verified_api_context" entries are a permitted factual source, on
          the same footing as Task-Level Approved Evidence, but each entry
          supports only the exact statement in its own `fact` text -- no
          extrapolation beyond it.
        """

        # Plain string, not an f-string, so {{data-*}} renders as written --
        # the real token shape, consistent with PERSONAL_DATA_REFERENCE_
        # CONTRACT. (Before Stage 3 this text lived inside an f-string that
        # halved it to a misleading single-brace {data-*}.)
        _personal_data_source_rule = """
        - "personal_data_placeholders" entries permit a customer-facing
          sentence to include an entry's `insert_token` verbatim when the
          sentence addresses that same entry's `for_request`. The sentence
          must never state, paraphrase, or guess the real value the token
          represents (it is never disclosed to the assistant, so it can
          never legitimately appear as prose) and must never use a
          `{{data-*}}` token that is not present in this list.
        """

        rule_sections = [_core_grounding_rules, _courtesy_context_rule]
        if has_adjacent_scope_evidence:
            rule_sections.append(_scope_expansion_honesty_rule)
        if approved_by_task:
            rule_sections.append(_evidence_applicability_rule)
        if has_clarification_family:
            rule_sections.append(_clarification_family_rules)
        if conversational_inputs:
            rule_sections.append(_conversational_rules)
        if has_clarification_family or conversational_inputs:
            rule_sections.append(_cross_type_coverage_rule)
        rule_sections.append(audit_feedback_rules)
        rule_sections.append(_audit_feedback_reporting_mechanics)
        source_boundary_sections = [_source_boundary_core_rules]
        if available_shortcuts:
            source_boundary_sections.append(_shortcut_source_rule)
        if unavailable_service_requests:
            source_boundary_sections.append(_unavailable_source_rule)
        if verified_api_context:
            source_boundary_sections.append(_verified_api_source_rule)
        if personal_data_placeholders:
            source_boundary_sections.append(_personal_data_source_rule)
        rule_sections.append("\n".join(source_boundary_sections))
        evidence_route_specific_rules = "\n\n".join(rule_sections)
        # Per-Task Outbound Response Revision Loop: a noise-reduction hint
        # only, never a constraint. These task_ids already passed a full
        # audit round earlier this turn; naming them here should stop the
        # auditor from re-flagging one merely because it's re-reading the
        # whole response fresh, without giving it any reason to suppress a
        # genuine new finding -- _partition_audited_response honors such a
        # finding unconditionally if one is reported anyway.
        previously_approved_task_ids = sorted({
            task_id
            for block in state.get("approved_response_blocks", [])
            for task_id in block.get("sourceTaskIds", [])
        }) if state.get("safety_retry_count", 0) > 0 else []
        result = _run_combined_audit(
            evidence_bundle={
                "approved_by_task": grounding_context["approved_by_task"],
                "available_shortcuts": grounding_context["available_shortcuts"],
                "unavailable_service_requests": grounding_context["unavailable_service_requests"],
                "verified_api_context": grounding_context["verified_api_context"],
                "personal_data_placeholders": grounding_context["personal_data_placeholders"],
                "previously_approved_task_ids_this_turn": previously_approved_task_ids,
                "previously_approved_task_ids_note": (
                    "Informational noise-reduction hint only, NOT a constraint -- these already "
                    "passed a full audit in an earlier round of this same turn. Do not re-flag one "
                    "merely to be thorough, but if you find a genuine new problem with one, report "
                    "it exactly as you would any other violation; this list must never suppress a "
                    "real finding."
                ) if previously_approved_task_ids else "",
            },
            evidence_description="Task-Level Approved Evidence, Available Shortcuts, Unavailable Service Requests, Verified Customer-Specific Facts, and Personal Data References — the only factual sources for answers and answer UI",
            evidence_route_specific_rules=evidence_route_specific_rules,
            evidence_extra_output_fields=f', "answer_evidence_grounded": true/false, "answer_bridge_grounded": true/false, "clarification_grounded": true/false, "clarification_connected": true/false{recovery_strategy_field}, "grounding_violations": [{{"task_id": "task_1", "scope": "answer" | "ui_payload" | "clarification", "audit_layer": "evidence" | "bridge" | "other", "violation_type": "unsupported_factual_claim" | "unsupported_bridge_inference" | "unsupported_instruction" | "wrong_task_evidence" | "unsupported_clarification_suggestion" | "missing_decline_reference" | "task_not_answered" | "other", "unsupported_content": ["verbatim customer-facing phrase"], "evidence_gap": "missing permitted evidence only; no proposed remedy", "generation_guidance": "..."}}]',
        )
        audit_scope = "normal_response"

    # --- Compliance axis (from the same combined `result`) ---
    answer_compliant = bool(result.get("answer_compliant", False))
    clarification_compliant = bool(result.get("clarification_compliant", False))
    compliance_passed = bool(result.get("conduct_compliant", False)) and answer_compliant and clarification_compliant
    compliance_audit = {
        "passed": compliance_passed,
        "answer_compliant": answer_compliant,
        "clarification_compliant": clarification_compliant,
        "reason": result.get("conduct_reason", "Compliance audit failed."),
        "rewrite_instruction": result.get("rewrite_instruction", "Rewrite the response to comply with banking regulations."),
        "conduct_violations": _normalize_conduct_violations(result.get("conduct_violations", [])),
    }
    log_event("outbound.compliance.completed", passed=compliance_passed, reason=compliance_audit["reason"])

    # --- Groundedness axis (from the same combined `result`) ---
    answer_grounded = bool(result.get("evidence_grounded", False))
    answer_evidence_grounded = bool(result.get("answer_evidence_grounded", answer_grounded))
    answer_bridge_grounded = bool(result.get("answer_bridge_grounded", answer_grounded))
    clarification_grounded = bool(result.get("clarification_grounded", False))
    clarification_connected = bool(result.get("clarification_connected", False))
    if audit_scope == "normal_response":
        answer_grounded = answer_grounded and answer_evidence_grounded and answer_bridge_grounded
    groundedness_passed = answer_grounded and clarification_grounded and clarification_connected

    grounding_violations = _normalize_grounding_violations(result.get("grounding_violations", result.get("evidence_violations", [])))
    response_recovery_strategy = str(result.get("response_recovery_strategy", "NO_STRATEGY")).upper()
    if response_recovery_strategy not in {
        "NO_STRATEGY",
        "REMOVE_UNSUPPORTED_CONTENT",
        "NARROW_TO_EVIDENCE_SCOPE",
        "CLARIFY_FROM_ALLOWED_CONTEXT",
    }:
        response_recovery_strategy = "NO_STRATEGY"

    groundedness_audit = {
        "passed": groundedness_passed,
        "answer_grounded": answer_grounded,
        "answer_evidence_grounded": answer_evidence_grounded,
        "answer_bridge_grounded": answer_bridge_grounded,
        "clarification_grounded": clarification_grounded,
        "clarification_connected": clarification_connected,
        "response_recovery_strategy": response_recovery_strategy,
        "reason": result.get("evidence_reason", "Groundedness audit failed."),
        "grounding_violations": grounding_violations,
        # Per-Task Outbound Response Revision Loop: lets node_06_outbound_
        # audit_join branch between the new per-task partition path
        # (normal_response) and the unchanged whole-response path (every
        # other scope) without re-deriving _is_pure_conversational_turn.
        "audit_scope": audit_scope,
    }
    log_event(
        "outbound.groundedness.completed",
        scope=audit_scope,
        passed=groundedness_passed,
        reason=groundedness_audit["reason"],
        answer_evidence_grounded=answer_evidence_grounded,
        answer_bridge_grounded=answer_bridge_grounded,
        grounding_violation_count=len(grounding_violations),
        response_recovery_strategy=response_recovery_strategy,
    )
    return {"outbound_compliance_result": compliance_audit, "outbound_groundedness_result": groundedness_audit}


def _record_answer_cache_outcomes(state: AgentState) -> None:
    """Fairness/consistency mitigation write path (BUG-023, BUG-025):
    once a turn's OWN outbound compliance/groundedness audit has passed --
    a stronger bar than the retrieval-level task status alone, chosen
    because BUG-015 showed task-level SUFFICIENT/RELEVANT can be wrong
    even when the customer-facing turn actually failed -- record each
    task's (question text, approved parent ids) outcome into the
    cross-session answer cache, and credit or blame any cache-suggested
    candidates this task consulted this round.

    Deliberately best-effort: a cache write is a side effect that
    improves future fairness, never something this turn's own response
    depends on. It must never be able to fail or slow down the turn the
    customer is actually waiting on.
    """
    try:
        tasks_by_id = {task.get("task_id"): task for task in state.get("search_tasks", [])}
        cache_injected = state.get("task_cache_injected_parent_ids", {})
        cache = get_answer_cache()
        for result in state.get("task_results", []):
            task_id = result.get("task_id")
            task = tasks_by_id.get(task_id)
            if not task:
                # Skips scope-expansion companion tasks (e.g. "task_1_scope")
                # and any task without a real subquery behind it -- only the
                # customer's own primary question is cache-eligible.
                continue
            text = task.get("direct_query") or task.get("customer_component") or ""
            if not text:
                continue
            surfaced_doc_ids = [hit["doc_id"] for hit in cache_injected.get(task_id, [])]
            cache.record_outcome(
                query_text=text,
                approved_parent_ids=result.get("approved_parent_ids", []),
                surfaced_doc_ids=surfaced_doc_ids,
            )
    except Exception as exc:  # noqa: BLE001 -- never let a cache side effect break a live turn
        print(f"   ⚠️ [ANSWER CACHE]: record_outcome failed, continuing without it: {exc}")
        log_event("answer_cache.record_outcome.failed", error=str(exc))


def node_06_outbound_audit_join(state: AgentState):
    """Join focused audits and retain the existing retry/degradation semantics.

    Per-Task Outbound Response Revision Loop: a `normal_response`-scope
    turn (real `search_tasks`/task_id decomposition) uses a deterministic
    per-task partition instead of a single whole-response pass/fail -- see
    `_partition_audited_response`. Every other scope (non-financial/
    evidence-only) keeps the original whole-response retry logic
    byte-for-byte below, since those turns are short/single-element and
    don't have the multi-independent-task structure this redesign targets.
    """
    print("🟢 [NODE 06] Joining Outbound Compliance & Groundedness Audits")
    compliance = state.get("outbound_compliance_result", {})
    groundedness = state.get("outbound_groundedness_result", {})
    audit_scope = groundedness.get("audit_scope", "normal_response")
    compliance_passed = bool(compliance.get("passed", False))
    groundedness_passed = bool(groundedness.get("passed", False))

    # The deterministic output checks (formerly node_05's own pre-audit
    # gate) run here, after the audit, so every problem in this draft --
    # probabilistic or deterministic -- is collected before deciding what
    # needs revising, and the reviser fixes all of it in one call.
    deterministic_findings = _deterministic_outbound_findings(state, check_citations=audit_scope == "normal_response")
    log_event(
        "outbound.deterministic.completed",
        passed=not deterministic_findings,
        findings=[
            {"task_id": f["task_id"], "violation_type": f["violation_type"], "unsupported_content": f["unsupported_content"]}
            for f in deterministic_findings
        ],
    )
    if deterministic_findings:
        print(f"   🚫 [DETERMINISTIC CHECK]: {[(f['task_id'] or '<response-wide>', f['violation_type'], f['unsupported_content']) for f in deterministic_findings]}")

    if audit_scope == "normal_response":
        response_blocks = state.get("ui_action_payload", {}).get("responseBlocks", [])
        conduct_violations = compliance.get("conduct_violations", [])
        grounding_violations = groundedness.get("grounding_violations", [])
        # One partition over every finding, whichever method produced it: a
        # block is approved only if neither the audit nor the deterministic
        # checks flagged any task id it names.
        approved_blocks, pending_ids, response_wide = _partition_audited_response(
            response_blocks, conduct_violations, [*grounding_violations, *deterministic_findings]
        )

        if not pending_ids and not response_wide:
            print("   ✅ [OUTBOUND AUDIT PASS]: Response is MAS-compliant and evidence-grounded.")
            _record_answer_cache_outcomes(state)
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
            # BUG-036: log only a record THIS turn appended -- otherwise the
            # previous turn's record would be re-logged as if this turn had
            # recorded it.
            if len(verified_task_history) > len(state.get("verified_task_history", [])):
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
                "approved_response_blocks": response_blocks,
                "pending_task_ids": [],
                "response_wide_violation_this_round": False,
                "task_violation_history": {},
            }

        count = state.get("safety_retry_count", 0) + 1
        print(f"   🚫 [OUTBOUND AUDIT VETO -- PARTIAL]: pending_task_ids={sorted(pending_ids)} response_wide={response_wide} round={count}")
        # Strictly more precise than the old whole-answer preserve_answer
        # heuristic (kept below for the other scope): approved_blocks is,
        # by construction, exactly the content with zero violations this
        # round, so it's always safe to surface on eventual exhaustion.
        safe_answer = "\n\n".join(
            p for p in (_flatten_response_block_text(b) for b in approved_blocks if b.get("type") != "clarification") if p
        )
        return {
            "safety_retry_count": count,
            "preflight_status": "fail",
            "pending_advance": False,
            "approved_response_blocks": approved_blocks,
            "pending_task_ids": sorted(pending_ids),
            "response_wide_violation_this_round": response_wide,
            "task_violation_history": _group_violations_by_task_id(conduct_violations, grounding_violations, deterministic_findings),
            "safe_answer_for_fallback": safe_answer,
        }

    # --- Every other audit scope: original whole-response logic, unchanged ---
    answer_text = state.get("final_answer", state.get("final_draft", ""))
    answer_compliant = bool(compliance.get("answer_compliant", False))
    answer_grounded = bool(groundedness.get("answer_grounded", False))
    clarification_failed = (
        not groundedness.get("clarification_grounded", False)
        or not groundedness.get("clarification_connected", False)
        or not bool(compliance.get("clarification_compliant", False))
    )
    if not (compliance_passed and groundedness_passed) or deterministic_findings:
        count = state.get("safety_retry_count", 0) + 1
        if _is_pure_conversational_turn(state) and count >= 3:
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
        if deterministic_findings:
            failed_feedback.append(
                "Deterministic output check violations (internal constraints only; never evidence): "
                + json.dumps(deterministic_findings, ensure_ascii=False)
            )
        history = list(state.get("draft_feedback_history", []))
        history.extend(failed_feedback)
        unresolved_clarification = bool(state.get("task_resolution_instructions") or state.get("clarification_tasks") or state.get("decline_tasks") or state.get("unresolvable_search_tasks"))
        preserve_answer = (
            unresolved_clarification
            and clarification_failed
            and answer_compliant
            and answer_grounded
            # Never keep an answer the deterministic checks just flagged as
            # the exhaustion fallback -- it would ship that exact problem.
            and not deterministic_findings
        )
        safe_answer = answer_text if preserve_answer and answer_text.strip() else state.get("safe_answer_for_fallback", "")
        print(f"   🚫 [OUTBOUND AUDIT VETO]: Compliance={compliance_passed} | Groundedness={groundedness_passed}")
        return {
            "safety_retry_count": count,
            "draft_feedback_history": history,
            "preflight_status": "fail",
            "pending_advance": False,
            "safe_answer_for_fallback": safe_answer,
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
    # BUG-036: log only a record this turn appended (see the normal_response
    # branch above).
    if len(verified_task_history) > len(state.get("verified_task_history", [])):
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
    }


def node_07_precision_substitution(state: AgentState):
    print("🟢 [NODE 07] Executing Precision Substitution")
    draft = state.get("final_draft", "")
    payload = state.get("ui_action_payload", {})
    # The response has already passed the compliance / groundedness audit by
    # the time it reaches here. Work out, before any substitution, which
    # block each deterministic card should follow; only ids, never text.
    action_anchors = _shortcut_anchor_block_ids(state, payload.get("responseBlocks", []))
    options = payload.get("options", [])
    step_details = payload.get("stepDetails", [])
    list_items = payload.get("items", []) if payload.get("uiType") == "checklist" else []
    list_title = payload.get("listTitle", "")
    list_intro = payload.get("listIntro", "")
    extended_text = payload.get("extendedText", "")
    list_clarification = payload.get("listClarificationText", "")
    response_blocks = payload.get("responseBlocks", [])

    # BUG-014: restore the {{ }} wrapper on any malformed precision
    # placeholder whose exact id is in this turn's approved evidence, before
    # anything below looks for placeholders -- so it gets substituted like
    # one the generator wrapped correctly. Here rather than in node_05
    # because node_11's exhausted-retry fallback reaches this node without
    # passing through node_05. Repair only; an id outside the evidence is
    # left for the unauthorized-placeholder check.
    evidence_placeholder_ids = _evidence_precision_placeholders(state)
    if evidence_placeholder_ids:
        repaired_ids: set = set()
        draft = _repair_precision_placeholders(draft, evidence_placeholder_ids, repaired_ids)
        options = _repair_precision_placeholders(options, evidence_placeholder_ids, repaired_ids)
        step_details = _repair_precision_placeholders(step_details, evidence_placeholder_ids, repaired_ids)
        list_items = _repair_precision_placeholders(list_items, evidence_placeholder_ids, repaired_ids)
        list_title = _repair_precision_placeholders(list_title, evidence_placeholder_ids, repaired_ids)
        list_intro = _repair_precision_placeholders(list_intro, evidence_placeholder_ids, repaired_ids)
        extended_text = _repair_precision_placeholders(extended_text, evidence_placeholder_ids, repaired_ids)
        list_clarification = _repair_precision_placeholders(list_clarification, evidence_placeholder_ids, repaired_ids)
        response_blocks = _repair_precision_placeholders(response_blocks, evidence_placeholder_ids, repaired_ids)
        if repaired_ids:
            print(f"   🩹 [PLACEHOLDER REPAIRED]: restored {{{{ }}}} on {sorted(repaired_ids)}")
            log_event("precision.placeholder.repaired", level="WARNING", placeholder_ids=sorted(repaired_ids))

    # Extract unique placeholders globally
    combined_text = " ".join([draft, *_ui_option_texts(options), *step_details, *list_items, list_title, list_intro, extended_text, list_clarification, json.dumps(response_blocks, ensure_ascii=False)])
    placeholders_in_draft = list(set(re.findall(r"\{\{(.*?)\}\}", combined_text)))
    precision_substituted = False
    
    # Deeplink navigation is no longer placeholder-driven: the button is
    # built deterministically further below, straight from tool_attempts,
    # exactly like serviceRequestActions, regardless of the draft text. This
    # block is only a defensive cleanup for a stray {{deeplink-*}} /
    # {deeplink-*} token that should no longer be generated at all (the
    # response-generator prompt no longer asks for one) but historically
    # leaked to the customer when it appeared -- it is never trusted for
    # anything and is stripped unconditionally rather than substituted.
    deeplink_matches = re.findall(r"\{{1,2}deeplink-([^}]+)\}{1,2}", combined_text)
    if deeplink_matches:
        destination_id = deeplink_matches[0]
        print(f"   🚫 [STRAY DEEPLINK TOKEN]: '{destination_id}' found in generated text -- stripping (navigation buttons are attached deterministically, not via this token).")

        # Clean both single and double bracket forms from ALL text fields globally
        def _strip_deeplink(obj):
            if isinstance(obj, str):
                return re.sub(r"\{{1,2}deeplink-" + destination_id + r"\}{1,2}", "", obj).strip()
            if isinstance(obj, list):
                return [_strip_deeplink(item) for item in obj]
            if isinstance(obj, dict):
                return {k: _strip_deeplink(v) for k, v in obj.items()}
            return obj
            
        draft = _strip_deeplink(draft)
        options = _strip_deeplink(options)
        step_details = _strip_deeplink(step_details)
        list_items = _strip_deeplink(list_items)
        list_title = _strip_deeplink(list_title)
        list_intro = _strip_deeplink(list_intro)
        extended_text = _strip_deeplink(extended_text)
        list_clarification = _strip_deeplink(list_clarification)
        response_blocks = _strip_deeplink(response_blocks)

        # Commit immediately -- this must not depend on the unrelated
        # {{faq_*}} substitution branch below ever running. A deeplink-only
        # response (the common case: no precise numeric value in the same
        # answer) has no faq_* placeholder, so that branch is skipped
        # entirely, and payload["responseBlocks"] (what a "blocks" UI type
        # actually renders) would otherwise keep the raw, unstripped text
        # while only the separately-returned `draft`/final_response got
        # cleaned -- exactly the "generated but directly exposed" symptom.
        payload["options"] = options
        payload["stepDetails"] = step_details
        if payload.get("uiType") == "checklist":
            payload["items"] = list_items
        payload["listTitle"] = list_title
        payload["listIntro"] = list_intro
        payload["extendedText"] = extended_text
        payload["listClarificationText"] = list_clarification
        payload["responseBlocks"] = response_blocks

    if placeholders_in_draft:
        valid_map = fetch_placeholders_from_db(placeholders_in_draft)
        precision_substituted = precision_substituted or bool(valid_map)
        
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
    # action for any service_request registered this turn, and the real
    # navigation action for any deeplink resolved this turn. Both run
    # regardless of how (or whether) the LLM's prose mentioned them -- the
    # actual actionable UI element must never depend on generation quality.
    # Every registered request / resolved destination gets its own button,
    # not just the first -- multiple in one turn are fully supported (the
    # planner already loops across rounds to create them; see
    # SERVICE_REQUEST_DESIGN_v1.0.md Section 9). Built straight from
    # tool_attempts, independent of the draft text entirely. Any natural
    # closing sentence connecting the answer to a shortcut is the model's
    # job (see the response generator's "Available shortcuts" instruction);
    # this block never writes customer-facing text, only attaches buttons.
    service_request_actions = []
    for pending_request in _pending_service_requests(state):
        contract = get_service_request_contract(pending_request.get("service_request_id", ""))
        if contract is None:
            continue
        anchor = action_anchors.get(("service_request", pending_request.get("special_id")))
        service_request_actions.append({
            **({"anchorBlockId": anchor} if anchor else {}),
            "label": contract.get("action_label", "Complete Form"),
            "specialId": pending_request.get("special_id"),
            "serviceRequestId": pending_request.get("service_request_id"),
            "description": pending_request.get("description"),
            "formSchema": contract["form_schema"],
        })
    if service_request_actions:
        payload["serviceRequestActions"] = service_request_actions

    navigation_actions = []
    for link_id in _resolved_deeplink_request_ids(state):
        # Persist only the opaque central-navigation request ID. Neither a
        # route nor destination identifier may enter chat history or the
        # frontend payload; the central API re-checks both at click time.
        request = deeplink_service.get(link_id)
        contract = DEEPLINK_DESTINATION_REGISTRY.get(
            request.get("destination_id", "") if request else ""
        )
        if contract is None:
            continue
        anchor = action_anchors.get(("deeplink", link_id))
        navigation_actions.append({
            "linkId": link_id, "label": contract["label"],
            **({"anchorBlockId": anchor} if anchor else {}),
        })
    if navigation_actions:
        payload["navigationActions"] = navigation_actions

    # Personal-data tokens (WP-1/WP-3) are deliberately NOT resolved here,
    # unlike {{faq_*}} above -- the real value never touches chat_history or
    # the DB. Instead this turn's token->value pairs are sent alongside the
    # response text; the customer's own browser resolves any {{data-*}} token
    # it finds, in this message and in chat history alike, falling back to a
    # generic "protected, no longer available" label for a token it has no
    # value for. No backend-side stripping of an unmatched token is needed:
    # the frontend's scan-and-replace-or-placeholder rendering already turns
    # any {{data-*}} token, matched or not, into either a real value or that
    # fallback label -- never raw braces.
    reveal_map = state.get("personal_data_reveal_map", {})
    if reveal_map:
        payload["personalDataReveals"] = [
            {"token": token, "value": value} for token, value in reveal_map.items()
        ]
        # Mirrors precisionSubstituted's shape: a plain boolean, safe to
        # persist to chat_history like any other UI flag (it carries no
        # token or value, just "this response contained personal data"),
        # driving the frontend's "Data protected" badge. True only when a
        # revealed token actually appears in the visible text, the same
        # "did this response actually cite it" standard precisionSubstituted
        # already uses -- not merely that a fetch happened this turn.
        payload["personalDataSubstituted"] = any(
            token.strip("{}") in placeholders_in_draft for token in reveal_map
        )

    return {"final_response": draft, "ui_action_payload": payload}


def _score_subquery(subquery: dict) -> float:
    """Bot-confidence contribution of one financial subquery.

    RESOLVED -> 1.0. OPEN with real partial RAG evidence (a `rag` attempt
    whose retrieval_state is RELEVANT) -> 0.5, so a genuinely
    partial answer isn't scored as a full failure. Everything else -> 0.0.
    Reuses _compute_subquery_status's rollup rather than re-deriving it.
    """
    status = subquery.get("resolution_status") or _compute_subquery_status(subquery)
    if status == "RESOLVED":
        return 1.0
    attempts = subquery.get("tool_attempts", []) if isinstance(subquery, dict) else []
    has_partial_rag_evidence = any(
        isinstance(a, dict) and a.get("tool_name") == "rag"
        and a.get("result", {}).get("retrieval_state") == "RELEVANT"
        for a in attempts
    )
    return 0.5 if has_partial_rag_evidence else 0.0


def _append_failed_turn_escalation_history(state: AgentState) -> list:
    """Used by node_11_deterministic_clarification_fallback ("n25"),
    route_n21's sole exhaustion destination after MAX_SAFETY_RETRIES failed
    generation/audit attempts, reached directly from "n21" -- which SKIPS
    node_08_soft_escalation_analyzer, the
    only place turn_escalation_history normally gets an entry. Without
    this, a turn the customer experienced as a total failure (whatever
    deterministic content n25 ends up showing -- real clarification/
    decline/no-info evidence, or the plain "I can't answer that right now"
    apology -- neither of which is the answer the customer actually asked
    for) silently contributes no data point to the rolling-confidence
    window, instead of the 0.0 it deserves -- confirmed live: a turn whose
    subqueries were internally RESOLVED (e.g. via a deeplink) but whose
    generated response never passed the outbound audit was invisible to
    the confidence metric rather than dragging it down. Every financial
    subquery this turn is force-scored 0.0 here, never read from
    _score_subquery, since internal resolution state is exactly what the
    customer did NOT end up receiving."""
    financial_subqueries = state.get("financial_subqueries", state.get("triaged_subqueries", []))
    failed_turn_subqueries = [
        {"text": sq.get("customer_component", ""), "score": 0.0}
        for sq in financial_subqueries
        if isinstance(sq, dict) and sq.get("customer_component")
    ]
    escalation_history = list(state.get("turn_escalation_history", []))
    escalation_history.append({
        "turn_number": state.get("turn_count", 0),
        "had_repeated_need": bool(state.get("repeated_need_detected_this_turn", False)),
        "subqueries": failed_turn_subqueries,
        "user_rating": None,
    })
    # Stored at the longer HYPOTHESIS_TEST_SAMPLE_SIZE, not
    # MAX_ESCALATION_HISTORY_TURNS -- node_03c's hypothesis test needs up
    # to 30 turns of history; node_08 explicitly slices back down to its
    # own shorter window when it reads this same list.
    return escalation_history[-HYPOTHESIS_TEST_SAMPLE_SIZE:]


def _turn_confidence(entry: dict) -> float | None:
    """One turn's confidence contribution: a user rating (see
    /api/feedback/turn-rating), when present, replaces the schema-derived
    average entirely rather than blending with it -- the customer's own
    feeling about the whole response is a strictly better signal than the
    proxy it is correcting. None when the turn had no financial subqueries
    and no rating (pure chitchat contributes no data point either way).
    """
    user_rating = entry.get("user_rating")
    if user_rating is not None:
        return float(user_rating)
    scores = [sq["score"] for sq in entry.get("subqueries", [])]
    return sum(scores) / len(scores) if scores else None


def _historical_subquery_scores(turn_escalation_history: list) -> list[float]:
    """Every individual subquery's own 1.0/0.5/0.0 grade across
    turn_escalation_history, in chronological order -- the sampling unit
    node_03c's RAG-effort hypothesis test (RAG_RETRY_EFFORT_v1.0.md) draws
    on, one point per subquery rather than one averaged point per turn.
    Averaging within a turn first (the earlier design) threw away
    information the schema already tracks at finer grain, and collapsed a
    turn's several subqueries down to a single, more discrete data point --
    worse for a test whose whole validity depends on how many independent
    observations it has. A subquery's own score is never touched by the
    customer's thumbs rating (only _turn_confidence's turn-level average
    is, for node_08's unrelated escalation judgment), so unlike the
    turn-level design this needed no separate rating-free variant to stay
    out of node_08's subjective-satisfaction domain -- it already is.
    """
    return [
        sq["score"]
        for entry in turn_escalation_history
        for sq in entry.get("subqueries", [])
        if isinstance(sq, dict) and sq.get("score") is not None
    ]


def _rolling_bot_confidence(turn_escalation_history: list) -> float | None:
    """Average _turn_confidence over up to MAX_ESCALATION_HISTORY_TURNS
    prior turns -- the same rolling metric node_08 uses to decide the soft
    escalation chip, factored out so node_03c can also read it mid-turn
    (before the current turn's own outcome is known, since this only ever
    looks at already-completed turns already sitting in state). None when
    there is no scored history yet (a brand-new session, or one that has
    been pure chitchat so far)."""
    confidences = [c for c in (_turn_confidence(entry) for entry in turn_escalation_history) if c is not None]
    return sum(confidences) / len(confidences) if confidences else None


def node_08_soft_escalation_analyzer(state: AgentState):
    """Signals evidence of a customer possibly struggling, even though
    nothing explicit demands it (an explicit request is the separate, hard
    node_09 path). Four independent, deterministically-evaluated signals,
    each individually logged so the reason a signal fired is always
    inspectable:

    1. Long session: elapsed time since login.
    2. Same product/intent dwelt on for many consecutive turns
       (same_intent_streak_count, maintained by node_02e).
    3. Rolling bot-confidence, averaged over the last
       MAX_ESCALATION_HISTORY_TURNS turns, drops below threshold. Tool
       Calling v3.0's schema already tracks resolution at a finer grain than
       resolved/not (see _score_subquery), so this is a continuous average,
       not a binary unresolved-turn count -- and a turn's own schema-derived
       average is itself replaceable by a direct customer thumbs rating (see
       _turn_confidence) for the ambiguous band a rating is offered on.
    4. The same underlying need recurring across several turns. Detected by
       node_02d/triage's own LLM call (repetitive_issue_detected), which
       judges directly from the conversation history it already sees --
       not a separately-injected list of prior subqueries (that was tried
       and dropped: it forced an artificial cross-turn identity match this
       codebase has no reliable way to key, and diluted triage's primary
       decomposition task) and not a separate embedding/cosine-similarity
       pass either -- that was tried and empirically rejected (see
       docs/TOOL_CALLING_v3.0.md history): short banking questions about the
       same topic but a different need score too similarly to genuine
       repeats to threshold reliably. Folding it into triage's existing call
       also adds zero new Groq/Gemini rate-limit
       exposure.

    None of these are separately persisted as a sticky "escalation active"
    flag -- all four are recomputed fresh every turn from live/windowed
    state, so a pattern that stops recurring naturally stops being
    signaled once it ages out of the window.

    Never appended as a chat-history chip: a chip baked into a past
    message stays visible forever, even once the underlying signal is long
    gone. Instead this sets `ui_action_payload["softEscalationAvailable"]`,
    a plain boolean surfaced on every turn's response (see api_gateway.py)
    -- the frontend renders it as a banner above the input box, driven by
    the latest turn's own value, so it disappears the moment a turn comes
    back with nothing triggered, the same way every other signal here
    already decays.

    A fifth, overriding condition: even when one of the four rules above
    fires, the banner is suppressed outright when THIS turn's own response
    was a clean, complete resolution (`turn_fully_resolved` below) -- a
    rolling multi-turn signal can still read as "struggling" today because
    of a rough patch several turns ago, but showing "need help?" in the
    same breath as a fully-answered question reads as the bot doubting its
    own correct answer, which is worse than saying nothing. This is not a
    fifth independent trigger -- it only ever suppresses, never causes, an
    offer.
    """
    print("🟢 [NODE 08] Executing Soft Escalation Analyzer")
    payload = state.get("ui_action_payload", {})

    financial_subqueries = state.get("financial_subqueries", state.get("triaged_subqueries", []))
    turn_subqueries = [
        {"text": sq.get("customer_component", ""), "score": _score_subquery(sq)}
        for sq in financial_subqueries
        if isinstance(sq, dict) and sq.get("customer_component")
    ]
    history = list(state.get("turn_escalation_history", []))
    history.append({
        "turn_number": state.get("turn_count", 0),
        "had_repeated_need": bool(state.get("repeated_need_detected_this_turn", False)),
        "subqueries": turn_subqueries,
        "user_rating": None,
    })
    # Stored at the longer HYPOTHESIS_TEST_SAMPLE_SIZE for node_03c's own
    # use (see there); this node's own signals below still explicitly
    # window down to MAX_ESCALATION_HISTORY_TURNS, their original size.
    history = history[-HYPOTHESIS_TEST_SAMPLE_SIZE:]

    # A thumbs prompt is offered only when THIS turn's own (unwindowed)
    # confidence sits in the band the schema itself is ambiguous about --
    # not the rolling window average, which mixes in other turns.
    this_turn_scores = [sq["score"] for sq in turn_subqueries]
    this_turn_confidence = sum(this_turn_scores) / len(this_turn_scores) if this_turn_scores else None
    show_feedback_prompt = (
        this_turn_confidence is not None
        and (
            TURN_FEEDBACK_SHOW_ON_ANY_SCORE
            or TURN_FEEDBACK_SCORE_LOWER_BOUND <= this_turn_confidence < TURN_FEEDBACK_SCORE_UPPER_BOUND
        )
        and not state.get("final_clarification", "").strip()
    )

    duration_triggered = False
    elapsed_seconds = None
    session_started_at = state.get("session_started_at", "")
    if session_started_at:
        try:
            started = datetime.fromisoformat(session_started_at)
            if started.tzinfo is None:
                started = started.replace(tzinfo=timezone.utc)
            elapsed_seconds = (datetime.now(timezone.utc) - started).total_seconds()
            duration_triggered = elapsed_seconds >= SOFT_ESCALATION_SESSION_DURATION_SECONDS
        except ValueError:
            pass

    same_intent_streak_count = state.get("same_intent_streak_count", 0)
    same_intent_triggered = same_intent_streak_count > SOFT_ESCALATION_SAME_INTENT_STREAK

    # This node's own signals stay windowed to their original, shorter
    # recency size even though `history` itself is now stored longer for
    # node_03c's hypothesis test -- a confidence dip or a repeated need
    # from 20+ turns ago is no longer an active pattern for either signal.
    recent_history = history[-MAX_ESCALATION_HISTORY_TURNS:]
    scored_turn_count = sum(1 for entry in recent_history if _turn_confidence(entry) is not None)
    confidence = _rolling_bot_confidence(recent_history)
    confidence_triggered = (
        scored_turn_count >= MIN_SCORED_TURNS_FOR_CONFIDENCE_TRUST
        and confidence is not None
        and confidence < SOFT_ESCALATION_CONFIDENCE_THRESHOLD
    )

    repeated_need_turns = sum(1 for entry in recent_history if entry.get("had_repeated_need"))
    repeated_need_triggered = repeated_need_turns >= SOFT_ESCALATION_REPEATED_NEED_TURN_COUNT

    triggered_rules = []
    if duration_triggered:
        triggered_rules.append("long_session")
    if same_intent_triggered:
        triggered_rules.append("same_intent_streak")
    if confidence_triggered:
        triggered_rules.append("low_confidence")
    if repeated_need_triggered:
        triggered_rules.append("repeated_need")

    # A rolling multi-turn signal (confidence, streak, duration) can still
    # be true even when THIS turn's own response was a clean, complete
    # resolution -- e.g. a rough patch several turns ago still drags the
    # rolling average down today. Offering "need help?" in the same breath
    # as a fully-answered question is jarring UX and undermines trust in
    # the answer just given, regardless of what the session as a whole
    # looks like. `this_turn_confidence` (already computed above, for the
    # feedback-prompt band) is 1.0 only when every financial subquery this
    # turn resolved cleanly; a decline is also a complete, definitive
    # resolution (a clear "no", not a gap), so a decline-only turn (no
    # financial subqueries to score at all) counts as resolved too. A pure
    # conversational turn (nothing to resolve either way) does NOT count --
    # it falls through to the rolling signals unchanged, same as before.
    had_resolvable_content_this_turn = bool(financial_subqueries) or bool(state.get("decline_tasks"))
    turn_fully_resolved = had_resolvable_content_this_turn and (
        this_turn_confidence is None or this_turn_confidence >= 1.0
    )

    # Clarification responses intentionally have no UI actions -- the same
    # reason this never touched payload["options"] even when it was a chip.
    soft_escalation_available = (
        bool(triggered_rules)
        and not turn_fully_resolved
        and not state.get("final_clarification", "").strip()
    )
    payload["softEscalationAvailable"] = soft_escalation_available
    if soft_escalation_available:
        log_event(
            "escalation.soft.triggered",
            rules=triggered_rules,
            elapsed_seconds=elapsed_seconds,
            same_intent_streak_count=same_intent_streak_count,
            confidence=confidence,
            scored_turn_count=scored_turn_count,
            repeated_need_turns=repeated_need_turns,
        )
    elif triggered_rules:
        log_event(
            "escalation.soft.suppressed_resolved",
            rules=triggered_rules,
            this_turn_confidence=this_turn_confidence,
            had_resolvable_content_this_turn=had_resolvable_content_this_turn,
        )

    payload["showFeedbackPrompt"] = show_feedback_prompt
    payload["turnNumber"] = state.get("turn_count", 0)

    chat_history = list(state.get("chat_history", []))
    if state.get("final_response", ""):
        chat_history.append(AIMessage(content=state.get("final_response", "")))
    return {"ui_action_payload": payload, "chat_history": chat_history, "turn_escalation_history": history}


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
    unresolved_inputs = {
        "intent_clarifications": [
            str(item.get("unresolved_request", "")).strip()
            for item in state.get("clarification_tasks", [])
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

    IMPORTANT LANGUAGE RULE: You MUST output the case summary strictly in {preferred_language}, as this summary will be presented directly to the user in the frontend UI. Exception: "Horizon" and every "Horizon <Name>" branded term (Horizon Bank, Horizon App, Horizon Key, etc.) is a proper brand name -- write it exactly as-is, in the original Latin script, never translated or transliterated.

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
        # Deterministic inbound vetoes are security/privacy controls, not a
        # finding that the customer requested a non-compliant bank action.
        "error_inbound_violation": get_message(state.get("user_profile", {}).get("language", "English"), "error_inbound_violation"),
        "error_sensitive_word": get_message(state.get("user_profile", {}).get("language", "English"), "error_inbound_violation"),
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
    """Terminal safety net after three failed generation/audit retries (see
    route_n21 in graph.py) -- the only trigger for this node,
    for every exhausted-retry turn, not just clarification-shaped ones:
    hard escalation (node_09/n23) is reserved for a customer's own explicit
    request for a human, never for the bot's own repeated inability to
    pass its own audit.

    By the time this node runs, node_06's shared audit has already had
    three chances to validate a generated draft and failed every time.
    Rather than attempt a fourth generation, this renders whatever
    deterministic evidence already exists -- decline_evidence/
    no_info_evidence, plus a clarification's fallback text computed
    on demand (_clarification_fallback_text) from clarification_tasks
    regardless of which node (node_02e or node_03c) authored a given
    entry, treated identically -- directly: fixed templates, never
    LLM-generated, independent of whatever caused the surrounding draft to
    keep failing, and therefore trustworthy without a further audit here.
    When none of that evidence exists at all (a real generated answer that
    simply never passed compliance/groundedness, with no clarification,
    decline, or no-info entry to fall back on), there is nothing safe left
    to show, so this apologizes plainly and invites the customer to retry
    or rephrase instead -- never a fabricated answer, and never a hard
    escalation for what may just be a phrasing or evidence gap rather than
    something only a human can resolve.
    """
    print("🟡 [NODE 11] Executing Deterministic Clarification Fallback")
    answer = state.get("safe_answer_for_fallback", "").strip()
    # One standard contract (subquery_id, originator, scope,
    # unresolved_request, clarification_reason, clarification_guesses)
    # regardless of which node authored the entry -- a "sufficiency_auditor"
    # clarification (node_03c) is exactly as final and trustworthy as an
    # "intent_formulator" one (node_02e); dropping either here would
    # silently lose a customer's clarification on the one path meant to be
    # the most reliable fallback in the whole pipeline.
    clarification_tasks = state.get("clarification_tasks", [])
    decline_tasks = state.get("decline_tasks", [])

    response_blocks = []
    answer_parts = []

    # The retained answer is exactly the text of the last round's approved
    # (zero-violation) blocks. Keep those blocks themselves when they still
    # match it, so each block keeps its type and its shortcut/tool handles
    # (which is what lets the deterministic cards land next to their
    # sentence); otherwise fall back to the single flattened text.
    approved_blocks = [
        b for b in state.get("approved_response_blocks", [])
        if isinstance(b, dict) and b.get("type") != "clarification"
    ]
    approved_text = "\n\n".join(p for p in (_flatten_response_block_text(b) for b in approved_blocks) if p)
    if answer and approved_blocks and approved_text.strip() == answer:
        response_blocks.extend(approved_blocks)
        answer_parts.append(answer)
    elif answer:
        response_blocks.append({
            "blockId": "retained_answer", "sourceTaskIds": [], "type": "text",
            "contextText": "", "text": answer, "items": [], "options": [],
            "progressTitle": "", "stepDetails": [], "currentStep": None,
            "totalSteps": None, "listTitle": "", "listIntro": "", "extendedText": "",
        })
        answer_parts.append(answer)

    clarification_parts = []
    for index, task in enumerate(clarification_tasks, start=1):
        text = _clarification_fallback_text(task)
        if not text or not task.get("unresolved_request"):
            continue
        options = [
            {"label": g["label"], "message": g["message"]}
            for g in task.get("clarification_guesses", []) or []
            if isinstance(g, dict) and g.get("label") and g.get("message")
        ]
        response_blocks.append({
            "blockId": f"clarification_{task.get('subquery_id') or index}",
            "sourceTaskIds": [], "type": "clarification", "text": text, "options": options,
        })
        answer_parts.append(text)
        clarification_parts.append(text)

    for index, task in enumerate(decline_tasks, start=1):
        subquery = str(task.get("subquery", "")).strip()
        fallback_scope = f'your "{subquery}" question' if subquery else "your request"
        text = str(task.get("decline_evidence", "")).strip() or _decline_message([task], fallback_scope)
        response_blocks.append({
            "blockId": f"decline_{task.get('task_id') or index}",
            "sourceTaskIds": [], "type": "text", "text": text, "options": [],
        })
        answer_parts.append(text)
        clarification_parts.append(text)

    for marker in state.get("task_resolution_instructions", []):
        if not isinstance(marker, dict) or marker.get("resolution_status") != "CLARIFICATION_REQUIRED":
            continue
        text = _no_info_evidence_text(marker.get("resolved_subquery", ""), marker.get("need", ""))
        response_blocks.append({
            "blockId": f"no_info_{marker.get('task_id')}",
            "sourceTaskIds": [], "type": "text", "text": text, "options": [],
        })
        answer_parts.append(text)
        clarification_parts.append(text)

    for entry in state.get("unresolvable_search_tasks", []):
        if not isinstance(entry, dict):
            continue
        text = str(entry.get("no_info_evidence", "")).strip() or _no_info_evidence_text(entry.get("resolved_subquery", ""), entry.get("need", ""))
        response_blocks.append({
            "blockId": f"unresolvable_{entry.get('task_id')}",
            "sourceTaskIds": [], "type": "text", "text": text, "options": [],
        })
        answer_parts.append(text)
        clarification_parts.append(text)

    # Every deterministic tool result of this turn (shortcut, unavailable
    # request, verified fact, personal data) still gets its fixed sentence if
    # nothing retained covers it -- the same net the normal path uses, built
    # only from tool-supplied text. Its cards are attached below via node_07.
    blocks_before = {id(b) for b in response_blocks}
    _apply_response_completeness_nets(
        response_blocks, state, [], {}, [], [], [], [], _available_response_shortcuts(state),
    )
    for block in response_blocks:
        if id(block) not in blocks_before:
            added_text = _flatten_response_block_text(block)
            if added_text:
                answer_parts.append(added_text)

    response = "\n\n".join(answer_parts) if answer_parts else (
        "I'm sorry, I'm not able to answer that right now. Could you try again, or ask in a different way?"
    )
    if not response_blocks:
        response_blocks.append({
            "blockId": "fallback", "sourceTaskIds": [], "type": "text",
            "contextText": "", "text": response, "items": [], "options": [],
            "progressTitle": "", "stepDetails": [], "currentStep": None,
            "totalSteps": None, "listTitle": "", "listIntro": "", "extendedText": "",
        })
    payload = {"uiType": "blocks", "responseBlocks": response_blocks, "options": []}
    history = list(state.get("chat_history", []))
    history.append(AIMessage(content=response))
    log_event(
        "clarification.deterministic_fallback",
        retained_answer=bool(answer),
        clarification_count=len(clarification_tasks),
        decline_count=len(decline_tasks),
        no_info_count=sum(
            1 for m in state.get("task_resolution_instructions", [])
            if isinstance(m, dict) and m.get("resolution_status") == "CLARIFICATION_REQUIRED"
        ),
        block_count=len(response_blocks),
        safety_retry_count=state.get("safety_retry_count", 0),
    )
    result = {
        "final_response": response,
        "final_answer": answer,
        "final_clarification": "\n\n".join(clarification_parts),
        "ui_action_payload": payload,
        "chat_history": history,
        "pending_advance": False,
    }
    # This node is now the sole destination for an exhausted-retry turn
    # (route_n21 never sends one to n23 anymore) -- see
    # _append_failed_turn_escalation_history for why every such turn is
    # scored 0 for the rolling confidence signal, regardless of which
    # deterministic content (real evidence or the plain apology) it ends
    # up showing the customer.
    if state.get("safety_retry_count", 0) >= 3:
        result["turn_escalation_history"] = _append_failed_turn_escalation_history(state)
    # This node ends the graph, so node_07 never runs for it. Run it here on
    # the fallback content so the same deterministic steps apply as on a
    # passing turn: {{faq_*}} substitution, the real form/navigation cards
    # (anchored via the blocks' handles), and the personal-data reveal map.
    finalized = node_07_precision_substitution({**state, **result, "final_draft": response})
    result["final_response"] = finalized["final_response"]
    result["ui_action_payload"] = finalized["ui_action_payload"]
    history[-1] = AIMessage(content=result["final_response"])
    return result
