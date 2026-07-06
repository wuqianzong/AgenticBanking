from langgraph.graph import StateGraph, START, END
from langgraph.checkpoint.memory import MemorySaver
from backend.app.orchestration.state import AgentState
from backend.app.orchestration import nodes

def build_orchestrator():
    builder = StateGraph(AgentState)

    # --- ADD NODES ---
    builder.add_node("node_0_turn_init", nodes.node_0_turn_init)
    builder.add_node("node_0_hydrate", nodes.node_0_hydrate)
    builder.add_node("node_1_ingest", nodes.node_1_deterministic_ingest_gate)
    builder.add_node("node_2_intent", nodes.node_2_intent_router)
    builder.add_node("node_3_retrieve", nodes.node_3_retrieval_execution)
    builder.add_node("node_4_generate", nodes.node_4_response_generator)
    builder.add_node("node_5_semantic", nodes.node_5_semantic_gate)
    builder.add_node("node_5_static_refusal", nodes.node_5_static_refusal)
    builder.add_node("node_5_soft_escalation", nodes.node_5_soft_escalation)
    builder.add_node("node_loop_search", nodes.node_loop_search)
    builder.add_node("node_loop_draft", nodes.node_loop_draft)
    builder.add_node("node_6_substitute", nodes.node_6_precision_substitution)
    builder.add_node("node_7_clarify", nodes.node_7_clarification_mediator)
    builder.add_node("node_8_escalate", nodes.node_8_escalation_handler)

    # --- CONDITIONAL ROUTING LOGIC ---
    def route_turn_init(state: AgentState):
        if not state.get("chat_history"):
            return "hydrate"
        return "ingest"

    def route_ingest(state: AgentState):
        # Implement deterministic block check
        is_safe = True 
        return "intent" if is_safe else "escalate"

    def route_intent(state: AgentState):
        strategy = state.get("retrieval_strategy")
        if strategy == "clarify":
            return "clarify"
        elif strategy in ["vector_search", "path_lookup", "relational_lookup"]:
            return "retrieve"
        return "escalate" # Unmapped exception

    def route_semantic(state: AgentState):
        status = state.get("semantic_evaluation_status")
        if status == "mas_violation":
            return "refusal"
        elif status == "groundedness_fail":
            return "loop_search" if state.get("knowledge_retry_count", 0) < 2 else "escalate"
        elif status == "generation_fail":
            return "loop_draft" if state.get("safety_retry_count", 0) < 2 else "escalate"
        elif status == "fatigue":
            return "soft_escalate"
        return "substitute" # Default PASS

    def route_substitution(state: AgentState):
        return "end" if state.get("substitution_status") == "valid" else "escalate"

    # --- ADD EDGES ---
    builder.add_edge(START, "node_0_turn_init")
    builder.add_conditional_edges("node_0_turn_init", route_turn_init, {"hydrate": "node_0_hydrate", "ingest": "node_1_ingest"})
    builder.add_edge("node_0_hydrate", "node_1_ingest")
    
    builder.add_conditional_edges("node_1_ingest", route_ingest, {"intent": "node_2_intent", "escalate": "node_8_escalate"})
    builder.add_conditional_edges("node_2_intent", route_intent, {"clarify": "node_7_clarify", "retrieve": "node_3_retrieve", "escalate": "node_8_escalate"})
    
    builder.add_edge("node_3_retrieve", "node_4_generate")
    builder.add_edge("node_4_generate", "node_5_semantic")
    
    builder.add_conditional_edges("node_5_semantic", route_semantic, {
        "refusal": "node_5_static_refusal",
        "loop_search": "node_loop_search",
        "loop_draft": "node_loop_draft",
        "soft_escalate": "node_5_soft_escalation",
        "substitute": "node_6_substitute",
        "escalate": "node_8_escalate"
    })
    
    # Internal Loops
    builder.add_edge("node_loop_search", "node_2_intent")
    builder.add_edge("node_loop_draft", "node_4_generate")
    builder.add_edge("node_5_soft_escalation", "node_6_substitute")
    
    # Termination Edges
    builder.add_edge("node_5_static_refusal", END)
    builder.add_conditional_edges("node_6_substitute", route_substitution, {"end": END, "escalate": "node_8_escalate"})
    builder.add_edge("node_7_clarify", END)
    builder.add_edge("node_8_escalate", END)

    #return builder.compile()
    # INITIALIZE THE IN-MEMORY CHECKPOINTER
    memory = MemorySaver()

    # ATTACH THE CHECKPOINTER WHEN COMPILING
    return builder.compile(checkpointer=memory)

# Instantiated graph to be imported into api_gateway.py
app_graph = build_orchestrator()