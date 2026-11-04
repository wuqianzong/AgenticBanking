"""Compare every recorded reply with the story expectations in cases.py and write results/CHECK_REPORT.md and
results/check.json. A turn passes when all its `must` checks pass; `should` misses are listed but do not fail it.
    python research/2026-10-23_demo_story_check/scripts/check.py
"""
import json
import re
import sys
from pathlib import Path

D = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(D / "scripts"))
from cases import CASES, NO_INFO, STORY2  # noqa: E402

UNIVERSAL_BAD = r"\{\{(?!data-)|destination_id|special_id|subquery_\d|raw_route"


def latest(paths):
    paths = sorted(paths)
    return paths[-1] if paths else None


def labels(actions):
    return [a.get("label") or a.get("title") for a in (actions or [])]


def check_one(check, t):
    reply = t.get("reply_text") or ""
    ui = t.get("ui") or {}
    triage = (t.get("summary") or {}).get("triage_status")
    if "re" in check:
        return bool(re.search(check["re"], reply, re.I)), f"reply matches /{check['re']}/"
    if "not_re" in check:
        return not re.search(check["not_re"], reply, re.I), f"reply avoids /{check['not_re']}/"
    if "nav" in check:
        return check["nav"] in labels(ui.get("navigationActions")), f"button '{check['nav']}'"
    if "form" in check:
        return check["form"] in labels(ui.get("serviceRequestActions")), f"form '{check['form']}'"
    if "triage" in check:
        return triage == check["triage"], f"triage {check['triage']} (got {triage})"
    if "not_triage" in check:
        return triage != check["not_triage"], f"triage not {check['not_triage']} (got {triage})"
    if "soft" in check:
        return bool(ui.get("softEscalationAvailable")) == check["soft"], "talk-to-an-agent banner shown"
    if "max_s" in check:
        return (t.get("elapsed_seconds") or 1e9) <= check["max_s"], f"reply within {check['max_s']} s (got {t.get('elapsed_seconds')})"
    if "answered" in check:
        return not re.search(NO_INFO, reply, re.I), "an answer, not 'no information'"
    raise ValueError(check)


def judge(spec, t):
    out = {"turn": t.get("turn_index"), "message": t.get("message"), "reply": t.get("reply_text"),
           "seconds": t.get("elapsed_seconds"), "triage": (t.get("summary") or {}).get("triage_status"),
           "buttons": labels((t.get("ui") or {}).get("navigationActions")),
           "forms": labels((t.get("ui") or {}).get("serviceRequestActions")),
           "banner": bool((t.get("ui") or {}).get("softEscalationAvailable")),
           "must_failed": [], "should_missed": [], "review": spec.get("review")}
    if t.get("status") != "OK" or not (t.get("reply_text") or "").strip():
        out["must_failed"].append(f"turn status {t.get('status')} or empty reply")
    if re.search(UNIVERSAL_BAD, t.get("reply_text") or ""):
        out["must_failed"].append("raw placeholder or internal id in the reply")
    for level, key in (("must", "must_failed"), ("should", "should_missed")):
        for c in spec.get(level, []):
            ok, label = check_one(c, t)
            if not ok:
                out[key].append(label)
    out["pass"] = not out["must_failed"]
    return out


def secrets_in_prompts(record, secrets):
    hits = []
    for e in record.get("system_log_events", []):
        if e.get("event") in ("llm.request", "llm.error"):
            prompt = str(e.get("raw_prompt", ""))
            hits += [s for s in secrets if s in prompt]
    return sorted(set(hits))


results = []
# A resumed attempt writes a new results file holding only the cases still missing, so merge every file in order.
fw = latest((D / "runs/stories/results").glob("*_results.jsonl"))
records = {}
for path in sorted((D / "runs/stories/results").glob("*_results.jsonl")):
    for line in open(path):
        r = json.loads(line)
        if r["status"] == "OK" or r["case_id"] not in records:
            records[r["case_id"]] = r
for case in CASES:
    r = records.get(case["case_id"])
    if not r:
        results.append({"case_id": case["case_id"], "story": case["story"], "title": case["title"], "pass": False,
                        "missing": True, "turns": []})
        continue
    turns = [judge(spec, t) for spec, t in zip(case["turns"], r["turns"])]
    leaked = secrets_in_prompts(r, case.get("secret_absent", []))
    ok = r["status"] == "OK" and len(turns) == len(case["turns"]) and all(t["pass"] for t in turns) and not leaked
    results.append({"case_id": case["case_id"], "story": case["story"], "title": case["title"], "pass": ok,
                    "session_id": r["session_id"], "secret_in_prompt": leaked, "turns": turns,
                    "llm_calls": sum(1 for e in r.get("system_log_events", []) if e.get("event") == "llm.request")})

s2 = latest([D / "runs/story2/results/story2_results.jsonl"] if (D / "runs/story2/results/story2_results.jsonl").exists() else [])
if s2:
    r = [json.loads(l) for l in open(s2)][-1]
    turns = [judge(spec, t) for spec, t in zip(STORY2["turns"], r["turns"])]
    login = judge(STORY2["login_greeting"], r["steps"]["login_b"])
    login["message"] = "(logs out, then logs back in)"
    submitted = (r["steps"].get("form_submitted") or {}).get("status")
    form_step = {"turn": "form", "message": "(submits the Change Address form)", "reply": json.dumps(r["steps"].get("form_submitted")),
                 "must_failed": [] if submitted == "completed" else [f"form status {submitted}"], "should_missed": [],
                 "pass": submitted == "completed"}
    leaked = secrets_in_prompts(r, STORY2["secret_absent"])
    all_turns = turns + [login, form_step]
    results.insert(1, {"case_id": STORY2["case_id"], "story": "S2", "title": STORY2["title"],
                       "pass": all(t["pass"] for t in all_turns) and not leaked and len(turns) == len(STORY2["turns"]),
                       "session_id": r["session_ids"], "secret_in_prompt": leaked, "turns": all_turns,
                       "llm_calls": sum(1 for e in r.get("system_log_events", []) if e.get("event") == "llm.request")})
else:
    results.insert(1, {"case_id": STORY2["case_id"], "story": "S2", "title": STORY2["title"], "pass": False,
                       "missing": True, "turns": []})

(D / "results").mkdir(exist_ok=True)
json.dump(results, open(D / "results/check.json", "w"), indent=1, ensure_ascii=False)
lines = ["# Demo user-story check", "", f"Results file: `{fw.relative_to(D) if fw else 'none'}`; Story 2: "
         f"`{s2.relative_to(D) if s2 else 'none'}`.", "",
         f"**{sum(r['pass'] for r in results)}/{len(results)} cases pass their must-checks.**", "",
         "| Case | Story | Pass | Must failed | Should missed | Secret in a prompt |", "|---|---|---|---|---|---|"]
for r in results:
    mf = "; ".join(f"t{t['turn']}: {', '.join(t['must_failed'])}" for t in r["turns"] if t["must_failed"]) or ("not run" if r.get("missing") else "")
    sm = "; ".join(f"t{t['turn']}: {', '.join(t['should_missed'])}" for t in r["turns"] if t["should_missed"])
    lines.append(f"| {r['case_id']} | {r['story']} | {'PASS' if r['pass'] else 'FAIL'} | {mf} | {sm} | {', '.join(r.get('secret_in_prompt') or []) or 'none'} |")
lines += ["", "## Every turn", ""]
for r in results:
    lines.append(f"### {r['case_id']} ({r['story']}): {r['title']}")
    lines.append(f"Session: `{r.get('session_id')}`; model calls: {r.get('llm_calls')}")
    lines.append("")
    for t in r["turns"]:
        extra = f" buttons={t.get('buttons')} forms={t.get('forms')} banner={t.get('banner')} triage={t.get('triage')} {t.get('seconds')}s"
        lines.append(f"- **t{t['turn']}** {t['message']}")
        lines.append(f"  - reply: {(t.get('reply') or '').replace(chr(10), ' ')}")
        lines.append(f"  - {'PASS' if t['pass'] else 'FAIL'}{extra}")
        if t.get("review"):
            lines.append(f"  - manual review: {t['review']}")
    lines.append("")
(D / "results/CHECK_REPORT.md").write_text("\n".join(lines), encoding="utf-8")
print("\n".join(lines[:8 + len(results)]))
