"""Shared helpers for v5.1 reply scoring: load frozen labels/keys, read run results, FAQ text."""
import ast
import glob
import json
import sys
from pathlib import Path

D = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(D.parents[1] / "research/2026-09-27_v4_measurement_judges/scripts"))
from judge_common import _db, fill_precision, judge  # noqa: E402,F401

FAQ = {pid: fill_precision(c) for pid, c in _db.execute("select parent_id, content from knowledge_parents")}
LABELS = {json.loads(l)["item_id"]: json.loads(l) for l in open(D / "labels/labels_final.jsonl")}
KEYS = {json.loads(l)["item_id"]: json.loads(l)["key_items"] for l in open(D / "labels/answer_keys_final.jsonl")}
STRICT = {json.loads(l)["item_id"]: json.loads(l)["key_items"] for l in open(D / "labels/answer_keys_strict.jsonl")}


def parse(v):
    if isinstance(v, str) and v[:1] in "[{":
        try:
            return ast.literal_eval(v)
        except (ValueError, SyntaxError):
            return v
    return v


def results(run):
    """Latest OK record per case_id across a run folder's result files."""
    out = {}
    for f in sorted(glob.glob(str(D / f"runs/{run}/results/*_results.jsonl"))):
        for l in open(f):
            r = json.loads(l)
            if r.get("status") == "OK" or r["case_id"] not in out:
                out[r["case_id"]] = r
    return out


def turn_view(t):
    s = t.get("summary", {})
    tr = parse(s.get("task_results")) or []
    approved = sorted({p for x in tr if isinstance(x, dict) for p in x.get("approved_parent_ids", [])})
    ui = t.get("ui") or {}
    return {"reply": t.get("reply_text", ""), "approved": approved,
            "nav": [a.get("label") for a in ui.get("navigationActions") or []],
            "forms": [a.get("label") for a in ui.get("serviceRequestActions") or []],
            "soft_escalation": bool(ui.get("softEscalationAvailable")),
            "personal_data_reveals": parse(s.get("personal_data_reveals")) or [],
            "preflight": s.get("preflight_status"), "triage": s.get("triage_status")}


def entries_text(ids, limit=None):
    return "\n\n".join(f"[{p}]\n{FAQ[p][:limit] if limit else FAQ[p]}" for p in ids if p in FAQ) or "(none)"
