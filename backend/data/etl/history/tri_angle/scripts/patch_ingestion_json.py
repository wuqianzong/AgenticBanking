import os
import json
import logging

# ==========================================
# CONFIGURATION & FILE PATHS
# ==========================================
BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../.."))
DATA_DIR = os.path.join(BASE_DIR, "backend/data")

SOURCE_FILE = os.path.join(DATA_DIR, "tokenized_horizon_faqs.json")
TARGET_FILE = os.path.join(DATA_DIR, "ready_for_ingestion.json")

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

def patch_ready_for_ingestion():
    print(f"\n{'━'*60}")
    print(" 🩹 INITIATING JSON HOTFIX: Repopulating Parent Context")
    print(f"{'━'*60}\n")

    # 1. Load the Source of Truth
    if not os.path.exists(SOURCE_FILE):
        logging.error(f"Source file not found: {SOURCE_FILE}")
        return
        
    with open(SOURCE_FILE, "r") as f:
        source_faqs = json.load(f)
        
    # Create a fast lookup dictionary: { "faq_id": {faq_data} }
    source_lookup = {faq["faq_id"]: faq for faq in source_faqs}
    logging.info(f"Loaded {len(source_lookup)} FAQs from source.")

    # 2. Load the Target Staging File
    if not os.path.exists(TARGET_FILE):
        logging.error(f"Target file not found: {TARGET_FILE}")
        return
        
    with open(TARGET_FILE, "r") as f:
        ingestion_data = json.load(f)

    parent_data = ingestion_data.get("parent_data", [])
    child_data = ingestion_data.get("child_data", [])
    
    if not parent_data:
        logging.error("No parent_data found in target JSON to patch.")
        return

    # 3. Execute the Patch
    patch_count = 0
    for parent in parent_data:
        parent_id = parent.get("parent_id")
        
        if parent_id in source_lookup:
            # Extract raw question and tokenized answer
            raw_q = source_lookup[parent_id].get("synth_question", "")
            tok_a = source_lookup[parent_id].get("tokenized_answer", "")
            
            # 🛠️ THE FIX: Rebuild the content string correctly
            parent["content"] = f"Question: {raw_q}\n\nAnswer: {tok_a}"
            patch_count += 1
        else:
            logging.warning(f"Could not find source data for {parent_id}")

    # 4. Save the Patched File
    logging.info(f"Patch complete! Writing updated data back to {TARGET_FILE}...")
    with open(TARGET_FILE, "w") as f:
        # We write back BOTH the patched parent data AND the untouched child data
        json.dump({
            "parent_data": parent_data,
            "child_data": child_data
        }, f, indent=2)

    print(f"\n{'━'*60}")
    print(f" ✅ HOTFIX SUCCESS: Successfully repopulated {patch_count} Parent payloads.")
    print(" You may now safely run db_ingestion.py")
    print(f"{'━'*60}\n")

if __name__ == "__main__":
    patch_ready_for_ingestion()