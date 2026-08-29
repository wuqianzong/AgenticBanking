with open("backend/app/orchestration/nodes.py", "r") as f:
    content = f.read()

target1 = """def node_03b_deeplink_worker(state: AgentState):
    return {"deeplink_payload": "[DEEPLINK DATA]:\nNo Deep-links requested for this MVP."}"""

replacement1 = """def _finalize_deeplink_tool_results(task: dict, destination_id: str | None, route: str | None, availability_reason: str | None) -> dict:
    return {
        "tool_task_id": task["tool_task_id"],
        "tool_name": "deeplink",
        "status": "completed" if route else "unavailable",
        "approved_result": {"destination_id": destination_id, "route": route} if route else None,
        "availability_reason": availability_reason,
    }

def node_03b_deeplink_worker(state: AgentState):
    from backend.app.orchestration.deeplink_destination_registry import DEEPLINK_DESTINATION_REGISTRY
    
    print("🟢 [NODE 03b] Executing Deeplink Worker")
    pending_tasks = [task for task in state.get("tool_tasks", []) if task.get("tool_name") == "deeplink" and task.get("status") == "running"]
    if not pending_tasks:
        return {"deeplink_tool_active": False}
        
    tool_results = list(state.get("tool_results", []))
    for task in pending_tasks:
        destination_id = task.get("input", {}).get("destination_id")
        contract = DEEPLINK_DESTINATION_REGISTRY.get(destination_id)
        if not contract:
            tool_results.append(_finalize_deeplink_tool_results(task, None, None, f"Destination {destination_id} not in registry"))
            continue
            
        tool_results.append(_finalize_deeplink_tool_results(task, destination_id, contract["route"], None))
        
    return {"tool_results": tool_results, "deeplink_tool_active": False}"""

content = content.replace(target1, replacement1)

target2 = """    for task in pending_tasks:
        tool_name = task.get("tool_name")
        if tool_name == "rag":
            task["status"] = "running"
            rag_task_ids.append(task["tool_task_id"])
            continue
        task["status"] = "unavailable"
        tool_results.append({
            "tool_task_id": task["tool_task_id"],
            "tool_name": tool_name,
            "status": "unavailable",
            "availability_reason": f"Tool '{tool_name}' executor is disabled or missing."
        })"""

replacement2 = """    for task in pending_tasks:
        tool_name = task.get("tool_name")
        if tool_name == "rag":
            task["status"] = "running"
            rag_task_ids.append(task["tool_task_id"])
            continue
        if tool_name == "deeplink":
            task["status"] = "running"
            deeplink_task_ids.append(task["tool_task_id"])
            continue
        task["status"] = "unavailable"
        tool_results.append({
            "tool_task_id": task["tool_task_id"],
            "tool_name": tool_name,
            "status": "unavailable",
            "availability_reason": f"Tool '{tool_name}' executor is disabled or missing."
        })"""

content = content.replace(target2, replacement2)

target3 = """    rag_task_ids = []
    
    for task in pending_tasks:"""

replacement3 = """    rag_task_ids = []
    deeplink_task_ids = []
    
    for task in pending_tasks:"""

content = content.replace(target3, replacement3)

target4 = """    return {
        "preflight_status": "tool_results_ready" if not rag_task_ids else "tool_tasks_dispatched",
        "tool_plan_status": "dispatched" if pending_tasks else "exhausted",
        "pending_tool_task_ids": rag_task_ids,
        "tool_results": tool_results,
        "rag_tool_active": bool(rag_task_ids),
    }"""

replacement4 = """    return {
        "preflight_status": "tool_results_ready" if not rag_task_ids and not deeplink_task_ids else "tool_tasks_dispatched",
        "tool_plan_status": "dispatched" if pending_tasks else "exhausted",
        "pending_tool_task_ids": rag_task_ids + deeplink_task_ids,
        "tool_results": tool_results,
        "rag_tool_active": bool(rag_task_ids),
        "deeplink_tool_active": bool(deeplink_task_ids),
    }"""

content = content.replace(target4, replacement4)

with open("backend/app/orchestration/nodes.py", "w") as f:
    f.write(content)
print("Updated nodes.py")
