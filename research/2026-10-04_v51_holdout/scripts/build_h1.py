"""Build the v5.1 H1 BANKING77 holdout (docs/METRICS_DESIGN_v5.1.md, Part B1/B4).

1. Inventory every BANKING77-style message used in development (test split + every fixture).
2. Drop exact duplicates (case/whitespace normalised) from the training-split pool.
3. Drop near duplicates: cosine >= 0.95 to any development message (all-mpnet-base-v2).
4. Stratified random draw, seed 20261004: 4 per category; within each category the
   first 2 drawn form the end-to-end sample, the other 2 the FULL top-up pool.
"""
import csv, glob, hashlib, json, random, re, sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
D = Path(__file__).resolve().parents[1]
SEED = 20261004
NEAR = 0.95


def norm(t):
    return re.sub(r"\s+", " ", t.strip().lower())


train = list(csv.DictReader(open(D / "data/banking77_train.csv", newline="")))
dev = set()
for row in csv.DictReader(open(ROOT / "research/2026-08-19_retrieval_query_refinement/data/banking77_test.csv", newline="")):
    dev.add(row["text"])
for f in glob.glob(str(ROOT / "research/**/fixtures/*.jsonl"), recursive=True):
    if "2026-10-04_v51_holdout" in f:
        continue
    for line in open(f):
        try:
            c = json.loads(line)
        except Exception:
            continue
        for m in c.get("messages") or []:
            if isinstance(m, str) and m.strip():
                dev.add(m)
dev_norm = {norm(t) for t in dev}

pool = [dict(r, row_index=i) for i, r in enumerate(train)]
exact = [r for r in pool if norm(r["text"]) in dev_norm]
pool = [r for r in pool if norm(r["text"]) not in dev_norm]

from sentence_transformers import SentenceTransformer  # noqa: E402
import numpy as np  # noqa: E402

model = SentenceTransformer("sentence-transformers/all-mpnet-base-v2")
dev_list = sorted(dev)
E_dev = model.encode(dev_list, batch_size=128, normalize_embeddings=True, show_progress_bar=False)
E_pool = model.encode([r["text"] for r in pool], batch_size=128, normalize_embeddings=True, show_progress_bar=False)
max_sim = np.zeros(len(pool))
for s in range(0, len(pool), 1000):
    max_sim[s:s + 1000] = (E_pool[s:s + 1000] @ E_dev.T).max(axis=1)
near = [r for r, m in zip(pool, max_sim) if m >= NEAR]
pool = [dict(r, max_sim_to_dev=round(float(m), 4)) for r, m in zip(pool, max_sim) if m < NEAR]

pool_bytes = json.dumps([[r["row_index"], r["text"], r["category"]] for r in pool], ensure_ascii=False).encode()
by_cat = defaultdict(list)
for r in pool:
    by_cat[r["category"]].append(r)
rng = random.Random(SEED)
selected = []
for cat in sorted(by_cat):
    pick = rng.sample(by_cat[cat], 4)
    for k, r in enumerate(pick):
        selected.append({"h1_id": f"h1_{cat}_{k + 1}", "row_index": r["row_index"], "text": r["text"], "category": cat,
                         "phase": "end_to_end" if k < 2 else "topup_pool", "max_sim_to_dev": r["max_sim_to_dev"]})

manifest = {
    "source": "BANKING77 training split, https://raw.githubusercontent.com/PolyAI-LDN/task-specific-datasets/master/banking_data/train.csv",
    "licence": "CC BY 4.0 (Casanueva et al., 2020)",
    "train_csv_sha256": hashlib.sha256((D / "data/banking77_train.csv").read_bytes()).hexdigest(),
    "train_rows": len(train), "dev_messages_inventoried": len(dev),
    "excluded_exact_duplicates": len(exact), "excluded_near_duplicates": len(near), "near_threshold": NEAR,
    "embedding_model": "sentence-transformers/all-mpnet-base-v2",
    "candidate_pool_size": len(pool), "candidate_pool_sha256": hashlib.sha256(pool_bytes).hexdigest(),
    "seed": SEED, "per_category": 4, "categories": len(by_cat), "selected": len(selected),
}
json.dump(manifest, open(D / "data/h1_provenance.json", "w"), indent=1)
with open(D / "data/h1_selected.jsonl", "w") as f:
    for r in selected:
        f.write(json.dumps(r, ensure_ascii=False) + "\n")
with open(D / "data/h1_excluded_near_duplicates.jsonl", "w") as f:
    for r, in zip(near):
        f.write(json.dumps({"row_index": r["row_index"], "text": r["text"], "category": r["category"]}, ensure_ascii=False) + "\n")
print(json.dumps(manifest, indent=1))
