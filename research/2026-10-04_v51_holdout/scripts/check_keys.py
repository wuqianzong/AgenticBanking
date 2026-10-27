"""v5.1 Part C step 5: Labeller A (gemini-3.1-flash-lite) checks Labeller B's answer keys against the FAQ text.

Labeller A sees the question, the adjudicated coverage, the full text of the answering entries and the key.
It reports (a) key items the entries do not support and (b) essential facts in the entries that the key leaves out.
Rule fixed in advance: an item either labeller puts forward stays in the key, so A's missing facts are added;
A's unsupported flags are reviewed by B against the FAQ text and every change is logged.

  python scripts/check_keys.py <worker> <nworkers>
"""
import json
import sys
from pathlib import Path

D = Path(__file__).resolve().parents[1]
ROOT = D.parents[1]
sys.path.insert(0, str(ROOT / "research/2026-09-27_v4_measurement_judges/scripts"))
from judge_common import _db, fill_precision, judge  # noqa: E402

PROMPT = """You are checking an answer key for a Singapore digital bank's FAQ assistant. The key lists the facts a reply must contain to answer the customer's question completely. You see the question, the FAQ entries that answer it, and the key. You do NOT see any assistant reply.

Coverage label: {coverage}. FULL means the entries contain the whole answer, so the key must cover the whole answer. PARTIAL means the entries contain only part of the answer, so the key covers only the available part.

Return JSON only:
1. "reason": one or two sentences.
2. "unsupported": key items that the FAQ entries do not support, copied exactly ([] if none).
3. "missing": facts from the FAQ entries that a reply must include to answer this customer's actual need but that the key leaves out, each as one short sentence ([] if none). Do not list optional detail, nice-to-have tips or facts about other situations.

CUSTOMER QUESTION:
{question}

FAQ ENTRIES:
{entries}

ANSWER KEY:
{key}
"""


def main():
    worker, nworkers = (int(sys.argv[1]), int(sys.argv[2])) if len(sys.argv) > 2 else (0, 1)
    labels = {json.loads(l)["item_id"]: json.loads(l) for l in open(D / "labels/labels_final.jsonl")}
    keys = {}
    for l in open(D / "labels/answer_keys_B.jsonl"):
        r = json.loads(l)
        keys[r["item_id"]] = r["key_items"]
    con = _db
    text_of = {pid: fill_precision(c) for pid, c in con.execute("select parent_id, content from knowledge_parents")}
    done = set()
    for f in (D / "labels").glob("key_check_A*.jsonl"):
        done |= {json.loads(l)["item_id"] for l in open(f) if l.strip()}
    with open(D / f"labels/key_check_A_w{worker}.jsonl", "a") as out:
        for n, item in enumerate(sorted(keys)):
            if item in done or n % nworkers != worker:
                continue
            lab = labels[item]
            entries = "\n\n".join(f"[{pid}]\n{text_of[pid]}" for pid in lab["answering_faq_ids"] if pid in text_of)
            key = "\n".join(f"- {k}" for k in keys[item])
            v = judge(PROMPT.format(coverage=lab["coverage"], question=lab["text"], entries=entries, key=key), pace=2.0)
            if isinstance(v, list):
                v = v[0] if v and isinstance(v[0], dict) else {"raw": v}
            out.write(json.dumps({"item_id": item, **v}, ensure_ascii=False) + "\n")
            out.flush()
            print(item, len(v.get("unsupported") or []), len(v.get("missing") or []), flush=True)


if __name__ == "__main__":
    main()
