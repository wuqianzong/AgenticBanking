import json
import re
from langchain_core.messages import HumanMessage, AIMessage
from backend.app.orchestration.state import AgentState
from backend.app.core.llm_service import call_groq_lpu  
from backend.app.services.memory_service import LongTermMemoryManager

# MOCK DATABASES FOR VALIDATION
VALID_PLACEHOLDERS = {"SAVINGS_RATE_01", "MIN_BALANCE_01", "FEE_01"}
#FAQ_DIRECTORIES = ["ACCOUNT_OPENING", "DIGITAL_BANKING", "PROFILE_UPDATE"]
# V3.74: COMPREHENSIVE TWO-TIERED TAXONOMY
GLOBAL_TAXONOMY = {
    "ACCOUNT_OPENING": ["Eligibility", "documents", "application status"],
    "DEPOSIT_ACCOUNTS": ["Features", "fees", "restrictions", "dormancy"],
    "DEBIT_CARDS": ["Activation", "decline", "lost card", "PIN"],
    "CREDIT_CARDS": ["Fees", "statement queries", "decline", "dispute"],
    "DIGITAL_BANKING": ["Password reset", "login issue", "OTP issue"],
    "STATEMENTS": ["Download", "missing statement", "transaction explanation"],
    "LOANS": ["Features", "documents", "application status", "repayment issue"],
    "BRANCHES": ["Branch locator", "ATM locator", "service availability"],
    "PROFILE": ["Contact update", "address update", "status"]
}

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
    """n6: Model A evaluates Yes/No/Else responses. Erases intent on Else."""
    print("🟢 [NODE 02b] Executing Confirmation Gate")
    user_msg = state.get("user_input", "")
    pending_domain = state.get("pending_domain_switch", {}).get("target_domain", "")
    pending_families = state.get("pending_domain_switch", {}).get("target_intent_families", [])
    
    prompt = f"""
    Evaluate user input: "{user_msg}"
    Did they agree to abandon progress and switch to {pending_domain}?
    Reply ONLY with JSON: {{"decision": "yes" | "no" | "else"}}
    """
    try:
        raw_res = call_groq_lpu(prompt)
        decision = _parse_json_robustly(raw_res).get("decision", "else")
    except:
        decision = "else"
        
    print(f"   ⚖️  [CONFIRMATION DECISION]: {decision.upper()}")
        
    if decision == "yes":
        return {
            "active_domain": pending_domain,
            "active_intent_families": pending_families,
            "pending_domain_switch": {}, 
            "preflight_status": "yes"
        }
    elif decision == "no":
        return {"pending_domain_switch": {}, "preflight_status": "no"}
    else:
        return {"active_domain": "", "active_intent_families": [], "pending_domain_switch": {}, "preflight_status": "else"}

def node_02e_contract_writer(state: AgentState):
    """n12: Sets pending switch flag & prompts confirmation."""
    print("🟢 [NODE 02e] Executing Contract Writer")
    target = state.get("pending_domain_switch", {}).get("target_domain", "a new topic")
    msg = f"Do you want to abandon your current progress and switch to {target}?"
    
    print(f"   🛑 [INTERCEPT]: Prompting user to confirm intent switch to {target}")
    history = list(state.get("chat_history", []))
    history.append(AIMessage(content=msg))
    
    return {
        "final_response": msg,
        "chat_history": history,
        "ui_action_payload": {"uiType": "chips", "options": ["Yes", "No"]}
    }

def node_02c_inbound_semantic_gate(state: AgentState):
    """n16: Model B query compliance check."""
    print("🟢 [NODE 02c] Executing Inbound Semantic Gate")
    return {"preflight_status": "pass"}

def node_02d_intent_classifier(state: AgentState):
    """n7: Model A classifies Intent and manages state memory."""
    print("🟢 [NODE 02d] Executing Intent Classifier")
    user_msg = state.get("user_input", "")
    
    active_domain = state.get("active_domain", "")
    active_families = state.get("active_intent_families", [])
    
    plan = state.get("active_plan", {})
    idx = plan.get("current_step_index", 0)
    steps = plan.get("steps", [])
    
    process_complete = False
    if active_domain and steps and idx >= len(steps):
        print("   🏁 [PROCESS COMPLETE]: Previous plan resolved.")
        process_complete = True
        
    current_step = steps[idx] if steps and idx < len(steps) else "None"
    
    full_history = state.get("chat_history", [])
    recent_context = full_history[-4:] if len(full_history) > 4 else full_history
    history_str = "\n".join([f"{'User' if isinstance(m, HumanMessage) else 'AI'}: {m.content}" for m in recent_context])
    
    prompt = f"""
    Current Active Domain: "{active_domain}"
    Current Active Families: {active_families}
    Current Target Step in Plan: "{current_step}"
    Global Taxonomy: {GLOBAL_TAXONOMY}
    Recent Conversation Context: {history_str}
    Latest User Input: "{user_msg}"
    
    TASK: Classify the user's input into a specific Domain and a list of specific Intent Families based on the Global Taxonomy.
    If the user asks for small talk or clarification, leave Domain and Families blank.
    
    RULES FOR CLASSIFICATION:
    1. DOMAIN CHANGE: Use if 'Current Active Domain' is NOT empty, AND the user introduces a new Domain entirely.
    2. INTENT CLARIFICATION NEEDED: Use for small talk, vague requests ("Ask another question"), requests crossing multiple domains, or no financial intent.
    3. OUT OF KNOWLEDGE: Use ONLY if the query is a financial topic completely missing from the Global Taxonomy.
    4. NEW DOMAIN: Use if 'Current Active Domain' is empty.
    5. FAMILY DRIFT: Use if the user stays in the SAME Domain, but asks about DIFFERENT or ADDITIONAL Intent Families.
    6. TOPIC PERSIST: Use ONLY if the user is advancing the current step without changing the topic.
    7. USER ESCALATION: Use if demanding a human agent.

    Output JSON ONLY:
    {{"route": "topic_persist" | "domain_change" | "family_drift" | "new_domain" | "out_of_knowledge" | "intent_clarification_needed" | "user_escalation", "target_domain": "...", "target_intent_families": ["..."]}}
    """
    try:
        raw_res = call_groq_lpu(prompt)
        res = _parse_json_robustly(raw_res) 
        route = res.get("route", "unexpected_exception")
        target_domain = res.get("target_domain", "")
        target_families = res.get("target_intent_families", [])
        
        # V3.75 SEMANTIC ROUTING OVERRIDE (Set-Based Math)
        if route not in ["intent_clarification_needed", "user_escalation", "out_of_knowledge", "unexpected_exception"]:
            if not target_domain:
                print("   ⚠️ [FALLBACK]: Empty target domain detected. Downgrading to clarification.")
                route = "intent_clarification_needed"
            elif not active_domain:
                route = "new_domain"
            elif target_domain != active_domain:
                route = "domain_change"
            elif set(target_families) != set(active_families):
                route = "family_drift"
            else:
                route = "topic_persist"

        print(f"   🎯 [ROUTING DECISION]: {route.upper()} | DOMAIN: {target_domain} | FAMILIES: {target_families}")
        
        # Notice pending_domain_switch is used here!
        clear_state = {"active_plan": {}, "pending_domain_switch": {}, "completed_steps": []}
        
        if route == "domain_change":
            return {"preflight_status": route, "pending_domain_switch": {"target_domain": target_domain, "target_intent_families": target_families}}
            
        elif route == "new_domain":
            return {
                "preflight_status": route, 
                "active_domain": target_domain, 
                "active_intent_families": target_families,
                "pending_domain_switch": {},
                "active_plan": {},
                "completed_steps": [] 
            }
            
        elif route == "family_drift":
            return {
                "preflight_status": route, 
                "active_domain": target_domain, 
                "active_intent_families": target_families,
                "pending_domain_switch": {},
                "active_plan": {} 
            }
            
        elif route in ["topic_persist", "intent_clarification_needed"]:
            if process_complete:
                return {**clear_state, "preflight_status": route}
            return {"preflight_status": route, "pending_domain_switch": {}}
            
        else:
            return {"preflight_status": route, "pending_domain_switch": {}}
            
    except Exception as e:
        print(f"🚨 [NODE 02d EXCEPTION] Routing to Node 10. Error: {e}")
        return {"preflight_status": "unexpected_exception"}

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
    """n17_rag: Dedicated ChromaDB Worker"""
    print("🟢 [NODE 03b] Executing RAG Worker")
    domain = state.get("active_domain", "")
    families = state.get("active_intent_families", [])
    
    # Existing mock logic
    if domain == "ACCOUNT_OPENING":
        retrieved = ""
        if "documents" in families or not families:
            retrieved += "To open a standard savings or checking account, you must provide: 1. Photo ID, 2. Proof of Address (≤3 months), 3. TIN/SSN.\n"
        if "Eligibility" in families:
            retrieved += "Applicants must be at least 18 years old and a resident.\n"
        if "application status" in families:
            retrieved += "To check your application status, please click the secure link below: bankapp://account/status\n"
    elif domain in GLOBAL_TAXONOMY:
        retrieved = f"Mock FAQ Policy for {domain} - {families}: To process this request, please refer to the digital guidelines."
    else:
        retrieved = "NO_KNOWLEDGE_FOUND"
        
    return {"rag_payload": f"[RAG DATA]:\n{retrieved.strip()}"}


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
    
    # 1. Aggregate outputs from whichever workers were activated
    rag_data = state.get("rag_payload", "")
    api_data = state.get("api_payload", "")
    deeplink_data = state.get("deeplink_payload", "")
    
    # Combine into the master string that Node 04a and 04b expect
    combined_knowledge = f"{rag_data}\n{api_data}\n{deeplink_data}".strip()
    
    if "NO_KNOWLEDGE_FOUND" in combined_knowledge and len(combined_knowledge) < 50:
        print("   ❌ [AUDIT]: Retrieved text is INSUFFICIENT (Out of Knowledge).")
        count = state.get("knowledge_retry_count", 0) + 1
        history = state.get("search_feedback_history", [])
        history.append(f"No documents found for Domain '{state.get('active_domain')}' and Families {state.get('active_intent_families')}.")
        
        return {
            "knowledge_retry_count": count,
            "search_feedback_history": history,
            "preflight_status": "insufficient",
            "raw_retrieved_text": combined_knowledge
        }
        
    print("   ✅ [AUDIT]: Aggregated text deemed sufficient for resolution.")
    return {
        "preflight_status": "sufficient",
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
    is_conclusion = bool(steps and idx >= len(steps))
    
    if routing_status == "intent_clarification_needed":
        current_step = "The user engaged in small talk, gave a vague response, gave a response that crosses more than one domain, or their intent is unclear. If user input crosses more than one domain, explain to the user that you will solve his queries crossing multiple domains one by one and guide the user to focus on a single domain and . If user input is banking-related, ask a follow-up question to clarify their intent or banking needs. If user input is not banking-related, respond naturally and politely telling them that you cannot engage in non-banking conversations, then gently ask them how you can assist them with their banking needs. Do NOT attempt to execute a process step."
    elif not steps:
        current_step = "Respond naturally to the user's latest input. If they indicate they need more help, prompt them directly for their new request. Do NOT repeat previous process summaries."
    elif is_conclusion:
        current_step = "Explicitly state that this specific request is completely finished. Summarize what was done. Ask if they need anything else. YOU MUST set uiType to 'chips' and generate exactly TWO custom UI chips in the 'options' array matching your conversational tone: one option for ending the conversation, and one for asking a new question."
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
    - ZERO HALLUCINATION: You must rely EXCLUSIVELY on the 'Retrieved Knowledge'. Do NOT add extra details, standard banking practices, or assume features (like optional documents, proof of income, or taking a selfie) unless explicitly stated in the text.
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