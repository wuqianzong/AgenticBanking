"""Build the 'final run open items' round of the B5b review page: the B5b failures of the 15 Oct final run that
still need an owner decision, plus failed_transfer_2 (decided 16 Oct) for the record. Per case: question, labels,
answer key, answering FAQ text, the 15 Oct reply and full pipeline trace, earlier verdicts, Claude's analysis,
and (where it exists) the reply on the fixed code from the 16 Oct pre-commit check.
python scripts/build_final_b5b_data.py <out.json>"""
import glob
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import build_review_data as R  # noqa: E402
from judge_common_v51 import D, FAQ, KEYS, LABELS, turn_view  # noqa: E402

F = D.parent / "2026-10-15_final_release_regression"
PRE = D.parent / "2026-10-16_precommit_check"

CASES = {
    "h1_h1_Refund_not_showing_up_1": (
        "Own case: the bot cannot check a supplier refund. The only FAQ explains where a refund lands (the original "
        "card if the purchase was pending, otherwise the currently paired card) and is written for card-pairing "
        "transactions. Useful background, but it does not answer 'where is my money'. Claude leans: no information "
        "is enough."),
    "h1_h1_Refund_not_showing_up_2": (
        "Same FAQ as Refund_not_showing_up_1. Here the pending/paired-card rule could explain why the customer cannot "
        "see the refund (it may have gone to another card), which is the strongest reason to keep this a failure. "
        "Claude leans: the FAQ part should have been given."),
    "h1_h1_why_verify_identity_1": (
        "The FAQ explains MyInfo (it fills in your details at sign-up) and in-person verification, but never says "
        "what identity checks are for. 'No information on the function of the identity check' is arguably true. "
        "Coverage is PARTIAL. Claude leans: no information is enough."),
    "h1_h1_contactless_not_working_1": (
        "The only 'do not uninstall unless Customer Care advises' rule is in an FAQ about closing the app during an "
        "account application. The question gives no context, so that condition may not apply. Claude leans: the "
        "FAQ rule was still worth giving with its condition stated, so the failure stays."),
    "h1_h1_failed_transfer_2": (
        "Failed only because a malformed fact-check reply (BUG-070) exhausted the revision rounds and produced the "
        "generic fallback. On the fixed code it says it has no information on the reason for this customer's "
        "transfer failure. Decided by the owner in chat on 16 Oct: correct, because it is the customer's own case "
        "and the bot has no route to a person on a first turn."),
}


def latest(path_glob):
    out = {}
    for f in sorted(glob.glob(str(path_glob))):
        for l in open(f):
            r = json.loads(l)
            if r.get("status") == "OK" or r["case_id"] not in out:
                out[r["case_id"]] = r
    return out


def full_trace(r, ansset):
    """The same step list as the 5 Oct round (build_review_data), applied to the final run."""
    steps, retrieved, approved, rounds = [], set(), set(), 0
    for e in r.get("system_log_events") or []:
        n = e.get("event")
        if n == "inbound.prompt_guard.checked":
            steps.append({"stage": "Attack guard", "text": f"{e.get('verdict')} (risk score {float(e.get('score', 0)):.4f}; blocks at 0.10)"})
        elif n == "inbound.compliance.checked":
            steps.append({"stage": "Compliance screen", "text": f"{e.get('screen_verdict')}; area: {e.get('compliance_area')}. Predicted request: {e.get('requested_from_horizon', '')}",
                          "bad": e.get("screen_verdict") == "no_compliant_route"})
        elif n == "triage.financial_boundary":
            steps.append({"stage": "Triage", "text": f"{e.get('triage_status')}; clarification needed: {e.get('clarification_needed')}"})
        elif n == "tool.task.created":
            steps.append({"stage": "Tool plan", "text": f"{e.get('tool_name')} / {e.get('operation')} (round {e.get('planning_round')})"})
        elif n == "task.scope_rejected":
            steps.append({"stage": "Search formulation", "text": "scope rejected: no search was run", "bad": True})
        elif n == "task.expansion.applied":
            steps.append({"stage": "Search widened", "text": f"{e.get('stage')}: products {e.get('old_product')} → {e.get('new_product')}"})
        elif n == "task.rag.completed":
            rounds += 1
            sel = R.P(e.get("selected_parent_ids")) or {}
            ids = [i for v in (sel.values() if isinstance(sel, dict) else []) for i in v]
            retrieved |= set(ids)
            steps.append({"stage": f"Retrieval round {rounds}", "ids": [{"id": i, "title": R.title(i), "answering": i in ansset} for i in ids],
                          "text": f"{len(ids)} FAQ entries found; answering entries among them: {len(set(ids) & ansset)}"})
        elif n in ("task.retry", "task.clarification_required", "task.sufficiency.completed", "evidence.boundary.approved", "task.partial_terminal"):
            ap = R.P(e.get("approved_parent_ids")) or []
            ap = ap if isinstance(ap, list) else []
            approved |= set(ap)
            steps.append({"stage": "Evidence check", "text": f"{e.get('status')}; approved: {', '.join(ap) if ap else 'none'}",
                          "reason": e.get("reason", ""), "bad": not (set(ap) & ansset)})
        elif n.startswith("response.") and "omitted" in n:
            steps.append({"stage": "Reply writer", "text": n.split(".")[-1].replace("_", " "), "bad": True})
        elif n == "outbound.groundedness.completed":
            steps.append({"stage": "Reply audit (facts)", "text": f"passed: {e.get('passed')}. {e.get('reason', '')[:400]}", "bad": e.get("passed") in ("False", False)})
        elif n.startswith("escalation.soft"):
            steps.append({"stage": "Offer of a person", "text": n})
    return steps, retrieved, approved


def main(out_path):
    new = latest(F / "runs/h1_banking77/results/*_results.jsonl")
    pre = latest(PRE / "runs/h1_banking77/results/*_results.jsonl")
    b5 = {json.loads(l)["case_id"]: json.loads(l) for l in open(D / "labels/b5b_owner_review.jsonl") if l.strip()}
    out = []
    for c, analysis in CASES.items():
        r = new[c]
        t = r["turns"][-1]
        tv = turn_view(t)
        item = next(x.split(":", 1)[1] for x in r["tags"] if x.startswith("item:"))
        lab = LABELS[item]
        ans = lab["answering_faq_ids"]
        steps, retrieved, approved = full_trace(r, set(ans))
        pre_status = t["summary"].get("preflight_status")
        if c == "h1_h1_failed_transfer_2":
            d = "Generic fallback: a malformed fact-check reply used up the revision rounds (BUG-070)"
        elif any(s["stage"] == "Search formulation" for s in steps):
            d = "No search run: the query step rejected the product/intent pair"
        elif pre_status != "pass":
            d = f"Stopped before answering ({pre_status})"
        elif not (retrieved & set(ans)):
            d = "Search never found the answering FAQ"
        elif not (approved & set(ans)):
            d = "Answering FAQ found, but the evidence check rejected it"
        else:
            d = "Answering FAQ approved, but the reply left it out"
        history = []
        if c in b5:
            o = b5[c]
            history.append(f"5 Oct owner review: {o.get('verdict')} ({o.get('diagnosis', '')})" + (f" — {o['comment']}" if o.get("comment") else ""))
        history.append("15 Oct final review: failed B5b" + (" (verdict carried over from 5 Oct)" if c in b5 else " (direct review)"))
        later = None
        if c in pre:
            later = {"label": "16 Oct, fixed code (pre-commit check)", "reply": (pre[c]["turns"][-1].get("reply_text") or "").strip()}
        out.append({"id": c[6:], "case_id": c, "diagnosis": d, "question": t["message"], "coverage": lab["coverage"],
                    "ambiguous": lab["ambiguous"], "routes": lab["acceptable_routes"],
                    "keys": [{"text": k} for k in KEYS.get(item, [])],
                    "faq": [{"id": p, "text": FAQ.get(p, "")[:1800]} for p in ans], "steps": steps,
                    "reply": tv["reply"], "nav": tv["nav"], "forms": tv["forms"], "soft": tv["soft_escalation"],
                    "preflight": pre_status, "history": history, "analysis": analysis, "later": later,
                    "decided": c == "h1_h1_failed_transfer_2"})
    json.dump(out, open(out_path, "w"), ensure_ascii=False)
    print(len(out), "cases ->", out_path)


if __name__ == "__main__":
    main(sys.argv[1])
