import json
import sqlite3
import chromadb
from chromadb.utils import embedding_functions
import os

# ==========================================
# 1. DYNAMIC ABSOLUTE PATHING
# ==========================================
CURRENT_DIR = os.path.dirname(os.path.abspath(__file__)) 
APP_DIR = os.path.dirname(CURRENT_DIR)                   
BACKEND_DIR = os.path.dirname(APP_DIR)                   

DATA_DIR = os.path.join(BACKEND_DIR, "data")
RAW_SOURCES_DIR = os.path.join(DATA_DIR, "raw_sources")
VECTOR_STORE_DIR = os.path.join(DATA_DIR, "vector_store")

INPUT_FILE = os.path.join(RAW_SOURCES_DIR, "ready_for_ingestion.json")
SQLITE_DB = os.path.join(DATA_DIR, "knowledge_base.db")
CHROMA_PATH = VECTOR_STORE_DIR
CHROMA_COLLECTION = "faq_child_chunks"

def run_ingestion():
    os.makedirs(DATA_DIR, exist_ok=True)
    os.makedirs(RAW_SOURCES_DIR, exist_ok=True)
    os.makedirs(VECTOR_STORE_DIR, exist_ok=True)

    print(f"[*] Loading staged data from {INPUT_FILE}...")
    with open(INPUT_FILE, "r", encoding="utf-8") as f:
        data = json.load(f)

    parent_data = data.get("parent_data", [])
    child_data = data.get("child_data", [])

    # ==========================================
    # 2. SQLITE: SURGICAL SCHEMA UPDATE & INGEST
    # ==========================================
    print(f"[*] Connecting to SQLite -> {SQLITE_DB}")
    conn = sqlite3.connect(SQLITE_DB)
    cursor = conn.cursor()

    # 1. Hard drop ONLY the knowledge_parents table to clear the old 'domain' schema
    print("  -> Dropping old 'knowledge_parents' schema...")
    cursor.execute('DROP TABLE IF EXISTS knowledge_parents')
    
    # 2. Recreate with the new RAG v2.1 Schema ('product_tags' instead of 'domain')
    print("  -> Creating fresh 'knowledge_parents' table...")
    cursor.execute('''
        CREATE TABLE knowledge_parents (
            parent_id TEXT PRIMARY KEY,
            product_tags TEXT NOT NULL,
            intent_families TEXT NOT NULL,
            content TEXT NOT NULL
        )
    ''')

    sqlite_records = [
        (p["parent_id"], p["product_tags"], p["intent_families"], p["content"]) 
        for p in parent_data
    ]
    
    cursor.executemany('''
        INSERT INTO knowledge_parents (parent_id, product_tags, intent_families, content)
        VALUES (?, ?, ?, ?)
    ''', sqlite_records)
    
    conn.commit()
    conn.close()
    print(f"  [+] Successfully inserted {len(sqlite_records)} clean records into SQLite.")

    # ==========================================
    # 3. CHROMADB: DROP AND INGEST
    # ==========================================
    print(f"[*] Connecting to ChromaDB -> {CHROMA_PATH}")
    chroma_client = chromadb.PersistentClient(path=CHROMA_PATH)
    emb_fn = embedding_functions.DefaultEmbeddingFunction()
    
    try:
        chroma_client.delete_collection(name=CHROMA_COLLECTION)
        print(f"  -> Dropped old ChromaDB collection '{CHROMA_COLLECTION}'.")
    except Exception:
        print(f"  -> Collection '{CHROMA_COLLECTION}' not found. Creating new...")

    collection = chroma_client.create_collection(
        name=CHROMA_COLLECTION,
        embedding_function=emb_fn,
        metadata={"hnsw:space": "cosine"} 
    )

    print("[*] Vectorizing and embedding Child Chunks into ChromaDB...")
    documents, metadatas, ids = [], [], []

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
    print("✅ RAG v2.1 DEPLOYMENT SUCCESSFUL")
    print("================================================================")
    print("Databases updated. 'Domain' is formally eradicated from retrieval.")

if __name__ == "__main__":
    run_ingestion()