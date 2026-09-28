with open("backend/app/orchestration/nodes.py", "r") as f:
    content = f.read()

target = """        - For tool_name "service_request", input must use the literal key
          "service_request_id" -- NOT "customer_component" (that key belongs
          only to rag tasks; do not copy it here even though it appears
          elsewhere in this prompt). Example of the exact shape required:
          {{"service_request_id": "profile-address-update"}}. Choose the
          single best-fitting id from that tool's available_requests in the
          enabled tool registry; never invent an id, and never include form
          field values (the customer has not filled anything in yet).
        - Read the full description of each available_requests entry before
          choosing one -- a description can scope out specific reasons, not
          just name a general topic. "Request a replacement physical card
          for a non-fraud reason (worn or damaged)" does not cover a lost or
          stolen card, even though both are card replacement in general
          terms. If the customer's actual situation falls outside every
          candidate's stated scope, do not create a task for it at all."""

replacement = """        - For tool_name "service_request", input must use the literal key
          "service_request_id". Choose the best-fitting id from that tool's
          available_requests in the registry. Never invent an id or include form fields.
        - For tool_name "deeplink", input must use the literal key
          "destination_id". Example: {{"destination_id": "card-management"}}.
          Use deeplinks to navigate the user to specific screens in the app
          (e.g., manage card, view statements) based on the available_destinations
          in the registry. A deeplink will be substituted into a button for the
          user to click.
        - Read the full description of each registry entry before choosing one.
          If the customer's actual situation falls outside every candidate's stated
          scope, do not create a task for it at all."""

content = content.replace(target, replacement)

with open("backend/app/orchestration/nodes.py", "w") as f:
    f.write(content)
print("Updated node_03a prompt in nodes.py")
