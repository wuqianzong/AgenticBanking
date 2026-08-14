import os
import sqlite3
import asyncio
import time  
import chromadb
from typing import List, Dict, Any

# ==========================================
# 1. GLOBAL CONNECTION MANAGEMENT
# ==========================================
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

# RRF Configuration Variables
DIRECT_HIT_THRESHOLD = 0.85
RRF_K_CONSTANT = 60
MAX_PARENTS = 4


# ==========================================
# 2. CORE RETRIEVAL ENGINE (V2.5 RRF Matrix)
# ==========================================
class RAGEngine:
    def __init__(self):
        """Initializes by dynamically fetching the freshest collection handle."""
        self.collection = get_chroma_collection()

    def _build_metadata_filter(self, active_product_tags: list, active_intent_families: list) -> dict:
        """Constructs the deterministic 2D Matrix boundary filter."""
        if not active_product_tags:
            active_product_tags = ["ALL"]
        if not active_intent_families:
            return {"product_tag": {"$contains": "ALL"}} # Fallback

        return {
            "$and": [
                {"intent_family": {"$in": active_intent_families}},
                {"$or": [
                    {"product_tag": {"$contains": tag}} for tag in active_product_tags
                ] + [{"product_tag": {"$contains": "ALL"}}]}
            ]
        }

    async def _query_single_angle(self, angle_text: str, metadata_filter: dict, top_k: int = 5) -> list:
        """Executes a single ChromaDB query asynchronously."""
        if not angle_text:
            return []
            
        loop = asyncio.get_running_loop()
        results = await loop.run_in_executor(
            None,
            lambda: self.collection.query(
                query_texts=[angle_text],
                n_results=top_k,
                where=metadata_filter
            )
        )
        
        # Format results: Distance in Chroma Cosine is (1 - cosine_similarity).
        formatted_hits = []
        if results and results.get('ids') and len(results['ids'][0]) > 0:
            for i in range(len(results['ids'][0])):
                raw_distance = results['distances'][0][i]
                cosine_sim = 1.0 - raw_distance if raw_distance <= 1.0 else 0.0
                
                formatted_hits.append({
                    "parent_id": results['metadatas'][0][i]['parent_id'],
                    "score": cosine_sim
                })
        return formatted_hits

    def _hydrate_parents_from_sqlite(self, parent_ids: list) -> List[Dict[str, str]]:
        """Fetch full parent text while preserving the evidence identifier."""
        if not parent_ids:
            return []

        cursor = sqlite_conn.cursor()
        placeholders = ','.join('?' for _ in parent_ids)
        query = f"SELECT parent_id, content FROM knowledge_parents WHERE parent_id IN ({placeholders})"
        cursor.execute(query, parent_ids)
        rows_by_id = {parent_id: content for parent_id, content in cursor.fetchall()}
        hydrated_payload = [
            {"parent_id": parent_id, "content": rows_by_id[parent_id]}
            for parent_id in parent_ids
            if parent_id in rows_by_id
        ]
        
        print(f"    🧱 [RAG HYDRATION] Hydrated {len(hydrated_payload)} intact parent chunks from SQLite.")
        return hydrated_payload

    def hydrate_parent_ids(self, parent_ids: list) -> List[Dict[str, str]]:
        """Re-read a vetted parent-ID set from the authoritative knowledge DB."""
        return self._hydrate_parents_from_sqlite(parent_ids)

    async def execute_multi_angle_search(self, blueprint: dict, product_tags: list, intent_families: list) -> List[Dict[str, str]]:
        """The Main Entrypoint for Tri-Angle RRF Search."""
        
        start_time = time.time() # Start Telemetry Timer
        
        # 📡 --- TELEMETRY: START SEARCH ---
        print("\n" + "═"*60)
        print(" 🔍 [RAG ENGINE v2.5] INITIALIZING TRI-ANGLE RRF SEARCH")
        print(f" 📦 Product Boundary: {product_tags} | Intent Boundary: {intent_families}")
        print(f" 📐 Procedural Angle: '{blueprint.get('procedural', 'None')}'")
        print(f" 📐 Policy Angle    : '{blueprint.get('policy', 'None')}'")
        print(f" 📐 Conceptual Angle: '{blueprint.get('conceptual', 'None')}'")
        print("─"*60)

        metadata_filter = self._build_metadata_filter(product_tags, intent_families)
        
        # 1. Fire the 3x Fan-Out
        tasks = [
            self._query_single_angle(blueprint.get("procedural", ""), metadata_filter),
            self._query_single_angle(blueprint.get("policy", ""), metadata_filter),
            self._query_single_angle(blueprint.get("conceptual", ""), metadata_filter)
        ]
        
        angle_results = await asyncio.gather(*tasks)
        procedural_hits, policy_hits, conceptual_hits = angle_results

        # Raw Hits Telemetry
        print("\n    [RAG TELEMETRY] --- Raw ChromaDB Hits per Angle ---")
        for angle_name, hits in [("Procedural", procedural_hits), ("Policy", policy_hits), ("Conceptual", conceptual_hits)]:
            print(f"      > {angle_name} Angle Hits:")
            if not hits:
                print("        None")
            for rank, hit in enumerate(hits):
                print(f"        Rank {rank+1}: Parent '{hit['parent_id']}' | Cosine Sim: {hit['score']:.4f}")

        # 2. Pass 1: The Direct Hit Bypass
        direct_hits = set()
        for hit_list in [procedural_hits, policy_hits, conceptual_hits]:
            for hit in hit_list:
                if hit["score"] >= DIRECT_HIT_THRESHOLD:
                    direct_hits.add(hit["parent_id"])
        
        if direct_hits:
            print(f"\n    🎯 Direct Hit Bypass Activated! Found {len(direct_hits)} exact matches (> {DIRECT_HIT_THRESHOLD}).")
            execution_time = (time.time() - start_time) * 1000
            payload = self._hydrate_parents_from_sqlite(list(direct_hits)[:MAX_PARENTS])
            print(f" ⏱️  [RAG ENGINE] Total Execution Time: {execution_time:.2f} ms")
            print("═"*60 + "\n")
            return payload

        # 3. Pass 2 & 3: Parent-Centric Reciprocal Rank Fusion
        print("\n    [RAG TELEMETRY] --- Reciprocal Rank Fusion (RRF) Point Accumulation ---")
        rrf_scores = {}
        
        # Calculate RRF per query angle
        for angle_name, hit_list in [("Procedural", procedural_hits), ("Policy", policy_hits), ("Conceptual", conceptual_hits)]:
            seen_in_this_query = set()
            for rank, hit in enumerate(hit_list):
                pid = hit["parent_id"]
                if pid not in seen_in_this_query:
                    seen_in_this_query.add(pid)
                    # Standard RRF Formula
                    rrf_points = 1.0 / (RRF_K_CONSTANT + (rank + 1))
                    rrf_scores[pid] = rrf_scores.get(pid, 0.0) + rrf_points
                    print(f"      + {rrf_points:.4f} pts to '{pid}' (from {angle_name} Rank {rank+1})")

        # Sort by total score
        sorted_parents = sorted(rrf_scores.items(), key=lambda item: item[1], reverse=True)
        
        if not sorted_parents:
            print("    ⚠️ No results returned from ChromaDB.")
            execution_time = (time.time() - start_time) * 1000
            print(f" ⏱️  [RAG ENGINE] Total Execution Time: {execution_time:.2f} ms")
            print("═"*60 + "\n")
            return []

        # The 50% Drop-Off Rule Calculation
        print("\n    [RAG TELEMETRY] --- Final RRF Standings & Drop-Off Selection ---")
        print(f"      Formula: Score = sum(1 / ({RRF_K_CONSTANT} + Rank))")
        print(f"      Rule: Drop candidates if Score drops by >= 50% compared to previous rank.")
        print(f"      Limit: Max {MAX_PARENTS} parents.")
        print("      " + "-" * 50)
        
        top_id, top_score = sorted_parents[0]
        selected_parent_ids = [top_id]
        previous_score = top_score
        
        print(f"      [Rank 1] Parent: '{top_id}' | Score: {top_score:.4f} | Ratio: N/A     -> ✅ AUTO-SELECTED")

        for i in range(1, len(sorted_parents)):
            current_id, current_score = sorted_parents[i]
            ratio = current_score / previous_score
            
            # Check maximum limits first
            if len(selected_parent_ids) >= MAX_PARENTS:
                print(f"      [Rank {i+1}] Parent: '{current_id}' | Score: {current_score:.4f} | Ratio: {ratio*100:5.1f}% -> ❌ DROPPED (Max Limit {MAX_PARENTS} Reached)")
                continue  # Continue printing the rest of the ranks for observability, but do not select

            # Check ratio threshold
            if ratio < 0.50:
                print(f"      [Rank {i+1}] Parent: '{current_id}' | Score: {current_score:.4f} | Ratio: {ratio*100:5.1f}% -> ❌ DROPPED (Ratio < 50%)")
                print(f"      ✂️ --- Selection Boundary Triggered ---")
                
                # Print the remaining items as automatically dropped due to the boundary
                for j in range(i+1, len(sorted_parents)):
                    drop_id, drop_score = sorted_parents[j]
                    drop_ratio = drop_score / current_score
                    print(f"      [Rank {j+1}] Parent: '{drop_id}' | Score: {drop_score:.4f} | Ratio: {drop_ratio*100:5.1f}% -> ❌ DROPPED (Below Boundary)")
                    current_score = drop_score # update for printing math accurately
                break
            else:
                print(f"      [Rank {i+1}] Parent: '{current_id}' | Score: {current_score:.4f} | Ratio: {ratio*100:5.1f}% -> ✅ SELECTED")
                selected_parent_ids.append(current_id)
                previous_score = current_score

        # 4. Hydrate & Return
        print(f"\n    📊 RRF Engine finalized {len(selected_parent_ids)} Parent(s) for LLM Hydration: {selected_parent_ids}")
        payload = self._hydrate_parents_from_sqlite(selected_parent_ids)
        
        execution_time = (time.time() - start_time) * 1000
        print(f" ⏱️  [RAG ENGINE] Total Execution Time: {execution_time:.2f} ms")
        print("═"*60 + "\n")
        
        return payload
