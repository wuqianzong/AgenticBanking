from langgraph.graph import StateGraph, END
from langgraph.checkpoint.memory import MemorySaver # NEW: Required for cross-turn memory
from backend.app.orchestration.state import AgentState
from backend.app.orchestration import nodes
from backend.app.orchestration.nodes import (
    node_02d_user_journey_classifier_and_triage,
    node_02e_intent_classifier_and_query_formulator,
    node_02f_contract_writer,
)

def build_agent_graph():
    workflow = StateGraph(AgentState)

    # 1. Register Nodes (Mapped to Mermaid Node IDs)
    workflow.add_node("n00", nodes.node_00_context_hydrator_and_greeting)
    workflow.add_node("n3",  nodes.node_01_deterministic_ingest_gate)
    workflow.add_node("n4",  nodes.node_02a_pending_contract_check)
    workflow.add_node("n6",  nodes.node_02b_confirmation_gate)
    workflow.add_node("n16", nodes.node_02c_inbound_semantic_gate)
    workflow.add_node("n2d", node_02d_user_journey_classifier_and_triage)
    workflow.add_node("n2e", node_02e_intent_classifier_and_query_formulator)
    workflow.add_node("n2f", node_02f_contract_writer)
    
    workflow.add_node("n14", nodes.node_03a_knowledge_blueprint_planner)
    workflow.add_node("n17_rag", nodes.node_03b_rag_worker)
    workflow.add_node("n17_api", nodes.node_03b_api_worker)
    workflow.add_node("n17_deeplink", nodes.node_03b_deeplink_worker)
    workflow.add_node("n18", nodes.node_03c_sufficiency_auditor)
    
    workflow.add_node("n19", nodes.node_04a_resolution_planner)
    workflow.add_node("n8",  nodes.node_04b_response_generator)
    
    workflow.add_node("n20", nodes.node_05_outbound_deterministic_gate)
    workflow.add_node("n21", nodes.node_06_outbound_semantic_gate)
    workflow.add_node("n22", nodes.node_07_precision_substitution)
    workflow.add_node("n24", nodes.node_08_soft_escalation_analyzer)
    workflow.add_node("n23", nodes.node_09_hard_escalation_broker)
    workflow.add_node("n5",  nodes.node_10_standard_rejection)

    # --- CONDITIONAL ROUTING LOGIC ---
    
    def route_start(state: AgentState):
        """Phase 0: Turn 1 Initialization vs Turn 2+ Ingest"""
        history = state.get("chat_history", [])
        
        # 📡 TERMINAL DEBUG LOGGER
        print("\n" + "="*50)
        print(f"🚦 [ROUTER GATE] Initializing Graph Edge")
        print(f"📦 [STATE CHECK] chat_history length: {len(history)}")
        print(f"🗣️  [INPUT] user_input: '{state.get('user_input', '')}'")
        
        if not history and state.get("user_input") in {"__INIT__", "**INIT__"}:
            print("🔀 [ROUTE] History empty -> Routing to n00 (Greeting/Init)")
            print("="*50 + "\n")
            return "n00" # Turn 1
            
        print("🔀 [ROUTE] History found -> Routing to n3 (Ingest Gate)")
        print("="*50 + "\n")
        return "n3"      # Turns 2+
        
    def route_n3(state: AgentState):
        # Do not forward a deterministic ingest veto to any model-backed gate.
        if state.get("preflight_status") != "pass": return "n5"
        return "n16"

    def route_n4(state: AgentState):
        if state.get("preflight_status") == "yes": return "n6"
        return "n2d"

    def route_n6(state: AgentState):
        status = state.get("preflight_status")
        if status == "yes": return "n14"
        if status == "no": return "n8"
        return "n2d" # Else (Ambiguous)

    def route_n16(state: AgentState):
        if state.get("preflight_status") == "pass": return "n4"
        return "n5"


    def route_n2d(state: AgentState):
        status = state.get("triage_status")
        if status == "ESCALATION": return "n23"
        if status == "OUT_OF_SCOPE": return "n23"
        if status == "CLARIFICATION": return "n8"
        return "n2e"


    def route_n2e(state: AgentState):
        status = state.get("preflight_status")
        
        # 1. Journey Change
        if status == "journey_change": 
            return "n2f"
        
        queries = state.get("search_tasks", [])
        retry_count = state.get("knowledge_retry_count", 0)
        
        # 2. Topic Persist WITHOUT queries (Direct Continuation)
        if not queries and status == "topic_persist":
            return "n19"
            
        # 3. Topic Persist WITH queries (The Semantic Cache Optimization)
        if queries and status == "topic_persist":
            if retry_count == 0:
                print("   ⏭️ [OPTIMIZATION] Topic Persist: Skipping DB Search, routing to Sufficiency Auditor (n18)")
                return "n18"
            else:
                print("   🔄 [CRAG RETRY] Cache Miss / Feedback Received: Forcing new DB Search (n14)")
                return "n14"
                
        # 4. New Journey or Intent Drift
        if queries and status in ["new_journey", "intent_drift"]:
            return "n14"
            
        return "n5"


    def route_n18(state: AgentState):
        status = state.get("preflight_status")

        # V3.77 FIX: Agentic routing logic with Kill-Switch
        if status == "insufficient": 
            return "n2e" # Loop back to rephrase/expand intents
        if status == "max_retries_exceeded": 
            return "n23" # Kill the loop, route to Node 09 (Hard Escalation)

        return "n19" # Success -> Proceed to Planner

    def route_after_audit(state: AgentState):
        status = state.get("preflight_status")
        if status == "insufficient":
            return "n2e"  # 🐛 FIX: Loop back to n2e instead of the dead-end n7b
        elif status == "max_retries_exceeded":
            return "n23"  # Hard Escalation

        # 🟢 NEW: Dynamic UX Routing
        if state.get("response_complexity") == "simple":
            print("   🔀 [DYNAMIC UX]: Bypassing Planner -> Direct Generation")
            return "n8"   # Route directly to Response Generator (Node 04b)
        else:
            print("   🔀 [DYNAMIC UX]: Complex Query -> Routing to Planner")
            return "n19"  # Route to Resolution Planner (Node 04a)

    def route_tools(state: AgentState):
        """Reads the array of requested tools and fans-out simultaneously."""
        return state.get("active_tool_routes", []) 

    def route_n19(state: AgentState):
        if state.get("preflight_status") == "fail": return "n5"
        return "n8"

    def route_n20(state: AgentState):
        status = state.get("preflight_status")
        count = state.get("safety_retry_count", 0)
        if status == "pass": return "n21"
        if count < 3: return "n8"
        return "n23" # Max retries

    def route_n21(state: AgentState):
        status = state.get("preflight_status")
        count = state.get("safety_retry_count", 0)
        if status == "pass": return "n22"
        if count < 3: return "n8"
        return "n23" # Max retries

    # --- ATTACH GRAPH EDGES ---
    workflow.set_conditional_entry_point(route_start, {"n00": "n00", "n3": "n3"})
    
    # Phase 0
    workflow.add_edge("n00", "n20")
    workflow.add_conditional_edges("n3", route_n3, {"n16": "n16", "n5": "n5"})
    
    # Phase 1
    workflow.add_conditional_edges("n4", route_n4, {"n6": "n6", "n2d": "n2d"})
    workflow.add_conditional_edges("n6", route_n6, {"n14": "n14", "n8": "n8", "n2d": "n2d"})
    workflow.add_conditional_edges("n16", route_n16, {"n4": "n4", "n5": "n5"})
    workflow.add_conditional_edges("n2d", route_n2d, {"n23": "n23", "n8": "n8", "n2e": "n2e"})
    workflow.add_conditional_edges("n2e", route_n2e, {"n2f": "n2f", "n14": "n14", "n19": "n19", "n18": "n18", "n5": "n5"})
    workflow.add_edge("n2f", END)
    
    # Phase 2 & 3
    # The Fan-Out: Node 03a (n14) triggers multiple nodes concurrently based on the list
    workflow.add_conditional_edges("n14", route_tools, {
        "n17_rag": "n17_rag",
        "n17_api": "n17_api",
        "n17_deeplink": "n17_deeplink"
    })

    # The Fan-In: All workers converge seamlessly at Node 03c (n18)
    workflow.add_edge("n17_rag", "n18")
    workflow.add_edge("n17_api", "n18")
    workflow.add_edge("n17_deeplink", "n18")
    
    workflow.add_conditional_edges("n18", route_after_audit, {"n2e": "n2e", "n23": "n23", "n8": "n8", "n19": "n19"})
    workflow.add_conditional_edges("n19", route_n19, {"n8": "n8", "n5": "n5"})
    workflow.add_edge("n8", "n20")
    
    # Phase 4
    workflow.add_conditional_edges("n20", route_n20, {"n21": "n21", "n8": "n8", "n23": "n23"})
    workflow.add_conditional_edges("n21", route_n21, {"n22": "n22", "n8": "n8", "n23": "n23"})
    workflow.add_edge("n22", "n24")
    
    # Terminal States
    workflow.add_edge("n24", END)
    workflow.add_edge("n23", END)
    workflow.add_edge("n5", END)

    memory = MemorySaver()
    return workflow.compile(checkpointer=memory)

# ADD THIS EXACT LINE TO EXPOSE THE GRAPH TO UVICORN
app_graph = build_agent_graph()
