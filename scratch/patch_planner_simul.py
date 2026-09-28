with open("backend/app/orchestration/nodes.py", "r") as f:
    content = f.read()

target = """    if not existing_tasks:
        # RAG is the only enabled executor in this release. This explicit task
        # creation is the safe fallback if the planner is unavailable.
        candidates = [
            {
                "subquery_id": subquery_id,
                "tool_name": "rag",
                "operation": "retrieve_faq_evidence",
                "input": {"customer_component": item["customer_component"]},
            }
            for subquery_id, item in known_subqueries.items()
        ]
    elif tool_gaps:
        prompt = f\"\"\"
        INCREMENTAL TOOL-CALLING PLANNER
        You may add tool tasks only when an unresolved, non-factual capability
        gap can be materially addressed by a registered enabled tool.

        Financial subqueries: {json.dumps(financial_subqueries, ensure_ascii=False)}
        Validated terminal tool results: {json.dumps(state.get("tool_results", []), ensure_ascii=False)}
        Resolution capability gaps: {json.dumps(tool_gaps, ensure_ascii=False)}"""

replacement = """    if not existing_tasks or tool_gaps:
        prompt = f\"\"\"
        INCREMENTAL TOOL-CALLING PLANNER
        You must plan tool tasks to address the customer's financial subqueries.
        If this is the first round, plan all tools that could be useful (e.g., RAG for knowledge, deeplink for navigation, service_request for forms).
        In later rounds, you may add tool tasks only when an unresolved capability gap remains.

        Financial subqueries: {json.dumps(financial_subqueries, ensure_ascii=False)}
        Validated terminal tool results: {json.dumps(state.get("tool_results", []), ensure_ascii=False)}
        Resolution capability gaps: {json.dumps(tool_gaps, ensure_ascii=False)}"""

content = content.replace(target, replacement)

with open("backend/app/orchestration/nodes.py", "w") as f:
    f.write(content)
print("Updated nodes.py to allow simultaneous tool calling")
