import json
from collections import Counter

def list_unique_categories():
    input_file = "raw_trustbank_faqs.json"
    
    if not os_file_exists(input_file):
        print(f"[!] Error: {input_file} not found in the current directory.")
        return

    with open(input_file, "r", encoding="utf-8") as f:
        corpus = json.load(f)

    # Extract all original_category values
    categories = [entry.get("original_category", "uncategorized") for entry in corpus]
    category_counts = Counter(categories)

    print("================================================================")
    print("📋 UNIQUE RAW CATEGORIES DISCOVERED IN CORPUS")
    print("================================================================")
    print(f"Total Entries Scanned: {len(corpus)}\n")
    
    for category, count in category_counts.most_common():
        print(f" - '{category}': {count} entries")

    print("\n================================================================")
    print("💡 TIP FOR YOUR DOMAIN MAPPING DICTIONARY:")
    print("You can map these exact category strings keys to your Capstone Domains:")
    print('CATEGORY_TO_DOMAIN_MAP = {')
    for category, _ in category_counts.most_common():
        print(f'    "{category}": "YOUR_CAPSTONE_DOMAIN_HERE",')
    print('}')
    print("================================================================")

def os_file_exists(path):
    import os
    return os.path.exists(path)

if __name__ == "__main__":
    list_unique_categories()