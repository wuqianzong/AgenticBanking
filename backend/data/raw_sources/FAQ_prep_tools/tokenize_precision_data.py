import json
import re
import sqlite3
import os

INPUT_FILE = "mapped_trustbank_faqs.json"
OUTPUT_FILE = "tokenized_horizon_faqs.json"
DB_FILE = "knowledge_base.db"

# Regex Pattern to catch Currencies, Percentages, and standard numbers
NUMBER_PATTERN = re.compile(r'(?:S\$|\$|SGD\s*)?\d+(?:,\d{3})*(?:\.\d+)?(?:%)?')

def tokenize_and_store():
    print(f"[*] Initializing SQLite Database at {DB_FILE}...")
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS Precision_Reference_Table (
            placeholder TEXT PRIMARY KEY,
            exact_value TEXT NOT NULL,
            domain TEXT NOT NULL
        )
    ''')
    cursor.execute('DELETE FROM Precision_Reference_Table')

    if not os.path.exists(INPUT_FILE):
        print(f"[!] Error: {INPUT_FILE} not found.")
        return
        
    with open(INPUT_FILE, "r", encoding="utf-8") as f:
        corpus = json.load(f)

    tokenized_corpus = []
    db_records = []
    total_placeholders = 0

    print("[*] Sweeping corpus for numeric values (Asymmetric Strategy)...")

    for entry in corpus:
        faq_id = entry.get("faq_id", "unknown_id")
        domain = entry.get("domain", "GENERAL_BANKING")
        synth_answer = entry.get("synth_answer", "")
        
        counter = 1
        
        def replacer(match):
            nonlocal counter
            nonlocal total_placeholders
            
            original_value = match.group(0)
            placeholder_name = f"{faq_id}_{counter:03d}"
            placeholder_tag = f"{{{{{placeholder_name}}}}}" 
            
            db_records.append((placeholder_name, original_value, domain))
            
            counter += 1
            total_placeholders += 1
            
            return placeholder_tag

        # Create the masked version
        tokenized_answer = NUMBER_PATTERN.sub(replacer, synth_answer)
        
        # ASYMMETRIC UPDATE: 
        # Keep synth_answer untouched for ChromaDB embeddings.
        # Add tokenized_answer for SQLite Parent storage.
        entry["tokenized_answer"] = tokenized_answer
        
        tokenized_corpus.append(entry)

    print("[*] Committing placeholders to Precision_Reference_Table...")
    cursor.executemany('''
        INSERT INTO Precision_Reference_Table (placeholder, exact_value, domain)
        VALUES (?, ?, ?)
    ''', db_records)
    conn.commit()
    conn.close()

    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(tokenized_corpus, f, indent=4, ensure_ascii=False)

    print("\n================================================================")
    print("✅ ASYMMETRIC TOKENIZATION COMPLETE")
    print("================================================================")
    print(f"Total FAQs Processed : {len(tokenized_corpus)}")
    print(f"Total Numbers Masked : {total_placeholders}")
    print(f"Tokenized JSON Saved : {OUTPUT_FILE}")
    print(f"SQLite DB Ready      : {DB_FILE}")

if __name__ == "__main__":
    tokenize_and_store()