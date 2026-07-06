import os
import sqlite3
import uuid
from datetime import datetime
from typing import List, Dict, Any
from langchain_core.messages import BaseMessage, HumanMessage, AIMessage
from backend.app.core.llm_service import call_groq_lpu

# ⚡ FIXED PATH: Resolves up TWO levels from backend/app/services/ to find backend/data/
DB_PATH = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "data", "simulated_ledger.db"))
MAX_SHORT_TERM_TURNS = 6  # Rolling sliding context window constraint limit
TARGET_SUMMARY_TOKENS = 100

def ensure_db_dir_exists():
    """🔒 Self-Healing Safeguard: Programmatically forces folder creation if missing."""
    db_dir = os.path.dirname(DB_PATH)
    if not os.path.exists(db_dir):
        print(f"🛠️ [MEMORY SERVICE] Parent directory missing. Creating path: {db_dir}")
        os.makedirs(db_dir, exist_ok=True)

class ShortTermMemoryManager:
    @staticmethod
    def trim_history(messages: List[BaseMessage]) -> List[BaseMessage]:
        """
        Implements a rolling sliding window memory filter.
        Ensures the short-term context history never pollutes the LLM context window.
        """
        if not messages:
            return []
            
        if len(messages) <= MAX_SHORT_TERM_TURNS:
            return messages
            
        print(f"✂️ [MEMORY SERVICE] Trimming short-term cache from {len(messages)} to last {MAX_SHORT_TERM_TURNS} messages.")
        return messages[-MAX_SHORT_TERM_TURNS:]


class LongTermMemoryManager:
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
            ORDER BY session_end_timestamp DESC LIMIT 1
        """
        try:
            with sqlite3.connect(DB_PATH) as conn:
                cursor = conn.cursor()
                cursor.execute("""
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
                conn.commit()
                
                cursor.execute(query, (customer_id,))
                row = cursor.fetchone()
                if row and row[0]:
                    print(f"💾 [MEMORY SERVICE] Hydrated long-term summary for {customer_id}: '{row[0]}'")
                    return row[0]
        except Exception as e:
            print(f"⚠️ [MEMORY SERVICE Warning] SQLite read failed: {e}")
            
        return "System Profile: Authenticated Customer Mr. Wu. No prior session context flagged."

    @staticmethod
    def compile_and_archive_session(customer_id: str, session_id: str, messages: List[BaseMessage]) -> str:
        """
        Asymmetric State Compaction: Triggered at session checkout/timeout.
        """
        if not messages:
            return "Empty session thread discarded."
            
        ensure_db_dir_exists()
            
        serialized_transcript = ""
        for msg in messages:
            role = "Customer" if isinstance(msg, HumanMessage) else "Horizon Bot"
            serialized_transcript += f"{role}: {msg.content}\n"
            
        compaction_prompt = f"""
        You are an enterprise banking context-compaction utility module.
        Compress this raw banking chat log into a single high-density summary footprint block.
        Target scale: exactly under {TARGET_SUMMARY_TOKENS} tokens.
        
        CRITICAL FOCUS FIELDS:
        - What specific banking product was the customer exploring?
        - What precise bottleneck did they hit (missing papers, policy validation, etc.)?
        - What was the unresolved next step?
        
        Raw Log to Compact:
        \"\"\"
        {serialized_transcript}
        \"\"\"
        
        Output ONLY the concise, high-density summary string. No introduction or chat markers.
        """
        
        try:
            print(f"🔄 [MEMORY SERVICE] Launching out-of-band summary compilation pass via LLM...")
            compact_summary = call_groq_lpu(compaction_prompt).strip()
            
            with sqlite3.connect(DB_PATH) as conn:
                cursor = conn.cursor()
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
            print(f"✅ [MEMORY SERVICE] Successfully archived compiled summary to database for {customer_id}.")
            return compact_summary
        except Exception as e:
            print(f"🚨 [MEMORY SERVICE CRITICAL ERROR] Compaction archiving pass failed: {e}")
            return "Error packing state tracking variables."