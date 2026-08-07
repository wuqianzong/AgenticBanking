import os
import sqlite3
import asyncio
import chromadb
from typing import List, Dict, Any

# ==========================================
# 1. GLOBAL CONNECTION MANAGEMENT
# ==========================================
# Instantiated once at module load to prevent I/O bottleneck
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

    def _hydrate_parents_from_sqlite(self, parent_ids: list) -> str:
        """Fetches the full, unfragmented text for the winning parents."""
        if not parent_ids:
            return "NO_KNOWLEDGE_FOUND"

        cursor = sqlite_conn.cursor()
        placeholders = ','.join('?' for _ in parent_ids)
        query = f"SELECT content FROM knowledge_parents WHERE parent_id IN ({placeholders})"
        cursor.execute(query, parent_ids)
        
        rows = cursor.fetchall()
        
        # Concatenate all winning payloads
        hydrated_payload = "\n\n---\n\n".join([row[0] for row in rows])
        
        print(f"    🧱 [RAG HYDRATION] Hydrated {len(rows)} intact parent chunks from SQLite.")
        print("═"*60 + "\n")
        return hydrated_payload

    async def execute_multi_angle_search(self, blueprint: dict, product_tags: list, intent_families: list) -> str:
        """The Main Entrypoint for Tri-Angle RRF Search."""
        
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

        # 2. Pass 1: The Direct Hit Bypass
        direct_hits = set()
        for hit_list in [procedural_hits, policy_hits, conceptual_hits]:
            for hit in hit_list:
                if hit["score"] >= DIRECT_HIT_THRESHOLD:
                    direct_hits.add(hit["parent_id"])
        
        if direct_hits:
            print(f"    🎯 Direct Hit Bypass Activated! Found {len(direct_hits)} exact matches (> {DIRECT_HIT_THRESHOLD}).")
            return self._hydrate_parents_from_sqlite(list(direct_hits)[:MAX_PARENTS])

        # 3. Pass 2 & 3: Parent-Centric Reciprocal Rank Fusion
        rrf_scores = {}
        
        # Calculate RRF per query angle
        for hit_list in [procedural_hits, policy_hits, conceptual_hits]:
            seen_in_this_query = set()
            for rank, hit in enumerate(hit_list):
                pid = hit["parent_id"]
                if pid not in seen_in_this_query:
                    seen_in_this_query.add(pid)
                    # Standard RRF Formula
                    rrf_points = 1.0 / (RRF_K_CONSTANT + (rank + 1))
                    rrf_scores[pid] = rrf_scores.get(pid, 0.0) + rrf_points

        # Sort by total score
        sorted_parents = sorted(rrf_scores.items(), key=lambda item: item[1], reverse=True)
        
        if not sorted_parents:
            print("    ⚠️ No results returned from ChromaDB.")
            print("═"*60 + "\n")
            return "NO_KNOWLEDGE_FOUND"

        # The 50% Drop-Off Rule
        selected_parent_ids = [sorted_parents[0][0]]
        previous_score = sorted_parents[0][1]

        for i in range(1, min(len(sorted_parents), MAX_PARENTS)):
            current_id, current_score = sorted_parents[i]
            ratio = current_score / previous_score
            
            if ratio < 0.50:
                print(f"    ✂️ Dynamic Selection Boundary triggered. Truncating at Rank {i+1} (Drop-off ratio: {ratio:.2f}).")
                break
                
            selected_parent_ids.append(current_id)
            previous_score = current_score

        # 4. Hydrate & Return
        print(f"    📊 RRF Engine selected {len(selected_parent_ids)} Parent(s) for LLM Hydration: {selected_parent_ids}")
        return self._hydrate_parents_from_sqlite(selected_parent_ids)