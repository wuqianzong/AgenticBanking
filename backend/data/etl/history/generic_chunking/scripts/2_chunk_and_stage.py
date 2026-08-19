import json
from langchain_text_splitters import RecursiveCharacterTextSplitter

INPUT_FILE = "consolidated_horizon_faqs.json"
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

    parent_storage_array = []
    child_vector_array = []

    print("[*] Executing v2.1 Parent-Child Chunking Protocol...")

    for entry in corpus:
        faq_id = entry.get("faq_id", "unknown_id")
        intents = entry.get("intent_families", ["General Inquiries"])
        product_tag = entry.get("product_tag", ["ALL"]) 
        
        question = entry.get("synth_question", "")
        unmasked_answer = entry.get("synth_answer", "")       
        # Fallback to unmasked if tokenized isn't generated yet
        masked_answer = entry.get("tokenized_answer", unmasked_answer)     

        # ----------------------------------------------------
        # 1. BUILD THE PARENT CHUNK (For SQLite)
        # ----------------------------------------------------
        parent_payload = f"Q: {question}\nA: {masked_answer}"
        
        parent_storage_array.append({
            "parent_id": faq_id,
            "product_tags": ",".join(product_tag), # Store as comma-separated in SQLite
            "intent_families": ",".join(intents), 
            "content": parent_payload
        })

        # ----------------------------------------------------
        # 2. BUILD THE CHILD CHUNKS (For ChromaDB)
        # ----------------------------------------------------
        # Per your log rule: Duplicate chunks for multiple intents
        for intent in intents:
            base_metadata = {
                "parent_id": faq_id,
                "intent_family": intent,
                "product_tag": product_tag # ChromaDB natively accepts List[str] arrays here
            }
            
            # Question Anchor Chunk
            q_meta = base_metadata.copy()
            q_meta["chunk_type"] = "question_anchor"
            child_vector_array.append({
                "text": f"Q: {question}",
                "metadata": q_meta
            })

            # Intact Answer Chunk (if small enough)
            if len(unmasked_answer) <= 800:
                a_meta = base_metadata.copy()
                a_meta["chunk_type"] = "intact_answer"
                child_vector_array.append({
                    "text": f"Q: {question}\nA: {unmasked_answer}",
                    "metadata": a_meta
                })
                
            # Splintered Answer Chunks (if too large)
            else:
                fragments = text_splitter.split_text(unmasked_answer)
                for index, fragment in enumerate(fragments):
                    f_meta = base_metadata.copy()
                    f_meta["chunk_type"] = f"splintered_answer_part_{index+1}"
                    # Semantic Preservation: Prepend question to fragment
                    anchored_fragment = f"Q: {question}\n{fragment}"
                    
                    child_vector_array.append({
                        "text": anchored_fragment,
                        "metadata": f_meta
                    })

    # 3. EXPORT TO JSON
    output_data = {
        "parent_data": parent_storage_array,
        "child_data": child_vector_array
    }

    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(output_data, f, indent=4, ensure_ascii=False)

    print("✅ DATA STAGING COMPLETE")
    print(f"Total Parent Records : {len(parent_storage_array)}")
    print(f"Total Child Vectors  : {len(child_vector_array)}")

if __name__ == "__main__":
    prepare_chunks()