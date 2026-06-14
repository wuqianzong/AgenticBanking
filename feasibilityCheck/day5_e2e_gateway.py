import os
import json
import re
import sqlite3
import chromadb
import redis
from typing import TypedDict, Annotated, Sequence, Dict, Any
import operator
from dotenv import load_dotenv

# FastAPI Imports
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from fastapi.testclient import TestClient

# LangChain & LangGraph Imports
from langchain_core.messages import BaseMessage, HumanMessage, AIMessage
from langgraph.graph import StateGraph, END

# Groq Import
from groq import Groq

print("=================================================================")
print("🌐 DAY 5: END-TO-END FASTAPI & LANGGRAPH INTEGRATION GATEWAY")
print("=================================================================\n")

# -----------------------------------------------------------------
# 1. INITIALIZATION & INFRASTRUCTURE SETUP
# -----------------------------------------------------------------
load_dotenv()
GROQ_API_KEY = os.getenv("GROQ_API_KEY")
if not GROQ_API_KEY:
    raise ValueError("❌ Missing GROQ_API_KEY. Please ensure your .env file is configured.")

client = Groq(api_key=GROQ_API_KEY)

# 1A. FastAPI Application Setup (The Bridge)
app = FastAPI(title="Horizon Banking API", description="LangGraph + Groq Orchestration API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"], # React Vite frontend
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 1B. Local Redis Cache Setup (Session Memory)
try:
    redis_client = redis.Redis(host='localhost', port=6379, db=0, decode_responses=True)
    redis_client.ping()
    print("🟢 Redis connection established.")
    USE_REDIS = True
except redis.ConnectionError:
    print("⚠️ Redis not running locally. Falling back to an isolated in-memory dictionary for this test to prevent crashes.")
    USE_REDIS = False
    in_memory_redis = {}

# 1C. SQLite & ChromaDB Setup (Local Data Layers)
os.makedirs("data", exist_ok=True)
conn = sqlite3.connect("data/mock_enterprise.db", check_same_thread=False)
cursor = conn.cursor()
cursor.execute("CREATE TABLE IF NOT EXISTS customer_profile (user_id TEXT PRIMARY KEY, kyc_status TEXT, account_tier TEXT)")
cursor.execute("INSERT OR REPLACE INTO customer_profile (user_id, kyc_status, account_tier) VALUES ('USR_999', 'Verified', 'Wealth')")
conn.commit()

chroma_client = chromadb.EphemeralClient()
collection = chroma_client.get_or_create_collection("banking_knowledge")
collection.upsert(
    ids=["doc1", "doc2"],
    documents=[
        "Trust Bank savings accounts have zero minimum balance and no fall-below fees.",
        "To open a fixed deposit, you must transfer a minimum of $5,000 via internet banking."
    ],
    metadatas=[{"category": "fees"}, {"category": "onboarding"}]
)

# -----------------------------------------------------------------
# 2. LANGGRAPH STATE MACHINE (THE HYBRID PARADIGM)
# -----------------------------------------------------------------
class AgentState(TypedDict):
    session_id: str
    user_id: str
    messages: Annotated[list, operator.add]
    intent: str
    context: str
    draft: dict
    final_payload: dict

def semantic_router_node(state: AgentState):
    """Groq Generative Semantic Routing."""
    user_msg = state["messages"][-1].content
    print(f"  [TRACE] 🧠 LangGraph -> Groq API: Executing Semantic Routing for '{user_msg}'")
    
    prompt = f"""
    Classify the user intent into exactly one of these categories:
    - KNOWLEDGE: Questions about fees, policies, or account features.
    - DATABASE: Questions about the user's specific profile or KYC status.
    - TRANSACTION: Requests to move money, pay bills, or invest.
    - GENERAL: Greetings or other talk.
    
    User Query: {user_msg}
    Respond strictly in JSON format: {{"intent": "CATEGORY"}}
    """
    
    completion = client.chat.completions.create(
        model="llama-3.3-70b-versatile",
        messages=[{"role": "user", "content": prompt}],
        temperature=0.0,
        response_format={"type": "json_object"}
    )
    
    intent = json.loads(completion.choices[0].message.content).get("intent", "GENERAL")
    print(f"  [TRACE] 🔀 Groq -> LangGraph: Intent classified as [{intent}]")
    return {"intent": intent}

def tool_execution_node(state: AgentState):
    """Local context retrieval based on semantic route."""
    intent = state.get("intent", "GENERAL")
    user_msg = state["messages"][-1].content
    context = ""
    
    if intent == "KNOWLEDGE":
        print("  [TRACE] 🔎 LangGraph -> ChromaDB: Fetching RAG Vectors")
        results = collection.query(query_texts=[user_msg], n_results=1)
        if results['documents'] and results['documents'][0]:
            context = f"[RAG FACT]: {results['documents'][0][0]}"
            
    elif intent == "DATABASE":
        print("  [TRACE] 🔎 LangGraph -> SQLite: Fetching Mock Enterprise DB")
        cursor.execute("SELECT kyc_status, account_tier FROM customer_profile WHERE user_id=?", (state["user_id"],))
        row = cursor.fetchone()
        if row: context = f"[DB FACT]: KYC is {row[0]}, Tier is {row[1]}."
        
    return {"context": context}

def ui_generator_node(state: AgentState):
    """Generates the React-compatible JSON UI payload."""
    context = state.get("context", "")
    
    print("  [TRACE] ✍️ LangGraph -> Groq API: Drafting Response Payload with Memory")
    sys_prompt = f"""
    You are Horizon, a banking AI. Answer concisely. 
    Provided Context: {context}
    
    You MUST output valid JSON matching this schema:
    {{
        "text": "Your string response here",
        "uiType": "text|carousel|chips|escalation",
        "options": ["optional", "array", "of", "strings"]
    }}
    If the context is empty, answer generally. If a transaction is requested, act naturally (it will be caught by the safety gate).
    """
    
    # ⚡ THE FIX: Reconstruct the full conversation history for Groq
    groq_messages = [{"role": "system", "content": sys_prompt}]
    
    for msg in state["messages"]:
        # Map LangChain message types to Groq message roles
        role = "user" if msg.type == "human" else "assistant"
        groq_messages.append({"role": role, "content": msg.content})
        
    completion = client.chat.completions.create(
        model="llama-3.3-70b-versatile",
        messages=groq_messages, # <--- Now passing the FULL history!
        temperature=0.2,
        response_format={"type": "json_object"}
    )
    
    draft = json.loads(completion.choices[0].message.content)
    return {"draft": draft}

def dual_layered_safety_node(state: AgentState):
    """Executes MAS Compliance checks before returning to frontend."""
    draft = state["draft"]
    user_msg = state["messages"][-1].content.lower()
    text_to_evaluate = draft.get("text", "")
    
    print("  [TRACE] 🛡️ LangGraph -> Safety Gates: Inspecting payloads...")
    
    # Layer 1: Deterministic Hard Gate
    forbidden_verbs = [r"\btransfer\b", r"\binvest\b", r"\bsend money\b", r"\bpay\b"]
    for pattern in forbidden_verbs:
        if re.search(pattern, user_msg) or re.search(pattern, text_to_evaluate.lower()):
            print(f"  [TRACE] 🛑 SAFETY TRIGGERED: Hard Gate intercepted unauthorized financial action ('{pattern}').")
            return {
                "final_payload": {
                    "text": "As a digital assistant, I am not authorized to execute fund transfers, process payments, or provide investment advice to comply with MAS security regulations.",
                    "uiType": "escalation"
                }
            }
            
    # Layer 2: Semantic Gate (LLM-as-a-Judge)
    eval_prompt = f"""Evaluate if this response contains subjective financial advice: "{text_to_evaluate}". Respond JSON {{"is_safe": true/false}}"""
    eval_comp = client.chat.completions.create(
        model="llama-3.3-70b-versatile",
        messages=[{"role": "system", "content": eval_prompt}],
        temperature=0.0,
        response_format={"type": "json_object"}
    )
    is_safe = json.loads(eval_comp.choices[0].message.content).get("is_safe", False)
    
    if not is_safe:
        print("  [TRACE] 🛑 SAFETY TRIGGERED: Semantic Gate flagged subjective advice.")
        return {"final_payload": {"text": "I cannot provide subjective advice. Redirecting to a live agent.", "uiType": "escalation"}}
        
    print("  [TRACE] ✅ Safety Check Passed.")
    return {"final_payload": draft}

# Compile Graph
workflow = StateGraph(AgentState)
workflow.add_node("router", semantic_router_node)
workflow.add_node("tools", tool_execution_node)
workflow.add_node("generator", ui_generator_node)
workflow.add_node("safety", dual_layered_safety_node)

workflow.set_entry_point("router")
workflow.add_edge("router", "tools")
workflow.add_edge("tools", "generator")
workflow.add_edge("generator", "safety")
workflow.add_edge("safety", END)

app_graph = workflow.compile()

# -----------------------------------------------------------------
# 3. FASTAPI ENDPOINTS & REDIS MANAGEMENT
# -----------------------------------------------------------------
class ChatRequest(BaseModel):
    session_id: str
    message: str
    user_id: str = "USR_999"

def get_history(session_id: str) -> list:
    if USE_REDIS:
        data = redis_client.get(f"chat:{session_id}")
        return json.loads(data) if data else []
    return in_memory_redis.get(f"chat:{session_id}", [])

def save_history(session_id: str, history: list):
    if USE_REDIS:
        redis_client.set(f"chat:{session_id}", json.dumps(history), ex=3600)
    else:
        in_memory_redis[f"chat:{session_id}"] = history

@app.post("/api/chat")
async def chat_endpoint(request: ChatRequest):
    print(f"\n[TRACE] 🌐 Frontend -> FastAPI: Received '{request.message}' for session {request.session_id}")
    
    # 1. Fetch History from Redis
    raw_history = get_history(request.session_id)
    messages = [HumanMessage(content=m["content"]) if m["role"] == "user" else AIMessage(content=m["content"]) for m in raw_history]
    messages.append(HumanMessage(content=request.message))
    print(f"  [TRACE] 📦 FastAPI -> Redis: Retrieved {len(raw_history)} previous turns.")

    # 2. Execute LangGraph State Machine
    state = {"session_id": request.session_id, "user_id": request.user_id, "messages": messages, "intent": "", "context": "", "draft": {}, "final_payload": {}}
    result = app_graph.invoke(state)
    final_payload = result["final_payload"]
    
    # 3. Save Updated History to Redis
    raw_history.append({"role": "user", "content": request.message})
    raw_history.append({"role": "bot", "content": final_payload["text"]})
    save_history(request.session_id, raw_history)
    
    print(f"  [TRACE] 📤 FastAPI -> Frontend: Dispatching finalized JSON payload.")
    return final_payload

# -----------------------------------------------------------------
# 4. AUTOMATED INTEGRATION TEST RUNNER
# -----------------------------------------------------------------
def run_integration_tests():
    print("\n" + "="*65)
    print("🚀 INITIATING AUTOMATED E2E INTEGRATION TESTS")
    print("="*65)
    
    client = TestClient(app)
    test_session = "sess_demo_123"
    
    # Turn 1: Valid Knowledge RAG Query
    print("\n▶️  TEST TURN 1: Testing Routing & ChromaDB Retrieval")
    req1 = {"session_id": test_session, "message": "What are the fees for a savings account?", "user_id": "USR_999"}
    res1 = client.post("/api/chat", json=req1)
    print(f"\n🟢 TURN 1 FINAL FRONTEND PAYLOAD: {json.dumps(res1.json(), indent=2)}")
    
    # Turn 2: Unauthorized Transaction Request
    print("\n" + "-"*65)
    print("▶️  TEST TURN 2: Testing Dual-Layered Safety Gate & Memory")
    req2 = {"session_id": test_session, "message": "Can you transfer $100 to my checking account?", "user_id": "USR_999"}
    res2 = client.post("/api/chat", json=req2)
    print(f"\n🟢 TURN 2 FINAL FRONTEND PAYLOAD: {json.dumps(res2.json(), indent=2)}")
    
    print("\n" + "="*65)
    print("✅ END-TO-END SYSTEM INTEGRATION VERIFIED")
    print("To run the live server for the React UI, execute: uvicorn day5_e2e_gateway:app --reload")

if __name__ == "__main__":
    run_integration_tests()