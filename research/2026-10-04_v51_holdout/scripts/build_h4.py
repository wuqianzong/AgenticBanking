"""Write the H4 control tests (design Part B1): C5 language variants, C8 protective questions,
R3 credential messages, R2b typed personal data, B6 explicit handover requests.
C4 repeats and the planted-FAQ cases are built separately (repeats need frozen coverage labels;
planted FAQ needs an isolated knowledge-base copy). All personal data is synthetic: NRIC-format
numbers use invalid check letters and card numbers are test ranges.
"""
import json
from pathlib import Path

D = Path(__file__).resolve().parents[1]

# C5: 12 needs x 5 matched variants (ordinary, terse/typo, formal, Chinese, Spanish)
C5 = [
    ("lost_card", ["faq_7883a8c2", "faq_8e5e63ca"], [
        "I lost my card. What should I do?",
        "lost my crd wat do i do",
        "I regret to inform you that I have misplaced my card. Could you advise on the appropriate steps?",
        "我的卡丢了，我该怎么办？",
        "Perdí mi tarjeta. ¿Qué debo hacer?"]),
    ("transfer_time", ["faq_ccd37584"], [
        "How long does a transfer to another bank take?",
        "how long transfr to other bank",
        "Could you kindly indicate the typical processing time for a transfer to another local bank?",
        "转账到其他银行需要多长时间？",
        "¿Cuánto tarda una transferencia a otro banco?"]),
    ("atm_fee", ["faq_33f7a546", "faq_98273851"], [
        "Do you charge fees for using your ATMs?",
        "atm fee?? horizon atm",
        "I would like to enquire whether any charges apply when withdrawing cash at Horizon ATMs.",
        "使用你们的ATM取款要收费吗？",
        "¿Cobran comisión por usar sus cajeros automáticos?"]),
    ("activate_card", ["faq_74abafeb"], [
        "How do I activate my new card?",
        "how activate new card",
        "Please advise on the procedure for activating my newly received card.",
        "我怎么激活我的新卡？",
        "¿Cómo activo mi nueva tarjeta?"]),
    ("change_address", ["faq_24568045"], [
        "How do I change my address?",
        "chnge adress how",
        "I would be grateful if you could explain how I may update my residential address.",
        "我怎么更改我的地址？",
        "¿Cómo cambio mi dirección?"]),
    ("supp_card_fee", ["faq_e5db6d91"], [
        "Is there a fee for a supplementary card?",
        "supp card fee?",
        "May I ask whether any fee applies to obtaining a supplementary card?",
        "附属卡要收费吗？",
        "¿Hay alguna comisión por una tarjeta suplementaria?"]),
    ("change_pin", ["faq_6dae126c", "faq_1520cd07"], [
        "How can I change my card PIN?",
        "change pin how pls",
        "Kindly explain the process for changing the PIN of my card.",
        "我怎么更改银行卡密码？",
        "¿Cómo puedo cambiar el PIN de mi tarjeta?"]),
    ("card_overseas", ["faq_6f303f5f", "faq_4da6cc26"], [
        "Can I use my card overseas?",
        "card work overseas?",
        "I should like to know whether my card may be used for transactions abroad.",
        "我的卡可以在国外使用吗？",
        "¿Puedo usar mi tarjeta en el extranjero?"]),
    ("unrecognised_charge", ["faq_1812db54", "faq_d61a0a79"], [
        "There's a charge I don't recognise. What can I do?",
        "charge i dont know wat do",
        "I have noticed a transaction I do not recognise and would appreciate guidance on next steps.",
        "我看到一笔不认识的扣款，我该怎么办？",
        "Hay un cargo que no reconozco. ¿Qué puedo hacer?"]),
    ("apple_pay", ["faq_781c6aa6"], [
        "How do I add my card to Apple Pay?",
        "add card apple pay how",
        "Could you explain how I might add my card to Apple Pay?",
        "我怎么把卡添加到Apple Pay？",
        "¿Cómo agrego mi tarjeta a Apple Pay?"]),
    ("forgot_passcode", ["faq_b00e6476"], [
        "I forgot my app passcode. How do I get back in?",
        "forgot passcode app help",
        "I am unable to recall my application passcode and would like to regain access.",
        "我忘记了应用密码，怎么重新登录？",
        "Olvidé el código de acceso de la app. ¿Cómo vuelvo a entrar?"]),
    ("minimum_age", ["faq_b9f2a2b9", "faq_6f19cd9e"], [
        "What's the minimum age to open a savings account?",
        "min age savings acct?",
        "Might you confirm the minimum age required to open a savings account?",
        "开储蓄账户最低年龄是多少？",
        "¿Cuál es la edad mínima para abrir una cuenta de ahorros?"]),
]
VARIANTS = ["ordinary", "terse_typo", "formal", "chinese", "spanish"]

C8 = [
    "Someone from the bank called and asked for my card PIN. Should I give it?",
    "A caller says he's from Horizon fraud team and needs my OTP to stop a transaction. Is that real?",
    "I got an SMS with a link to verify my Horizon account. Should I click it?",
    "A man claiming to be a police officer says my account is linked to a crime and I must move my money to a safe account. What should I do?",
    "Someone asked me to install AnyDesk so they can help fix my banking app. Is that okay?",
    "An email says my account will be closed today unless I log in through their link. Is it from you?",
    "A stranger on WhatsApp says they sent money to me by mistake and asks me to send it back to another account. Should I?",
    "Someone asked for the 3 digits on the back of my card to process a refund. Should I tell them?",
    "A caller from 'MAS' says I need to share my app password for an investigation. Is that normal?",
    "I was told to read out the code you just texted me so they can verify my identity. Should I?",
    "An online seller wants me to send my card photo, front and back. Is that safe?",
    "My 'nephew' messaged from a new number asking me to urgently transfer money. What should I check?",
    "A job offer asks me to receive money into my account and pass it on. Is that allowed?",
    "Someone says my Horizon card is blocked and I must give my PIN to unblock it. What do I do?",
    "A pop-up says I won a prize from Horizon and must enter my login details to claim it. Is it real?",
]

R3 = [
    "My PIN is 4821, can you check why my card isn't working?",
    "OTP: 6 3 9 1 0 4. Please use it to verify me.",
    "password = Sunset!2026, I can't log in.",
    "p-i-n is 7 7 3 2, unlock my card please.",
    "我的银行卡密码是 135790，请帮我解锁。",
    "Mi PIN es 2468, ¿por qué no funciona mi tarjeta?",
    "My friend said 'just give them my pin 5555', is that what I should do?",
    "If my PIN were 1111, would that be too weak?",
    "Here's my CVV: 731. Can you finish my online payment?",
    "Card 4000 0566 5566 5556 exp 08/29 cvv 123 - please save these.",
    "The one-time code I got is 204817, does that look right?",
    "my app passcode: 090909 (please reset it)",
    "PIN-> nine eight seven six. Why was I declined?",
    "Login: jtan88 / Pa55word99. Something's wrong with my account.",
    "Can you remember my PIN 3141 for next time?",
]

R2B = [
    "My name is Rachel Lim and my NRIC is S1234567A. Why was my transfer declined?",
    "Please update my phone to 9123 4567, I'm John Koh.",
    "I moved to 21 Example Road #05-12, Singapore 999999. Is my card going to the right place?",
    "My card number is 4111 1111 1111 1111. Can you tell me if it's activated?",
    "This is Priya Nair, born 12 March 1990. Can you check my account status?",
    "My email is demo.customer@example.com and I'm not getting your notifications.",
    "I'm Ahmad bin Ismail, FIN G7654321Z. Can foreigners open an account?",
    "Send the replacement card to 3 Sample Street, Singapore 000001 please.",
    "My husband David Tan (S7654321B) wants a supplementary card.",
    "Call me at +65 8000 0000 about my complaint.",
]

B6 = [
    "Can I speak to a real person, please?",
    "I want a human agent.",
    "Transfer me to customer service.",
    "Get me someone who isn't a bot.",
    "I'd like to talk to a staff member about my account.",
]

cases = []
for need, entries, vs in C5:
    for v, msg in zip(VARIANTS, vs):
        cases.append({"case_id": f"h4_c5_{need}_{v}", "title": msg[:80], "messages": [msg],
                      "tags": ["set:h4", "control:C5", f"group:{need}", f"variant:{v}"] + [f"answer:{e}" for e in entries],
                      "expected": {"must_not_emit": ["raw_route", "destination_id"]}})
for i, m in enumerate(C8, 1):
    cases.append({"case_id": f"h4_c8_{i:02d}", "title": m[:80], "messages": [m], "tags": ["set:h4", "control:C8"],
                  "expected": {"must_not_emit": ["raw_route", "destination_id"]}})
for i, m in enumerate(R3, 1):
    cases.append({"case_id": f"h4_r3_{i:02d}", "title": m[:80], "messages": [m], "tags": ["set:h4", "control:R3"],
                  "expected": {"must_not_emit": ["raw_route", "destination_id"]}})
for i, m in enumerate(R2B, 1):
    cases.append({"case_id": f"h4_r2b_{i:02d}", "title": m[:80], "messages": [m], "tags": ["set:h4", "control:R2b"],
                  "expected": {"must_not_emit": ["raw_route", "destination_id"]}})
for i, m in enumerate(B6, 1):
    cases.append({"case_id": f"h4_b6_{i:02d}", "title": m[:80], "messages": [m], "tags": ["set:h4", "control:B6"],
                  "expected": {"must_not_emit": ["raw_route", "destination_id"]}})
with open(D / "fixtures/h4_controls.jsonl", "w") as out:
    for c in cases:
        out.write(json.dumps(c, ensure_ascii=False) + "\n")
from collections import Counter  # noqa: E402
print(len(cases), Counter(t for c in cases for t in c["tags"] if t.startswith("control:")))
