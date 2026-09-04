import json
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
# A parent typically owns about two child chunks in this corpus (a question
# anchor plus an answer chunk). Excluding N already-judged parents from a
# ranked child-level fetch effectively removes about 2N children worth of
# room, so widening the raw fetch by that much recovers roughly N genuinely
# new parents. This is a one-shot estimate, not a guarantee: an unusually
# high exclusion count against a thin corpus slice can still under-fill: an
# accepted, low-probability edge case rather than something worth an
# additional round-trip to Chroma to correct.
CHILDREN_PER_PARENT_ESTIMATE = 2


def _fetch_depth_for_exclusion(excluded_count: int) -> int:
    """One-shot child-level fetch depth that compensates for known exclusions."""
    return RRF_CANDIDATES_PER_ANGLE + (excluded_count * CHILDREN_PER_PARENT_ESTIMATE)


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

    @staticmethod
    def _exclude_rejected(ranked_parents: list[tuple[str, float]], excluded_ids: set[str], label: str) -> list[tuple[str, float]]:
        """Drop parents a prior sufficiency audit already judged not relevant.

        A widened retry filter can re-surface the exact same candidates the
        auditor already rejected last attempt. Left in, they can occupy a
        selection slot the widened scope was supposed to use for something
        genuinely new, silently crowding out the answer the expansion was
        meant to find. This never removes parents retained as partial
        evidence — only ones explicitly seen and rejected outright.
        """
        if not excluded_ids:
            return ranked_parents
        kept = [(parent_id, score) for parent_id, score in ranked_parents if parent_id not in excluded_ids]
        dropped = [parent_id for parent_id, _ in ranked_parents if parent_id in excluded_ids]
        if dropped:
            print(f"    [{label}] --- Excluding Previously-Rejected Parents ---")
            for parent_id in dropped:
                print(f"      🚫 '{parent_id}' already judged not relevant in an earlier attempt for this task; excluded from selection.")
        return kept

    def _select_by_dropoff(self, sorted_parents: list[tuple[str, float]], excluded_ids: set[str], max_parents: int, label: str) -> list[tuple[str, float]]:
        """Apply the 50%-drop-off + max-parents selection rule to one
        retrieval track's own RRF-sorted standings.

        Factored out so each track (see execute_multi_angle_search) is
        selected purely on its own scores, never re-ranked against another
        track's candidates first -- selection used to run once, after the
        direct query's score was already summed into the same total as the
        three semantic angles, which let a parent that ranked #1 on the
        literal query alone get diluted by the other three and then pruned
        by this exact rule. See RAG_RETRY_EFFORT_v1.0.md S8f.
        """
        sorted_parents = self._exclude_rejected(sorted_parents, excluded_ids, label)
        if not sorted_parents:
            return []

        print(f"\n    [RAG TELEMETRY] --- {label}: Final RRF Standings & Drop-Off Selection ---")
        print(f"      Formula: Score = average(group sum(1 / ({RRF_K_CONSTANT} + Rank)))")
        print("      Rule: stop when a score is less than 50% of the prior selected score.")
        print(f"      Limit: Max {max_parents} parents.")
        print("      " + "-" * 50)

        top_id, top_score = sorted_parents[0]
        selected = [(top_id, top_score)]
        previous_score = top_score
        print(f"      [Rank 1] Parent: '{top_id}' | Score: {top_score:.4f} | Ratio: N/A     -> ✅ AUTO-SELECTED")

        for i in range(1, len(sorted_parents)):
            current_id, current_score = sorted_parents[i]
            ratio = current_score / previous_score

            if len(selected) >= max_parents:
                print(f"      [Rank {i+1}] Parent: '{current_id}' | Score: {current_score:.4f} | Ratio: {ratio*100:5.1f}% -> ❌ DROPPED (Max Limit {max_parents} Reached)")
                continue

            if ratio < 0.50:
                print(f"      [Rank {i+1}] Parent: '{current_id}' | Score: {current_score:.4f} | Ratio: {ratio*100:5.1f}% -> ❌ DROPPED (Ratio < 50%)")
                print(f"      ✂️ --- Selection Boundary Triggered ---")
                for j in range(i + 1, len(sorted_parents)):
                    drop_id, drop_score = sorted_parents[j]
                    drop_ratio = drop_score / current_score
                    print(f"      [Rank {j+1}] Parent: '{drop_id}' | Score: {drop_score:.4f} | Ratio: {drop_ratio*100:5.1f}% -> ❌ DROPPED (Below Boundary)")
                    current_score = drop_score
                break
            else:
                print(f"      [Rank {i+1}] Parent: '{current_id}' | Score: {current_score:.4f} | Ratio: {ratio*100:5.1f}% -> ✅ SELECTED")
                selected.append((current_id, current_score))
                previous_score = current_score

        return selected

    def _hydrate_parents_from_sqlite(self, parent_ids: list) -> List[Dict[str, str]]:
        """Fetch full parent text while preserving the evidence identifier.

        Also surfaces each parent's own product_tags/intent_families --
        already stored in this table, but previously never selected, which
        left the sufficiency auditor with no way to independently verify a
        parent's real product/intent classification and instead relying on
        a rule telling it to simply trust node_02e's own pre-evidence
        routing choice. Surfacing the real, stored classification gives the
        auditor actual grounds for confirming a match (e.g. a parent's
        marketing name reads as "Instant Loan" but its own product_tags
        already states Personal_Loan) instead of a directive to stop
        doubting."""
        if not parent_ids:
            return []

        def _parse_tag_list(raw: Any) -> list[str]:
            if not raw:
                return []
            try:
                parsed = json.loads(raw)
                return parsed if isinstance(parsed, list) else [str(parsed)]
            except (TypeError, ValueError):
                return [str(raw)]

        cursor = sqlite_conn.cursor()
        placeholders = ','.join('?' for _ in parent_ids)
        query = f"SELECT parent_id, product_tags, intent_families, content FROM knowledge_parents WHERE parent_id IN ({placeholders})"
        cursor.execute(query, parent_ids)
        rows_by_id = {
            parent_id: (product_tags, intent_families, content)
            for parent_id, product_tags, intent_families, content in cursor.fetchall()
        }
        hydrated_payload = [
            {
                "parent_id": parent_id,
                "content": rows_by_id[parent_id][2],
                "product_tags": _parse_tag_list(rows_by_id[parent_id][0]),
                "intent_families": _parse_tag_list(rows_by_id[parent_id][1]),
            }
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

    async def execute_multi_angle_search(self, blueprint: dict, scope_pairs: list[dict], excluded_parent_ids: list[str] | None = None, direct_query: str = "", verified_session_parent_ids: list[str] | None = None) -> List[Dict[str, str]]:
        """Retrieve one task through two independent tracks, unioned before
        hydration -- never RRF-fused together.

        Track 1 (semantic): every angle group is an alternative semantic
        representation. Each group runs its procedural, policy, and
        conceptual searches against the same valid product/intent filter.
        RRF is first calculated inside each group, then equal-normalised
        across groups at task level. Selected independently via the 50%
        drop-off rule, up to MAX_PARENTS.

        Track 2 (direct): `direct_query` -- the task's own resolved-
        subquery text -- searched on its own, plus `verified_session_parent_ids`
        boosting (Search Strategy v2.8: parents already approved earlier
        this session, re-queried against `direct_query`, fused only into
        this track's own RRF sum). Selected independently via the same
        50% drop-off rule, up to its own MAX_PARENTS.

        These two tracks used to be RRF-summed together (`direct_query`
        folded in as a fourth angle of the base group -- Extra Effort v2.0
        Stage 1). Live-tracing a real failed turn showed this silently
        crowds out a genuinely strong literal-phrase match: summing four
        representations equally means a parent ranked #1 on the direct
        query alone, but absent from the top of the other three, gets a
        diluted combined score that the 50%-drop-off rule can then prune
        outright -- the same "top-K noise crowds out a correct match"
        failure this file already rejected once for narrow/full intent
        pooling, just not carried over here. Splitting the tracks means a
        literal match is judged purely on its own ranking, never averaged
        down by the semantic angles (or vice versa). See
        RAG_RETRY_EFFORT_v1.0.md S8f.

        Cost: one extra vector query per task (Track 2's own direct query
        already ran before; it's just no longer merged in-flight with
        Track 1). Zero additional LLM calls -- up to MAX_PARENTS from each
        track (so up to 2x MAX_PARENTS total, deduplicated) still reach the
        same single sufficiency-audit call, which already evaluates each
        supplied parent independently.
        """

        start_time = time.time() # Start Telemetry Timer

        # 📡 --- TELEMETRY: START SEARCH ---
        print("\n" + "═"*60)
        print(" 🔍 [RAG ENGINE v2.6] INITIALIZING SPLIT-TRACK RRF SEARCH")
        print(f" 📦 Valid Product/Intent Scope: {scope_pairs}")
        print("─"*60)

        angle_groups = self._normalize_angle_groups(blueprint)
        metadata_filter = self._build_metadata_filter(scope_pairs)
        excluded_ids = {pid for pid in (excluded_parent_ids or []) if pid}
        angle_fetch_top_k = _fetch_depth_for_exclusion(len(excluded_ids))

        # ============================================================
        # Track 1: semantic angle-group RRF (procedural/policy/conceptual)
        # ============================================================
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
        print(f" 📐 Angle groups: {len(angle_groups)} | Semantic vector searches: {len(query_specs)}")
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

        semantic_selected: list[tuple[str, float]] = []
        if not query_specs:
            print("    ⚠️ No valid semantic angles supplied for this task.")
        else:
            query_results = await asyncio.gather(*[
                self._query_single_angle(angle_text, metadata_filter, top_k=angle_fetch_top_k)
                for _, _, angle_text, metadata_filter in query_specs
            ])
            ranked_query_results = [
                (query_specs[index][0], query_specs[index][1], hits)
                for index, hits in enumerate(query_results)
            ]

            print("\n    [RAG TELEMETRY] --- Raw ChromaDB Hits per Semantic Angle ---")
            for group_id, angle_name, hits in ranked_query_results:
                print(f"      > {group_id} / {angle_name} Hits:")
                if not hits:
                    print("        None")
                for rank, hit in enumerate(hits):
                    print(f"        Rank {rank+1}: Parent '{hit['parent_id']}' | Cosine Sim: {hit['score']:.4f}")

            # Group RRF: fuse the three angle rankings within each group.
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

            # Task RRF: angle groups have equal normalised influence. This
            # prevents score inflation when feedback adds more semantic groups.
            print("\n    [RAG TELEMETRY] --- Equal-Normalised Task RRF (Semantic Track) ---")
            task_rrf_scores: Dict[str, float] = {}
            group_count = len(angle_groups)
            group_weight = 1.0 / group_count if group_count else 0.0
            for group_id, group_scores in group_rrf_scores.items():
                for pid, group_score in group_scores.items():
                    contribution = group_score * group_weight
                    task_rrf_scores[pid] = task_rrf_scores.get(pid, 0.0) + contribution
                    print(f"      + {contribution:.4f} task pts to '{pid}' ({group_id} group score {group_score:.4f} × {group_weight:.3f})")

            sorted_semantic = sorted(task_rrf_scores.items(), key=lambda item: item[1], reverse=True)
            semantic_selected = self._select_by_dropoff(sorted_semantic, excluded_ids, MAX_PARENTS, "SEMANTIC ANGLE-GROUP RRF")

        # ============================================================
        # Track 2: direct query + verified-session boosting
        # ============================================================
        clean_direct_query = str(direct_query or "").strip()
        deduped_session_ids = list(dict.fromkeys(
            pid for pid in (verified_session_parent_ids or []) if isinstance(pid, str) and pid
        ))
        direct_ranked_lists: list[tuple[str, list]] = []
        if clean_direct_query:
            # No angle-match restriction here, even under the tri-angle
            # schema: "direct" isn't one of the indexed per-chunk angle
            # tags (only procedural/policy/conceptual are), so filtering on
            # it would silently match nothing. Search the plain
            # product/intent filter unrestricted instead -- the same way
            # the now-retired standalone DIRECT retrieval mode always did.
            direct_hits = await self._query_single_angle(clean_direct_query, metadata_filter, top_k=angle_fetch_top_k)
            direct_ranked_lists.append(("Direct", direct_hits))
            if deduped_session_ids:
                # Re-query already-approved-this-session parents against the
                # resolved subquery, not the current vector metadata filter: a
                # previously verified parent's stored child metadata may be
                # narrower or stale even though the verified task scope was
                # valid (see the mechanism this generalizes, formerly
                # _execute_direct_search_with_verified_session_evidence).
                session_filter = {"parent_id": {"$in": deduped_session_ids}}
                session_top_k = max(RRF_CANDIDATES_PER_ANGLE, len(deduped_session_ids) * 4)
                session_hits = await self._query_single_angle(clean_direct_query, session_filter, top_k=session_top_k)
                if session_hits:
                    direct_ranked_lists.append(("VerifiedSession", session_hits))

        print("\n    [RAG TELEMETRY] --- Raw ChromaDB Hits: Direct Track ---")
        for source_label, hits in direct_ranked_lists:
            print(f"      > {source_label} Hits:")
            if not hits:
                print("        None")
            for rank, hit in enumerate(hits):
                print(f"        Rank {rank+1}: Parent '{hit['parent_id']}' | Cosine Sim: {hit['score']:.4f}")

        direct_scores: Dict[str, float] = {}
        for source_label, hit_list in direct_ranked_lists:
            seen_in_this_query = set()
            for rank, hit in enumerate(hit_list):
                pid = hit["parent_id"]
                if pid not in seen_in_this_query:
                    seen_in_this_query.add(pid)
                    rrf_points = 1.0 / (RRF_K_CONSTANT + (rank + 1))
                    direct_scores[pid] = direct_scores.get(pid, 0.0) + rrf_points
                    print(f"      + {rrf_points:.4f} pts to '{pid}' (from Direct Track / {source_label} Rank {rank+1})")

        sorted_direct = sorted(direct_scores.items(), key=lambda item: item[1], reverse=True)
        direct_selected = self._select_by_dropoff(sorted_direct, excluded_ids, MAX_PARENTS, "DIRECT TRACK") if sorted_direct else []

        if not semantic_selected and not direct_selected:
            print("    ⚠️ No results returned from ChromaDB on either track.")
            execution_time = (time.time() - start_time) * 1000
            print(f" ⏱️  [RAG ENGINE] Total Execution Time: {execution_time:.2f} ms")
            print("═"*60 + "\n")
            return []

        # ============================================================
        # Union: each track's own selection stands on its own merit --
        # never re-ranked against the other track's candidates.
        # ============================================================
        final_ids = list(dict.fromkeys(
            [pid for pid, _ in semantic_selected] + [pid for pid, _ in direct_selected]
        ))
        print(
            f"\n    📊 RRF Engine finalized {len(final_ids)} Parent(s) for LLM Hydration "
            f"({len(semantic_selected)} semantic + {len(direct_selected)} direct, "
            f"{len(final_ids)} after dedup): {final_ids}"
        )
        payload = self._hydrate_parents_from_sqlite(final_ids)

        execution_time = (time.time() - start_time) * 1000
        print(f" ⏱️  [RAG ENGINE] Total Execution Time: {execution_time:.2f} ms")
        print("═"*60 + "\n")

        return payload
