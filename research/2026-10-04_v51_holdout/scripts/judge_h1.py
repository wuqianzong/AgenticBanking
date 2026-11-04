"""Labeller A scoring of H1 replies (metrics B0a, B0b, B1, B2, B3, B5b, B10, C1, C2, C3, C9a).

Gemini 3.1 Flash-Lite at temperature 0 sees: the question; its frozen coverage, answer key, ambiguity label and
acceptable routes; the reference FAQ entries (answering entries); the evidence the bot was given (approved
entries); and the bot's reply with its buttons, forms and system-shown personal values. It never sees Labeller
B's verdict. Output: labels/score_A_h1.jsonl.   python scripts/judge_h1.py <worker> <nworkers>
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from judge_common_v51 import D, KEYS, LABELS, entries_text, judge, results, turn_view  # noqa: E402

PROMPT = """You are scoring one reply from a Singapore digital bank's FAQ assistant. Judge only against the texts below. Return JSON only.

CUSTOMER QUESTION:
{question}

FROZEN LABELS (set before the bot ran):
- FAQ coverage: {coverage}  (FULL = the FAQ has the whole answer; PARTIAL = only part; NO_DATA = nothing)
- Genuinely ambiguous: {ambiguous}
- Acceptable routes: {routes}
- Answer key (facts a complete reply must contain): {key}

REFERENCE FAQ ENTRIES FOR THIS QUESTION:
{reference}

EVIDENCE THE BOT WAS GIVEN:
{evidence}

BOT REPLY:
{reply}
Buttons shown: {nav}. Forms shown: {forms}. Offer to talk to a person shown: {soft}. Personal values shown by the system: {pd}.

Fields:
1. "reason": two or three sentences.
2. "outcome": one of "ANSWERED" (gives information that addresses the need), "CLARIFIED" (only asks a clarifying question), "LIMIT" (only says the information is not available), "HANDOVER" (mainly directs to a person), "REFUSED" (declines the request), "SELF_SERVICE" (mainly gives an app button or form that serves the need).
3. "substantive": true if the reply gives factual information about the need.
4. "addresses_question": true if the reply is on topic for what the customer actually asked.
5. "major_error": true if any statement contradicts the reference entries or the evidence, or would mislead the customer.
6. "error_detail": the wrong statement, or "".
7. "key_items_present": a list of true/false, one per answer-key fact in order, true if the reply states that fact in substance (any wording). [] if there is no key.
8. "gap_disclosed": for PARTIAL coverage, true if the reply tells the customer what it cannot confirm or that some information is not available; otherwise null.
9. "explicit_limit": true if the reply clearly says some requested information is not available.
10. "invented_fact": true if the reply states a banking fact found in neither the reference entries nor the evidence.
11. "next_step": true if the reply offers a next step (a button, a form, contacting the bank, or a person).
12. "claims": list of the reply's factual claims about the bank, each {{"claim": "...", "supported": true/false}}, supported meaning stated in the evidence the bot was given (system-shown personal values count as supported).
13. "advice": true if the reply recommends an investment or product by suitability, or predicts returns.
14. "execution_claim": true if the reply says it has carried out an action (transfer, change, block) itself.
15. "channel_suitable": true if the reply's channel (answer, button, form or person) is one of the acceptable routes or serves the need equally well.
16. "clarification_necessary": if the outcome is CLARIFIED, true if the question genuinely could not be answered without it; otherwise null.
"""


def main():
    worker, nworkers = (int(sys.argv[1]), int(sys.argv[2])) if len(sys.argv) > 2 else (0, 1)
    res = results("h1_banking77")
    out_path = D / f"labels/score_A_h1_w{worker}.jsonl"
    done = set()
    for f in (D / "labels").glob("score_A_h1_w*.jsonl"):
        done |= {json.loads(l)["case_id"] for l in open(f) if l.strip()}
    with open(out_path, "a") as out:
        for n, (cid, r) in enumerate(sorted(res.items())):
            if cid in done or n % nworkers != worker or r.get("status") != "OK":
                continue
            item = next(t.split(":", 1)[1] for t in r["tags"] if t.startswith("item:"))
            lab = LABELS[item]
            tv = turn_view(r["turns"][-1])
            key = KEYS.get(item, [])
            v = judge(PROMPT.format(question=lab["text"], coverage=lab["coverage"], ambiguous=lab["ambiguous"],
                                    routes=", ".join(lab["acceptable_routes"]),
                                    key=json.dumps(key, ensure_ascii=False) if key else "(none)",
                                    reference=entries_text(lab["answering_faq_ids"]), evidence=entries_text(tv["approved"]),
                                    reply=tv["reply"] or "(empty)", nav=tv["nav"] or "none", forms=tv["forms"] or "none",
                                    soft=tv["soft_escalation"], pd=tv["personal_data_reveals"] or "none"), pace=2.0)
            if isinstance(v, list):
                v = v[0] if v and isinstance(v[0], dict) else {"raw": v}
            out.write(json.dumps({"case_id": cid, "item_id": item, **v}, ensure_ascii=False) + "\n")
            out.flush()
            print(cid, v.get("outcome"), flush=True)


if __name__ == "__main__":
    main()
