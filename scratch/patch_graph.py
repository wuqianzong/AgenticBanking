with open("backend/app/orchestration/graph.py", "r") as f:
    content = f.read()

content = content.replace(
    '''workflow.add_node("n17_rag", nodes.node_03b_rag_worker)''',
    '''workflow.add_node("n17_rag", nodes.node_03b_rag_worker)\nworkflow.add_node("n17_deeplink", nodes.node_03b_deeplink_worker)'''
)

content = content.replace(
    '''    if state.get("rag_tool_active"):
        return "n17_rag"
    
    return "n04a"''',
    '''    if state.get("rag_tool_active"):
        return "n17_rag"
    if state.get("deeplink_tool_active"):
        return "n17_deeplink"
    
    return "n04a"'''
)

content = content.replace(
    '''workflow.add_edge("n17_rag", "n04a")''',
    '''workflow.add_edge("n17_rag", "n04a")\nworkflow.add_edge("n17_deeplink", "n04a")'''
)

with open("backend/app/orchestration/graph.py", "w") as f:
    f.write(content)
print("Updated graph.py")
