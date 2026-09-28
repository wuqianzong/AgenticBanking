import sqlite3
import chromadb
import os

# SQLite Ledger Client Setup
os.makedirs("backend/data", exist_ok=True)
db_conn = sqlite3.connect("backend/data/simulated_ledger.db", check_same_thread=False)
db_cursor = db_conn.cursor()
db_cursor.execute("CREATE TABLE IF NOT EXISTS customer_profile (user_id TEXT PRIMARY KEY, kyc_status TEXT, account_tier TEXT)")
db_cursor.execute("INSERT OR REPLACE INTO customer_profile (user_id, kyc_status, account_tier) VALUES ('USR_999', 'Verified', 'Wealth')")
db_conn.commit()

# Local Knowledge Vector Memory (ChromaDB Ephemeral Store)
chroma_client = chromadb.EphemeralClient()
knowledge_collection = chroma_client.get_or_create_collection("banking_knowledge")
knowledge_collection.upsert(
    ids=["doc1", "doc2"],
    documents=[
        "Trust Bank savings accounts have zero minimum balance and no fall-below fees.",
        "To open a fixed deposit, you must transfer a minimum of $5,000 via internet banking."
    ],
    metadatas=[{"category": "fees"}, {"category": "onboarding"}]
)