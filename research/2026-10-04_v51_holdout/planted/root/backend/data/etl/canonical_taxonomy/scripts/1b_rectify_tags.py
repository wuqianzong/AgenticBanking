import json

# ==========================================
# CONFIGURATION
# ==========================================
INPUT_FILE = "tagged_horizon_faqs.json" 
OUTPUT_FILE = "rectified_horizon_faqs.json"
OUTPUT_MAP_FILE = "clean_product_intent_map.json"

# The Official System of Record for Products
AUTHORIZED_PRODUCTS = {
    "Credit_Card", "Debit_Card", "Deposit_Account", 
    "Personal_Loan", "Stocks", "Mutual_Funds", "Insurance", 
    "Horizon_APP", "Rewards_Program", "ALL"
}

# 1. Product Rectification Mapping
PRODUCT_FIXES = {
    "PayNow": "ALL",
    "Savings_Account": "Deposit_Account",
    "Rewards_program": "Rewards_Program", # Catch casing errors
    "Horizon_App": "Horizon_APP",
    "Horizon_app": "Horizon_APP",
    "Trust+": "Deposit_Account"
}

def clean_intent_string(raw_intent: str) -> str:
    """
    Normalizes chaotic LLM outputs:
    'application_process' -> 'Application Process'
    'card activation ' -> 'Card Activation'
    """
    clean = raw_intent.replace("_", " ").strip()
    return clean.title()

def run_rectification():
    print(f"[*] Loading raw tagged data from {INPUT_FILE}...")
    with open(INPUT_FILE, 'r', encoding='utf-8') as f:
        data = json.load(f)

    # Dictionary to build the clean map as we go
    final_map = {}

    print("[*] Executing Rectification and Deduplication...")
    
    for entry in data:
        raw_products = entry.get("product_tag", ["ALL"])
        raw_intents = entry.get("intent_families", [])

        # Fix 1: Rectify Products
        if not isinstance(raw_products, list):
            raw_products = [raw_products]
            
        clean_products = set()
        for p in raw_products:
            # Apply the fix if it exists in the dictionary, otherwise keep the string
            fixed_p = PRODUCT_FIXES.get(p, p)
            
            # If the LLM completely hallucinated a product not in our list, default to ALL
            if fixed_p not in AUTHORIZED_PRODUCTS:
                fixed_p = "ALL"
                
            clean_products.add(fixed_p)

        # Fix 2: Rectify and Deduplicate Intents
        clean_intents = set()
        for intent in raw_intents:
            standardized_intent = clean_intent_string(intent)
            clean_intents.add(standardized_intent)

        # Update the entry with clean data (converting sets back to lists)
        entry["product_tag"] = list(clean_products)
        entry["intent_families"] = list(clean_intents)

        # Build the final map for Node 02e
        for p in clean_products:
            if p not in final_map:
                final_map[p] = set()
            final_map[p].update(clean_intents)

    # Save the Cleaned Dataset (ready for chunking!)
    with open(OUTPUT_FILE, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=4, ensure_ascii=False)
        
    print(f"✅ Clean dataset saved to {OUTPUT_FILE}")

    # Format and save the Map for the Prompt
    prompt_map = {k: sorted(list(v)) for k, v in final_map.items()}
    prompt_map = dict(sorted(prompt_map.items())) # Sort alphabetically

    with open(OUTPUT_MAP_FILE, 'w', encoding='utf-8') as f:
        json.dump(prompt_map, f, indent=4, ensure_ascii=False)
        
    print(f"✅ Clean Intent Map saved to {OUTPUT_MAP_FILE} (Copy this to Node 02e!)")

if __name__ == "__main__":
    run_rectification()