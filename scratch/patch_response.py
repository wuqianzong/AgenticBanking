with open("backend/app/orchestration/nodes.py", "r") as f:
    content = f.read()

target = """    unavailable_service_requests = _unavailable_service_requests(state)"""

replacement = """    unavailable_service_requests = _unavailable_service_requests(state)
    deeplink_results = [
        {"destination_id": r.get("approved_result", {}).get("destination_id")}
        for r in state.get("tool_results", [])
        if r.get("tool_name") == "deeplink" and r.get("status") == "completed"
    ]"""

content = content.replace(target, replacement)

target2 = """    Pending service request forms registered this turn: {pending_service_request_count}"""

replacement2 = """    Available deeplinks for this turn: {json.dumps(deeplink_results, ensure_ascii=False)}
    (If a deeplink destination_id is provided above, you MUST insert a placeholder exactly like {{deeplink-<destination_id>}} in your text where you want to show a button for the user to navigate to that screen.)
    Pending service request forms registered this turn: {pending_service_request_count}"""

content = content.replace(target2, replacement2)

with open("backend/app/orchestration/nodes.py", "w") as f:
    f.write(content)
print("Updated node_04b prompt")
