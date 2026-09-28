with open("backend/app/orchestration/nodes.py", "r") as f:
    content = f.read()

target = """    if not existing_tasks or tool_gaps:
        prompt = f\"\"\"
        INCREMENTAL TOOL-CALLING PLANNER
        You must plan tool tasks to address the customer's financial subqueries.
        If this is the first round, plan all tools that could be useful (e.g., RAG for knowledge, deeplink for navigation, service_request for forms).
        In later rounds, you may add tool tasks only when an unresolved capability gap remains."""

replacement = """    if not existing_tasks or tool_gaps:
        deterministic_rag_candidates = []
        if not existing_tasks:
            deterministic_rag_candidates = [
                {
                    "subquery_id": subquery_id,
                    "tool_name": "rag",
                    "operation": "retrieve_faq_evidence",
                    "input": {"customer_component": item["customer_component"]},
                }
                for subquery_id, item in known_subqueries.items()
            ]

        prompt = f\"\"\"
        INCREMENTAL TOOL-CALLING PLANNER
        You must plan tool tasks to address the customer's financial subqueries.
        In the first round, we automatically plan RAG tasks for knowledge. You should plan any OTHER simultaneous tools (like deeplink or service_request) that are relevant.
        In later rounds, you may add tool tasks only when an unresolved capability gap remains."""

content = content.replace(target, replacement)

target2 = """            candidates = _parse_json_robustly(call_groq_lpu(prompt, model_id=MODEL_C_PLANNER)).get("tool_tasks", [])
        except Exception as error:
            print(f"⚠️ [TOOL PLANNER ERROR]: {error}")
            candidates = []"""

replacement2 = """            candidates = _parse_json_robustly(call_groq_lpu(prompt, model_id=MODEL_C_PLANNER)).get("tool_tasks", [])
        except Exception as error:
            print(f"⚠️ [TOOL PLANNER ERROR]: {error}")
            candidates = []
            
        if not existing_tasks:
            # Combine deterministic RAG with whatever else the LLM decided to run simultaneously.
            # Filter out any duplicate RAG tasks the LLM might have hallucinated.
            llm_candidates = [c for c in candidates if c.get("tool_name") != "rag"]
            candidates = deterministic_rag_candidates + llm_candidates"""

content = content.replace(target2, replacement2)

with open("backend/app/orchestration/nodes.py", "w") as f:
    f.write(content)
print("Updated nodes.py to force deterministic RAG + simultaneous LLM tools")
