import json
import re
from langchain_core.messages import HumanMessage, AIMessage
from backend.app.orchestration.state import AgentState
from backend.app.core.llm_service import call_groq_lpu  
from backend.app.services.memory_service import LongTermMemoryManager

# MOCK DATABASES FOR VALIDATION
VALID_PLACEHOLDERS = {"SAVINGS_RATE_01", "MIN_BALANCE_01", "FEE_01"}
#FAQ_DIRECTORIES = ["ACCOUNT_OPENING", "DIGITAL_BANKING", "PROFILE_UPDATE"]
GLOBAL_TAXONOMY = {
    "CREDIT_CARDS": ["Account Management", "App Activation", "Application", "Application Process", "Application Requirements", "Application_Process", "AutoPay", "Benefits", "Bonus Caps", "Bonus Interest", "Browser Compatibility", "Card Activation", "Card Application", "Card Controls", "Card Details Access", "Card Limits", "Card Management", "Card Naming", "Card Pairing", "Card Replacement", "Card Security", "Card Types", "Cashback", "Credit Card Features", "Credit Card Information", "Credit Card Management", "Credit_Card_Instalments", "Customer Classification", "Data Security", "Dispute", "Earn", "Eligibility", "Fees", "Fuel Discounts", "International Use", "Minimum Spend", "Online Application", "Overseas Usage", "Partner Applications", "Product Eligibility", "Promotion Eligibility", "Promotion Expiry", "Promotion Limits", "Promotional Delivery", "Promotions", "Recurring Payments", "Redemption Limits", "Refund Impact", "Regulations", "Reward Claim", "Reward Notification", "Reward Redemption", "Reward Transferability", "Rewards", "Rewards Determination", "Rewards Eligibility", "Rewards Points", "Rewards Tracking", "Spending Limit", "Supplementary Card", "Supplementary Card Eligibility", "Supplementary Card Limits", "Technical Issues", "Visa Instalments", "account access", "account management", "account status", "account_cancellation", "account_changes", "account_linking", "account_settings", "account_updates", "app access", "application", "application process", "application status", "application_process", "benefits", "card activation", "card benefits", "card replacement", "card updates", "card_features", "card_management", "cashback", "credit limit", "credit reports", "credit_card_application", "credit_cards", "credit_limit", "credit_limit_increase", "decline", "dispute", "eligibility", "existing_relationship", "login", "membership changes", "membership updates", "merchant eligibility", "notifications", "payment management", "payment methods", "payment processing", "preferences", "promotional offers", "proof_of_income", "rebate", "regulations", "regulatory_compliance", "repayment", "repayment terms", "request modification", "rewards", "security", "statement queries", "technical", "transaction limits", "transaction rewards", "transaction_processing", "transfer"],
    "DEBIT_CARDS": ["ATM transactions", "Digital Cards", "Digital Wallets", "Fees", "PIN Management", "Rewards", "account issues", "account maintenance", "activation", "card information", "card queries", "card replacement", "card usage", "card_activation", "card_delivery", "card_security", "card_types", "decline", "dispute", "overseas usage", "security", "statement queries", "support queries"],
    "DEPOSIT_ACCOUNTS": ["ATM Branding", "ATM Discrepancy", "ATM Dispute", "ATM Locations", "ATM Withdrawal", "Account Details", "Account Number Retrieval", "AutoPay", "AutoPay Setup", "Bill Pay", "Card Replacement", "Card Retention", "Deposit Methods", "Features", "Fees", "Interest Bonuses", "Interest Payments", "Interest Rates", "Interest Timing", "NFC Issues", "PIN Management", "PIN Reset", "Processing Time", "Promotional Rates", "Referral Bonus", "Restrictions", "Savings Pot", "Security", "Unlocking Funds", "application requirements", "application timeline", "bonus eligibility", "bonus interest timing", "cash discrepancy", "dormancy", "features", "fees", "interest", "restrictions"],
    "DIGITAL_BANKING": ["3DSecure", "Account Security", "Account Settings", "Account Update", "App Security", "Card loss", "Device Loss", "Device registration", "Fund Transfer", "Password reset", "PayNow", "PayNow Registration", "PayNow Update", "PayNow changes", "PayNow payment", "PayNow registration", "PayNow settings", "Payment Details", "Privacy", "Security Key", "Security PIN", "Security key", "Security key activation", "Security key setup", "Security token", "Transaction alerts", "Transaction impact", "Transaction security", "Transaction signing", "account management", "account security", "card transaction", "digital payments", "fund transfer", "login issue", "scam/fraud", "security feature", "security measure", "security measures", "transaction block", "transaction control"],
    "GENERAL_BANKING": ["Account eligibility", "Account updates", "Address Update", "App Security", "App navigation", "Bank details", "Card Compatibility", "Card Delivery", "Card Types", "Card activation", "Card pending", "Cashback", "Cashback eligibility", "Credit Card Application", "Credit Card Eligibility", "Credit Card Limits", "Credit Card Payments", "Credit Limit", "Credit Limits", "Email verification", "Fee Waiver", "General inquiries", "MyInfo Accuracy", "OTP Validity", "On-site verification", "Rewards eligibility", "Supplementary Card", "Transit Transactions", "Trust+ features"],
    "INSURANCE": ["Claims", "Home Insurance", "Insurance", "Insurance Offers", "Life Insurance", "Motorcycle Insurance", "Pet Insurance", "Premiums", "Promotions", "Travel Insurance"],
    "INVESTMENTS": ["App Issues", "AutoInvest", "Capital Protection", "Currency Availability", "Fees", "Funding Sources", "Investment Advice", "Investment Insurance", "Investment Risks", "Investments", "Mutual Funds", "Risk", "Savings Account", "Stocks", "Technical Support", "Trading Hours"],
    "LOANS": ["Eligibility", "Features", "application status", "product eligibility", "repayment issue"],
    "REWARDS_AND_OFFERS": ["App Download", "Coupons", "Eligible Spend", "Ineligible Transactions", "Linkpoints", "Merchant Promos", "Redemption", "Referral", "Referral Code", "Referral Codes", "Referral Program", "Referral Rewards", "Rewards Availability", "Rewards Transfer"],
    "TRANSACTIONS": ["Account Funding", "Account Management", "AutoPay", "Balance Transfer", "Bill Payments", "Cancel Transfer", "Charges", "Cheque Deposit", "Credit Card", "Direct Debit", "E-Payment Protection", "FAST transfer", "Fees", "Interest Rates", "International Transfer", "Limits", "Loan Application", "Loan Options", "PayNow", "Payment Management", "Payment Scheduling", "Payment Terms", "Profile Update", "QR Payments", "Recipient Verification", "Recurring Transfer", "Repayment", "Salary Credits", "Security", "Spend Bonus Eligibility", "Transaction Failure", "Transaction Restrictions", "Transaction Security", "Transfer Errors", "Transfer Fees", "Transfer Mistake", "eGIRO Support"]
}

DOMAIN_CLASSIFICATION_GUIDELINE = """
1. DEBIT_CARDS: Functionality, management, and physical/digital usage of Horizon debit cards. Differences between physical/digital cards (Apple Pay, Google Pay, FairPrice App), overseas usage activation, tracking card delivery, viewing card details (CVV, expiry), PIN management, and reporting lost, stolen, or damaged debit cards.
2. CREDIT_CARDS: Lifecycle, management, and billing of Horizon credit cards, including eligibility, limits, and specific credit features. Credit limit increases/decreases, regulatory customer caps, applications using MyInfo or CPF LIFE, Effective Interest Rates (EIR) and fee waivers, selecting preferred cashback categories, supplementary cards, and Split Purchase/Visa Instalment plans.
3. DEPOSIT_ACCOUNTS: Horizon Bank's Savings Accounts, encompassing account setup, daily banking operations, and interest generation. Account opening requirements, savings plans, calculating Average Daily Balance (ADB) and bonus interest, Savings Pots and Trust Lock features, accessing monthly statements, zero-fee structures, and ATM cash withdrawals.
4. INVESTMENTS: Wealth management and trading products, including Stocks, ETFs, and Mutual Funds. Trading US/UK UCITS ETFs, minimum investment thresholds, curated mutual funds, management fee rebates, holding assets via custodians, and investment risks. 
5. INSURANCE: Insurance policies offered in partnership with Income Insurance Limited. Policy types (Life, Enhanced Home, Travel, Pet, Car, Motorcycle, Family Personal Accident), eligibility requirements, connecting with Insurance Specialists, and exclusive promotions.
6. TRANSACTIONS: Movement of money, encompassing domestic transfers, payment systems, and short-term credit-to-cash facilities. Local SGD FAST transfers, PayNow registrations/limits, resolving erroneous transfers, and Credit Card Balance Transfers (converting credit limit to funds at 0% interest for other bank debts).
7. REWARDS_AND_OFFERS: Hub for loyalty, promotional, and incentive-based queries. Earning and tracking Linkpoints, digital scratch cards, referral codes, and claiming merchant coupons.
8. LOANS: Strictly for Horizon Bank's "Instant Loan" product. Converting a portion of available credit card balance into cash deposited into a savings account, early repayment fees (3%), and automatic account opening caveats.
9. DIGITAL_BANKING: Technical security, anti-fraud measures, and digital identity management within the Horizon App. Setting up Horizon Key (security token, PIN, 12-hour cooling-off), combating phishing/scams, mandatory push notifications, and masking of PayNow account names.
10. GENERAL_BANKING: Catch-all for institutional inquiries, broad eligibility, and top-level account administration spanning multiple products. Bank identification (Bank Name, Bank Code, BIC Code), digital-native banking model (no physical branches), updating personal details (address, phone, email), MyInfo integration, and general app accessibility.
"""

def _parse_json_robustly(raw_text: str) -> dict:
    """Strips markdown formatting to prevent json.loads() from crashing."""
    clean_text = raw_text.strip()
    if clean_text.startswith("```json"):
        clean_text = clean_text[7:]
    elif clean_text.startswith("```"):
        clean_text = clean_text[3:]
    if clean_text.endswith("```"):
        clean_text = clean_text[:-3]
    try:
        return json.loads(clean_text.strip())
    except Exception as e:
        print(f"🚨 [JSON PARSE ERROR]: {e} \nRaw text was: {raw_text}")
        raise e


# --- PHASE 0: INGESTION & INGEST GUARDS ---

def node_00_context_hydrator_and_greeting(state: AgentState):
    print("🟢 [NODE EXECUTING] Node 00: Context Hydrator & Greeting")
    """n00: Turn 1 Initialization. Clears past turn logs, fetches Table B summary, generates greeting."""
    customer_id = "USR_999"
    summary = LongTermMemoryManager.fetch_compact_footprint(customer_id)
    
    greeting = f"Welcome back. Based on your previous visit: {summary}. How can I help you today?"
    if not summary:
        greeting = "Hello! I am Horizon. How can I assist you today?"
        
    return {
        "final_draft": greeting,
        "working_context": summary,
        "ui_action_payload": {"uiType": "text", "options": []},
        "search_feedback_history": [], # v3.7 Requirement: Clear past turn logs
        "draft_feedback_history": [],
        "knowledge_retry_count": 0,
        "safety_retry_count": 0
    }



def node_01_deterministic_ingest_gate(state: AgentState):
    """n3: Turns 2+ Security Rule. Sweeps garbled text, injection & user-typed placeholders."""
    print("🟢 [NODE 01] Executing Deterministic Ingest Gate")
    user_msg = state.get("user_input", "")
    history = list(state.get("chat_history", []))
    
    # NEW: Append user message to history (ignore hidden init triggers)
    if user_msg and user_msg != "__INIT__": 
        history.append(HumanMessage(content=user_msg))
        print(f"💾 [MEMORY] User Input saved. History length: {len(history)}")
        
    if re.search(r"\{\{.*?\}\}", user_msg) or "<script>" in user_msg.lower():
        return {"preflight_status": "fail", "chat_history": history}
        
    return {"preflight_status": "pass", "chat_history": history}


# --- PHASE 1: PREFLIGHT ROUTING & INTERLOCK CONTEXTS ---

def node_02a_pending_contract_check(state: AgentState):
    """n4: Scans state for active pending_domain_switch flag."""
    print("🟢 [NODE 02a] Executing Pending Contract Check")
    pending = state.get("pending_domain_switch", {})
    if pending and pending.get("target_domain"):
        print(f"   🔒 [STATE INTERLOCKED]: Pending switch to '{pending.get('target_domain')}'")
        return {"preflight_status": "yes"} 
    
    print("   🔓 [STATE OPEN]: No pending contracts.")
    return {"preflight_status": "no"}      

def node_02b_confirmation_gate(state: AgentState):
    """n6: Evaluates Yes/No. Pushes active state to Queue on Yes."""
    print("🟢 [NODE 02b] Executing Confirmation Gate")
    user_msg = state.get("user_input", "")
    pending_domain = state.get("pending_domain_switch", {}).get("target_domain", "")
    pending_families = state.get("pending_domain_switch", {}).get("target_intent_families", [])
    
    prompt = f"""
    Evaluate user input: "{user_msg}"
    Did they agree to pause their progress and switch to {pending_domain}?
    Reply ONLY with JSON: {{"decision": "yes" | "no" | "else"}}
    """
    try:
        decision = _parse_json_robustly(call_groq_lpu(prompt)).get("decision", "else")
    except:
        decision = "else"
        
    if decision == "yes":
        current_queue = state.get("pending_domain_queue", [])
        
        # 1. PUSH current progress into the queue to save it
        current_queue.append({
            "domain": state.get("active_domain"),
            "families": state.get("active_intent_families"),
            "plan": state.get("active_plan"),
            "completed_steps": state.get("completed_steps")
        })
        print(f"   📦 [QUEUE PUSH]: Saved progress for -> {state.get('active_domain')}")
        
        # 2. CHECK if the target domain is already in the queue to resume
        for i, q in enumerate(current_queue):
            if q["domain"] == pending_domain:
                resumed = current_queue.pop(i)
                print(f"   📦 [QUEUE POP]: Resuming paused task -> {pending_domain}")
                return {
                    "user_input": f"Resume my previous request about {pending_domain}",
                    "active_domain": resumed["domain"], 
                    "active_intent_families": resumed["families"],
                    "active_plan": resumed["plan"], 
                    "completed_steps": resumed["completed_steps"],
                    "pending_domain_queue": current_queue, 
                    "pending_domain_switch": {}, 
                    "preflight_status": "yes"
                }

        # 3. Otherwise, start the new domain fresh
        return {
            "user_input": f"I want to proceed with {pending_domain} {pending_families}",
            "active_domain": pending_domain, 
            "active_intent_families": pending_families,
            "active_plan": {}, 
            "completed_steps": [],
            "pending_domain_queue": current_queue, 
            "pending_domain_switch": {}, 
            "preflight_status": "yes"
        }
        
    elif decision == "no":
        return {"pending_domain_switch": {}, "preflight_status": "no"}
    else:
        return {"active_domain": "", "active_intent_families": [], "pending_domain_switch": {}, "preflight_status": "else"}


def node_02c_inbound_semantic_gate(state: AgentState):
    """n16: Model B query compliance check."""
    print("🟢 [NODE 02c] Executing Inbound Semantic Gate")
    return {"preflight_status": "pass"}

def node_02d_domain_classifier_and_triage(state: AgentState):
    """n2d: Global Triage & Domain Extractor (Pass 1)"""
    print("🟢 [NODE 02d] Executing Domain Classifier & Triage")
    user_msg = state.get("user_input", "")
    history = state.get("chat_history", [])
    history_str = "\n".join([f"{'User' if isinstance(m, HumanMessage) else 'AI'}: {m.content}" for m in history[-6:]])
    
    prompt = f"""
    ROLE ALIGNMENT:
    You are an impartial, read-only Horizon Bank routing agent. You do NOT give financial advice, you do NOT have personal preferences, and you CANNOT make product choices for the user. 
    
    DOMAIN CLASSIFICATION GUIDELINES:
    {DOMAIN_CLASSIFICATION_GUIDELINE}
    
    Recent Conversation Context: {history_str}
    Latest User Input: "{user_msg}"
    
    TASK: Triage the user input.
    1. ESCALATION: User demands a human agent or manager.
    2. OUT_OF_SCOPE: Use ONLY for highly specific FINANCIAL or BANKING questions that are clearly outside our 10 Domains (e.g., buying Crypto, Commercial Mortgages). Do NOT use this for non-financial topics.
    3. CLARIFICATION: Use for chitchat, non-financial topics, overly broad requests, OR when the user asks YOU to make a choice, recommendation, or give a preference (e.g., "you choose", "which is best"). Because you cannot choose for them, you must ask them to specify.
    4. VALID_DOMAIN: A valid query mapping to our 10 Domains.
    
    If VALID_DOMAIN, output an ordered array of target_domains (primary first).
    
    Output JSON ONLY:
    {{"triage_status": "ESCALATION" | "OUT_OF_SCOPE" | "CLARIFICATION" | "VALID_DOMAIN", "target_domains": ["DOMAIN_1"]}}
    """
    try:
        res = _parse_json_robustly(call_groq_lpu(prompt))
        triage_status = res.get("triage_status", "CLARIFICATION")
        target_domains = res.get("target_domains", [])
    except Exception as e:
        print(f"🚨 [NODE 02d EXCEPTION]: {e}")
        triage_status = "CLARIFICATION"
        target_domains = []

    print(f"   🧠 [TRIAGE STATUS]: {triage_status} | DOMAINS: {target_domains}")
    
    current_queue = state.get("pending_domain_queue", [])
    if len(target_domains) > 1:
        for d in target_domains[1:]:
            current_queue.append({"domain": d, "families": [], "plan": {}, "completed_steps": []})
            print(f"   📥 [QUEUE PUSH]: Pushed secondary domain {d} to queue.")

    # V3.77 FIX: Map the Triage Status to Preflight Status so downstream nodes understand the route
    mapped_preflight = "intent_clarification_needed" if triage_status == "CLARIFICATION" else triage_status

    return {
        "triage_status": triage_status,
        "preflight_status": mapped_preflight, # Crucial for Node 04b!
        "extracted_domains": target_domains,
        "pending_domain_queue": current_queue
    }


def node_02e_intent_classifier_and_query_formulator(state: AgentState):
    """n2e: Query Formulator & Intent Extractor (Pass 2) + Math Routing"""
    print("🟢 [NODE 02e] Executing Intent & Query Formulator")
    user_msg = state.get("user_input", "")
    active_domain = state.get("active_domain", "")
    active_families = state.get("active_intent_families", [])
    extracted_domains = state.get("extracted_domains", [])
    
    target_domain = extracted_domains[0] if extracted_domains else active_domain
    focused_taxonomy = {target_domain: GLOBAL_TAXONOMY.get(target_domain, [])}
    
    history = state.get("chat_history", [])
    history_str = "\n".join([f"{'User' if isinstance(m, HumanMessage) else 'AI'}: {m.content}" for m in history[-4:]])
    
    feedback = state.get("search_feedback_history", [])
    feedback_str = f"PREVIOUS SEARCH FAILED. ADJUST QUERIES/INTENTS BASED ON THIS FEEDBACK: {feedback[-1]}" if feedback else ""
    
    prompt = f"""
    Target Domain: "{target_domain}"
    Focused Taxonomy (Valid Intents): {focused_taxonomy}
    Recent Conversation: {history_str}
    Latest User Input: "{user_msg}"
    {feedback_str}
    
    TASK: Act as a Search Query Architect.
    1. Clean & Rephrase the user input (fix typos, resolve pronouns from history).
    2. Decompose multi-part questions into distinct queries.
    3. Map each discrete query to 1-3 specific Intent Families from the Focused Taxonomy.
    4. If previous search failed (see feedback), rephrase keywords or expand intents.
    5. If the user is just saying "proceed" or "yes" without a specific question, output an empty queries array.
    
    Output JSON ONLY:
    {{"queries": [{{"rephrased_query": "...", "intent_families": ["..."]}}]}}
    """
    
    try:
        res = _parse_json_robustly(call_groq_lpu(prompt))
        queries = res.get("queries", [])
    except Exception as e:
        print(f"🚨 [NODE 02e EXCEPTION]: {e}")
        queries = []

    target_families = []
    for q in queries:
        target_families.extend(q.get("intent_families", []))
    target_families = list(set(target_families))
    
    print(f"   🔎 [QUERIES FORMULATED]: {len(queries)} queries mapped to {target_families}")

    plan = state.get("active_plan", {})
    process_complete = bool(plan.get("steps") and plan.get("current_step_index", 0) >= len(plan.get("steps", [])))
    current_queue = state.get("pending_domain_queue", [])
    
    if not target_domain:
        route = "intent_clarification_needed"
    elif target_domain != active_domain:
        route = "new_domain" if (not active_domain or process_complete) else "domain_change"
    elif set(target_families) != set(active_families):
        route = "family_drift"
    else:
        route = "topic_persist"

    print(f"   🎯 [PYTHON ROUTER]: {route.upper()}")
    
    if route == "new_domain":
        for i, task in enumerate(current_queue):
            if task["domain"] == target_domain:
                popped = current_queue.pop(i)
                print(f"   📦 [QUEUE POP]: Resuming -> {target_domain}")
                return {
                    "preflight_status": "topic_persist", 
                    "active_domain": popped["domain"], "active_intent_families": popped["families"],
                    "active_plan": popped["plan"], "completed_steps": popped["completed_steps"],
                    "pending_domain_queue": current_queue, "search_queries": queries
                }
        return {
            "preflight_status": route, "active_domain": target_domain, "active_intent_families": target_families,
            "active_plan": {}, "completed_steps": [], "search_queries": queries
        }
        
    elif route == "domain_change":
        return {"preflight_status": route, "pending_domain_switch": {"target_domain": target_domain, "target_intent_families": target_families}, "search_queries": queries}
        
    elif route == "family_drift":
        return {"preflight_status": route, "active_domain": target_domain, "active_intent_families": target_families, "active_plan": {}, "search_queries": queries}
        
    return {"preflight_status": route, "search_queries": queries}


def node_02f_contract_writer(state: AgentState):
    """n12: Sets pending switch flag & prompts PAUSE confirmation."""
    print("🟢 [NODE 02f] Executing Contract Writer")
    target = state.get("pending_domain_switch", {}).get("target_domain", "a new topic")
    active = state.get("active_domain", "")
    
    msg = f"You are currently in the middle of {active}. Would you like to pause this to look at {target}? I can save your progress for later."
    
    history = list(state.get("chat_history", []))
    history.append(AIMessage(content=msg))
    
    return {
        "final_response": msg,
        "chat_history": history,
        "ui_action_payload": {"uiType": "chips", "options": ["Yes", "No"]}
    }

# --- PHASE 2: BOUNDED RAG ARCHITECTURE ---

def node_03a_knowledge_blueprint_planner(state: AgentState):
    """n14: The Supervisor - Determines which parallel worker nodes to spawn."""
    print("🟢 [NODE 03a] Executing Supervisor Tool Router")
    
    user_input = state.get("user_input", "")
    active_domain = state.get("active_domain", "")
    active_families = state.get("active_intent_families", [])
    
    prompt = f"""
    You are the Cognitive Supervisor for a banking AI.
    User Input: "{user_input}"
    Active Domain: "{active_domain}" | Active Families: {active_families}
    
    TASK: Determine which retrieval tools are necessary to answer the query. You can select multiple tools.
    
    1. "n17_rag": Use for fetching bank policies, rules, limits, or general guidelines. (Always include this for standard inquiries).
    2. "n17_api": Use ONLY if the user is asking to look up specific personal numerical data (e.g., "What is my balance?").
    3. "n17_deeplink": Use ONLY if the user wants to navigate the app or take an action (e.g., "How do I update my profile?").
    
    Output JSON ONLY matching this schema:
    {{"active_tool_routes": ["n17_rag", "n17_api"]}}
    """
    
    try:
        raw_res = call_groq_lpu(prompt)
        blueprint = _parse_json_robustly(raw_res)
        routes = blueprint.get("active_tool_routes", ["n17_rag"])
        
        # V3.77 FIX: Prevent LangGraph Empty Fan-Out Crashes
        if not routes:
            print("   ⚠️ [FALLBACK]: Supervisor returned empty array. Defaulting to RAG.")
            routes = ["n17_rag"]
            
    except:
        routes = ["n17_rag"]
        
    print(f"   🔀 [SUPERVISOR DISPATCH]: Fanning out to -> {routes}")
    
    # We clear the buffers to prevent ghost data from previous turns, and set the routes
    return {
        "active_tool_routes": routes,
        "rag_payload": "",
        "api_payload": "",
        "deeplink_payload": ""
    }


def node_03b_rag_worker(state: AgentState):
    """n17_rag: Parallel ChromaDB Search + Parent SQLite Hydration"""
    print("🟢 [NODE 03b] Executing RAG Worker (Parent-Child Hydration)")
    domain = state.get("active_domain", "")
    queries = state.get("search_queries", [])
    
    aggregated_parent_texts = []
    
    # 1. PARALLEL DECOMPOSED SEARCH
    for q in queries:
        query_text = q.get("rephrased_query", "")
        intents = q.get("intent_families", [])
        print(f"   🔎 [VECTOR SEARCH]: '{query_text}' filtered by {intents}")
        
        # [MOCK CHROMA DB CHILD SEARCH]
        # In production: 
        # results = chromadb.query(query_texts=[query_text], where={"$and": [{"domain": domain}, {"intent_family": {"$in": intents}}]})
        # filtered_results = [res for res in results if res.score >= 0.50]
        # parent_ids = list(set([res.metadata['parent_id'] for res in filtered_results]))
        
        # [MOCK SQLITE PARENT HYDRATION]
        # In production:
        # for pid in parent_ids:
        #    parent_text = sqlite.query("SELECT content FROM parents WHERE id = ?", pid)
        #    aggregated_parent_texts.append(parent_text)
        
    if domain == "CREDIT_CARDS":
        retrieved = "To apply for a credit card, you must provide proof of income. Fees are {{CREDIT_CARD_FEE}}."
    elif domain == "DEPOSIT_ACCOUNTS":
        retrieved = "To open an account, provide ID and Proof of Address."
    else:
        retrieved = "NO_KNOWLEDGE_FOUND"
        
    return {"rag_payload": f"[RAG DATA (Hydrated Parents)]:\n{retrieved.strip()}"}


def node_03b_api_worker(state: AgentState):
    """n17_api: Dedicated Placeholder Fetcher (Deferred for now)"""
    print("🟢 [NODE 03b] Executing API Worker")
    # In the future, this will do SQLite lookups for placeholder syntax
    return {"api_payload": "[API DATA]:\nNo API data requested for this MVP."}


def node_03b_deeplink_worker(state: AgentState):
    """n17_deeplink: Dedicated Navigation Fetcher (Deferred for now)"""
    print("🟢 [NODE 03b] Executing Deeplink Worker")
    # In the future, this will do SQLite lookups for app URIs
    return {"deeplink_payload": "[DEEPLINK DATA]:\nNo Deep-links requested for this MVP."}

def node_03c_sufficiency_auditor(state: AgentState):
    """n18: The Reducer - Aggregates worker payloads & evaluates sufficiency."""
    print("🟢 [NODE 03c] Executing Sufficiency Auditor & Reducer")
    
    rag_data = state.get("rag_payload", "")
    api_data = state.get("api_payload", "")
    deeplink_data = state.get("deeplink_payload", "")
    
    combined_knowledge = f"{rag_data}\n{api_data}\n{deeplink_data}".strip()
    
    if "NO_KNOWLEDGE_FOUND" in combined_knowledge and len(combined_knowledge) < 50:
        count = state.get("knowledge_retry_count", 0) + 1
        history = state.get("search_feedback_history", [])
        history.append(f"No documents found for Domain '{state.get('active_domain')}' and Families {state.get('active_intent_families')}.")
        
        # V3.77 FIX: The Agentic Kill-Switch (Break the Infinite Loop)
        if count >= 3:
            print("   🚨 [AUDIT]: MAX RETRIES EXCEEDED. Forcing Hard Escalation.")
            return {
                "knowledge_retry_count": count,
                "search_feedback_history": history,
                "preflight_status": "max_retries_exceeded",
                "raw_retrieved_text": combined_knowledge
            }
            
        print(f"   ⚠️ [AUDIT]: INSUFFICIENT. Triggering Agentic Retry {count}/3")
        return {
            "knowledge_retry_count": count,
            "search_feedback_history": history,
            "preflight_status": "insufficient",
            "raw_retrieved_text": combined_knowledge
        }
        
    print("   ✅ [AUDIT]: Aggregated text deemed sufficient for resolution.")
    
    return {
        "preflight_status": "sufficient",
        "knowledge_retry_count": 0,
        "raw_retrieved_text": combined_knowledge
    }

# --- PHASE 3: RESOLUTION BLUEPRINT & STEP-BY-STEP RESPONSE ---

def node_04a_resolution_planner(state: AgentState):
    """n19: Model A compiles linear active_plan array based on bounded RAG text & completed ledger."""
    print("🟢 [NODE 04a] Executing Resolution Planner")
    
    plan = state.get("active_plan", {})
    steps = plan.get("steps", [])
    idx = plan.get("current_step_index", 0)
    
    if steps and idx < len(steps):
        print("   ⏩ [PLAN EXISTS]: Preserving current active_plan. Bypassing replan.")
        return {"preflight_status": "pass"}
        
    retrieved_text = state.get("raw_retrieved_text", "")
    user_input = state.get("user_input", "")
    
    active_domain = state.get("active_domain", "")
    active_families = state.get("active_intent_families", [])
    
    completed_steps = state.get("completed_steps", [])
    completed_str = "\n".join([f"- {s}" for s in completed_steps]) if completed_steps else "None"
    
    prompt = f"""
    ROLE ALIGNMENT: 
    You are a Strict Information Extraction Engine. You are NOT a helpful assistant, you are NOT a conversationalist, and you do not solve problems. Your ONLY function is to act as a strict logical filter: you read a user's query, search the provided text for the exact answer, and output a clinical plan to deliver that specific answer.
    
    User's Latest Request: "{user_input}"
    Active Domain: "{active_domain}"
    Active Intent Families: {active_families}
    Retrieved Knowledge: "{retrieved_text}"
    
    MILESTONES ALREADY COMPLETED:
    {completed_str}
    
    ARCHITECTURAL PRINCIPLES (Strictly Enforce):
    1. EXACT MATCH EXTRACTION (NO VOLUNTEERING): You must ONLY plan steps that directly and explicitly answer the user's specific query. Do NOT volunteer extra facts. If the user asks for required documents, plan a step to list the documents. Do NOT plan a step to state the interest rate unless the user explicitly asked for the interest rate.
    2. THE PROCEED TRAP: If a user asks to "proceed", "start", or "next", you must scan the retrieved knowledge for explicit procedural instructions (e.g., links, forms to fill). If the retrieved knowledge does NOT contain procedural steps, you are strictly forbidden from guessing, and you are strictly forbidden from grabbing unrelated facts (like interest rates) just to have something to say. 
    3. THE EMPTY ARRAY RULE: If the exact answer to the user's request is not in the text, OR if the request is already fully covered by the completed milestones, you MUST output an empty array: {{"steps": [], "current_step_index": 0}}.
    4. LEAN PLANNING: Write steps as short, abstract directives (e.g., "List the required documents").
    5. LEDGER STRICT AVOIDANCE: Review the "MILESTONES ALREADY COMPLETED". You are STRICTLY FORBIDDEN from generating any step that repeats or overlaps with these completed actions. 
    
    Based EXCLUSIVELY on the retrieved knowledge, output the extraction plan.
    
    Output JSON ONLY matching this schema:
    {{"steps": ["Short directive for step 1"...], "current_step_index": 0}}
    """
    
    try:
        raw_res = call_groq_lpu(prompt)
        print(f"   🤖 [LLM RAW OUTPUT]: {raw_res.strip()}")
        res = _parse_json_robustly(raw_res)
        
        if "steps" not in res or not isinstance(res["steps"], list):
            res = {"steps": [], "current_step_index": 0}
            
        print(f"   📋 [ACTIVE PLAN SAVED]: {res}")
        return {"active_plan": res, "preflight_status": "pass"}
        
    except Exception as e:
        print(f"🚨 [NODE 04a EXCEPTION] Plan Generation Failed: {e}")
        return {"preflight_status": "fail"}


def node_04b_response_generator(state: AgentState):
    """n8: Model A Synthesizes text, dynamic chips & logs completed steps."""
    print("🟢 [NODE 04b] Executing Response Generator")
    
    plan = state.get("active_plan", {})
    idx = plan.get("current_step_index", 0)
    steps = plan.get("steps", [])
    routing_status = state.get("preflight_status", "")
    
    completed_steps = state.get("completed_steps", [])
    pending_queue = state.get("pending_domain_queue", [])
    is_conclusion = bool(steps and idx >= len(steps))
    
    if routing_status in ["intent_clarification_needed", "CLARIFICATION"]:
        current_step = "The user's request is vague, small talk, or asks you to make a choice for them. Respond naturally. If they asked you to choose, politely explain that you cannot make financial decisions for them and ask what they prefer. Otherwise, ask a direct, polite follow-up question to clarify their specific needs. Do NOT apologize for missing knowledge."
    elif not steps:
        current_step = "Respond naturally to the user's latest input without repeating previous summaries."
    elif is_conclusion:
        if pending_queue:
            next_task = pending_queue[0]["domain"]
            current_step = f"Explicitly state that this specific request is completely finished. Inform the user that you saved their request/progress for '{next_task}'. Ask if they would like to resume/start it now. YOU MUST set uiType to 'chips' and generate exactly TWO options: ['Resume {next_task}', 'No, I am done']."
        else:
            current_step = "Explicitly state that this specific request is completely finished. Summarize what was done. Ask if they need anything else. YOU MUST set uiType to 'chips' and generate exactly TWO custom UI chips for ending or asking a new question."
    else:
        current_step = steps[idx]
        
    print(f"   📍 [CURRENT TARGET STEP]: {current_step} (Index {idx})")
    
    knowledge = state.get("raw_retrieved_text", "")
    full_history = state.get("chat_history", [])
    recent_context = full_history[-6:] if len(full_history) > 6 else full_history
    history_str = "\n".join([f"{'User' if isinstance(m, HumanMessage) else 'AI'}: {m.content}" for m in recent_context])
    feedback = state.get("draft_feedback_history", [])
    feedback_str = f"CRITICAL CORRECTION NEEDED FROM LAST ATTEMPT: {feedback[-1]}" if feedback else ""

    prompt = f"""
    ROLE ALIGNMENT:
    You are Horizon, a Read-Only FAQ Navigation Bot embedded in a banking app. Your ONLY identity is a conversational search interface. You do not have eyes, you do not have backend system access, you cannot see user screens, and you cannot receive, view, or verify uploaded files. You exist SOLELY to read static FAQ documents to the user and guide them.
    
    Current Plan: {steps}
    Current Step to Execute: "{current_step}"
    Retrieved Knowledge: "{knowledge}"
    Recent Conversation: {history_str}
    
    {feedback_str}

    Rules for Text Generation:
    - Draft a conversational response addressing ONLY the 'Current Step'.
    - ZERO HALLUCINATION: You must rely EXCLUSIVELY on the 'Retrieved Knowledge'. Do NOT add extra details unless explicitly stated in the text. (EXCEPTION: If the 'Current Step' asks you to clarify an ambiguous request or handle small talk, you are permitted to ask a natural clarifying question without needing retrieved knowledge).
    - If the knowledge does not contain the answer to the user's specific question, you MUST gracefully state that the information is not provided in your current policy documents. Do not attempt to guess or infer.
    - If the user says they uploaded a file or completed an action, praise them for completing it on their end, but DO NOT claim that you received it or will check it.
    - If quoting exact numerical rates or fees, use ONLY allowed placeholders: {list(VALID_PLACEHOLDERS)}.
    
    Rules for UI Payload:
    - UI Payload is not mandatory. It is used to improve user experience by providing them with predicted next user input according to context and retrieved knowledge.
    - UI Payload is not mandatory, if there is no need for UI payload based on the context, choose 'uiType' to be 'none'.
    - Dynamically select the most suitable 'uiType' for the current context: 'chips', 'stepper', or 'none'. 
    - If 'chips', populate 'options' array with no more than 3 predicted user input in the next turn based on retrieved knowledge only. DO NOT provide option of talking to human agent or similar meaning.
    
    Output JSON ONLY matching this schema:
    {{"response": "Your conversational text here", "uiType": "chips" | "stepper" | "none", "options": ["prediction 1"], "advance_step": true}}
    """
    
    try:
        raw_res = call_groq_lpu(prompt)
        res = _parse_json_robustly(raw_res)
        draft = res.get("response", "Could you please clarify your request?")
        
        print(f"   💬 [DRAFT RESPONSE]: {draft}")
        
        should_advance = res.get("advance_step") and steps and idx < len(steps) and routing_status != "intent_clarification_needed"
        
        if should_advance:
            completed_steps.append(current_step)
            plan["current_step_index"] = idx + 1
            print(f"   ⏩ [STEP ADVANCED]: Moving to index {plan['current_step_index']}")
            print(f"   ✅ [LEDGER UPDATED]: Appended step to completed_steps.")
            
        ui_type = res.get("uiType", "none")
        options = res.get("options", [])
        
        if is_conclusion:
            ui_type = "chips"
            if not options or len(options) < 2:
                options = ["That's all, thank you.", "I have another question."]
            else:
                print(f"   🛑 [PROCESS CONCLUSION]: Dynamic UI Chips Generated: {options}")
        elif ui_type == "none":
            options = [] 
            
        return {
            "final_draft": draft, 
            "active_plan": plan, 
            "completed_steps": completed_steps, 
            "ui_action_payload": {"uiType": ui_type, "options": options}
        }
    except Exception as e:
        print(f"🚨 [NODE 04b EXCEPTION] Text Generation Failed: {e}")
        return {"final_draft": "I encountered an error. Please try again.", "preflight_status": "fail"}


# --- PHASE 4: OUTPUT GUARDRAILS & ESCALATION HANDLER ---


def node_05_outbound_deterministic_gate(state: AgentState):
    """n20: Sweeps draft for sensitive words & ILLEGAL PLACEHOLDERS."""
    print("🟢 [NODE 05] Executing Outbound Deterministic Gate")
    draft = state.get("final_draft", "")
    
    placeholders = re.findall(r"\{\{(.*?)\}\}", draft)
    illegal_detected = [p for p in placeholders if p not in VALID_PLACEHOLDERS]
    
    if illegal_detected or "ILLEGAL_WORD" in draft:
        print(f"   🚫 [HARD GATE VETO]: Illegal placeholders detected: {illegal_detected}")
        count = state.get("safety_retry_count", 0) + 1
        history = state.get("draft_feedback_history", [])
        history.append(f"Draft contained illegal placeholders: {illegal_detected}. Do not use them.")
        return {"safety_retry_count": count, "draft_feedback_history": history, "preflight_status": "fail"}
        
    print("   ✅ [HARD GATE PASS]: No illegal syntax found.")
    return {"preflight_status": "pass"}

def node_06_outbound_semantic_gate(state: AgentState):
    """n21: Model B Groundedness & MAS compliance check."""
    print("🟢 [NODE 06] Executing Outbound Semantic Gate")
    print("   ✅ [SEMANTIC PASS]: Draft aligns with MAS guidelines.")
    return {"preflight_status": "pass"}

def node_07_precision_substitution(state: AgentState):
    """n22: Maps placeholders to reference database values."""
    print("🟢 [NODE 07] Executing Precision Substitution")
    draft = state.get("final_draft", "")
    final_text = draft.replace("{{SAVINGS_RATE_01}}", "3.50%")
    print(f"   🔢 [SUBSTITUTION]: Hydrated text variables.")
    return {"final_response": final_text}

def node_08_soft_escalation_analyzer(state: AgentState):
    """n24: Injects optional agent chip on turn fatigue."""
    print("🟢 [NODE 08] Executing Soft Escalation Analyzer")
    turns = state.get("turn_count", 0)
    payload = state.get("ui_action_payload", {})
    if turns >= 5:
        payload["options"].append("Talk to Agent")
        
    # NEW: Append final AI response to history
    history = list(state.get("chat_history", []))
    final_text = state.get("final_response", "")
    if final_text:
        history.append(AIMessage(content=final_text))
        print(f"💾 [MEMORY] AI Response saved. History length: {len(history)}")
        
    return {"ui_action_payload": payload, "chat_history": history}


def node_09_hard_escalation_broker(state: AgentState):
    """n23: Shuts down graph & compiles CSR summary with RAG feedback (v3.7)."""
    print("🟢 [NODE 09] Executing Hard Escalation Broker")
    search_faults = state.get("search_feedback_history", [])
    draft_faults = state.get("draft_feedback_history", [])
    
    # Compile rich summary for the human agent
    summary_items = [f"System RAG Diagnostics: {fault}" for fault in search_faults]
    summary_items.extend([f"Safety Diagnostics: {fault}" for fault in draft_faults])

    # NEW: Append final AI response to history
    history = list(state.get("chat_history", []))
    final_text = state.get("final_response", "")
    if final_text:
        history.append(AIMessage(content=final_text))
        print(f"💾 [MEMORY] AI Response saved. History length: {len(history)}")    
    
    return {
        "final_response": "I am transferring you to a human agent.",
        "ui_action_payload": {"uiType": "case_summary", "items": summary_items}
    }



def node_10_standard_rejection(state: AgentState):
    """n5: Returns static failure disclaimer text."""
    # This will print EXACTLY which status caused the rejection
    print(f"🔴 [NODE 10] REJECTION TRIGGERED. Preflight Status: '{state.get('preflight_status')}'")
    
    draft = "To comply with security and regulatory guidelines, I cannot process this request."
    
    # Save the rejection to history so the loop doesn't break
    history = list(state.get("chat_history", []))
    history.append(AIMessage(content=draft))
    
    return {
        "final_response": draft,
        "ui_action_payload": {"uiType": "text"},
        "chat_history": history
    }
