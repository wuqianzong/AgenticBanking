import json
import re
import sqlite3
import os
import asyncio
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
from backend.app.core.compliance import MAS_REGULATIONS_CONTEXT
from backend.app.core.system_logger import log_event

# Define the DB Path (adjust relative path if necessary based on your working directory)
SQLITE_DB_PATH = "backend/data/knowledge_base.db"

# --- 🧹 VOLATILE STATE RESET DICTIONARY ---
# Applied at the start of every turn to prevent cross-turn memory leakage
VOLATILE_STATE_RESET = {
    "safety_retry_count": 0,
    "search_feedback_history": [],
    "draft_feedback_history": [],
    "search_tasks": [],
    "task_results": [],
    "task_retry_counts": {},
    "task_feedback": {},
    "task_resolution_instructions": [],
    "safe_answer_for_fallback": "",
    "clarification_fallback_required": False,
    "final_answer": "",
    "final_clarification": "",
    "selected_ux_journey": "INFORMATIONAL",
    "presentation_plan": {},
    "pending_evidence_cache_updates": {},
    "active_tool_routes": [],
    "pending_advance": False,
}

MAX_APPROVED_EVIDENCE_CACHE_ENTRIES = 30

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


def _task_evidence_cache_key(task: dict) -> str:
    """Return an order- and case-insensitive product/intent cache key."""
    product = str(task.get("target_commercial_product", "")).strip().casefold()
    intents = task.get("target_intent", [])
    if isinstance(intents, str):
        intents = [intents]
    intent_set = {
        str(intent).strip().casefold()
        for intent in intents
        if str(intent).strip()
    }
    if not product or not intent_set:
        return ""
    return f"{product}|{','.join(sorted(intent_set))}"


def _terminal_retrieval_feedback(task_id: str, feedback: str, reason: str, unapproved_parents: list[dict]) -> dict:
    """Keep terminal retrieval context internal until clarification generation."""
    return {
        "task_id": task_id,
        "type": "TERMINAL_RETRIEVAL_FEEDBACK",
        "feedback": feedback,
        "reason": reason,
        # These are exact database records retrieved for the task but not
        # approved as an answer.  They may be offered only as grounded topic
        # suggestions, never as an answer to the original request.
        "unapproved_parents": unapproved_parents,
    }


def _build_conversation_context(state: AgentState) -> str:
    """Combine durable conversation context with the current 10-turn window.

    The compacted context may originate from a prior session.  It is advisory
    conversation context only: it must never be interpreted as restored graph
    state, a pending action, or a previously validated retrieval result.
    """
    summary = state.get("compacted_history_summary", "").strip()
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
    prior_context = summary or "None"
    return f"Long-Term Conversation Context (advisory only):\n{prior_context}\n\nCurrent Session (latest 10 turns):\n{recent_history}"


def _build_user_baseline_context(state: AgentState) -> str:
    """Return optional personalization context for selected non-safety prompts."""
    baseline = state.get("user_baseline_summary", "").strip()
    return baseline or "None"


def _generate_returning_customer_greeting(summary: str) -> str:
    """Create a concise welcome without restoring workflow execution state."""
    if not summary:
        return "Hello! I am Horizon. How can I assist you today?"

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

    Prior conversation context:
    {summary}

    Output JSON only: {{"greeting": "your greeting"}}.
    """
    try:
        raw_response = call_groq_lpu(prompt).strip()
        greeting = _parse_json_robustly(raw_response).get("greeting", "").strip()
        if greeting:
            return greeting
    except Exception as error:
        print(f"⚠️ [MEMORY SERVICE] Returning-customer greeting generation failed: {error}")

    return "Welcome back. I can use our previous conversation to help you continue, or we can start with a new question."


# --- PHASE 0: INGESTION & INGEST GUARDS ---

def node_00_context_hydrator_and_greeting(state: AgentState):
    print("🟢 [NODE EXECUTING] Node 00: Context Hydrator & Greeting")
    customer_id = state.get("user_id", "USR_999")
    summary = LongTermMemoryManager.fetch_compact_footprint(customer_id)
    user_baseline = LongTermMemoryManager.fetch_user_baseline(customer_id)
    greeting = _generate_returning_customer_greeting(summary or user_baseline)

    return {
        # A new login begins a clean graph.  Only this compact, durable
        # conversation context crosses the session boundary; no plan, task
        # evidence, retry data, or other execution state does.
        **VOLATILE_STATE_RESET,
        "chat_history": [],
        "history_sequence_offset": 0,
        "compacted_history_summary": summary,
        "user_baseline_summary": user_baseline,
        "active_product_intents": [],
        "active_plan": {},
        "completed_steps": [],
        "process_slots": {},
        "approved_evidence_cache": {},
        "raw_retrieved_text": "",
        "final_draft": greeting,
        "final_answer": greeting,
        "final_clarification": "",
        "working_context": summary,
        "ui_action_payload": {"uiType": "text", "options": []},
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
    """n2d: safety-preserving triage; UX selection happens after retrieval."""
    print("🟢 [NODE 02d] Executing Request Triage")
    user_msg = state.get("user_input", "")
    history_str = _build_conversation_context(state)
    user_baseline = _build_user_baseline_context(state)
    
    prompt = f"""
    ROLE ALIGNMENT:
    You are an impartial, read-only Horizon Bank UI routing agent. You do NOT give financial advice, and you CANNOT make product choices for the user. 
    
    Recent Conversation Context: {history_str}
    User Baseline (advisory only; do not treat it as a current request): {user_baseline}
    Latest User Input: "{user_msg}"
    
    TRIAGE A BANKING REQUEST.
    1. ESCALATION: User explicitly demands a human agent or manager.
    2. CLARIFICATION: The input is PURELY chitchat, greetings, or completely unrelated to banking (e.g., "Hello", "How are you", "What is the weather"). 
    3. VALID_REQUEST: A banking question, follow-up, or request that can be answered from Horizon FAQ knowledge.
    
    Output JSON ONLY:
    {{"triage_status": "ESCALATION" | "CLARIFICATION" | "VALID_REQUEST"}}
    """
    try:
        res = _parse_json_robustly(call_groq_lpu(prompt, model_id=MODEL_E_TRIAGE))
        triage_status = res.get("triage_status", "CLARIFICATION")
    except Exception as e:
        print(f"🚨 [NODE 02d EXCEPTION]: {e}")
        triage_status = "CLARIFICATION"
    if triage_status not in {"ESCALATION", "CLARIFICATION", "VALID_REQUEST"}:
        triage_status = "CLARIFICATION"

    print(f"   🧠 [TRIAGE STATUS]: {triage_status}")

    mapped_preflight = "intent_clarification_needed" if triage_status == "CLARIFICATION" else triage_status

    return {
        "triage_status": triage_status,
        "preflight_status": mapped_preflight, 
    }

def node_02e_intent_classifier_and_query_formulator(state: AgentState):
    """n2e: create or selectively refine up to three independent RAG tasks."""
    print("🟢 [NODE 02e] Executing Multi-Task Query Formulator")
    user_msg = state.get("user_input", "")
    active_matrix = state.get("active_product_intents", [])
    active_plan = state.get("active_plan", {})
    has_active_plan = bool(active_plan.get("steps"))
    has_retrieved_knowledge = bool(state.get("raw_retrieved_text", "").strip())
    recalled_context = state.get("compacted_history_summary", "").strip()
    
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
    
    prompt = f"""
    PRODUCT/INTENT MATRIX (Valid Nouns and Verbs):
    {PRODUCT_INTENT_MATRIX}
    
    INTENT DEFINITION GUIDELINE:
    {INTENT_GUIDELINE}
    
    Active Context Matrix: {active_matrix}
    Current Graph Context: active_plan={has_active_plan}, retrieved_knowledge={has_retrieved_knowledge}
    Recalled Prior-Session Context Is Available: {bool(recalled_context)}
    Recent Conversation: {history_str}
    User Baseline (advisory only; do not treat it as a current request): {user_baseline}
    Latest User Input: "{user_msg}"
    TASK: Act as a Decoupled Database Search Architect.
    
    CRITICAL RULES FOR VECTOR DENSITY:
    - NEVER use conversational filler (e.g., "Provide a guide", "Explain how to", "What is").
    - NEVER use complete sentences.
    - Extract ONLY the core nouns, target verbs, and specific constraints.
    - Format each angle as a concise, comma-separated string of keywords.
    
    1. CONTEXTUAL INHERITANCE: Clean & Rephrase the user input. If it is a follow-up question, you MUST inherit the Product (Noun) from the "Active Context Matrix" or recent AI response. Do not default to "ALL" if they are discussing a specific active product process.
    2. Decompose multi-part questions into at most THREE distinct tasks. For EACH task, formulate Three Distinct Semantic Angles based strictly on the density rules above:
       - "procedural": Only the action verbs and tool nouns (e.g., "Horizon App, navigation, setup MyInfo, verification process").
       - "policy": Only the rule nouns and constraints (e.g., "eligibility rules, required documents, security policy, restrictions").
       - "conceptual": Only the definition nouns (e.g., "MyInfo service, personal data sharing, Horizon Bank").
    3. For EACH task, extract the exact "target_commercial_product" (Noun) and a list of 1-3 "target_intent" (Verbs) from the Matrix above.
    4. Each task MUST contain task_id. On a new request use task_1, task_2, task_3 in order. In RETRY MODE preserve the supplied task_id and use its feedback to change the search terms, product, or intent.
    5. CONVERSATIONAL CONTINUATION: Output an empty array for "search_tasks": [] only when the user is simply providing information requested in a previous step (e.g., "I have MyInfo ready", "Next", "Proceed") AND Current Graph Context confirms that an active plan or retrieved knowledge exists. If recalled prior-session context is available but the current graph has neither an active plan nor retrieved knowledge, the user is starting a fresh graph session: for a continuation such as "yes, continue", generate a search task from the recalled topic so current FAQ knowledge can be retrieved.
    6. If the question applies universally (e.g. app password), use "ALL". Labels are retrieval filters, not a reason to discard a valid free-form request.
    
    Output JSON ONLY:
    {{"search_tasks": [{{"task_id": "task_1", "procedural": "...", "policy": "...", "conceptual": "...", "target_commercial_product": "Credit_Card", "target_intent": ["Transaction Disputes"]}}]}}
    {retry_context}
    """
    
    try:
        res = _parse_json_robustly(call_groq_lpu(prompt, model_id=MODEL_E_TRIAGE))
        formulated_tasks = res.get("search_tasks", [])
    except Exception as e:
        print(f"🚨 [NODE 02e EXCEPTION]: {e}")
        formulated_tasks = []

    if retry_tasks:
        replacements = {task.get("task_id"): task for task in formulated_tasks if task.get("task_id") in retry_task_ids}
        search_tasks = [replacements.get(task.get("task_id"), task) for task in prior_tasks]
    else:
        search_tasks = formulated_tasks[:3]

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
            
    print(f"   🔎 [SEARCH TASKS]: {len(search_tasks)} tasks | Matrix: {new_matrix}")
    log_event("task.formulated", task_ids=[task.get("task_id") for task in search_tasks], task_count=len(search_tasks), retry_mode=bool(retry_tasks))

    if not search_tasks:
        if active_plan.get("steps") or has_retrieved_knowledge:
            return {"preflight_status": "continue_plan", "search_tasks": []}
        return {"preflight_status": "intent_clarification_needed", "search_tasks": []}

    # Cache routing is deliberately deterministic and happens only after the
    # formulator has independently created the current task set.  A retry must
    # run a new RAG search; a prior cache insufficiency is not a RAG attempt.
    cached_results = []
    cache_hits = 0
    if not retry_tasks:
        evidence_cache = state.get("approved_evidence_cache", {})
        cache_candidates = [
            (task, _task_evidence_cache_key(task), evidence_cache.get(_task_evidence_cache_key(task), {}))
            for task in search_tasks
        ]
        if any(entry.get("approved_parent_ids") for _, _, entry in cache_candidates):
            evidence_store = RAGEngine()
            for task, cache_key, entry in cache_candidates:
                parent_ids = entry.get("approved_parent_ids", []) if cache_key else []
                if not parent_ids:
                    continue
                parents = evidence_store.hydrate_parent_ids(parent_ids)
                if not parents:
                    log_event("evidence_cache.stale", task_id=task.get("task_id"), cache_key=cache_key)
                    continue
                cached_results.append({
                    "task_id": task.get("task_id"),
                    "status": "CACHED",
                    "origin": "CACHE",
                    "cache_key": cache_key,
                    "parents": parents,
                })
                cache_hits += 1
                log_event(
                    "evidence_cache.hit",
                    task_id=task.get("task_id"),
                    cache_key=cache_key,
                    parent_ids=[parent.get("parent_id") for parent in parents],
                )

    cache_misses = len(search_tasks) - cache_hits
    if cache_hits:
        print(f"   ⚡ [EVIDENCE CACHE]: {cache_hits} hit(s), {cache_misses} RAG task(s).")
    else:
        print("   🎯 [TASK ROUTER]: No approved-evidence cache match; retrieval required.")
    log_event("evidence_cache.route", hit_count=cache_hits, miss_count=cache_misses, retry_mode=bool(retry_tasks))
    return {
        "preflight_status": "validate_cached" if cache_hits == len(search_tasks) else "retrieve",
        "search_tasks": search_tasks,
        "task_results": cached_results if not retry_tasks else task_results,
        "active_product_intents": new_matrix,
        "active_plan": {} if not retry_tasks else active_plan,
        "completed_steps": [] if not retry_tasks else state.get("completed_steps", []),
        "raw_retrieved_text": "" if not retry_tasks else state.get("raw_retrieved_text", ""),
    }

# --- PHASE 2: BOUNDED RAG ARCHITECTURE ---

def node_03a_knowledge_blueprint_planner(state: AgentState):
    print("🟢 [NODE 03a] Preparing Task-Level RAG Retrieval")
    prior_results = {result.get("task_id"): result for result in state.get("task_results", [])}
    tasks_to_retrieve = [
        task for task in state.get("search_tasks", [])
        if prior_results.get(task.get("task_id"), {}).get("status") not in {"CACHED", "SUFFICIENT", "UNAVAILABLE"}
    ]
    routes = ["n17_rag"] if tasks_to_retrieve else []
        
    print(f"   🔀 [SUPERVISOR DISPATCH]: Fanning out to -> {routes}")
    return {"active_tool_routes": routes, "rag_payload": "", "api_payload": "", "deeplink_payload": ""}

async def node_03b_rag_worker(state: AgentState):
    print("🟢 [NODE 03b_1] Executing Task-Level RAG Worker (Tri-Angle RRF)")
    tasks = state.get("search_tasks", [])
    prior_results = {result.get("task_id"): result for result in state.get("task_results", [])}
    tasks_to_retrieve = [
        task for task in tasks
        if prior_results.get(task.get("task_id"), {}).get("status") not in {"CACHED", "SUFFICIENT", "UNAVAILABLE"}
    ]
    if not tasks_to_retrieve:
        return {"rag_payload": "", "task_results": list(prior_results.values())}

    engine = RAGEngine()
    async def retrieve_task(task: dict) -> dict:
        target_product = task.get("target_commercial_product", "ALL")
        product_tags = [target_product] if isinstance(target_product, str) else target_product
        parents = await engine.execute_multi_angle_search(
            blueprint=task,
            product_tags=product_tags,
            intent_families=task.get("target_intent", [])
        )
        return {"task_id": task.get("task_id"), "status": "RETRIEVED", "origin": "RAG", "parents": parents}

    retrieved_results = await asyncio.gather(*(retrieve_task(task) for task in tasks_to_retrieve))
    merged_results = {**prior_results, **{result["task_id"]: result for result in retrieved_results}}
    print(f"   🔎 [TASK RAG]: Retrieved {len(retrieved_results)} task result set(s) in parallel.")
    log_event("task.rag.completed", task_ids=[result.get("task_id") for result in retrieved_results])
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
        if result.get("status") in {"RETRIEVED", "CACHED"}
    ]
    retry_counts = dict(state.get("task_retry_counts", {}))
    feedback = dict(state.get("task_feedback", {}))

    evaluation_inputs = [
        {
            "task_id": result.get("task_id"),
            "task": tasks_by_id.get(result.get("task_id"), {}),
            "parents": result.get("parents", []),
        }
        for result in evidence_candidates
    ]
    auditor_results = {}
    if evaluation_inputs:
        prompt = f"""
        TASK-LEVEL SUFFICIENCY AND EVIDENCE SELECTION
        You are a groundedness auditor for a read-only Horizon Bank FAQ assistant.
        Evaluate EACH supplied task independently. A parent is sufficient only if it directly supports that task.

        User Request: {state.get("user_input", "")}
        Trusted Conversation Context: { _build_conversation_context(state) }
        Task Inputs: {json.dumps(evaluation_inputs, ensure_ascii=False)}

        Rules:
        - Return one result for every supplied task_id.
        - For SUFFICIENT, return only parent IDs from that exact task that are directly relevant. At least one approved ID is required.
        - For RETRY, return no approved IDs and concise query-refinement feedback. The backend determines terminal UNAVAILABLE status after three failed attempts.
        - Treat {{placeholders}} in parent content as valid approved facts.
        - Never invent a parent ID, fact, or instruction.

        Output JSON only: {{"task_results": [{{"task_id": "...", "status": "SUFFICIENT" | "RETRY" | "UNAVAILABLE", "approved_parent_ids": ["..."], "reason": "...", "search_feedback": "..."}}]}}
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
        available_ids = {parent.get("parent_id") for parent in result.get("parents", [])}
        audit = auditor_results.get(task_id, {})
        approved_ids = [parent_id for parent_id in audit.get("approved_parent_ids", []) if parent_id in available_ids]
        requested_status = audit.get("status", "RETRY")
        reason = audit.get("reason", "No sufficient grounded FAQ evidence was selected.")
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
                    "cache_key": result.get("cache_key", _task_evidence_cache_key(tasks_by_id.get(task_id, {}))),
                    "approved_parent_ids": approved_ids,
                    "parents": approved_parents,
                    "reason": reason,
                }
                feedback.pop(task_id, None)
                continue

        cache_fallback = result.get("origin") == "CACHE"
        # A cached result that fails sufficiency must fall through to a live
        # retrieval without consuming a RAG retry.  It therefore may not have
        # a retry counter yet.
        attempt_count = retry_counts.get(task_id, 0)
        if not cache_fallback:
            attempt_count += 1
            retry_counts[task_id] = attempt_count
        refinement = audit.get("search_feedback") or reason
        # An unsupported task remains retryable until its third failed
        # retrieval attempt; only then ask the user for targeted clarification.
        if attempt_count >= 3:
            unapproved_ids = [parent.get("parent_id") for parent in result.get("parents", []) if parent.get("parent_id")]
            unapproved_parents = RAGEngine().hydrate_parent_ids(unapproved_ids)
            current_results[task_id] = {
                "task_id": task_id,
                "status": "CLARIFICATION_REQUIRED",
                "parents": [],
                "approved_parent_ids": [],
                "reason": reason,
                "terminal_feedback": refinement,
                "unapproved_parents": unapproved_parents,
            }
            feedback.pop(task_id, None)
        else:
            current_results[task_id] = {"task_id": task_id, "status": "RETRY", "parents": [], "approved_parent_ids": [], "reason": reason}
            feedback[task_id] = refinement
            if cache_fallback:
                log_event("evidence_cache.insufficient", task_id=task_id, cache_key=result.get("cache_key", ""), reason=reason)

    approved_blocks = []
    for result in current_results.values():
        if result.get("status") != "SUFFICIENT":
            continue
        for parent in result.get("parents", []):
            approved_blocks.append(f"[Task {result.get('task_id')} | Parent {parent.get('parent_id')}]\n{parent.get('content', '')}")
    approved_knowledge = "\n\n---\n\n".join(approved_blocks)
    unresolved = [result for result in current_results.values() if result.get("status") == "RETRY"]
    pending_cache_updates = {}
    for result in current_results.values():
        if result.get("status") != "SUFFICIENT":
            continue
        cache_key = result.get("cache_key") or _task_evidence_cache_key(tasks_by_id.get(result.get("task_id"), {}))
        parent_ids = result.get("approved_parent_ids", [])
        if cache_key and parent_ids:
            pending_cache_updates[cache_key] = {"approved_parent_ids": parent_ids}
    task_resolution_instructions = [
        _terminal_retrieval_feedback(
            result.get("task_id"),
            result.get("terminal_feedback", result.get("reason", "")),
            result.get("reason", ""),
            result.get("unapproved_parents", []),
        )
        for result in current_results.values()
        if result.get("status") == "CLARIFICATION_REQUIRED"
    ]
    print(f"   ✅ [TASK AUDIT]: {sum(result.get('status') == 'SUFFICIENT' for result in current_results.values())} sufficient, {len(unresolved)} pending retry.")
    for result in current_results.values():
        event = "task.sufficiency.completed" if result.get("status") == "SUFFICIENT" else "task.retry" if result.get("status") == "RETRY" else "task.clarification_required"
        log_event(event, task_id=result.get("task_id"), status=result.get("status"), approved_parent_ids=result.get("approved_parent_ids", []), reason=result.get("reason", ""))
    return {
        "preflight_status": "retry_tasks" if unresolved else "task_resolution_ready",
        "task_results": list(current_results.values()),
        "task_retry_counts": retry_counts,
        "task_feedback": feedback,
        "task_resolution_instructions": task_resolution_instructions,
        "pending_evidence_cache_updates": pending_cache_updates,
        "raw_retrieved_text": approved_knowledge,
    }


# --- PHASE 3: RESOLUTION BLUEPRINT & STEP-BY-STEP RESPONSE ---

def node_04a_resolution_planner(state: AgentState):
    """n19: choose the UX journey after task evidence is sufficient or terminal."""
    print("🟢 [NODE 04a] Executing User Journey V2.0 Presentation Planner")
    existing_plan = state.get("active_plan", {})
    existing_steps = existing_plan.get("steps", [])
    existing_index = existing_plan.get("current_step_index", 0)
    if state.get("preflight_status") == "continue_plan" and existing_steps:
        return {
            "preflight_status": "presentation_ready",
            "selected_ux_journey": "GUIDED_PROCESS",
            "presentation_plan": state.get("presentation_plan", {"journey": "GUIDED_PROCESS", "steps": existing_steps, "options": []}),
        }

    task_results = state.get("task_results", [])
    planner_tasks = []
    approved_by_task = {}
    for result in task_results:
        task_id = result.get("task_id")
        parents = result.get("parents", []) if result.get("status") == "SUFFICIENT" else []
        approved_ids = {parent.get("parent_id") for parent in parents}
        approved_by_task[task_id] = approved_ids
        planner_tasks.append({
            "task_id": task_id,
            "status": result.get("status"),
            "reason": result.get("reason", ""),
            "approved_parents": parents,
        })

    prompt = f"""
    USER JOURNEY V2.0 PRESENTATION PLANNER
    You are designing the presentation for a read-only Horizon Bank FAQ response.
    Decide the user experience only after reviewing task-level approved evidence.

    User request: {state.get("user_input", "")}
    Task results: {json.dumps(planner_tasks, ensure_ascii=False)}

    Choose exactly one journey:
    - INFORMATIONAL: factual answer or several factual sections.
    - GUIDED_PROCESS: exactly one primary task has directly supported, genuinely sequential steps.
    - TROUBLESHOOTING: evidence supports diagnosis/resolution of an issue.
    - CLARIFICATION: more than one supported guided process competes, or no task has usable FAQ evidence.
    - ESCALATION: only when human assistance was explicitly requested or required.

    Rules:
    - Never invent facts, steps, parent IDs, or a banking action.
    - Select evidence_parent_ids only from approved parents of the same task.
    - A GUIDED_PROCESS requires at least two short steps, grounded only in selected evidence.
    - Do not create more than one active guided process.
    - Mark unavailable tasks as UNAVAILABLE and use no evidence IDs for them.
    - If two guided processes are supported, choose CLARIFICATION and provide at most two user-choice options.

    Output JSON only with journey, primary_task_id, presentation_tasks, steps, options, and reason.
    """
    try:
        plan = _parse_json_robustly(call_groq_lpu(prompt, model_id=MODEL_C_PLANNER))
    except Exception as error:
        print(f"🚨 [USER JOURNEY PLANNER ERROR]: {error}")
        plan = {"journey": "CLARIFICATION", "primary_task_id": "", "presentation_tasks": [], "steps": [], "options": [], "reason": "Planner unavailable."}

    journey = plan.get("journey", "CLARIFICATION")
    if journey not in {"INFORMATIONAL", "GUIDED_PROCESS", "TROUBLESHOOTING", "CLARIFICATION", "ESCALATION"}:
        journey = "CLARIFICATION"
    valid_task_ids = set(approved_by_task)
    presentations = []
    selected_blocks = []
    for item in plan.get("presentation_tasks", []):
        task_id = item.get("task_id")
        if task_id not in valid_task_ids:
            continue
        valid_ids = [parent_id for parent_id in item.get("evidence_parent_ids", []) if parent_id in approved_by_task[task_id]]
        result = next((value for value in task_results if value.get("task_id") == task_id), {})
        if result.get("status") == "SUFFICIENT" and not valid_ids:
            valid_ids = list(approved_by_task[task_id])
        presentations.append({
            "task_id": task_id,
            "display_mode": item.get("display_mode", "SECTION"),
            "evidence_parent_ids": valid_ids,
            "section_title": item.get("section_title", "Horizon Bank guidance"),
        })
        for parent in result.get("parents", []):
            if parent.get("parent_id") in valid_ids:
                selected_blocks.append(f"[Task {task_id} | {item.get('section_title', 'Horizon Bank guidance')} | Parent {parent.get('parent_id')}]\n{parent.get('content', '')}")

    if not presentations:
        for result in task_results:
            if result.get("status") == "SUFFICIENT":
                parent_ids = [parent.get("parent_id") for parent in result.get("parents", [])]
                presentations.append({"task_id": result.get("task_id"), "display_mode": "SECTION", "evidence_parent_ids": parent_ids, "section_title": "Horizon Bank guidance"})
                selected_blocks.extend(f"[Task {result.get('task_id')} | Parent {parent.get('parent_id')}]\n{parent.get('content', '')}" for parent in result.get("parents", []))

    steps = [step for step in plan.get("steps", []) if isinstance(step, str) and step.strip()]
    primary_task_id = plan.get("primary_task_id", "")
    if journey == "GUIDED_PROCESS" and (primary_task_id not in valid_task_ids or len(steps) < 2):
        journey = "INFORMATIONAL" if presentations else "CLARIFICATION"
        steps = []
    if journey != "GUIDED_PROCESS":
        steps = []

    normalized_plan = {
        "journey": journey,
        "primary_task_id": primary_task_id,
        "presentation_tasks": presentations,
        "steps": steps,
        "options": [option for option in plan.get("options", []) if isinstance(option, str)][:2],
        "reason": plan.get("reason", ""),
    }
    print(f"   🧭 [USER JOURNEY V2]: {journey} | Tasks shown: {len(presentations)}")
    log_event("journey.planned", journey=journey, primary_task_id=primary_task_id, presentation_task_ids=[item.get("task_id") for item in presentations])
    return {
        "preflight_status": "presentation_ready",
        "selected_ux_journey": journey,
        "presentation_plan": normalized_plan,
        "active_plan": {"steps": steps, "current_step_index": 0} if journey == "GUIDED_PROCESS" else {},
        "completed_steps": [] if journey != "GUIDED_PROCESS" else state.get("completed_steps", []),
        "raw_retrieved_text": "\n\n---\n\n".join(selected_blocks),
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

def node_04b_response_generator(state: AgentState):
    print("🟢 [NODE 04b] Executing Response Generator")
    plan = state.get("active_plan", {})
    idx = plan.get("current_step_index", 0)
    steps = plan.get("steps", [])
    routing_status = state.get("preflight_status", "")
    journey = state.get("selected_ux_journey", "INFORMATIONAL")
    presentation_plan = state.get("presentation_plan", {})
    task_resolution_instructions = state.get("task_resolution_instructions", [])
    has_terminal_retrieval_feedback = bool(task_resolution_instructions)
    unapproved_suggestion_blocks = []
    for instruction in task_resolution_instructions:
        for parent in instruction.get("unapproved_parents", []):
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
    completed_steps = state.get("completed_steps", [])
    is_conclusion = bool(steps and idx >= len(steps))
    
    if has_terminal_retrieval_feedback:
        current_step = "Answer supported tasks using selected approved FAQ evidence. For each unresolved task, briefly apologize that relevant FAQ details could not be found, then ask a concise, general clarification question."
    elif routing_status in ["intent_clarification_needed", "CLARIFICATION"]:
        current_step = "The user's request is vague, small talk, or asks you to make a choice. Respond naturally. If the request is about banking, ask the user to clarify their intent. If the request is unrelated to banking, politely decline, explain you are a banking assistant only, and ask if they have a banking question."
    elif not steps:
        if journey == "CLARIFICATION":
            current_step = "Ask a concise clarification or explain that the available FAQs do not contain enough information. Do not invent a banking process."
        elif journey == "ESCALATION":
            current_step = "State that a human agent can assist with the request."
        else:
            current_step = "Provide a complete, direct answer using only the selected approved FAQ evidence. Group distinct task topics under their supplied section titles."
    elif is_conclusion:
        current_step = "State that this guided FAQ is complete. Offer at most two general next actions."
    else:
        current_step = steps[idx]
        
    print(f"   📍 [CURRENT TARGET STEP]: {current_step} (Index {idx})")
    
    history_str = _build_conversation_context(state)
    user_baseline = _build_user_baseline_context(state)
    feedback = state.get("draft_feedback_history", [])
    feedback_str = f"CRITICAL CORRECTION NEEDED: {feedback[-1]}" if feedback else ""

    prompt = f"""
    ROLE ALIGNMENT:
    You are Horizon, a Read-Only FAQ Navigation Bot. You exist SOLELY to read static FAQ documents.
    
    Current Plan: {steps}
    Current Step to Execute: "{current_step}"
    Selected User Journey: {journey}
    Presentation Plan: {presentation_plan}
    Selected Approved Knowledge: "{state.get("raw_retrieved_text", "")}"
    Terminal Retrieval Feedback (internal guidance only; do not quote it or treat it as FAQ knowledge): {json.dumps(terminal_feedback_context, ensure_ascii=False)}
    Unapproved Retrieved Knowledge for Clarification Suggestions (not approved answer evidence): "{unapproved_suggestion_knowledge}"
    Recent Conversation: {history_str}
    User Baseline (advisory only; adapt communication only when relevant): {user_baseline}
    {feedback_str}

    Rules for Text Generation:
    - For GUIDED_PROCESS, explain ONLY the Current Step.
    - For INFORMATIONAL, group answers by presentation-plan section title when more than one task is present.
    - For a terminal retrieval feedback item, the clarification MUST begin with a brief apology/inability statement (for example, "Sorry, I couldn't find relevant FAQ details to answer that.").
    - When the user has multiple requests, explicitly name the unresolved term exactly as the user wrote it in the apology (for example, "Sorry, I couldn't find relevant FAQ details about ABGX loan."). Never use an ambiguous reference such as "that", "it", or "the remaining part" in its place.
    - You may then offer up to two potentially related FAQ topics, but ONLY when each topic is explicitly present in Unapproved Retrieved Knowledge for Clarification Suggestions. Make clear these are possible related topics, not an answer to the customer's request.
    - End with a general, natural invitation to clarify if those suggested topics are irrelevant. If no suitable suggestion exists, ask only the general clarification question.
    - Do not request specific details such as an amount, term, purpose, eligibility, or application information unless they are explicitly supported by Unapproved Retrieved Knowledge for Clarification Suggestions.
    - Treat terminal retrieval feedback as uncertain internal guidance: do not quote it, turn it into a customer-facing product label, or treat it as a fact.
    - Do not offer UI options for an unresolved task.
    - ZERO HALLUCINATION: Rely EXCLUSIVELY on Selected Approved Knowledge, except for a safe clarification, apology, or inability statement.
    - If quoting exact numerical rates or fees, you MUST ONLY use the exact {{placeholders}} provided in the 'Retrieved Knowledge'. Do NOT invent, alter, or hallucinate placeholders.
    
    Output JSON ONLY:
    {{"answer": "Grounded answer text only, or an empty string", "clarification": "Natural clarification question only, or an empty string", "options": ["Grounded answer-related next action"], "advance_step": true}}
    """
    
    try:
        res = _parse_json_robustly(call_groq_lpu(prompt))
        answer = res.get("answer", res.get("response", "")).strip()
        clarification = res.get("clarification", "").strip()
        if has_terminal_retrieval_feedback and not clarification:
            clarification = (
                "Sorry, I couldn't find relevant FAQ details to answer that. "
                "Could you clarify what information you are looking for?"
            )
        if has_terminal_retrieval_feedback and not state.get("raw_retrieved_text", "").strip():
            answer = ""
        draft = "\n\n".join(part for part in [answer, clarification] if part)
        if not draft:
            draft = "Could you please clarify your request?"
            answer = draft
        print(f"   💬 [DRAFT RESPONSE]: {draft}")
        
        # V4.1 FIX: Do NOT advance the step here! The text might fail the safety gates.
        # Instead, set a volatile flag indicating this step *should* advance if it passes.
        pending_advance = res.get("advance_step") and steps and idx < len(steps) and routing_status != "intent_clarification_needed"
            
        options = res.get("options", [])
        ui_items = []
        use_progress_mode = journey == "GUIDED_PROCESS" and len(steps) > 1 and not is_conclusion
        ui_type = "stepper" if use_progress_mode else "none"

        if is_conclusion:
            ui_type = "chips"
            if not options or len(options) < 2: options = ["That's all, thank you.", "I have another question."]
            plan = {}
            completed_steps = []
            print(f"   🧹 [STATE CLEANUP]: Wiped active plan and completed steps.")
        elif has_terminal_retrieval_feedback:
            ui_type = "none"
            options = []
        elif journey == "CLARIFICATION":
            ui_type = "chips" if presentation_plan.get("options") else "none"
            options = presentation_plan.get("options", [])
        elif journey == "TROUBLESHOOTING":
            ui_type = "chips" if presentation_plan.get("options") else "none"
            options = presentation_plan.get("options", [])
        elif ui_type == "none":
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
        else:
            print(
                f"   📝 [USER JOURNEY RESPONSE MODE]: Journey={journey} | "
                f"Active multi-step plan={use_progress_mode}"
            )
        
        return {
            "final_draft": draft,
            "final_answer": answer,
            "final_clarification": clarification,
            "active_plan": plan,
            "completed_steps": completed_steps,
            "ui_action_payload": {
                "uiType": ui_type,
                "options": options,
                "items": ui_items,
                "stepDetail": draft if ui_type == "stepper" else "",
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
    
    # 1. Combine draft text AND UI options to sweep globally
    combined_text = draft + " " + " ".join(options)
    placeholders_in_draft = re.findall(r"\{\{(.*?)\}\}", combined_text)
    
    illegal_detected = []
    if placeholders_in_draft:
        valid_map = fetch_placeholders_from_db(placeholders_in_draft)
        for p in placeholders_in_draft:
            if p not in valid_map:
                illegal_detected.append(p)
    
    # 2. Trigger Safety Loop if any placeholder is invalid
    if illegal_detected or "ILLEGAL_WORD" in combined_text:
        print(f"   🚫 [HARD GATE VETO]: Illegal/invented placeholders detected: {illegal_detected}")
        count = state.get("safety_retry_count", 0) + 1
        history = state.get("draft_feedback_history", [])
        
        history.append(f"Draft or UI chips contained invalid placeholders: {illegal_detected}. You MUST ONLY use placeholders explicitly present in the Retrieved Knowledge.")
        
        return {
            "safety_retry_count": count, 
            "draft_feedback_history": history, 
            "preflight_status": "fail",
            "pending_advance": False # Enforce Atomic State
        }
        
    print("   ✅ [HARD GATE PASS]: All placeholders in text and UI validated against DB.")
    return {"preflight_status": "pass"}


def node_06_outbound_semantic_gate(state: AgentState):
    print("🟢 [NODE 06] Executing Outbound Semantic Gate (MAS Compliance & Groundedness)")
    
    draft = state.get("final_draft", "")
    answer_text = state.get("final_answer", draft)
    clarification_text = state.get("final_clarification", "")
    payload = state.get("ui_action_payload", {})
    options = payload.get("options", [])
    answer_with_options = f"Answer Text: {answer_text}\nAnswer UI Buttons: {options}"

    # `raw_retrieved_text` is the planner's final evidence subset used for this
    # response.  Independently rebuild every parent that node 03c approved, so
    # the gate can also reject unsafe evidence before any subset is presented.
    selected_knowledge = state.get("raw_retrieved_text", "")
    approved_evidence_blocks = []
    for task_result in state.get("task_results", []):
        approved_ids = set(task_result.get("approved_parent_ids", []))
        for parent in task_result.get("parents", []):
            parent_id = parent.get("parent_id")
            if parent_id in approved_ids:
                approved_evidence_blocks.append(
                    f"[Task {task_result.get('task_id')} | Parent {parent_id}]\n{parent.get('content', '')}"
                )
    approved_knowledge = "\n\n---\n\n".join(approved_evidence_blocks)
    terminal_retrieval_feedback = state.get("task_resolution_instructions", [])
    unapproved_clarification_blocks = []
    for instruction in terminal_retrieval_feedback:
        for parent in instruction.get("unapproved_parents", []):
            parent_id = parent.get("parent_id", "")
            content = parent.get("content", "")
            if content:
                unapproved_clarification_blocks.append(
                    f"[Task {instruction.get('task_id')} | Unapproved Parent {parent_id}]\n{content}"
                )
    unapproved_clarification_knowledge = "\n\n---\n\n".join(unapproved_clarification_blocks)
    
    # 1. The LLM-as-a-Judge Prompt
    prompt = f"""
    You are an independent Compliance Auditor for a Singaporean Bank.
    
    Regulatory Context:
    {MAS_REGULATIONS_CONTEXT}

    User Request:
    "{state.get('user_input', '')}"
    
    Task-Level Approved Knowledge (all parent IDs approved by the sufficiency checker):
    "{approved_knowledge}"

    Selected Approved Knowledge Used for This Response:
    "{selected_knowledge}"

    Unapproved Retrieved Knowledge for Clarification Suggestions:
    "{unapproved_clarification_knowledge}"
    
    Generated Answer and Answer UI Options:
    "{answer_with_options}"

    Generated Clarification Text:
    "{clarification_text}"
    
    TASK: Evaluate the selected approved knowledge, answer, answer UI options, and clarification on six strict binary criteria:
    1. APPROVED-KNOWLEDGE SAFETY: Is the Task-Level Approved Knowledge safe and compliant to use in a read-only banking FAQ response under the Regulatory Context? It must not contain unauthorized personalized financial advice, transaction execution instructions, deceptive claims, or instructions to bypass security, KYC, AML/CFT, or other controls. (True/False)
       The parent-ID headers are metadata, not customer-facing content. If no knowledge is supplied because the response is a safe clarification, apology, or inability statement, evaluate this as True.
    2. ANSWER GROUNDEDNESS: Is every claim, fact, instruction, and UI button in the Generated Answer and Answer UI Options strictly derived from the Selected Approved Knowledge? An empty answer and no options are grounded. (True/False)
    3. CLARIFICATION GROUNDEDNESS: Is every banking topic, product reference, and factual suggestion in the Generated Clarification Text directly supported by the Unapproved Retrieved Knowledge for Clarification Suggestions? A generic apology and general request for clarification are grounded without that source. (True/False)
    4. CLARIFICATION CONNECTION: If the clarification addresses an unresolved portion of a multi-part request, does it explicitly name or unambiguously identify that customer-supplied term from User Request? It must not use an ambiguous reference such as "that" or "it" after answering another task. A pure generic clarification where no term can be identified is True. (True/False)
    5. CLARIFICATION COMPLIANCE: Is the Generated Clarification Text a safe, neutral request for more detail? It must not state unsupported banking facts, product claims, rates, requirements, or procedural instructions. (True/False)
    6. RESPONSE SAFETY: Does any generated answer, clarification, or UI option contain unauthorized financial advice, subjective product recommendations, or return forecasts that violate the Regulatory Context? (True/False)
    
    Output JSON ONLY:
    {{"is_approved_knowledge_compliant": true/false, "is_grounded": true/false, "is_clarification_grounded": true/false, "is_clarification_connected": true/false, "is_clarification_compliant": true/false, "contains_advice": true/false, "reason": "..."}}
    """

    # 🐛 FIX: Pre-initialize variables so they always exist in memory even if the API crashes
    res = {}
    is_grounded = False
    contains_advice = True
    is_clarification_grounded = False
    is_clarification_connected = False
    is_clarification_compliant = False
    is_approved_knowledge_compliant = False
    reason = "API Evaluation Failed"

    
    try:
        res = _parse_json_robustly(call_groq_lpu(prompt, model_id=MODEL_B_AUDITOR))
        is_grounded = res.get("is_grounded", False)
        contains_advice = res.get("contains_advice", True)
        is_clarification_grounded = res.get("is_clarification_grounded", False)
        is_clarification_connected = res.get("is_clarification_connected", False)
        is_clarification_compliant = res.get("is_clarification_compliant", False)
        is_approved_knowledge_compliant = res.get("is_approved_knowledge_compliant", False)
    except Exception as e:
        print(f"🚨 [SEMANTIC GATE ERROR]: {e}")
        is_grounded, contains_advice, is_clarification_grounded, is_clarification_connected, is_clarification_compliant, is_approved_knowledge_compliant = False, True, False, False, False, False # Fail-safe defaults

    # 2. Evaluate Judge Results

    # 🟢 UPDATE: Deterministic Guardrail Exemption (include initial greeting)
    is_exempt = (
        state.get("user_input") in {"__INIT__", "**INIT**"}
        or state.get("triage_status") == "CLARIFICATION"
        or (state.get("selected_ux_journey") == "CLARIFICATION" and not selected_knowledge.strip() and not answer_text.strip())
        or state.get("preflight_status") in ["intent_clarification_needed", "initial_greeting"]
    )

    if is_exempt:
        print(f"   💡 [GUARDRAIL EXEMPTION]: {state.get('preflight_status', 'CLARIFICATION')} detected. Bypassing Groundedness requirement.")
        is_grounded = True # Deterministically override the LLM's failure
        # There is no selected FAQ evidence for a greeting or clarification.
        is_approved_knowledge_compliant = True
    # -----------------------------------------------------

    clarification_failed = not is_clarification_grounded or not is_clarification_connected or not is_clarification_compliant
    answer_safe_for_fallback = (
        is_grounded
        and not contains_advice
        and is_approved_knowledge_compliant
    )
    if not is_grounded or contains_advice or clarification_failed or not is_approved_knowledge_compliant:
        print(f"   🚫 [SEMANTIC VETO]: Evidence compliant: {is_approved_knowledge_compliant} | Answer grounded: {is_grounded} | Clarification grounded: {is_clarification_grounded} | Clarification connected: {is_clarification_connected} | Clarification compliant: {is_clarification_compliant} | Contains Advice: {contains_advice}")
        count = state.get("safety_retry_count", 0) + 1
        history = state.get("draft_feedback_history", [])
        history.append(f"Semantic compliance failed. Approved evidence compliant: {is_approved_knowledge_compliant}. Answer grounded: {is_grounded}. Clarification grounded: {is_clarification_grounded}. Clarification connected: {is_clarification_connected}. Clarification compliant: {is_clarification_compliant}. Contained Advice: {contains_advice}. Reason: {res.get('reason', 'Violation')}. Rewrite with a connected, grounded answer and safe, neutral clarification.")

        # Preserve an individually safe answer across clarification-only
        # retries.  If all three clarification rewrites fail, the graph can
        # still deliver this answer with a deterministic, neutral fallback.
        terminal_clarification = bool(state.get("task_resolution_instructions"))
        preserve_answer = terminal_clarification and clarification_failed and answer_safe_for_fallback
        safe_answer = answer_text if preserve_answer and answer_text.strip() else state.get("safe_answer_for_fallback", "")
        
        return {
            "safety_retry_count": count, 
            "draft_feedback_history": history, 
            "preflight_status": "fail",
            "pending_advance": False, # Block ledger update
            "safe_answer_for_fallback": safe_answer,
            "clarification_fallback_required": preserve_answer,
        }

    print("   ✅ [SEMANTIC PASS]: Approved evidence and response are grounded and MAS-compliant.")

    # 3. ATOMIC LEDGER UPDATE: Safe to advance step progression!
    plan = state.get("active_plan", {})
    completed_steps = state.get("completed_steps", [])
    
    if state.get("pending_advance"):
        idx = plan.get("current_step_index", 0)
        steps = plan.get("steps", [])
        if steps and idx < len(steps):
            completed_steps.append(steps[idx])
            plan["current_step_index"] = idx + 1
            print(f"   ⏩ [ATOMIC LEDGER UPDATE]: Safely advanced to step index {plan['current_step_index']}")

    # Promote parent IDs only after the outbound gate has approved both the
    # selected evidence and the response. The cache intentionally stores no
    # parent text; every cache hit is re-read from SQLite.
    evidence_cache = dict(state.get("approved_evidence_cache", {}))
    for cache_key, entry in state.get("pending_evidence_cache_updates", {}).items():
        parent_ids = entry.get("approved_parent_ids", [])
        if not cache_key or not parent_ids:
            continue
        evidence_cache.pop(cache_key, None)
        evidence_cache[cache_key] = {"approved_parent_ids": parent_ids}
    while len(evidence_cache) > MAX_APPROVED_EVIDENCE_CACHE_ENTRIES:
        evidence_cache.pop(next(iter(evidence_cache)))
    if state.get("pending_evidence_cache_updates"):
        log_event("evidence_cache.promoted", entry_count=len(evidence_cache), keys=list(state.get("pending_evidence_cache_updates", {}).keys()))

    return {
        "preflight_status": "pass",
        "active_plan": plan,
        "completed_steps": completed_steps,
        "pending_advance": False,
        "approved_evidence_cache": evidence_cache,
        "pending_evidence_cache_updates": {},
        "safe_answer_for_fallback": "",
        "clarification_fallback_required": False,
    }


def node_07_precision_substitution(state: AgentState):
    print("🟢 [NODE 07] Executing Precision Substitution")
    draft = state.get("final_draft", "")
    payload = state.get("ui_action_payload", {})
    options = payload.get("options", [])
    
    # Extract unique placeholders globally
    combined_text = draft + " " + " ".join(options)
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
            updated_opt = opt
            for p_id, p_val in valid_map.items():
                updated_opt = updated_opt.replace(f"{{{{{p_id}}}}}", p_val)
            updated_options.append(updated_opt)
            
        payload["options"] = updated_options
        if payload.get("stepDetail"):
            payload["stepDetail"] = draft

    payload["precisionSubstituted"] = precision_substituted
    return {"final_response": draft, "ui_action_payload": payload}


def node_08_soft_escalation_analyzer(state: AgentState):
    print("🟢 [NODE 08] Executing Soft Escalation Analyzer")
    payload = state.get("ui_action_payload", {})
    if state.get("turn_count", 0) >= 5: payload["options"].append("Talk to Agent")
        
    history = list(state.get("chat_history", []))
    if state.get("final_response", ""):
        history.append(AIMessage(content=state.get("final_response", "")))
    return {"ui_action_payload": payload, "chat_history": history}

def node_09_hard_escalation_broker(state: AgentState):
    print("🟢 [NODE 09] Executing Hard Escalation Broker")
    summary_items = [f"RAG Diagnostics: {f}" for f in state.get("search_feedback_history", [])] + [f"Safety: {f}" for f in state.get("draft_feedback_history", [])]
    history = list(state.get("chat_history", []))
    response = "I am transferring you to a human agent."
    history.append(AIMessage(content=response))
    return {
        "final_response": response,
        "ui_action_payload": {"uiType": "case_summary", "items": summary_items},
        "chat_history": history,
    }

def node_10_standard_rejection(state: AgentState):
    status = state.get("preflight_status", "fail")
    print(f"🔴 [NODE 10] DETERMINISTIC MESSAGE ROUTER TRIGGERED. Code: '{status}'")
    
    # MAS-Approved Deterministic Message Dictionary
    MESSAGE_MAP = {
        "error_malicious": "We cannot process your request. Please try rephrasing or ask another question.",
        "error_non_compliant": "Sorry, we cannot process your request due to its non-compliant nature under MAS laws and banking regulations. Please try asking another question.",
        "fail": "Sorry, I’m unable to respond to that right now. Please try again or rephrase your question."
    }
    
    # Fetch the mapped message, default to "fail" if the code isn't explicitly defined
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
    clarification = (
        "Sorry, I couldn't find relevant FAQ details for the remaining part of your request. "
        "Could you clarify what information you are looking for?"
        if answer
        else "Sorry, I couldn't find relevant FAQ details to answer that. Could you clarify what information you are looking for?"
    )
    response = "\n\n".join(part for part in [answer, clarification] if part)
    history = list(state.get("chat_history", []))
    history.append(AIMessage(content=response))
    log_event(
        "clarification.deterministic_fallback",
        retained_answer=bool(answer),
        safety_retry_count=state.get("safety_retry_count", 0),
    )
    return {
        "final_response": response,
        "final_answer": answer,
        "final_clarification": clarification,
        "ui_action_payload": {"uiType": "none", "options": []},
        "chat_history": history,
        "pending_advance": False,
    }
