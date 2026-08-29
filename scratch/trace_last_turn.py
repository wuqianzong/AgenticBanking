import sqlite3
import json
import sys

def main():
    conn = sqlite3.connect("backend/data/simulated_ledger.db")
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    
    # Get the latest session_id
    cursor.execute("SELECT session_id FROM Event_Log ORDER BY timestamp DESC LIMIT 1")
    row = cursor.fetchone()
    if not row:
        print("No events found.")
        return
    session_id = row["session_id"]
    
    # Get all events for the latest session
    cursor.execute("SELECT event_type, details, timestamp FROM Event_Log WHERE session_id = ? ORDER BY timestamp ASC", (session_id,))
    events = cursor.fetchall()
    
    print(f"Tracing session: {session_id}")
    for event in events:
        event_type = event["event_type"]
        if event_type in ("chat.request", "tool.plan.created", "tool.task.created", "tool.result", "chat.response", "rag.search.requested", "tool.result.service_request"):
            print(f"[{event['timestamp']}] {event_type}: {event['details']}")
            
if __name__ == "__main__":
    main()
