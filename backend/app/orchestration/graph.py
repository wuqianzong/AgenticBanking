from langgraph.graph import StateGraph, END
from langgraph.checkpoint.memory import MemorySaver # NEW: Required for cross-turn memory
from backend.app.orchestration.state import AgentState
from backend.app.orchestration import nodes

def build_agent_graph():
    workflow = StateGraph(AgentState)

    # 1. Register Nodes (Mapped to Mermaid Node IDs)
    workflow.add_node("n00", nodes.node_00_context_hydrator_and_greeting)
    workflow.add_node("n3",  nodes.node_01_deterministic_ingest_gate)
    workflow.add_node("n4",  nodes.node_02a_pending_contract_check)
    workflow.add_node("n6",  nodes.node_02b_confirmation_gate)
    workflow.add_node("n16", nodes.node_02c_inbound_semantic_gate)
    workflow.add_node("n7",  nodes.node_02d_intent_classifier)
    workflow.add_node("n12", nodes.node_02e_contract_writer)
    
    workflow.add_node("n14", nodes.node_03a_knowledge_blueprint_planner)
    workflow.add_node("n17", nodes.node_03b_vector_retrieval_engine)
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
        
        if not history: 
            print("🔀 [ROUTE] History empty -> Routing to n00 (Greeting/Init)")
            print("="*50 + "\n")
            return "n00" # Turn 1
            
        print("🔀 [ROUTE] History found -> Routing to n3 (Ingest Gate)")
        print("="*50 + "\n")
        return "n3"      # Turns 2+
        
    def route_n3(state: AgentState):
        if state.get("preflight_status") == "fail": return "n5"
        return "n4"

    def route_n4(state: AgentState):
        if state.get("preflight_status") == "yes": return "n6"
        return "n16"

    def route_n6(state: AgentState):
        status = state.get("preflight_status")
        if status == "yes": return "n14"
        if status == "no": return "n8"
        return "n16" # Else (Ambiguous)

    def route_n16(state: AgentState):
        if state.get("preflight_status") == "pass": return "n7"
        return "n5"

    def route_n7(state: AgentState):
        status = state.get("preflight_status")
        if status == "intent_change": return "n12"
        if status == "intent_persist": return "n8"
        if status in ["new_intent", "intent_drift"]: return "n14"
        if status in ["out_of_knowledge", "user_escalation"]: return "n23"
        return "n5" # Unexpected Exception

    def route_n18(state: AgentState):
        status = state.get("preflight_status")
        count = state.get("knowledge_retry_count", 0)
        if status == "sufficient": return "n19"
        if count < 3: return "n14"
        return "n23" # Max retries

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
    workflow.add_conditional_edges("n3", route_n3, {"n4": "n4", "n5": "n5"})
    
    # Phase 1
    workflow.add_conditional_edges("n4", route_n4, {"n6": "n6", "n16": "n16"})
    workflow.add_conditional_edges("n6", route_n6, {"n14": "n14", "n8": "n8", "n16": "n16"})
    workflow.add_conditional_edges("n16", route_n16, {"n7": "n7", "n5": "n5"})
    workflow.add_conditional_edges("n7", route_n7, {
        "n12": "n12", "n8": "n8", "n14": "n14", "n23": "n23", "n5": "n5"
    })
    workflow.add_edge("n12", END)
    
    # Phase 2 & 3
    workflow.add_edge("n14", "n17")
    workflow.add_edge("n17", "n18")
    workflow.add_conditional_edges("n18", route_n18, {"n19": "n19", "n14": "n14", "n23": "n23"})
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