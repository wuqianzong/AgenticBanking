from langgraph.graph import StateGraph, END
from langgraph.checkpoint.memory import MemorySaver
from backend.app.orchestration.state import AgentState
from backend.app.orchestration import nodes


def build_agent_graph():
    """Build the User Journey v2.0 graph.

    Product/topic tasks are decomposed before retrieval. The presentation
    journey is selected only after task-level evidence has passed sufficiency.
    """
    workflow = StateGraph(AgentState)

    workflow.add_node("n00", nodes.node_00_context_hydrator_and_greeting)
    workflow.add_node("n00b", nodes.node_00b_followup_check)
    workflow.add_node("n3", nodes.node_01_deterministic_ingest_gate)
    workflow.add_node("n1b", nodes.node_01b_load_user_profile)
    workflow.add_node("n16", nodes.node_02c_inbound_semantic_gate)
    workflow.add_node("n2d", nodes.node_02d_user_journey_classifier_and_triage)
    workflow.add_node("n2e", nodes.node_02e_intent_classifier_and_query_formulator)
    workflow.add_node("n14", nodes.node_03a_knowledge_blueprint_planner)
    workflow.add_node("n17_dispatch", nodes.node_03b_tool_dispatcher)
    workflow.add_node("n17_rag", nodes.node_03b_rag_worker)
    workflow.add_node("n18", nodes.node_03c_sufficiency_auditor)
    workflow.add_node("n19", nodes.node_04a_resolution_planner)
    workflow.add_node("n8", nodes.node_04b_response_generator)
    workflow.add_node("n8b", nodes.node_04c_response_reviser)
    workflow.add_node("n21_audit", nodes.node_06_outbound_combined_audit_gate)
    workflow.add_node("n21", nodes.node_06_outbound_audit_join)
    workflow.add_node("n22", nodes.node_07_precision_substitution)
    workflow.add_node("n24", nodes.node_08_soft_escalation_analyzer)
    workflow.add_node("n23", nodes.node_09_hard_escalation_broker)
    workflow.add_node("n5", nodes.node_10_standard_rejection)
    workflow.add_node("n25", nodes.node_11_deterministic_clarification_fallback)
    workflow.add_node("n_svc", nodes.node_svc_service_request_resolver)

    def route_start(state: AgentState):
        history = state.get("chat_history", [])
        print("\n" + "=" * 50)
        print("🚦 [ROUTER GATE] Initializing Graph Edge")
        print(f"📦 [STATE CHECK] chat_history length: {len(history)}")
        print(f"🗣️  [INPUT] user_input: '{state.get('user_input', '')}'")
        if state.get("service_request_event"):
            print("🔀 [ROUTE] Service request event present -> Routing to n_svc (Special Route)")
            print("=" * 50 + "\n")
            return "n_svc"
        if not history and state.get("user_input") in {"__INIT__", "**INIT__"}:
            print("🔀 [ROUTE] History empty -> Routing to n00 (Greeting/Init)")
            print("=" * 50 + "\n")
            return "n00"
        print("🔀 [ROUTE] History found -> Routing to n3 (Ingest Gate)")
        print("=" * 50 + "\n")
        return "n3"

    def route_n00(state: AgentState):
        return "n00b" if state.get("preflight_status") == "greeting_followup_check" else "end"

    def route_n3(state: AgentState):
        if state.get("preflight_status") == "escalation_confirmation_accepted":
            return "n23"
        return "n16" if state.get("preflight_status") == "pass" else "n5"

    def route_n16(state: AgentState):
        return "n1b" if state.get("preflight_status") == "pass" else "n5"

    def route_n2d(state: AgentState):
        status = state.get("triage_status")
        if status == "ESCALATION":
            return "n23"
        if status == "NON_COMPLIANT":
            return "n5"
        # DECOMPOSED: triage always partitions the turn into components
        # (TOOL_ELIGIBLE / NON_FINANCIAL / CLARIFICATION -- see node_02d).
        # Only a turn with at least one TOOL_ELIGIBLE component needs n14
        # (tool planning) at all; a turn that's entirely NON_FINANCIAL/
        # CLARIFICATION has nothing for n14 to plan, so it skips straight to
        # n19 (node_04a) the same way node_02e's own clarification-only
        # turns already reach it (route_n2e's "intent_clarification"
        # branch) -- this is what keeps a pure-chitchat or pure-ambiguous
        # turn from ever reaching the RAG/tool-calling loop at all.
        if state.get("financial_subqueries"):
            return "n14"
        return "n19"

    def route_n2e(state: AgentState):
        status = state.get("preflight_status")
        if status == "declined":
            return "n5"
        if status == "retrieve":
            return "n17_rag"
        if status == "continue_plan":
            return "n19"
        if status == "intent_clarification":
            return "n19"
        return "n8"

    def route_tool_planner(state: AgentState):
        return "n17_dispatch" if state.get("preflight_status") == "tool_dispatch" else "n19"

    def route_tool_dispatcher(state: AgentState):
        return "n2e" if state.get("preflight_status") == "rag_tool_active" else "n19"

    def route_n18(state: AgentState):
        return "n2e" if state.get("preflight_status") == "retry_tasks" else "n19"

    def route_n19(state: AgentState):
        if state.get("preflight_status") == "needs_tools":
            return "n14"
        if state.get("preflight_status") == "fail":
            return "n5"
        # A pending intent_clarification_task with no search_tasks at all
        # used to be special-cased straight to n25 here, because n8's single
        # open-ended prompt reliably gave a bland non-answer instead of
        # asking the pending clarification. n8 now dispatches to a narrower
        # sub-prompt for exactly this shape of turn (see
        # _generate_evidence_only_response in nodes.py), whose schema has no
        # alternative field to substitute a different answer into -- so this
        # case goes through n8 like any other turn, sharing the same node_06
        # audit. n25 (node_11) is reachable only via route_n21's own
        # three-failed-retries condition now.
        return "n8"

    def route_n21(state: AgentState):
        count = state.get("safety_retry_count", 0)
        if state.get("preflight_status") == "pass":
            return "n22"
        # Exhausted retries never hard-escalate anymore: n23 is reserved for
        # a customer's own explicit request for a human (triage's ESCALATION
        # status, or an accepted transfer confirmation), never for the bot's
        # own repeated inability to pass its own audit. n25 renders whatever
        # deterministic evidence actually exists (clarification/decline/
        # no-info) when there is any, or a plain "I can't answer that right
        # now" apology when there genuinely is none -- see node_11's
        # docstring.
        if count >= 3:
            return "n25"
        # Per-Task Outbound Response Revision Loop: a normal_response-scope
        # turn's failure is task-attributed (pending_task_ids/
        # response_wide_violation_this_round, set by node_06_outbound_
        # audit_join's new partition branch) and only ever needs the
        # narrower node_04c reviser, never a full node_04b redraft. Every
        # other scope (non-financial/evidence-only, _is_pure_conversational_
        # turn) keeps the original full-redraft path unchanged -- those
        # turns are short/single-element and were never node_06's new
        # partition branch to begin with.
        return "n8" if nodes._is_pure_conversational_turn(state) else "n8b"

    workflow.set_conditional_entry_point(route_start, {"n00": "n00", "n3": "n3", "n_svc": "n_svc"})
    workflow.add_edge("n_svc", END)
    # The greeting itself is fully deterministic (no LLM call, no generated
    # claim), so it skips the outbound audit/precision/escalation chain --
    # there is nothing there for it to check. n00b is a separate, optional
    # step that only runs when n00 found a prior transcript or a pending
    # service request worth checking; n00b does its own focused audit calls
    # inline (see node_00b_followup_check) rather than reusing n21_audit/n21,
    # since those are shaped around a RAG-answered turn, not a login check.
    workflow.add_conditional_edges("n00", route_n00, {"n00b": "n00b", "end": END})
    workflow.add_edge("n00b", END)
    workflow.add_conditional_edges("n3", route_n3, {"n16": "n16", "n23": "n23", "n5": "n5"})
    workflow.add_conditional_edges("n16", route_n16, {"n1b": "n1b", "n5": "n5"})
    workflow.add_edge("n1b", "n2d")
    workflow.add_conditional_edges("n2d", route_n2d, {"n23": "n23", "n14": "n14", "n5": "n5", "n19": "n19"})
    workflow.add_conditional_edges("n2e", route_n2e, {"n17_rag": "n17_rag", "n19": "n19", "n8": "n8", "n5": "n5"})
    workflow.add_conditional_edges("n14", route_tool_planner, {"n17_dispatch": "n17_dispatch", "n19": "n19"})
    workflow.add_conditional_edges("n17_dispatch", route_tool_dispatcher, {"n2e": "n2e", "n19": "n19"})
    workflow.add_edge("n17_rag", "n18")
    workflow.add_conditional_edges("n18", route_n18, {"n2e": "n2e", "n19": "n19"})
    workflow.add_conditional_edges("n19", route_n19, {"n14": "n14", "n8": "n8", "n5": "n5", "n25": "n25"})
    # Generated/revised output goes straight to the audit. The deterministic
    # output checks (formerly their own "n20" gate BEFORE the audit, which
    # forced a full regeneration of un-audited text on any hit) now run
    # inside n21 alongside the audit's findings, so everything wrong with a
    # draft is revised together in one reviser call.
    workflow.add_edge("n8", "n21_audit")
    workflow.add_edge("n21_audit", "n21")
    workflow.add_conditional_edges("n21", route_n21, {"n22": "n22", "n8": "n8", "n8b": "n8b", "n25": "n25"})
    # The reassembled candidate goes back through the same audit + join --
    # both re-scan whatever is currently in ui_action_payload regardless of
    # which node produced it.
    workflow.add_edge("n8b", "n21_audit")
    workflow.add_edge("n22", "n24")
    workflow.add_edge("n24", END)
    workflow.add_edge("n23", END)
    workflow.add_edge("n5", END)
    workflow.add_edge("n25", END)

    return workflow.compile(checkpointer=MemorySaver())


app_graph = build_agent_graph()
