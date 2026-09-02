"""Deterministic, database-maintained inbound safety checks.

This module deliberately performs no semantic inference.  It answers only
whether the raw input contains one of the centrally maintained sensitive
phrases.  Prompt injection remains the job of the downstream Prompt Guard.
"""

from __future__ import annotations

import sqlite3

from backend.app.services.memory_service import DB_PATH, ensure_memory_schema


def find_sensitive_word_rule_ids(user_input: str) -> list[str]:
    """Return active sensitive-rule IDs matched by *user_input*.

    Rule text never needs to be returned to the graph or written to logs;
    opaque IDs preserve auditability without echoing the sensitive term.
    """
    normalized_input = str(user_input or "").casefold()
    if not normalized_input:
        return []

    with sqlite3.connect(DB_PATH) as conn:
        ensure_memory_schema(conn)
        rules = conn.execute(
            "SELECT rule_id, term FROM Inbound_Sensitive_Word WHERE is_active = 1"
        ).fetchall()

    return [
        str(rule_id)
        for rule_id, term in rules
        if str(term or "").casefold() in normalized_input
    ]
