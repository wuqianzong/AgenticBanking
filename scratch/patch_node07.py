with open("backend/app/orchestration/nodes.py", "r") as f:
    content = f.read()

target = """    if placeholders_in_draft:
        valid_map = fetch_placeholders_from_db(placeholders_in_draft)
        precision_substituted = bool(valid_map)"""

replacement = """    # Check for deeplink placeholders
    deeplink_matches = re.findall(r"\{\{deeplink-([^}]+)\}\}", combined_text)
    if deeplink_matches:
        from backend.app.orchestration.deeplink_destination_registry import DEEPLINK_DESTINATION_REGISTRY
        destination_id = deeplink_matches[0]
        contract = DEEPLINK_DESTINATION_REGISTRY.get(destination_id)
        if contract:
            payload["navigationAction"] = {
                "label": contract["label"],
                "destinationId": destination_id,
                "route": contract["route"]
            }
            draft = draft.replace(f"{{{{deeplink-{destination_id}}}}}", "").strip()
            print(f"   🔗 [DEEPLINK SUB]: Replaced deeplink-{destination_id} -> navigationAction")
            precision_substituted = True

    if placeholders_in_draft:
        valid_map = fetch_placeholders_from_db(placeholders_in_draft)
        precision_substituted = precision_substituted or bool(valid_map)"""

content = content.replace(target, replacement)

with open("backend/app/orchestration/nodes.py", "w") as f:
    f.write(content)
print("Updated node_07")
