with open("backend/app/orchestration/tool_registry.py", "r") as f:
    content = f.read()

content = content.replace(
    '''    "deeplink": {
        "operations": {"resolve_navigation"},
        "enabled": False,
        "read_only": True,
        "description": "Return a validated Horizon navigation destination.",
    },''',
    '''    "deeplink": {
        "operations": {"resolve_navigation"},
        "enabled": True,
        "read_only": True,
        "description": "Return a validated Horizon navigation destination. Use this when the customer needs to navigate to a specific screen in the app.",
    },'''
)

enrichment = '''    from backend.app.orchestration.service_request_registry import available_service_requests'''
new_enrichment = '''    from backend.app.orchestration.service_request_registry import available_service_requests
    from backend.app.orchestration.deeplink_destination_registry import available_deeplink_destinations'''

content = content.replace(enrichment, new_enrichment)

enrichment2 = '''        if name == "service_request":
            capability["available_requests"] = available_service_requests()'''
new_enrichment2 = '''        if name == "service_request":
            capability["available_requests"] = available_service_requests()
        if name == "deeplink":
            capability["available_destinations"] = available_deeplink_destinations()'''

content = content.replace(enrichment2, new_enrichment2)

with open("backend/app/orchestration/tool_registry.py", "w") as f:
    f.write(content)
print("Updated tool_registry.py")
