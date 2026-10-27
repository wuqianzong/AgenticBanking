"""v5.1 Part C, steps 1-3: candidate retrieval and Labeller A (Gemini judge) for H1 + calibration items.

Candidates per item, independent of the bot run and of the bot's own retriever:
  - 20 nearest FAQ parents by all-mpnet-base-v2 cosine over the parent text (precision values filled);
  - 10 best by Okapi BM25 (k1=1.5, b=0.75) over the same text, simple lower-case word tokens.
Items are shuffled (seed 20261004) under opaque ids so calibration items cannot be told apart.
Labeller A = gemini-3.1-flash-lite (a different model from the 3.5 bot), temperature 0, reasoning first.

  python research/2026-10-04_v51_holdout/scripts/labeller_a.py
"""
import json
import math
import random
import re
import sys
from collections import Counter
from pathlib import Path

D = Path(__file__).resolve().parents[1]
ROOT = D.parents[1]
sys.path.insert(0, str(ROOT / "research/2026-09-27_v4_measurement_judges/scripts"))
from judge_common import _db, fill_precision, judge  # noqa: E402

TOOLS = """Routes the assistant can use (several may be acceptable for one question):
- faq_answer: answer from the bank's FAQ content
- own_data_lookup:balance | own_data_lookup:address | own_data_lookup:loyalty_tier (shows the customer's own value)
- app_screen:Manage Card (freeze, replace, enable overseas use) | app_screen:View Statements | app_screen:Update Profile (name, address, phone, email) | app_screen:Security Settings (PIN, Horizon Key) | app_screen:Pay Bill (pay credit card from savings) | app_screen:Send Money (PayNow or FAST transfer)
- service_form:change_address | service_form:replace_card (worn or damaged, not lost or stolen) | service_form:kyc_resubmission
- human_handover: the customer needs a person (account-specific investigation the assistant cannot do)"""

PROMPT = """You are labelling an evaluation set for a Singapore digital bank's FAQ assistant. You see one customer question and candidate FAQ entries from the bank's knowledge base. You do NOT see any assistant reply.

Work in this order and return JSON only:
1. "reason": two or three sentences on what the customer actually needs and which candidates, if any, contain it.
2. "coverage": one of
   - "FULL": one entry, or two together, contain the whole answer to what the customer actually asked.
   - "PARTIAL": an entry contains part of the answer, or the answer for a closely related case, but not the whole answer.
   - "NO_DATA": no candidate contains anything that answers it.
   Judge the customer's real need, not keyword overlap.
3. "answering_faq_ids": ids of the entries that answer or partly answer it ([] for NO_DATA).
4. "ambiguous": true only if the question is genuinely ambiguous, so that a good assistant must ask a clarifying question before it can answer; otherwise false.
5. "acceptable_routes": every route below that would serve the need well.

{tools}

CUSTOMER QUESTION:
{question}

CANDIDATE FAQ ENTRIES:
{candidates}
"""

CALIBRATION = [
    ("FULL", "faq_14914805", "How can I quickly block my app if I think someone else is using it?"),
    ("FULL", "faq_8ce1bac1", "How do I get an extra credit card for my wife?"),
    ("FULL", "faq_f89201b7", "Are there any fees if I pay with a Visa instalment plan?"),
    ("FULL", "faq_2ba8c382", "I missed the payment on two of my cards, will I get charged two late fees?"),
    ("FULL", "faq_33f045af", "My card snapped in half, how do I get a new one?"),
    ("FULL", "faq_daff2a37", "How long does a credit card payment from my savings account take to go through?"),
    ("FULL", "faq_824007f2", "Why is my cashback tracker showing less than my total spending?"),
    ("FULL", "faq_4887f1cb", "Can I have Horizon Key on two phones at the same time?"),
    ("FULL", "faq_be976c33", "What is NFC and what does the bank use it for?"),
    ("FULL", "faq_69278ad5", "Which cards will I get if I apply for the cashback card together with a savings account?"),
    ("NO_DATA", "", "Can I rent a safe deposit box from you?"),
    ("NO_DATA", "", "My father passed away. How do I claim the money in his account?"),
    ("NO_DATA", "", "How much of an overdraft can I get on my account?"),
    ("NO_DATA", "", "How do I get a cashier's order?"),
    ("NO_DATA", "", "Can I buy gold bars from the bank?"),
    ("NO_DATA", "", "Can someone else manage my account under a power of attorney?"),
    ("NO_DATA", "", "I'm declaring bankruptcy. What happens to my account?"),
    ("NO_DATA", "", "Can you notarise a document for me?"),
    ("NO_DATA", "", "Can I set up a trust fund for my kids with you?"),
    ("NO_DATA", "", "Do you offer a will-writing service?"),
]


def tokens(text):
    return re.findall(r"[a-z0-9]+", text.lower())


def main():
    import judge_common
    worker, nworkers = (int(sys.argv[1]), int(sys.argv[2])) if len(sys.argv) > 2 else (0, 1)
    judge_common._key_index = worker * 2  # spread workers across the key pool
    faqs = [(pid, fill_precision(content)) for pid, content in _db.execute("select parent_id, content from knowledge_parents order by parent_id")]
    ids = [f[0] for f in faqs]
    texts = [f[1] for f in faqs]

    from sentence_transformers import SentenceTransformer
    import numpy as np
    model = SentenceTransformer("sentence-transformers/all-mpnet-base-v2")
    E = model.encode(texts, batch_size=64, normalize_embeddings=True, show_progress_bar=False)

    docs = [tokens(t) for t in texts]
    avgdl = sum(map(len, docs)) / len(docs)
    df = Counter(w for d in docs for w in set(d))
    N = len(docs)
    tf = [Counter(d) for d in docs]

    def bm25(query, k1=1.5, b=0.75):
        q = tokens(query)
        scores = []
        for i, d in enumerate(docs):
            s = 0.0
            for w in q:
                if w in tf[i]:
                    idf = math.log(1 + (N - df[w] + 0.5) / (df[w] + 0.5))
                    s += idf * tf[i][w] * (k1 + 1) / (tf[i][w] + k1 * (1 - b + b * len(d) / avgdl))
            scores.append(s)
        return scores

    items = [{"source": "h1", **json.loads(l)} for l in open(D / "data/h1_selected.jsonl")]
    items += [{"source": "calibration", "h1_id": f"cal_{i + 1:02d}", "text": q, "known_coverage": cov, "known_faq": fid}
              for i, (cov, fid, q) in enumerate(CALIBRATION)]
    rng = random.Random(20261004)
    rng.shuffle(items)
    for n, it in enumerate(items, 1):
        it["item_id"] = f"item_{n:03d}"

    out_c = open(D / "labels/candidates.jsonl", "w")
    for it in items:
        qe = model.encode([it["text"]], normalize_embeddings=True)[0]
        dense = [ids[i] for i in np.argsort(-(E @ qe))[:20]]
        sc = bm25(it["text"])
        sparse = [ids[i] for i in sorted(range(N), key=lambda i: -sc[i])[:10] if sc[i] > 0]
        it["candidates"] = list(dict.fromkeys(dense + sparse))
        out_c.write(json.dumps({k: it[k] for k in ("item_id", "h1_id", "source", "text", "candidates")}, ensure_ascii=False) + "\n")
    out_c.close()
    json.dump({it["item_id"]: {k: it.get(k) for k in ("h1_id", "source", "known_coverage", "known_faq")} for it in items},
              open(D / "labels/item_key.json", "w"), indent=1)

    text_of = dict(zip(ids, texts))
    done = set()
    for f in (D / "labels").glob("labels_A*.jsonl"):
        done |= {json.loads(l)["item_id"] for l in open(f) if l.strip()}
    out_path = D / ("labels/labels_A.jsonl" if nworkers == 1 else f"labels/labels_A_w{worker}.jsonl")
    with open(out_path, "a") as out:
        for n, it in enumerate(items):
            if it["item_id"] in done or n % nworkers != worker:
                continue
            cands = "\n\n".join(f"[{pid}]\n{text_of[pid][:900]}" for pid in it["candidates"])
            verdict = judge(PROMPT.format(tools=TOOLS, question=it["text"], candidates=cands), pace=2.0)
            if isinstance(verdict, list):  # the model occasionally wraps the object in a list
                verdict = verdict[0] if verdict and isinstance(verdict[0], dict) else {"coverage": None, "raw": verdict}
            out.write(json.dumps({"item_id": it["item_id"], **verdict}, ensure_ascii=False) + "\n")
            out.flush()
            print(it["item_id"], verdict.get("coverage"), flush=True)


if __name__ == "__main__":
    main()
