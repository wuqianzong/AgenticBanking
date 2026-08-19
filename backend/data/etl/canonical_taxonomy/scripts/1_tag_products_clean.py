import json
import os
import time
from groq import Groq 

# ==========================================
# CONFIGURATION
# ==========================================
INPUT_FILE = "tokenized_horizon_faqs.json" 
OUTPUT_FILE = "tagged_horizon_faqs.json"

CONFIG = {
    "PRODUCT_LIST": [
        "Credit_Card", "Debit_Card", "Deposit_Account", "Personal_Loan", 
        "Stocks", "Mutual_Funds", "Insurance", "Horizon_APP", "Rewards_Program"
    ],
    "ALL_TAG": "ALL",
    
    "PRODUCT_DOMAINS": {
        "credit-card": ["Credit_Card"],
        "savings-account": ["Deposit_Account"],
        "trust-card": ["Credit_Card", "Debit_Card"],
        "supplementary-card": ["Credit_Card"],
        "instant-loan": ["Personal_Loan"],
        "balance-transfer": ["Credit_Card", "Personal_Loan"], 
        "mutual-funds": ["Mutual_Funds"],
        "stocks": ["Stocks"],
        "autoinvest-(stocks)": ["Stocks"],
        "insurance": ["Insurance"],
        "life-insurance": ["Insurance"],
        "pay-and-transfer": ["Deposit_Account", "Debit_Card"],
        "split-hub": ["Credit_Card"],
        "trust-plus": ["Deposit_Account"]
    }
}

client = Groq(api_key="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")

def load_existing_progress():
    if os.path.exists(OUTPUT_FILE):
        try:
            with open(OUTPUT_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except json.JSONDecodeError:
            return []
    return []

def get_llm_product_tag(orig_cat: str, intents: list, question: str, answer: str) -> list:
    """Uses LLM strictly to infer the product tag for functional categories."""
    prompt = f"""
    You are a strict banking data architect. Analyze this FAQ.
    Based on banking logic, which specific commercial products does this rule or process apply to?
    
    Authorized Product List: {CONFIG['PRODUCT_LIST']}
    
    Rules:
    1. Look for explicit product names in the text.
    2. Use banking reasoning to identify implicit links (e.g., 'cheque' = Deposit_Account).
    3. If it applies to specific products, output those products.
    4. If it is universally applicable to all banking customers (e.g., app login), output ["ALL"].
    
    Data to Analyze:
    - Original Category: {orig_cat}
    - Existing Intents: {intents}
    - Question: {question}
    - Answer: {answer}
    
    Respond ONLY with a valid JSON object containing a single key "product_tags". 
    Example 1: {{"product_tags": ["Credit_Card", "Debit_Card"]}}
    Example 2: {{"product_tags": ["ALL"]}}
    """
    
    try:
        response = client.chat.completions.create(
            model="llama-3.1-8b-instant", 
            messages=[{"role": "user", "content": prompt}],
            temperature=0.0,
            response_format={"type": "json_object"}
        )
        
        result = json.loads(response.choices[0].message.content)
        tags = result.get("product_tags", [])
        
        if isinstance(tags, list) and len(tags) > 0:
            return tags
        else:
            return [CONFIG["ALL_TAG"]]
            
    except Exception as e:
        print(f"[!] LLM Request Failed: {e}.")
        raise e 

def process_dataset():
    print(f"[*] Loading raw dataset from {INPUT_FILE}...")
    with open(INPUT_FILE, 'r', encoding='utf-8') as f:
        raw_data = json.load(f)
        
    tagged_data = load_existing_progress()
    start_idx = len(tagged_data)
    
    if start_idx >= len(raw_data):
        print("✅ Data tagging is already 100% complete.")
        return

    print(f"[*] Resuming from index {start_idx} out of {len(raw_data)}...")
    
    try:
        for i in range(start_idx, len(raw_data)):
            entry = raw_data[i]
            orig_cat = entry.get("original_category", "").lower()
            
            # 1. Product Tagging (Deterministic Fast-Path)
            if orig_cat in CONFIG["PRODUCT_DOMAINS"]:
                product_tags = CONFIG["PRODUCT_DOMAINS"][orig_cat]
                print(f"  -> [{i}/{len(raw_data)-1}] {orig_cat} | Tagged as: {product_tags} (Hardcoded)")
                
            # 1b. Product Tagging (LLM Inferential Path)
            else:
                intents = entry.get("intent_families", [])
                question = entry.get("synth_question", "")
                answer = entry.get("synth_answer", "")
                
                product_tags = get_llm_product_tag(
                    orig_cat=orig_cat,
                    intents=intents,
                    question=question,
                    answer=answer
                )
                
                # --- NEW VERIFICATION PRINT BLOCK ---
                print(f"\n{'='*70}")
                print(f"🤖 LLM INFERENCE REVIEW [{i}/{len(raw_data)-1}]")
                print(f"Category : {orig_cat}")
                print(f"Intents  : {intents}")
                print(f"Question : {question}")
                print(f"Answer   : {answer}")
                print(f"--> INFERRED TAGS: {product_tags}")
                print(f"{'='*70}\n")
                
            entry["product_tag"] = product_tags
            entry.pop("domain", None) 
            
            tagged_data.append(entry)
            
            if (i + 1) % 20 == 0:
                with open(OUTPUT_FILE, 'w', encoding='utf-8') as f:
                    json.dump(tagged_data, f, indent=4, ensure_ascii=False)
                print(f"  [💾 Checkpoint] Safe-saved at index {i}")

            if orig_cat not in CONFIG["PRODUCT_DOMAINS"]:
                time.sleep(1.2) 
                
    except (Exception, KeyboardInterrupt) as e:
        print(f"\n[⚠️] Process interrupted at index {len(tagged_data)}: {type(e).__name__}")
        print("[*] State Recovery: Saving current progress before shutting down...")
        
    finally:
        with open(OUTPUT_FILE, 'w', encoding='utf-8') as f:
            json.dump(tagged_data, f, indent=4, ensure_ascii=False)
        print(f"✅ State preserved. You have successfully tagged {len(tagged_data)} entries.")

if __name__ == "__main__":
    process_dataset()