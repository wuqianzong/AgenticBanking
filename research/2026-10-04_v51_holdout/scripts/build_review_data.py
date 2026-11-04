"""Build review-page data from the H1 run: per case the question, frozen labels, answer key with both labellers'
verdicts, reply, answering FAQ text, and the pipeline trace from system_log_events, plus a failure-point diagnosis.
python scripts/build_review_data.py <case_ids.json> <out.json>"""
import ast
import glob
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from judge_common_v51 import D, FAQ, KEYS, LABELS, results, turn_view  # noqa: E402

AMBIG = {json.loads(l)["item_id"]: json.loads(l)["ambiguous"] for l in open(D / "labels/ambiguity_review.jsonl")}


def P(v):
    if isinstance(v, str) and v[:1] in "[{":
        try:
            return ast.literal_eval(v)
        except Exception:
            return v
    return v


def title(pid):
    return FAQ.get(pid, "").strip().split("\n")[0][:110]


def main(ids_path, out_path):
    want = json.load(open(ids_path))
    A = {}
    for f in glob.glob(str(D / "labels/score_A_h1_w*.jsonl")) + [str(D / "labels/score_A_h1_ambig_rejudge.jsonl")]:
        for l in open(f):
            r = json.loads(l)
            A[r["case_id"]] = r
    B = {json.loads(l)["case_id"]: json.loads(l) for l in open(D / "labels/score_B_h1.jsonl")}
    res = results("h1_banking77")
    out = []
    for c in want:
        r = res[c]
        t = r["turns"][-1]
        s, tv = t["summary"], turn_view(t)
        item = next(x.split(":", 1)[1] for x in r["tags"] if x.startswith("item:"))
        lab = LABELS[item]
        ans = lab["answering_faq_ids"]
        ansset = set(ans)
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
            elif n == "task.expansion.applied":
                steps.append({"stage": "Search widened", "text": f"{e.get('stage')}: products {e.get('old_product')} → {e.get('new_product')}"})
            elif n == "task.rag.completed":
                rounds += 1
                sel = P(e.get("selected_parent_ids")) or {}
                ids = [i for v in (sel.values() if isinstance(sel, dict) else []) for i in v]
                retrieved |= set(ids)
                steps.append({"stage": f"Retrieval round {rounds}", "ids": [{"id": i, "title": title(i), "answering": i in ansset} for i in ids],
                              "text": f"{len(ids)} FAQ entries found; answering entries among them: {len(set(ids) & ansset)}"})
            elif n in ("task.retry", "task.clarification_required", "task.sufficiency.completed", "evidence.boundary.approved", "task.partial_terminal"):
                ap = P(e.get("approved_parent_ids")) or []
                if isinstance(ap, list):
                    approved |= set(ap)
                steps.append({"stage": "Evidence check", "text": f"{e.get('status')}; approved: {', '.join(ap) if ap else 'none'}",
                              "reason": e.get("reason", ""), "bad": not (set(ap) & ansset)})
            elif n.startswith("response.") and "omitted" in n:
                steps.append({"stage": "Reply writer", "text": n.split(".")[-1].replace("_", " "), "bad": True})
            elif n == "outbound.groundedness.completed":
                steps.append({"stage": "Reply audit (facts)", "text": f"passed: {e.get('passed')}. {e.get('reason', '')[:400]}", "bad": e.get("passed") in ("False", False)})
            elif n == "escalation.soft.offered" or n.startswith("escalation.soft"):
                steps.append({"stage": "Offer of a person", "text": n})
        pre = s.get("preflight_status")
        if pre != "pass":
            d = f"Stopped before answering ({pre})"
        elif not (retrieved & ansset):
            d = "Search never found the answering FAQ"
        elif not (approved & ansset):
            d = "Answering FAQ found, but the evidence check rejected it"
        else:
            d = "Answering FAQ approved, but the reply left out key facts"
        key = KEYS.get(item, [])
        ak = A[c].get("key_items_present") or []
        bk = str(B[c].get("keys") or "")
        out.append({"id": c[6:], "case_id": c, "diagnosis": d, "question": t["message"], "coverage": lab["coverage"],
                    "ambiguous": AMBIG.get(item, lab["ambiguous"]), "routes": lab["acceptable_routes"],
                    "keys": [{"text": k, "A": bool(ak[i]) if i < len(ak) else False, "B": i < len(bk) and bk[i] == "1"} for i, k in enumerate(key)],
                    "faq": [{"id": p, "text": FAQ.get(p, "")[:1800]} for p in ans], "steps": steps, "reply": tv["reply"], "nav": tv["nav"],
                    "forms": tv["forms"], "soft": tv["soft_escalation"], "preflight": pre, "a_reason": A[c].get("reason", ""),
                    "b_note": B[c].get("note", ""), "a_outcome": A[c].get("outcome"), "b_outcome": B[c].get("outcome")})
    json.dump(out, open(out_path, "w"), ensure_ascii=False)
    print(len(out), "cases ->", out_path)


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
