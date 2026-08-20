import os
import sqlite3
import asyncio
import time  
import chromadb
import hashlib
from typing import List, Dict, Any
from pathlib import Path
from backend.app.core.system_logger import request_context

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
# Shadow retrieval is automatic only for the explicitly tagged evaluation.
# Production can opt in to diagnostic shadows without changing its evidence
# path by setting either variable to a truthy value.
LOG_SHADOW_DIRECT_QUERY = os.getenv("RETRIEVAL_LOG_SHADOW_DIRECT", "0").strip().lower() in {"1", "true", "yes"}
LOG_SHADOW_ORIGINAL_QUERY = os.getenv("RETRIEVAL_LOG_SHADOW_ORIGINAL_QUERY", "0").strip().lower() in {"1", "true", "yes"}
EVALUATION_TAG = "retrieval_cosine_eval"
_INDEX_FINGERPRINT_CACHE: dict[tuple[str, int, int], str] = {}
_EMPTY_RETRIEVAL_FOCUS_VALUES = {"none", "n/a", "na", "not applicable", "unknown", "no focus"}


def _shadow_queries_enabled(configured: bool) -> bool:
    return configured or request_context.get().get("evaluation_tag") == EVALUATION_TAG


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

    def _index_identity(self) -> Dict[str, Any]:
        """Return a cheap, log-safe identifier for the index snapshot used.

        The collection metadata alone is not enough to distinguish two
        ingestion runs with the same schema.  SQLite size and mtime are not a
        content hash, but together with collection name/schema let replay jobs
        reject obviously mismatched snapshots without opening the store.
        """
        index_file = Path(CHROMA_PATH) / "chroma.sqlite3"
        try:
            stat = index_file.stat()
            cache_key = (str(index_file.resolve()), stat.st_size, stat.st_mtime_ns)
            fingerprint = _INDEX_FINGERPRINT_CACHE.get(cache_key)
            if fingerprint is None:
                digest = hashlib.sha256()
                with index_file.open("rb") as handle:
                    for block in iter(lambda: handle.read(1024 * 1024), b""):
                        digest.update(block)
                fingerprint = digest.hexdigest()
                _INDEX_FINGERPRINT_CACHE.clear()
                _INDEX_FINGERPRINT_CACHE[cache_key] = fingerprint
            storage = {
                "sqlite_size_bytes": stat.st_size,
                "sqlite_mtime_ns": stat.st_mtime_ns,
                "sqlite_sha256": fingerprint,
            }
        except OSError:
            storage = {"sqlite_size_bytes": None, "sqlite_mtime_ns": None, "sqlite_sha256": None}
        metadata = self.collection.metadata or {}
        return {
            "collection_name": self.collection.name,
            "schema_version": metadata.get("schema_version", "unknown"),
            "distance_space": metadata.get("hnsw:space", "unknown"),
            **storage,
        }

    @staticmethod
    def _single_view_parent_result(hits: list[dict]) -> tuple[list[dict], list[str]]:
        """Apply the production parent RRF and drop-off rules to one query view."""
        rrf_scores: Dict[str, float] = {}
        max_child_cosines: Dict[str, float] = {}
        seen_parents = set()
        for rank, hit in enumerate(hits, start=1):
            parent_id = hit["parent_id"]
            max_child_cosines[parent_id] = max(max_child_cosines.get(parent_id, float("-inf")), hit["score"])
            if parent_id in seen_parents:
                continue
            seen_parents.add(parent_id)
            rrf_scores[parent_id] = rrf_scores.get(parent_id, 0.0) + 1.0 / (RRF_K_CONSTANT + rank)
        ranking = [
            {
                "parent_id": parent_id,
                "rrf_score": rrf_score,
                "max_child_cosine": max_child_cosines[parent_id],
            }
            for parent_id, rrf_score in sorted(rrf_scores.items(), key=lambda item: item[1], reverse=True)
        ]
        selected, previous_score = [], None
        for entry in ranking:
            if len(selected) >= MAX_PARENTS:
                break
            if previous_score is not None and entry["rrf_score"] / previous_score < 0.5:
                break
            selected.append(entry["parent_id"])
            previous_score = entry["rrf_score"]
        return ranking, selected

    @staticmethod
    def _compose_anchored_angle_query(subquery: str, retrieval_focus: str) -> str:
        """Keep the resolved request intact and append a narrow search lens.

        The three existing angle fields remain the model contract.  Their
        values are focus text, never stand-alone rewrites of the request. The
        human-readable field label belongs in telemetry, not in the embedding
        input where it would add irrelevant semantic weight.
        """
        return f"{subquery}\n{retrieval_focus}" if retrieval_focus else subquery

    async def execute_multi_angle_search(
        self,
        blueprint: dict,
        scope_pairs: list[dict],
        *,
        include_telemetry: bool = False,
    ) -> List[Dict[str, str]] | tuple[List[Dict[str, str]], Dict[str, Any]]:
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
        telemetry = {
            "telemetry_version": 2,
            "index": self._index_identity(),
            "scope_pairs": scope_pairs,
            # The resolved subquery is the fair task-level direct baseline.
            # Keep the legacy `direct_*` names for existing log readers.
            "original_query": str(blueprint.get("original_user_query", "")).strip(),
            "subquery": str(blueprint.get("customer_component", "")).strip(),
            "direct_query": str(blueprint.get("customer_component", "")).strip(),
            "original_query_executed": False,
            "original_query_reused_subquery_result": False,
            "original_query_hits": [],
            "original_retrieved_parent_ids": [],
            "original_parent_ranking": [],
            "original_ranked_parent_ids": [],
            "original_selected_parent_ids": [],
            "direct_query_executed": False,
            "direct_query_hits": [],
            "direct_retrieved_parent_ids": [],
            "direct_parent_ranking": [],
            "direct_ranked_parent_ids": [],
            "direct_selected_parent_ids": [],
            "angle_groups": angle_groups,
            "per_angle_hits": [],
            "skipped_angle_views": [],
            "all_angle_retrieved_parent_ids": [],
            "rrf_parent_ranking": [],
            "all_rrf_ranked_parent_ids": [],
            "selected_parent_ids": [],
            "rrf_k": RRF_K_CONSTANT,
            "candidates_per_angle": RRF_CANDIDATES_PER_ANGLE,
            "max_parents": MAX_PARENTS,
            "dropoff_ratio": 0.5,
        }

        # The untouched customer message is a diagnostic-only shadow. For a
        # multi-part turn it is deliberately broader than this task's subquery,
        # so it must never enter the production RRF or evidence path.
        if _shadow_queries_enabled(LOG_SHADOW_ORIGINAL_QUERY) and telemetry["original_query"]:
            if telemetry["original_query"] == telemetry["subquery"]:
                telemetry["original_query_reused_subquery_result"] = True
            else:
                original_hits = await self._query_single_angle(
                    telemetry["original_query"], metadata_filter
                )
                original_ranking, original_selected = self._single_view_parent_result(original_hits)
                telemetry.update({
                    "original_query_executed": True,
                    "original_query_hits": original_hits,
                    "original_retrieved_parent_ids": list(dict.fromkeys(hit["parent_id"] for hit in original_hits)),
                    "original_parent_ranking": original_ranking,
                    "original_ranked_parent_ids": [entry["parent_id"] for entry in original_ranking],
                    "original_selected_parent_ids": original_selected,
                })
        if _shadow_queries_enabled(LOG_SHADOW_DIRECT_QUERY) and telemetry["direct_query"]:
            direct_hits = await self._query_single_angle(
                telemetry["direct_query"], metadata_filter
            )
            direct_ranking, direct_selected = self._single_view_parent_result(direct_hits)
            telemetry.update({
                "direct_query_executed": True,
                "direct_query_hits": direct_hits,
                "direct_retrieved_parent_ids": list(dict.fromkeys(hit["parent_id"] for hit in direct_hits)),
                "direct_parent_ranking": direct_ranking,
                "direct_ranked_parent_ids": [entry["parent_id"] for entry in direct_ranking],
                "direct_selected_parent_ids": direct_selected,
            })
            if telemetry["original_query_reused_subquery_result"]:
                telemetry.update({
                    "original_query_executed": True,
                    "original_query_hits": direct_hits,
                    "original_retrieved_parent_ids": list(dict.fromkeys(hit["parent_id"] for hit in direct_hits)),
                    "original_parent_ranking": direct_ranking,
                    "original_ranked_parent_ids": [entry["parent_id"] for entry in direct_ranking],
                    "original_selected_parent_ids": direct_selected,
                })
        query_specs = []
        for group in angle_groups:
            for angle_name, model_focus in (
                ("Procedural", group["procedural"]),
                ("Policy", group["policy"]),
                ("Conceptual", group["conceptual"]),
            ):
                # A non-distinct or sentinel focus should not become a fake
                # duplicate angle. Keep the view out of RRF and make the
                # decision explicit for later analysis.
                normalized_model_focus = model_focus.strip() if isinstance(model_focus, str) else ""
                has_safe_model_focus = bool(normalized_model_focus) and normalized_model_focus.casefold() not in _EMPTY_RETRIEVAL_FOCUS_VALUES
                if not has_safe_model_focus:
                    telemetry["skipped_angle_views"].append({
                        "group_id": group["group_id"],
                        "angle": angle_name.lower(),
                        "model_retrieval_focus": normalized_model_focus,
                        "reason": "empty_or_sentinel_focus",
                    })
                    continue
                retrieval_focus = normalized_model_focus
                query_specs.append((
                    group["group_id"],
                    angle_name,
                    normalized_model_focus,
                    retrieval_focus,
                    "model",
                    self._compose_anchored_angle_query(telemetry["subquery"], retrieval_focus),
                    (
                        {"$and": [metadata_filter, {"angle": angle_name.lower()}]}
                        if self.has_tri_angle_schema
                        else metadata_filter
                    ),
                ))
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
            return ([], telemetry) if include_telemetry else []
        query_results = await asyncio.gather(*[
            self._query_single_angle(executed_query, metadata_filter)
            for _, _, _, _, _, executed_query, metadata_filter in query_specs
        ])
        ranked_query_results = [
            (query_specs[index][0], query_specs[index][1], hits)
            for index, hits in enumerate(query_results)
        ]
        telemetry["per_angle_hits"] = [
            {
                "group_id": group_id,
                "angle": angle_name.lower(),
                "model_retrieval_focus": model_focus,
                "retrieval_focus": retrieval_focus,
                "retrieval_focus_source": focus_source,
                "query": executed_query,
                "hits": hits,
            }
            for (group_id, angle_name, model_focus, retrieval_focus, focus_source, executed_query, _), (_, _, hits) in zip(query_specs, ranked_query_results)
        ]
        telemetry["all_angle_retrieved_parent_ids"] = list(dict.fromkeys(
            hit["parent_id"]
            for _, _, hits in ranked_query_results
            for hit in hits
        ))

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
            return ([], telemetry) if include_telemetry else []

        telemetry["rrf_parent_ranking"] = [
            {"parent_id": parent_id, "rrf_score": score}
            for parent_id, score in sorted_parents
        ]
        telemetry["all_rrf_ranked_parent_ids"] = [parent_id for parent_id, _ in sorted_parents]

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
        telemetry["selected_parent_ids"] = selected_parent_ids
        payload = self._hydrate_parents_from_sqlite(selected_parent_ids)
        
        execution_time = (time.time() - start_time) * 1000
        print(f" ⏱️  [RAG ENGINE] Total Execution Time: {execution_time:.2f} ms")
        print("═"*60 + "\n")
        
        return (payload, telemetry) if include_telemetry else payload
