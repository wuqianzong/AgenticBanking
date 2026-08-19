import json

# ==========================================
# CONFIGURATION
# ==========================================
# This reads the final output from your ETL pipeline
INPUT_FILE = "final_product_intent_map.json" 
OUTPUT_MAP_FILE = "product_intent_map.json"

def generate_product_intent_map():
    print(f"[*] Reading {INPUT_FILE} to generate exact Product-to-Intent map...")
    
    with open(INPUT_FILE, "r", encoding="utf-8") as f:
        corpus = json.load(f)
        
    # Dictionary to hold the mapping: { "Product": Set("Intent1", "Intent2") }
    product_map = {}
    
    for entry in corpus:
        # Extract the fields
        products = entry.get("product_tag", [])
        intents = entry.get("intent_families", [])
        
        # Guard clause in case of dirty data
        if not isinstance(products, list):
            products = [products]
            
        for product in products:
            if product not in product_map:
                product_map[product] = set()
                
            for intent in intents:
                product_map[product].add(intent)
                
    # Convert sets back to sorted lists for clean JSON serialization
    final_map = {
        product: sorted(list(intents)) 
        for product, intents in product_map.items()
    }
    
    # Sort the dictionary keys so "ALL" or specific products appear consistently
    final_map = dict(sorted(final_map.items()))
    
    with open(OUTPUT_MAP_FILE, "w", encoding="utf-8") as f:
        json.dump(final_map, f, indent=4, ensure_ascii=False)
        
    print("\n✅ [SUCCESS] Dynamic Product-to-Intent Map Generated!")
    print(f"Output saved to {OUTPUT_MAP_FILE}")
    print("\n--- Map Preview ---")
    print(json.dumps(final_map, indent=2))

if __name__ == "__main__":
    generate_product_intent_map()