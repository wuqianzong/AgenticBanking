"""Replay the collected prompts through the bot's own call path with the new response schemas and check:
(1) the reply parses, (2) it carries every field the consumers read, (3) it matches the original reply's
verdict/content. Resumable; waits out quota. Output: replay.jsonl, summary.json."""
import difflib, json, re, sys, time
from pathlib import Path

H = Path(__file__).resolve().parent
sys.path.insert(0, str(H.parents[1]))
import backend.app.orchestration.nodes as N  # noqa: E402
from backend.app.core.llm_service import (FlexibleResponseGeneration, ModelUnavailableError, NonFinancialCombinedAudit,  # noqa: E402
                                          NormalCombinedAudit, PreDecidedStatementComposition)

N.log_event = lambda *a, **k: None
REQUIRED = {
    "audit_normal": ["conduct_reason", "conduct_compliant", "conduct_violations", "answer_compliant", "clarification_compliant", "rewrite_instruction",
                     "evidence_reason", "evidence_grounded", "evidence_violations", "answer_evidence_grounded", "answer_bridge_grounded",
                     "clarification_grounded", "clarification_connected", "response_recovery_strategy", "grounding_violations"],
    "audit_nonfinancial": ["conduct_reason", "conduct_compliant", "conduct_violations", "answer_compliant", "clarification_compliant", "rewrite_instruction",
                           "evidence_reason", "evidence_grounded", "evidence_violations", "answer_bridge_grounded", "clarification_grounded", "clarification_connected"],
    "writer": ["elements", "clarification", "next_actions"], "statement": ["elements"], "reviser": ["response_blocks"],
}


def loads(raw):
    t = raw.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
    try:
        return json.loads(t)
    except Exception:
        return None


def skeleton(prompt):
    return [{"block_id": b, "type": t} for b, t in re.findall(r'"block_id":\s*"([^"]+)",\s*"type":\s*"([^"]+)"', prompt)]


def schema_and_model(case):
    st = case["step"]
    if st == "audit_normal":
        return NormalCombinedAudit, N.MODEL_B_AUDITOR
    if st == "audit_nonfinancial":
        return NonFinancialCombinedAudit, N.MODEL_B_AUDITOR
    if st == "writer":
        return FlexibleResponseGeneration, None
    if st == "statement":
        return PreDecidedStatementComposition, None
    return N._reviser_response_schema(skeleton(case["prompt"])), None


def verdict(r, st):
    keys = ["conduct_compliant", "evidence_grounded", "answer_compliant", "clarification_compliant", "answer_bridge_grounded", "clarification_grounded", "clarification_connected"]
    if st == "audit_normal":
        keys.append("answer_evidence_grounded")
    return all(bool(r.get(k, False)) for k in keys)


def text_of(r, st):
    if st in ("writer", "statement"):
        return " ".join(" ".join(str(e.get(k, "")) for k in ("context_text", "answer", "text", "extended_text", "message")) for e in r.get("elements", []) if isinstance(e, dict))
    if st == "reviser":
        return " ".join(json.dumps({k: v for k, v in b.items() if k not in ("block_id", "type", "revision_status") and v is not None}, ensure_ascii=False) for b in r.get("response_blocks", []) if isinstance(b, dict))
    return ""


def compare(case, new):
    st, orig = case["step"], loads(case["original"])
    out = {}
    if orig is None:
        out["original"] = "malformed (no comparison possible)"
        return out
    if st.startswith("audit"):
        out.update(original_pass=verdict(orig, st), new_pass=verdict(new, st),
                   original_violations=len(orig.get("evidence_violations", [])) + len(orig.get("conduct_violations", [])),
                   new_violations=len(new.get("evidence_violations", [])) + len(new.get("conduct_violations", [])))
        out["same_verdict"] = out["original_pass"] == out["new_pass"]
    else:
        key = "response_blocks" if st == "reviser" else "elements"
        out.update(original_count=len(orig.get(key, [])), new_count=len(new.get(key, [])),
                   similarity=round(difflib.SequenceMatcher(None, text_of(orig, st), text_of(new, st)).ratio(), 3))
        if st == "writer":
            out.update(original_types=[e.get("type") for e in orig.get("elements", [])], new_types=[e.get("type") for e in new.get("elements", [])])
        if st == "reviser":
            pend = [{"blockId": b["block_id"], "type": b["type"]} for b in skeleton(case["prompt"])
                    if re.search(r'"block_id":\s*"%s"[^{}]*"revision_status":\s*"pending"' % re.escape(b["block_id"]), case["prompt"])]
            no, nn = N._normalize_reviser_output(orig, pend), N._normalize_reviser_output(new, pend)
            out.update(pending=[p["blockId"] for p in pend], original_revised=sorted(no), new_revised=sorted(nn),
                       new_cleared_fields=[f"{b}.{k}" for b, rev in nn.items() for k, v in rev.items() if v in ("", [], {}) and no.get(b, {}).get(k) not in ("", [], {}, None)])
    return out


def main():
    cases = json.load(open(H / "cases.json"))
    done = {json.loads(l)["i"] for l in open(H / "replay.jsonl")} if (H / "replay.jsonl").exists() else set()
    with open(H / "replay.jsonl", "a") as o:
        for i, c in enumerate(cases):
            if i in done:
                continue
            schema, model = schema_and_model(c)
            while True:
                try:
                    raw = N.call_groq_lpu(c["prompt"], model_id=model, response_schema_override=schema) if model else N.call_groq_lpu(c["prompt"], response_schema_override=schema)
                    break
                except ModelUnavailableError:
                    print("model unavailable, waiting 60 s", flush=True); time.sleep(60)
            new = loads(raw)
            rec = {"i": i, "step": c["step"], "session": c["session"], "original_parses": c["original_parses"], "schema": getattr(schema, "__name__", None),
                   "new_parses": new is not None}
            if new is not None:
                rec["missing_fields"] = [k for k in REQUIRED[c["step"]] if k not in new]
                rec.update(compare(c, new))
            else:
                rec["raw_head"] = raw[:300]
            o.write(json.dumps(rec, ensure_ascii=False) + "\n"); o.flush()
            print(i, c["step"], "parses" if new is not None else "MALFORMED", rec.get("missing_fields"), {k: rec[k] for k in ("same_verdict", "similarity") if k in rec}, flush=True)
            time.sleep(1.5)


if __name__ == "__main__":
    main()
