from typing import Any

from backend.app.services.api_field_mock_data import (
    fetch_account_balance,
    fetch_customer_address,
    fetch_loyalty_tier,
)

# `data_classification` is a compliance decision, not an engineering guess --
# it should be reviewed and owned by whoever is accountable for compliance in
# a real deployment before a new field is added here. "Not identity-exposing"
# (disclosure_mode == "direct_value") is an engineering split for choosing
# placeholder vs. direct-value handling; it is not the same statement as "not
# personal data" -- a loyalty tier tied to a specific logged-in customer is
# still personal data under most PDPA-style definitions, just lower
# sensitivity than an address.
API_FIELD_REGISTRY: dict[str, dict[str, Any]] = {
    "profile-address": {
        "disclosure_mode": "placeholder",  # identity-exposing
        "data_classification": "identity_exposing",
        "description": "The customer's current postal address on file.",
        "fetch": fetch_customer_address,
    },
    "loyalty-status": {
        "disclosure_mode": "direct_value",  # not identity-exposing on its own
        "data_classification": "non_identity_low_sensitivity",
        "description": "The customer's current loyalty program tier.",
        "fetch": fetch_loyalty_tier,
        "fact_template": lambda value: f"The customer's current loyalty tier is {value}.",
    },
    "account-balance": {
        "disclosure_mode": "placeholder",  # sensitive financial data
        "data_classification": "financial_sensitive",
        "description": "The customer's current account balance.",
        "fetch": fetch_account_balance,
    },
}


def available_api_fields() -> list[dict[str, Any]]:
    return [
        {
            "field_id": field_id,
            "description": field["description"],
            "disclosure_mode": field["disclosure_mode"],
        }
        for field_id, field in API_FIELD_REGISTRY.items()
    ]
