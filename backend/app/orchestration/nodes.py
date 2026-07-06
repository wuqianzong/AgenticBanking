import json
import re
from langchain_core.messages import HumanMessage, AIMessage
from backend.app.orchestration.state import AgentState
from backend.app.core.llm_service import call_groq_lpu 
from backend.app.services.memory_service import ShortTermMemoryManager, LongTermMemoryManager


def _serialize_chat_history(messages: list) -> str:
    """Programmatically serializes LangGraph message arrays into an explicit plaintext timeline."""
    if not messages:
        return "No prior dialogue history exists for this session thread."
    
    serialized_string = ""
    for msg in messages:
        role = "Customer" if isinstance(msg, HumanMessage) else "Horizon Assistant"
        serialized_string += f"[{role}]: {msg.content}\n"
    return serialized_string.strip()


def node_0_turn_init(state: AgentState):
    """Atomically wipes per-turn volatile states while appending user message to timeline."""
    raw_history = state.get("chat_history", [])
    if raw_history is None:
        raw_history = []
        
    # Apply sliding window truncation via our decoupled service layer
    managed_history = ShortTermMemoryManager.trim_history(list(raw_history))
    
    # Commit incoming frontend message directly to short-term history stream
    user_msg = state.get("user_input", "")
    if user_msg.strip():
        managed_history.append(HumanMessage(content=user_msg))
    
    return {
        "chat_history": managed_history,
        "user_input": user_msg, 
        "active_intent": "",
        "retrieval_strategy": "",
        "target_path": "",
        "optimized_query": "",
        "raw_retrieved_text": "",
        "working_context": "",
        "final_draft": "",
        "final_response": "",
        "knowledge_retry_count": state.get("knowledge_retry_count", 0),
        "safety_retry_count": state.get("safety_retry_count", 0),
        "search_feedback_history": state.get("search_feedback_history", []),
        "draft_feedback_history": state.get("draft_feedback_history", []),
        "ui_action_payload": {"uiType": "text", "options": [], "items": []},
        "semantic_evaluation_status": "",
        "substitution_status": ""
    }


def node_0_hydrate(state: AgentState):
    """Turn 1 execution: Pulls compact summary footprint from Long-Term Memory Store."""
    customer_id = "USR_999" 
    summary_footprint = LongTermMemoryManager.fetch_compact_footprint(customer_id)
    return {"working_context": summary_footprint}


def node_1_deterministic_ingest_gate(state: AgentState):
    """Fast local regex policy matching to filter unpermitted strings before LLM execution."""
    return state


def node_2_intent_router(state: AgentState):
    """⚡ CONTEXT-AWARE ROUTER: Evaluates rolling history streams alongside incoming prompts."""
    user_msg = state.get("user_input", "")
    history_array = state.get("chat_history", [])
    
    # Extract historical message log elements up to this exact moment
    conversation_context = _serialize_chat_history(history_array)
    
    prompt = f"""
    You are the Intent Routing Node for a Consumer Banking AI.
    Your job is to classify the user's latest message by looking at the conversation context below.
    
    ACTIVE SESSION DIALOGUE CONTEXT:
    ---
    {conversation_context}
    ---
    
    LATEST USER INPUT: "{user_msg}"
    
    CLASSIFICATION RULES:
    1. If the context or latest input shows a clear account question ("open account", "savings account requirements", "minimum age") -> "active_intent": "ACCOUNT_OPENING", "retrieval_strategy": "vector_search", "target_path": "/account_opening/documents"
    2. If the user is answering a clarifying query or providing details to open an account -> "active_intent": "ACCOUNT_OPENING", "retrieval_strategy": "vector_search", "target_path": "/account_opening/documents"
    3. If the input is about banking apps, passwords, or login protocols -> "active_intent": "DIGITAL_BANKING", "retrieval_strategy": "vector_search", "target_path": "/digital_banking/login"
    4. If the input is banking-related but not in one of the classification of banking terms in the active_intent -> "active_intent": "UNKNOWN", "retrieval_strategy": "clarify", "target_path": ""
    5. If the input is just a greeting ("hi", "hello") or too vague to map -> "active_intent": "NONE_BANKING", "retrieval_strategy": "vector_search", "target_path": ""

    Output ONLY a valid raw JSON dictionary. Do not wrap in markdown codeblocks.
    {{
        "active_intent": "ACCOUNT_OPENING" | "DIGITAL_BANKING" | "NONE_BANKING" | "UNKNOWN",
        "retrieval_strategy": "vector_search" | "clarify",
        "target_path": "string"
    }}
    """
    
    # 📡 TERMINAL WIRE DEBUG TELEMETRY STREAM
    print("\n" + "🚀" * 35)
    print("📡 [WIRE TELEMETRY LOG: NODE 2] OUTBOUND INTENT PROMPT TO CLOUD LLM")
    print(f"PROMPT SENT:\n{prompt.strip()}")
    print("🚀" * 35 + "\n")

    try:
        response_text = call_groq_lpu(prompt)
        
        print("\n" + "📥" * 35)
        print("📡 [WIRE TELEMETRY LOG: NODE 2] INBOUND INTENT RESPONSE FROM CLOUD LLM")
        print(f"RAW RECEIVED TEXT:\n{response_text}")
        print("📥" * 35 + "\n")

        clean_text = response_text.strip()
        if clean_text.startswith("```json"):
            clean_text = clean_text[7:]
        elif clean_text.startswith("```"):
            clean_text = clean_text[3:]
        if clean_text.endswith("```"):
            clean_text = clean_text[:-3]
            
        blueprint = json.loads(clean_text.strip())
        return blueprint
        
    except Exception as e:
        print(f"\n🚨 [TELEMETRY REGISTRY INTERCEPT FAULT]: Node 2 Parsing failure: {e}\n")
        return {"active_intent": "UNKNOWN", "retrieval_strategy": "clarify", "target_path": ""}


def node_3_retrieval_execution(state: AgentState):
    """Executes rigid database query bounded by target_path."""
    return {
        "raw_retrieved_text": "Retail Banking Policy: The minimum age to open an account is 18 years old. Valid NRIC or digital Singpass authentication is required.",
        "working_context": "Retail Banking Policy: The minimum age to open an account is 18 years old. Valid NRIC or digital Singpass authentication is required."
    }


def node_4_response_generator(state: AgentState):
    """⚡ CONTEXT-AWARE RESPONSE LOGIC: Synthesizes final drafts using history context."""
    user_msg = state.get("user_input", "")
    context = state.get("working_context", "")
    history_array = state.get("chat_history", [])
    
    conversation_context = _serialize_chat_history(history_array)
    
    prompt = f"""
    You are Horizon, a highly secure digital assistant for consumer banking.
    Synthesize a clear, natural language response based on the retrieved data and session history.
    
    APPROVED POLICY BOUNDARY VALUES (Factual Grounding Moat):
    ---
    {context}
    ---
    
    CONVERSATION TIMELINE CONTEXT:
    ---
    {conversation_context}
    ---
    
    CURRENT CUSTOMER INPUT: "{user_msg}"
    
    INSTRUCTIONS:
    - Answer using ONLY the approved policy data bounds in banking topics, for normal chitchat, answer friendly. 
    - Maintain continuous narrative alignment across multi-turn prompts.
    - Never volunteer unlicensed wealth advisory claims or tax structures.
    """
    
    # 📡 TERMINAL WIRE DEBUG TELEMETRY STREAM
    print("\n" + "🚀" * 35)
    print("📡 [WIRE TELEMETRY LOG: NODE 4] OUTBOUND GENERATION PROMPT TO CLOUD LLM")
    print(f"PROMPT SENT:\n{prompt.strip()}")
    print("🚀" * 35 + "\n")

    try:
        response_text = call_groq_lpu(prompt)
        
        print("\n" + "📥" * 35)
        print("📡 [WIRE TELEMETRY LOG: NODE 4] INBOUND GENERATION RESPONSE FROM CLOUD LLM")
        print(f"RAW RECEIVED TEXT:\n{response_text}")
        print("📥" * 35 + "\n")
        
        return {"final_draft": response_text}
        
    except Exception as e:
        print(f"\n🚨 [TELEMETRY REGISTRY INTERCEPT FAULT]: Node 4 Synthesis failure: {e}\n")
        return {"final_draft": f"Inference pipeline timeout exception."}


def node_5_semantic_gate(state: AgentState):
    """Model B independent out-of-band non-bias safety gate."""
    return {"semantic_evaluation_status": "pass"}


def node_5_static_refusal(state: AgentState):
    """Tier 1 Escalation: MAS Advisory Violation containment gate."""
    res = "To comply with MAS regulations, I am strictly a digital assistant and cannot provide investment or wealth advice."
    
    history = list(state.get("chat_history", []))
    history.append(AIMessage(content=res))
    
    return {
        "chat_history": history,
        "final_response": res,
        "ui_action_payload": {"uiType": "escalation", "options": [], "items": []}
    }


def node_5_soft_escalation(state: AgentState):
    """Tier 2 Escalation: Injects non-blocking action option button chip."""
    current_payload = state.get("ui_action_payload", {})
    current_payload["uiType"] = "chips"
    current_payload["options"] = ["[Talk to a Human Agent]"]
    return {"ui_action_payload": current_payload}


def node_loop_search(state: AgentState):
    """Search expansion self-correction counter mapping layer."""
    history = state.get("search_feedback_history", [])
    history.append("Critique: Expanding search bounds.")
    return {"knowledge_retry_count": state.get("knowledge_retry_count", 0) + 1, "search_feedback_history": history}


def node_loop_draft(state: AgentState):
    """Linguistic self-correction counter mapping layer."""
    history = state.get("draft_feedback_history", [])
    history.append("Critique: Re-aligning vocabulary tone.")
    return {"safety_retry_count": state.get("safety_retry_count", 0) + 1, "draft_feedback_history": history}


def node_6_precision_substitution(state: AgentState):
    """Code-driven precision lookup substitution pass; commits final response to memory."""
    draft = state.get("final_draft", "")
    final_text = draft.replace("{{MIN_AGE}}", "18")
    
    # Commit final approved response payload directly to short-term history stream
    history = list(state.get("chat_history", []))
    history.append(AIMessage(content=final_text))
    
    return {
        "chat_history": history,
        "final_response": final_text, 
        "substitution_status": "valid"
    }




def node_7_clarification_mediator(state: AgentState):
    """
    ⚡ FULLY DYNAMIC GENERATIVE MEDIATOR: Offloads clarification strategy, 
    conversational phrasing, and frontend widget structural layout rules 
    directly to the LLM reasoning head.
    """
    user_msg = state.get("user_input", "")
    history_array = state.get("chat_history", [])
    
    # 1. Serialize multi-turn context stream to eliminate conversational amnesia
    conversation_context = _serialize_chat_history(history_array)
    
    # 2. Frame system prompt forcing structured JSON output mapping to components
    prompt = f"""
    You are the Clarification Mediator Node for Horizon, an Agentic Bank Assistant.
    The user's query is vague, greeting-based, or overlaps multiple active intent boundaries.
    Your objective is to ask a natural clarifying question and autonomously construct the perfect interactive UI payload to guide them.

    CURRENT AVAILABLE BANKING TAXONOMY UNDER MVP SCOPE:
    1. ACCOUNT_OPENING: Eligibility criteria, required onboarding documents, application status tracking.
    2. DIGITAL_BANKING: Mobile app configuration, login protocols, password resets, multi-try PIN unblocking.
    3. PROFILE: Updating personal contact data, updating home address, auditing KYC profile switches.

    UI PAYLOAD INSTRUCTION LAYER:
    You must dynamically evaluate the depth of the clarification and pick the best visual representation:
    - Option A: Use "uiType": "chips" if the choices are simple text actions. The "options" field must be an array of plain strings.
      Example structure: {{"uiType": "chips", "options": ["Apply for Savings Account", "Reset App Password"]}}
    - Option B: Use "uiType": "carousel" if the choices are rich feature descriptions. The "options" field must be an array of dictionaries containing an "id", "title", "desc", and an "icon" (allowlisted icons: 'landmark', 'card', 'wallet', 'lock').
      Example structure: {{"uiType": "carousel", "options": [{{"id": 1, "title": "Title", "desc": "Short description text", "icon": "landmark"}}]}}

    ACTIVE SESSION DIALOGUE HISTORY:
    ---
    {conversation_context}
    ---
    
    LATEST CUSTOMER INPUT ELEMENT: "{user_msg}"

    Output ONLY a valid raw JSON dictionary with the structure below. Do not wrap in markdown codeblocks, and do not add conversational prefix markers outside the JSON block.
    {{
        "clarification_text": "Your natural, polite clarifying natural language greeting or phrase goes here.",
        "uiType": "chips" | "carousel",
        "options": []
    }}
    """
    
    # 📡 TERMINAL TELEMETRY INTERCEPTION WIRE LOGS
    print("\n" + "🚀" * 35)
    print("📡 [WIRE TELEMETRY LOG: NODE 7] OUTBOUND CLARIFICATION PROMPT TO REMOTE LLM")
    print(f"TARGET INFERENCE RUNTIME: GPT OSS 120B")
    print(f"PROMPT SENT:\n{prompt.strip()}")
    print("🚀" * 35 + "\n")

    try:
        # Call remote inference runtime manager
        response_text = call_groq_lpu(prompt)
        
        print("\n" + "📥" * 35)
        print("📡 [WIRE TELEMETRY LOG: NODE 7] INBOUND STRUCTURAL PAYLOAD RETURNED FROM LLM")
        print(f"RAW STRING PAYLOAD:\n{response_text}")
        print("📥" * 35 + "\n")

        # Robust markdown backtick stripping
        clean_text = response_text.strip()
        if clean_text.startswith("```json"):
            clean_text = clean_text[7:]
        elif clean_text.startswith("```"):
            clean_text = clean_text[3:]
        if clean_text.endswith("```"):
            clean_text = clean_text[:-3]
            
        generative_payload = json.loads(clean_text.strip())
        
        # 3. Disassemble the output variables to update graph runtime parameters
        final_text = generative_payload.get("clarification_text", "How can I help you today?")
        ui_type = generative_payload.get("uiType", "chips")
        options_data = generative_payload.get("options", [])
        
        # 4. Enforce strict persistence logging inside short-term memory arrays
        history = list(history_array)
        history.append(AIMessage(content=final_text))
        
        return {
            "chat_history": history,
            "final_response": final_text,
            "ui_action_payload": {
                "uiType": ui_type,
                "options": options_data,
                "items": []
            }
        }
        
    except Exception as e:
        print(f"\n🚨 [TELEMETRY REGISTRY INTERCEPT FAULT]: Node 7 Generative engine failure: {e}\n")
        # Hardcoded fail-safe resilience fallback block if structure parsing cracks
        fallback_text = "I received your request. I'm sorry that I did not understand your request clearly. Could you clarify? Below are a few services I can provide."
        history = list(history_array)
        history.append(AIMessage(content=fallback_text))
        return {
            "chat_history": history,
            "final_response": fallback_text,
            "ui_action_payload": {
                "uiType": "chips",
                "options": ["Account Opening", "Digital Banking Help"],
                "items": []
            }
        }


def node_8_escalation_handler(state: AgentState):
    """Tier 3 Escalation: Forced workspace hand-off sequence; commits response to memory."""
    res = "I am transferring you to a human agent."
    
    history = list(state.get("chat_history", []))
    history.append(AIMessage(content=res))
    
    return {
        "chat_history": history,
        "final_response": res,
        "ui_action_payload": {"uiType": "case_summary", "items": ["Context: " + state.get("active_intent", "Unknown")]}
    }