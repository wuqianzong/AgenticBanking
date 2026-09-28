with open("backend/app/orchestration/nodes.py", "r") as f:
    content = f.read()

content = content.replace('''        if tool_name == "deeplink":
            task["status"] = "running"
            deeplink_task_ids.append(task["tool_task_id"])
            continue''', '')

content = content.replace('''    deeplink_task_ids = []''', '')
content = content.replace('''"preflight_status": "tool_results_ready" if not rag_task_ids and not deeplink_task_ids else "tool_tasks_dispatched",''', '''"preflight_status": "tool_results_ready" if not rag_task_ids else "tool_tasks_dispatched",''')
content = content.replace('''"pending_tool_task_ids": rag_task_ids + deeplink_task_ids,''', '''"pending_tool_task_ids": rag_task_ids,''')
content = content.replace('''"deeplink_tool_active": bool(deeplink_task_ids),''', '')

with open("backend/app/orchestration/nodes.py", "w") as f:
    f.write(content)
print("Cleaned dispatcher")
