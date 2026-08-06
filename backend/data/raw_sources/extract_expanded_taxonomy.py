import json
import os
from collections import defaultdict

# The file containing your fully processed, tokenized, and mapped data
INPUT_FILE = "tokenized_horizon_faqs.json"

def extract_taxonomy():
    if not os.path.exists(INPUT_FILE):
        print(f"[!] Error: {INPUT_FILE} not found. Please ensure it is in the same directory.")
        return

    print(f"[*] Loading {INPUT_FILE}...")
    with open(INPUT_FILE, "r", encoding="utf-8") as f:
        corpus = json.load(f)

    # Use a defaultdict of sets to automatically handle unique intents per domain
    expanded_taxonomy = defaultdict(set)
    total_unique_intents = 0

    print("[*] Extracting domains and dynamically generated intents...")

    for entry in corpus:
        domain = entry.get("domain", "UNKNOWN_DOMAIN")
        # Handle the new 'intent_families' array structure (v0.4 specification)
        intent_families = entry.get("intent_families", [])
        
        for intent in intent_families:
            # Clean up the intent string (strip whitespace, ensure standard casing if desired)
            clean_intent = intent.strip()
            if clean_intent:
                expanded_taxonomy[domain].add(clean_intent)

    # Convert sets to sorted lists for clean output
    final_taxonomy = {domain: sorted(list(intents)) for domain, intents in expanded_taxonomy.items()}

    print("\n================================================================")
    print("🌟 EXPANDED GLOBAL TAXONOMY DISCOVERED")
    print("================================================================\n")
    
    # Print the dictionary in a format that can be directly copy-pasted into your Orchestrator code
    print("GLOBAL_TAXONOMY = {")
    for domain in sorted(final_taxonomy.keys()):
        intents = final_taxonomy[domain]
        total_unique_intents += len(intents)
        # Format the intents array nicely
        formatted_intents = ", ".join([f'"{i}"' for i in intents])
        print(f'    "{domain}": [{formatted_intents}],')
    print("}")
    
    print("\n================================================================")
    print(f"Total Domains           : {len(final_taxonomy)}")
    print(f"Total Unique Intents    : {total_unique_intents}")
    print("================================================================")
    print("💡 TIP: Copy the GLOBAL_TAXONOMY dictionary above and paste it directly")
    print("into your Node 03a (Knowledge Blueprint Planner) system prompt configuration.")

if __name__ == "__main__":
    extract_taxonomy()