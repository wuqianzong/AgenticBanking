"""Derive the release-run metrics from the independent per-case rescore.

Single source: results/rescore_independent_v2.jsonl, written by an independent
AI reviewer (GPT-6.1 Sol) under RESCORE_BRIEF.md version 2. Every reported
release-run figure for B0a, B0b, B1, B2, B3a, B5b and C2, and the end-to-end
outcome split, comes from this script. No verdict is edited or carried over.

    python research/2026-10-15_final_release_regression/scripts/derive_metrics_from_rescore.py
"""
from __future__ import annotations

import json
from math import sqrt
from pathlib import Path

from scipy.stats import beta, norm

D = Path(__file__).resolve().parents[1]
ROWS = [json.loads(x) for x in (D / "results/rescore_independent_v2.jsonl").read_text().splitlines()]
PAIRED = [json.loads(x) for x in (D / "results/paired_response_review.jsonl").read_text().splitlines()]
OUT = D / "results/rescore_v2_metrics.json"


def cp_lower(k: int, n: int) -> float:
    return float(beta.ppf(0.05, k, n - k + 1)) if k else 0.0


def wilson(k: int, n: int) -> tuple[float, float]:
    z = norm.ppf(0.975)
    p = k / n
    centre = (p + z * z / (2 * n)) / (1 + z * z / n)
    half = z * sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / (1 + z * z / n)
    return round(centre - half, 4), round(centre + half, 4)


def metric(k: int, n: int, target: float | None = None) -> dict:
    out = {"k": k, "n": n, "rate": round(k / n, 4), "wilson95": wilson(k, n)}
    if target is not None:
        need = next((x for x in range(n + 1) if cp_lower(x, n) >= target), None)
        out |= {
            "target": target,
            "exact_lower95": round(cp_lower(k, n), 4),
            "minimum_for_demonstration": need,
            "decision": "demonstrated" if need is not None and k >= need
            else ("met on estimate" if k / n >= target else "not met"),
        }
    return out


def main() -> None:
    ids = [r["case_id"] for r in ROWS]
    assert len(ROWS) == 234 and len(set(ids)) == 234
    assert set(ids) == {p["case_id"] for p in PAIRED}
    assert not any(r.get("guard_false_block") for r in ROWS)
    for r in ROWS:
        if r["coverage"] == "FULL":
            assert r["handled_correctly"] == r["b2_complete"], r["case_id"]

    kept = [r for r in ROWS if not r["excluded"]]
    full = [r for r in ROWS if r["coverage"] == "FULL"]
    substantive = [r for r in kept if r["substantive"]]
    available = [r for r in kept if r["coverage"] != "NO_DATA"]
    e2e = [r for r in kept if r["phase"] == "end_to_end"]
    correct = [r for r in e2e if r["handled_correctly"]]
    offered = [r for r in e2e if not r["handled_correctly"] and r["person_offered"]]
    partial = [r for r in e2e if r["coverage"] == "PARTIAL"]
    no_data = [r for r in e2e if r["coverage"] == "NO_DATA"]

    split = {
        "resolved_full": sum(r["coverage"] == "FULL" and r["handled_correctly"] for r in e2e),
        "partly_answered": sum(r["coverage"] == "PARTIAL" and r["handled_correctly"] and bool(r["substantive"]) for r in e2e),
        "safely_limited_partial": sum(r["coverage"] == "PARTIAL" and r["handled_correctly"] and not r["substantive"] for r in e2e),
        "safely_limited_no_data": sum(r["coverage"] == "NO_DATA" and r["handled_correctly"] for r in e2e),
        "offered_person": len(offered),
        "failed_full": sum(r["coverage"] == "FULL" and not r["handled_correctly"] and not r["person_offered"] for r in e2e),
        "failed_partial": sum(r["coverage"] == "PARTIAL" and not r["handled_correctly"] and not r["person_offered"] for r in e2e),
    }
    assert sum(split.values()) == len(e2e)

    summary = {
        "source": "results/rescore_independent_v2.jsonl (GPT-6.1 Sol, RESCORE_BRIEF.md version 2, 29 Oct 2026)",
        "excluded": [r["case_id"] for r in ROWS if r["excluded"]],
        "metrics": {
            "B0a_end_to_end_correct": metric(len(correct), len(e2e)),
            "B0b_correct_or_person_offered": metric(len(correct) + len(offered), len(e2e)),
            "B1_correct_relevant": metric(sum(r["b1_pass"] for r in substantive), len(substantive), 0.90),
            "B2_complete": metric(sum(r["b2_complete"] for r in full), len(full), 0.80),
            "B3a_partial_handled": metric(sum(r["handled_correctly"] for r in partial), len(partial)),
            "B5b_suitable_channel": metric(sum(r["b5b_suitable"] for r in available), len(available), 0.90),
            "C2_fully_grounded": metric(sum(r["c2_pass"] for r in substantive), len(substantive), 0.95),
            "C3_no_data_honest": metric(sum(r["handled_correctly"] for r in no_data), len(no_data)),
        },
        "end_to_end_outcome_split": split,
        "end_to_end_full_resolved": {"k": split["resolved_full"], "n": sum(r["coverage"] == "FULL" for r in e2e)},
    }
    OUT.write_text(json.dumps(summary, indent=2) + "\n")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
