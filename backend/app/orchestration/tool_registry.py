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
    # These contracts intentionally exist before their real integrations. They
    # remain unavailable until an executor and validation contract are enabled.
    "api": {
        "operations": {"read_customer_data"},
        "enabled": False,
        "read_only": True,
        "description": "Read validated, customer-authorized live banking data.",
    },
    "deeplink": {
        "operations": {"resolve_navigation"},
        "enabled": False,
        "read_only": True,
        "description": "Return a validated Horizon navigation destination.",
    },
}


def enabled_tool_capabilities() -> list[dict[str, Any]]:
    """Return only capabilities safe to plan in the current deployment."""
    return [
        {
            "tool_name": name,
            "operations": sorted(contract["operations"]),
            "description": contract["description"],
            "read_only": contract["read_only"],
        }
        for name, contract in TOOL_REGISTRY.items()
        if contract.get("enabled")
    ]


def is_registered_operation(tool_name: str, operation: str) -> bool:
    contract = TOOL_REGISTRY.get(str(tool_name))
    return bool(contract and contract.get("enabled") and operation in contract.get("operations", set()))
