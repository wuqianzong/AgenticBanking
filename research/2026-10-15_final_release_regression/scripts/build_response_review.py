"""Build a local, non-judging comparison index for direct release review.

No network calls. The output surfaces changes and evidence; semantic verdicts
are deliberately left to the reviewer rather than inferred from similarity.
"""
from __future__ import annotations

import glob
import json
import re
from collections import Counter
from pathlib import Path

from sentence_transformers import SentenceTransformer


D = Path(__file__).resolve().parents[1]
BASE = D.parent / "2026-10-04_v51_holdout"


def load_results(folder: Path) -> dict[str, dict]:
    rows: dict[str, dict] = {}
    for path in sorted(folder.glob("*_results.jsonl")):
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            if row.get("status") == "OK" or row.get("case_id") not in rows:
                rows[row["case_id"]] = row
    return rows


def view(row: dict) -> dict:
    turn = row["turns"][-1]
    summary = turn.get("summary") or {}
    ui = turn.get("ui") or {}
    parents = sorted({
        parent
        for task in summary.get("task_results") or []
        if isinstance(task, dict)
        for parent in task.get("approved_parent_ids") or []
    })
    return {
        "reply": (turn.get("reply_text") or "").strip(),
        "nav": [x.get("label") for x in ui.get("navigationActions") or []],
        "forms": [x.get("label") for x in ui.get("serviceRequestActions") or []],
        "soft": bool(ui.get("softEscalationAvailable")),
        "preflight": summary.get("preflight_status"),
        "triage": summary.get("triage_status"),
        "parents": parents,
    }


def norm(text: str) -> str:
    return re.sub(r"\s+", " ", text.casefold()).strip()


def rough_outcome(v: dict) -> str:
    text = norm(v["reply"])
    if v["preflight"] in {"non_compliant", "error_malicious", "error_sensitive_word", "error_credential"}:
        return "REFUSED"
    if v["forms"] or v["nav"]:
        return "SELF_SERVICE"
    if any(p in text for p in ("don't have information", "do not have information", "no information available", "unable to find information")):
        return "LIMIT"
    if text.endswith("?") and len(text.split("?")) <= 3:
        return "CLARIFIED"
    return "ANSWERED" if text else "EMPTY"


labels = {json.loads(x)["item_id"]: json.loads(x) for x in (BASE / "labels/labels_final.jsonl").read_text().splitlines()}
keys = {json.loads(x)["item_id"]: json.loads(x)["key_items"] for x in (BASE / "labels/answer_keys_final.jsonl").read_text().splitlines()}
faq = {}
import sqlite3
db = sqlite3.connect(D.parents[1] / "backend/data/knowledge_base.db")
faq.update(db.execute("select parent_id, content from knowledge_parents"))

old = load_results(BASE / "runs/h1_banking77/results")
new = load_results(D / "runs/h1_banking77/results")
old_items = {json.loads(x)["case_id"]: json.loads(x) for x in (BASE / "results/h1_items.jsonl").read_text().splitlines()}

model = SentenceTransformer("sentence-transformers/all-mpnet-base-v2", local_files_only=True)
texts: list[str] = []
plans: list[tuple[str, list[str]]] = []
for cid in sorted(new):
    item_id = next(tag.split(":", 1)[1] for tag in new[cid]["tags"] if tag.startswith("item:"))
    key = keys.get(item_id, [])
    ov, nv = view(old[cid]), view(new[cid])
    group = [ov["reply"], nv["reply"], *key]
    plans.append((cid, group))
    texts.extend(group)
embeddings = model.encode(texts, normalize_embeddings=True, show_progress_bar=False)

rows = []
offset = 0
for cid, group in plans:
    item_id = next(tag.split(":", 1)[1] for tag in new[cid]["tags"] if tag.startswith("item:"))
    lab = labels[item_id]
    key = keys.get(item_id, [])
    ov, nv = view(old[cid]), view(new[cid])
    block = embeddings[offset:offset + len(group)]
    offset += len(group)
    old_new_cosine = float(block[0] @ block[1])
    old_key_cosines = [float(block[0] @ e) for e in block[2:]]
    new_key_cosines = [float(block[1] @ e) for e in block[2:]]
    old_metric = old_items.get(cid, {})
    exact = ov == nv
    old_numbers = re.findall(r"(?<![A-Za-z])\d+(?:\.\d+)?%?|\$\s*\d+(?:\.\d+)?", ov["reply"])
    new_numbers = re.findall(r"(?<![A-Za-z])\d+(?:\.\d+)?%?|\$\s*\d+(?:\.\d+)?", nv["reply"])
    row = {
        "case_id": cid,
        "item_id": item_id,
        "question": lab["text"],
        "coverage": lab["coverage"],
        "ambiguous": lab["ambiguous"],
        "acceptable_routes": lab["acceptable_routes"],
        "answer_key": key,
        "reference_faq_ids": lab["answering_faq_ids"],
        "reference_faq": {pid: faq.get(pid, "") for pid in lab["answering_faq_ids"]},
        "old": ov,
        "new": nv,
        "old_metric": old_metric,
        "exact_visible_match": exact,
        "reply_cosine": round(old_new_cosine, 4),
        "old_key_cosines": [round(x, 4) for x in old_key_cosines],
        "new_key_cosines": [round(x, 4) for x in new_key_cosines],
        "rough_old_outcome": rough_outcome(ov),
        "rough_new_outcome": rough_outcome(nv),
        "parent_set_changed": ov["parents"] != nv["parents"],
        "ui_changed": any(ov[k] != nv[k] for k in ("nav", "forms", "soft")),
        "numeric_tokens_changed": old_numbers != new_numbers,
    }
    risk = []
    if old_metric.get("b1_pass") and rough_outcome(nv) in {"LIMIT", "REFUSED", "EMPTY"}: risk.append("old_correct_to_no_answer")
    if old_metric.get("resolved") and rough_outcome(nv) not in {"ANSWERED", "SELF_SERVICE"}: risk.append("old_resolved_to_unresolved")
    if row["numeric_tokens_changed"]: risk.append("numbers_changed")
    if row["parent_set_changed"]: risk.append("evidence_changed")
    if row["ui_changed"]: risk.append("channel_changed")
    if old_new_cosine < 0.75: risk.append("reply_materially_changed")
    row["review_flags"] = risk
    rows.append(row)

out = D / "results/paired_response_review.jsonl"
out.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")
summary = {
    "cases": len(rows),
    "exact_visible_matches": sum(r["exact_visible_match"] for r in rows),
    "rough_outcome_transitions": dict(Counter(f"{r['rough_old_outcome']}->{r['rough_new_outcome']}" for r in rows)),
    "flag_counts": dict(Counter(flag for r in rows for flag in r["review_flags"])),
}
(D / "results/paired_response_review_summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
print(json.dumps(summary, indent=2))
