"""Mocked data sources behind the WP-1 `api` tool.

There is no real customer-data platform behind this MVP. These functions
stand in for "the actual specific read-only APIs working behind the central
gateway" -- what must stay real is the contract: one function per field,
taking a customer_id and returning that field's current value, so swapping
in a genuine backend integration later replaces only the function body, not
anything that calls it (see api_field_registry.py).
"""

from __future__ import annotations

_MOCK_CUSTOMER_ADDRESSES = {
    "USR_999": "12 Marina Boulevard, #08-01, Singapore 018982",
}
_DEFAULT_ADDRESS = "1 Raffles Place, Singapore 048616"


def fetch_customer_address(customer_id: str) -> str:
    return _MOCK_CUSTOMER_ADDRESSES.get(customer_id, _DEFAULT_ADDRESS)


_MOCK_LOYALTY_TIERS = {
    "USR_999": "Gold",
}
_DEFAULT_LOYALTY_TIER = "Silver"


def fetch_loyalty_tier(customer_id: str) -> str:
    return _MOCK_LOYALTY_TIERS.get(customer_id, _DEFAULT_LOYALTY_TIER)
