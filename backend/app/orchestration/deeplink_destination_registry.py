from typing import Any

DEEPLINK_DESTINATION_REGISTRY: dict[str, dict[str, Any]] = {
    "card-management": {
        "route": "/app/cards/manage",
        "allowed_products": ["Credit_Card", "Debit_Card"],
        "allowed_intents": ["Card Logistics", "Authentication And Security", "Features And Limits"],
        "description": "View & Manage Card screen — freeze, replace, enable overseas usage.",
        "label": "Manage Card",
    },
    "account-statements": {
        "route": "/app/accounts/statements",
        "allowed_products": ["Deposit_Account"],
        "allowed_intents": ["Account Maintenance", "Disputes And Exceptions"],
        "description": "Statements screen — view and download past monthly statements.",
        "label": "View Statements",
    },
    "security-settings": {
        "route": "/app/profile/security",
        "allowed_products": ["Horizon_APP"],
        "allowed_intents": ["Authentication And Security"],
        "description": "Profile -> Security screen — update PIN, view Horizon Key status.",
        "label": "Security Settings",
    },
    "pay-credit-card-bill": {
        "route": "/app/cards/pay",
        "allowed_products": ["Credit_Card", "Deposit_Account"],
        "allowed_intents": ["Payments And Transfers", "Card Logistics"],
        "description": "Pay Credit Card Bill screen — pay outstanding balance from a savings account.",
        "label": "Pay Bill",
    },
}

def available_deeplink_destinations() -> list[dict[str, Any]]:
    return [
        {
            "destination_id": k,
            "description": v["description"],
            "route": v["route"],
            "label": v["label"],
        }
        for k, v in DEEPLINK_DESTINATION_REGISTRY.items()
    ]
