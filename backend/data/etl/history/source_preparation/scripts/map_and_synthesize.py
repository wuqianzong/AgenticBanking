import json
import os
import time
from groq import Groq

# ==========================================
# 1. CONFIGURATION & TAXONOMY
# ==========================================
os.environ["GROQ_API_KEY"] = "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"

INPUT_FILE = "raw_trustbank_faqs.json"
OUTPUT_FILE = "mapped_trustbank_faqs.json"
PROGRESS_FILE = "mapping_progress.txt"

# Expanded Capstone Taxonomy
GLOBAL_TAXONOMY = {
    "ACCOUNT_OPENING": ["Eligibility", "documents", "application status"],
    "DEPOSIT_ACCOUNTS": ["Features", "fees", "restrictions", "dormancy"],
    "DEBIT_CARDS": ["Activation", "decline", "lost card", "PIN"],
    "CREDIT_CARDS": ["Fees", "statement queries", "decline", "dispute"],
    "DIGITAL_BANKING": ["Password reset", "login issue", "OTP issue", "scam/fraud"],
    "STATEMENTS": ["Download", "missing statement", "transaction explanation"],
    "LOANS": ["Features", "documents", "application status", "repayment issue"],
    "BRANCHES": ["Branch locator", "ATM locator", "service availability"],
    "PROFILE": ["Contact update", "address update", "status"],
    # -- NEWLY ADDED DOMAINS --
    "INVESTMENTS": ["Stocks", "Mutual Funds", "AutoInvest", "Fees"],
    "INSURANCE": ["Life Insurance", "Travel Insurance", "Claims", "Premiums"],
    "REWARDS_AND_OFFERS": ["Linkpoints", "Coupons", "Merchant Promos", "Redemption"],
    "TRANSACTIONS": ["PayNow", "FAST transfer", "Limits", "International Transfer"],
    "GENERAL_BANKING": ["Trust+ features", "General inquiries", "App navigation"]
}

# Exhaustive Mapping of Scraped Categories to Domains
CATEGORY_TO_DOMAIN_MAP = {
    "credit-card": "CREDIT_CARDS",
    "trust-card": "DEBIT_CARDS",
    "supplementary-card": "CREDIT_CARDS",
    "savings-account": "DEPOSIT_ACCOUNTS",
    "security": "DIGITAL_BANKING",
    "pay-and-transfer": "TRANSACTIONS",
    "general": "GENERAL_BANKING",
    "trust-plus": "DEPOSIT_ACCOUNTS",
    "mutual-funds": "INVESTMENTS",
    "stocks": "INVESTMENTS",
    "autoinvest-(stocks)": "INVESTMENTS",
    "merchant-offers": "REWARDS_AND_OFFERS",
    "linkpoints": "REWARDS_AND_OFFERS",
    "promotions": "REWARDS_AND_OFFERS",
    "split-hub": "CREDIT_CARDS",
    "instant-loan": "LOANS",
    "balance-transfer": "LOANS",
    "insurance": "INSURANCE",
    "life-insurance": "INSURANCE"
}

# ==========================================
# 2. STATE RECOVERY 
# ==========================================
def get_start_index():
    if os.path.exists(PROGRESS_FILE):
        with open(PROGRESS_FILE, "r") as f:
            content = f.read().strip()
            if content.isdigit():
                return int(content)
    return 0

def save_progress(index):
    with open(PROGRESS_FILE, "w") as f:
        f.write(str(index))

def load_existing_mapped_data():
    if os.path.exists(OUTPUT_FILE):
        with open(OUTPUT_FILE, "r", encoding="utf-8") as f:
            try:
                return json.load(f)
            except json.JSONDecodeError:
                return []
    return []

# ==========================================
# 3. LLM CLASSIFIER & SYNTHESIZER
# ==========================================

def classify_and_synthesize(client, question, answer, domain):
    suggested_intents = GLOBAL_TAXONOMY.get(domain, ["General"])
    
    system_prompt = f"""You are a Senior FinTech Data Architect and Copywriter.
    Your task is twofold:
    1. Classify the FAQ into 1 to 3 intent families.
    2. Rewrite and paraphrase the FAQ to create completely original, professionally worded synthetic data for 'Horizon Bank'.
    
    DOMAIN CONTEXT: '{domain}'
    SUGGESTED INTENTS: {suggested_intents}
    
    RULES FOR INTENT CLASSIFICATION:
    - You MUST output an array of 1 to 3 string tags.
    - You MAY use the SUGGESTED INTENTS above.
    - IF the suggested intents do not fit perfectly, generate your own highly accurate 1-3 word intent string (e.g., "ATM Limits", "Joint Account").
    
    RULES FOR SYNTHESIS (PARAPHRASING & REBRANDING):
    - CRITICAL REWRITE: You must completely paraphrase the question and answer. Change the sentence structure, tone, and vocabulary so it is an original composition. Do NOT just do a word-swap.
    - REBRAND: Replace ANY mention of "Trust Bank", "Trust App", "Trust card", or "Trust" with "Horizon Bank", "Horizon App", "Horizon card", or "Horizon".
    - PRESERVE FACTS: Do NOT alter the actual financial numbers, timeframes (e.g., "6-7 working days"), percentages, or core banking rules.
    - PRESERVE FORMAT: If the original answer has a step-by-step list, keep it as a bulleted list, but rewrite the text of the steps professionally.
    
    OUTPUT JSON FORMAT:
    {{
        "intent_families": ["intent_1", "intent_2"],
        "synth_question": "The newly paraphrased and rebranded question",
        "synth_answer": "The newly paraphrased and rebranded answer"
    }}
    """
    
    user_prompt = f"Original Question: {question}\nOriginal Answer: {answer}"

    response = client.chat.completions.create(
        model="llama-3.3-70b-versatile",
        #model="openai/gpt-oss-120b",
        
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt}
        ],
        response_format={"type": "json_object"},
        temperature=0.3 # Increased slightly to allow the LLM more creative freedom to paraphrase
    )
    
    return json.loads(response.choices[0].message.content)

# ==========================================
# 4. BATCH EXECUTION PIPELINE
# ==========================================
def run_mapping_pipeline(custom_start=None, custom_end=None):
    client = Groq()
    
    with open(INPUT_FILE, "r", encoding="utf-8") as f:
        raw_corpus = json.load(f)
        
    mapped_corpus = load_existing_mapped_data()
    
    start_idx = custom_start if custom_start is not None else get_start_index()
    end_idx = custom_end if custom_end is not None else len(raw_corpus)
    
    print(f"[*] Starting Synthesis Pipeline from index {start_idx} to {end_idx-1}...")

    i = start_idx
    try:
        for i in range(start_idx, end_idx):
            entry = raw_corpus[i]
            orig_cat = entry.get("original_category", "").lower()
            
            # 1. Deterministic Domain Pre-Mapping
            domain = CATEGORY_TO_DOMAIN_MAP.get(orig_cat, "GENERAL_BANKING")
            entry["domain"] = domain
            
            print(f"  -> [{i}/{len(raw_corpus)-1}] Synthesizing Domain: {domain}...")
            
            # 2. Dynamic Intent Generation & Synthesis
            payload = classify_and_synthesize(client, entry["question"], entry["answer"], domain)
            
            # 3. Store Results (Preserving Raw Data)
            entry["intent_families"] = payload.get("intent_families", ["OTHER"])
            entry["synth_question"] = payload.get("synth_question", entry["question"])
            entry["synth_answer"] = payload.get("synth_answer", entry["answer"])
            
            entry.pop("intent_family",None)

            mapped_corpus.append(entry)
            
            # 4. Batch Checkpointing
            if (i + 1) % 10 == 0:
                save_progress(i + 1)
                with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
                    json.dump(mapped_corpus, f, indent=4, ensure_ascii=False)
                print(f"  [+] Checkpoint Saved at entry {i + 1}")
            
            time.sleep(1.5) # Protect Groq free-tier rate limits
            
    except (Exception, KeyboardInterrupt) as e:
        print(f"\n[!] PIPELINE HALTED AT INDEX {i} DUE TO: {type(e).__name__}")
        save_progress(i)
        with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
            json.dump(mapped_corpus, f, indent=4, ensure_ascii=False)
        print(f"[+] Successfully saved {len(mapped_corpus)} entries to {OUTPUT_FILE}.")
        return

    # Final save
    save_progress(end_idx)
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(mapped_corpus, f, indent=4, ensure_ascii=False)
        
    print(f"\n[SUCCESS] Pipeline complete! {len(mapped_corpus)} entries completely synthesized.")

if __name__ == "__main__":
    run_mapping_pipeline()