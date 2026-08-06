import json
import os

# ==========================================
# 1. CONFIGURATION
# ==========================================

# The file containing your fully processed, tokenized data
TARGET_FILE = "tokenized_horizon_faqs.json"

# This is where you define your correction rules.
# Key   = The "original_category" you want to target (e.g., "trust-card")
# Value = The corrected "domain" you want to assign (e.g., "DEBIT_CARDS")
# You can add as many rules here as you need in the future.
CORRECTION_MAP = {
    "trust-card": "DEBIT_CARDS",
    # "another-category": "ANOTHER_DOMAIN"  <-- Example of extending it
}

# ==========================================
# 2. EXECUTION
# ==========================================

def apply_mapping_corrections():
    if not os.path.exists(TARGET_FILE):
        print(f"[!] Error: {TARGET_FILE} not found. Please ensure it is in the same directory.")
        return

    print(f"[*] Loading {TARGET_FILE}...")
    with open(TARGET_FILE, "r", encoding="utf-8") as f:
        corpus = json.load(f)

    print(f"[*] Applying deterministic taxonomy corrections...")
    
    correction_count = 0
    
    # Iterate through the corpus and apply rules
    for entry in corpus:
        original_cat = entry.get("original_category", "")
        current_domain = entry.get("domain", "")
        
        # Check if the original category is in our list of rules
        if original_cat in CORRECTION_MAP:
            new_domain = CORRECTION_MAP[original_cat]
            
            # Only update and count if it actually requires a change
            if current_domain != new_domain:
                entry["domain"] = new_domain
                correction_count += 1
                # Optional: Uncomment the next line to see every individual change
                # print(f"    - Fixed FAQ {entry['faq_id']}: {current_domain} -> {new_domain}")

    # Overwrite the file with the corrected data
    print(f"[*] Saving updated corpus back to {TARGET_FILE}...")
    with open(TARGET_FILE, "w", encoding="utf-8") as f:
        json.dump(corpus, f, indent=4, ensure_ascii=False)

    print("================================================================")
    print("✅ TAXONOMY CORRECTION COMPLETE")
    print("================================================================")
    print(f"Total Entries Scanned : {len(corpus)}")
    print(f"Total Domains Updated : {correction_count}")
    if correction_count > 0:
        print(f"The 'trust-card' entries have been successfully mapped to 'DEBIT_CARDS'.")
    else:
        print("No changes were needed. The data already matches your correction rules.")

if __name__ == "__main__":
    apply_mapping_corrections()