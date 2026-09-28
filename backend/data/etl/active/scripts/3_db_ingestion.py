import json
import sqlite3
import chromadb
from chromadb.utils import embedding_functions
import argparse
from pathlib import Path

# ==========================================
# 1. DYNAMIC ABSOLUTE PATHING
# ==========================================
SCRIPT_DIR = Path(__file__).resolve().parent
ACTIVE_ETL_DIR = SCRIPT_DIR.parent
DATA_DIR = ACTIVE_ETL_DIR.parents[1]
VECTOR_STORE_DIR = DATA_DIR / "vector_store"

INPUT_FILE = ACTIVE_ETL_DIR / "staging" / "active_generic_ready_for_ingestion.json"
SQLITE_DB = DATA_DIR / "knowledge_base.db"
CHROMA_PATH = VECTOR_STORE_DIR
CHROMA_COLLECTION = "faq_child_chunks"

def run_ingestion(input_file: str | Path = INPUT_FILE):
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    VECTOR_STORE_DIR.mkdir(parents=True, exist_ok=True)

    print(f"[*] Loading staged data from {input_file}...")
    with Path(input_file).open("r", encoding="utf-8") as f:
        data = json.load(f)

    parent_data = data.get("parent_data", [])
    child_data = data.get("child_data", [])
    if not parent_data or not child_data:
        raise ValueError("Staged input must contain non-empty parent_data and child_data arrays.")
    tri_angle_schema = all(
        isinstance(child, dict)
        and isinstance(child.get("metadata"), dict)
        and child["metadata"].get("angle") in {"procedural", "policy", "conceptual"}
        for child in child_data
    )

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
    chroma_client = chromadb.PersistentClient(path=str(CHROMA_PATH))
    emb_fn = embedding_functions.DefaultEmbeddingFunction()
    
    try:
        chroma_client.delete_collection(name=CHROMA_COLLECTION)
        print(f"  -> Dropped old ChromaDB collection '{CHROMA_COLLECTION}'.")
    except Exception:
        print(f"  -> Collection '{CHROMA_COLLECTION}' not found. Creating new...")

    collection = chroma_client.create_collection(
        name=CHROMA_COLLECTION,
        embedding_function=emb_fn,
        metadata={
            "hnsw:space": "cosine",
            "schema_version": "tri_angle_v1" if tri_angle_schema else "generic_v1",
        }
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
    print("✅ RAG DEPLOYMENT SUCCESSFUL")
    print("================================================================")
    print(
        "Databases updated. Active schema: "
        + ("tri_angle_v1" if tri_angle_schema else "generic_v1")
    )

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Ingest a validated RAG staging artifact.")
    parser.add_argument(
        "--input",
        default=INPUT_FILE,
        help="Path to a staging JSON file (defaults to the active reconciled tri-angle artifact).",
    )
    args = parser.parse_args()
    run_ingestion(args.input)
