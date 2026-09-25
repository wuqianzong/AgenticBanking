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
        if _matches(str(term or "").casefold(), normalized_input)
    ]


# A disclosure phrase ending in "is" ("my pin is", "my password is") only
# discloses when a value follows it. When the next word describes the
# credential's state instead -- "my PIN is not working", "my password is
# wrong", "my OTP is expired" -- it is an ordinary support question and must
# reach the assistant (BUG-046). Other terms keep the plain phrase match.
_STATUS_WORDS = {
    "not", "wrong", "incorrect", "invalid", "blocked", "locked", "expired",
    "working", "forgotten", "lost", "stolen", "compromised", "leaked",
    "disabled", "suspended", "reset", "changed", "correct", "the", "a",
    "still", "no", "too", "being", "also", "missing", "unknown", "stuck",
}


def _matches(term: str, text: str) -> bool:
    if not term:
        return False
    if not term.endswith(" is"):
        return term in text
    start = 0
    while True:
        i = text.find(term, start)
        if i < 0:
            return False
        rest = text[i + len(term):].lstrip(" :=-\"'")
        next_word = rest.split()[0].strip(".,!?;:\"'") if rest.split() else ""
        if next_word and next_word not in _STATUS_WORDS and not next_word.endswith("n't"):
            return True
        start = i + 1
