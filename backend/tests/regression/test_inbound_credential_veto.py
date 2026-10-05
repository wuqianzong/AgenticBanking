"""Credential disclosure veto at the deterministic ingest gate (v5.1 R3 follow-up).

A PIN, OTP, CVV, passcode, password or card number typed into the chat must be stopped before Prompt Guard or any
other model call. A credential word without a value is an ordinary question and must pass.
"""
import sys

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[3]))
import backend.app.orchestration.nodes as N  # noqa: E402
from backend.app.core.translations import get_message  # noqa: E402
from backend.app.services.inbound_safety_service import find_credential_rule_ids as F  # noqa: E402

N.log_event = lambda *a, **k: None
checks = []


def check(name, good):
    checks.append(good)
    print(("PASS " if good else "FAIL ") + name)


BLOCK = [
    ("My PIN is 4821, can you check why my card isn't working?", "pin_value"),
    ("OTP: 6 3 9 1 0 4. Please use it to verify me.", "otp_value"),
    ("password = Sunset!2026, I can't log in.", "password_value"),
    ("p-i-n is 7 7 3 2, unlock my card please.", "pin_value"),
    ("我的银行卡密码是 135790，请帮我解锁。", "pin_value"),
    ("Mi PIN es 2468, ¿por qué no funciona mi tarjeta?", "pin_value"),
    ("My friend said 'just give them my pin 5555', is that what I should do?", "pin_value"),
    ("If my PIN were 1111, would that be too weak?", "pin_value"),
    ("Here's my CVV: 731. Can you finish my online payment?", "cvv_value"),
    ("Card 4000 0566 5566 5556 exp 08/29 cvv 123 - please save these.", "card_number"),
    ("The one-time code I got is 204817, does that look right?", "otp_value"),
    ("my app passcode: 090909 (please reset it)", "passcode_value"),
    ("PIN-> nine eight seven six. Why was I declined?", "pin_value"),
    ("Login: jtan88 / Pa55word99. Something's wrong with my account.", "password_value"),
    ("My card number is 4111 1111 1111 1111. Can you tell me if it's activated?", "card_number"),
]
PASS = [
    "My PIN is not working.",
    "Is my PIN 6 digits?",
    "How do I set a 6-digit PIN?",
    "I entered the wrong PIN 3 times and I'm locked out.",
    "I forgot my password.",
    "Someone from the bank called and asked for my card PIN. Should I give it?",
    "Someone asked for the 3 digits on the back of my card to process a refund.",
    "My OTP didn't arrive.",
    "What is my daily transfer limit? I want to send S$5,000.",
    "Transfer S$500 from my account to account 123-456789-0 now.",
    "Call me at +65 8000 0000 about my complaint.",
    "My postal code is 018982, is my card going there?",
    "My NRIC is S1234567A. Why was my transfer declined?",
    "I was charged 1,250 dollars twice.",
]
for msg, rule in BLOCK:
    check(f"blocks: {msg[:50]}", rule in F(msg))
for msg in PASS:
    check(f"passes: {msg[:50]}", F(msg) == [])

# The gate itself: a credential never reaches a model, and the customer gets the specific message.
calls = []
N.call_groq_lpu = lambda *a, **k: calls.append(a) or "{}"
out = N.node_01_deterministic_ingest_gate({**N._fresh_volatile_state_reset(), "user_input": BLOCK[0][0], "chat_history": [],
                                           "user_profile": {"language": "English"}})
check("gate returns error_credential", out.get("preflight_status") == "error_credential")
check("no model call made at the gate", calls == [])
msg = N.node_10_standard_rejection({**out, "user_profile": {"language": "English"}}).get("final_response", "")
check("customer sees the credential message", msg == get_message("English", "error_credential"))
check("message does not echo the credential", "4821" not in msg)
check("credential removed from the stored history", all("4821" not in str(m.content) for m in out.get("chat_history", [])))
ok = N.node_01_deterministic_ingest_gate({**N._fresh_volatile_state_reset(), "user_input": PASS[0], "chat_history": [],
                                          "user_profile": {"language": "English"}})
check("ordinary credential question passes the gate", ok.get("preflight_status") == "pass")

print(f"\n{sum(checks)}/{len(checks)} checks passed")
sys.exit(0 if all(checks) else 1)
