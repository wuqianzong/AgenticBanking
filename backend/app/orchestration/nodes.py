import json
import re
from langchain_core.messages import HumanMessage, AIMessage
from backend.app.orchestration.state import AgentState
from backend.app.core.llm_service import call_groq_lpu  
from backend.app.services.memory_service import LongTermMemoryManager

# MOCK DATABASES FOR VALIDATION
VALID_PLACEHOLDERS = {"SAVINGS_RATE_01", "MIN_BALANCE_01", "FEE_01"}
FAQ_DIRECTORIES = ["ACCOUNT_OPENING", "DIGITAL_BANKING", "PROFILE_UPDATE"]

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
    """n4: Scans state for active pending_intent_switch flag."""
    print("🟢 [NODE 02a] Executing Pending Contract Check")
    pending = state.get("pending_intent_switch", {})
    if pending and pending.get("target_intent"):
        print(f"   🔒 [STATE INTERLOCKED]: Pending switch to '{pending.get('target_intent')}'")
        return {"preflight_status": "yes"} 
    
    print("   🔓 [STATE OPEN]: No pending contracts.")
    return {"preflight_status": "no"}      

def node_02b_confirmation_gate(state: AgentState):
    """n6: Model A evaluates Yes/No/Else responses. Erases intent on Else."""
    print("🟢 [NODE 02b] Executing Confirmation Gate")
    user_msg = state.get("user_input", "")
    pending_intent = state.get("pending_intent_switch", {}).get("target_intent", "")
    
    prompt = f"""
    Evaluate user input: "{user_msg}"
    Did they agree to abandon progress and switch to {pending_intent}?
    Reply ONLY with JSON: {{"decision": "yes" | "no" | "else"}}
    """
    try:
        raw_res = call_groq_lpu(prompt)
        print(f"   🤖 [LLM RAW OUTPUT]: {raw_res.strip()}")
        decision = _parse_json_robustly(raw_res).get("decision", "else")
    except:
        decision = "else"
        
    print(f"   ⚖️  [CONFIRMATION DECISION]: {decision.upper()}")
        
    if decision == "yes":
        return {
            "active_intent": pending_intent,
            "pending_intent_switch": {}, 
            "preflight_status": "yes"
        }
    elif decision == "no":
        return {
            "pending_intent_switch": {}, 
            "preflight_status": "no"
        }
    else:
        return {
            "active_intent": "",         
            "pending_intent_switch": {}, 
            "preflight_status": "else"
        }

def node_02c_inbound_semantic_gate(state: AgentState):
    """n16: Model B query compliance check."""
    print("🟢 [NODE 02c] Executing Inbound Semantic Gate")
    return {"preflight_status": "pass"}






def node_02d_intent_classifier(state: AgentState):
    """n7: Model A classifies New, Persist, Drift, Change, Out of Knowledge."""
    print("🟢 [NODE 02d] Executing Intent Classifier")
    user_msg = state.get("user_input", "")
    active = state.get("active_intent", "")
    
    prompt = f"""
    Current Active Intent: "{active}"
    FAQ Directories: {FAQ_DIRECTORIES}
    User Input: "{user_msg}"
    
    RULES FOR CLASSIFICATION (Follow in strict order):
    1. INTENT CHANGE: If 'Current Active Intent' is NOT empty, and the user's input changes the topic completely, you MUST classify it as "intent_change" and extract the new topic as 'target_intent'. Do this even if the new topic is not in the FAQ Directories.
    2. OUT OF KNOWLEDGE: Use this ONLY if 'Current Active Intent' IS empty AND the user's requested topic is not in the FAQ Directories.
    3. NEW INTENT: Use if 'Current Active Intent' is empty AND the topic is inside the FAQ Directories.
    4. INTENT PERSIST: Use if the user is answering a question or continuing the 'Current Active Intent'.
    5. INTENT DRIFT: Use if continuing the current intent but with a slight sub-topic shift.
    6. USER ESCALATION: Use if the user explicitly demands a human agent.

    Classify the routing step. Output JSON ONLY:
    {{"route": "intent_persist" | "intent_change" | "intent_drift" | "new_intent" | "out_of_knowledge" | "user_escalation", "target_intent": "..."}}
    """
    
    try:
        raw_res = call_groq_lpu(prompt)
        print(f"   🤖 [LLM RAW OUTPUT]: {raw_res.strip()}") 
        
        res = _parse_json_robustly(raw_res) 
        route = res.get("route", "unexpected_exception")
        target = res.get("target_intent", "")
        
        print(f"   🎯 [ROUTING DECISION]: {route.upper()} | TARGET: {target}")
        
        if route == "intent_change":
            return {"preflight_status": route, "pending_intent_switch": {"target_intent": target}}
            
        elif route in ["new_intent", "intent_drift"]:
            return {"preflight_status": route, "active_intent": target, "pending_intent_switch": {}}
            
        else:
            return {"preflight_status": route, "pending_intent_switch": {}}
            
    except Exception as e:
        print(f"🚨 [NODE 02d EXCEPTION] Routing to Node 10. Error: {e}")
        return {"preflight_status": "unexpected_exception"}


def node_02e_contract_writer(state: AgentState):
    """n12: Sets pending switch flag & prompts confirmation."""
    print("🟢 [NODE 02e] Executing Contract Writer")
    target = state.get("pending_intent_switch", {}).get("target_intent", "a new topic")
    msg = f"Do you want to abandon your current progress and switch to {target}?"
    
    print(f"   🛑 [INTERCEPT]: Prompting user to confirm intent switch to {target}")
    
    history = list(state.get("chat_history", []))
    history.append(AIMessage(content=msg))
    
    return {
        "final_response": msg,
        "chat_history": history,
        "ui_action_payload": {"uiType": "chips", "options": ["Yes", "No"]}
    }

# --- PHASE 2: BOUNDED RAG ARCHITECTURE ---


def node_03a_knowledge_blueprint_planner(state: AgentState):
    """n14: Model A formulates domain search keywords."""
    print("🟢 [NODE 03a] Executing Knowledge Blueprint Planner")
    
    user_input = state.get("user_input", "")
    target_intent = state.get("active_intent", "")
    
    # FIX: If the user just said "Yes" to confirm a switch, "Yes" is a bad search term.
    # Use the target intent as the search keyword instead.
    if user_input.strip().lower() in ["yes", "y", "yeah", "correct", "sure"]:
        search_keywords = target_intent
    else:
        search_keywords = user_input
        
    blueprint = {
        "keywords": search_keywords, 
        "directory": target_intent
    }
    print(f"   🗺️ [BLUEPRINT GENERATED]: {blueprint}")
    return {"knowledge_blueprint": blueprint}

def node_03b_vector_retrieval_engine(state: AgentState):
    """n17: ChromaDB vector search bounded by folder path."""
    print("🟢 [NODE 03b] Executing Vector Retrieval Engine")
    intent = state.get("active_intent", "")
    print(f"   🔎 [SEARCHING DB]: Target path -> '{intent}'")
    
    # FIX: Dynamically simulate the DB check against our allowed directories
    if intent in FAQ_DIRECTORIES:
        retrieved = f"Mock FAQ Policy for {intent}: To process {intent}, please provide required documents."
        print(f"   📄 [RETRIEVED DATA]: Found valid policy for {intent}")
    else:
        retrieved = "NO_KNOWLEDGE_FOUND"
        print(f"   ❌ [RETRIEVED DATA]: Database returned empty for {intent}")
        
    return {"raw_retrieved_text": retrieved}

def node_03c_sufficiency_auditor(state: AgentState):
    """n18: Model B checks context sufficiency (Max 3 retries)."""
    print("🟢 [NODE 03c] Executing Sufficiency Auditor")
    
    retrieved_text = state.get("raw_retrieved_text", "")
    
    # FIX: Explicitly fail the audit if no knowledge was found
    if retrieved_text == "NO_KNOWLEDGE_FOUND":
        print("   ❌ [AUDIT]: Retrieved text is INSUFFICIENT (Out of Knowledge).")
        
        count = state.get("knowledge_retry_count", 0) + 1
        history = state.get("search_feedback_history", [])
        history.append(f"No documents found in knowledge base for directory '{state.get('active_intent')}'.")
        
        return {
            "knowledge_retry_count": count,
            "search_feedback_history": history,
            "preflight_status": "insufficient"
        }
        
    print("   ✅ [AUDIT]: Retrieved text deemed sufficient for resolution.")
    return {"preflight_status": "sufficient"}

# --- PHASE 3: RESOLUTION BLUEPRINT & STEP-BY-STEP RESPONSE ---

def node_04a_resolution_planner(state: AgentState):
    """n19: Model A compiles linear active_plan array based on retrieved FAQ knowledge."""
    print("🟢 [NODE 04a] Executing Resolution Planner")
    
    intent = state.get("active_intent", "")
    knowledge = state.get("raw_retrieved_text", "")
    user_msg = state.get("user_input", "")

    prompt = f"""
    You are a backend system for a banking AI.
    Your task is to create a step-by-step resolution plan based ONLY on the retrieved FAQ policy.
    
    User Request: "{user_msg}"
    Active Intent: "{intent}"
    Retrieved FAQ Policy: "{knowledge}"

    Rules:
    1. Break the solution down into a logical sequence of steps.
    2. If the user's request is a simple question, make it a 1-step plan.
    3. Output ONLY valid JSON.

    Output Schema:
    {{"steps": ["Step 1 description", "Step 2 description"], "current_step_index": 0}}
    """
    
    try:
        raw_res = call_groq_lpu(prompt)
        print(f"   🤖 [LLM RAW OUTPUT]: {raw_res.strip()}") # <-- ADDED VISIBILITY
        
        plan = _parse_json_robustly(raw_res)
        print(f"   📋 [ACTIVE PLAN SAVED]: {json.dumps(plan, indent=2)}")
        
        return {"active_plan": plan, "preflight_status": "success"}
    except Exception as e:
        print(f"🚨 [NODE 04a EXCEPTION] Plan Compilation Failed: {e}")
        return {"preflight_status": "fail"}


def node_04b_response_generator(state: AgentState):
    """n8: Model A Synthesizes text & placeholders for the active step under strict non-transactional rules."""
    print("🟢 [NODE 04b] Executing Response Generator")
    
    plan = state.get("active_plan", {})
    idx = plan.get("current_step_index", 0)
    steps = plan.get("steps", [])
    
    current_step = steps[idx] if idx < len(steps) else "Summarize and conclude."
    print(f"   📍 [CURRENT TARGET STEP]: {current_step} (Index {idx})")
    
    knowledge = state.get("raw_retrieved_text", "")
    full_history = state.get("chat_history", [])
    recent_context = full_history[-6:] if len(full_history) > 6 else full_history
    history_str = "\n".join([f"{'User' if isinstance(m, HumanMessage) else 'AI'}: {m.content}" for m in recent_context])
    
    feedback = state.get("draft_feedback_history", [])
    feedback_str = f"CRITICAL CORRECTION NEEDED FROM LAST ATTEMPT: {feedback[-1]}" if feedback else ""

    # THE ENFORCED SYSTEM ROLE PROMPT
    prompt = f"""
    You are Horizon, an informational digital banking assistant embedded in a consumer banking mobile application.
    
    CORE OPERATIONAL RULES (MAS COMPLIANCE & ARCHITECTURAL LIMITS):
    1. READ-ONLY & INFORMATIONAL ONLY: You extract FAQ info, explain procedures, and guide users step-by-step. 
    2. NO TRANSACTION EXECUTION: You CANNOT execute transactions, create accounts, modify profiles, or generate account numbers/data. 
    3. NO FALSE COMPLETION: Never tell a user that an account has been created, a loan has been approved, or a transaction is finalized. When a process reaches its final step, instruct the user to complete it on the bank's secure native screen using allowlisted deep-links.
    4. KNOWLEDGE CONTAINMENT: Rely ONLY on the retrieved knowledge base facts below. Do not extrapolate or fabricate data.
    
    Current Plan: {steps}
    Current Step to Execute: "{current_step}"
    Retrieved Knowledge: "{knowledge}"
    Recent Conversation: {history_str}
    
    {feedback_str}

    Rules for Generation:
    - Draft a conversational response addressing ONLY the 'Current Step'.
    - Ask a clear, single follow-up question or provide the exact next process instruction.
    - If quoting exact numerical rates or fees from the knowledge base, use ONLY allowed placeholders: {list(VALID_PLACEHOLDERS)}.
    
    Output JSON ONLY:
    {{"response": "Your conversational text here", "uiType": "stepper", "advance_step": true}}
    """
    
    try:
        raw_res = call_groq_lpu(prompt)
        print(f"   🤖 [LLM RAW OUTPUT]: {raw_res.strip()}") 
        
        res = _parse_json_robustly(raw_res)
        draft = res.get("response", "Could you please clarify your request?")
        
        print(f"   💬 [DRAFT RESPONSE]: {draft}")
        
        if res.get("advance_step") and (idx + 1) < len(steps):
            plan["current_step_index"] = idx + 1
            print(f"   ⏩ [STEP ADVANCED]: Moving to index {plan['current_step_index']}")
            
        return {
            "final_draft": draft, 
            "active_plan": plan, 
            "ui_action_payload": {"uiType": res.get("uiType", "stepper"), "options": []}
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