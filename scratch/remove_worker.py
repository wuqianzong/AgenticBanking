import re
with open("backend/app/orchestration/nodes.py", "r") as f:
    content = f.read()

content = re.sub(r'def _finalize_deeplink_tool_results[\s\S]*?def node_03b_deeplink_worker[\s\S]*?return {"tool_results": tool_results, "deeplink_tool_active": False}', '', content)

with open("backend/app/orchestration/nodes.py", "w") as f:
    f.write(content)
print("Removed worker")
