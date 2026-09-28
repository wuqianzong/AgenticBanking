with open("backend/app/api_gateway.py", "r") as f:
    content = f.read()

target = """                "serviceRequestActions": execution_result.get("ui_action_payload", {}).get("serviceRequestActions", []),"""

replacement = """                "serviceRequestActions": execution_result.get("ui_action_payload", {}).get("serviceRequestActions", []),
                "navigationAction": execution_result.get("ui_action_payload", {}).get("navigationAction", None),"""

content = content.replace(target, replacement)

with open("backend/app/api_gateway.py", "w") as f:
    f.write(content)
print("Updated api_gateway.py")
