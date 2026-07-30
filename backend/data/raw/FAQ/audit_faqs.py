import json
from collections import Counter

def audit_corpus():
    with open("raw_trustbank_faqs.json", "r", encoding="utf-8") as f:
        corpus = json.load(f)

    total_entries = len(corpus)
    print(f"=== 📊 RAG CORPUS HEALTH REPORT ===")
    print(f"Total Entries: {total_entries}\n")

    # 1. Category Breakdown (Completeness Check)
    categories = [entry.get("original_category", "unknown") for entry in corpus]
    category_counts = Counter(categories)
    print("--- 📂 Entries by Category ---")
    for cat, count in category_counts.most_common():
        print(f" - {cat}: {count} entries")
        if count == 0:
            print(f"   [!] WARNING: {cat} is empty!")

    # 2. Duplication Check
    questions = [entry.get("question", "").strip() for entry in corpus]
    duplicate_qs = [q for q, count in Counter(questions).items() if count > 1]
    print(f"\n--- 👯 Duplication Check ---")
    if duplicate_qs:
        print(f"[!] Found {len(duplicate_qs)} duplicate questions (Banks often put the same FAQ in multiple categories).")
        # Print the first 3 duplicates as an example
        for q in duplicate_qs[:3]:
            print(f"    - '{q}'")
    else:
        print("[+] 0 Duplicate questions found. Clean!")

    # 3. Data Quality (Anomalies & HTML leakage)
    print(f"\n--- ⚠️ Data Quality Anomalies ---")
    short_answers = 0
    html_leaks = 0

    for entry in corpus:
        ans = entry.get("answer", "")
        # Flag unusually short answers
        if len(ans) < 15:
            short_answers += 1
            print(f"    [!] Short Answer Alert (ID: {entry['faq_id']}): {ans}")
        
        # Flag missed HTML tags
        if "<div" in ans or "<span" in ans or "<p>" in ans:
            html_leaks += 1

    if short_answers == 0:
        print("[+] 0 abnormally short answers.")
    if html_leaks == 0:
        print("[+] 0 HTML tag leaks detected. Text is clean.")
    else:
        print(f"[!] Found {html_leaks} entries with raw HTML tags.")

    print("\n=== AUDIT COMPLETE ===")

if __name__ == "__main__":
    audit_corpus()