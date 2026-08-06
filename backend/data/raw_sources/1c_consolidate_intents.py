import json

# ==========================================
# CONFIGURATION
# ==========================================
# Input: The file outputted by the Product Tagging script
INPUT_FILE = "rectified_horizon_faqs.json" 
OUTPUT_FILE = "consolidated_horizon_faqs.json"
OUTPUT_MAP_FILE = "final_product_intent_map.json"

# The Master Verb/Action Mapping Dictionary
INTENT_MAPPING = {
    # 1. APPLICATION
    "Application": "Application", "Application Process": "Application", "Application Requirements": "Application", 
    "Application Status": "Application", "Application Timeline": "Application", "Card Application": "Application", 
    "Credit Card Application": "Application", "Loan Application": "Application", "Online Application": "Application", 
    "Partner Applications": "Application",
    
    # 2. ELIGIBILITY
    "Eligibility": "Eligibility", "Cashback Eligibility": "Eligibility", "Credit Card Eligibility": "Eligibility", 
    "Merchant Eligibility": "Eligibility", "Product Eligibility": "Eligibility", "Promotion Eligibility": "Eligibility", 
    "Spend Bonus Eligibility": "Eligibility", "Supplementary Card Eligibility": "Eligibility", "Account Eligibility": "Eligibility", 
    "Bonus Eligibility": "Eligibility", "Customer Classification": "Eligibility", "Existing Relationship": "Eligibility", 
    "Proof Of Income": "Eligibility",

    # 3. FEATURES AND LIMITS
    "Benefits": "Features And Limits", "Card Benefits": "Features And Limits", "Card Features": "Features And Limits", 
    "Credit Card Features": "Features And Limits", "Features": "Features And Limits", "Trust+ Features": "Features And Limits", 
    "Card Limits": "Features And Limits", "Credit Card Limits": "Features And Limits", "Credit Limit": "Features And Limits", 
    "Credit Limit Increase": "Features And Limits", "Credit Limits": "Features And Limits", "Limits": "Features And Limits", 
    "Spending Limit": "Features And Limits", "Transaction Limits": "Features And Limits", "Redemption Limits": "Features And Limits", 
    "Promotion Limits": "Features And Limits", "Card Types": "Features And Limits", "Digital Cards": "Features And Limits", 
    "Supplementary Card": "Features And Limits", "Ineligible Transactions": "Features And Limits", "Overseas Usage": "Features And Limits", 
    "International Use": "Features And Limits", "Card Usage": "Features And Limits", "Browser Compatibility": "Features And Limits", 
    "Card Compatibility": "Features And Limits",

    # 4. FEES AND CHARGES
    "Charges": "Fees And Charges", "Fee Waiver": "Fees And Charges", "Fees": "Fees And Charges", "Transfer Fees": "Fees And Charges",

    # 5. INTEREST AND RATES
    "Bonus Caps": "Interest And Rates", "Bonus Interest": "Interest And Rates", "Bonus Interest Timing": "Interest And Rates", 
    "Interest": "Interest And Rates", "Interest Bonuses": "Interest And Rates", "Interest Payments": "Interest And Rates", 
    "Interest Rates": "Interest And Rates", "Interest Timing": "Interest And Rates", "Promotional Rates": "Interest And Rates",

    # 6. ACCOUNT MANAGEMENT
    "Account Cancellation": "Account Management", "Account Changes": "Account Management", "Account Issues": "Account Management", 
    "Account Linking": "Account Management", "Account Maintenance": "Account Management", "Account Management": "Account Management", 
    "Account Settings": "Account Management", "Account Status": "Account Management", "Account Updates": "Account Management", 
    "Card Controls": "Account Management", "Card Management": "Account Management", "Card Naming": "Account Management", 
    "Card Pairing": "Account Management", "Card Pending": "Account Management", "Card Updates": "Account Management", 
    "Dormancy": "Account Management", "Restrictions": "Account Management", "Unlocking Funds": "Account Management", 
    "Bank Details": "Account Management", "Payment Details": "Account Management", "Preferences": "Account Management", 
    "Membership Changes": "Account Management", "Membership Updates": "Account Management", "Account Details": "Account Management", 
    "Account Number Retrieval": "Account Management", "Card Details Access": "Account Management",

    # 7. PROFILE UPDATES
    "Address Update": "Profile Updates", "Profile Update": "Profile Updates", "Request Modification": "Profile Updates",

    # 8. CARD LOGISTICS
    "Activation": "Card Logistics", "App Activation": "Card Logistics", "Card Activation": "Card Logistics", 
    "Card Delivery": "Card Logistics", "Card Loss": "Card Logistics", "Card Replacement": "Card Logistics", 
    "Card Retention": "Card Logistics", "Pin Management": "Card Logistics", "Pin Reset": "Card Logistics",

    # 9. TRANSACTION DISPUTES
    "Decline": "Transaction Disputes", "Dispute": "Transaction Disputes", "Refund Impact": "Transaction Disputes", 
    "Statement Queries": "Transaction Disputes", "Atm Dispute": "Transaction Disputes", "Transfer Errors": "Transaction Disputes", 
    "Transfer Mistake": "Transaction Disputes", "Cash Discrepancy": "Transaction Disputes", "Transaction Failure": "Transaction Disputes",

    # 10. AUTHENTICATION AND SECURITY
    "3Dsecure": "Authentication And Security", "Account Access": "Authentication And Security", "App Access": "Authentication And Security", 
    "Login": "Authentication And Security", "Login Issue": "Authentication And Security", "Account Security": "Authentication And Security", 
    "App Security": "Authentication And Security", "Card Security": "Authentication And Security", "Data Security": "Authentication And Security", 
    "Privacy": "Authentication And Security", "Scam/Fraud": "Authentication And Security", "Security": "Authentication And Security", 
    "Security Feature": "Authentication And Security", "Security Key": "Authentication And Security", "Security Key Activation": "Authentication And Security", 
    "Security Key Setup": "Authentication And Security", "Security Measure": "Authentication And Security", "Security Pin": "Authentication And Security", 
    "Security Token": "Authentication And Security", "Transaction Security": "Authentication And Security", "Email Verification": "Authentication And Security", 
    "Myinfo Accuracy": "Authentication And Security", "On-Site Verification": "Authentication And Security", "Recipient Verification": "Authentication And Security", 
    "Transaction Signing": "Authentication And Security", "Device Registration": "Authentication And Security", "Device Loss": "Authentication And Security", 
    "Password Reset": "Authentication And Security", "Otp Validity": "Authentication And Security",

    # 11. PAYMENTS AND TRANSFERS
    "Atm Transactions": "Payments And Transfers", "Atm Withdrawal": "Payments And Transfers", "Autopay": "Payments And Transfers", 
    "Autopay Setup": "Payments And Transfers", "Bill Pay": "Payments And Transfers", "Bill Payments": "Payments And Transfers", 
    "Cancel Transfer": "Payments And Transfers", "Cheque Deposit": "Payments And Transfers", "Deposit Methods": "Payments And Transfers", 
    "Digital Payments": "Payments And Transfers", "Digital Wallets": "Payments And Transfers", "Direct Debit": "Payments And Transfers", 
    "Egiro Support": "Payments And Transfers", "Fast Transfer": "Payments And Transfers", "Fund Transfer": "Payments And Transfers", 
    "International Transfer": "Payments And Transfers", "Payment Management": "Payments And Transfers", "Payment Methods": "Payments And Transfers", 
    "Payment Processing": "Payments And Transfers", "Payment Scheduling": "Payments And Transfers", "Paynow": "Payments And Transfers", 
    "Paynow Changes": "Payments And Transfers", "Paynow Payment": "Payments And Transfers", "Paynow Registration": "Payments And Transfers", 
    "Paynow Settings": "Payments And Transfers", "Paynow Update": "Payments And Transfers", "Qr Payments": "Payments And Transfers", 
    "Recurring Payments": "Payments And Transfers", "Recurring Transfer": "Payments And Transfers", "Transfer": "Payments And Transfers", 
    "Transit Transactions": "Payments And Transfers", "Salary Credits": "Payments And Transfers", "Savings Pot": "Payments And Transfers", 
    "Transaction Block": "Payments And Transfers", "Transaction Control": "Payments And Transfers", "Transaction Impact": "Payments And Transfers", 
    "E-Payment Protection": "Payments And Transfers",

    # 12. REWARDS AND PROMOTIONS
    "Cashback": "Rewards And Promotions", "Coupons": "Rewards And Promotions", "Earn": "Rewards And Promotions", 
    "Fuel Discounts": "Rewards And Promotions", "Linkpoints": "Rewards And Promotions", "Merchant Promos": "Rewards And Promotions", 
    "Promotional Delivery": "Rewards And Promotions", "Promotional Offers": "Rewards And Promotions", "Promotions": "Rewards And Promotions", 
    "Rebate": "Rewards And Promotions", "Redemption": "Rewards And Promotions", "Referral": "Rewards And Promotions", 
    "Referral Bonus": "Rewards And Promotions", "Referral Code": "Rewards And Promotions", "Referral Codes": "Rewards And Promotions", 
    "Referral Program": "Rewards And Promotions", "Referral Rewards": "Rewards And Promotions", "Reward Claim": "Rewards And Promotions", 
    "Reward Notification": "Rewards And Promotions", "Reward Redemption": "Rewards And Promotions", "Reward Transferability": "Rewards And Promotions", 
    "Rewards": "Rewards And Promotions", "Rewards Availability": "Rewards And Promotions", "Rewards Determination": "Rewards And Promotions", 
    "Rewards Tracking": "Rewards And Promotions", "Rewards Transfer": "Rewards And Promotions", "Transaction Rewards": "Rewards And Promotions",

    # 13. LOAN AND CREDIT MANAGEMENT
    "Balance Transfer": "Loan And Credit Management", "Credit Card Instalments": "Loan And Credit Management", "Visa Instalments": "Loan And Credit Management", 
    "Loan Options": "Loan And Credit Management", "Repayment": "Loan And Credit Management", "Repayment Issue": "Loan And Credit Management", 
    "Repayment Terms": "Loan And Credit Management", "Payment Terms": "Loan And Credit Management", "Credit Reports": "Loan And Credit Management",

    # 14. BRANCH AND ATM LOCATOR
    "Atm Branding": "Branch And ATM Locator", "Atm Locations": "Branch And ATM Locator",

    # 15. WEALTH AND INSURANCE
    "Autoinvest": "Wealth And Insurance", "Capital Protection": "Wealth And Insurance", "Currency Availability": "Wealth And Insurance", 
    "Funding Sources": "Wealth And Insurance", "Investment Advice": "Wealth And Insurance", "Investment Insurance": "Wealth And Insurance", 
    "Investment Risks": "Wealth And Insurance", "Investments": "Wealth And Insurance", "Mutual Funds": "Wealth And Insurance", "Risk": "Wealth And Insurance", 
    "Stocks": "Wealth And Insurance", "Trading Hours": "Wealth And Insurance", "Claims": "Wealth And Insurance", "Home Insurance": "Wealth And Insurance", 
    "Insurance": "Wealth And Insurance", "Insurance Offers": "Wealth And Insurance", "Life Insurance": "Wealth And Insurance", 
    "Motorcycle Insurance": "Wealth And Insurance", "Pet Insurance": "Wealth And Insurance", "Premiums": "Wealth And Insurance", 
    "Travel Insurance": "Wealth And Insurance",

    # 16. GENERAL INQUIRIES
    "General Inquiries": "General Inquiries", "Support Queries": "General Inquiries", "Technical": "General Inquiries", 
    "Technical Issues": "General Inquiries", "Technical Support": "General Inquiries", "App Issues": "General Inquiries", 
    "App Navigation": "General Inquiries", "Card Information": "General Inquiries", "Card Queries": "General Inquiries", 
    "Credit Card Information": "General Inquiries", "Credit Card": "General Inquiries", "Credit Cards": "General Inquiries", 
    "Regulations": "General Inquiries", "Regulatory Compliance": "General Inquiries", "Notifications": "General Inquiries", "Transaction Alerts": "General Inquiries"
}

def consolidate_intents():
    print(f"[*] Loading raw tagged data from {INPUT_FILE}...")
    with open(INPUT_FILE, 'r', encoding='utf-8') as f:
        data = json.load(f)

    # Dictionary to build the ultra-clean Node 02e prompt map
    final_prompt_map = {}

    print("[*] Executing Verb/Intent Consolidation...")
    
    for entry in data:
        raw_intents = entry.get("intent_families", [])
        products = entry.get("product_tag", ["ALL"])
        
        consolidated_intents = set()
        
        # 1. Map old intents to the new Master Intents
        for intent in raw_intents:
            # We title case it just to be safe before looking it up
            clean_key = intent.replace("_", " ").strip().title()
            
            # If it's in our mapping, translate it. If not, default to General Inquiries.
            master_intent = INTENT_MAPPING.get(clean_key, "General Inquiries")
            consolidated_intents.add(master_intent)
            
        # 2. Update the JSON entry directly (leaves the rest of the object untouched)
        entry["intent_families"] = list(consolidated_intents)
        
        # 3. Build the mapping for the Orchestration Engineer
        for p in products:
            if p not in final_prompt_map:
                final_prompt_map[p] = set()
            final_prompt_map[p].update(consolidated_intents)

    # Save the finalized, mathematically deduplicated dataset
    with open(OUTPUT_FILE, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=4, ensure_ascii=False)
        
    print(f"✅ Consolidated dataset saved to {OUTPUT_FILE}")

    # Format and save the Map for Node 02e
    clean_map = {k: sorted(list(v)) for k, v in final_prompt_map.items()}
    clean_map = dict(sorted(clean_map.items()))

    with open(OUTPUT_MAP_FILE, 'w', encoding='utf-8') as f:
        json.dump(clean_map, f, indent=4, ensure_ascii=False)
        
    print(f"✅ Ultra-Clean Prompt Map saved to {OUTPUT_MAP_FILE}")

if __name__ == "__main__":
    consolidate_intents()