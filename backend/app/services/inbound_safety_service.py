"""Deterministic, database-maintained inbound safety checks.

This module deliberately performs no semantic inference.  It answers only
whether the raw input contains one of the centrally maintained sensitive
phrases.  Prompt injection remains the job of the downstream Prompt Guard.
"""

from __future__ import annotations

import re
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


# ---------------------------------------------------------------------------
# Credential disclosure (v5.1 R3 follow-up). A credential typed into the chat
# must be stopped here, before Prompt Guard or any other model call, because
# every later check is itself a model that would receive the value. Pattern
# rules only; a match returns opaque rule IDs and never the value.
# ---------------------------------------------------------------------------

_NUMBER_WORDS = r"(?:zero|oh|one|two|three|four|five|six|seven|eight|nine)"
# A short numeric secret: 3-8 digits, optionally spaced or dashed
# ("482913", "6 3 9 1 0 4", "7-7-3-2"), or three or more number words.
_DIGIT_VALUE = r"(?<![\d$])(?:\d[ \-]?){2,7}\d(?!\d|,\d|\s*(?:digits?|-digit|times?|days?|hours?|%|sgd|dollars?))"
_WORD_VALUE = rf"{_NUMBER_WORDS}(?:[\s\-]+{_NUMBER_WORDS}){{2,}}\b"
# Between the credential word and its value: connectors such as "is", ":",
# "=", "->", "es", "是", plus at most three short words ("the code I got is").
_BRIDGE = r"(?:\s*(?:[:=]|->|>|is|was|were|es|是|为|：)?\s*(?:[a-z']{1,6}\s+){0,3}?(?:is\s+|was\s+|:\s*|=\s*)?)"

_NUMERIC_SECRET_WORDS = {
    "pin_value": r"(?:\bpin\b|p-i-n|\bpin\s*code\b|密码|clave|\bnip\b)",
    "otp_value": r"(?:\botp\b|one[\s-]?time\s+(?:password|passcode|code|pin)|verification\s+code|security\s+code|sms\s+code|验证码|c[oó]digo\s+de\s+verificaci[oó]n)",
    "cvv_value": r"(?:\bcvv2?\b|\bcvc2?\b|\bcsc\b|card\s+security\s+code)",
    "passcode_value": r"(?:\bpasscode\b|\bapp\s+code\b)",
}
_PASSWORD_WORD = r"(?:\bpassword\b|\bpasswd\b|\bpwd\b|contraseña|登录密码|\blogin\b|\bsign[\s-]?in\b)"
# A password value: one token right after a connector, containing a digit
# or a symbol, or a "user / password" pair.
_PASSWORD_VALUE = r"\s*(?:is|=|:|-)\s*[\"']?(?=\S*[\d!@#$%^&*])\S{4,}"
_LOGIN_PAIR = r"\s*[:=]?\s*\S{3,}\s*/\s*\S{4,}"

_CARD_CANDIDATE = re.compile(r"(?<!\d)(?:\d[ \-]?){12,18}\d(?!\d)")


def _luhn_ok(digits: str) -> bool:
    total, alt = 0, False
    for ch in reversed(digits):
        d = int(ch)
        if alt:
            d *= 2
            if d > 9:
                d -= 9
        total += d
        alt = not alt
    return total % 10 == 0


def find_credential_rule_ids(user_input: str) -> list[str]:
    """Return rule IDs for credentials disclosed in *user_input* (PIN, OTP, CVV,
    passcode, password or login, or a card number that passes the Luhn check).

    A credential word alone ("my PIN is not working", "a 6-digit PIN",
    "I forgot my password") does not match; a value must follow it.
    """
    text = str(user_input or "").casefold()
    if not text:
        return []
    hits: list[str] = []
    for rule_id, word in _NUMERIC_SECRET_WORDS.items():
        if re.search(word + _BRIDGE + rf"(?:{_DIGIT_VALUE}|{_WORD_VALUE})", text):
            hits.append(rule_id)
    if re.search(_PASSWORD_WORD + rf"(?:{_PASSWORD_VALUE}|{_LOGIN_PAIR})", text):
        hits.append("password_value")
    for m in _CARD_CANDIDATE.finditer(text):
        digits = re.sub(r"\D", "", m.group())
        if 13 <= len(digits) <= 19 and _luhn_ok(digits):
            hits.append("card_number")
            break
    return hits


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
