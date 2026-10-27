"""Rescore the 5 Oct H1 run under the honest-limit rule (owner decision, 27 Oct 2026).

No bot can answer from content it does not have, so an honest "no information" or a
relevant clarification on a question, or part of one, that the FAQ cannot answer is
correct; only an invented, wrong or refused answer fails. This supersedes the strict
B0a/B0b/B3a/B10a in h1_summary.json, which stay on record. The release run is rescored
the same way in research/2026-10-15_final_release_regression/scripts/final_acceptance_review.py.

    python research/2026-10-04_v51_holdout/scripts/rescore_honest_limit.py
"""
import json
from collections import Counter
from pathlib import Path

D = Path(__file__).resolve().parents[1]
ITEMS = [json.loads(line) for line in (D / "results/h1_items.jsonl").read_text().splitlines()]

# Clarifications the owner's scoring rule accepts because a needed detail was missing
# ("Are you available in my country?", currencies without a product, "top off transfer";
# RESULTS_v5.1.md section 3.8).
MISSING_DETAIL_CLARIFICATIONS = {
    "h1_h1_country_support_3",
    "h1_h1_fiat_currency_support_4",
    "h1_h1_top_up_by_bank_transfer_charge_4",
}


def handled_correctly(x: dict) -> bool:
    if x["coverage"] == "FULL":
        return x["resolved"]
    wrong = x["substantive"] and x["b1_pass"] is False
    return not x["invented"] and not x["refused"] and not wrong


def rate(k: int, n: int) -> dict:
    return {"k": k, "n": n, "rate": round(k / n, 4)}


def clarification_reason(x: dict) -> str | None:
    if x["ambiguous"]:
        return "ambiguous_question"
    if x["case_id"] in MISSING_DETAIL_CLARIFICATIONS:
        return "missing_detail"
    if x["coverage"] == "NO_DATA":
        return "faq_cannot_answer"
    return None


e2e = [x for x in ITEMS if x["phase"] == "end_to_end" and not x["excluded_provider_failure"]]
partial = [x for x in e2e if x["coverage"] == "PARTIAL"]
correct = [x for x in e2e if handled_correctly(x)]
handed_over = [x for x in e2e if x["handover"] and not handled_correctly(x)]
clar = [x for x in ITEMS if x["clarified"]]
reasons = {x["case_id"]: clarification_reason(x) for x in clar}
assert (len(e2e), len(correct), len(partial), len(clar)) == (152, 125, 54, 14)

summary = {
    "rule": "Honest-limit rule (owner decision 27 Oct 2026); supersedes the strict B0a/B0b/B3a/B10a in h1_summary.json.",
    "B0a_end_to_end_correct_handling": rate(len(correct), len(e2e)),
    "B0b_correct_handling_or_handover": rate(len(correct) + len(handed_over), len(e2e)),
    "B3a_partial_without_invented_content": rate(sum(handled_correctly(x) for x in partial), len(partial)),
    "B10a_clarifications_justified": rate(sum(r is not None for r in reasons.values()), len(clar)),
    "B10a_strict_two_labeller_necessary": rate(sum(x["clar_necessary"] for x in clar), len(clar)),
    "B10a_reasons": dict(Counter(r or "not_justified" for r in reasons.values())),
    "B10a_not_justified": sorted(c for c, r in reasons.items() if r is None),
    "failures": sorted(x["case_id"] for x in e2e if not handled_correctly(x)),
}
(D / "results/h1_honest_limit_rescore.json").write_text(json.dumps(summary, indent=1) + "\n")
print(json.dumps({k: v for k, v in summary.items() if k != "failures"}, indent=1))
