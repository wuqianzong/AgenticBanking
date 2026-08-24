"""Registration store and state machine for the service_request tool (WP-7).

See docs/SERVICE_REQUEST_DESIGN_v1.0.md Section 5 for the state machine this
implements. All persistence lives in Service_Request_Registration inside
simulated_ledger.db, alongside the other durable customer-scoped tables.

Deliberately only three externally-visible statuses: "pending", "expired",
"completed". This registry tracks that a submission was made, not whether
the underlying banking action ultimately succeeded in whatever legacy
system processes it -- that would require integrating with systems this
project has no visibility into. A customer checking whether a change
actually took effect does so through a read path (api/deeplink/the bank
app), not through this table. A failed credential check is therefore not a
terminal outcome: the registration simply stays "pending" and is
resubmittable until it expires, the same as one that was never submitted.
"""

from __future__ import annotations

import hashlib
import json
import secrets
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Any

from backend.app.services.memory_service import DB_PATH, ensure_memory_schema
from backend.app.core.system_logger import log_event

PENDING = "pending"
EXPIRED = "expired"
COMPLETED = "completed"
NON_TERMINAL_STATUSES = {PENDING}
TERMINAL_STATUSES = {COMPLETED, EXPIRED}
REGISTRATION_TTL_HOURS = 72


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    record = dict(row)
    for json_field in ("product_tags", "intent_tags", "submitted_data", "result_payload"):
        raw = record.get(json_field)
        record[json_field] = json.loads(raw) if raw else ({} if json_field in {"submitted_data", "result_payload"} else [])
    record["requires_step_up"] = bool(record.get("requires_step_up"))
    record["grant_consumed"] = bool(record.get("grant_consumed"))
    return record


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    ensure_memory_schema(conn)
    return conn


def _expire_if_due(conn: sqlite3.Connection, record: dict[str, Any]) -> dict[str, Any]:
    if record["status"] in NON_TERMINAL_STATUSES and record["expires_at"] < _now():
        conn.execute(
            "UPDATE Service_Request_Registration SET status = ? WHERE special_id = ?",
            (EXPIRED, record["special_id"]),
        )
        conn.commit()
        record["status"] = EXPIRED
        log_event("service_request.expired", special_id=record["special_id"])
    return record


def list_registrations(user_id: str) -> list[dict[str, Any]]:
    """All service_request registrations for a customer, newest first.

    Scoped by user_id, not session_id -- this is a to-do list of the
    customer's own service requests, meant to persist and stay visible
    across sessions, not something tied to whichever session happened to
    register it.
    """
    with _connect() as conn:
        cursor = conn.execute(
            "SELECT * FROM Service_Request_Registration WHERE user_id = ? ORDER BY created_at DESC",
            (user_id,),
        )
        records = [_row_to_dict(row) for row in cursor.fetchall()]
        return [_expire_if_due(conn, record) for record in records]


def register(
    session_id: str,
    user_id: str,
    service_request_id: str,
    resolved_customer_component: str,
    product_tags: list[str],
    intent_tags: list[str],
    risk_level: str,
    requires_step_up: bool,
) -> dict[str, Any]:
    special_id = secrets.token_urlsafe(24)
    created_at = _now()
    expires_at = (datetime.now(timezone.utc) + timedelta(hours=REGISTRATION_TTL_HOURS)).isoformat()
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO Service_Request_Registration
            (special_id, session_id, user_id, service_request_id, resolved_customer_component,
             product_tags, intent_tags, risk_level, requires_step_up, status,
             submitted_data, submitted_data_hash, grant_id, grant_consumed, result_payload,
             created_at, expires_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL, NULL, 0, NULL, ?, ?)
            """,
            (
                special_id, session_id, user_id, service_request_id, resolved_customer_component,
                json.dumps(product_tags or []), json.dumps(intent_tags or []),
                risk_level, int(bool(requires_step_up)), PENDING, created_at, expires_at,
            ),
        )
        conn.commit()
    log_event(
        "service_request.registered",
        special_id=special_id, session_id=session_id, service_request_id=service_request_id,
        risk_level=risk_level, requires_step_up=requires_step_up,
    )
    return get(special_id)


def get(special_id: str) -> dict[str, Any] | None:
    with _connect() as conn:
        cursor = conn.execute(
            "SELECT * FROM Service_Request_Registration WHERE special_id = ?", (special_id,)
        )
        row = cursor.fetchone()
        if row is None:
            return None
        record = _row_to_dict(row)
        return _expire_if_due(conn, record)


def mark_submitted(special_id: str, submitted_data: dict[str, Any]) -> dict[str, Any] | None:
    """Record a submission (or a resubmission after a failed credential check).

    Valid whenever the registration is still "pending" -- that covers both
    a fresh, never-submitted form and one that was submitted before but
    reset by a failed credential check (see resolve_credential_check).
    Overwrites any prior submission and clears a stale grant, since a new
    submission is a new payload to authorize from scratch.
    """
    record = get(special_id)
    if record is None or record["status"] != PENDING:
        return record
    canonical = json.dumps(submitted_data or {}, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    data_hash = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    with _connect() as conn:
        conn.execute(
            "UPDATE Service_Request_Registration SET submitted_data = ?, submitted_data_hash = ?, grant_id = NULL WHERE special_id = ?",
            (canonical, data_hash, special_id),
        )
        conn.commit()
    log_event("service_request.submitted", special_id=special_id, data_hash=data_hash)
    return get(special_id)


def resolve_credential_check(special_id: str, passed: bool) -> dict[str, Any] | None:
    """On pass, issues a grant (status stays "pending" until complete_with_result
    runs). On fail, resets the submission entirely and status stays "pending"
    -- a failed check is not terminal; the customer can resubmit until this
    registration expires.
    """
    record = get(special_id)
    if record is None or record["status"] != PENDING or not record.get("submitted_data"):
        return record
    if passed:
        grant_id = secrets.token_urlsafe(24)
        with _connect() as conn:
            conn.execute(
                "UPDATE Service_Request_Registration SET grant_id = ? WHERE special_id = ?",
                (grant_id, special_id),
            )
            conn.commit()
        log_event("service_request.credential_check.passed", special_id=special_id, grant_id=grant_id)
    else:
        with _connect() as conn:
            conn.execute(
                "UPDATE Service_Request_Registration SET submitted_data = NULL, submitted_data_hash = NULL, grant_id = NULL WHERE special_id = ?",
                (special_id,),
            )
            conn.commit()
        log_event("service_request.credential_check.failed", special_id=special_id, level="WARNING")
    return get(special_id)


def complete_with_result(special_id: str, result_payload: dict[str, Any]) -> dict[str, Any] | None:
    """Consumes any grant and records the downstream mock API's result.

    Valid only while "pending", and only once a grant exists for any
    registration that required step-up -- a low-risk registration completes
    straight from a submission, an intermediate-risk one only after
    resolve_credential_check has issued a grant.
    """
    record = get(special_id)
    if record is None or record["status"] != PENDING:
        return record
    if record["requires_step_up"] and not record.get("grant_id"):
        return record
    with _connect() as conn:
        conn.execute(
            "UPDATE Service_Request_Registration SET status = ?, result_payload = ?, grant_consumed = 1 WHERE special_id = ?",
            (COMPLETED, json.dumps(result_payload or {}, ensure_ascii=False), special_id),
        )
        conn.commit()
    log_event("service_request.completed", special_id=special_id)
    return get(special_id)
