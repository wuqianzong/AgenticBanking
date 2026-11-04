"""Scan every Gemini reply in recent capture logs and test it with the bot's own JSON parsing steps
(strip code fences, json.loads, then the BUG-065 trailing-delimiter repair). Deterministic, no LLM calls.
Each reply is attributed to a pipeline step by the JSON keys it contains. Output: results.json, failures.jsonl."""
import glob
import json
import re
from collections import Counter, defaultdict
from pathlib import Path

R = Path(__file__).resolve().parents[1]
RUNS = {
    "2026-10-04 v5.1 holdout": "2026-10-04_v51_holdout/runs/*/results/capture_log.jsonl",
    "2026-10-04 v5.1 planted": "2026-10-04_v51_holdout/planted/{results,v2/results}/capture_log.jsonl",
    "2026-10-14 BUG-065 replay": "2026-10-14_bug065_random_replay/results/capture_log.jsonl",
    "2026-10-14 C4 cache v1.1": "2026-10-14_c4_cache_v11/results/capture_log.jsonl",
    "2026-10-15 final release": "2026-10-15_final_release_regression/runs/*/results/capture_log.jsonl",
}
STEPS = [  # (step name, a key that only that step's reply carries)
    ("outbound audit", "conduct_reason"), ("reviser", "revision_status"), ("reply writer", "\"elements\""),
    ("compliance screen", "requested_from_horizon"), ("evidence check (sufficiency)", "approved_parent_ids"),
    ("triage", "triage_status"), ("intent formulator", "search_tasks"), ("tool planner", "subquery_domains"),
    ("resolution planner", "presentation_plan"), ("soft escalation", "escalation"), ("consistency check", "conflicts"),
]


def step_of(raw):
    for name, key in STEPS:
        if key in raw:
            return name
    m = re.search(r'"([a-z_]+)"\s*:', raw)
    return "other (first key: %s)" % (m.group(1) if m else "none")


def parse(raw):
    t = raw.strip()
    if t.startswith("```json"):
        t = t[7:]
    elif t.startswith("```"):
        t = t[3:]
    if t.endswith("```"):
        t = t[:-3]
    t = t.strip()
    try:
        json.loads(t)
        return "ok", None
    except json.JSONDecodeError as e:
        tail = t[e.pos:].strip()
        if e.msg == "Extra data" and tail and set(tail) <= {"}", "]"}:
            try:
                json.loads(t[:e.pos].rstrip())
                return "repaired_trailing", None
            except json.JSONDecodeError:
                pass
        return "fail", e


def kind(raw, e):
    t = raw.strip()
    line = t.splitlines()[e.lineno - 1] if e.lineno - 1 < len(t.splitlines()) else ""
    if re.match(r"^\s*-\s*\"", line):
        return "stray list marker before a key ('- \"key\"')"
    if e.msg == "Extra data":
        return "extra content after the object"
    if "Unterminated string" in e.msg or (e.pos >= len(t) - 2):
        return "truncated / unterminated"
    if "delimiter" in e.msg:
        return "missing comma or bracket"
    if not t.startswith(("{", "[", "`")):
        return "not JSON (prose)"
    return e.msg


def main():
    out, fails = {}, []
    for label, pat in RUNS.items():
        tot, res, by_step, fail_step, kinds = 0, Counter(), Counter(), Counter(), Counter()
        pats = [pat.replace("{results,v2/results}", x) for x in ("results", "v2/results")] if "{" in pat else [pat]
        for f in [g for p in pats for g in glob.glob(str(R / p))]:
            for l in open(f):
                if '"llm.response"' not in l:
                    continue
                e = json.loads(l)
                if not str(e.get("model_id", "")).startswith("gemini"):
                    continue
                raw = e.get("raw_response") or ""
                tot += 1
                st = step_of(raw)
                by_step[st] += 1
                r, err = parse(raw)
                res[r] += 1
                if r != "ok":
                    fail_step[st] += 1
                    k = kind(raw, err) if err else "trailing closing brace (repaired)"
                    kinds[k] += 1
                    fails.append({"run": label, "file": str(Path(f).relative_to(R)), "session": e.get("session_id"), "step": st, "result": r,
                                  "kind": k, "error": str(err) if err else "", "model": e.get("model_id"),
                                  "context": raw[max(0, (err.pos if err else len(raw)) - 160):(err.pos if err else len(raw)) + 80] if raw else ""})
        out[label] = {"gemini_replies": tot, "results": dict(res), "replies_by_step": dict(by_step), "failures_by_step": dict(fail_step), "failure_kinds": dict(kinds)}
    json.dump(out, open(Path(__file__).parent / "results.json", "w"), indent=1)
    with open(Path(__file__).parent / "failures.jsonl", "w") as o:
        for x in fails:
            o.write(json.dumps(x, ensure_ascii=False) + "\n")
    for k, v in out.items():
        bad = v["results"].get("fail", 0)
        print(f"{k:28} replies {v['gemini_replies']:6}  unparseable {bad:3} ({bad / max(1, v['gemini_replies']) * 100:.2f}%)  repaired {v['results'].get('repaired_trailing', 0)}  kinds {v['failure_kinds']}")
        print(f"{'':28} failures by step {v['failures_by_step']}")


if __name__ == "__main__":
    main()
