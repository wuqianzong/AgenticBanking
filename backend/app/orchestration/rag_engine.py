import asyncio
import sqlite3
import chromadb
from typing import List, Dict, Any

# ==========================================
# 1. GLOBAL CONNECTION MANAGEMENT
# ==========================================
# Instantiated once at module load
CHROMA_PATH = "backend/data/vector_store/"
COLLECTION_NAME = "faq_child_chunks"

chroma_client = chromadb.PersistentClient(path=CHROMA_PATH)

def get_chroma_collection():
    """Dynamically fetches or gets-or-creates the collection to prevent stale handles after re-ingestion."""
    return chroma_client.get_or_create_collection(
        name=COLLECTION_NAME,
        metadata={"hnsw:space": "cosine"}
    )

# SQLite connection with check_same_thread=False for async compatibility
sqlite_conn = sqlite3.connect("backend/data/knowledge_base.db", check_same_thread=False)

# ==========================================
# 2. HYBRID SEARCH EXECUTION (V2.1 Decoupled Matrix)
# ==========================================
async def execute_hybrid_rag_search(
    search_tasks: List[Dict[str, Any]], 
    similarity_floor: float = 0.01, 
    high_confidence_cutoff: float = 0.90, 
    min_chunks: int = 3
) -> str:
    
    # 📡 --- TELEMETRY: START SEARCH ---
    print("\n" + "═"*60)
    print(" 🔍 [RAG ENGINE v2.1] INITIALIZING DECOUPLED MATRIX SEARCH")
    print(f" ⚙️  Thresholds    : Floor={similarity_floor}, HighConf={high_confidence_cutoff}, Min={min_chunks}")
    print(" 📋 Search Tasks  :")
    for idx, t in enumerate(search_tasks):
        print(f"    [{idx+1}] Sub-Query: '{t.get('sub_query')}' | Product: '{t.get('target_commercial_product')}' | Intents: {t.get('target_intent')}")
    print("─"*60)

    all_parent_ids = set()

    # Define the isolated Chroma search task with V2.1 Matrix filtering ($and + $or)
    def query_chroma(task):
        # Safely extract target_product (it might be a string or a list from the LLM)
        target_product = task.get("target_commercial_product", "ALL")
        if isinstance(target_product, list):
            target_product = target_product[0] if target_product else "ALL"
            
        target_intents = task.get("target_intent", [])
        if isinstance(target_intents, str):
            target_intents = [target_intents]
            
        # V2.1 Matrix Filter: Intent (Verb) boundary + Product (Noun) safety gate with "ALL" fallback
        final_where = {
            "$and": [
                {"intent_family": {"$in": target_intents}},
                {"$or": [
                    {"product_tag": {"$contains": target_product}},
                    {"product_tag": {"$contains": "ALL"}}
                ]}
            ]
        }
            
        collection = get_chroma_collection()
        return collection.query(
            query_texts=[task.get("sub_query", "")],
            n_results=10,
            where=final_where
        )
    # 1. Concurrent Dispatch
    loop = asyncio.get_running_loop()
    tasks = [loop.run_in_executor(None, query_chroma, t) for t in search_tasks]
    results = await asyncio.gather(*tasks)

    # 2. Dynamic Thresholding & Telemetry per Task
    for task_idx, res in enumerate(results):
        task_info = search_tasks[task_idx]
        print(f"\n 📊 [RAG RESULTS] Task {task_idx+1}: '{task_info.get('sub_query')}'")
        
        if not res.get('distances') or not res['distances'][0]:
            print("    ⚠️ No results returned from ChromaDB.")
            continue
            
        # Convert Chroma distance to Cosine Similarity score
        scores = [1.0 - dist for dist in res['distances'][0]]
        metadatas = res['metadatas'][0]
        
        # 📡 --- TELEMETRY: SCORE DISTRIBUTION ---
        total_retrieved = len(scores)
        above_90 = sum(1 for s in scores if s >= high_confidence_cutoff)
        below_50 = sum(1 for s in scores if s < similarity_floor)
        mid_range = total_retrieved - above_90 - below_50
        
        print(f"    Total retrieved : {total_retrieved}")
        print(f"    High Conf (>{high_confidence_cutoff}) : {above_90}")
        print(f"    Mid Range       : {mid_range}")
        print(f"    Dropped (<{similarity_floor}) : {below_50}")
        print("    --- Top Chunk Diagnostics ---")
        
        valid_chunks = []
        for score, meta in zip(scores, metadatas):
            if score >= similarity_floor:
                valid_chunks.append({
                    "score": score, 
                    "parent_id": meta.get("parent_id", "UNKNOWN"), 
                    "product": meta.get("product_tag", "UNKNOWN"), 
                    "intent": meta.get("intent_family", "UNKNOWN")
                })
        
        # Sort by score descending
        valid_chunks = sorted(valid_chunks, key=lambda x: x["score"], reverse=True)
        
        # Apply high confidence / min chunk logic
        selected_parents = []
        for idx, chunk in enumerate(valid_chunks):
            is_selected = False
            
            if chunk["score"] >= high_confidence_cutoff or len(selected_parents) < min_chunks:
                selected_parents.append(chunk["parent_id"])
                is_selected = True
                
            status = "✅ KEEP" if is_selected else "❌ SKIP (Cap reached)"
            
            # 📡 --- TELEMETRY: INDIVIDUAL CHUNK STATS ---
            print(f"    [{idx+1}] Score: {chunk['score']:.4f} | Parent: {chunk['parent_id']} | Product: {chunk['product']} | Intent: '{chunk['intent']}' -> {status}")

        # 3. Deduplication
        all_parent_ids.update(selected_parents)

    print("\n 🧱 [RAG HYDRATION] Extracting from SQLite")
    print(f"    Unique Parent IDs mapped: {list(all_parent_ids)}")
    
    if not all_parent_ids:
        print(" 🚨 [RAG ENGINE] No valid chunks passed the thresholds.")
        print("═"*60 + "\n")
        return "NO_KNOWLEDGE_FOUND"

    # 4. SQLite Parent Hydration
    cursor = sqlite_conn.cursor()
    placeholders = ",".join("?" * len(all_parent_ids))
    query = f"SELECT content FROM knowledge_parents WHERE parent_id IN ({placeholders})"
    
    cursor.execute(query, tuple(all_parent_ids))
    rows = cursor.fetchall()
    
    # 5. Concatenate Payload
    hydrated_payload = "\n\n---\n\n".join([row[0] for row in rows])
    
    print(f"    Hydrated {len(rows)} intact parent chunks.")
    print("═"*60 + "\n")
    
    return hydrated_payload