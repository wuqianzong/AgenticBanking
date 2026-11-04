"""Write the 15 H2b journey scripts (design Part B1, Part H) and their answer keys.

Customer messages are fixed (the runner does not adapt them). Each turn records the need,
the expected route(s) and, for FAQ needs, the answering entries confirmed by Labeller B.
"""
import json
from pathlib import Path

D = Path(__file__).resolve().parents[1]

J = [
    ("h2b_01", "Balance, then transfer", ["US-01", "US-03", "US-12"], [
        ("What's my current balance?", "own balance", ["own_data_lookup:balance"], []),
        ("I want to send $150 to my brother.", "start a transfer", ["app_screen:Send Money"], []),
        ("Yes, go ahead.", "confirm the transfer", ["app_screen:Send Money"], [])]),
    ("h2b_02", "Address check, then change", ["US-02", "US-07"], [
        ("What address do you have for me?", "own address", ["own_data_lookup:address"], []),
        ("That's my old place. I need to change it to my new address.", "change address", ["service_form:change_address", "app_screen:Update Profile"], []),
        ("Thanks.", "closing", ["none"], [])]),
    ("h2b_03", "Lost card", ["US-05"], [
        ("I can't find my card anywhere.", "lost card steps", ["faq_answer", "app_screen:Manage Card"], ["faq_7883a8c2", "faq_8e5e63ca"]),
        ("Will it cost me anything to get a new one?", "replacement fee", ["faq_answer"], ["faq_8336a2c7"]),
        ("How long will the new card take to arrive?", "delivery time", ["faq_answer"], ["faq_5ad80c63"])]),
    ("h2b_04", "New card set-up", ["US-06"], [
        ("My new card just came in the mail. How do I start using it?", "activate card", ["faq_answer"], ["faq_74abafeb"]),
        ("And how do I set a PIN for it?", "set PIN", ["faq_answer", "app_screen:Security Settings"], ["faq_6dae126c", "faq_1520cd07"]),
        ("Can I add it to Apple Pay too?", "add to Apple Pay", ["faq_answer"], ["faq_781c6aa6"])]),
    ("h2b_05", "Transfer rules", ["US-04"], [
        ("How much can I transfer in a day with PayNow?", "PayNow limits", ["faq_answer"], ["faq_6f4ef9e6"]),
        ("How long does a transfer to another local bank take?", "transfer timing", ["faq_answer"], ["faq_ccd37584"]),
        ("Can I cancel it if I send it to the wrong person?", "wrong recipient", ["faq_answer"], ["faq_ea430613", "faq_6eed6806"])]),
    ("h2b_06", "ATM fees", ["US-09"], [
        ("Are there fees if I use a Horizon ATM?", "ATM fees", ["faq_answer"], ["faq_33f7a546", "faq_98273851"]),
        ("What about withdrawing cash overseas?", "overseas withdrawal fees", ["faq_answer"], ["faq_31e7da42", "faq_4010241c"]),
        ("Which account should I pick at the ATM?", "account selection", ["faq_answer"], ["faq_090880d1", "faq_c0c33407"])]),
    ("h2b_07", "Supplementary card", ["US-08", "US-09"], [
        ("I'd like to get a card for my husband on my account.", "supplementary card", ["faq_answer"], ["faq_8ce1bac1", "faq_01a375c6"]),
        ("Is there a fee for that?", "supplementary card fee", ["faq_answer"], ["faq_e5db6d91"]),
        ("Can I set a spending limit on his card?", "supplementary limit", ["faq_answer"], ["faq_03c613a9", "faq_aeb90c91"])]),
    ("h2b_08", "Multi-part question", ["US-11", "US-12"], [
        ("What's my balance, and what's the minimum age to open a savings account for my son?", "own balance + minimum age", ["own_data_lookup:balance", "faq_answer"], ["faq_b9f2a2b9"]),
        ("He's 15. So he can't open one yet?", "follow-up on age", ["faq_answer"], ["faq_b9f2a2b9"])]),
    ("h2b_09", "Loyalty tier", ["US-02"], [
        ("What loyalty tier am I on?", "own loyalty tier", ["own_data_lookup:loyalty_tier"], []),
        ("How do I check how many Linkpoints I have?", "check Linkpoints", ["faq_answer"], ["faq_1b7fc742"])]),
    ("h2b_10", "Damaged card replacement", ["US-08"], [
        ("My card is cracked and the chip doesn't work anymore.", "damaged card", ["faq_answer", "service_form:replace_card"], ["faq_33f045af"]),
        ("Yes, I'd like to replace it.", "replace card", ["service_form:replace_card", "app_screen:Manage Card"], ["faq_33f045af"]),
        ("Will my old card stop working?", "old card status", ["faq_answer"], ["faq_83fb94a7"])]),
    ("h2b_11", "Scam call, then disclosed OTP", ["US-18"], [
        ("Someone called saying they're from Horizon and asked for my OTP. Should I give it to them?", "protective warning", ["faq_answer"], ["faq_bfe32bf8", "faq_c77746cd"]),
        ("I already gave it to them. What should I do now?", "fraud response", ["faq_answer", "human_handover"], ["faq_bfe32bf8", "faq_14914805"])]),
    ("h2b_12", "Unrecognised charge", ["US-09", "US-23"], [
        ("There's a charge on my card I don't recognise.", "unrecognised charge", ["faq_answer"], ["faq_1812db54"]),
        ("How do I dispute it?", "dispute", ["faq_answer"], ["faq_d61a0a79"]),
        ("Should I freeze my card in the meantime?", "freeze card", ["faq_answer", "app_screen:Manage Card"], ["faq_7883a8c2"])]),
    ("h2b_13", "Phone number change", ["US-07"], [
        ("I changed my phone number. How do I update it?", "update phone number", ["faq_answer", "app_screen:Update Profile"], ["faq_824abbaf", "faq_24568045"]),
        ("Will my PayNow still work with the old number?", "PayNow after number change", ["faq_answer"], ["faq_ce650cb6"])]),
    ("h2b_14", "No information, then a person", ["US-10", "US-15", "US-25"], [
        ("Where is my refund? The merchant said they sent it a week ago.", "refund status", ["faq_answer", "human_handover"], ["faq_0fe245c8"]),
        ("It's still not there. Can I talk to a person?", "explicit handover", ["human_handover"], [])]),
    ("h2b_15", "Travel follow-ups", ["US-06", "US-12"], [
        ("Can I use my card when I travel to Japan?", "card use abroad", ["faq_answer"], ["faq_6f303f5f"]),
        ("Do I need to turn anything on first?", "enable overseas use", ["faq_answer", "app_screen:Manage Card"], ["faq_4da6cc26", "faq_ba38dfaa"]),
        ("And will I be charged extra for purchases there?", "foreign fees", ["faq_answer"], ["faq_1b3f3507", "faq_f26ab8a2"])]),
]

with open(D / "fixtures/h2b_journeys.jsonl", "w") as fx, open(D / "fixtures/h2b_keys.jsonl", "w") as keys:
    for cid, title, stories, turns in J:
        fx.write(json.dumps({"case_id": cid, "title": title, "messages": [t[0] for t in turns],
                             "tags": ["set:h2b", "journey"] + [f"story:{s}" for s in stories],
                             "expected": {"must_not_emit": ["raw_route", "destination_id"]}}, ensure_ascii=False) + "\n")
        keys.write(json.dumps({"case_id": cid, "turns": [{"message": m, "need": n, "acceptable_routes": r, "answer_entries": a}
                                                          for m, n, r, a in turns]}, ensure_ascii=False) + "\n")
print(len(J), "journeys,", sum(len(t[3]) for t in J), "turns")
