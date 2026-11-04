import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from backend.app.orchestration import answer_cache as A
from backend.app.orchestration import nodes as N


class FakeCollection:
    def __init__(self):
        self.rows = {}
        self.last_query = None

    def count(self):
        return len(self.rows)

    def get(self, ids=None):
        found = [doc_id for doc_id in (ids or self.rows) if doc_id in self.rows]
        return {
            "ids": found,
            "metadatas": [self.rows[doc_id][1] for doc_id in found],
        }

    def add(self, ids, documents, metadatas):
        for doc_id, document, metadata in zip(ids, documents, metadatas):
            self.rows[doc_id] = (document, dict(metadata))

    def update(self, ids, metadatas):
        for doc_id, metadata in zip(ids, metadatas):
            self.rows[doc_id] = (self.rows[doc_id][0], dict(metadata))

    def query(self, **kwargs):
        self.last_query = kwargs
        ids = list(self.rows)[: kwargs["n_results"]]
        return {
            "ids": [ids],
            "distances": [[0.0 for _ in ids]],
            "metadatas": [[self.rows[doc_id][1] for doc_id in ids]],
        }


class Cache(A.AnswerCache):
    def _current_faq_content_hashes(self, parent_ids):
        return {parent_id: f"hash-{parent_id}" for parent_id in parent_ids}


checks = []


def check(label, condition):
    checks.append(bool(condition))
    print("PASS" if condition else "FAIL", label)


collection = FakeCollection()
cache = Cache(collection)
check("recall-first default threshold is 0.65", A.DEFAULT_SIMILARITY_THRESHOLD == 0.65)
cache.record_outcome(
    query_text="resolved bank-transfer subquery",
    alternate_query_texts=["Yes", "resolved bank-transfer subquery"],
    approved_parent_ids=["faq_b", "faq_a"],
)

check("stores subquery and original representations per parent", len(collection.rows) == 4)
check(
    "new records are immediately active",
    all(metadata["status"] == A.STATUS_ACTIVE for _, metadata in collection.rows.values()),
)
check(
    "each record owns one parent",
    all(len(json.loads(metadata["parent_ids"])) == 1 for _, metadata in collection.rows.values()),
)

# Historical candidate records must remain searchable after removing the
# promotion gate; this also exercises filtering quarantine before top-N.
first_id = next(iter(collection.rows))
collection.rows[first_id][1]["status"] = "candidate"
hits = cache.lookup("resolved bank-transfer subquery")
check("legacy candidate is no longer blocked", any(hit["doc_id"] == first_id for hit in hits))
check(
    "quarantine filter is applied inside vector query",
    collection.last_query.get("where") == {"status": {"$ne": A.STATUS_QUARANTINED}},
)


class CaptureCache:
    def __init__(self):
        self.calls = []

    def record_outcome(self, **kwargs):
        self.calls.append(kwargs)


capture = CaptureCache()
old_get_cache = N.get_answer_cache
N.get_answer_cache = lambda: capture
try:
    N._record_answer_cache_outcomes({
        "user_input": "Yes",
        "search_tasks": [{"task_id": "task_1", "direct_query": "replace my stolen card"}],
        "task_results": [{"task_id": "task_1", "approved_parent_ids": ["faq_a"]}],
        "task_cache_injected_parent_ids": {},
    })
finally:
    N.get_answer_cache = old_get_cache

check("write uses resolved subquery as primary text", capture.calls[0]["query_text"] == "replace my stolen card")
check("write also records original customer wording", capture.calls[0]["alternate_query_texts"] == ["Yes"])

print(f"{sum(checks)}/{len(checks)}")
