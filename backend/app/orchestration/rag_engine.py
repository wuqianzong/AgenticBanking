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
RRF_K_CONSTANT = 60
MAX_PARENTS = 4
# Each semantic angle contributes this many candidates before task-level RRF.
# A deeper pool gives expanded metadata scopes enough candidates to compete
# without increasing the number of vector queries.
RRF_CANDIDATES_PER_ANGLE = int(os.getenv("RRF_CANDIDATES_PER_ANGLE", "8"))


# ==========================================
# 2. CORE RETRIEVAL ENGINE (V2.5 RRF Matrix)
# ==========================================
class RAGEngine:
    def __init__(self, collection=None):
        """Use the active collection, or an explicitly supplied read-only benchmark collection."""
        self.collection = collection if collection is not None else get_chroma_collection()
        self.has_tri_angle_schema = (
            (self.collection.metadata or {}).get("schema_version") == "tri_angle_v1"
        )

    def _build_metadata_filter(self, scope_pairs: list[dict]) -> dict:
        """Build one shared filter from valid product/intent pairs.

        Product and intent are metadata constraints, not independently fused
        retrieval branches.  The caller supplies only matrix-valid pairs so
        multi-product expansion cannot create invalid cross-pairings.
        """
        pair_conditions = []
        for pair in scope_pairs:
            product = str(pair.get("product", "")).strip()
            intents = [
                str(intent).strip()
                for intent in pair.get("intents", [])
                if str(intent).strip()
            ]
            if not product or not intents:
                continue
            pair_conditions.append({
                "$and": [
                    {"product_tag": {"$contains": product}},
                    {"intent_family": {"$in": intents}},
                ]
            })

        if not pair_conditions:
            # This should be unreachable because n2e rejects invalid tasks.
            # Return a deliberately empty boundary rather than widening to all
            # knowledge if a malformed task reaches the worker.
            return {"product_tag": {"$contains": "__NO_VALID_SCOPE__"}}
        return pair_conditions[0] if len(pair_conditions) == 1 else {"$or": pair_conditions}

    async def _query_single_angle(self, angle_text: str, metadata_filter: dict, top_k: int = RRF_CANDIDATES_PER_ANGLE) -> list:
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

    async def execute_direct_search(self, query_text: str, scope_pairs: list[dict]) -> List[Dict[str, str]]:
        """Retrieve a task through its resolved customer subquery only.

        Direct retrieval is the default path: it retains the customer's most
        discriminative wording and uses Product × Intent solely as a metadata
        boundary.  It does not manufacture multiple rank lists, so Chroma's
        cosine ranking is used directly before parent-level de-duplication.
        """
        clean_query = str(query_text or "").strip()
        if not clean_query:
            print("    ⚠️ [DIRECT RAG] No resolved task subquery was supplied.")
            return []

        start_time = time.time()
        metadata_filter = self._build_metadata_filter(scope_pairs)
        print("\n" + "═" * 60)
        print(" 🔍 [RAG ENGINE] INITIALIZING DIRECT TASK SEARCH")
        print(f" 📦 Valid Product/Intent Scope: {scope_pairs}")
        print(f" 🗣️  Resolved task subquery: '{clean_query}'")

        hits = await self._query_single_angle(clean_query, metadata_filter)
        if not hits:
            print("    ⚠️ No results returned from ChromaDB.")
            print("═" * 60 + "\n")
            return []

        # A parent may own multiple child chunks.  Chroma returns them in
        # descending cosine similarity, so retain the first (strongest) child
        # score for each parent before applying the shared hydration boundary.
        parent_scores: Dict[str, float] = {}
        for child_rank, hit in enumerate(hits, start=1):
            parent_id = hit["parent_id"]
            if parent_id in parent_scores:
                continue
            parent_scores[parent_id] = hit["score"]
            print(
                f"      [Child Rank {child_rank}] Parent: '{parent_id}' "
                f"| Cosine Sim: {hit['score']:.4f}"
            )

        ranked_parents = sorted(parent_scores.items(), key=lambda item: item[1], reverse=True)
        selected_parent_ids = []
        previous_score = None
        print("    [DIRECT RAG] --- Parent Selection ---")
        for rank, (parent_id, score) in enumerate(ranked_parents, start=1):
            if not selected_parent_ids:
                selected_parent_ids.append(parent_id)
                previous_score = score
                print(f"      [Rank 1] Parent: '{parent_id}' | Score: {score:.4f} -> ✅ AUTO-SELECTED")
                continue
            if len(selected_parent_ids) >= MAX_PARENTS:
                print(f"      [Rank {rank}] Parent: '{parent_id}' | Score: {score:.4f} -> ❌ DROPPED (Max Limit {MAX_PARENTS} Reached)")
                continue
            ratio = score / previous_score if previous_score else 0.0
            if ratio < 0.50:
                print(f"      [Rank {rank}] Parent: '{parent_id}' | Score: {score:.4f} | Ratio: {ratio * 100:.1f}% -> ❌ DROPPED (Ratio < 50%)")
                break
            selected_parent_ids.append(parent_id)
            previous_score = score
            print(f"      [Rank {rank}] Parent: '{parent_id}' | Score: {score:.4f} | Ratio: {ratio * 100:.1f}% -> ✅ SELECTED")

        print(f"    📊 Direct search finalized {len(selected_parent_ids)} Parent(s) for LLM Hydration: {selected_parent_ids}")
        payload = self._hydrate_parents_from_sqlite(selected_parent_ids)
        print(f" ⏱️  [DIRECT RAG] Total Execution Time: {(time.time() - start_time) * 1000:.2f} ms")
        print("═" * 60 + "\n")
        return payload

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

    @staticmethod
    def _normalize_angle_groups(blueprint: dict) -> list[dict]:
        """Accept V2.5 angle groups and legacy flat three-angle tasks."""
        groups = []
        for index, raw_group in enumerate(blueprint.get("angle_groups", []) or [], start=1):
            if not isinstance(raw_group, dict):
                continue
            group = {
                "group_id": str(raw_group.get("group_id", "")).strip() or f"group_{index}",
                "procedural": str(raw_group.get("procedural", "")).strip(),
                "policy": str(raw_group.get("policy", "")).strip(),
                "conceptual": str(raw_group.get("conceptual", "")).strip(),
            }
            if any(group[name] for name in ("procedural", "policy", "conceptual")):
                groups.append(group)

        if groups:
            return groups
        legacy_group = {
            "group_id": "base",
            "procedural": str(blueprint.get("procedural", "")).strip(),
            "policy": str(blueprint.get("policy", "")).strip(),
            "conceptual": str(blueprint.get("conceptual", "")).strip(),
        }
        return [legacy_group] if any(legacy_group[name] for name in ("procedural", "policy", "conceptual")) else []

    async def execute_multi_angle_search(self, blueprint: dict, scope_pairs: list[dict]) -> List[Dict[str, str]]:
        """Retrieve one task through angle-group RRF under one shared scope.

        Every angle group is an alternative semantic representation.  Each
        group runs its procedural, policy, and conceptual searches against the
        same valid product/intent filter.  RRF is first calculated inside each
        group, then equal-normalised across groups at task level.
        """
        
        start_time = time.time() # Start Telemetry Timer
        
        # 📡 --- TELEMETRY: START SEARCH ---
        print("\n" + "═"*60)
        print(" 🔍 [RAG ENGINE v2.5] INITIALIZING ANGLE-GROUP RRF SEARCH")
        print(f" 📦 Valid Product/Intent Scope: {scope_pairs}")
        print("─"*60)

        angle_groups = self._normalize_angle_groups(blueprint)
        metadata_filter = self._build_metadata_filter(scope_pairs)
        query_specs = [
            (
                group["group_id"],
                angle_name,
                angle_text,
                (
                    {"$and": [metadata_filter, {"angle": angle_name.lower()}]}
                    if self.has_tri_angle_schema
                    else metadata_filter
                ),
            )
            for group in angle_groups
            for angle_name, angle_text in (
                ("Procedural", group["procedural"]),
                ("Policy", group["policy"]),
                ("Conceptual", group["conceptual"]),
            )
            if angle_text
        ]
        print(f" 📐 Angle groups: {len(angle_groups)} | Vector searches: {len(query_specs)}")
        print(
            " 🧭 Child-vector schema: "
            + (
                "tri-angle (matching-angle filters enabled)"
                if self.has_tri_angle_schema
                else "generic (compatibility mode)"
            )
        )
        for group in angle_groups:
            print(f"    • {group['group_id']}: procedural='{group['procedural']}', policy='{group['policy']}', conceptual='{group['conceptual']}'")

        if not query_specs:
            print("    ⚠️ No valid semantic angles supplied for this task.")
            return []
        query_results = await asyncio.gather(*[
            self._query_single_angle(angle_text, metadata_filter)
            for _, _, angle_text, metadata_filter in query_specs
        ])
        ranked_query_results = [
            (query_specs[index][0], query_specs[index][1], hits)
            for index, hits in enumerate(query_results)
        ]

        # Raw Hits Telemetry
        print("\n    [RAG TELEMETRY] --- Raw ChromaDB Hits per Angle ---")
        for group_id, angle_name, hits in ranked_query_results:
            print(f"      > {group_id} / {angle_name} Hits:")
            if not hits:
                print("        None")
            for rank, hit in enumerate(hits):
                print(f"        Rank {rank+1}: Parent '{hit['parent_id']}' | Cosine Sim: {hit['score']:.4f}")

        # 2. Group RRF: fuse the three angle rankings within each group.
        print("\n    [RAG TELEMETRY] --- Group-Level RRF Point Accumulation ---")
        group_rrf_scores: Dict[str, Dict[str, float]] = {}
        for group_id, angle_name, hit_list in ranked_query_results:
            group_scores = group_rrf_scores.setdefault(group_id, {})
            seen_in_this_query = set()
            for rank, hit in enumerate(hit_list):
                pid = hit["parent_id"]
                if pid not in seen_in_this_query:
                    seen_in_this_query.add(pid)
                    rrf_points = 1.0 / (RRF_K_CONSTANT + (rank + 1))
                    group_scores[pid] = group_scores.get(pid, 0.0) + rrf_points
                    print(f"      + {rrf_points:.4f} pts to '{pid}' (from {group_id} / {angle_name} Rank {rank+1})")

        # 3. Task RRF: angle groups have equal normalised influence.  This
        # prevents score inflation when feedback adds more semantic groups.
        print("\n    [RAG TELEMETRY] --- Equal-Normalised Task RRF ---")
        task_rrf_scores: Dict[str, float] = {}
        group_count = len(angle_groups)
        group_weight = 1.0 / group_count if group_count else 0.0
        for group_id, group_scores in group_rrf_scores.items():
            for pid, group_score in group_scores.items():
                contribution = group_score * group_weight
                task_rrf_scores[pid] = task_rrf_scores.get(pid, 0.0) + contribution
                print(f"      + {contribution:.4f} task pts to '{pid}' ({group_id} group score {group_score:.4f} × {group_weight:.3f})")

        sorted_parents = sorted(task_rrf_scores.items(), key=lambda item: item[1], reverse=True)
        
        if not sorted_parents:
            print("    ⚠️ No results returned from ChromaDB.")
            execution_time = (time.time() - start_time) * 1000
            print(f" ⏱️  [RAG ENGINE] Total Execution Time: {execution_time:.2f} ms")
            print("═"*60 + "\n")
            return []

        # 4. The task-level 50% Drop-Off Rule Calculation
        print("\n    [RAG TELEMETRY] --- Final RRF Standings & Drop-Off Selection ---")
        print(f"      Formula: Score = average(group sum(1 / ({RRF_K_CONSTANT} + Rank)))")
        print("      Rule: stop when a score is less than 50% of the prior selected score.")
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

        # 5. Hydrate & Return
        print(f"\n    📊 RRF Engine finalized {len(selected_parent_ids)} Parent(s) for LLM Hydration: {selected_parent_ids}")
        payload = self._hydrate_parents_from_sqlite(selected_parent_ids)
        
        execution_time = (time.time() - start_time) * 1000
        print(f" ⏱️  [RAG ENGINE] Total Execution Time: {execution_time:.2f} ms")
        print("═"*60 + "\n")
        
        return payload
