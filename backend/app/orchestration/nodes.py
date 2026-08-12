import json
import re
import sqlite3
import os
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

# Define the DB Path (adjust relative path if necessary based on your working directory)
SQLITE_DB_PATH = "backend/data/knowledge_base.db"

# --- 🧹 VOLATILE STATE RESET DICTIONARY ---
# Applied at the start of every turn to prevent cross-turn memory leakage
VOLATILE_STATE_RESET = {
    "knowledge_retry_count": 0,
    "safety_retry_count": 0,
    "search_feedback_history": [],
    "draft_feedback_history": [],
    "search_tasks": [],
    "active_tool_routes": [],
    "response_complexity": "simple",
    "pending_advance": False,
}

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

# --- MAS REGULATORY COMPLIANCE CONTEXT ---
MAS_REGULATIONS_CONTEXT = """
1. Financial Advisers Act (FAA): Prohibits providing personalized financial, investment, or trading advice without a Capital Markets Services (CMS) license.
2. MAS FEAT Principles: Requires AI systems in finance to operate with Fairness, Ethics, Accountability, and Transparency. Forbids biased, unethical, or deceptive behavior.
3. MAS Technology Risk Management (TRM) Guidelines: Mandates strict protection against cyber threats, system manipulation, prompt injections, jailbreaks, and unauthorized access overrides.
4. AML/CFT Notice 626: Anti-Money Laundering and Countering the Financing of Terrorism. Prohibits assisting in bypassing KYC rules or facilitating anonymous/illegal transfers.
"""

USER_JOURNEY_CLASSIFICATION_GUIDELINE = """
1. ACCOUNT_OPENING: Users attempting to establish a new banking relationship or open a new account type. (Keywords: Eligibility, required documents like NRIC/Singpass, application status tracking).
2. DEPOSIT_ACCOUNTS: Managing active cash deposits, interest earnings, and daily operations. (Keywords: Interest yields, balances, Savings Pots, account restrictions/fees, reactivating dormant accounts).
3. DEBIT_CARDS: Lifecycle and security of everyday spending debit cards. (Keywords: Activation, checkout declines, reporting lost cards, PIN management).
4. CREDIT_CARDS: Applying for, managing, or disputing charges on personal credit lines. (Keywords: Annual fee waivers, checking limits, transaction declines, chargebacks, disputes).
5. DIGITAL_BANKING: App access, login authentication, and credential safety. (Keywords: Password/passcode reset, biometric lockouts, delayed OTPs, security token issues).
6. STATEMENTS: Obtaining or understanding monthly banking statements. (Keywords: PDF downloads, missing statements, explaining confusing merchant names).
7. LOANS: Seeking personal lending, credit conversions, or cash liquidity. (Keywords: Loan features, EIR, application status, missed payments, late penalties).
8. BRANCHES: Locating physical bank touchpoints and automated teller machines. (Keywords: Branch/ATM locator, opening hours, checking service availability like cash deposit or card printing).
9. PROFILE: Authenticated customers managing personal records. (Keywords: Updating registered mobile/email/address, tracking address verification PIN mailers).
10. INVESTMENTS: Wealth management, trading, and automated investments. (Keywords: Stocks, ETFs, Mutual Funds, AutoInvest, portfolios, trading fees).
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


# --- PHASE 0: INGESTION & INGEST GUARDS ---

def node_00_context_hydrator_and_greeting(state: AgentState):
    print("🟢 [NODE EXECUTING] Node 00: Context Hydrator & Greeting")
    customer_id = "USR_999"
    summary = LongTermMemoryManager.fetch_compact_footprint(customer_id)
    
    greeting = f"Welcome back. Based on your previous visit: {summary}. How can I help you today?"
    if not summary:
        greeting = "Hello! I am Horizon. How can I assist you today?"
        
    return {
        **VOLATILE_STATE_RESET, # 🧹 Wipes any artifacts upon session initialization
        "final_draft": greeting,
        "working_context": summary,
        "ui_action_payload": {"uiType": "text", "options": []},
        "preflight_status": "initial_greeting",
    }

def node_01_deterministic_ingest_gate(state: AgentState):
    print("🟢 [NODE 01] Executing Deterministic Ingest Gate")
    user_msg = state.get("user_input", "")
    history = list(state.get("chat_history", []))
    
    if user_msg and user_msg not in {"__INIT__", "**INIT**"}:
        history.append(HumanMessage(content=user_msg))
        print(f"💾 [MEMORY] User Input saved. History length: {len(history)}")

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


# --- PHASE 1: PREFLIGHT ROUTING & INTERLOCK CONTEXTS ---

def node_02a_pending_contract_check(state: AgentState):
    print("🟢 [NODE 02a] Executing Pending Contract Check")
    pending = state.get("pending_journey_switch", {})
    if pending and pending.get("target_journey"):
        print(f"   🔒 [STATE INTERLOCKED]: Pending switch to '{pending.get('target_journey')}'")
        return {"preflight_status": "yes"} 
    
    print("   🔓 [STATE OPEN]: No pending contracts.")
    return {"preflight_status": "no"}      

def node_02b_confirmation_gate(state: AgentState):
    print("🟢 [NODE 02b] Executing Confirmation Gate")
    user_msg = state.get("user_input", "")
    pending_journey = state.get("pending_journey_switch", {}).get("target_journey", "")
    pending_matrix = state.get("pending_journey_switch", {}).get("target_product_intents", [])
    
    prompt = f"""
    Evaluate user input: "{user_msg}"
    Did they agree to pause their progress and switch to {pending_journey}?
    Reply ONLY with JSON: {{"decision": "yes" | "no" | "else"}}
    """
    try: decision = _parse_json_robustly(call_groq_lpu(prompt)).get("decision", "else")
    except: decision = "else"
        
    if decision == "yes":
        current_queue = state.get("pending_journey_queue", [])
        
        # 1. PUSH current progress into the queue
        current_queue.append({
            "journey": state.get("active_user_journey"),
            "product_intents": state.get("active_product_intents"),
            "plan": state.get("active_plan"),
            "completed_steps": state.get("completed_steps")
        })
        print(f"   📦 [QUEUE PUSH]: Saved progress for -> {state.get('active_user_journey')}")
        
        # 2. CHECK if target is in queue to resume
        for i, q in enumerate(current_queue):
            if q["journey"] == pending_journey:
                resumed = current_queue.pop(i)
                print(f"   📦 [QUEUE POP]: Resuming paused task -> {pending_journey}")
                return {
                    "user_input": f"Resume my previous request about {pending_journey}",
                    "active_user_journey": resumed["journey"], 
                    "active_product_intents": resumed["product_intents"],
                    "active_plan": resumed["plan"], 
                    "completed_steps": resumed["completed_steps"],
                    "pending_journey_queue": current_queue, 
                    "pending_journey_switch": {}, 
                    "preflight_status": "yes"
                }

        # 3. Otherwise start fresh
        return {
            "user_input": f"I want to proceed with {pending_journey}",
            "active_user_journey": pending_journey, 
            "active_product_intents": pending_matrix,
            "active_plan": {}, 
            "completed_steps": [],
            "pending_journey_queue": current_queue, 
            "pending_journey_switch": {}, 
            "preflight_status": "yes"
        }
        
    elif decision == "no":
        return {"pending_journey_switch": {}, "preflight_status": "no"}
    else:
        return {"active_user_journey": "", "active_product_intents": [], "pending_journey_switch": {}, "preflight_status": "else"}


def node_02c_inbound_semantic_gate(state: AgentState):
    print("🟢 [NODE 02c] Executing Inbound Semantic Gate (Prompt Guard)")
    user_input = state.get("user_input", "")
    
    try:
        # ONLY pass the user_input string.
        res = call_groq_lpu(user_input, model_id=MODEL_D_PROMPT_GUARD)
        
        # 🐛 FIX: Parse the raw float probability from the model
        try:
            probability_score = float(res.strip())
            # If the probability of being an attack is > 50%, mark as unsafe
            is_safe = probability_score < 0.5
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
    return {"preflight_status": "pass"}


def node_02d_user_journey_classifier_and_triage(state: AgentState):
    """n2d: Global Triage & User Journey Extractor (Brain 1)"""
    print("🟢 [NODE 02d] Executing User Journey Classifier & Triage")
    user_msg = state.get("user_input", "")
    history = state.get("chat_history", [])
    history_str = "\n".join([f"{'User' if isinstance(m, HumanMessage) else 'AI'}: {m.content}" for m in history[-6:]])
    
    prompt = f"""
    ROLE ALIGNMENT:
    You are an impartial, read-only Horizon Bank UI routing agent. You do NOT give financial advice, and you CANNOT make product choices for the user. 
    
    USER JOURNEY GUIDELINES (UI Sandbox):
    {USER_JOURNEY_CLASSIFICATION_GUIDELINE}
    
    Recent Conversation Context: {history_str}
    Latest User Input: "{user_msg}"
    
    TASK: Triage the user input based on the stage of their journey.
    1. ESCALATION: User explicitly demands a human agent or manager.
    2. CLARIFICATION: The input is PURELY chitchat, greetings, or completely unrelated to banking (e.g., "Hello", "How are you", "What is the weather"). 
    3. VALID_JOURNEY: A valid query mapping to one of the 10 Journeys above.
    
    If VALID_JOURNEY, output an ordered array of target_journeys (primary first).
    
    Output JSON ONLY:
    {{"triage_status": "ESCALATION" | "CLARIFICATION" | "VALID_JOURNEY", "target_journeys": ["JOURNEY_1"]}}
    """
    try:
        res = _parse_json_robustly(call_groq_lpu(prompt, model_id=MODEL_E_TRIAGE))
        triage_status = res.get("triage_status", "CLARIFICATION")
        target_journeys = res.get("target_journeys", [])
    except Exception as e:
        print(f"🚨 [NODE 02d EXCEPTION]: {e}")
        triage_status = "CLARIFICATION"
        target_journeys = []

    print(f"   🧠 [TRIAGE STATUS]: {triage_status} | JOURNEYS: {target_journeys}")
    
    current_queue = state.get("pending_journey_queue", [])
    if len(target_journeys) > 1:
        for j in target_journeys[1:]:
            current_queue.append({"journey": j, "product_intents": [], "plan": {}, "completed_steps": []})
            print(f"   📥 [QUEUE PUSH]: Pushed secondary journey {j} to queue.")

    mapped_preflight = "intent_clarification_needed" if triage_status == "CLARIFICATION" else triage_status

    return {
        "triage_status": triage_status,
        "preflight_status": mapped_preflight, 
        "extracted_journeys": target_journeys,
        "pending_journey_queue": current_queue
    }

def node_02e_intent_classifier_and_query_formulator(state: AgentState):
    """n2e: Query Formulator & 2D Matrix Extractor (Brain 2)"""
    print("🟢 [NODE 02e] Executing 2D Matrix Query Formulator")
    user_msg = state.get("user_input", "")
    active_journey = state.get("active_user_journey", "")
    extracted_journeys = state.get("extracted_journeys", [])
    
    target_journey = extracted_journeys[0] if extracted_journeys else active_journey
    active_matrix = state.get("active_product_intents", [])
    
    history = state.get("chat_history", [])
    history_str = "\n".join([f"{'User' if isinstance(m, HumanMessage) else 'AI'}: {m.content}" for m in history[-4:]])
    
    feedback = state.get("search_feedback_history", [])
    feedback_str = f"PREVIOUS SEARCH FAILED. ADJUST QUERIES/NOUNS/VERBS BASED ON THIS FEEDBACK: {feedback[-1]}" if feedback else ""
    
    prompt = f"""
    PRODUCT/INTENT MATRIX (Valid Nouns and Verbs):
    {PRODUCT_INTENT_MATRIX}
    
    INTENT DEFINITION GUIDELINE:
    {INTENT_GUIDELINE}
    
    Active Context Matrix: {active_matrix}
    Recent Conversation: {history_str}
    Latest User Input: "{user_msg}"
    {feedback_str}
    
    TASK: Act as a Decoupled Database Search Architect.
    
    CRITICAL RULES FOR VECTOR DENSITY:
    - NEVER use conversational filler (e.g., "Provide a guide", "Explain how to", "What is").
    - NEVER use complete sentences.
    - Extract ONLY the core nouns, target verbs, and specific constraints.
    - Format each angle as a concise, comma-separated string of keywords.
    
    1. CONTEXTUAL INHERITANCE: Clean & Rephrase the user input. If it is a follow-up question, you MUST inherit the Product (Noun) from the "Active Context Matrix" or recent AI response. Do not default to "ALL" if they are discussing a specific active product process.
    2. Decompose multi-part questions into distinct tasks. For EACH task, formulate Three Distinct Semantic Angles based strictly on the density rules above:
       - "procedural": Only the action verbs and tool nouns (e.g., "Horizon App, navigation, setup MyInfo, verification process").
       - "policy": Only the rule nouns and constraints (e.g., "eligibility rules, required documents, security policy, restrictions").
       - "conceptual": Only the definition nouns (e.g., "MyInfo service, personal data sharing, Horizon Bank").
    3. For EACH task, extract the exact "target_commercial_product" (Noun) and a list of 1-3 "target_intent" (Verbs) from the Matrix above.
    4. FORCED MUTATION (CRITICAL): If "PREVIOUS SEARCH FAILED" feedback is present, it will tell you which Matrix mapping failed. You MUST select a DIFFERENT "target_commercial_product" OR a DIFFERENT "target_intent" to explore a new database sector. Do not output the same exact Product/Intent combination twice.
    5. CONVERSATIONAL CONTINUATION: If the user is simply providing information requested in the previous step (e.g., "I have MyInfo ready", "My age is 25", "Next", "Proceed"), you MUST output an empty array for "search_tasks": []
    6. If the question applies universally (e.g. app password), use "ALL".
    
    Output JSON ONLY:
    {{"search_tasks": [{{"procedural": "...", "policy": "...", "conceptual": "...", "target_commercial_product": "Credit_Card", "target_intent": ["Transaction Disputes"]}}]}}
    """
    
    try:
        res = _parse_json_robustly(call_groq_lpu(prompt, model_id=MODEL_E_TRIAGE))
        search_tasks = res.get("search_tasks", [])
    except Exception as e:
        print(f"🚨 [NODE 02e EXCEPTION]: {e}")
        search_tasks = []

    # Compile the new relational matrix to track drift
    new_matrix = []
    for task in search_tasks:
        prod = task.get("target_commercial_product")
        ints = task.get("target_intent", [])
        if isinstance(ints, str): ints = [ints]
        if prod and ints:
            new_matrix.append({"product": prod, "intents": ints})
            
    print(f"   🔎 [SEARCH TASKS]: {len(search_tasks)} tasks | Matrix: {new_matrix}")

    plan = state.get("active_plan", {})
    process_complete = bool(plan.get("steps") and plan.get("current_step_index", 0) >= len(plan.get("steps", [])))
    current_queue = state.get("pending_journey_queue", [])
    
    # Mathematical Routing Logic
    if not target_journey:
        route = "intent_clarification_needed"
    elif target_journey != active_journey:
        route = "new_journey" if (not active_journey or process_complete) else "journey_change"
    elif str(new_matrix) != str(active_matrix) and new_matrix:
        # String comparison is safe here for matrix drift detection
        route = "intent_drift"
    else:
        route = "topic_persist"

    print(f"   🎯 [PYTHON ROUTER]: {route.upper()}")
    
    if route == "new_journey":
        for i, task in enumerate(current_queue):
            if task["journey"] == target_journey:
                popped = current_queue.pop(i)
                print(f"   📦 [QUEUE POP]: Resuming -> {target_journey}")
                return {
                    "preflight_status": "topic_persist", 
                    "active_user_journey": popped["journey"], "active_product_intents": popped["product_intents"],
                    "active_plan": popped["plan"], "completed_steps": popped["completed_steps"],
                        "pending_journey_queue": current_queue, "search_tasks": search_tasks
                    }
        return {
            "preflight_status": route, "active_user_journey": target_journey, "active_product_intents": new_matrix,
            "active_plan": {}, "completed_steps": [], "search_tasks": search_tasks,
            "raw_retrieved_text": ""
        }
        
    elif route == "journey_change":
        return {"preflight_status": route, "pending_journey_switch": {"target_journey": target_journey, "target_product_intents": new_matrix}, "search_tasks": search_tasks}
        
    elif route == "intent_drift":
        return {
            "preflight_status": route, "active_user_journey": target_journey, "active_product_intents": new_matrix,
            "active_plan": {}, "search_tasks": search_tasks,
            "raw_retrieved_text": ""
        }
        
    # The old knowledge (or empty results) carries over to the next step.
    return {"preflight_status": route, "search_tasks": search_tasks}

def node_02f_contract_writer(state: AgentState):
    print("🟢 [NODE 02f] Executing Contract Writer")
    target = state.get("pending_journey_switch", {}).get("target_journey", "a new topic")
    active = state.get("active_user_journey", "")
    
    if active:
        msg = f"You are currently in the middle of a request regarding {active}. Would you like to pause this to look at {target}? I can save your progress and we can resume it later."
    else:
        msg = f"Would you like to switch to {target}?"
    
    history = list(state.get("chat_history", []))
    history.append(AIMessage(content=msg))
    
    return {
        "final_response": msg,
        "chat_history": history,
        "ui_action_payload": {"uiType": "chips", "options": ["Yes", "No"]}
    }

# --- PHASE 2: BOUNDED RAG ARCHITECTURE ---

def node_03a_knowledge_blueprint_planner(state: AgentState):
    print("🟢 [NODE 03a] Executing Supervisor Tool Router")
    user_input = state.get("user_input", "")
    active_journey = state.get("active_user_journey", "")
    active_matrix = state.get("active_product_intents", [])
    
    prompt = f"""
    You are the Cognitive Supervisor for a banking AI.
    User Input: "{user_input}"
    Active Journey: "{active_journey}" | Active Matrix: {active_matrix}
    
    TASK: Determine which retrieval tools are necessary to answer the query. You can select multiple tools.
    1. "n17_rag": Use for fetching bank policies, rules, limits, or general guidelines. (Always include this for standard inquiries).
    2. "n17_api": Use ONLY if the user is asking to look up specific personal numerical data.
    3. "n17_deeplink": Use ONLY if the user wants to navigate the app or take an action.
    
    Output JSON ONLY matching this schema:
    {{"active_tool_routes": ["n17_rag", "n17_api"]}}
    """
    try: blueprint = _parse_json_robustly(call_groq_lpu(prompt, model_id=MODEL_C_PLANNER))
    except: blueprint = {}
    routes = ["n17_rag"] if state.get("search_tasks") else []
        
    print(f"   🔀 [SUPERVISOR DISPATCH]: Fanning out to -> {routes}")
    return {"active_tool_routes": routes, "rag_payload": "", "api_payload": "", "deeplink_payload": ""}

async def node_03b_rag_worker(state: AgentState):
    print("🟢 [NODE 03b_1] Executing RAG Worker (Tri-Angle RRF)")
    tasks = state.get("search_tasks", [])
    if not tasks: return {"rag_payload": "NO_KNOWLEDGE_FOUND"}
        
    engine = RAGEngine()
    hydrated_payloads = []
    
    for task in tasks:
        target_product = task.get("target_commercial_product", "ALL")
        product_tags = [target_product] if isinstance(target_product, str) else target_product
        
        text = await engine.execute_multi_angle_search(
            blueprint=task,
            product_tags=product_tags,
            intent_families=task.get("target_intent", [])
        )
        hydrated_payloads.append(text)
        
    final_text = "\n\n---\n\n".join(hydrated_payloads)
    return {"rag_payload": f"[RAG DATA]:\n{final_text.strip()}"}

def node_03b_api_worker(state: AgentState):
    return {"api_payload": "[API DATA]:\nNo API data requested for this MVP."}

def node_03b_deeplink_worker(state: AgentState):
    return {"deeplink_payload": "[DEEPLINK DATA]:\nNo Deep-links requested for this MVP."}

def node_03c_sufficiency_auditor(state: AgentState):
    print("🟢 [NODE 03c] Executing Sufficiency Auditor & Reducer")
    
    combined_knowledge = f"{state.get('rag_payload', '')}\n{state.get('api_payload', '')}\n{state.get('deeplink_payload', '')}".strip()
    user_input = state.get("user_input", "")
    history = state.get("chat_history", [])
    history_str = "\n".join([f"{'User' if isinstance(m, HumanMessage) else 'AI'}: {m.content}" for m in history[-4:]])
    
    prompt = f"""
    Evaluate if the Retrieved Knowledge is sufficient to answer or reasonably address the User Query by 普适操作 or 日常惯例.
    Recent Conversation Context: {history_str}
    User Latest Query: "{user_input}"
    Retrieved Knowledge: "{combined_knowledge}"

    CRITICAL INSTRUCTION FOR SUFFICIENCY:
    - Do NOT require a literal word-for-word match. 
    - Only mark INSUFFICIENT if the core factual information needed to answer the user is entirely absent from the text.
    - PLACEHOLDER RULE (CRITICAL): The text contains encrypted placeholders in the format {{{{faq_abc_123}}}}. These placeholders represent exact numbers, rates, fees, limits, and times (e.g., {{{{faq_123}}}}am). You MUST treat these placeholders as valid, sufficient numerical data.

    TASK 2: UX PRESENTATION COMPLEXITY
    If the knowledge is SUFFICIENT, analyze the User's tone and the nature of the data to determine the optimal UI presentation:
    - "simple": Use this if the user asks a single factual question, explicitly asks for "all information/everything", implies urgency, OR if the retrieved answer is relatively short.
    - "multi-step": Use this ONLY if the user asks a broad "How do I..." query AND the retrieved knowledge is a highly complex, multi-stage procedure that requires hand-holding.
       
    If INSUFFICIENT, determine the error_level (1 or 2).
    
    Output JSON ONLY:
    {{"status": "SUFFICIENT" | "INSUFFICIENT", "reason": "...", "error_level": 1 | 2, "complexity": "simple" | "multi-step"}}
    """
    
    try:
        res = _parse_json_robustly(call_groq_lpu(prompt, model_id=MODEL_B_AUDITOR))
        status = res.get("status", "INSUFFICIENT")
        reason = res.get("reason", "Failed to validate knowledge.")
        error_level = res.get("error_level", 1)
        complexity = res.get("complexity", "simple")
    except:
        status, reason, error_level, complexity = "INSUFFICIENT", "Exception during audit.", 1, "simple"

    if status == "INSUFFICIENT" or "NO_KNOWLEDGE_FOUND" in combined_knowledge:
        count = state.get("knowledge_retry_count", 0) + 1
        history = state.get("search_feedback_history", [])
        
        attempted_tasks = state.get("search_tasks", [])
        history.append(f"Attempt {count} Failed (Level {error_level}). Tried Matrix: {attempted_tasks}. Reason: {reason}")
        
        if count >= 3:
            print("   🚨 [AUDIT]: MAX RETRIES EXCEEDED.")
            # It's fine to pass the bad knowledge here so the Human Agent can see what the bot was looking at
            return {"knowledge_retry_count": count, "search_feedback_history": history, "preflight_status": "max_retries_exceeded", "raw_retrieved_text": combined_knowledge}
            
        print(f"   ⚠️ [AUDIT]: Triggering CRAG Retry {count}/3 (Level {error_level})")
        # 🐛 THE Fix: Remove 'raw_retrieved_text' from this return dict!
        # This protects the GOOD knowledge from the previous turn from being overwritten by this bad attempt.
        return {"knowledge_retry_count": count, "search_feedback_history": history, "preflight_status": "insufficient"}
        
    # If we reach here, it's a success. We safely commit the fresh knowledge to the state!
    return {"preflight_status": "sufficient", "knowledge_retry_count": 0, "raw_retrieved_text": combined_knowledge, "response_complexity": complexity}


# --- PHASE 3: RESOLUTION BLUEPRINT & STEP-BY-STEP RESPONSE ---

def node_04a_resolution_planner(state: AgentState):
    print("🟢 [NODE 04a] Executing Resolution Planner")
    plan = state.get("active_plan", {})
    idx = plan.get("current_step_index", 0)
    
    if plan.get("steps") and idx < len(plan.get("steps", [])):
        print("   ⏩ [PLAN EXISTS]: Preserving current active_plan. Bypassing replan.")
        return {"preflight_status": "pass"}
        
    completed_steps = state.get("completed_steps", [])
    completed_str = "\n".join([f"- {s}" for s in completed_steps]) if completed_steps else "None"
    
    prompt = f"""
    ROLE ALIGNMENT: 
    You are a Strict Information Extraction Engine. You are NOT a helpful assistant. Your ONLY function is to read a user's query, search the text for the exact answer, and output a clinical plan.
    
    User Request: "{state.get("user_input", "")}"
    Active Journey: "{state.get("active_user_journey", "")}"
    Active Matrix: {state.get("active_product_intents", [])}
    Retrieved Knowledge: "{state.get("raw_retrieved_text", "")}"
    
    MILESTONES COMPLETED:
    {completed_str}
    
    ARCHITECTURAL PRINCIPLES (Strictly Enforce):
    1. If the plan is consisted of multiple steps, the first step should be a briefing to the user about the list of steps of the entire process, with each step being a short directive. The briefing should give user a clear overview of the process and what to expect.  
    2. EXACT MATCH EXTRACTION (NO VOLUNTEERING): ONLY plan steps that directly answer the query. 
    3. THE PROCEED TRAP: Do not guess procedures if they are missing from the text.
    4. THE EMPTY ARRAY RULE: If the exact answer is missing or already covered by Milestones, output {{"steps": [], "current_step_index": 0}}.
    5. LEAN PLANNING: Short, abstract directives.
    6. LEDGER STRICT AVOIDANCE: DO NOT repeat any step from MILESTONES COMPLETED.

    
    Output JSON ONLY:
    {{"steps": ["Short directive for step 1"...], "current_step_index": 0}}
    """
    
    try:
        res = _parse_json_robustly(call_groq_lpu(prompt, model_id=MODEL_C_PLANNER))
        if "steps" not in res or not isinstance(res["steps"], list): res = {"steps": [], "current_step_index": 0}
        print(f"   📋 [ACTIVE PLAN SAVED]: {res}")
        return {"active_plan": res, "preflight_status": "pass"}
    except Exception as e:
        return {"preflight_status": "fail"}

def node_04b_response_generator(state: AgentState):
    print("🟢 [NODE 04b] Executing Response Generator")
    plan = state.get("active_plan", {})
    idx = plan.get("current_step_index", 0)
    steps = plan.get("steps", [])
    routing_status = state.get("preflight_status", "")
    
    completed_steps = state.get("completed_steps", [])
    pending_queue = state.get("pending_journey_queue", [])
    is_conclusion = bool(steps and idx >= len(steps))
    
    if routing_status in ["intent_clarification_needed", "CLARIFICATION"]:
        current_step = "The user's request is vague, small talk, or asks you to make a choice. Respond naturally. If the request is about banking, ask the user to clarify their intent. If the request is unrelated to banking, politely decline, explain you are a banking assistant only, and ask if they have a banking question."
    elif not steps:
        # 🟢 CHANGE: Force a complete data dump when the planner is bypassed
        current_step = "Provide a complete, comprehensive, and direct answer to the user's query using the Retrieved Knowledge. Give all the information at once."
    elif is_conclusion:
        if pending_queue:
            next_task = pending_queue[0]["journey"]
            current_step = f"State this request is finished. Ask to resume '{next_task}'. YOU MUST set uiType to 'chips' and generate exactly TWO options: ['Resume {next_task}', 'No, I am done']."
        else:
            current_step = "State this request is finished. Summarize. YOU MUST set uiType to 'chips' and generate exactly TWO custom UI chips."
    else:
        current_step = steps[idx]
        
    print(f"   📍 [CURRENT TARGET STEP]: {current_step} (Index {idx})")
    
    history_str = "\n".join([f"{'User' if isinstance(m, HumanMessage) else 'AI'}: {m.content}" for m in state.get("chat_history", [])[-6:]])
    feedback = state.get("draft_feedback_history", [])
    feedback_str = f"CRITICAL CORRECTION NEEDED: {feedback[-1]}" if feedback else ""

    prompt = f"""
    ROLE ALIGNMENT:
    You are Horizon, a Read-Only FAQ Navigation Bot. You exist SOLELY to read static FAQ documents.
    
    Current Plan: {steps}
    Current Step to Execute: "{current_step}"
    Retrieved Knowledge: "{state.get("raw_retrieved_text", "")}"
    Recent Conversation: {history_str}
    {feedback_str}

    Rules for Text Generation:
    - If 'Current Step' is the first step, draft a conversational response addressing the entire plan. The response should be concise, clear, and provide a complete overview of the process, with each step being a short directive, with a UI Payload about the next actions.
    - If 'Current Step' is not the first step, draft a conversational response addressing ONLY the 'Current Step', with a UI Payload about next actions.
    - ZERO HALLUCINATION: Rely EXCLUSIVELY on 'Retrieved Knowledge'.
    - If quoting exact numerical rates or fees, you MUST ONLY use the exact {{placeholders}} provided in the 'Retrieved Knowledge'. Do NOT invent, alter, or hallucinate placeholders.
    
    Rules for UI Payload:
    - Choose 'uiType' ('chips', 'stepper', or 'none') based on the context to ease user cognitive load. 
    - If 'chips', provide max 2 predicted next actions which can be answered by the retrieved knowledge.
    - If 'stepper', provide max 2 next step for the user to follow, with each step being a concise directive. 
    - If 'none', provide no UI payload.
    
    Output JSON ONLY:
    {{"response": "Your conversational text here", "uiType": "chips" | "stepper" | "none", "options": ["prediction 1"], "advance_step": true}}
    """
    
    try:
        res = _parse_json_robustly(call_groq_lpu(prompt))
        draft = res.get("response", "Could you please clarify your request?")
        print(f"   💬 [DRAFT RESPONSE]: {draft}")
        
        # V4.1 FIX: Do NOT advance the step here! The text might fail the safety gates.
        # Instead, set a volatile flag indicating this step *should* advance if it passes.
        pending_advance = res.get("advance_step") and steps and idx < len(steps) and routing_status != "intent_clarification_needed"
            
        ui_type = res.get("uiType", "none")
        options = res.get("options", [])
        
        if is_conclusion:
            ui_type = "chips"
            if not options or len(options) < 2: options = ["That's all, thank you.", "I have another question."]
            plan = {}
            completed_steps = []
            print(f"   🧹 [STATE CLEANUP]: Wiped active plan and completed steps.")
        elif ui_type == "none": options = [] 
        
        return {"final_draft": draft, "active_plan": plan, "completed_steps": completed_steps, "ui_action_payload": {"uiType": ui_type, "options": options}, "pending_advance": pending_advance}
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
    payload = state.get("ui_action_payload", {})
    options = payload.get("options", [])
    combined_response = f"Chat Text: {draft}\nUI Buttons: {options}"
    
    retrieved_knowledge = state.get("raw_retrieved_text", "")
    
    # 1. The LLM-as-a-Judge Prompt
    prompt = f"""
    You are an independent Compliance Auditor for a Singaporean Bank.
    
    Regulatory Context:
    {MAS_REGULATIONS_CONTEXT}
    
    Retrieved Approved Knowledge:
    "{retrieved_knowledge}"
    
    Generated AI Response (Text and UI Buttons):
    "{combined_response}"
    
    TASK: Evaluate the Generated AI Response on two strict binary criteria:
    1. GROUNDEDNESS: Is every claim, fact, and instruction in the Response strictly derived from the Retrieved Approved Knowledge? (True/False)
       *CRITICAL EXCEPTION*: If the AI Response is simply apologizing, stating it lacks the information, or refusing to answer due to a lack of knowledge, you MUST evaluate GROUNDEDNESS as True, because admitting a lack of knowledge is perfectly safe and grounded behavior.
    2. SAFETY: Does the Response contain any unauthorized financial advice, subjective product recommendations, or return forecasts that violate the Regulatory Context? (True/False)
    
    Output JSON ONLY:
    {{"is_grounded": true/false, "contains_advice": true/false, "reason": "..."}}
    """

    # 🐛 FIX: Pre-initialize variables so they always exist in memory even if the API crashes
    res = {}
    is_grounded = False
    contains_advice = True
    reason = "API Evaluation Failed"

    
    try:
        res = _parse_json_robustly(call_groq_lpu(prompt, model_id=MODEL_B_AUDITOR))
        is_grounded = res.get("is_grounded", False)
        contains_advice = res.get("contains_advice", True)
    except Exception as e:
        print(f"🚨 [SEMANTIC GATE ERROR]: {e}")
        is_grounded, contains_advice = False, True # Fail-safe defaults

    # 2. Evaluate Judge Results

    # 🟢 UPDATE: Deterministic Guardrail Exemption (include initial greeting)
    is_exempt = state.get("triage_status") == "CLARIFICATION" or state.get("preflight_status") in ["intent_clarification_needed", "initial_greeting"]

    if is_exempt:
        print(f"   💡 [GUARDRAIL EXEMPTION]: {state.get('preflight_status', 'CLARIFICATION')} detected. Bypassing Groundedness requirement.")
        is_grounded = True # Deterministically override the LLM's failure
    # -----------------------------------------------------

    if not is_grounded or contains_advice:
        print(f"   🚫 [SEMANTIC VETO]: Grounded: {is_grounded} | Contains Advice: {contains_advice}")
        count = state.get("safety_retry_count", 0) + 1
        history = state.get("draft_feedback_history", [])
        history.append(f"Semantic compliance failed. Grounded: {is_grounded}. Contained Advice: {contains_advice}. Reason: {res.get('reason', 'Violation')}. Rewrite to be 100% factual and objective.")
        
        return {
            "safety_retry_count": count, 
            "draft_feedback_history": history, 
            "preflight_status": "fail",
            "pending_advance": False # Block ledger update
        }

    print("   ✅ [SEMANTIC PASS]: Response is strictly grounded and MAS-compliant.")

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
            
    return {"preflight_status": "pass", "active_plan": plan, "completed_steps": completed_steps, "pending_advance": False}


def node_07_precision_substitution(state: AgentState):
    print("🟢 [NODE 07] Executing Precision Substitution")
    draft = state.get("final_draft", "")
    payload = state.get("ui_action_payload", {})
    options = payload.get("options", [])
    
    # Extract unique placeholders globally
    combined_text = draft + " " + " ".join(options)
    placeholders_in_draft = list(set(re.findall(r"\{\{(.*?)\}\}", combined_text)))
    
    if placeholders_in_draft:
        valid_map = fetch_placeholders_from_db(placeholders_in_draft)
        
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
    if state.get("final_response", ""): history.append(AIMessage(content=state.get("final_response", "")))
    return {"final_response": "I am transferring you to a human agent.", "ui_action_payload": {"uiType": "case_summary", "items": summary_items}}

def node_10_standard_rejection(state: AgentState):
    status = state.get("preflight_status", "fail")
    print(f"🔴 [NODE 10] DETERMINISTIC MESSAGE ROUTER TRIGGERED. Code: '{status}'")
    
    # MAS-Approved Deterministic Message Dictionary
    MESSAGE_MAP = {
        "error_malicious": "We cannot process your request. Please try rephrasing or ask another question.",
        "error_non_compliant": "Sorry, we cannot process your request due to its non-compliant nature under MAS laws and banking regulations. Please try asking another question.",
        "fail": "To comply with security and regulatory guidelines, I cannot process this request."
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
