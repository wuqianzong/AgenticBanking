"""Central navigation-request service for the deeplink tool.

The graph may register a request for a destination ID, but it never creates
or trusts a route. This service owns the central allow-list check and returns
a route only for a registered request belonging to the customer.
"""

from __future__ import annotations

import secrets
import sqlite3
import json
from datetime import datetime, timezone
from typing import Any

from backend.app.core.system_logger import log_event
from backend.app.orchestration.deeplink_destination_registry import DEEPLINK_DESTINATION_REGISTRY
from backend.app.services.memory_service import DB_PATH, ensure_memory_schema

REGISTERED = "registered"
RESOLVED = "resolved"
UNAVAILABLE = "unavailable"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    ensure_memory_schema(conn)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS Deep_Link_Migration (
            migration_id TEXT PRIMARY KEY,
            applied_at TEXT NOT NULL
        )
    """)
    _migrate_legacy_navigation_payloads(conn)
    return conn


def _migrate_legacy_navigation_payloads(conn: sqlite3.Connection) -> None:
    """Replace pre-centralization chat routes with opaque request IDs.

    Historic UI payloads used to persist ``destinationId`` and ``route``.
    This one-time migration keeps those buttons usable while removing
    navigation metadata from chat history. It retains only the opaque ID and
    a safe registry-derived display label.
    """
    migration_id = "opaque_navigation_payloads_v2"
    already_applied = conn.execute(
        "SELECT 1 FROM Deep_Link_Migration WHERE migration_id = ?", (migration_id,)
    ).fetchone()
    if already_applied:
        return
    rows = conn.execute(
        """
        SELECT message_id, customer_id, session_id, created_at, ui_payload
        FROM Persistent_Chat_Message
        WHERE ui_payload LIKE '%navigationActions%'
        """
    ).fetchall()
    migrated = 0
    for row in rows:
        try:
            payload = json.loads(row["ui_payload"])
            actions = payload.get("navigationActions", [])
            if not isinstance(actions, list):
                continue
            opaque_actions = []
            changed = False
            for action in actions:
                if not isinstance(action, dict):
                    changed = True
                    continue
                if action.get("linkId"):
                    request = conn.execute(
                        "SELECT destination_id FROM Deep_Link_Request WHERE request_id = ?",
                        (str(action["linkId"]),),
                    ).fetchone()
                    contract = DEEPLINK_DESTINATION_REGISTRY.get(
                        request["destination_id"] if request else ""
                    )
                    if contract is None:
                        changed = True
                        continue
                    opaque_actions.append({
                        "linkId": str(action["linkId"]),
                        "label": contract["label"],
                    })
                    changed = changed or action != opaque_actions[-1]
                    continue
                destination_id = str(action.get("destinationId", "")).strip()
                contract = DEEPLINK_DESTINATION_REGISTRY.get(destination_id)
                if contract is None:
                    changed = True
                    continue
                request_id = secrets.token_urlsafe(24)
                conn.execute(
                    """
                    INSERT INTO Deep_Link_Request
                    (request_id, session_id, user_id, destination_id,
                     resolved_customer_component, status, route, created_at, resolved_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        request_id, row["session_id"], row["customer_id"], destination_id,
                        "Historic chat navigation action", RESOLVED, contract["route"],
                        row["created_at"], _now(),
                    ),
                )
                opaque_actions.append({"linkId": request_id, "label": contract["label"]})
                changed = True
            if changed:
                payload["navigationActions"] = opaque_actions
                conn.execute(
                    "UPDATE Persistent_Chat_Message SET ui_payload = ? WHERE message_id = ?",
                    (json.dumps(payload, ensure_ascii=False), row["message_id"]),
                )
                migrated += 1
        except (TypeError, ValueError, json.JSONDecodeError):
            # A malformed historic UI payload remains readable as text; never
            # block current navigation because an old message cannot migrate.
            continue
    conn.execute(
        "INSERT INTO Deep_Link_Migration (migration_id, applied_at) VALUES (?, ?)",
        (migration_id, _now()),
    )
    conn.commit()
    if migrated:
        log_event("deeplink.history_payloads.migrated", message_count=migrated)


def _row_to_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
    return dict(row) if row is not None else None


def get(request_id: str) -> dict[str, Any] | None:
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM Deep_Link_Request WHERE request_id = ?", (request_id,)
        ).fetchone()
    return _row_to_dict(row)


def register(*, session_id: str, user_id: str, destination_id: str,
             resolved_customer_component: str) -> dict[str, Any]:
    """Persist a navigation request before central route resolution."""
    request_id = secrets.token_urlsafe(24)
    created_at = _now()
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO Deep_Link_Request
            (request_id, session_id, user_id, destination_id,
             resolved_customer_component, status, route, created_at, resolved_at)
            VALUES (?, ?, ?, ?, ?, ?, NULL, ?, NULL)
            """,
            (request_id, session_id, user_id, destination_id,
             resolved_customer_component, REGISTERED, created_at),
        )
        conn.commit()
    log_event("deeplink.request.registered", request_id=request_id,
              session_id=session_id, destination_id=destination_id)
    return get(request_id) or {}


def resolve_registered_request(request_id: str, user_id: str | None = None) -> dict[str, Any] | None:
    """Central allow-list decision for one registered navigation request."""
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM Deep_Link_Request WHERE request_id = ?", (request_id,)
        ).fetchone()
        record = _row_to_dict(row)
        if record is None or (user_id is not None and record["user_id"] != user_id):
            return None
        if record["status"] == RESOLVED:
            return record

        contract = DEEPLINK_DESTINATION_REGISTRY.get(record["destination_id"])
        if contract is None:
            conn.execute(
                "UPDATE Deep_Link_Request SET status = ?, resolved_at = ? WHERE request_id = ?",
                (UNAVAILABLE, _now(), request_id),
            )
            conn.commit()
            log_event("deeplink.request.unavailable", request_id=request_id,
                      destination_id=record["destination_id"], level="WARNING")
            return get(request_id)

        route = contract["route"]
        conn.execute(
            "UPDATE Deep_Link_Request SET status = ?, route = ?, resolved_at = ? WHERE request_id = ?",
            (RESOLVED, route, _now(), request_id),
        )
        conn.commit()

    log_event("deeplink.request.resolved", request_id=request_id,
              destination_id=record["destination_id"], route=route)
    return get(request_id)


def register_and_resolve(*, session_id: str, user_id: str, destination_id: str,
                         resolved_customer_component: str) -> dict[str, Any] | None:
    """Graph adapter: register first, then invoke the central resolver."""
    request = register(
        session_id=session_id,
        user_id=user_id,
        destination_id=destination_id,
        resolved_customer_component=resolved_customer_component,
    )
    return resolve_registered_request(request["request_id"], user_id=user_id)
