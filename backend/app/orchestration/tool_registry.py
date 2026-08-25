"""Registered tool contracts for Tool Calling v2.0.

The orchestration graph never hard-codes a list of tool workers.  New tools
are registered here with a small, declarative contract and can later provide
an executor without changing the task ledger or resolution planner.
"""

from __future__ import annotations

from typing import Any


TOOL_REGISTRY: dict[str, dict[str, Any]] = {
    "rag": {
        "operations": {"retrieve_faq_evidence"},
        "enabled": True,
        "read_only": True,
        "description": "Retrieve and verify Horizon FAQ and policy evidence.",
    },
    "service_request": {
        "operations": {"register_service_request"},
        "enabled": True,
        "read_only": False,
        "description": (
            "Register a guided, multi-step form for an approved low- or "
            "intermediate-risk service request (e.g. address update, card "
            "replacement, KYC document resubmission). Never used for money "
            "movement or high-risk actions -- those are deeplink's job."
        ),
    },
    # These contracts intentionally exist before their real integrations. They
    # remain unavailable until an executor and validation contract are enabled.
    "api": {
        "operations": {"read_customer_data"},
        "enabled": True,
        "read_only": True,
        "description": (
            "Read one specific, verified piece of the customer's own banking "
            "data on file (e.g. their address, their loyalty tier). Use this "
            "for a request about the customer's actual personal data value, "
            "not a general policy or procedure question -- that is RAG's job."
        ),
    },
    "deeplink": {
        "operations": {"resolve_navigation"},
        "enabled": True,
        "read_only": True,
        "description": "Return a validated Horizon navigation destination. Use this when the customer needs to navigate to a specific screen in the app.",
    },
}


def enabled_tool_capabilities() -> list[dict[str, Any]]:
    """Return only capabilities safe to plan in the current deployment.

    service_request additionally exposes its registered forms (id,
    description, risk) so the planner can choose which one fits -- never the
    form's actual fields, which stay invisible to every LLM at every stage.
    """
    from backend.app.orchestration.service_request_registry import available_service_requests
    from backend.app.orchestration.deeplink_destination_registry import available_deeplink_destinations
    from backend.app.orchestration.api_field_registry import available_api_fields

    capabilities = []
    for name, contract in TOOL_REGISTRY.items():
        if not contract.get("enabled"):
            continue
        capability = {
            "tool_name": name,
            "operations": sorted(contract["operations"]),
            "description": contract["description"],
            "read_only": contract["read_only"],
        }
        if name == "service_request":
            capability["available_requests"] = available_service_requests()
        if name == "deeplink":
            capability["available_destinations"] = available_deeplink_destinations()
        if name == "api":
            capability["available_fields"] = available_api_fields()
        capabilities.append(capability)
    return capabilities


def is_registered_operation(tool_name: str, operation: str) -> bool:
    contract = TOOL_REGISTRY.get(str(tool_name))
    return bool(contract and contract.get("enabled") and operation in contract.get("operations", set()))
