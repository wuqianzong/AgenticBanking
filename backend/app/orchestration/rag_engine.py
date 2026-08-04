import asyncio
import sqlite3
import chromadb
from typing import List, Dict, Any

# ==========================================
# 1. GLOBAL CONNECTION MANAGEMENT
# ==========================================
# Instantiated once at module load to prevent I/O bottleneck
chroma_client = chromadb.PersistentClient(path="backend/data/vector_store/")
chroma_collection = chroma_client.get_collection("faq_child_chunks")

# SQLite connection with check_same_thread=False for async compatibility
sqlite_conn = sqlite3.connect("backend/data/knowledge_base.db", check_same_thread=False)

# ==========================================
# 2. HYBRID SEARCH EXECUTION (Enhanced Telemetry)
# ==========================================
async def execute_hybrid_rag_search(
    active_domain: str, 
    search_tasks: List[Dict[str, str]], 
    similarity_floor: float = 0.01, 
    high_confidence_cutoff: float = 0.90, 
    min_chunks: int = 3
) -> str:
    
    # 📡 --- TELEMETRY: START SEARCH ---
    print("\n" + "═"*60)
    print(" 🔍 [RAG ENGINE] INITIALIZING HYBRID SEARCH")
    print(f" 🎯 Active Domain : {active_domain}")
    print(f" ⚙️  Thresholds    : Floor={similarity_floor}, HighConf={high_confidence_cutoff}, Min={min_chunks}")
    print(" 📋 Search Tasks  :")
    for idx, t in enumerate(search_tasks):
        print(f"    [{idx+1}] Query: '{t.get('query')}' | Intent: '{t.get('intent')}'")
    print("─"*60)

    all_parent_ids = set()

    # Define the isolated Chroma search task with the dynamic WHERE clause fix
    def query_chroma(task):
        where_filter = {}
        if active_domain:
            where_filter["domain"] = active_domain
            
        intent_val = task.get("intent")
        
        # FIX: Handle if intent_val is a string representation of a list (e.g. "['a', 'b']")
        if isinstance(intent_val, str) and intent_val.startswith("[") and intent_val.endswith("]"):
            import ast
            try:
                intent_val = ast.literal_eval(intent_val)
            except:
                pass # Fallback to raw string if parsing fails
                
        if intent_val:
            where_filter["intent_family"] = intent_val
            
        final_where = None
        
        # Build the final WHERE clause dynamically
        if len(where_filter) > 0:
            conditions = []
            
            if "domain" in where_filter:
                conditions.append({"domain": {"$eq": where_filter["domain"]}})
                
            if "intent_family" in where_filter:
                val = where_filter["intent_family"]
                if isinstance(val, list):
                    # If it's a list, use the $in operator
                    conditions.append({"intent_family": {"$in": val}})
                else:
                    # If it's a string, use the $eq operator
                    conditions.append({"intent_family": {"$eq": val}})
                    
            if len(conditions) == 2:
                final_where = {"$and": conditions}
            elif len(conditions) == 1:
                final_where = conditions[0]
            
        return chroma_collection.query(
            query_texts=[task.get("query", "")],
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
        print(f"\n 📊 [RAG RESULTS] Task {task_idx+1}: '{task_info.get('query')}'")
        
        if not res['distances'] or not res['distances'][0]:
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
                    "domain": meta.get("domain", "UNKNOWN"), 
                    "intent": meta.get("intent_family", "UNKNOWN")
                })
        
        # Sort by score descending
        valid_chunks = sorted(valid_chunks, key=lambda x: x["score"], reverse=True)
        
        # Apply high confidence / min chunk logic
        selected_parents = []
        for idx, chunk in enumerate(valid_chunks):
            is_selected = False
            
            # Logic: If it's a high confidence match, OR we haven't met our minimum chunk quota yet
            if chunk["score"] >= high_confidence_cutoff or len(selected_parents) < min_chunks:
                selected_parents.append(chunk["parent_id"])
                is_selected = True
                
            status = "✅ KEEP" if is_selected else "❌ SKIP (Cap reached)"
            
            # 📡 --- TELEMETRY: INDIVIDUAL CHUNK STATS ---
            print(f"    [{idx+1}] Score: {chunk['score']:.4f} | Parent: {chunk['parent_id']} | Intent: '{chunk['intent']}' -> {status}")

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