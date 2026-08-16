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
    "intent_clarification_tasks": [],
    "task_resolution_instructions": [],
    "safe_answer_for_fallback": "",
    "clarification_fallback_required": False,
    "final_answer": "",
    "final_clarification": "",
    "selected_ux_journey": "TEXT",
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
        "approved_evidence_cache": {},
        "raw_retrieved_text": "",
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
    """n2e: partition a request into independent retrieval or clarification components."""
    print("🟢 [NODE 02e] Executing Multi-Task Query Formulator")
    user_msg = state.get("user_input", "")
    active_matrix = state.get("active_product_intents", [])
    active_plan = state.get("active_plan", {})
    has_active_plan = bool(active_plan.get("steps"))
    has_retrieved_knowledge = bool(state.get("raw_retrieved_text", "").strip())
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
    2. FIRST partition the actionable content of the LATEST USER INPUT into its distinct customer-request components. A component is one question, request, comparison dimension, or independently answerable topic. Do not split one coherent question into artificial fragments. Do not impose a numerical cap: return every distinct component needed to answer the input faithfully.
    3. Assign EVERY component exactly once and only once:
       - SEARCH: use when you can map the component to a valid Product/Intent retrieval task.
       - CLARIFY: use only when you cannot map that component to a valid Product/Intent task without guessing.
       A component, its wording, a substring of its wording, or a paraphrase of it MUST NOT appear in both resolutions. If you produce a SEARCH task for a component, it is resolved for this turn and MUST NOT also generate a clarification.
    4. For EACH SEARCH component, formulate Three Distinct Semantic Angles based strictly on the density rules above:
       - "procedural": Only the action verbs and tool nouns (e.g., "Horizon App, navigation, setup MyInfo, verification process").
       - "policy": Only the rule nouns and constraints (e.g., "eligibility rules, required documents, security policy, restrictions").
       - "conceptual": Only the definition nouns (e.g., "MyInfo service, personal data sharing, Horizon Bank").
    5. For EACH SEARCH task, extract the exact "target_commercial_product" (Noun) and a list of 1-3 "target_intent" (Verbs) from the Matrix above. Each search task MUST contain task_id. On a new request use task_1, task_2, task_3 and continue numbering as needed. In RETRY MODE preserve the supplied task_id and use its feedback to change the search terms, product, or intent.
    6. CONVERSATIONAL CONTINUATION: Return no components only when the user is simply providing information requested in a previous step (e.g., "I have MyInfo ready", "Next", "Proceed") AND Current Graph Context confirms that an active plan or retrieved knowledge exists. If recalled prior-session context is available but the current graph has neither an active plan nor retrieved knowledge, the user is starting a fresh graph session: for a continuation such as "yes, continue", create a SEARCH component from the recalled topic so current FAQ knowledge can be retrieved.
    7. INTENT CLARIFICATION: For a CLARIFY component, copy only the ambiguous customer term into clarification.unresolved_request and explain internally why it cannot be searched. Do not guess a product or create a generic task merely to fill the array.
       Recalled prior-session context and the user baseline are advisory context only: NEVER create a search task or clarification task solely because they mention an old pending issue. Use them only when the latest user input explicitly continues or refers to that topic.
    8. If the question applies universally (e.g. app password), use "ALL". Labels are retrieval filters, not a reason to discard a valid free-form request.
    
    Output JSON ONLY:
    {{"components": [{{"component_id":"component_1","customer_component":"open an account","resolution":"SEARCH","search_task":{{"task_id":"task_1","procedural":"...","policy":"...","conceptual":"...","target_commercial_product":"Deposit_Account","target_intent":["Application"]}},"clarification":null}},{{"component_id":"component_2","customer_component":"the fee","resolution":"CLARIFY","search_task":null,"clarification":{{"clarification_id":"intent_clarification_1","unresolved_request":"the fee","reason":"No product or service is identified."}}}}]}}
    {retry_context}
    """
    
    try:
        res = _parse_json_robustly(call_groq_lpu(prompt, model_id=MODEL_E_TRIAGE))
        # The model returns a mutually exclusive component partition. Derive
        # the existing downstream task lists from it so retrieval, caching,
        # retries, and clarification handling retain their current contracts.
        formulated_tasks = []
        formulated_clarifications = []
        for component in res.get("components", []) or []:
            if not isinstance(component, dict):
                continue
            resolution = str(component.get("resolution", "")).strip().upper()
            if resolution == "SEARCH" and isinstance(component.get("search_task"), dict):
                formulated_tasks.append(component["search_task"])
            elif resolution == "CLARIFY" and isinstance(component.get("clarification"), dict):
                formulated_clarifications.append(component["clarification"])
    except Exception as e:
        print(f"🚨 [NODE 02e EXCEPTION]: {e}")
        formulated_tasks = []
        formulated_clarifications = []

    if retry_tasks:
        replacements = {task.get("task_id"): task for task in formulated_tasks if task.get("task_id") in retry_task_ids}
        search_tasks = [replacements.get(task.get("task_id"), task) for task in prior_tasks]
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
                "raw_retrieved_text": "",
            }
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
        "intent_clarification_tasks": intent_clarification_tasks,
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
    """n19: choose the response presentation after task evidence is sufficient or terminal."""
    print("🟢 [NODE 04a] Executing User Journey V2.0 Presentation Planner")
    existing_plan = state.get("active_plan", {})
    existing_steps = existing_plan.get("steps", [])
    existing_index = existing_plan.get("current_step_index", 0)
    if state.get("preflight_status") == "continue_plan" and existing_steps:
        return {
            "preflight_status": "presentation_ready",
            "selected_ux_journey": "PROGRESS",
            "presentation_plan": state.get("presentation_plan", {"presentation_type": "PROGRESS", "steps": existing_steps, "options": []}),
        }

    intent_clarification_tasks = state.get("intent_clarification_tasks", [])
    if state.get("preflight_status") == "intent_clarification" and not state.get("task_results", []):
        # No valid retrieval task exists. Keep the UX intentionally plain so
        # Node 04b can ask only for the exact unresolved customer wording.
        return {
            "preflight_status": "presentation_ready",
            "selected_ux_journey": "TEXT",
            "presentation_plan": {"presentation_type": "TEXT", "steps": [], "options": [], "reason": "Intent clarification required before retrieval."},
            "active_plan": {},
            "completed_steps": [],
            "raw_retrieved_text": "",
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
    Intent-level clarification tasks (unresolved user-derived components; no FAQ evidence): {json.dumps(intent_clarification_tasks, ensure_ascii=False)}

    DEEPER PRESENTATION PRINCIPLE:
    Select the presentation that visualizes the mental model the customer needs at this moment. The customer's higher-level intent selects the experience; approved FAQ evidence limits every title, item, stage, and option within that experience.

    Choose one presentation type for EACH answerable task:
    - TEXT — use when the customer needs to understand an explanation, policy, factual answer, or comparison. For a comparison, organise the supported facts into clear labelled sections; there is no dedicated comparison table yet.
    - LIST — use when the customer needs to scan or remember independent items. This is optimal for three or more eligibility criteria, required documents, prerequisites, fees, limits, or security checks.
    - PROGRESS — use when the customer needs to know where they are in, or complete, a process. This is especially appropriate for an account-opening or application/onboarding request asking how to proceed, how to apply, or what steps remain.
    - ESCALATION — use only when human assistance was explicitly requested or required.

    Rules:
    - Never invent facts, steps, parent IDs, or a banking action.
    - Select evidence_parent_ids only from approved parents of the same task.
    - A PROGRESS presentation requires at least two grounded stages. Each item in `steps` is a short navigational title of 2-7 words, not a full instruction or sentence. The response generator will create the expandable detail later. For example: "Sign in to the Horizon App", "Select your physical card", "Choose an activation method".
    - For account-opening or application/onboarding requests, prefer PROGRESS when approved evidence supports two or more distinct actions, prerequisites, or constraints that can be presented as honest preparation stages. Do not invent app clicks or claim a complete bank workflow when the evidence only supports preparation. For example, evidence about residency, MyInfo, and local submission can support stages such as "Confirm you can apply", "Prepare MyInfo verification", and "Submit locally in Singapore".
    - A LIST presentation requires at least three independent, grounded items. Provide a concise `list_title` describing the list, such as "What you'll need" or "Eligibility requirements". Do not use LIST when order matters.
    - A single customer turn may contain multiple response blocks. Choose the best presentation independently for each answerable task. Each block may use TEXT, LIST, or PROGRESS when its approved evidence supports it.
    - Do not force a visual payload: use TEXT when a list or progress flow would not improve the customer's understanding.
    - Mark unavailable tasks as UNAVAILABLE and use no evidence IDs for them.
    - Intent-level clarification tasks do not have FAQ evidence and must not be treated as answerable tasks or UI choices.
    - Use up to two grounded `options` only when the customer needs to choose a supported next action or topic. Every option must be an object with a concise visible `label` (2-6 words) and a complete, unambiguous `message` that will be sent when clicked. Options are supplementary interaction, not a replacement for the selected presentation type. Leave options empty when the answer is complete, clarification is required, or the evidence does not support a meaningful choice.

    CRITICAL OUTPUT CONTRACT:
    - The JSON key MUST be `response_blocks`. Never output `journey`.
    - Every block's `presentation_type` MUST be exactly TEXT, PROGRESS, LIST, or ESCALATION. Never output legacy labels such as INFORMATIONAL, GUIDED_PROCESS, TROUBLESHOOTING, or CLARIFICATION.
    - Return blocks in the same order as the customer raised the corresponding task.
    - For eligibility requirements with three or more independent criteria, use LIST.

    Output JSON only:
    {{"response_blocks":[{{"task_id":"task_1","presentation_type":"LIST","evidence_parent_ids":["approved_parent_id"],"section_title":"Credit card eligibility requirements","steps":[],"list_title":"Credit card eligibility requirements","options":[],"reason":"Independent eligibility criteria are best reviewed as a list."}}]}}
    """
    try:
        plan = _parse_json_robustly(call_groq_lpu(prompt, model_id=MODEL_C_PLANNER))
    except Exception as error:
        print(f"🚨 [USER JOURNEY PLANNER ERROR]: {error}")
        plan = {"presentation_type": "TEXT", "primary_task_id": "", "presentation_tasks": [], "steps": [], "list_title": "", "options": [], "reason": "Planner unavailable."}

    # Response Generation v2.0: every answerable task receives its own
    # presentation plan, while the generator still composes one coherent turn.
    if isinstance(plan.get("response_blocks"), list):
        valid_task_ids = set(approved_by_task)
        normalized_blocks = []
        selected_blocks = []
        for raw_block in plan["response_blocks"]:
            task_id = raw_block.get("task_id")
            if task_id not in valid_task_ids:
                continue
            result = next((value for value in task_results if value.get("task_id") == task_id), {})
            if result.get("status") != "SUFFICIENT":
                continue
            allowed_ids = approved_by_task[task_id]
            evidence_ids = [parent_id for parent_id in raw_block.get("evidence_parent_ids", []) if parent_id in allowed_ids]
            if not evidence_ids:
                evidence_ids = list(allowed_ids)
            block_type = raw_block.get("presentation_type", "TEXT")
            if block_type not in {"TEXT", "PROGRESS", "LIST", "ESCALATION"}:
                block_type = "TEXT"
            steps = [step.strip() for step in raw_block.get("steps", []) if isinstance(step, str) and step.strip()]
            if block_type == "PROGRESS" and len(steps) < 2:
                block_type, steps = "TEXT", []
            if block_type != "PROGRESS":
                steps = []
            normalized_blocks.append({
                "task_id": task_id,
                "presentation_type": block_type,
                "evidence_parent_ids": evidence_ids,
                "section_title": raw_block.get("section_title", "Horizon Bank guidance"),
                "steps": steps,
                "list_title": raw_block.get("list_title", "") if block_type == "LIST" else "",
                "options": _normalize_ui_options(raw_block.get("options", [])),
                "reason": raw_block.get("reason", ""),
            })
            for parent in result.get("parents", []):
                if parent.get("parent_id") in evidence_ids:
                    selected_blocks.append(f"[Task {task_id} | {raw_block.get('section_title', 'Horizon Bank guidance')} | Parent {parent.get('parent_id')}]\\n{parent.get('content', '')}")
        if normalized_blocks:
            normalized_plan = {"response_blocks": normalized_blocks, "reason": "Task-level response block plan."}
            print(f"   🧭 [RESPONSE BLOCKS PLANNED]: {len(normalized_blocks)} blocks")
            log_event("journey.planned", presentation_type="BLOCKS", presentation_task_ids=[block["task_id"] for block in normalized_blocks])
            return {
                "preflight_status": "presentation_ready",
                "selected_ux_journey": "BLOCKS",
                "presentation_plan": normalized_plan,
                "active_plan": {},
                "completed_steps": [],
                "raw_retrieved_text": "\\n\\n---\\n\\n".join(selected_blocks),
            }

    # Accept the old planner labels during a rolling backend restart, but
    # normalize all persisted and newly generated plans to the UI-first model.
    legacy_presentation_map = {
        "INFORMATIONAL": "TEXT",
        "GUIDED_PROCESS": "PROGRESS",
        "TROUBLESHOOTING": "TEXT",
        "CLARIFICATION": "TEXT",
    }
    raw_presentation_type = plan.get("presentation_type", plan.get("journey", "TEXT"))
    presentation_type = legacy_presentation_map.get(raw_presentation_type, raw_presentation_type)
    if presentation_type not in {"TEXT", "PROGRESS", "LIST", "ESCALATION"}:
        presentation_type = "TEXT"
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
    if presentation_type == "PROGRESS" and (primary_task_id not in valid_task_ids or len(steps) < 2):
        presentation_type = "TEXT"
        steps = []
    if presentation_type != "PROGRESS":
        steps = []

    normalized_plan = {
        "presentation_type": presentation_type,
        "primary_task_id": primary_task_id,
        "presentation_tasks": presentations,
        "steps": steps,
        "list_title": plan.get("list_title", "") if presentation_type == "LIST" else "",
        "options": _normalize_ui_options(plan.get("options", [])),
        "reason": plan.get("reason", ""),
    }
    print(f"   🧭 [PRESENTATION PLANNED]: {presentation_type} | Tasks shown: {len(presentations)}")
    log_event("journey.planned", presentation_type=presentation_type, planner_raw_type=raw_presentation_type, primary_task_id=primary_task_id, presentation_task_ids=[item.get("task_id") for item in presentations])
    return {
        "preflight_status": "presentation_ready",
        "selected_ux_journey": presentation_type,
        "presentation_plan": normalized_plan,
        "active_plan": {"steps": steps, "current_step_index": 0} if presentation_type == "PROGRESS" else {},
        "completed_steps": [] if presentation_type != "PROGRESS" else state.get("completed_steps", []),
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


def _generate_response_blocks(state: AgentState) -> dict:
    """Generate one coherent, ordered set of independently presented response blocks."""
    block_plans = state.get("presentation_plan", {}).get("response_blocks", [])
    task_results = {result.get("task_id"): result for result in state.get("task_results", [])}
    block_inputs = []
    for block in block_plans:
        result = task_results.get(block.get("task_id"), {})
        approved_ids = set(block.get("evidence_parent_ids", []))
        parents = [
            {"parent_id": parent.get("parent_id"), "content": parent.get("content", "")}
            for parent in result.get("parents", [])
            if parent.get("parent_id") in approved_ids
        ]
        if parents:
            block_inputs.append({**block, "approved_parents": parents})

    intent_clarifications = [
        {"unresolved_request": task.get("unresolved_request", "")}
        for task in state.get("intent_clarification_tasks", [])
    ]
    terminal_feedback = state.get("task_resolution_instructions", [])
    conversation_context = _build_conversation_context(state)
    user_baseline = _build_user_baseline_context(state)
    prompt = f"""
    RESPONSE GENERATION V2.0
    You are Horizon, a read-only Horizon Bank FAQ assistant. Generate ONE
    coherent customer turn as several ordered response blocks. Each block will
    render as a separate Horizon message, but they remain one response.

    Customer request: {state.get('user_input', '')}
    Planned response blocks and their approved evidence: {json.dumps(block_inputs, ensure_ascii=False)}
    Conversation Context (for continuity only):
    {conversation_context}
    User Baseline Memory (lowest-priority advisory context):
    {user_baseline}
    Intent clarification tasks: {json.dumps(intent_clarifications, ensure_ascii=False)}
    Terminal retrieval feedback: {json.dumps(terminal_feedback, ensure_ascii=False)}

    Rules:
    - Use Current Session Trusted History as the primary continuity context for
      references to an earlier answer, correction, comparison, or follow-up.
      Use Current-Session Rolling Context only to extend older same-session
      context. Last Session Recall and User Baseline Memory are lower-priority
      advisory context: never revive an old topic, request, issue, or workflow
      unless the latest customer request explicitly refers to it.
    - Conversation context is NOT banking evidence. It may help you understand
      what the customer means, but every factual claim, instruction,
      comparison, UI item, extended_text item, and option must be supported by
      the current block's approved parents. If current evidence differs from a
      prior assistant response, follow the current approved evidence.
    - Return exactly one generated block for every supplied planned task_id, in
      the same order. Never omit an answerable task.
    - Use only that block's approved parents for its answer, UI details,
      extended_text, and options. Do not mix facts between task IDs.
    - TEXT: put the complete answer in answer. LIST: provide 3-8 independent
      list_items and an optional short answer introduction. PROGRESS: provide
      one grounded step_detail for every supplied short step title, in order.
    - Every block may include optional extended_text: natural additional
      context, qualification, or useful answer detail that does not belong in
      its main UI. Do not repeat the main payload or call it supporting text.
    - Use options only when they are meaningful, grounded next actions for that
      same block. Do not provide options if clarification is needed.
    - Combine all unresolved components into one natural clarification field at
      the end. Explicitly name each unresolved customer term; do not invent a
      product or fact. Clarification is plain text, not a UI block.
    - SOURCE-NEUTRAL CONVERSATION: FAQ records, policies, documents, database
      entries, search results, and retrieved knowledge are internal evidence.
      Never mention them, parent IDs, model names, or retrieval mechanics.
      Present approved facts naturally as Horizon guidance.
    - ZERO HALLUCINATION: do not add facts, procedures, rates, requirements, or
      advice beyond the approved evidence. Preserve exact {{placeholders}}.

    Output JSON only:
    {{"blocks":[{{"task_id":"task_1","answer":"...","step_details":["..."],"list_items":["..."],"extended_text":"...","options":[{{"label":"short action","message":"complete grounded question"}}]}}],"clarification":"..."}}
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
    for index, plan in enumerate(block_plans, start=1):
        item = generated_by_task.get(plan.get("task_id"), {})
        block_type = plan.get("presentation_type", "TEXT")
        answer = str(item.get("answer", "")).strip()
        extended_text = str(item.get("extended_text", item.get("supporting_text", ""))).strip()
        options = _normalize_ui_options(item.get("options", plan.get("options", [])))
        steps = plan.get("steps", [])
        step_details = item.get("step_details", []) if isinstance(item.get("step_details", []), list) else []
        step_details = [detail.strip() for detail in step_details if isinstance(detail, str) and detail.strip()]
        list_items = item.get("list_items", []) if isinstance(item.get("list_items", []), list) else []
        list_items = [entry.strip() for entry in list_items if isinstance(entry, str) and entry.strip()][:8]
        ui_type = "text"
        if block_type == "PROGRESS" and len(steps) > 1:
            if len(step_details) != len(steps):
                step_details = list(steps)
            ui_type = "stepper"
        elif block_type == "LIST" and len(list_items) >= 3:
            ui_type = "checklist"
        elif not answer:
            answer = "Could you please clarify your request?"
        if ui_type == "stepper":
            items = _build_stepper_payload(steps, 0)["items"]
            record = "\n\n".join([*step_details, extended_text])
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
            "text": answer if ui_type == "text" else "",
            "items": items,
            "options": options,
            "progressTitle": plan.get("section_title", "Horizon Bank guided process") if ui_type == "stepper" else "",
            "stepDetails": step_details if ui_type == "stepper" else [],
            "currentStep": 1 if ui_type == "stepper" else None,
            "totalSteps": len(steps) if ui_type == "stepper" else None,
            "listTitle": plan.get("list_title", plan.get("section_title", "Key information")) if ui_type == "checklist" else "",
            "listIntro": answer if ui_type == "checklist" else "",
            "extendedText": extended_text,
        })
        if record:
            record_parts.append(record)
    clarification = str(generated.get("clarification", "")).strip()
    if (intent_clarifications or terminal_feedback) and not clarification:
        unresolved = ", ".join(item.get("unresolved_request", "") for item in intent_clarifications if item.get("unresolved_request", ""))
        clarification = f"Could you clarify what you mean by {unresolved}?" if unresolved else "Could you clarify what information you are looking for?"
    if clarification:
        response_blocks.append({"blockId": "clarification", "type": "clarification", "text": clarification})
        record_parts.append(clarification)
    draft = "\n\n".join(record_parts) or "Could you please clarify your request?"
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

def node_04b_response_generator(state: AgentState):
    print("🟢 [NODE 04b] Executing Response Generator")
    if state.get("presentation_plan", {}).get("response_blocks"):
        return _generate_response_blocks(state)
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
    for result in state.get("task_results", []):
        task_id = result.get("task_id")
        selected_ids = presentation_by_task.get(task_id, set())
        for parent in result.get("parents", []):
            if parent.get("parent_id") not in selected_ids:
                continue
            block = f"[Task {task_id} | Parent {parent.get('parent_id')}]\n{parent.get('content', '')}"
            if presentation_type == "LIST" and task_id == primary_task_id:
                primary_list_evidence.append(block)
            elif task_id != primary_task_id:
                extended_evidence.append(block)
    primary_list_knowledge = "\n\n---\n\n".join(primary_list_evidence)
    extended_knowledge = "\n\n---\n\n".join(extended_evidence)
    has_terminal_retrieval_feedback = bool(task_resolution_instructions)
    has_intent_clarification = bool(intent_clarification_tasks)
    has_any_clarification = has_terminal_retrieval_feedback or has_intent_clarification
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
    intent_clarification_context = [
        {
            "clarification_id": task.get("clarification_id", ""),
            "unresolved_request": task.get("unresolved_request", ""),
            "reason": task.get("reason", ""),
        }
        for task in intent_clarification_tasks
    ]
    completed_steps = state.get("completed_steps", [])
    is_conclusion = bool(steps and idx >= len(steps))
    is_progress_mode = presentation_type == "PROGRESS" and len(steps) > 1 and not is_conclusion
    
    if has_any_clarification:
        current_step = "Answer supported tasks using selected approved FAQ evidence. For every unresolved component, briefly explain what needs clarification and ask a concise, general clarification question."
    elif routing_status in ["intent_clarification_needed", "CLARIFICATION"]:
        current_step = "The user's request is vague, small talk, or asks you to make a choice. Respond naturally. If the request is about banking, ask the user to clarify their intent. If the request is unrelated to banking, politely decline, explain you are a banking assistant only, and ask if they have a banking question."
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
    
    history_str = _build_conversation_context(state)
    user_baseline = _build_user_baseline_context(state)
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
    Selected Approved Knowledge: "{state.get("raw_retrieved_text", "")}"
    Primary LIST Evidence (use ONLY this evidence for list_items): "{primary_list_knowledge}"
    Additional Selected Evidence (available for extended_text, never for primary list_items): "{extended_knowledge}"
    Intent Clarification Tasks (user-derived internal guidance only; do not quote reason text): {json.dumps(intent_clarification_context, ensure_ascii=False)}
    Terminal Retrieval Feedback (internal guidance only; do not quote it or treat it as FAQ knowledge): {json.dumps(terminal_feedback_context, ensure_ascii=False)}
    Unapproved Retrieved Knowledge for Clarification Suggestions (not approved answer evidence): "{unapproved_suggestion_knowledge}"
    Recent Conversation: {history_str}
    User Baseline (advisory only; adapt communication only when relevant): {user_baseline}
    {feedback_str}

    Rules for Text Generation:
    - Answer every independently answerable component of the customer's latest request. The selected UI visualizes the main mental model; it does not limit the rest of the response.
    - `extended_text` is optional, natural conversational continuation shown below a visual payload. Use it for relevant approved information that does not belong inside the selected UI, including other answerable components of a multi-part request, useful qualifications, or context. Do not repeat the UI payload or label this text as "supporting" or as a separate internal section.
    - For PROGRESS with multiple steps, write one concise, grounded detail for EVERY item in Current Plan, in the same order. A detail must provide the concrete instruction behind its short step title; do not merely repeat or lightly paraphrase the title. These details will appear in an expandable progress payload, so each one must explain only its matching step and may not rely on an earlier or later detail being visible. Put approved information that is outside this single process in `extended_text`.
    - For LIST, provide 3-8 concise, independent `list_items` derived strictly from Primary LIST Evidence only. Each item must belong to the primary task named by the list title. Do not use a list for ordered instructions. Put other relevant approved material in `extended_text`; never mix it into `list_items`. Supply a brief `answer` introduction only when it improves comprehension.
    - For TEXT, group answers by presentation-plan section title when more than one task is present. Use `extended_text` only when a separate natural continuation improves readability.
    - If Comparison Request is true, begin with a direct, concise comparison conclusion that answers whether the products are the same or different using only the approved evidence. Then explain the relevant similarities and differences under clearly named product headings. Do not merely place two unrelated product summaries next to each other. Put the complete comparison in `answer`.
    - For every Intent Clarification Task, state what exactly needs clarification by explicitly naming its unresolved_request. Never replace it with an ambiguous reference such as "that", "it", "the request", or "the remaining part". Do not guess a product, policy, or banking fact beyond the customer's wording.
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
        if has_any_clarification and not clarification:
            unresolved_terms = [task.get("unresolved_request", "").strip() for task in intent_clarification_tasks if task.get("unresolved_request", "").strip()]
            named_terms = ", ".join(unresolved_terms)
            clarification = (
                f"Sorry, I need a little more detail about {named_terms}. Could you clarify what information you are looking for?"
                if named_terms
                else "Sorry, I couldn't find relevant FAQ details to answer that. Could you clarify what information you are looking for?"
            )
        if has_terminal_retrieval_feedback and not state.get("raw_retrieved_text", "").strip():
            answer = ""
        if list_items:
            answer_for_record = "\n".join(part for part in [list_intro, *list_items, extended_text] if part)
        elif is_progress_mode:
            answer_for_record = "\n\n".join(part for part in [*step_details, extended_text] if part)
        else:
            answer_for_record = answer
        draft = "\n\n".join(part for part in [answer_for_record, clarification] if part)
        if not draft:
            draft = "Could you please clarify your request?"
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
        elif has_any_clarification:
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


def node_06_outbound_semantic_gate(state: AgentState):
    print("🟢 [NODE 06] Executing Outbound Semantic Gate (MAS Compliance & Groundedness)")
    
    draft = state.get("final_draft", "")
    answer_text = state.get("final_answer", draft)
    clarification_text = state.get("final_clarification", "")
    payload = state.get("ui_action_payload", {})
    options = payload.get("options", [])
    step_details = payload.get("stepDetails", [])
    list_items = payload.get("items", []) if payload.get("uiType") == "checklist" else []
    list_title = payload.get("listTitle", "")
    list_intro = payload.get("listIntro", "")
    extended_text = payload.get("extendedText", "")
    list_clarification = payload.get("listClarificationText", "")
    response_blocks = payload.get("responseBlocks", [])
    answer_with_options = f"Answer Text: {answer_text}\nAnswer UI Buttons: {json.dumps(options, ensure_ascii=False)}\nAnswer Step Details: {step_details}\nAnswer List Title: {list_title}\nAnswer List Introduction: {list_intro}\nAnswer List Items: {list_items}\nAnswer Extended Text: {extended_text}\nResponse Blocks: {json.dumps(response_blocks, ensure_ascii=False)}"
    is_initial_greeting = (
        state.get("user_input") in {"__INIT__", "**INIT**"}
        or state.get("preflight_status") == "initial_greeting"
    )
    greeting_memory_context = _build_greeting_memory_context(
        state.get("last_session_summary", state.get("working_context", "")),
        state.get("user_baseline_summary", ""),
    )

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
    intent_clarification_tasks = state.get("intent_clarification_tasks", [])
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
    intent_clarification_context = [
        {
            "unresolved_request": task.get("unresolved_request", ""),
            "reason": task.get("reason", ""),
        }
        for task in intent_clarification_tasks
    ]
    
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

    Intent Clarification Tasks Derived From the User Request:
    "{json.dumps(intent_clarification_context, ensure_ascii=False)}"

    Durable Memory Context for Initial Greeting Only:
    "{greeting_memory_context}"
    
    Generated Answer and Answer UI Options:
    "{answer_with_options}"

    Generated Clarification Text:
    "{clarification_text}\n{list_clarification}"
    
    TASK: Evaluate the selected approved knowledge, answer, answer UI options, and clarification on seven strict binary criteria:
    1. APPROVED-KNOWLEDGE SAFETY: Is the Task-Level Approved Knowledge safe and compliant to use in a read-only banking FAQ response under the Regulatory Context? It must not contain unauthorized personalized financial advice, transaction execution instructions, deceptive claims, or instructions to bypass security, KYC, AML/CFT, or other controls. (True/False)
       The parent-ID headers are metadata, not customer-facing content. If no knowledge is supplied because the response is a safe clarification, apology, or inability statement, evaluate this as True.
    2. ANSWER GROUNDEDNESS: Is every claim, fact, instruction, and UI button in the Generated Answer and Answer UI Options strictly derived from the Selected Approved Knowledge? For an initial greeting (`User Request` is `__INIT__` or `**INIT**`), assess it instead against Durable Memory Context for Initial Greeting Only. A first-time greeting with no memory is grounded only if it is generic and makes no personalized factual claim; its options must be empty. An empty answer and no options are grounded. (True/False)
    3. ANSWER COMPLIANCE: Is the Generated Answer and Answer UI Options safe, neutral, and compliant under the Regulatory Context? An empty answer and no options are compliant. (True/False)
    4. CLARIFICATION GROUNDEDNESS: For terminal retrieval clarifications, is every banking topic, product reference, and factual suggestion directly supported by Unapproved Retrieved Knowledge for Clarification Suggestions? For intent clarifications, is every named unresolved term derived from Intent Clarification Tasks or User Request, with no guessed product, policy, or banking fact? A generic apology and general request for clarification are grounded without either source. (True/False)
    5. CLARIFICATION CONNECTION: Does each clarification explicitly name or unambiguously identify its corresponding customer-supplied unresolved term from Intent Clarification Tasks or User Request? It must not use an ambiguous reference such as "that" or "it" after answering another task. A pure generic clarification where no term can be identified is True. (True/False)
    6. CLARIFICATION COMPLIANCE: Is the Generated Clarification Text a safe, neutral request for more detail? It must not state unsupported banking facts, product claims, rates, requirements, or procedural instructions. If no clarification text is supplied, evaluate criteria 4-6 as True. (True/False)
    7. RESPONSE SAFETY: Does any generated answer, clarification, or UI option contain unauthorized financial advice, subjective product recommendations, or return forecasts that violate the Regulatory Context? (True/False)
    
    Output JSON ONLY:
    {{"is_approved_knowledge_compliant": true/false, "is_grounded": true/false, "is_answer_compliant": true/false, "is_clarification_grounded": true/false, "is_clarification_connected": true/false, "is_clarification_compliant": true/false, "contains_advice": true/false, "reason": "..."}}
    """

    # 🐛 FIX: Pre-initialize variables so they always exist in memory even if the API crashes
    res = {}
    is_grounded = False
    is_answer_compliant = False
    contains_advice = True
    is_clarification_grounded = False
    is_clarification_connected = False
    is_clarification_compliant = False
    is_approved_knowledge_compliant = False
    reason = "API Evaluation Failed"

    
    try:
        res = _parse_json_robustly(call_groq_lpu(prompt, model_id=MODEL_B_AUDITOR))
        is_grounded = res.get("is_grounded", False)
        is_answer_compliant = res.get("is_answer_compliant", False)
        contains_advice = res.get("contains_advice", True)
        is_clarification_grounded = res.get("is_clarification_grounded", False)
        is_clarification_connected = res.get("is_clarification_connected", False)
        is_clarification_compliant = res.get("is_clarification_compliant", False)
        is_approved_knowledge_compliant = res.get("is_approved_knowledge_compliant", False)
    except Exception as e:
        print(f"🚨 [SEMANTIC GATE ERROR]: {e}")
        is_grounded, is_answer_compliant, contains_advice, is_clarification_grounded, is_clarification_connected, is_clarification_compliant, is_approved_knowledge_compliant = False, False, True, False, False, False, False # Fail-safe defaults

    # 2. Evaluate Judge Results

    # Clarification-only responses have no answer evidence. Initial greetings
    # are deliberately excluded: their text and chips are grounded against the
    # durable-memory context above and must receive a real semantic verdict.
    is_exempt = (
        state.get("triage_status") == "CLARIFICATION"
        or (bool(intent_clarification_tasks) and not selected_knowledge.strip() and not answer_text.strip())
        or state.get("preflight_status") == "intent_clarification_needed"
    )

    if is_exempt:
        print(f"   💡 [GUARDRAIL EXEMPTION]: {state.get('preflight_status', 'CLARIFICATION')} detected. Bypassing Groundedness requirement.")
        is_grounded = True # Deterministically override the LLM's failure
        is_answer_compliant = True
        # There is no selected FAQ evidence for a greeting or clarification.
        is_approved_knowledge_compliant = True
    # -----------------------------------------------------

    clarification_failed = not is_clarification_grounded or not is_clarification_connected or not is_clarification_compliant
    answer_safe_for_fallback = (
        is_grounded
        and is_answer_compliant
        and is_approved_knowledge_compliant
    )
    if not is_grounded or contains_advice or clarification_failed or not is_approved_knowledge_compliant:
        if is_initial_greeting:
            # A greeting cannot safely enter the normal generation retry path:
            # there is no retrieved answer evidence to regenerate from. Degrade
            # to a generic welcome without memory-derived claims or chips.
            print("   🚫 [GREETING VETO]: Falling back to generic greeting without suggested actions.")
            generic_greeting = "Hello! I am Horizon. How can I assist you today?"
            return {
                "preflight_status": "pass",
                "final_draft": generic_greeting,
                "final_answer": generic_greeting,
                "final_clarification": "",
                "ui_action_payload": {"uiType": "text", "options": []},
                "pending_advance": False,
                "pending_evidence_cache_updates": {},
            }
        print(f"   🚫 [SEMANTIC VETO]: Evidence compliant: {is_approved_knowledge_compliant} | Answer grounded: {is_grounded} | Answer compliant: {is_answer_compliant} | Clarification grounded: {is_clarification_grounded} | Clarification connected: {is_clarification_connected} | Clarification compliant: {is_clarification_compliant} | Contains Advice: {contains_advice}")
        count = state.get("safety_retry_count", 0) + 1
        history = state.get("draft_feedback_history", [])
        history.append(f"Semantic compliance failed. Approved evidence compliant: {is_approved_knowledge_compliant}. Answer grounded: {is_grounded}. Answer compliant: {is_answer_compliant}. Clarification grounded: {is_clarification_grounded}. Clarification connected: {is_clarification_connected}. Clarification compliant: {is_clarification_compliant}. Contained Advice: {contains_advice}. Reason: {res.get('reason', 'Violation')}. Rewrite with a connected, grounded answer and safe, neutral clarification.")

        # Preserve an individually safe answer across clarification-only
        # retries.  If all three clarification rewrites fail, the graph can
        # still deliver this answer with a deterministic, neutral fallback.
        unresolved_clarification = bool(state.get("task_resolution_instructions") or intent_clarification_tasks)
        preserve_answer = unresolved_clarification and clarification_failed and answer_safe_for_fallback
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
        "ui_action_payload": {"uiType": "none", "options": []},
        "chat_history": history,
        "pending_advance": False,
    }
