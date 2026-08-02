import json
import sqlite3
import chromadb
from chromadb.utils import embedding_functions
import os

# ==========================================
# 1. DYNAMIC ABSOLUTE PATHING (Enterprise Safe)
# ==========================================
# This calculates the exact absolute path to the "backend" folder, 
# assuming this script lives in backend/app/services/db_ingestion.py
CURRENT_DIR = os.path.dirname(os.path.abspath(__file__)) # /services
APP_DIR = os.path.dirname(CURRENT_DIR)                   # /app
BACKEND_DIR = os.path.dirname(APP_DIR)                   # /backend

# Define exact structure targets based on your architecture
DATA_DIR = os.path.join(BACKEND_DIR, "data")
RAW_SOURCES_DIR = os.path.join(DATA_DIR, "raw_sources")
VECTOR_STORE_DIR = os.path.join(DATA_DIR, "vector_store")

# Final File Paths
INPUT_FILE = os.path.join(RAW_SOURCES_DIR, "ready_for_ingestion.json")
SQLITE_DB = os.path.join(DATA_DIR, "knowledge_base.db")
CHROMA_PATH = VECTOR_STORE_DIR
CHROMA_COLLECTION = "faq_child_chunks"

def run_ingestion():
    # Ensure all enterprise data directories exist before writing
    os.makedirs(DATA_DIR, exist_ok=True)
    os.makedirs(RAW_SOURCES_DIR, exist_ok=True)
    os.makedirs(VECTOR_STORE_DIR, exist_ok=True)

    if not os.path.exists(INPUT_FILE):
        print(f"[!] Error: {INPUT_FILE} not found.")
        print(f"    Please ensure 'ready_for_ingestion.json' is placed in: {RAW_SOURCES_DIR}")
        return

    print(f"[*] Loading staged data from {INPUT_FILE}...")
    with open(INPUT_FILE, "r", encoding="utf-8") as f:
        data = json.load(f)

    parent_data = data.get("parent_data", [])
    child_data = data.get("child_data", [])

    print(f"[*] Found {len(parent_data)} Parent Chunks and {len(child_data)} Child Chunks.")

    # ==========================================
    # 2. SQLITE PARENT INGESTION
    # ==========================================
    print(f"[*] Initializing SQLite Database at:\n    -> {SQLITE_DB}")
    conn = sqlite3.connect(SQLITE_DB)
    cursor = conn.cursor()

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS knowledge_parents (
            parent_id TEXT PRIMARY KEY,
            domain TEXT NOT NULL,
            intent_families TEXT NOT NULL,
            content TEXT NOT NULL
        )
    ''')
    cursor.execute('DELETE FROM knowledge_parents') 

    sqlite_records = [
        (p["parent_id"], p["domain"], p["intent_families"], p["content"]) 
        for p in parent_data
    ]
    
    cursor.executemany('''
        INSERT INTO knowledge_parents (parent_id, domain, intent_families, content)
        VALUES (?, ?, ?, ?)
    ''', sqlite_records)
    
    conn.commit()
    conn.close()
    print(f"  [+] Successfully inserted {len(sqlite_records)} records into SQLite.")

    # ==========================================
    # 3. CHROMADB CHILD INGESTION
    # ==========================================
    print(f"[*] Initializing Local ChromaDB at:\n    -> {CHROMA_PATH}")
    
    chroma_client = chromadb.PersistentClient(path=CHROMA_PATH)
    emb_fn = embedding_functions.DefaultEmbeddingFunction()
    
    try:
        chroma_client.delete_collection(name=CHROMA_COLLECTION)
    except Exception:
        pass

    collection = chroma_client.create_collection(
        name=CHROMA_COLLECTION,
        embedding_function=emb_fn,
        metadata={"hnsw:space": "cosine"} 
    )

    print("[*] Vectorizing and embedding Child Chunks into ChromaDB...")
    
    documents = []
    metadatas = []
    ids = []

    for idx, chunk in enumerate(child_data):
        documents.append(chunk["text"])
        metadatas.append(chunk["metadata"])
        ids.append(f"chunk_{idx:05d}") 

    batch_size = 500
    for i in range(0, len(documents), batch_size):
        end_idx = min(i + batch_size, len(documents))
        print(f"  -> Processing batch {i} to {end_idx}...")
        
        collection.add(
            documents=documents[i:end_idx],
            metadatas=metadatas[i:end_idx],
            ids=ids[i:end_idx]
        )

    print("\n================================================================")
    print("✅ PHASE 1 COMPLETE: ENTERPRISE INGESTION SUCCESSFUL")
    print("================================================================")
    print("Your infrastructure is now fully populated and mathematically aligned:")
    print(f"1. Precision Placeholders -> {SQLITE_DB}")
    print(f"2. Unfragmented Context   -> {SQLITE_DB}")
    print(f"3. Dense Semantic Vectors -> {CHROMA_PATH}")
    print("================================================================")

if __name__ == "__main__":
    run_ingestion()