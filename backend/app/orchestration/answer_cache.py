"""Cross-session subquery answer cache -- a Fairness/consistency mitigation.

Motivation (see docs/KNOWN_ISSUES_TRACKER.md BUG-023, BUG-025 and
docs/FAIRNESS_ANSWER_CACHE_DESIGN.md for the full design rationale): two
BANKING77 studies found that near-identical, and even byte-identical,
customer questions do not reliably retrieve the same evidence and reach
the same outcome. Root-cause tracing showed at least part of the gap is
retrieval-side: the same intent can surface a different, incomplete
candidate pool on different runs (a different LLM-generated search angle,
a different intent/product mapping, or a different retrieval round).

This module does NOT try to guess the answer for the customer, and it
never bypasses the sufficiency auditor. It only ensures that a parent id
which has, historically, resolved a sufficiently similar question is at
least *offered as a candidate* to this turn's own independent sufficiency
audit -- the audit is always the final word on whether that candidate is
actually relevant to THIS request. This is deliberately a low-risk
design: a bad cache suggestion costs nothing but one extra candidate for
the auditor to (correctly) reject; it is never served directly.

Two operations:
  - lookup(query_text): read path, called before the sufficiency audit,
    to gather additional candidate parent ids.
  - record_outcome(query_text, approved_parent_ids, surfaced_doc_ids):
    write path, called only after a turn's OWN outbound compliance/
    groundedness audit has passed -- a stronger bar than the retrieval-
    level task status alone, chosen specifically because BUG-015 showed
    that task-level SUFFICIENT/RELEVANT can be wrong even when the
    customer-facing turn actually failed.

Safety properties, all deliberately conservative:
  - PROMOTION_CONSENSUS_THRESHOLD: a (text, parent-id-set) pairing must be
    independently confirmed this many times before it is ever surfaced.
    A single degraded/lenient approval can never alone seed the cache --
    it needs to recur, which a one-off mislabeling essentially can't.
  - Rolling rejection-rate quarantine: every cache-suggested candidate's
    surfaced/rejected counts are tracked separately from organically
    retrieved candidates (see nodes.py's task_cache_injected_parent_ids).
    An entry whose own suggestions keep getting rejected by fresh,
    independent audits is automatically quarantined, regardless of how it
    was promoted.
  - Content-hash staleness check on every lookup: a parent id is dropped
    from a cache hit the moment the underlying FAQ content changes, so a
    superseded policy answer can never be served from a stale cache entry
    even before a maintenance pass catches it.
"""
from __future__ import annotations

import hashlib
import json
import time
from typing import Any

from backend.app.orchestration.rag_engine import chroma_client, sqlite_conn

COLLECTION_NAME = "subquery_answer_cache"

# Chosen deliberately looser than a typical bypass-style semantic cache
# would tolerate, because a bad hit here only ever reaches the sufficiency
# auditor as one extra candidate -- it is never served to the customer
# directly. See docs/FAIRNESS_ANSWER_CACHE_DESIGN.md S2 for the
# precision/recall tradeoff discussion and the empirical band data
# (research/2026-09-05_banking77_full_coverage/reports/fairness_cosine_consistency.md)
# this threshold was chosen against.
DEFAULT_SIMILARITY_THRESHOLD = 0.85
DEFAULT_TOP_K = 3

# A (text, parent-id-set) pairing must recur this many times, confirmed by
# an outbound-audit-passed turn each time, before it is ever surfaced.
PROMOTION_CONSENSUS_THRESHOLD = 2

# Quarantine an entry once its own cache-suggested candidates have been
# rejected at least this fraction of the time, but only once it has been
# surfaced enough times for that ratio to be meaningful -- a single early
# rejection must not be enough to quarantine an otherwise-good entry.
QUARANTINE_MIN_SURFACED = 5
QUARANTINE_REJECTION_RATE = 0.35

STATUS_CANDIDATE = "candidate"
STATUS_ELIGIBLE = "eligible"
STATUS_QUARANTINED = "quarantined"


def _normalize_text(text: str) -> str:
    return " ".join(str(text or "").strip().lower().split())


def _doc_id(normalized_text: str, parent_ids: list[str]) -> str:
    text_hash = hashlib.sha1(normalized_text.encode("utf-8")).hexdigest()[:16]
    parent_hash = hashlib.sha1("|".join(sorted(parent_ids)).encode("utf-8")).hexdigest()[:12]
    return f"{text_hash}_{parent_hash}"


def _content_hash(content: str) -> str:
    return hashlib.sha1(str(content or "").encode("utf-8")).hexdigest()[:16]


class AnswerCache:
    def __init__(self, collection=None):
        self.collection = collection if collection is not None else self._get_or_create_collection()

    @staticmethod
    def _get_or_create_collection():
        return chroma_client.get_or_create_collection(
            name=COLLECTION_NAME,
            metadata={"hnsw:space": "cosine"},
        )

    def _current_faq_content_hashes(self, parent_ids: list[str]) -> dict[str, str]:
        if not parent_ids:
            return {}
        cursor = sqlite_conn.cursor()
        placeholders = ",".join("?" for _ in parent_ids)
        cursor.execute(
            f"SELECT parent_id, content FROM knowledge_parents WHERE parent_id IN ({placeholders})",
            parent_ids,
        )
        return {parent_id: _content_hash(content) for parent_id, content in cursor.fetchall()}

    # ------------------------------------------------------------------
    # Read path
    # ------------------------------------------------------------------
    def lookup(
        self,
        query_text: str,
        top_k: int = DEFAULT_TOP_K,
        similarity_threshold: float = DEFAULT_SIMILARITY_THRESHOLD,
    ) -> list[dict[str, Any]]:
        """Return up to `top_k` eligible cache entries close to `query_text`.

        Each hit is {"doc_id", "parent_ids", "similarity"}. Only entries
        with status ELIGIBLE are ever returned -- CANDIDATE entries are
        still accumulating consensus and QUARANTINED entries have shown a
        real rejection pattern. Any parent id whose underlying FAQ content
        has changed since this entry was last written is dropped from the
        hit inline, never served stale.
        """
        normalized = _normalize_text(query_text)
        if not normalized:
            return []

        count = self.collection.count()
        if count == 0:
            return []

        results = self.collection.query(
            query_texts=[normalized],
            n_results=min(top_k * 4, count),  # over-fetch; filtered below by status/threshold
        )
        if not results or not results.get("ids") or not results["ids"][0]:
            return []

        hits: list[dict[str, Any]] = []
        for i, doc_id in enumerate(results["ids"][0]):
            raw_distance = results["distances"][0][i]
            similarity = 1.0 - raw_distance if raw_distance <= 1.0 else 0.0
            if similarity < similarity_threshold:
                continue
            metadata = results["metadatas"][0][i] or {}
            if metadata.get("status") != STATUS_ELIGIBLE:
                continue

            stored_parent_ids: list[str] = json.loads(metadata.get("parent_ids", "[]"))
            stored_hashes: dict[str, str] = json.loads(metadata.get("faq_content_hashes", "{}"))
            current_hashes = self._current_faq_content_hashes(stored_parent_ids)
            live_parent_ids = [
                pid for pid in stored_parent_ids
                if pid in current_hashes and current_hashes[pid] == stored_hashes.get(pid)
            ]
            if not live_parent_ids:
                # Every parent this entry ever pointed to has since changed
                # or been removed -- nothing safe left to surface. Flag it
                # for the next maintenance pass rather than silently
                # leaving a permanently-dead entry in the index.
                self._quarantine(doc_id, reason="all_parents_stale")
                continue
            if len(live_parent_ids) < len(stored_parent_ids):
                self._quarantine(doc_id, reason="partial_parent_staleness")
                continue

            hits.append({"doc_id": doc_id, "parent_ids": live_parent_ids, "similarity": similarity})
            if len(hits) >= top_k:
                break

        return hits

    # ------------------------------------------------------------------
    # Write path
    # ------------------------------------------------------------------
    def record_outcome(
        self,
        query_text: str,
        approved_parent_ids: list[str],
        surfaced_doc_ids: list[str] | None = None,
    ) -> None:
        """Record one outbound-audit-passed turn's outcome for a task.

        Two independent effects:
          1. Every doc_id this turn's candidate pool was drawn from (via
             `lookup`, tracked by the caller in
             `task_cache_injected_parent_ids`) gets its surfaced/rejected
             counters updated -- rejected if none of that doc's own
             parent ids made it into this turn's final approved set.
          2. If this turn itself resolved with a non-empty approved set,
             that (text, approved-parent-set) pairing's consensus count is
             incremented (or a new candidate entry created at count 1),
             independent of whether the cache assisted this turn at all --
             this is how the cache keeps learning from ordinary,
             non-cache-assisted traffic, not just from its own hits.
        """
        approved = sorted({pid for pid in (approved_parent_ids or []) if pid})

        for doc_id in surfaced_doc_ids or []:
            self._record_surfacing(doc_id, approved)

        if not approved:
            return

        normalized = _normalize_text(query_text)
        if not normalized:
            return

        doc_id = _doc_id(normalized, approved)
        existing = self.collection.get(ids=[doc_id])
        now = time.time()

        if existing and existing.get("ids"):
            metadata = existing["metadatas"][0]
            consensus_count = int(metadata.get("consensus_count", 0)) + 1
            status = metadata.get("status", STATUS_CANDIDATE)
            if status == STATUS_CANDIDATE and consensus_count >= PROMOTION_CONSENSUS_THRESHOLD:
                status = STATUS_ELIGIBLE
            metadata.update({
                "consensus_count": consensus_count,
                "status": status,
                "faq_content_hashes": json.dumps(self._current_faq_content_hashes(approved)),
                "last_updated": now,
            })
            self.collection.update(ids=[doc_id], metadatas=[metadata])
        else:
            initial_status = STATUS_CANDIDATE if PROMOTION_CONSENSUS_THRESHOLD > 1 else STATUS_ELIGIBLE
            self.collection.add(
                ids=[doc_id],
                documents=[normalized],
                metadatas=[{
                    "parent_ids": json.dumps(approved),
                    "consensus_count": 1,
                    "surfaced_count": 0,
                    "rejected_count": 0,
                    "status": initial_status,
                    "faq_content_hashes": json.dumps(self._current_faq_content_hashes(approved)),
                    "first_seen": now,
                    "last_updated": now,
                }],
            )

    def _record_surfacing(self, doc_id: str, approved_parent_ids: list[str]) -> None:
        existing = self.collection.get(ids=[doc_id])
        if not existing or not existing.get("ids"):
            return
        metadata = existing["metadatas"][0]
        stored_parent_ids = set(json.loads(metadata.get("parent_ids", "[]")))
        was_used = bool(stored_parent_ids & set(approved_parent_ids))

        surfaced_count = int(metadata.get("surfaced_count", 0)) + 1
        rejected_count = int(metadata.get("rejected_count", 0)) + (0 if was_used else 1)
        metadata["surfaced_count"] = surfaced_count
        metadata["rejected_count"] = rejected_count

        if (
            metadata.get("status") != STATUS_QUARANTINED
            and surfaced_count >= QUARANTINE_MIN_SURFACED
            and rejected_count / surfaced_count >= QUARANTINE_REJECTION_RATE
        ):
            metadata["status"] = STATUS_QUARANTINED
            metadata["quarantine_reason"] = "rejection_rate"

        self.collection.update(ids=[doc_id], metadatas=[metadata])

    def _quarantine(self, doc_id: str, reason: str) -> None:
        existing = self.collection.get(ids=[doc_id])
        if not existing or not existing.get("ids"):
            return
        metadata = existing["metadatas"][0]
        if metadata.get("status") == STATUS_QUARANTINED:
            return
        metadata["status"] = STATUS_QUARANTINED
        metadata["quarantine_reason"] = reason
        self.collection.update(ids=[doc_id], metadatas=[metadata])

    # ------------------------------------------------------------------
    # Maintenance (run out-of-band, e.g. a periodic job -- not on the hot path)
    # ------------------------------------------------------------------
    def validate_and_prune(self) -> dict[str, int]:
        """Full-cache staleness sweep: re-check every entry's stored FAQ
        content hashes against the live knowledge base and quarantine any
        entry pointing at content that has since changed. `lookup` already
        does this inline for whatever it happens to touch; this is the
        exhaustive version for entries that haven't been queried recently."""
        all_docs = self.collection.get()
        checked = 0
        quarantined = 0
        for doc_id, metadata in zip(all_docs.get("ids", []), all_docs.get("metadatas", [])):
            if metadata.get("status") == STATUS_QUARANTINED:
                continue
            checked += 1
            stored_parent_ids: list[str] = json.loads(metadata.get("parent_ids", "[]"))
            stored_hashes: dict[str, str] = json.loads(metadata.get("faq_content_hashes", "{}"))
            current_hashes = self._current_faq_content_hashes(stored_parent_ids)
            if any(current_hashes.get(pid) != stored_hashes.get(pid) for pid in stored_parent_ids):
                self._quarantine(doc_id, reason="maintenance_sweep_stale")
                quarantined += 1
        return {"checked": checked, "quarantined": quarantined}


_default_cache: AnswerCache | None = None


def get_answer_cache() -> AnswerCache:
    global _default_cache
    if _default_cache is None:
        _default_cache = AnswerCache()
    return _default_cache
