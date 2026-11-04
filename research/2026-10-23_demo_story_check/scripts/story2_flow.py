"""Story 2 end to end through the real API, which the message-only study runner cannot do: log in, ask for the
address on file, ask to change it, leave the Change Address form unsubmitted, log out, log back in (the greeting
should remind the customer of the pending request), then submit the form.
Run from the isolated root:  cd isolated_root && python -u ../scripts/story2_flow.py
"""
import json
import os
import socket
import sys
import threading
import time
import uuid
from pathlib import Path

D = Path("/Users/bensonwu/Projects/AgenticBanking/research/2026-10-23_demo_story_check")
sys.path.insert(0, os.getcwd())
sys.path.insert(1, str(D / "scripts"))

from cases import STORY2  # noqa: E402
from research._framework.common import (  # noqa: E402
    ApiKeyPool, _api_turn, extract_state_summary, extract_system_log_events_from, install_dedicated_capture_log, utc_now,
)

OUT = D / "runs/story2/results"
CAPTURE = OUT / "capture_log.jsonl"
USER = "USR_999"
PACING = 3
offset = CAPTURE.stat().st_size if CAPTURE.exists() else 0
install_dedicated_capture_log(CAPTURE)
progress = open(OUT / "progress.txt", "a", encoding="utf-8")
pool = ApiKeyPool()
if pool.available:
    pool.install_call_site_rotation(progress, all_keys_wait_seconds=600)

import httpx  # noqa: E402
import uvicorn  # noqa: E402
from backend.app.api_gateway import MODEL_UNAVAILABLE_MESSAGE, app  # noqa: E402
from backend.app.orchestration.graph import app_graph  # noqa: E402

with socket.socket() as probe:
    probe.bind(("127.0.0.1", 0))
    port = probe.getsockname()[1]
server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
threading.Thread(target=server.run, daemon=True).start()
while not server.started:
    time.sleep(0.1)
client = httpx.Client(base_url=f"http://127.0.0.1:{port}", timeout=httpx.Timeout(900.0))


def log(msg):
    progress.write(f"{utc_now()} {msg}\n")
    progress.flush()


def turn(session_id, message, index):
    for attempt in range(1, 5):
        outcome = _api_turn(client, session_id, USER, message, "gemini")
        complete = outcome.get("complete") or {}
        text = str(complete.get("text", ""))
        if text.strip().startswith(MODEL_UNAVAILABLE_MESSAGE) and attempt < 4:
            log(f"turn {index} model unavailable; retry in 600s")
            time.sleep(600)
            continue
        summary = extract_state_summary(app_graph.get_state({"configurable": {"thread_id": session_id}}).values or {})
        log(f"turn {index} done in {outcome.get('elapsed_seconds')}s")
        return {"turn_index": index, "message": message, "status": "OK", "attempts": attempt,
                "first_feedback_seconds": outcome.get("first_feedback_seconds"),
                "elapsed_seconds": outcome.get("elapsed_seconds"), "reply_text": text,
                "ui": {k: complete.get(k) for k in ("uiType", "navigationActions", "serviceRequestActions",
                                                    "softEscalationAvailable") if k in complete},
                "personal_data_reveal_count": len(complete.get("personalDataReveals") or []),
                "summary": summary}


log("=== story 2 flow started ===")
session_a = f"eval_story2_a_{uuid.uuid4().hex[:8]}"
steps = {"login_a": turn(session_a, "**INIT**", 0)}
turns = []
for i, message in enumerate(STORY2["messages"], start=1):
    time.sleep(PACING)
    turns.append(turn(session_a, message, i))
special_id = next((a.get("specialId") for t in turns for a in (t["ui"].get("serviceRequestActions") or [])
                   if a.get("label") == "Change Address"), None)
log(f"Change Address special_id={special_id}")
steps["logout_a"] = client.post("/api/session/checkout",
                                json={"session_id": session_a, "user_id": USER, "reason": "logout"}).json()
time.sleep(PACING)
session_b = f"eval_story2_b_{uuid.uuid4().hex[:8]}"
steps["login_b"] = turn(session_b, "**INIT**", 0)
steps["service_requests_listed"] = client.get(f"/api/service-request/list/{USER}").json()
if special_id:
    steps["form_opened"] = client.get(f"/api/service-request/{special_id}").json()
    steps["form_submitted"] = client.post("/api/service-request/submit", json={
        "special_id": special_id, "user_id": USER, "session_id": session_b,
        "submitted_data": STORY2["submitted_data"]}).json()
steps["logout_b"] = client.post("/api/session/checkout",
                                json={"session_id": session_b, "user_id": USER, "reason": "logout"}).json()

events = []
for sid in (session_a, session_b):
    found, _ = extract_system_log_events_from(CAPTURE, sid, offset)
    events.extend(found)
record = {"study_id": "2026-10-23_demo_story_check_story2", "case_id": STORY2["case_id"], "title": STORY2["title"],
          "session_ids": [session_a, session_b], "special_id": special_id, "status": "OK", "turns": turns,
          "steps": steps, "system_log_events": events, "completed_at": utc_now()}
with open(OUT / "story2_results.jsonl", "a", encoding="utf-8") as f:
    f.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
log("=== story 2 flow finished ===")
client.close()
server.should_exit = True
