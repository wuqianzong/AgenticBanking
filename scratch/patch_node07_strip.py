with open("backend/app/orchestration/nodes.py", "r") as f:
    content = f.read()

target = r"""    deeplink_matches = re.findall(r"\{{1,2}deeplink-([^}]+)\}{1,2}", combined_text)
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
        
        # Clean both single and double bracket forms from ALL text fields globally
        def _strip_deeplink(obj):
            if isinstance(obj, str):
                return re.sub(f"\\{{1,2}}deeplink-{destination_id}\\}}{{1,2}}", "", obj).strip()
            if isinstance(obj, list):
                return [_strip_deeplink(item) for item in obj]
            if isinstance(obj, dict):
                return {k: _strip_deeplink(v) for k, v in obj.items()}
            return obj
            
        draft = _strip_deeplink(draft)
        options = _strip_deeplink(options)
        step_details = _strip_deeplink(step_details)
        list_items = _strip_deeplink(list_items)
        list_title = _strip_deeplink(list_title)
        list_intro = _strip_deeplink(list_intro)
        extended_text = _strip_deeplink(extended_text)
        list_clarification = _strip_deeplink(list_clarification)
        response_blocks = _strip_deeplink(response_blocks)"""

content = content.replace(target, replacement)

with open("backend/app/orchestration/nodes.py", "w") as f:
    f.write(content)
print("Updated node_07 globally stripped deeplinks")
