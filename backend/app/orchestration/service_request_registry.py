"""Registered form contracts for the service_request tool (WP-7).

Mirrors deeplink_destination_registry.py's allow-list discipline: the model
only ever selects a service_request_id; risk level, whether a step-up
credential check is required, and the form's exact shape all come from this
registry, never from generation. See docs/SERVICE_REQUEST_DESIGN_v1.0.md for
the full design.
"""

from __future__ import annotations

from typing import Any


SERVICE_REQUEST_REGISTRY: dict[str, dict[str, Any]] = {
    "profile-address-update": {
        "risk_level": "low",
        "requires_step_up": False,
        "allowed_products": ["Horizon_APP"],
        "allowed_intents": ["Profile Updates"],
        "action_label": "Change Address",
        "description": "Submit a new postal address for the customer's profile.",
        "form_schema": {
            "steps": [
                {
                    "step_number": 1,
                    "caption": "New Address",
                    "fields": [
                        {"field_id": "line1", "label": "Address Line 1", "field_type": "text", "default": "", "notice": ""},
                        {"field_id": "line2", "label": "Address Line 2 (optional)", "field_type": "text", "default": "", "notice": ""},
                        {"field_id": "city", "label": "City", "field_type": "text", "default": "", "notice": ""},
                        {"field_id": "postal_code", "label": "Postal Code", "field_type": "text", "default": "", "notice": ""},
                        {"field_id": "country", "label": "Country", "field_type": "options", "options": ["SG", "MY", "US", "Other"], "default": "SG", "notice": ""},
                    ],
                },
                {
                    "step_number": 2,
                    "caption": "Review and Confirm",
                    "fields": [
                        {"field_id": "confirm", "label": "I confirm this address is correct", "field_type": "checkbox", "default": False, "notice": "Review the details above before confirming."},
                    ],
                },
            ]
        },
    },
    "card-replacement-request": {
        "risk_level": "low",
        "requires_step_up": False,
        "allowed_products": ["Credit_Card", "Debit_Card"],
        "allowed_intents": ["Card Logistics"],
        "action_label": "Replace Card",
        "description": "Request a replacement physical card for a non-fraud reason (worn or damaged).",
        "form_schema": {
            "steps": [
                {
                    "step_number": 1,
                    "caption": "Select Card",
                    "fields": [
                        {"field_id": "card_id", "label": "Which card needs replacing?", "field_type": "options", "options": ["Primary Debit Card", "Primary Credit Card"], "default": "", "notice": ""},
                    ],
                },
                {
                    "step_number": 2,
                    "caption": "Reason",
                    "fields": [
                        {"field_id": "reason", "label": "Reason for replacement", "field_type": "options", "options": ["worn", "damaged", "other"], "default": "", "notice": "For a lost or stolen card, please contact us immediately instead of using this form."},
                    ],
                },
                {
                    "step_number": 3,
                    "caption": "Confirm Mailing Address",
                    "fields": [
                        {"field_id": "confirm_address", "label": "Send my replacement card to my address on file", "field_type": "checkbox", "default": False, "notice": ""},
                    ],
                },
            ]
        },
    },
    "kyc-document-resubmission": {
        "risk_level": "intermediate",
        "requires_step_up": True,
        "allowed_products": ["Horizon_APP"],
        "allowed_intents": ["Authentication And Security"],
        "action_label": "Resubmit KYC Document",
        "description": "Resubmit an identity document for an existing customer's KYC record.",
        "form_schema": {
            "steps": [
                {
                    "step_number": 1,
                    "caption": "Document Selection",
                    "fields": [
                        {"field_id": "document_type", "label": "Document type", "field_type": "options", "options": ["NRIC", "Passport", "Employment Pass", "Other"], "default": "", "notice": ""},
                        {"field_id": "document_number", "label": "Document number", "field_type": "text", "default": "", "notice": ""},
                    ],
                },
                {
                    "step_number": 2,
                    "caption": "Document Upload",
                    "fields": [
                        {"field_id": "document_file", "label": "Upload document", "field_type": "document_reference", "default": "", "notice": "This is a demonstration environment: no real file is uploaded or stored."},
                        {"field_id": "expiry_date", "label": "Document expiry date", "field_type": "text", "default": "", "notice": ""},
                    ],
                },
                {
                    "step_number": 3,
                    "caption": "Confirm Personal Details",
                    "fields": [
                        {"field_id": "details_unchanged", "label": "My name and date of birth on file are still accurate", "field_type": "checkbox", "default": False, "notice": "If your address has also changed, please submit that separately."},
                    ],
                },
                {
                    "step_number": 4,
                    "caption": "Review and Submit",
                    "fields": [
                        {"field_id": "acknowledge", "label": "I confirm the information provided is accurate", "field_type": "checkbox", "default": False, "notice": ""},
                    ],
                },
            ]
        },
    },
}


def available_service_requests() -> list[dict[str, Any]]:
    """Compact listing for the tool-calling planner prompt: id, description, risk only.

    Never exposes the full form_schema to the planning LLM -- it only needs
    enough to choose which registered request fits the customer's need.
    """
    return [
        {
            "service_request_id": service_request_id,
            "description": contract["description"],
            "risk_level": contract["risk_level"],
        }
        for service_request_id, contract in SERVICE_REQUEST_REGISTRY.items()
    ]


def get_service_request_contract(service_request_id: str) -> dict[str, Any] | None:
    return SERVICE_REQUEST_REGISTRY.get(str(service_request_id))
