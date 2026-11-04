"""Paired content check. Each collected prompt is re-run twice: with its new schema and without any schema (the
current production behaviour). Content similarity to the original reply is computed on the flattened text of every
string field, so layout choices (TEXT vs PROGRESS) do not count as content loss. The no-schema rerun measures the
model's own run-to-run variation; the schema effect is the difference between the two. Saves raw replies.
Output: paired.jsonl, paired_summary.json."""
import difflib, json, re, sys, time
from pathlib import Path

H = Path(__file__).resolve().parent
sys.path.insert(0, str(H.parents[1]))
import replay as RP  # noqa: E402
from replay import N, ModelUnavailableError  # noqa: E402


def flat(v):
    if isinstance(v, str):
        return [v.strip()] if v.strip() else []
    if isinstance(v, list):
        return [s for x in v for s in flat(x)]
    if isinstance(v, dict):
        return [s for k, x in v.items() if k not in ("type", "block_id", "revision_status", "source_task_ids", "status", "severity",
                                                     "conduct_reason", "evidence_reason", "rewrite_instruction") for s in flat(x)]
    return []


def sim(a, b):
    return round(difflib.SequenceMatcher(None, " ".join(flat(a)), " ".join(flat(b))).ratio(), 3)


def call(case, schema, model):
    while True:
        try:
            kw = {"response_schema_override": schema} if schema is not None else {}
            return N.call_groq_lpu(case["prompt"], model_id=model, **kw) if model else N.call_groq_lpu(case["prompt"], **kw)
        except ModelUnavailableError:
            print("model unavailable, waiting 60 s", flush=True); time.sleep(60)


def main():
    cases = json.load(open(H / "cases.json"))
    out_path = H / "paired.jsonl"
    done = {json.loads(l)["i"] for l in open(out_path)} if out_path.exists() else set()
    with open(out_path, "a") as o:
        for i, c in enumerate(cases):
            if i in done:
                continue
            schema, model = RP.schema_and_model(c)
            raw_s = call(c, schema, model); time.sleep(1.5)
            raw_n = call(c, None, model); time.sleep(1.5)
            s, n, orig = RP.loads(raw_s), RP.loads(raw_n), RP.loads(c["original"])
            rec = {"i": i, "step": c["step"], "original_parses": c["original_parses"], "schema_parses": s is not None, "noschema_parses": n is not None,
                   "raw_schema": raw_s, "raw_noschema": raw_n}
            if c["step"].startswith("audit"):
                for tag, r in (("schema", s), ("noschema", n), ("original", orig)):
                    rec[f"{tag}_pass"] = RP.verdict(r, c["step"]) if r else None
            if orig is not None:
                if s is not None:
                    rec["sim_schema_vs_original"] = sim(s, orig)
                if n is not None:
                    rec["sim_noschema_vs_original"] = sim(n, orig)
            if s is not None and n is not None:
                rec["sim_schema_vs_noschema"] = sim(s, n)
            o.write(json.dumps(rec, ensure_ascii=False) + "\n"); o.flush()
            print(i, c["step"], "schema", "ok" if s is not None else "MALFORMED", "| noschema", "ok" if n is not None else "MALFORMED",
                  {k: rec[k] for k in rec if k.startswith(("sim_", "schema_pass", "noschema_pass", "original_pass"))}, flush=True)


if __name__ == "__main__":
    main()
