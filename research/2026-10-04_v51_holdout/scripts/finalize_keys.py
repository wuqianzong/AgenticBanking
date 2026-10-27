"""Merge Labeller B's answer keys with Labeller A's check into the frozen keys (design Part C step 5).

Primary key  = B's key, with B's corrections after reviewing A's "unsupported" flags (REMOVE below),
               plus every A "missing" fact that is a fact the customer's need requires (ACCEPT below).
               A additions not accepted carry a reason code:
                 GAP      a statement that information is missing; scored as gap disclosure (B3a), not as a key fact
                 OTHER    a fact about a different need, product or situation than the question asks
                 DETAIL   an optional detail, alternative channel or step-level elaboration of a key item
                 DUP      already in the key in other words
                 CONFLICT contradicted by another answering entry (the key keeps facts the entries agree on)
                 UNSUP    not stated in the answering entries
Strict key   = B's corrected key plus every A addition (no filtering); B2 is also reported on it as a sensitivity check.
"""
import glob
import json
from pathlib import Path

D = Path(__file__).resolve().parents[1]

# B's review of A's "unsupported" flags: (item, key item to remove, reason)
REMOVE = [
    ("item_002", "Customer can contact support to investigate a failed transfer", "not in the answering entries"),
    ("item_003", "Customer can contact support to investigate a declined transfer", "not in the answering entries"),
    ("item_022", "Contact support for a specific refund", "not in the answering entries"),
    ("item_044", "Contact support if a top-up is still missing", "not in the answering entries"),
    ("item_008", "Funding by external credit card is not listed", "gap statement; scored under B3a disclosure"),
    ("item_028", "Funding by American Express or Apple Pay is not listed", "gap statement; consistency with item_008"),
    ("item_260", "Topping up from an Amex card is not listed", "gap statement; consistency with item_008"),
    ("item_227", "Horizon does not list a cash deposit option", "gap statement; consistency with item_008"),
]
REWRITE = {  # item: (old key item, new key item, reason)
    "item_245": ("The first metal card is free, later replacements may incur a fee", "The first metal card is free",
                 "replacement fee conflicts between faq_34e4a4ab and faq_8336a2c7; key keeps the agreed fact"),
}
# A's "missing" facts accepted, by item and position in A's list (0-based); "all" accepts every one
ACCEPT = {
    "item_002": [0], "item_003": [0], "item_110": [0], "item_196": [0], "item_262": [0], "item_124": [1],
    "item_030": [0], "item_034": [0, 1], "item_074": [0, 1], "item_083": [0], "item_035": [0], "item_072": [0],
    "item_102": [0], "item_282": [0], "item_149": [0, 1], "item_235": [0, 2], "item_272": [0, 1], "item_309": [0],
}
MERGE = {"item_300": "Common reasons for declined transfers: insufficient funds, a blocked or closed savings account, or exceeding the daily limit"}
REJECT = {  # item: reason code per A fact, in A's order
    "item_009": ["OTHER", "OTHER", "OTHER"], "item_019": ["GAP"], "item_022": ["DUP"], "item_026": ["GAP"],
    "item_029": ["GAP", "GAP"], "item_030": [None, "DETAIL", "DETAIL"], "item_034": [None, None, "DETAIL"],
    "item_036": ["DUP"], "item_039": ["DUP"], "item_044": ["OTHER"], "item_052": ["DETAIL", "DETAIL", "OTHER"],
    "item_059": ["DETAIL", "DETAIL", "DETAIL"], "item_061": ["DETAIL", "DETAIL"], "item_064": ["OTHER", "OTHER", "OTHER"],
    "item_065": ["OTHER"] * 6, "item_075": ["OTHER"], "item_076": ["OTHER", "OTHER", "OTHER"], "item_087": ["DETAIL"],
    "item_093": ["DUP"], "item_099": ["CONFLICT"], "item_100": ["DUP"], "item_101": ["GAP"], "item_108": ["DETAIL"],
    "item_121": ["DETAIL"], "item_122": ["DETAIL"], "item_124": ["DETAIL", None], "item_137": ["DETAIL"],
    "item_139": ["DETAIL"], "item_145": ["GAP"], "item_146": ["DUP"], "item_153": ["OTHER"], "item_161": ["UNSUP", "DUP"],
    "item_163": ["OTHER", "OTHER"], "item_169": ["OTHER", "OTHER"], "item_174": ["DETAIL", "DETAIL"],
    "item_183": ["DETAIL"], "item_185": ["OTHER", "OTHER"], "item_193": ["DETAIL"], "item_201": ["UNSUP"],
    "item_208": ["DETAIL"], "item_210": ["OTHER", "OTHER"], "item_217": ["GAP", "GAP"], "item_222": ["DETAIL"],
    "item_235": [None, "OTHER", None, "DETAIL"], "item_237": ["OTHER"], "item_244": ["DETAIL", "DETAIL"],
    "item_245": ["CONFLICT"], "item_251": ["DETAIL"], "item_261": ["OTHER", "OTHER"], "item_264": ["CONFLICT"],
    "item_265": ["OTHER", "OTHER"], "item_268": ["DETAIL", "DETAIL"], "item_270": ["OTHER", "OTHER"],
    "item_278": ["DETAIL", "DETAIL"], "item_284": ["OTHER"], "item_288": ["DETAIL"], "item_291": ["DUP"],
    "item_294": ["GAP"], "item_299": ["GAP"], "item_300": ["MERGED"] * 4, "item_308": ["GAP"], "item_312": ["DETAIL"] * 3,
    "item_314": ["GAP"], "item_316": ["OTHER"],
}

keys = {}
for l in open(D / "labels/answer_keys_B.jsonl"):
    r = json.loads(l)
    keys[r["item_id"]] = list(r["key_items"])
A = {}
for f in glob.glob(str(D / "labels/key_check_A*.jsonl")):
    for l in open(f):
        r = json.loads(l)
        A[r["item_id"]] = r
assert set(A) == set(keys), "Labeller A check incomplete"

log = []
for item, text, why in REMOVE:
    keys[item].remove(text)
    log.append({"item_id": item, "action": "removed", "key_item": text, "reason": why})
for item, (old, new, why) in REWRITE.items():
    keys[item][keys[item].index(old)] = new
    log.append({"item_id": item, "action": "rewritten", "key_item": old, "new": new, "reason": why})
strict = {k: list(v) for k, v in keys.items()}
for item, r in A.items():
    miss = r.get("missing") or []
    strict[item] += miss
    acc = ACCEPT.get(item, [])
    codes = REJECT.get(item, [])
    for i, m in enumerate(miss):
        if i in acc:
            keys[item].append(m)
            log.append({"item_id": item, "action": "added_from_A", "key_item": m})
        else:
            code = codes[i] if i < len(codes) and codes[i] else None
            assert code, f"no decision for {item} #{i}: {m}"
            log.append({"item_id": item, "action": "A_addition_not_kept", "key_item": m, "reason": code})
    if item in MERGE:
        keys[item].append(MERGE[item])
        log.append({"item_id": item, "action": "added_from_A_merged", "key_item": MERGE[item]})
    for r2 in A[item].get("unsupported") or []:
        if item not in REWRITE and not any(x["item_id"] == item and x.get("key_item") == r2 for x in log):
            log.append({"item_id": item, "action": "A_unsupported_flag_rejected", "key_item": r2,
                        "reason": "the fact is stated in an answering entry"})

for name, data in (("answer_keys_final", keys), ("answer_keys_strict", strict)):
    with open(D / f"labels/{name}.jsonl", "w") as out:
        for k in sorted(data):
            assert data[k], f"empty key {k}"
            out.write(json.dumps({"item_id": k, "key_items": data[k]}, ensure_ascii=False) + "\n")
with open(D / "labels/key_decisions.jsonl", "w") as out:
    for x in log:
        out.write(json.dumps(x, ensure_ascii=False) + "\n")
from collections import Counter  # noqa: E402
print(Counter(x["action"] for x in log))
print(Counter(x["reason"] for x in log if x["action"] == "A_addition_not_kept"))
print("final key sizes", Counter(len(v) for v in keys.values()), "| strict mean", round(sum(map(len, strict.values())) / len(strict), 2),
      "| final mean", round(sum(map(len, keys.values())) / len(keys), 2))
