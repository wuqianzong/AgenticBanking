with open("backend/app/orchestration/nodes.py", "r") as f:
    content = f.read()

target = """        if tool_name == "service_request":
            outcome = _dispatch_service_request_task(state, task)
            task["status"] = outcome["status"]"""

replacement = """        if tool_name == "service_request":
            outcome = _dispatch_service_request_task(state, task)
            task["status"] = outcome["status"]
            tool_results.append({
                "tool_task_id": task["tool_task_id"],
                "subquery_id": task.get("subquery_id"),
                "tool_name": tool_name,
                "status": outcome["status"],
                "approved_result": outcome.get("approved_result", {}),
                "customer_safe_summary": "",
                "availability_reason": outcome.get("reason", ""),
            })
            log_event(
                "tool.result.service_request",
                tool_task_id=task["tool_task_id"], subquery_id=task.get("subquery_id"),
                status=outcome["status"],
                special_id=outcome.get("approved_result", {}).get("special_id"),
            )
            continue
        if tool_name == "deeplink":
            from backend.app.orchestration.deeplink_destination_registry import DEEPLINK_DESTINATION_REGISTRY
            destination_id = task.get("input", {}).get("destination_id")
            contract = DEEPLINK_DESTINATION_REGISTRY.get(destination_id)
            if contract:
                status = "completed"
                approved_result = {"destination_id": destination_id, "route": contract["route"]}
                reason = ""
            else:
                status = "unavailable"
                approved_result = {}
                reason = f"Destination {destination_id} not in registry"
            task["status"] = status"""

content = content.replace(target, replacement)

# The replaced text had the tool_results.append block. I need to make sure I didn't duplicate it.
# Wait, let's just use regex or a cleaner replace.

import re
content = re.sub(
    r'(if tool_name == "service_request":[\s\S]*?continue\n)',
    r'\1' + r'''        if tool_name == "deeplink":
            from backend.app.orchestration.deeplink_destination_registry import DEEPLINK_DESTINATION_REGISTRY
            destination_id = task.get("input", {}).get("destination_id")
            contract = DEEPLINK_DESTINATION_REGISTRY.get(destination_id)
            if contract:
                status = "completed"
                approved_result = {"destination_id": destination_id, "route": contract["route"]}
                reason = ""
            else:
                status = "unavailable"
                approved_result = {}
                reason = f"Destination {destination_id} not in registry"
            task["status"] = status
            tool_results.append({
                "tool_task_id": task["tool_task_id"],
                "subquery_id": task.get("subquery_id"),
                "tool_name": tool_name,
                "status": status,
                "approved_result": approved_result,
                "customer_safe_summary": "",
                "availability_reason": reason,
            })
            continue
''',
    content
)

with open("backend/app/orchestration/nodes.py", "w") as f:
    f.write(content)
print("Updated node_03b_tool_dispatcher inline")
