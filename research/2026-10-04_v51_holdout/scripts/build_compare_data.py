"""Pair a case's 5 Oct (frozen v5.1) reply with its 15 Oct (final release) reply for the degradation review.
Per case: question, labels, answer key, answering FAQ text, both replies with routes and adopted parents, the final
run's pipeline trace, the 5 Oct owner verdicts, and the final review's category and note.
python scripts/build_compare_data.py <out.json>"""
import glob
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import build_review_data as R  # noqa: E402
from judge_common_v51 import D, FAQ, KEYS, LABELS, results, turn_view  # noqa: E402

F = D.parent / "2026-10-15_final_release_regression"


def latest(path_glob):
    out = {}
    for f in sorted(glob.glob(str(path_glob))):
        for l in open(f):
            r = json.loads(l)
            if r.get("status") == "OK" or r["case_id"] not in out:
                out[r["case_id"]] = r
    return out


def trace(r, ansset):
    steps = []
    rounds = 0
    for e in r.get("system_log_events") or []:
        n = e.get("event")
        if n == "inbound.compliance.checked":
            steps.append({"stage": "Compliance screen", "text": f"{e.get('screen_verdict')}; area: {e.get('compliance_area')}", "bad": e.get("screen_verdict") == "no_compliant_route"})
        elif n == "task.formulated" or n == "tool.plan.subquery_domains":
            continue
        elif n == "task.rag.completed":
            rounds += 1
            sel = R.P(e.get("selected_parent_ids")) or {}
            cache = R.P(e.get("cache_injected_parent_ids")) or {}
            ids = [i for v in (sel.values() if isinstance(sel, dict) else []) for i in v]
            cids = [i for v in (cache.values() if isinstance(cache, dict) else []) for i in v]
            steps.append({"stage": f"Retrieval round {rounds}", "ids": [{"id": i, "title": R.title(i), "answering": i in ansset} for i in ids],
                          "text": f"{len(ids)} FAQ entries found; answering entries among them: {len(set(ids) & ansset)}" + (f"; from cache: {', '.join(cids)}" if cids else "")})
        elif n in ("task.retry", "task.clarification_required", "task.sufficiency.completed", "evidence.boundary.approved", "task.partial_terminal"):
            ap = R.P(e.get("approved_parent_ids")) or []
            ap = ap if isinstance(ap, list) else []
            steps.append({"stage": "Evidence check", "text": f"{e.get('status')}; approved: {', '.join(ap) if ap else 'none'}", "reason": e.get("reason", ""), "bad": not (set(ap) & ansset)})
        elif n == "outbound.groundedness.completed":
            steps.append({"stage": "Reply audit (facts)", "text": f"passed: {e.get('passed')}. {e.get('reason', '')[:300]}", "bad": e.get("passed") in ("False", False)})
        elif n == "task.expansion.applied":
            steps.append({"stage": "Search widened", "text": f"{e.get('stage')}"})
    return steps


def main(out_path):
    sem = {json.loads(l)["case_id"]: json.loads(l) for l in open(F / "results/semantic_comparison.jsonl")}
    notes = {json.loads(l)["case_id"]: json.loads(l) for l in open(F / "results/manual_response_verdicts.jsonl")}
    b2 = {json.loads(l)["case_id"]: json.loads(l) for l in open(D / "labels/b2_owner_review.jsonl")}
    b5 = {json.loads(l)["case_id"]: json.loads(l) for l in open(D / "labels/b5b_owner_review.jsonl")}
    old, new = results("h1_banking77"), latest(F / "runs/h1_banking77/results/*_results.jsonl")
    out = []
    for c, x in sem.items():
        if x["category"] not in ("degraded", "mixed"):
            continue
        ro, rn = old[c], new[c]
        to, tn = turn_view(ro["turns"][-1]), turn_view(rn["turns"][-1])
        item = next(t.split(":", 1)[1] for t in ro["tags"] if t.startswith("item:"))
        lab = LABELS[item]
        ans = set(lab["answering_faq_ids"])
        ov = b2.get(c, b5.get(c, {}))
        out.append({"id": c[6:], "case_id": c, "category": x["category"], "question": ro["turns"][-1]["message"], "coverage": lab["coverage"],
                    "routes": lab["acceptable_routes"], "keys": KEYS.get(item, []),
                    "faq": [{"id": p, "text": FAQ.get(p, "")[:1800]} for p in lab["answering_faq_ids"]],
                    "old": {"reply": to["reply"], "nav": to["nav"], "forms": to["forms"], "parents": [{"id": p, "title": R.title(p), "answering": p in ans} for p in x["old_parent_ids"]],
                            "outcome": x["old_outcome"], "owner_verdict": ov.get("verdict"), "owner_comment": ov.get("comment", "")},
                    "new": {"reply": tn["reply"], "nav": tn["nav"], "forms": tn["forms"], "parents": [{"id": p, "title": R.title(p), "answering": p in ans} for p in x["new_parent_ids"]],
                            "outcome": x["new_outcome"], "steps": trace(rn, ans), "preflight": tn["preflight"]},
                    "final_review": {"b2_complete": notes.get(c, {}).get("b2_complete"), "b5b_suitable": notes.get(c, {}).get("b5b_suitable"), "note": notes.get(c, {}).get("reason", "")}})
    json.dump(out, open(out_path, "w"), ensure_ascii=False)
    print(len(out), "cases ->", out_path)


if __name__ == "__main__":
    main(sys.argv[1])
