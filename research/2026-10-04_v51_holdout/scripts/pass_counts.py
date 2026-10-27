"""Part D4: minimum pass counts, fixed before any reply is scored.

Decision rule: a target is demonstrated when the one-sided exact (Clopper-Pearson) 95% lower bound of the
pass rate is at least the target, i.e. Beta.ppf(0.05, k, n-k+1) >= target. The minimum pass count for n is
the smallest such k (None if even k = n fails). Zero-incident gates use the one-sided upper bound
1 - 0.05**(1/n). Outputs results/pass_counts.json: fixed denominators from the frozen labels, and the full
lookup table for n = 1..800 for metrics whose denominator depends on the replies (B1, C2, C3, ...).
"""
import json
from pathlib import Path

from scipy.stats import beta

D = Path(__file__).resolve().parents[1]
TARGETS = [0.70, 0.80, 0.90, 0.95, 0.98]


def lower(k, n):
    return 0.0 if k == 0 else float(beta.ppf(0.05, k, n - k + 1))


def min_pass(n, t):
    for k in range(n + 1):
        if lower(k, n) >= t:
            return k
    return None


lab = [json.loads(l) for l in open(D / "labels/labels_final.jsonl")]
non_faq = lambda r: any(x != "faq_answer" and x != "human_handover" for x in r["acceptable_routes"])  # noqa: E731
e2e = [r for r in lab if r["phase"] == "end_to_end"]
full = [r for r in lab if r["coverage"] == "FULL"]
avail_e2e = [r for r in e2e if r["coverage"] != "NO_DATA" or non_faq(r)]
topup_full = [r for r in lab if r["phase"] == "topup_pool" and r["coverage"] == "FULL"]
fixed = {
    "B0a_end_to_end": {"n": len(e2e), "target": None},
    "B2_complete_answers": {"n": len(full), "target": 0.80},
    "B3_partial_handling": {"n": sum(r["coverage"] == "PARTIAL" for r in e2e), "target": None},
    "B5b_right_channel": {"n": len(avail_e2e) + len(topup_full), "target": 0.90},
    "B10_ambiguous": {"n": sum(r["ambiguous"] for r in lab), "target": None},
    "C4_repeat_consistency": {"n": 20, "target": 0.90},
    "C5a_linguistic_groups": {"n": 12, "target": 0.80},
}
for m in fixed.values():
    if m["target"]:
        m["min_pass"] = min_pass(m["n"], m["target"])
        m["lower_bound_if_all_pass"] = round(lower(m["n"], m["n"]), 4)
table = {f"{int(t * 100)}%": {n: min_pass(n, t) for n in range(1, 801)} for t in TARGETS}
zero = {n: round(1 - 0.05 ** (1 / n), 4) for n in range(1, 801)}
json.dump({"rule": "min k with Beta.ppf(0.05, k, n-k+1) >= target", "fixed_denominators": fixed,
           "lookup": table, "zero_incident_upper_bound": zero}, open(D / "results/pass_counts.json", "w"), indent=1)
print(json.dumps(fixed, indent=1))
for n in (30, 50, 60, 75, 100, 150, 200, 300):
    print(n, [table[f"{int(t*100)}%"][n] for t in TARGETS])
