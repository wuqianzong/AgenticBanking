"""Build an FAQ-blind 80-message retrieval evaluation fixture.

Each scenario is specified only by a valid Product×Intent boundary and a
generic banking topic.  No FAQ identifier, source question, answer, or vector
content participates in query generation.  The generated fixture is intended
for blinded candidate-text review, not for modifying production retrieval.
"""

import json
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[3]
RESEARCH_ROOT = Path(__file__).resolve().parents[1]
OUTPUT = RESEARCH_ROOT / "fixtures" / "blind_query_strategy_eval_v1.json"


# topic, direct cleaned query, procedural angle, policy angle, conceptual angle
SCENARIOS = [
    ("Personal_Loan", "Application", "starting a loan request in the banking app", "personal loan application in Horizon App", "start personal loan application in app", "loan application process and required application information", "Horizon personal loan application"),
    ("Personal_Loan", "Eligibility", "whether someone qualifies for a personal loan", "personal loan eligibility age income residency", "check personal loan eligibility", "personal loan age income residency qualification rules", "Horizon personal loan eligibility"),
    ("Personal_Loan", "Interest And Rates", "understanding the cost of borrowing", "personal loan borrowing cost interest and rate calculation", "review personal loan borrowing cost", "personal loan interest rate and borrowing cost rules", "Horizon personal loan pricing"),
    ("Personal_Loan", "Loan And Credit Management", "managing a repayment schedule", "personal loan repayment schedule and payment management", "check personal loan repayment schedule", "loan repayment timing and management rules", "Horizon loan repayment"),
    ("Deposit_Account", "Interest And Rates", "when savings interest is paid", "deposit account interest credit timing", "check savings interest credit date", "deposit interest payout schedule and timing rules", "Horizon savings account interest"),
    ("Deposit_Account", "Payments And Transfers", "adding money to a savings account", "fund Horizon savings account", "add money to savings account", "savings account funding methods and transfer rules", "Horizon savings account funding"),
    ("Deposit_Account", "Account Management", "an account becoming dormant", "deposit account dormancy and reactivation", "check dormant savings account status", "account dormancy and reactivation rules", "Horizon savings account management"),
    ("Debit_Card", "Card Logistics", "activating a new debit card", "activate newly received Horizon debit card", "activate new debit card", "debit card activation and verification requirements", "Horizon debit card activation"),
    ("Debit_Card", "Card Logistics", "when a replacement card will arrive", "debit card delivery timeframe and tracking", "track debit card delivery", "debit card delivery timing and mailing rules", "Horizon debit card delivery"),
    ("Debit_Card", "Card Logistics", "replacing a lost debit card", "lost debit card reporting and replacement", "report lost debit card and request replacement", "lost card security and replacement rules", "Horizon debit card replacement"),
    ("Credit_Card", "Application", "applying for a credit card", "credit card application process", "start credit card application", "credit card application steps and required information", "Horizon credit card application"),
    ("Credit_Card", "Fees And Charges", "costs of a balance transfer", "credit card balance transfer fees and charges", "review balance transfer charges", "balance transfer fees and additional cost rules", "Horizon balance transfer pricing"),
    ("Credit_Card", "Transaction Disputes", "an unrecognised card transaction", "unrecognised credit card transaction dispute", "report unrecognised card transaction", "card transaction dispute investigation and refund rules", "Horizon credit card transaction dispute"),
    ("Horizon_APP", "Authentication And Security", "recovering access after forgetting an app password", "Horizon App password reset and account access", "reset Horizon App password", "app authentication password reset and security rules", "Horizon App account access"),
    ("Horizon_APP", "Features And Limits", "what the mobile banking app can do", "Horizon App features and available functions", "review Horizon App functions", "mobile banking app feature availability and limits", "Horizon mobile banking app"),
    ("Stocks", "Wealth And Insurance", "setting up recurring stock investments", "automatic stock investment setup", "set up recurring stock investment", "AutoInvest schedule funding and setup rules", "Horizon automated stock investing"),
    ("Stocks", "Fees And Charges", "fees for stock investing", "stock investment fees and charges", "review stock investment fees", "stock trading and investment charge rules", "Horizon stock investing costs"),
    ("Mutual_Funds", "Wealth And Insurance", "available mutual fund investments", "available Horizon mutual fund products", "view mutual fund products", "mutual fund availability and investment product rules", "Horizon mutual fund offerings"),
    ("Insurance", "Wealth And Insurance", "viewing life insurance policy details online", "digital access to life insurance policy details", "view life insurance policy details", "life insurance policy information access rules", "Horizon life insurance policy"),
    ("Rewards_Program", "Rewards And Promotions", "linking an existing rewards membership to a card", "link rewards membership to Horizon card", "link existing rewards membership", "rewards membership and card linking rules", "Horizon card rewards membership"),
]

STYLE_TEMPLATES = [
    "I am trying to sort out {topic}. What should I do?",
    "Before I make a decision, I need to understand {topic}. Can you explain?",
    "I have heard different things about {topic}, and I am not sure where to begin.",
    "This may be obvious, but I need help with {topic} without making a mistake.",
]


def main() -> None:
    cases = []
    for scenario_index, (product, intent, topic, direct, procedural, policy, conceptual) in enumerate(SCENARIOS, start=1):
        for style_index, template in enumerate(STYLE_TEMPLATES, start=1):
            cases.append({
                "id": f"blind_{scenario_index:02d}_{style_index}",
                "customer_query": template.format(topic=topic),
                "direct_query": direct,
                "scope_pairs": [{"product": product, "intents": [intent]}],
                "angle_groups": [{
                    "group_id": "base",
                    "procedural": procedural,
                    "policy": policy,
                    "conceptual": conceptual,
                }],
                "expected_parent_id": "__BLIND_REVIEW_NO_PRESELECTED_TARGET__",
            })
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(cases, indent=2), encoding="utf-8")
    print(f"Wrote {len(cases)} FAQ-blind query cases to {OUTPUT}")


if __name__ == "__main__":
    main()
