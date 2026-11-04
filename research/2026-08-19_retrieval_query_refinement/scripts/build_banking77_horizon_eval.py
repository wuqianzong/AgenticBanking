"""Build a reproducible external-language retrieval fixture from BANKING77.

The public BANKING77 test split supplies banking-support phrasing which is
independent of the Horizon FAQ corpus.  It is not Horizon answer ground truth,
so this script deliberately selects only categories with a reviewed, defensible
mapping to a Horizon product/intent boundary and acceptable parent evidence.

It does not modify the production knowledge base or vector store.
"""

import csv
import json
import random
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[3]
RESEARCH_ROOT = Path(__file__).resolve().parents[1]
SOURCE = RESEARCH_ROOT / "data" / "banking77_test.csv"
OUTPUT = RESEARCH_ROOT / "fixtures" / "banking77_horizon_retrieval_eval_v1.json"
SAMPLE_PER_CATEGORY = 10
RANDOM_SEED = 20260819


# These mappings were reviewed against Horizon's current parent table.  The
# labels establish the user need; acceptable parent IDs establish the Horizon
# evidence that can answer it.  They are intentionally narrow and exclude
# BANKING77 categories with no safe Horizon equivalent (e.g. foreign transfers).
MAPPINGS = {
    "activate_my_card": {
        "scope_pairs": [{"product": "Debit_Card", "intents": ["Card Logistics"]}],
        "component": "activate a newly received card",
        "acceptable_parent_ids": ["faq_74abafeb"],
        "narratives": [
            "How to activate a newly received Horizon debit card",
            "Horizon debit card activation steps and verification requirements",
            "Enable a new Horizon debit card before first use",
        ],
        "faq_distilled_patterns": [
            "Activate a newly issued, replacement, or renewed Horizon card in the Horizon App before using it for transactions",
            "Open Horizon App account management, select the physical card, and choose Activate Your Card using NFC or a welcome-material code",
        ],
    },
    "change_pin": {
        "scope_pairs": [{"product": "Debit_Card", "intents": ["Card Logistics"]}],
        "component": "change a card PIN",
        "acceptable_parent_ids": ["faq_6dae126c"],
        "narratives": [
            "How to change the PIN for a Horizon debit card",
            "Horizon debit card PIN update process and security requirements",
            "Update the personal identification number for a Horizon debit card",
        ],
        "faq_distilled_patterns": [
            "Update a Horizon card PIN in the Horizon App through Card Details and Modify PIN",
            "Create a new card personal identification number after selecting Modify PIN in the app",
        ],
    },
    "card_arrival": {
        "scope_pairs": [{"product": "Debit_Card", "intents": ["Card Logistics", "General Inquiries"]}],
        "component": "card delivery or a card that has not arrived",
        "acceptable_parent_ids": ["faq_9d7a4b83", "faq_74392fe2"],
        "narratives": [
            "Horizon debit card delivery timing and tracking",
            "What to do when a newly applied Horizon debit card has not arrived",
            "Expected delivery of a Horizon debit card after application",
        ],
        "faq_distilled_patterns": [
            "Expected delivery timeframe for a physical Horizon card after an application",
            "A newly applied or replacement Horizon card has not arrived: contact support through Horizon App live chat or voice call",
        ],
    },
    "lost_or_stolen_card": {
        "scope_pairs": [{"product": "Debit_Card", "intents": ["Card Logistics", "Transaction Disputes"]}],
        "component": "report a lost or stolen card and protect the account",
        "acceptable_parent_ids": ["faq_7883a8c2", "faq_8e5e63ca"],
        "narratives": [
            "What to do when a Horizon debit card is lost or stolen",
            "Report a missing Horizon card and arrange protection or replacement",
            "Horizon debit card loss, security, and replacement guidance",
        ],
        "faq_distilled_patterns": [
            "A Horizon card is lost or stolen: freeze the card in the app and order a replacement",
            "Protect an account after a missing card by opening View and Manage Card, freezing it, and replacing the old card",
        ],
    },
    "transfer_timing": {
        "scope_pairs": [{"product": "Deposit_Account", "intents": ["Payments And Transfers"]}],
        "component": "timing of a domestic transfer",
        "acceptable_parent_ids": ["faq_ccd37584"],
        "narratives": [
            "Horizon domestic transfer processing time",
            "When a transfer sent from a Horizon savings account is received",
            "Timing and processing speed for Horizon local transfers",
        ],
        "faq_distilled_patterns": [
            "Processing time for a Horizon domestic transfer: Horizon sends it instantly and the recipient bank may need time to settle it",
            "When a recipient receives a local transfer after Horizon provides immediate confirmation",
        ],
    },
    "cancel_transfer": {
        "scope_pairs": [{"product": "Deposit_Account", "intents": ["Payments And Transfers"]}],
        "component": "cancel or revoke a submitted transfer",
        "acceptable_parent_ids": ["faq_ea430613"],
        "narratives": [
            "Can a submitted Horizon domestic transfer be cancelled",
            "Revoke or cancel a transfer after it has been approved",
            "Horizon transfer cancellation rules after submission",
        ],
        "faq_distilled_patterns": [
            "A Horizon domestic transfer cannot be withdrawn after it has been submitted and approved",
            "Check the beneficiary and amount before finalising a Horizon transfer because an approved transfer cannot be revoked",
        ],
    },
    "transfer_into_account": {
        "scope_pairs": [{"product": "Deposit_Account", "intents": ["Payments And Transfers"]}],
        "component": "fund a savings account by bank transfer",
        "acceptable_parent_ids": ["faq_7d49c937"],
        "narratives": [
            "Ways to add money to a Horizon savings account",
            "Fund a Horizon savings account through bank transfer",
            "Horizon savings account funding methods and transfer options",
        ],
        "faq_distilled_patterns": [
            "Fund a Horizon savings account through a FAST bank transfer by adding it as a local beneficiary at another bank",
            "Add money to a Horizon savings account using PayNow after registering the Horizon mobile number or NRIC/FIN",
        ],
    },
    "passcode_forgotten": {
        "scope_pairs": [{"product": "Horizon_APP", "intents": ["Authentication And Security"]}],
        "component": "regain access after forgetting an app passcode",
        "acceptable_parent_ids": ["faq_b00e6476"],
        "narratives": [
            "Regain access after forgetting a Horizon App passcode",
            "Horizon App passcode reset and account access steps",
            "What to do when a Horizon App passcode is forgotten",
        ],
        "faq_distilled_patterns": [
            "Regain Horizon App access after a forgotten passcode using the Forgot Passcode option",
            "Reset a forgotten Horizon App passcode and optionally enable facial or fingerprint authentication",
        ],
    },
}


def main() -> None:
    if not SOURCE.exists():
        raise FileNotFoundError(f"Missing BANKING77 test split: {SOURCE}")
    with SOURCE.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))

    cases = []
    rng = random.Random(RANDOM_SEED)
    for category, mapping in MAPPINGS.items():
        candidates = [row["text"].strip() for row in rows if row["category"] == category]
        if len(candidates) < SAMPLE_PER_CATEGORY:
            raise ValueError(f"{category} has only {len(candidates)} source messages")
        for index, customer_query in enumerate(rng.sample(candidates, SAMPLE_PER_CATEGORY), start=1):
            cases.append({
                "id": f"banking77_{category}_{index:02d}",
                "source": "PolyAI BANKING77 test split (CC BY 4.0)",
                "source_intent": category,
                # The external customer wording is the direct-anchor baseline.
                "customer_query": customer_query,
                "direct_query": customer_query,
                "expected_parent_id": mapping["acceptable_parent_ids"][0],
                "scope_pairs": mapping["scope_pairs"],
                "angle_groups": [{
                    "group_id": "faq_narrative_views",
                    "procedural": mapping["narratives"][0],
                    "policy": mapping["narratives"][1],
                    "conceptual": mapping["narratives"][2],
                }],
                "faq_distilled_patterns": mapping["faq_distilled_patterns"],
                "expected_components": [{
                    "name": mapping["component"],
                    "acceptable_parent_ids": mapping["acceptable_parent_ids"],
                }],
            })

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(cases, indent=2), encoding="utf-8")
    print(f"Wrote {len(cases)} BANKING77 evaluation cases to {OUTPUT}")


if __name__ == "__main__":
    main()
