"""Self-consistency agreement (design Part C step 8): first pass vs re-label for each labeller."""
import glob
import json
from collections import Counter
from pathlib import Path

D = Path(__file__).resolve().parents[1]


def last(paths):
    d = {}
    for p in paths:
        for l in open(p):
            if l.strip():
                r = json.loads(l)
                d[r["item_id"]] = r
    return d


def norm(x):
    x = (x or "").upper().replace(" ", "_")
    return {"NONE": "NO_DATA", "NODATA": "NO_DATA"}.get(x, x)


def kappa(pairs, cats):
    n = len(pairs)
    po = sum(a == b for a, b in pairs) / n
    pa, pb = Counter(a for a, _ in pairs), Counter(b for _, b in pairs)
    pe = sum(pa[c] * pb[c] for c in cats) / (n * n)
    return (po - pe) / (1 - pe) if pe < 1 else 1.0


pick = json.load(open(D / "labels/selfcons_items.json"))
out = {}
for name, first, second in (("A", glob.glob(str(D / "labels/labels_A*.jsonl")), [D / "labels/selfcons_A.jsonl"]),
                            ("B", [D / "labels/labels_B.jsonl"], [D / "labels/selfcons_B.jsonl"])):
    f, s = last(first), last(second)
    pairs = [(norm(f[i]["coverage"]), norm(s[i]["coverage"])) for i in pick]
    agree = sum(a == b for a, b in pairs)
    out[name] = {"agreement": f"{agree}/{len(pairs)} = {agree / len(pairs):.1%}",
                 "kappa": round(kappa(pairs, ["FULL", "PARTIAL", "NO_DATA"]), 3),
                 "changes": [(i, a, b) for i, (a, b) in zip(pick, pairs) if a != b]}
json.dump(out, open(D / "results/selfcons_summary.json", "w"), indent=1)
print(json.dumps(out, indent=1))
