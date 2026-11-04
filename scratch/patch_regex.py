with open("backend/app/orchestration/nodes.py", "r") as f:
    content = f.read()

# Replace the specific node_07 regex logic
target = r"""    deeplink_matches = re.findall(r"\{\{deeplink-([^}]+)\}\}", combined_text)
    if deeplink_matches:
        resolved_destinations = set(_resolved_deeplink_destinations(state))
        destination_id = deeplink_matches[0]
        contract = DEEPLINK_DESTINATION_REGISTRY.get(destination_id)
        if contract and destination_id in resolved_destinations:
            payload["navigationAction"] = {
                "label": contract["label"],
                "destinationId": destination_id,
                "route": contract["route"]
            }
            print(f"   🔗 [DEEPLINK SUB]: Replaced deeplink-{destination_id} -> navigationAction")
            precision_substituted = True
        else:
            print(f"   🚫 [DEEPLINK VETO]: '{destination_id}' was not resolved this turn -- stripping placeholder without a button.")
        draft = draft.replace(f"{{{{deeplink-{destination_id}}}}}", "").strip()"""

# The LLM sometimes outputs {deeplink-id} instead of {{deeplink-id}} because 
# {{ is interpreted by python f-strings as a single bracket.
# So we update the regex to allow single or double curly braces.
replacement = r"""    deeplink_matches = re.findall(r"\{{1,2}deeplink-([^}]+)\}{1,2}", combined_text)
    if deeplink_matches:
        resolved_destinations = set(_resolved_deeplink_destinations(state))
        destination_id = deeplink_matches[0]
        contract = DEEPLINK_DESTINATION_REGISTRY.get(destination_id)
        if contract and destination_id in resolved_destinations:
            payload["navigationAction"] = {
                "label": contract["label"],
                "destinationId": destination_id,
                "route": contract["route"]
            }
            print(f"   🔗 [DEEPLINK SUB]: Replaced deeplink-{destination_id} -> navigationAction")
            precision_substituted = True
        else:
            print(f"   🚫 [DEEPLINK VETO]: '{destination_id}' was not resolved this turn -- stripping placeholder without a button.")
        
        # Clean both single and double bracket forms from the final text
        draft = re.sub(f"\\{{1,2}}deeplink-{destination_id}\\}}{{1,2}}", "", draft).strip()"""

content = content.replace(target, replacement)

with open("backend/app/orchestration/nodes.py", "w") as f:
    f.write(content)
print("Updated node_07 regex")
