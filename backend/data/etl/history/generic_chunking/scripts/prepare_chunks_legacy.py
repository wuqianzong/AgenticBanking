import json
from langchain_text_splitters import RecursiveCharacterTextSplitter

INPUT_FILE = "tokenized_horizon_faqs.json"
OUTPUT_FILE = "ready_for_ingestion.json"

# The optimal splitter for all-MiniLM-L6-v2 (512 token limit)
text_splitter = RecursiveCharacterTextSplitter(
    chunk_size=800,       
    chunk_overlap=150,    
    separators=["\n\n", "\n- ", ". ", " "]
)

def prepare_chunks():
    print(f"[*] Reading {INPUT_FILE}...")
    with open(INPUT_FILE, "r", encoding="utf-8") as f:
        corpus = json.load(f)

    # We will output two distinct arrays for the ingest.py script to consume
    parent_storage_array = []
    child_vector_array = []

    print("[*] Executing v0.4 Parent-Child Chunking Protocol...")

    for entry in corpus:
        faq_id = entry["faq_id"]
        domain = entry["domain"]
        intents = entry["intent_families"]
        
        question = entry["synth_question"]
        unmasked_answer = entry["synth_answer"]       # Used for ChromaDB (Vectors)
        masked_answer = entry["tokenized_answer"]     # Used for SQLite (LLM Generation)

        # ----------------------------------------------------
        # 1. BUILD THE PARENT CHUNK (For SQLite)
        # ----------------------------------------------------
        # We concatenate the Question and the MASKED answer for ultimate compliance.
        parent_payload = f"Q: {question}\nA: {masked_answer}"
        
        parent_storage_array.append({
            "parent_id": faq_id,
            "domain": domain,
            # We store intents as a comma-separated string in SQLite for easy reading
            "intent_families": ",".join(intents), 
            "content": parent_payload
        })

        # ----------------------------------------------------
        # 2. BUILD THE CHILD CHUNKS (For ChromaDB)
        # ----------------------------------------------------
        
        # Part A: The Standalone Question Chunk
        # (This catches broad, high-level intent queries)
        for intent in intents:
            child_vector_array.append({
                "text": f"Q: {question}",
                "metadata": {
                    "parent_id": faq_id,
                    "domain": domain,
                    "intent_family": intent,
                    "chunk_type": "question_anchor"
                }
            })

        # Part B: The Answer Chunks (Using UNMASKED text for mathematical accuracy)
        if len(unmasked_answer) <= 800:
            # Condition A: Answer is short. Do NOT split it. Prevent Semantic Orphans.
            for intent in intents:
                child_vector_array.append({
                    "text": f"Q: {question}\nA: {unmasked_answer}",
                    "metadata": {
                        "parent_id": faq_id,
                        "domain": domain,
                        "intent_family": intent,
                        "chunk_type": "intact_answer"
                    }
                })
        else:
            # Condition B: Answer is too long for MiniLM. Execute Recursive Split.
            fragments = text_splitter.split_text(unmasked_answer)
            
            for index, fragment in enumerate(fragments):
                # Context Prepending: Anchor the fragment back to the core question
                anchored_fragment = f"Q: {question}\n{fragment}"
                
                # Multi-Intent Duplication Loop
                for intent in intents:
                    child_vector_array.append({
                        "text": anchored_fragment,
                        "metadata": {
                            "parent_id": faq_id,
                            "domain": domain,
                            "intent_family": intent,
                            "chunk_type": f"splintered_answer_part_{index+1}"
                        }
                    })

    # ----------------------------------------------------
    # 3. EXPORT TO JSON
    # ----------------------------------------------------
    output_data = {
        "parent_data": parent_storage_array,
        "child_data": child_vector_array
    }

    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(output_data, f, indent=4, ensure_ascii=False)

    print("\n================================================================")
    print("✅ DATA STAGING COMPLETE")
    print("================================================================")
    print(f"Total FAQs Processed : {len(corpus)}")
    print(f"Total Parent Records : {len(parent_storage_array)}")
    print(f"Total Child Vectors  : {len(child_vector_array)}")
    print(f"File Saved           : {OUTPUT_FILE}")

if __name__ == "__main__":
    prepare_chunks()