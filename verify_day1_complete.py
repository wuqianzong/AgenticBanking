import os
import sys
import sqlite3
from dotenv import load_dotenv

print("=================================================================")
print("🏁 COMPREHENSIVE DAY 1 INFRASTRUCTURE & API VALIDATION RUNNER")
print("=================================================================\n")

# 1. Directory Checks
base_path = "/Users/bensonwu/Projects/AgenticBanking"
required_dirs = ["backend", "frontend", "data"]
print("📂 Checking Project Directory Tree:")
for d in required_dirs:
    full_path = os.path.join(base_path, d)
    if os.path.exists(full_path):
        print(f"  🟢 {d}/ directory: Verified layout at {full_path}")
    else:
        print(f"  ❌ Missing directory: {full_path}")

# 2. Environment Configurations
print("\n🔑 Verifying Environment Configuration:")
load_dotenv()
api_key = os.getenv("GROQ_API_KEY")
if api_key and "your_actual" not in api_key:
    print(f"  🟢 GROQ_API_KEY: Loaded successfully (Length: {len(api_key)} chars)")
else:
    print("  ❌ GROQ_API_KEY: Missing or placeholder detected in .env file.")

# 3. Redis Functional Check
print("\n🗄️ Testing Transient Redis Cache Operations:")
try:
    import redis
    r = redis.Redis(host='localhost', port=6379, db=0, socket_timeout=2)
    r.set("day1_test_key", "infrastructure_verified")
    val = r.get("day1_test_key").decode('utf-8')
    if val == "infrastructure_verified":
        print("  🟢 Redis Service: Write/Read transaction successful on port 6379.")
    r.delete("day1_test_key")
except Exception as e:
    print(f"  ❌ Redis Diagnostic Failure: {e}")

# 4. SQLite Relational Integrity Check
print("\n💾 Testing SQLite Mock Database Capability:")
try:
    db_path = os.path.join(base_path, "data", "test_diagnostic.db")
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    cursor.execute("CREATE TABLE IF NOT EXISTS health (id INTEGER PRIMARY KEY, status TEXT)")
    cursor.execute("INSERT INTO health (status) VALUES ('operational')")
    conn.commit()
    cursor.execute("SELECT status FROM health ORDER BY id DESC LIMIT 1")
    row = cursor.fetchone()
    if row and row[0] == 'operational':
        print(f"  🟢 SQLite Engine: Local database created and writing cleanly to {db_path}")
    conn.close()
    if os.path.exists(db_path):
        os.remove(db_path)
except Exception as e:
    print(f"  ❌ SQLite Diagnostic Failure: {e}")

# 5. ChromaDB & Vector Calculations Check
print("\n🧠 Testing Local ChromaDB Embeddings Vector Pipeline:")
try:
    import chromadb
    chroma_client = chromadb.EphemeralClient()
    collection = chroma_client.create_collection(name="diagnostic_test")
    collection.add(documents=["Banking App Context"], ids=["id1"])
    results = collection.query(query_texts=["Banking App Context"], n_results=1)
    if results and len(results['ids'][0]) > 0:
        print("  🟢 ChromaDB Engine: Semantic local index storage and retrieval initialized.")
except Exception as e:
    print(f"  ❌ ChromaDB Diagnostic Failure: {e}")

# 6. Remote Groq LLM API Check
print("\n☁️ Testing Remote Groq LLM API Wire Connectivity:")
if api_key and "your_actual" not in api_key:
    try:
        from groq import Groq
        client = Groq(api_key=api_key)
        completion = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[{"role": "user", "content": "Respond with the single word: Connected"}],
            max_tokens=10
        )
        response_text = completion.choices[0].message.content.strip()
        print(f"  🟢 Groq API Telemetry: Received response -> '{response_text}'")
    except Exception as e:
        print(f"  ❌ Groq Cloud Handshake Failed: {e}")
else:
    print("  🟡 Groq Verification Skipped: Valid API token not present in .env")

print("\n=================================================================")
print("🏁 DIAGNOSTIC COMPLETE")
print("=================================================================")
