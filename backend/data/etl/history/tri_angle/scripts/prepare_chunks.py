import os
import json
import asyncio
import logging
from typing import List, Dict, Any
from tenacity import retry, wait_exponential, stop_after_attempt, retry_if_exception_type
from openai import AsyncOpenAI, RateLimitError, APIError
from langchain_text_splitters import RecursiveCharacterTextSplitter

# ==========================================
# CONFIGURATION & FILE PATHS
# ==========================================
BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../.."))
DATA_DIR = os.path.join(BASE_DIR, "backend/data/raw_sources")

INPUT_FILE = os.path.join(DATA_DIR, "tokenized_horizon_faqs.json")
OUTPUT_FILE = os.path.join(DATA_DIR, "ready_for_ingestion.json")
CHECKPOINT_FILE = os.path.join(DATA_DIR, "prepare_chunks_checkpoint.json")

# LLM Config (Using standard AsyncOpenAI client routed to Groq)
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "your_groq_api_key_here")
client = AsyncOpenAI(api_key=GROQ_API_KEY, base_url="https://api.groq.com/openai/v1")
MODEL_NAME = "llama-3.1-8b-instant" # Upgraded to 3.1 for superior JSON compliance

# Splitting Config (V2.5 Size Check)
MAX_CHAR_LIMIT = 1000
text_splitter = RecursiveCharacterTextSplitter(
    chunk_size=800,
    chunk_overlap=150,
    length_function=len,
    separators=["\n\n", "\n", ". ", " ", ""]
)

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

# ==========================================
# LLM REDUCTION LOGIC (WITH RATE LIMIT HANDLING)
# ==========================================
@retry(
    wait=wait_exponential(multiplier=1.5, min=2, max=15), 
    stop=stop_after_attempt(5),
    retry=retry_if_exception_type((RateLimitError, APIError))
)
async def generate_tri_angle_chunks(fragment_text: str) -> Dict[str, str]:
    """Passes the Pre-Prepended fragment to Groq for Tri-Angle Faceted Compression."""
    
    system_prompt = """You are an expert financial data summarizer building an enterprise search index.
Your task is to read the provided FAQ fragment and compress its core information into three highly concise, keyword-dense search phrases.
DO NOT use conversational filler (e.g., "How do I", "You can"). Extract ONLY nouns, verbs, and specific constraints.

Provide exactly 3 angles:
1. "procedural": The step-by-step actions.
2. "policy": The rules, fees, limits, and eligibility.
3. "conceptual": The core nouns and definitions.

CRITICAL INSTRUCTION: Output ONLY a raw, valid JSON object. Do NOT wrap the JSON in markdown formatting (like ```json). Do NOT output any introductory text.
Format: {"procedural": "...", "policy": "...", "conceptual": "..."}"""

    response = await client.chat.completions.create(
        model=MODEL_NAME,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": f"FAQ Fragment to compress:\n\n{fragment_text}"}
        ],
        temperature=0.1
    )
    
    raw_content = response.choices[0].message.content.strip()
    
    # Heuristic Cleanup: Strip markdown if the LLM still hallucinates it
    if raw_content.startswith("```json"):
        raw_content = raw_content[7:]
    if raw_content.startswith("```"):
        raw_content = raw_content[3:]
    if raw_content.endswith("```"):
        raw_content = raw_content[:-3]
        
    return json.loads(raw_content.strip())

# ==========================================
# CORE ETL PIPELINE (WITH CHECKPOINTING)
# ==========================================
async def process_faqs():
    # 1. Load or Initialize Checkpoint
    if os.path.exists(CHECKPOINT_FILE):
        logging.info("Checkpoint found. Resuming previous ETL job...")
        with open(CHECKPOINT_FILE, "r") as f:
            state = json.load(f)
    else:
        logging.info("Starting fresh ETL job...")
        state = {
            "processed_faq_ids": [],
            "parent_data": [],
            "child_data": []
        }

    # 2. Load Raw Synthetic Data
    if not os.path.exists(INPUT_FILE):
        logging.error(f"Input file not found at {INPUT_FILE}")
        return

    with open(INPUT_FILE, "r") as f:
        raw_faqs = json.load(f)

    total_faqs = len(raw_faqs)
    
    # 3. Iterate and Process
    for index, faq in enumerate(raw_faqs):
        faq_id = faq.get("faq_id")
        
        # Skip if already processed (Pause/Resume safety)
        if faq_id in state["processed_faq_ids"]:
            continue
            
        logging.info(f"Processing {index + 1}/{total_faqs}: {faq_id}")
        
        # A. Format Parent Payload for SQLite
        parent_payload = {
            "parent_id": faq_id,
            "product_tags": json.dumps(faq.get("product_tags", [])),
            "intent_families": json.dumps(faq.get("intent_families", [])),
            "content": faq.get("tokenized_answer", "")
        }
        
        # B. Split First (V2.5 Size Evaluation)
        raw_question = faq.get("synth_question", "")
        raw_answer = faq.get("synth_answer", "")
        
        if len(raw_answer) > MAX_CHAR_LIMIT:
            fragments = text_splitter.split_text(raw_answer)
        else:
            fragments = [raw_answer]
            
        # C. Prepend Question & Compress Second
        faq_child_chunks = []
        for frag_idx, fragment in enumerate(fragments):
            # Pre-Compression Context Prepending
            prepended_text = f"Q: {raw_question}\n\nA: {fragment}"
            
            try:
                # LLM Call
                angles = await generate_tri_angle_chunks(prepended_text)
                
                # -----------------------------------------------------
                # 🔍 HUMAN VERIFICATION PRINTOUT
                # -----------------------------------------------------
                print(f"\n{'━'*60}")
                print(f" 🔍 HUMAN CHECK: {faq_id} (Fragment {frag_idx+1}/{len(fragments)})")
                print(f"{'━'*60}")
                print(f"[RAW INPUT TO LLM]:\n{prepended_text}\n")
                print(f"{'-'*60}")
                print(f"[LLM COMPRESSED OUTPUT (Vector Targets)]:")
                print(f"  🟢 PROCEDURAL : {angles.get('procedural')}")
                print(f"  🔵 POLICY     : {angles.get('policy')}")
                print(f"  🟣 CONCEPTUAL : {angles.get('conceptual')}")
                print(f"{'━'*60}\n")
                # -----------------------------------------------------
                
                # Protect Groq API Limits
                await asyncio.sleep(1.5) 
                
                # D. Multi-Intent Duplication Protocol
                for intent in faq.get("intent_families", ["UNKNOWN"]):
                    for angle_name, compressed_text in angles.items():
                        child_chunk = {
                            "child_id": f"{faq_id}_frag{frag_idx}_{angle_name}_{intent.replace(' ', '')}",
                            "parent_id": faq_id,
                            "product_tag": faq.get("product_tags", ["ALL"]), # Stored as array for $contains
                            "intent_family": intent, # Stored as string for $in
                            "search_text": compressed_text
                        }
                        faq_child_chunks.append(child_chunk)
                        
            except Exception as e:
                logging.error(f"Failed to compress fragment for {faq_id}: {e}")
                # Save checkpoint and abort gracefully so user can resume
                with open(CHECKPOINT_FILE, "w") as f:
                    json.dump(state, f, indent=2)
                return

        # Commit to state and save checkpoint
        state["parent_data"].append(parent_payload)
        state["child_data"].extend(faq_child_chunks)
        state["processed_faq_ids"].append(faq_id)
        
        with open(CHECKPOINT_FILE, "w") as f:
            json.dump(state, f, indent=2)

    # 4. Finalize and Cleanup
    logging.info("All FAQs processed successfully. Writing final ingestion file...")
    final_output = {
        "parent_data": state["parent_data"],
        "child_data": state["child_data"]
    }
    
    with open(OUTPUT_FILE, "w") as f:
        json.dump(final_output, f, indent=2)
        
    # Delete checkpoint upon successful completion
    if os.path.exists(CHECKPOINT_FILE):
        os.remove(CHECKPOINT_FILE)
        
    logging.info(f"✅ V2.5 ETL Complete! {len(state['parent_data'])} Parents and {len(state['child_data'])} Children ready for database insertion.")

if __name__ == "__main__":
    # Ensure data directory exists
    os.makedirs(DATA_DIR, exist_ok=True)
    asyncio.run(process_faqs())