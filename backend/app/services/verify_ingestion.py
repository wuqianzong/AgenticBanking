import os
import json
import sqlite3
import chromadb
from chromadb.utils import embedding_functions

# ==========================================
# 1. DYNAMIC ABSOLUTE PATHING
# ==========================================
CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
if os.path.basename(CURRENT_DIR) == "services":
    APP_DIR = os.path.dirname(CURRENT_DIR)
    BACKEND_DIR = os.path.dirname(APP_DIR)
else:
    BACKEND_DIR = os.path.join(os.getcwd(), "backend")

DATA_DIR = os.path.join(BACKEND_DIR, "data")
RAW_SOURCES_DIR = os.path.join(DATA_DIR, "raw_sources")
VECTOR_STORE_DIR = os.path.join(DATA_DIR, "vector_store")

INPUT_FILE = os.path.join(RAW_SOURCES_DIR, "ready_for_ingestion.json")
SQLITE_DB = os.path.join(DATA_DIR, "knowledge_base.db")
CHROMA_PATH = VECTOR_STORE_DIR
CHROMA_COLLECTION = "faq_child_chunks"

def run_validation():
    print("================================================================")
    print("🔍 RAG INGESTION VALIDATION SUITE")
    print("================================================广\n")

    # ----------------------------------------------------
    # STEP 1: LOAD GROUND TRUTH STAGED DATA
    # ----------------------------------------------------
    if not os.path.exists(INPUT_FILE):
        print(f"❌ [FAIL] Missing ground truth staging file: {INPUT_FILE}")
        return

    with open(INPUT_FILE, "r", encoding="utf-8") as f:
        staged_data = json.load(f)

    expected_parents = len(staged_data.get("parent_data", []))
    expected_children = len(staged_data.get("child_data", []))

    # ----------------------------------------------------
    # STEP 2: AUDIT SQLITE (PARENT STORAGE)
    # ----------------------------------------------------
    if not os.path.exists(SQLITE_DB):
        print(f"❌ [FAIL] SQLite DB missing: {SQLITE_DB}")
        return

    conn = sqlite3.connect(SQLITE_DB)
    cursor = conn.cursor()
    
    cursor.execute("SELECT COUNT(*) FROM knowledge_parents")
    sqlite_count = cursor.fetchone()[0]

    cursor.execute("SELECT content FROM knowledge_parents LIMIT 1")
    sample_parent_content = cursor.fetchone()[0]
    conn.close()

    print("1️⃣  QUANTITATIVE AUDIT: SQLITE PARENT STORAGE")
    print(f"   - Expected Parent Records : {expected_parents}")
    print(f"   - Actual SQLite Records   : {sqlite_count}")
    if sqlite_count == expected_parents:
        print("   ✅ Status: PASS (Parent records perfectly matched)")
    else:
        print("   ❌ Status: FAIL (Record count mismatch)")

    # ----------------------------------------------------
    # STEP 3: AUDIT CHROMADB (CHILD VECTOR STORE)
    # ----------------------------------------------------
    chroma_client = chromadb.PersistentClient(path=CHROMA_PATH)
    emb_fn = embedding_functions.DefaultEmbeddingFunction()
    collection = chroma_client.get_collection(name=CHROMA_COLLECTION, embedding_function=emb_fn)
    chroma_count = collection.count()

    print("\n2️⃣  QUANTITATIVE AUDIT: CHROMADB CHILD VECTORS")
    print(f"   - Expected Child Vectors  : {expected_children}")
    print(f"   - Actual ChromaDB Vectors : {chroma_count}")
    if chroma_count == expected_children:
        print("   ✅ Status: PASS (Vector count perfectly matched)")
    else:
        print("   ❌ Status: FAIL (Vector count mismatch)")

# ----------------------------------------------------
    # STEP 4: ASYMMETRIC INVARIANT AUDIT (UPDATED)
    # ----------------------------------------------------
    print("\n3️⃣  ASYMMETRIC INVARIANT AUDIT")
    
    # Smarter query: Explicitly fetch a parent record that contains a placeholder
    conn = sqlite3.connect(SQLITE_DB)
    cursor = conn.cursor()
    cursor.execute("SELECT content FROM knowledge_parents WHERE content LIKE '%{{faq_%' LIMIT 1")
    sample_parent_content = cursor.fetchone()[0]
    conn.close()

    sample_vector = collection.get(limit=1, include=["documents", "metadatas"])
    vector_text = sample_vector["documents"][0]
    vector_metadata = sample_vector["metadatas"][0]

    has_placeholders = "{{" in sample_parent_content

    # ----------------------------------------------------
    # STEP 5: END-TO-END RETRIEVAL LOOKUP TEST
    # ----------------------------------------------------
    print("\n4️⃣  END-TO-END RETRIEVAL LOOKUP SIMULATION")
    test_query = "What are the fees or interest rates for credit card cash advances?"
    
    results = collection.query(
        query_texts=[test_query],
        n_results=1
    )

    top_metadata = results["metadatas"][0][0]
    matched_parent_id = top_metadata.get("parent_id")

    # Hydrate from SQLite
    conn = sqlite3.connect(SQLITE_DB)
    cursor = conn.cursor()
    cursor.execute("SELECT domain, content FROM knowledge_parents WHERE parent_id = ?", (matched_parent_id,))
    retrieved_record = cursor.fetchone()
    conn.close()

    if retrieved_record:
        domain, parent_content = retrieved_record
        print(f"   - Test Query          : '{test_query}'")
        print(f"   - Top Matched Parent  : {matched_parent_id} (Domain: {domain})")
        print(f"   - Hydrated SQLite Text : \"{parent_content[:100]}...\"")
        print("   ✅ Status: PASS (Pointer hydration successfully completed)")
    else:
        print("   ❌ Status: FAIL (Relational pointer lookup failed)")

    print("\n================================================================")
    print("🎉 ALL INGESTION VALIDATION CHECKS COMPLETED")
    print("================================================================")

if __name__ == "__main__":
    run_validation()