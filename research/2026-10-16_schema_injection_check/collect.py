"""Collect real prompts and their original replies for the four schema-less steps from the 15 Oct final release run.
Pairs each reply to the earliest unanswered request of the same step within its session. Includes every prompt whose
original reply was malformed, plus a reproducible random sample (random.Random(20261016)). Output: cases.json."""
import json, random, glob
from collections import defaultdict
from pathlib import Path

R = Path(__file__).resolve().parents[1]
LOGS = glob.glob(str(R / "2026-10-15_final_release_regression/runs/*/results/capture_log.jsonl"))
HEADS = {"FLEXIBLE RESPONSE GENERATION": "writer", "PRE-DECIDED STATEMENT COMPOSITION": "statement",
         "FLEXIBLE RESPONSE REVISION": "reviser", "GROUNDEDNESS v2.0 — COMBINED CONDUCT & EVIDENCE AUDIT": "audit"}
REPLY_KEY = {"writer": '"elements"', "statement": '"elements"', "reviser": "response_blocks", "audit": "conduct_reason"}


def step_of_prompt(p):
    for h, s in HEADS.items():
        if h in p[:200]:
            if s == "audit":
                return "audit_nonfinancial" if "politeness and natural interaction" in p else "audit_normal"
            return s
    return None


def parses(raw):
    t = raw.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
    try:
        json.loads(t); return True
    except Exception:
        return False


pairs = defaultdict(list)
for f in LOGS:
    pending = defaultdict(list)
    for l in open(f):
        if '"llm.request"' not in l and '"llm.response"' not in l:
            continue
        e = json.loads(l)
        if not str(e.get("model_id", "")).startswith("gemini"):
            continue
        sid = e["session_id"]
        if e["event"] == "llm.request":
            st = step_of_prompt(e.get("raw_prompt") or "")
            if st:
                pending[(sid, st.split("_")[0])].append(e["raw_prompt"])
        else:
            raw = e.get("raw_response") or ""
            for st, key in REPLY_KEY.items():
                q = pending.get((sid, st))
                if q and (key in raw or not parses(raw)):
                    p = q.pop(0)
                    pairs[step_of_prompt(p)].append({"session": sid, "step": step_of_prompt(p), "prompt": p, "original": raw, "original_parses": parses(raw)})
                    break
rng = random.Random(20261016)
quota = {"audit_normal": 14, "audit_nonfinancial": 4, "writer": 12, "statement": 6, "reviser": 8}
cases = []
for st, rows in pairs.items():
    bad = [r for r in rows if not r["original_parses"]]
    good = [r for r in rows if r["original_parses"]]
    rng.shuffle(good)
    cases += bad + good[:quota.get(st, 5)]
    print(st, "available", len(rows), "malformed originals", len(bad), "sampled", len(bad) + min(len(good), quota.get(st, 5)))
json.dump(cases, open(Path(__file__).parent / "cases.json", "w"), ensure_ascii=False)
print("total cases", len(cases))
