"""Mocked downstream processing for service_request submissions (WP-7).

There is no real KYC process, card production line, or address-change
system behind this MVP. These functions stand in for "the actual specific
APIs working behind the central gateway" the design doc describes. What
must stay real is the *contract*: one function per service_request_id,
taking the submitted form data and returning a small result dict that the
special-route response builder treats as verified evidence -- so swapping
in a genuine backend integration later replaces only the function body, not
anything that calls it.

Every result deliberately stops at "received / submitted", never "approved"
or "verified" -- see docs/SERVICE_REQUEST_DESIGN_v1.0.md Section 11 for why.
"""

from __future__ import annotations

import secrets
from typing import Any


def _reference(prefix: str) -> str:
    return f"{prefix}-{secrets.token_hex(3).upper()}"


def process_profile_address_update(submitted_data: dict[str, Any]) -> dict[str, Any]:
    parts = [
        submitted_data.get("line1", ""),
        submitted_data.get("line2", ""),
        submitted_data.get("city", ""),
        submitted_data.get("postal_code", ""),
        submitted_data.get("country", ""),
    ]
    new_address = ", ".join(part for part in parts if part)
    return {"new_address": new_address}


def process_card_replacement(submitted_data: dict[str, Any]) -> dict[str, Any]:
    return {"reference": _reference("CARD")}


def process_kyc_resubmission(submitted_data: dict[str, Any]) -> dict[str, Any]:
    return {"reference": _reference("KYC"), "business_days": 3}


_DOWNSTREAM_HANDLERS = {
    "profile-address-update": process_profile_address_update,
    "card-replacement-request": process_card_replacement,
    "kyc-document-resubmission": process_kyc_resubmission,
}


def run_downstream_service(service_request_id: str, submitted_data: dict[str, Any]) -> dict[str, Any]:
    handler = _DOWNSTREAM_HANDLERS.get(service_request_id)
    if handler is None:
        return {}
    return handler(submitted_data or {})


def run_mock_credential_check(passed: bool) -> bool:
    """Stand-in for the bank's centralized credential check.

    The real check would return a pass/fail signal from a re-authentication
    challenge (biometric, PIN re-entry) the frontend already invoked; this
    MVP has the frontend supply the demo outcome directly instead of
    building that challenge. The interface -- a single pass/fail boolean --
    is what a real integration would also return, so this function is a
    drop-in point, not a shape that needs to change later.
    """
    return bool(passed)
