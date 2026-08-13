import os
import sqlite3
import uuid
import json
from datetime import datetime
from typing import List, Dict, Any
from langchain_core.messages import BaseMessage, HumanMessage, AIMessage
from backend.app.core.llm_service import call_groq_lpu

# ⚡ FIXED PATH: Resolves up TWO levels from backend/app/services/ to find backend/data/
DB_PATH = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "data", "simulated_ledger.db"))
MAX_SHORT_TERM_TURNS = 10
MAX_SHORT_TERM_MESSAGES = MAX_SHORT_TERM_TURNS * 2
TARGET_SUMMARY_TOKENS = 100
USER_BASELINE_SESSION_LIMIT = 5
USER_BASELINE_TARGET_TOKENS = 150


def _extract_json_string(raw_response: str, field_name: str) -> str:
    """Read a required string field from the JSON-only shared LLM client."""
    cleaned = raw_response.strip()
    if cleaned.startswith("```json"):
        cleaned = cleaned[7:]
    elif cleaned.startswith("```"):
        cleaned = cleaned[3:]
    if cleaned.endswith("```"):
        cleaned = cleaned[:-3]

    parsed = json.loads(cleaned.strip())
    value = parsed.get(field_name, "")
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"LLM response did not contain a non-empty '{field_name}' string.")
    return value.strip()

def ensure_db_dir_exists():
    """🔒 Self-Healing Safeguard: Programmatically forces folder creation if missing."""
    db_dir = os.path.dirname(DB_PATH)
    if not os.path.exists(db_dir):
        print(f"🛠️ [MEMORY SERVICE] Parent directory missing. Creating path: {db_dir}")
        os.makedirs(db_dir, exist_ok=True)


def ensure_memory_schema(conn: sqlite3.Connection) -> None:
    """Create the durable, user-scoped conversation history tables."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS Persistent_Customer_Dialogue_Registry (
            history_id TEXT PRIMARY KEY,
            customer_id TEXT,
            session_id TEXT,
            session_start_timestamp TEXT,
            session_end_timestamp TEXT,
            raw_chat_transcript TEXT,
            compact_summary_footprint TEXT,
            session_exit_status TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS Persistent_Chat_Message (
            message_id TEXT PRIMARY KEY,
            customer_id TEXT NOT NULL,
            session_id TEXT NOT NULL,
            message_sequence INTEGER NOT NULL,
            role TEXT NOT NULL CHECK(role IN ('user', 'assistant')),
            content TEXT NOT NULL,
            created_at TEXT NOT NULL,
            archive_reason TEXT NOT NULL,
            ui_payload TEXT,
            UNIQUE(session_id, message_sequence)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS Persistent_User_Baseline_Memory (
            customer_id TEXT PRIMARY KEY,
            baseline_summary TEXT NOT NULL,
            source_session_ids TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            schema_version INTEGER NOT NULL
        )
    """)
    # Backward-compatible migration for databases created before structured UI
    # payloads were persisted with each assistant message.
    existing_columns = {
        row[1] for row in conn.execute("PRAGMA table_info(Persistent_Chat_Message)")
    }
    if "ui_payload" not in existing_columns:
        conn.execute("ALTER TABLE Persistent_Chat_Message ADD COLUMN ui_payload TEXT")
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_persistent_chat_message_owner
        ON Persistent_Chat_Message(customer_id, session_id, message_sequence)
    """)

class ShortTermMemoryManager:
    @staticmethod
    def trim_history(messages: List[BaseMessage]) -> List[BaseMessage]:
        """
        Implements a rolling sliding window memory filter.
        Ensures the short-term context history never pollutes the LLM context window.
        """
        if not messages:
            return []
            
        if len(messages) <= MAX_SHORT_TERM_MESSAGES:
            return messages
            
        print(f"✂️ [MEMORY SERVICE] Trimming short-term cache from {len(messages)} to last {MAX_SHORT_TERM_TURNS} turns.")
        return messages[-MAX_SHORT_TERM_MESSAGES:]


class LongTermMemoryManager:
    @staticmethod
    def fetch_user_baseline(customer_id: str) -> str:
        """Fetch optional advisory baseline; a missing record is normal."""
        ensure_db_dir_exists()
        try:
            with sqlite3.connect(DB_PATH) as conn:
                ensure_memory_schema(conn)
                row = conn.execute("""
                    SELECT baseline_summary
                    FROM Persistent_User_Baseline_Memory
                    WHERE customer_id = ?
                """, (customer_id,)).fetchone()
            return row[0].strip() if row and row[0] else ""
        except Exception as error:
            print(f"⚠️ [MEMORY SERVICE] User baseline read failed: {error}")
            return ""

    @staticmethod
    def refresh_user_baseline(customer_id: str) -> str:
        """Consolidate up to five completed session summaries into user context."""
        ensure_db_dir_exists()
        try:
            with sqlite3.connect(DB_PATH) as conn:
                ensure_memory_schema(conn)
                rows = conn.execute("""
                    SELECT session_id, compact_summary_footprint
                    FROM Persistent_Customer_Dialogue_Registry
                    WHERE customer_id = ?
                      AND compact_summary_footprint IS NOT NULL
                      AND TRIM(compact_summary_footprint) != ''
                      AND compact_summary_footprint NOT LIKE 'Error packing%'
                    GROUP BY session_id
                    ORDER BY MAX(session_end_timestamp) DESC
                    LIMIT ?
                """, (customer_id, USER_BASELINE_SESSION_LIMIT)).fetchall()

            # First-time users and summaries that failed to compile simply have
            # no baseline. The session itself remains fully usable.
            if not rows:
                return ""

            source_session_ids = [row[0] for row in rows]
            session_summaries = "\n".join(
                f"Session {index + 1} (newest first): {row[1]}"
                for index, row in enumerate(rows)
            )
            prompt = f"""
            Build a read-only customer baseline from up to {USER_BASELINE_SESSION_LIMIT}
            completed banking-chat session summaries. Keep it under
            {USER_BASELINE_TARGET_TOKENS} tokens.

            Preserve only stable, useful context: recurring banking topics,
            explicit communication preferences, explicitly stated facts, and
            unresolved themes. Prefer newer explicit information if sessions
            conflict. Do not infer financial preferences or make decisions for
            the customer.

            STRICT UI EXCLUSION: Never include chips, buttons, checkbox labels,
            stepper items, suggested replies, or UI payloads. They are not user
            choices. Do not claim any workflow or application remains active.

            Completed session summaries:
            {session_summaries}

            Output JSON only: {{"baseline": "concise user baseline"}}.
            """
            baseline = _extract_json_string(call_groq_lpu(prompt), "baseline")
            with sqlite3.connect(DB_PATH) as conn:
                ensure_memory_schema(conn)
                conn.execute("""
                    INSERT INTO Persistent_User_Baseline_Memory
                    (customer_id, baseline_summary, source_session_ids, updated_at, schema_version)
                    VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT(customer_id) DO UPDATE SET
                        baseline_summary = excluded.baseline_summary,
                        source_session_ids = excluded.source_session_ids,
                        updated_at = excluded.updated_at,
                        schema_version = excluded.schema_version
                """, (
                    customer_id,
                    baseline,
                    json.dumps(source_session_ids),
                    datetime.now().isoformat(timespec="seconds"),
                    1,
                ))
                conn.commit()
            print(
                f"🧠 [MEMORY SERVICE] Refreshed user baseline for '{customer_id}' "
                f"from {len(source_session_ids)} completed session(s)."
            )
            return baseline
        except Exception as error:
            # A baseline is optional personalization. Preserve the previous
            # version if this refresh fails, rather than breaking checkout.
            print(f"⚠️ [MEMORY SERVICE] User baseline refresh skipped: {error}")
            return LongTermMemoryManager.fetch_user_baseline(customer_id)

    @staticmethod
    def summarize_trimmed_history(existing_summary: str, messages: List[BaseMessage]) -> str:
        """Merge evicted messages into compact context, excluding UI metadata."""
        if not messages:
            return existing_summary

        transcript = "\n".join(
            f"{'Customer' if isinstance(message, HumanMessage) else 'Horizon Bot'}: {message.content}"
            for message in messages
        )
        prompt = f"""
        Update a compact banking conversation memory for a future assistant turn.
        Preserve: user goals, product/journey, verified facts provided, decisions,
        completed steps, unresolved questions, and required next actions.
        Do not invent facts. Keep it under {TARGET_SUMMARY_TOKENS} tokens.

        STRICT UI EXCLUSION:
        Chips, buttons, checkbox labels, stepper items, suggested replies, and
        every other UI payload are presentation metadata, not customer choices
        or conversation facts. Do not summarize, infer, or retain them. Only a
        message explicitly sent by the customer is a customer decision.

        Existing compact memory:
        {existing_summary or 'None'}

        Newly evicted conversation:
        {transcript}

        Output JSON only: {{"summary": "updated compact memory"}}.
        """
        return _extract_json_string(call_groq_lpu(prompt), "summary")
    @staticmethod
    def fetch_compact_footprint(customer_id: str) -> str:
        """
        Executes a localized relational query against Table B to hydrate 
        the session turn initialization node on Turn 1.
        """
        # Enforce directory path verification before database interaction
        ensure_db_dir_exists()
        
        query = """
            SELECT compact_summary_footprint 
            FROM Persistent_Customer_Dialogue_Registry 
            WHERE customer_id = ?
              AND compact_summary_footprint IS NOT NULL
              AND TRIM(compact_summary_footprint) != ''
              AND compact_summary_footprint NOT LIKE 'Error packing%'
            ORDER BY session_end_timestamp DESC LIMIT 1
        """
        try:
            with sqlite3.connect(DB_PATH) as conn:
                cursor = conn.cursor()
                ensure_memory_schema(conn)
                conn.commit()
                
                cursor.execute(query, (customer_id,))
                row = cursor.fetchone()
                if row and row[0]:
                    print(f"💾 [MEMORY SERVICE] Hydrated long-term summary for {customer_id}: '{row[0]}'")
                    return row[0]
        except Exception as e:
            print(f"⚠️ [MEMORY SERVICE Warning] SQLite read failed: {e}")
            
        return ""

    @staticmethod
    def persist_chat_history(
        customer_id: str,
        session_id: str,
        messages: List[BaseMessage],
        archive_reason: str,
        starting_sequence: int = 0,
    ) -> int:
        """Idempotently persist an exact, chronologically ordered session transcript."""
        if not messages:
            return 0

        ensure_db_dir_exists()
        archived_at = datetime.now().isoformat(timespec="seconds")
        rows = [
            (
                str(uuid.uuid4()),
                customer_id,
                session_id,
                sequence,
                "user" if isinstance(message, HumanMessage) else "assistant",
                str(message.content),
                archived_at,
                archive_reason,
            )
            for sequence, message in enumerate(messages, start=starting_sequence)
        ]

        try:
            with sqlite3.connect(DB_PATH) as conn:
                ensure_memory_schema(conn)
                before_count = conn.total_changes
                conn.executemany("""
                    INSERT OR IGNORE INTO Persistent_Chat_Message
                    (message_id, customer_id, session_id, message_sequence, role, content, created_at, archive_reason)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """, rows)
                conn.commit()
                inserted_count = conn.total_changes - before_count
            print(
                f"💾 [MEMORY SERVICE] Archived {inserted_count} chat messages for "
                f"user '{customer_id}', session '{session_id}' ({archive_reason})."
            )
            return inserted_count
        except Exception as e:
            print(f"🚨 [MEMORY SERVICE] Chat-history archive failed: {e}")
            raise

    @staticmethod
    def fetch_chat_history(customer_id: str, session_id: str | None = None) -> List[Dict[str, Any]]:
        """Read only this customer's ordered transcript; never mix customer records."""
        ensure_db_dir_exists()
        query = """
            SELECT session_id, message_sequence, role, content, created_at
            FROM Persistent_Chat_Message
            WHERE customer_id = ?
        """
        params: List[str] = [customer_id]
        if session_id:
            query += " AND session_id = ?"
            params.append(session_id)
        query += " ORDER BY session_id, message_sequence"

        with sqlite3.connect(DB_PATH) as conn:
            ensure_memory_schema(conn)
            rows = conn.execute(query, params).fetchall()
        return [
            {
                "session_id": row[0],
                "sequence": row[1],
                "role": row[2],
                "content": row[3],
                "created_at": row[4],
            }
            for row in rows
        ]

    @staticmethod
    def fetch_chat_history_page(
        customer_id: str,
        session_id: str,
        limit: int,
        before_sequence: int | None = None,
    ) -> Dict[str, Any]:
        """Return one user-owned transcript page, newest first at the query boundary."""
        ensure_db_dir_exists()
        where_clause = "customer_id = ? AND session_id = ?"
        params: List[Any] = [customer_id, session_id]
        if before_sequence is not None:
            where_clause += " AND message_sequence < ?"
            params.append(before_sequence)

        query = f"""
            SELECT message_sequence, role, content, created_at, ui_payload
            FROM Persistent_Chat_Message
            WHERE {where_clause}
            ORDER BY message_sequence DESC
            LIMIT ?
        """
        with sqlite3.connect(DB_PATH) as conn:
            ensure_memory_schema(conn)
            rows = conn.execute(query, [*params, limit]).fetchall()
            has_prior_history = conn.execute("""
                SELECT 1 FROM Persistent_Chat_Message
                WHERE customer_id = ? AND session_id != ?
                LIMIT 1
            """, (customer_id, session_id)).fetchone() is not None

        messages = [
            {
                "sequence": row[0],
                "role": row[1],
                "content": row[2],
                "created_at": row[3],
                "ui_payload": json.loads(row[4]) if row[4] else None,
            }
            for row in reversed(rows)
        ]
        oldest_sequence = messages[0]["sequence"] if messages else None
        return {
            "messages": messages,
            "next_before_sequence": oldest_sequence,
            "has_more": len(rows) == limit and oldest_sequence is not None and oldest_sequence > 0,
            "has_prior_history": has_prior_history,
        }

    @staticmethod
    def fetch_prior_session_history_page(
        customer_id: str,
        current_session_id: str,
        limit: int,
        before_row_id: int | None = None,
    ) -> Dict[str, Any]:
        """Page through this user's earlier sessions, newest messages first."""
        ensure_db_dir_exists()
        where_clause = "customer_id = ? AND session_id != ?"
        params: List[Any] = [customer_id, current_session_id]
        if before_row_id is not None:
            where_clause += " AND rowid < ?"
            params.append(before_row_id)

        query = f"""
            SELECT rowid, session_id, message_sequence, role, content, created_at, ui_payload
            FROM Persistent_Chat_Message
            WHERE {where_clause}
            ORDER BY rowid DESC
            LIMIT ?
        """
        with sqlite3.connect(DB_PATH) as conn:
            ensure_memory_schema(conn)
            rows = conn.execute(query, [*params, limit]).fetchall()

        messages = [
            {
                "row_id": row[0],
                "session_id": row[1],
                "sequence": row[2],
                "role": row[3],
                "content": row[4],
                "created_at": row[5],
                "ui_payload": json.loads(row[6]) if row[6] else None,
            }
            for row in reversed(rows)
        ]
        return {
            "messages": messages,
            "next_before_row_id": messages[0]["row_id"] if messages else None,
            "has_more": len(rows) == limit,
        }

    @staticmethod
    def persist_message_ui_payload(
        customer_id: str,
        session_id: str,
        message_sequence: int,
        ui_payload: Dict[str, Any],
    ) -> None:
        """Attach the structured UI contract to its persisted assistant message."""
        ensure_db_dir_exists()
        with sqlite3.connect(DB_PATH) as conn:
            ensure_memory_schema(conn)
            conn.execute("""
                UPDATE Persistent_Chat_Message
                SET ui_payload = ?
                WHERE customer_id = ? AND session_id = ? AND message_sequence = ? AND role = 'assistant'
            """, (json.dumps(ui_payload), customer_id, session_id, message_sequence))
            conn.commit()

    @staticmethod
    def compile_and_archive_session(
        customer_id: str,
        session_id: str,
        messages: List[BaseMessage],
        starting_sequence: int = 0,
    ) -> str:
        """
        Asymmetric State Compaction: Triggered at session checkout/timeout.
        """
        if not messages:
            return "Empty session thread discarded."
            
        ensure_db_dir_exists()
        LongTermMemoryManager.persist_chat_history(
            customer_id=customer_id,
            session_id=session_id,
            messages=messages,
            archive_reason="session_checkout",
            starting_sequence=starting_sequence,
        )

        persisted_messages = LongTermMemoryManager.fetch_chat_history(customer_id, session_id)
        serialized_transcript = "".join(
            f"{'Customer' if message['role'] == 'user' else 'Horizon Bot'}: {message['content']}\n"
            for message in persisted_messages
        )
            
        compaction_prompt = f"""
        You are an enterprise banking context-compaction utility module.
        Compress this raw banking chat log into a single high-density summary footprint block.
        Target scale: exactly under {TARGET_SUMMARY_TOKENS} tokens.
        
        CRITICAL FOCUS FIELDS:
        - What specific banking product was the customer exploring?
        - What precise bottleneck did they hit (missing papers, policy validation, etc.)?
        - What was the unresolved next step?

        STRICT UI EXCLUSION:
        Do not include chips, buttons, checkbox labels, stepper entries,
        suggested replies, UI actions, or any other payload options in this
        summary. They are not user choices and must not influence the recalled
        context. Preserve only explicit customer messages and the assistant's
        substantive textual answer.
        
        Raw Log to Compact:
        \"\"\"
        {serialized_transcript}
        \"\"\"
        
        Output JSON only: {{"summary": "concise high-density summary"}}.
        Do not include an introduction or chat markers in the summary value.
        """
        
        try:
            print(f"🔄 [MEMORY SERVICE] Launching out-of-band summary compilation pass via LLM...")
            compact_summary = _extract_json_string(
                call_groq_lpu(compaction_prompt),
                "summary",
            )
            
            with sqlite3.connect(DB_PATH) as conn:
                cursor = conn.cursor()
                ensure_memory_schema(conn)
                cursor.execute("""
                    INSERT INTO Persistent_Customer_Dialogue_Registry 
                    (history_id, customer_id, session_id, session_start_timestamp, session_end_timestamp, raw_chat_transcript, compact_summary_footprint, session_exit_status)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    str(uuid.uuid4()),
                    customer_id,
                    session_id,
                    str(datetime.now()),
                    str(datetime.now()),
                    serialized_transcript,
                    compact_summary,
                    "CHECKOUT"
                ))
                conn.commit()
            LongTermMemoryManager.refresh_user_baseline(customer_id)
            print(f"✅ [MEMORY SERVICE] Successfully archived compiled summary to database for {customer_id}.")
            return compact_summary
        except Exception as e:
            print(f"🚨 [MEMORY SERVICE CRITICAL ERROR] Compaction archiving pass failed: {e}")
            return "Error packing state tracking variables."
