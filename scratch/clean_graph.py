with open("backend/app/orchestration/graph.py", "r") as f:
    content = f.read()

content = content.replace('workflow.add_node("n17_deeplink", nodes.node_03b_deeplink_worker)\n', '')
content = content.replace('workflow.add_edge("n17_deeplink", "n04a")\n', '')
content = content.replace('''    if state.get("deeplink_tool_active"):
        return "n17_deeplink"
''', '')

with open("backend/app/orchestration/graph.py", "w") as f:
    f.write(content)
print("Cleaned graph.py")
