"""Adjudicate coverage labels (design Part C, steps 6-9) and report agreement and calibration.

Rules, fixed in advance:
  coverage  : take the label that keeps the question in the bot's scored set (FULL > PARTIAL > NO_DATA)
  entries   : union of both labellers' answering entries
  ambiguous : true only if both labellers say true (a clarifying question is then allowed)
  routes    : intersection of both labellers' acceptable routes; if empty, Labeller B's routes (counted)
Labeller B: last line per item wins. Calibration items are scored, then removed.
"""
import glob
import json
import re
from collections import Counter
from pathlib import Path

D = Path(__file__).resolve().parents[1]
RANK = {"NO_DATA": 0, "PARTIAL": 1, "FULL": 2}


def norm_cov(x):
    x = (x or "").upper().replace(" ", "_")
    return {"NONE": "NO_DATA", "NODATA": "NO_DATA"}.get(x, x)


def norm_routes(rs):
    out = set()
    for r in rs or []:
        r = re.sub(r"\s*\(.*?\)", "", str(r)).strip()
        r = r.replace("own data lookup", "own_data_lookup").replace("app screen", "app_screen").replace("service form", "service_form")
        out.add(r.lower().replace(" ", "_"))
    return out


def kappa(pairs, cats):
    n = len(pairs)
    po = sum(a == b for a, b in pairs) / n
    pa = Counter(a for a, _ in pairs)
    pb = Counter(b for _, b in pairs)
    pe = sum(pa[c] * pb[c] for c in cats) / (n * n)
    return (po - pe) / (1 - pe) if pe < 1 else 1.0


key = json.load(open(D / "labels/item_key.json"))
A = {}
for f in glob.glob(str(D / "labels/labels_A*.jsonl")):
    for line in open(f):
        if line.strip():
            r = json.loads(line)
            A[r["item_id"]] = r
B = {}
for line in open(D / "labels/labels_B.jsonl"):
    r = json.loads(line)
    B[r["item_id"]] = r
missing = [k for k in key if k not in A or k not in B]
assert not missing, f"missing labels: {missing[:5]}"

cal = [(k, key[k]["known_coverage"], norm_cov(A[k].get("coverage"))) for k in key if key[k]["source"] == "calibration"]
cal_acc = sum(kc == ac for _, kc, ac in cal)
pairs, final, disagreements, route_fallback = [], [], [], 0
for k, meta in key.items():
    if meta["source"] != "h1":
        continue
    a, b = A[k], B[k]
    ca, cb = norm_cov(a.get("coverage")), norm_cov(b["coverage"])
    pairs.append((ca, cb))
    cov = ca if RANK.get(ca, -1) >= RANK[cb] else cb
    ra, rb = norm_routes(a.get("acceptable_routes")), norm_routes(b["acceptable_routes"])
    routes = ra & rb
    if not routes:
        routes, route_fallback = rb, route_fallback + 1
    rec = {"item_id": k, "h1_id": meta["h1_id"], "coverage": cov, "coverage_A": ca, "coverage_B": cb,
           "answering_faq_ids": sorted(set(a.get("answering_faq_ids") or []) | set(b["answering_faq_ids"])),
           "ambiguous": bool(a.get("ambiguous")) and bool(b["ambiguous"]),
           "ambiguous_A": bool(a.get("ambiguous")), "ambiguous_B": bool(b["ambiguous"]),
           "acceptable_routes": sorted(routes), "reason_A": a.get("reason", ""), "reason_B": b["reason"]}
    final.append(rec)
    if ca != cb:
        disagreements.append({k: (ca, cb)})

h1 = {json.loads(l)["h1_id"]: json.loads(l) for l in open(D / "data/h1_selected.jsonl")}
for r in final:
    r["phase"] = h1[r["h1_id"]]["phase"]
    r["text"] = h1[r["h1_id"]]["text"]
    r["category"] = h1[r["h1_id"]]["category"]
with open(D / "labels/labels_final.jsonl", "w") as out:
    for r in final:
        out.write(json.dumps(r, ensure_ascii=False) + "\n")

agree = sum(a == b for a, b in pairs)
amb_pairs = [(r["ambiguous_A"], r["ambiguous_B"]) for r in final]
summary = {
    "h1_items": len(final),
    "coverage_agreement": f"{agree}/{len(pairs)} = {agree / len(pairs):.1%}",
    "coverage_kappa": round(kappa(pairs, ["FULL", "PARTIAL", "NO_DATA"]), 3),
    "confusion_A_vs_B": {f"{a}|{b}": n for (a, b), n in sorted(Counter(pairs).items())},
    "ambiguity_agreement": f"{sum(a == b for a, b in amb_pairs)}/{len(amb_pairs)}",
    "ambiguity_kappa": round(kappa(amb_pairs, [True, False]), 3),
    "route_fallbacks_to_B": route_fallback,
    "calibration_accuracy_A": f"{cal_acc}/{len(cal)}",
    "calibration_misses_A": [(k, kc, ac) for k, kc, ac in cal if kc != ac],
    "final_coverage_all": dict(Counter(r["coverage"] for r in final)),
    "final_coverage_end_to_end": dict(Counter(r["coverage"] for r in final if r["phase"] == "end_to_end")),
    "final_coverage_topup_pool": dict(Counter(r["coverage"] for r in final if r["phase"] == "topup_pool")),
    "final_ambiguous": sum(r["ambiguous"] for r in final),
}
json.dump(summary, open(D / "labels/adjudication_summary.json", "w"), indent=1)
print(json.dumps(summary, indent=1))
