import os
import json
import hashlib
import chromadb
from sentence_transformers import SentenceTransformer

print("=================================================================")
print("🧠 TASK 3: FIRST PRINCIPLES DECOUPLED RAG INGESTION")
print("=================================================================")

# Configuration
CHROMA_PATH = "data/chroma"
PARENT_STORE_PATH = "data/parent_store.json"
COLLECTION_NAME = "banking_knowledge"

# Initialize Storage Mechanisms
os.makedirs("data", exist_ok=True)

# 1. Load Local Embedding Model
print("Loading all-MiniLM-L6-v2 embedding model...")
embedder = SentenceTransformer("all-MiniLM-L6-v2")

# 2. Initialize ChromaDB
chroma_client = chromadb.PersistentClient(path=CHROMA_PATH)
collection = chroma_client.get_or_create_collection(name=COLLECTION_NAME)

# 3. Initialize Local JSON Parent Store
if os.path.exists(PARENT_STORE_PATH):
    with open(PARENT_STORE_PATH, 'r', encoding='utf-8') as f:
        parent_store = json.load(f)
else:
    parent_store = {}

def generate_uuid(text: str) -> str:
    """Creates a deterministic SHA-256 hash UUID based on text content."""
    return hashlib.sha256(text.encode('utf-8')).hexdigest()

def sliding_window_chunk(text: str, window_size: int = 400, overlap: int = 50) -> list:
    """A transparent, First Principles text chunker."""
    words = text.split()
    chunks = []
    for i in range(0, len(words), window_size - overlap):
        chunk = " ".join(words[i:i + window_size])
        chunks.append(chunk)
    return chunks

def ingest_data():
    documents = []
    
    # Load Scraped FAQs
    faq_path = "data/raw/faq_dataset.json"
    if os.path.exists(faq_path):
        with open(faq_path, 'r', encoding='utf-8') as f:
            faqs = json.load(f)
            for faq in faqs:
                # Combine Q&A into one parent context
                parent_text = f"Category: {faq['category']}\nQ: {faq['question']}\nA: {faq['answer']}"
                documents.append({"type": "faq", "text": parent_text})
                
    # Load PDF Text
    pdf_path = "data/raw/sample_fee_schedule.txt"
    if os.path.exists(pdf_path):
        with open(pdf_path, 'r', encoding='utf-8') as f:
            pdf_text = f.read()
            # Split PDF by double newlines as natural parent boundaries
            paragraphs = [p.strip() for p in pdf_text.split('\n\n') if len(p.strip()) > 50]
            for p in paragraphs:
                documents.append({"type": "pdf_policy", "text": p})

    print(f"Processing {len(documents)} Parent Documents...")

    child_texts = []
    child_metadatas = []
    child_ids = []
    
    global_child_counter = 0

    # The Custom Parent-Child Loop
    for doc in documents:
        parent_text = doc["text"]
        doc_type = doc["type"]
        
        # 1. Hash Parent to UUID
        parent_id = generate_uuid(parent_text)
        
        # 2. Store Massive Parent String in Decoupled JSON Store
        parent_store[parent_id] = {
            "type": doc_type,
            "content": parent_text
        }
        
        # 3. Slice into Children
        chunks = sliding_window_chunk(parent_text, window_size=50, overlap=10)
        
        for chunk_idx, child_chunk in enumerate(chunks):
            child_id = f"{parent_id}_child_{chunk_idx}"
            
            child_texts.append(child_chunk)
            # Store ONLY the tiny UUID in the vector metadata
            child_metadatas.append({"parent_id": parent_id, "source_type": doc_type})
            child_ids.append(child_id)
            global_child_counter += 1

    # Save the updated Parent Store to disk
    with open(PARENT_STORE_PATH, 'w', encoding='utf-8') as f:
        json.dump(parent_store, f, indent=2, ensure_ascii=False)
    print(f"🟢 Saved {len(parent_store)} massive Parent texts to decoupled JSON storage.")

    # Vectorize and seed into ChromaDB
    if child_texts:
        print(f"Generating vectors for {len(child_texts)} Child chunks...")
        embeddings = embedder.encode(child_texts).tolist()
        
        collection.upsert(
            documents=child_texts,
            embeddings=embeddings,
            metadatas=child_metadatas,
            ids=child_ids
        )
        print("🟢 Seeded local ChromaDB with embedded child vectors.")

print("\n=================================================================")
print("🔍 SIMILARITY SEARCH VALIDATION (THE FETCH LOOP)")
print("=================================================================")

def test_rag_retrieval(query: str):
    print(f"Query: '{query}'")
    
    # 1. Embed user query
    query_vector = embedder.encode([query]).tolist()
    
    # 2. Search ChromaDB for the closest Child Chunk
    results = collection.query(
        query_embeddings=query_vector,
        n_results=1
    )
    
    if not results['ids'][0]:
        print("No results found.")
        return

    top_child_id = results['ids'][0][0]
    top_child_text = results['documents'][0][0]
    metadata = results['metadatas'][0][0]
    parent_uuid = metadata['parent_id']
    
    print("\n--- STAGE 1: VECTOR RETRIEVAL (CHROMADB) ---")
    print(f"Top Child ID: {top_child_id}")
    print(f"Child Text: {top_child_text}")
    print(f"Metadata Link: parent_id = {parent_uuid}")
    
    # 3. Instantly fetch the full Parent text from the JSON Store
    print("\n--- STAGE 2: PARENT HYDRATION (JSON KEY-VALUE) ---")
    with open(PARENT_STORE_PATH, 'r', encoding='utf-8') as f:
        local_store = json.load(f)
        
    if parent_uuid in local_store:
        full_parent = local_store[parent_uuid]['content']
        print(f"🟢 Successfully recovered FULL contextual payload ($O(1)$ lookup):")
        print(f"{'-'*50}\n{full_parent}\n{'-'*50}")
    else:
        print("❌ Parent UUID missing from local store.")

if __name__ == "__main__":
    ingest_data()
    # Execute validation query
    test_rag_retrieval("What are the fees for a lost card?")