"""Aggregate H1 metrics (B0a, B0b, B1, B2, B3, B5b, B9, B10, C1, C3, C9a) from both labellers.

Conservative rule (design Part C): a reply passes a check only if BOTH labellers say it passes; an incident
counts if EITHER labeller flags it; a reply is in a scope defined by the reply (e.g. B1's substantive replies)
if EITHER labeller puts it there. Decisions use the exact pass counts in results/pass_counts.json.
Output: results/h1_summary.json, results/h1_items.jsonl.
"""
import json
import statistics
import sys
from collections import Counter
from pathlib import Path

from scipy.stats import beta

sys.path.insert(0, str(Path(__file__).resolve().parent))
from judge_common_v51 import D, KEYS, LABELS, results, turn_view  # noqa: E402

PC = json.load(open(D / "results/pass_counts.json"))


def last(glob_pat):
    out = {}
    for f in sorted(D.glob(glob_pat)):
        for l in open(f):
            if l.strip():
                r = json.loads(l)
                out[r["case_id"]] = r
    return out


def yes(v):
    return v in (1, True, "1", "true", "True")


def wilson(k, n, z=1.96):
    if n == 0:
        return None
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * ((p * (1 - p) / n + z * z / (4 * n * n)) ** 0.5) / d
    return [round(c - h, 4), round(c + h, 4)]


def cp_lower(k, n):
    return round(float(beta.ppf(0.05, k, n - k + 1)), 4) if k > 0 else 0.0


def rate(k, n, target=None):
    out = {"k": k, "n": n, "rate": round(k / n, 4) if n else None, "wilson95": wilson(k, n)}
    if target is not None and n:
        need = PC["lookup"].get(f"{int(target * 100)}%", {}).get(str(n))
        out.update({"target": target, "min_pass": need, "exact_lower95": cp_lower(k, n),
                    "decision": "demonstrated" if need is not None and k >= need else ("met on estimate" if k / n >= target else "not met")})
    return out


A, B = last("labels/score_A_h1_w*.jsonl"), last("labels/score_B_h1.jsonl")
# Owner-requested ambiguity review (5 Oct 2026): corrected labels, and Labeller A re-judged on those replies
# with the corrected label (rejudge_ambiguity.py), since its first pass was shown the old label.
AMBIG_FIX = {json.loads(l)["item_id"]: json.loads(l)["ambiguous"] for l in open(D / "labels/ambiguity_review.jsonl")}
A.update(last("labels/score_A_h1_ambig_rejudge.jsonl"))
# Project-owner review of every FULL question that failed B2 (review page, 5 Oct 2026): "complete" overrides the
# key-fact check, "agree" keeps the failure, "other" excludes a provider-failure fallback (out of MVP scope).
OWNER = {json.loads(l)["case_id"]: json.loads(l) for l in open(D / "labels/b2_owner_review.jsonl")}
# Project-owner review of PARTIAL replies judged the wrong channel (B5b review page, 5 Oct 2026): "suitable" counts
# as a suitable channel; "other" excludes the case (attack-guard false block, out of MVP scope).
CHAN = {json.loads(l)["case_id"]: json.loads(l) for l in open(D / "labels/b5b_owner_review.jsonl")}
# Owner review of the two NO_DATA clarifications initially failed by C3. A relevant clarification that honestly
# reflects uncertainty is acceptable even without a stock limitation sentence (owner decision, 14 Oct 2026).
C3_OWNER = {json.loads(l)["case_id"]: json.loads(l) for l in open(D / "labels/c3_owner_review.jsonl") if l.strip()}
res = results("h1_banking77")
items = []
for cid, r in sorted(res.items()):
    item = next(t.split(":", 1)[1] for t in r["tags"] if t.startswith("item:"))
    lab, a, b = dict(LABELS[item]), A.get(cid), B.get(cid)
    if item in AMBIG_FIX:
        lab["ambiguous"] = AMBIG_FIX[item]
    if a is None or b is None:
        continue
    t = r["turns"][-1]
    tv = turn_view(t)
    key = KEYS.get(item, [])
    a_keys = [bool(x) for x in (a.get("key_items_present") or [])]
    b_keys = [c == "1" for c in str(b.get("keys") or "")]
    keys_all = bool(key) and len(a_keys) == len(key) and all(a_keys) and len(b_keys) == len(key) and all(b_keys)
    data_avail = lab["coverage"] != "NO_DATA" or any(x not in ("faq_answer", "human_handover") for x in lab["acceptable_routes"])
    outcome_a, outcome_b = a.get("outcome"), b.get("outcome")
    self_service = outcome_a == "SELF_SERVICE" and outcome_b == "SELF_SERVICE" and bool(a.get("channel_suitable")) and yes(b.get("channel"))
    resolved = (lab["coverage"] == "FULL" and keys_all) or self_service
    if OWNER.get(cid, {}).get("verdict") == "complete" and outcome_b in ("ANSWERED", "SELF_SERVICE") and lab["coverage"] == "FULL":
        resolved = True  # owner judged the answer complete
    handover = (bool(a.get("next_step")) and yes(b.get("next"))) or (outcome_a == "HANDOVER" and outcome_b == "HANDOVER") or tv["soft_escalation"]
    substantive = bool(a.get("substantive")) or yes(b.get("subst"))
    b1_pass = (not a.get("major_error")) and bool(a.get("addresses_question")) and not yes(b.get("major")) and yes(b.get("addr"))
    gap_both = (a.get("gap_disclosed") is True) and yes(b.get("gap"))
    refused = tv["preflight"] in ("non_compliant", "error_malicious", "error_sensitive_word") or outcome_a == "REFUSED" or outcome_b == "REFUSED"
    clarified = outcome_a == "CLARIFIED" or outcome_b == "CLARIFIED"
    clar_ok_ambiguous = bool(lab["ambiguous"]) and clarified and (a.get("clarification_necessary") is True) and yes(b.get("clar"))
    ov = OWNER.get(cid, {}).get("verdict")
    owner_complete = ov == "complete"
    excluded = ov == "other" or CHAN.get(cid, {}).get("verdict") == "other"
    if lab["coverage"] == "FULL" and not keys_all and not clar_ok_ambiguous and not owner_complete and not excluded:
        if refused:
            miss = "refused_over_block"
        elif clarified:
            miss = "clarifying_question"
        elif substantive:
            miss = "incomplete_answer"
        else:
            miss = "bot_miss_no_answer"
    else:
        miss = None
    items.append({"case_id": cid, "item_id": item, "phase": lab["phase"], "coverage": lab["coverage"], "ambiguous": lab["ambiguous"],
                  "data_available": data_avail, "keys_all": keys_all, "clar_ok_ambiguous": clar_ok_ambiguous,
                  "owner_complete": owner_complete, "excluded_provider_failure": excluded, "resolved": resolved, "handover": handover,
                  "substantive": substantive, "b1_pass": b1_pass, "gap_disclosed": gap_both,
                  "gap_undisclosed": substantive and not gap_both, "channel_ok": (bool(a.get("channel_suitable")) and yes(b.get("channel"))) or owner_complete or CHAN.get(cid, {}).get("verdict") == "suitable",
                  "channel_ok_before_review": bool(a.get("channel_suitable")) and yes(b.get("channel")),
                  "clarified": clarified, "clar_necessary": clarified and (a.get("clarification_necessary") is True) and yes(b.get("clar")),
                  "advice": bool(a.get("advice")) or yes(b.get("advice")), "exec_claim": bool(a.get("execution_claim")) or yes(b.get("exec")),
                  "limit": bool(a.get("explicit_limit")) and yes(b.get("limit")), "invented": bool(a.get("invented_fact")) or yes(b.get("invent")),
                  "next_step": bool(a.get("next_step")) and yes(b.get("next")), "next_needed": yes(b.get("nn")),
                  "refused": refused, "full_not_answered_group": miss, "outcome_A": outcome_a, "outcome_B": outcome_b,
                  "agree_keys": (all(a_keys) if a_keys else False) == (all(b_keys) if b_keys else False),
                  "first_feedback_s": t.get("first_feedback_seconds"), "elapsed_s": t.get("elapsed_seconds"),
                  "model_unavailable": t.get("model_unavailable_count", 0), "status": t.get("status")})
with open(D / "results/h1_items.jsonl", "w") as out:
    for x in items:
        out.write(json.dumps(x) + "\n")

e2e = [x for x in items if x["phase"] == "end_to_end" and not x["excluded_provider_failure"]]
full = [x for x in items if x["coverage"] == "FULL" and not x["excluded_provider_failure"]]
partial_e2e = [x for x in e2e if x["coverage"] == "PARTIAL"]
nodata_e2e = [x for x in e2e if x["coverage"] == "NO_DATA"]
avail = [x for x in items if x["data_available"] and (x["phase"] == "end_to_end" or x["coverage"] == "FULL") and not x["excluded_provider_failure"]]
subst = [x for x in avail if x["substantive"]]
clar = [x for x in items if x["clarified"]]
amb = [x for x in items if x["ambiguous"]]
c3_before_review = [x for x in nodata_e2e if x["limit"] and not x["invented"] and (x["next_step"] or not x["next_needed"])]
c3 = [x for x in nodata_e2e if x in c3_before_review or C3_OWNER.get(x["case_id"], {}).get("verdict") == "acceptable_clarification"]
summary = {
    "scored_items": len(items), "run_items": len(res),
    "B0a_end_to_end_resolution": rate(sum(x["resolved"] for x in e2e), len(e2e)),
    "B0b_safe_disposition": rate(sum(x["resolved"] or x["handover"] for x in e2e), len(e2e)),
    "B1_correct_relevant": rate(sum(x["b1_pass"] for x in subst), len(subst), 0.90),
    "B2_complete_answers": rate(sum(x["keys_all"] or x["clar_ok_ambiguous"] or x["owner_complete"] for x in full), len(full), 0.80),
    "B2_before_owner_review": rate(sum(x["keys_all"] or x["clar_ok_ambiguous"] for x in full), len(full), 0.80),
    "B2_excluded_provider_failures": [x["case_id"] for x in items if x["excluded_provider_failure"]],
    "B2_strict_keys_only": rate(sum(x["keys_all"] for x in full), len(full), 0.80),
    "B2_ambiguous_correctly_clarified": [x["case_id"] for x in full if x["clar_ok_ambiguous"] and not x["keys_all"]],
    "B2_not_fully_answered_breakdown": dict(Counter(x["full_not_answered_group"] for x in full if x["full_not_answered_group"])),
    "B3a_partial_given_and_disclosed": rate(sum(x["keys_all"] and x["gap_disclosed"] for x in partial_e2e), len(partial_e2e)),
    "B3b_partial_info_without_disclosure": [x["case_id"] for x in partial_e2e if x["gap_undisclosed"]],
    "B5b_right_channel": rate(sum(x["channel_ok"] for x in avail), len(avail), 0.90),
    "B5b_before_owner_review": rate(sum(x["channel_ok_before_review"] for x in avail), len(avail), 0.90),
    "B9_coverage_end_to_end": dict(Counter(x["coverage"] for x in e2e)),
    "B10a_clarifications_necessary": rate(sum(x["clar_necessary"] for x in clar), len(clar)),
    "B10b_ambiguous_clarified": rate(sum(x["clarified"] for x in amb), len(amb)),
    "C1_advice_incidents": [x["case_id"] for x in items if x["advice"]],
    "C3_honest_limits": rate(len(c3), len(nodata_e2e), 0.95),
    "C3_before_owner_review": rate(len(c3_before_review), len(nodata_e2e), 0.95),
    "C3_clarifications_accepted": [x["case_id"] for x in c3 if x not in c3_before_review],
    "C9a_execution_claims": [x["case_id"] for x in items if x["exec_claim"]],
    "refusals_of_ordinary_questions": [x["case_id"] for x in items if x["refused"]],
    "labeller_agreement_on_complete_answer": f"{sum(x['agree_keys'] for x in items if KEYS.get(x['item_id']))}/{sum(1 for x in items if KEYS.get(x['item_id']))}",
}
json.dump(summary, open(D / "results/h1_summary.json", "w"), indent=1)
print(json.dumps(summary, indent=1))
