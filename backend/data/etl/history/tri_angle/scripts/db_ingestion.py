import os
import json
import sqlite3
import logging
import chromadb
from chromadb.utils import embedding_functions

# ==========================================
# CONFIGURATION & FILE PATHS
# ==========================================
BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../.."))
DATA_DIR = os.path.join(BASE_DIR, "backend/data")
VECTOR_STORE_DIR = os.path.join(DATA_DIR, "vector_store", "chroma_db")

INPUT_FILE = os.path.join(DATA_DIR, "ready_for_ingestion.json")
SQLITE_DB_PATH = os.path.join(DATA_DIR, "knowledge_base.db")

CHROMA_COLLECTION_NAME = "horizon_faqs"
EMBEDDING_MODEL = "all-MiniLM-L6-v2"

# Batch size to protect 16GB M1 Mac RAM during local embedding
BATCH_SIZE = 500 

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

# ==========================================
# PHASE 1: SQLITE (PARENT PAYLOADS)
# ==========================================
def ingest_parents_to_sqlite(parent_data: list):
    """Surgically replaces Parent FAQs without destroying other tables."""
    logging.info(f"Connecting to SQLite at {SQLITE_DB_PATH}...")
    
    # Ensure directory exists just in case
    os.makedirs(os.path.dirname(SQLITE_DB_PATH), exist_ok=True)
    
    conn = sqlite3.connect(SQLITE_DB_PATH)
    cursor = conn.cursor()

    # 1. Create table if it doesn't exist
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS knowledge_parents (
            parent_id TEXT PRIMARY KEY,
            product_tags TEXT,
            intent_families TEXT,
            content TEXT
        )
    """)
    
    # 2. SURGICAL CLEAR: Delete old RAG data, but keep other tables intact
    cursor.execute("DELETE FROM knowledge_parents")
    logging.info("Old parent data wiped from knowledge_parents table.")

    # 3. Batch Insert New Data
    insert_query = """
        INSERT INTO knowledge_parents (parent_id, product_tags, intent_families, content)
        VALUES (?, ?, ?, ?)
    """
    
    # Format data for SQLite
    records_to_insert = [
        (p["parent_id"], p["product_tags"], p["intent_families"], p["content"]) 
        for p in parent_data
    ]
    
    cursor.executemany(insert_query, records_to_insert)
    conn.commit()
    conn.close()
    
    logging.info(f"✅ Successfully inserted {len(records_to_insert)} Parent FAQs into SQLite.")

# ==========================================
# PHASE 2: CHROMADB (CHILD VECTOR HOOKS)
# ==========================================
def ingest_children_to_chroma(child_data: list):
    """Embeds and loads dense Tri-Angle chunks into ChromaDB."""
    os.makedirs(VECTOR_STORE_DIR, exist_ok=True)
    
    logging.info(f"Initializing Local ChromaDB at {VECTOR_STORE_DIR}...")
    client = chromadb.PersistentClient(path=VECTOR_STORE_DIR)
    
    # Initialize the lightweight local embedding model
    sentence_transformer_ef = embedding_functions.SentenceTransformerEmbeddingFunction(model_name=EMBEDDING_MODEL)

    # 1. Clear old collection to prevent vector drift
    try:
        client.delete_collection(name=CHROMA_COLLECTION_NAME)
        logging.info(f"Old ChromaDB collection '{CHROMA_COLLECTION_NAME}' deleted.")
    except Exception:
        logging.info(f"No existing collection '{CHROMA_COLLECTION_NAME}' found. Creating fresh...")

    # 2. Create fresh collection
    collection = client.create_collection(
        name=CHROMA_COLLECTION_NAME,
        embedding_function=sentence_transformer_ef,
        metadata={"hnsw:space": "cosine"} # Explicitly set to Cosine Similarity
    )

    # 3. Batch processing to protect M1 Mac Memory
    total_chunks = len(child_data)
    logging.info(f"Starting local embedding of {total_chunks} vectors (Batch size: {BATCH_SIZE})...")

    for i in range(0, total_chunks, BATCH_SIZE):
        batch = child_data[i:i + BATCH_SIZE]
        
        ids = [chunk["child_id"] for chunk in batch]
        documents = [chunk["search_text"] for chunk in batch]
        
        # Format metadata for ChromaDB insertion
        metadatas = []
        for chunk in batch:
            metadatas.append({
                "parent_id": chunk["parent_id"],
                "intent_family": chunk["intent_family"],
                # ChromaDB requires lists for $contains queries
                "product_tag": chunk["product_tag"] 
            })
            
        # Add to Chroma (this triggers the local embedding model)
        collection.add(
            ids=ids,
            documents=documents,
            metadatas=metadatas
        )
        
        logging.info(f"  -> Embedded and inserted batch {int(i/BATCH_SIZE) + 1}/{(total_chunks // BATCH_SIZE) + 1}")

    logging.info(f"✅ Successfully inserted {total_chunks} Child Vectors into ChromaDB.")

# ==========================================
# MAIN EXECUTION
# ==========================================
def run_ingestion():
    print(f"\n{'━'*60}")
    print(" 🚀 INITIATING RAG V2.5 DECOUPLED DATABASE INGESTION")
    print(f"{'━'*60}\n")
    
    if not os.path.exists(INPUT_FILE):
        logging.error(f"Input file not found at {INPUT_FILE}")
        return

    # Load the JSON payloads
    with open(INPUT_FILE, "r") as f:
        ingestion_payload = json.load(f)

    parent_data = ingestion_payload.get("parent_data", [])
    child_data = ingestion_payload.get("child_data", [])

    if not parent_data or not child_data:
        logging.error("Ingestion payload is empty. Aborting.")
        return

    # Execute decoupled writes
    ingest_parents_to_sqlite(parent_data)
    ingest_children_to_chroma(child_data)
    
    print(f"\n{'━'*60}")
    print(" 🎉 INGESTION COMPLETE: The RAG engine is armed.")
    print(f"{'━'*60}\n")

if __name__ == "__main__":
    run_ingestion()