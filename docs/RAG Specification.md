# **RAG Specification (User Journey v2.0)**

**Project:** Agentic AI Chatbot for Consumer Banking (MSc FinTech Capstone)  
**Module:** Document Ingestion & Knowledge Retrieval Execution  
**Target Environment:** Local execution on 16GB Unified Memory M1 Mac

## 

## **1\. Architecture**

The RAG architecture implements a **Hybrid Processing Paradigm** combined with a **Hierarchical (Parent-Child) Chunking Strategy**.

* **LLM compute** is provided by Groq-hosted generation models and Gemini 3.5 Flash Lite for task-level sufficiency and outbound semantic evaluation.  
* **Vector Math & Privacy:** Localized to the M1 Mac using a local ChromaDB instance and the lightweight sentence-transformers/all-MiniLM-L6-v2 embedding model.

Retrieval Metadata and Presentation Separation: Product and intent families deterministically restrict the eligible Chroma records for each task’s RAG search. Angle-group RRF then ranks eligible parent records. User Journey v2.0 is selected only after task-level evidence passes sufficiency evaluation and controls response presentation rather than retrieval eligibility.

## 

## **2\. Data Extraction & Pre-Processing Pipeline**

This section distinguishes the **active runtime evidence path** from a historical tri-angle artifact. The full lineage and file ownership are maintained in [ETL History and Artifact Guide](etl/ETL_HISTORY_AND_ARTIFACT_GUIDE.md).

Raw banking data is sourced from publicly available FAQ structures (modeled on SG Trust Bank) but not exact copy for proprietary reason. The extraction process now utilizes a "Decoupled ETL approach", strictly separating raw data retrieval from the Capstone taxonomy mapping.

### **2.1 Extraction Methodology (Synchronous Scraper)**

* **Tooling:** `FAQ_prep_tools/extract_raw_faqs.py` uses Playwright with BeautifulSoup to crawl the Trust Bank FAQ structure.
* **Logic:** The script parses static HTML DOM trees, isolating accordion containers (div.faq-accordion-block) to extract Question and Answer text pairs natively. The script now dynamically crawls the root FAQ homepage first to discover all available taxonomy branches before extracting the Q\&A pairs.  
* **Sanitization:** The parser strips erratic newline characters and converts HTML list tags (\<li\>) into logical string bullet points.

### **2.2 Decoupled Two-Tier Transformation**

The raw extraction output is passed through an LLM-powered Synthetic Pre-Processing Pipeline. This layer performs two critical functions:

1. Proprietary Cleansing: It comprehensively paraphrases the raw banking FAQs into original compositions, rebranding all institutional references to "Horizon Bank" to create a Horizon Bank–branded synthetic dataset and reduce dependence on source phrasing.  
2. **Product Tagging:** The pipeline now utilizes a 'Deterministic Product Mapping \+ LLM Inferential Fallback' script to assign product\_tag lists (e.g., \['Credit\_Card'\] or \['ALL'\]).

### **2.3 Semantic Intent Consolidation**

To ensure architectural stability, a Rectification Engine (1c\_consolidate\_intents.py) maps thousands of dynamic raw intents into 16 standardized Master Intents. This prevents vector drift and ensures that Node 03b operates on a finite, high-confidence semantic search space.

### **2.4 Product-Intent metadata status**

The numbered pipeline establishes retrieval taxonomy through `1_tag_products_clean.py`, `1b_rectify_tags.py`, and `1c_consolidate_intents.py`. At runtime, Node 02e produces task-level retrieval definitions containing target\_commercial\_product and target\_intent values; these values are used as deterministic Chroma metadata filters.

`4_generate_intent_map.py` is currently stale: its expected list-of-FAQ input does not match the Product→Intent dictionary written by `1c_consolidate_intents.py`. It is not part of a valid rebuild.

  {

    "faq\_id": "faq\_495693bf",

    "product\_tags": \["REWARDS\_PROGRAM"\],

    "intent\_families": \["Linkpoints", "Merchant Promos"\],

    "synth\_question": "What steps can I take to accumulate rewards points...",

    "synth\_answer": "To earn rewards, start by either tapping your physical Horizon card...",

    "tokenized\_answer": "To earn rewards, start by either tapping your physical Horizon card..."

  }

## **3\. Current data staging contract**

The active runtime reconciliation script is `backend/data/etl/active/scripts/6_reconcile_generic_stage.py`. It preserves historical question and question-plus-answer child text, derives canonical metadata from rectified records plus the existing intent map, and writes `backend/data/etl/active/staging/active_generic_ready_for_ingestion.json` with `parent_data` and `child_data` arrays.

Its active child records are generic retrieval hooks: question anchors, intact question-and-answer chunks, and answer fragments for long answers. Each child carries canonical Product×Intent metadata and a `chunk_type`. This is the staging artifact consumed by the active database writer.

The preserved historical generic staging artifact remains at `backend/data/etl/history/generic_chunking/artifacts/ready_for_ingestion.json`. Its child text is not modified; the active reconciliation stage normalizes its metadata into a separate output before ingestion. The tri-angle collection is retained separately as `faq_child_chunks_tri_angle_v1` for analysis.

## **4\. Current database ingestion engine**

`backend/data/etl/active/scripts/3_db_ingestion.py` is the current dedicated database writer. Its active invocation reads `backend/data/etl/active/staging/active_generic_ready_for_ingestion.json` and writes SQLite parents plus the active Chroma collection `backend/data/vector_store/faq_child_chunks`.

### **4.1 Parent Chunk Storage (SQLite)**

* **Concept:** The complete, un-fragmented Q\&A pair.  
* **Execution:** The ingestion script concatenates the Question and Answer strings and writes them to a local lightweight SQLite database (knowledge\_parents).  
* **Schema Columns:** parent\_id (PK), product\_tags (TEXT), intent\_families (TEXT), content (TEXT).  
* The system employs an Asymmetric Tokenization Strategy. The content column in SQLite stores the tokenized\_answer (where all financial figures, rates, and thresholds have been replaced with opaque deterministic placeholders like {{faq\_xyz\_001}}). This enables deterministic validation and substitution of approved numerical placeholders, substantially reducing numeric-hallucination risk.

### **4.2 Active child-vector retrieval**

* The active writer embeds reconciled generic `child_data` into `faq_child_chunks`; each child points to a parent through `parent_id` and carries canonical `product_tag`, `intent_family`, and `chunk_type` metadata.
* Each FAQ has a question anchor plus intact answer or answer-fragment children as required by answer length.
* Children are duplicated per canonical intent lane. Product metadata is held as a list on each child and matched through the retrieval filter.
* SQLite holds the tokenized parent evidence. Chroma holds unmasked retrieval text. Parent IDs bind the two stores.
* Runtime query views are evaluated against the generic child collection. `rag_engine.py` detects the `generic_v1` schema and uses generic compatibility mode without a document-angle filter before reciprocal-rank fusion.

The preserved tri-angle collection `faq_child_chunks_tri_angle_v1` and the older disconnected `horizon_faqs` collection remain available for analysis; neither is the runtime default.

## 

## **5\. User Journey v2.0 Task-Level RAG Orchestration**

The RAG pipeline operates as an isolated sequence within the main conversational state machine, seamlessly integrating with the Orchestrator's routing variables.

### **5.1 Runtime State**

search\_tasks (List\[Dict\]): Independently formulated retrieval tasks. Each task contains a stable task\_id, one or more complete procedural/policy/conceptual angle groups, target\_commercial\_product, and target\_intent.

* task\_results (List\[Dict\]): Per-task retrieval and evaluation state, including CACHED, RETRIEVED, SUFFICIENT, RETRY, or CLARIFICATION\_REQUIRED status, candidate parents, approved parent IDs, and source origin.  
* task\_retry\_counts (Dict\[str, int\]): Live-RAG insufficiency count per task ID. Cache insufficiency does not consume this count.  
* task\_feedback (Dict\[str, str\]): Task-specific refinement feedback used only when Node 02e retries unresolved tasks.  
* approved\_evidence\_cache (Dict): Session-scoped cache of approved SQLite parent IDs, keyed by normalized product and unordered intent-family set.  
* pending\_evidence\_cache\_updates (Dict): Candidate cache promotions held until the outbound semantic gate approves the evidence and generated response.  
* task\_resolution\_instructions (List\[Dict\]): Terminal retrieval context for unresolved tasks, including exact rejected SQLite parent records that may be used only as clarification-topic suggestions.  
* intent\_clarification\_tasks (List\[Dict\]): User-derived request components that Node 02e cannot safely map to a valid Product/Intent retrieval task. They bypass RAG and may only support a neutral clarification.
* Approved evidence remains parent-ID based. The presentation planner, response generator, and outbound answer-groundedness check rehydrate only task-scoped parent IDs that Node 03c approved from the authoritative SQLite knowledge database.

### **5.2 Node 02e: Intent Formulator and Task Router**

* **Function:** Task-level semantic query formulation.  
* **Logic:** \- Decomposes a valid request into stable tasks. \- Produces one complete base procedural/policy/conceptual angle group for each task. \- When a valid request component cannot be safely mapped to a Product/Intent task, records it as an intent clarification task instead of guessing a product. \- May return searchable and intent-clarification tasks together. \- Preserves task IDs and reformulates only failed tasks during retry mode. \- Applies a four-attempt, additive recall-expansion ladder: initial precise retrieval; append one complete angle group; add valid intent filters; then add valid product tags. Earlier angle groups and filters are retained rather than replaced. \- A product-expansion task may carry an ordered product-tag array, preserving the original product first and adding only a plausible matrix-valid related product. \- Independently formulates current tasks before deterministic cache comparison. \- Compares each task by normalized product-tag set plus unordered intent-family set. Routing: \- All cache hits route directly to Node 03c for fresh sufficiency validation. \- Any cache miss routes to Node 03a. \- Intent clarification tasks bypass RAG and join the response layer with approved evidence. \- A retry always performs live RAG for the failed task; it does not reuse previously insufficient cache evidence.

### **5.3 Node 03a and Node 03b: Retrieval Dispatch and Task-Level RAG**

* Node 03a is deterministic. It identifies only cache-miss and retry tasks and dispatches RAG only when live retrieval is needed. Node 03b retrieves eligible tasks concurrently. For each task, it: 1\. builds one shared metadata filter from matrix-valid product/intent pairs, so product and intent remain constraints rather than independent RRF branches; 2\. executes concurrent procedural, policy, and conceptual **query** searches against the same eligible generic-child collection for every complete angle group; 3\. applies RRF inside each angle group, then combines group scores with equal normalisation at task level; 4\. applies the 50% drop-off rule and MAX\_PARENTS \= 4; 5\. hydrates the selected parent IDs from SQLite; 6\. returns parent records grouped by task\_id. There is no direct-hit bypass. Cached tasks bypass Node 03a and Node 03b, but their parent IDs are rehydrated from SQLite before Node 03c evaluates them again.

### **5.4 Node 03c: Task-Level Sufficiency Auditor and Evidence Selector**

* **Function:** Gemini task-level evidence evaluation.  
* **Logic:** \- Evaluates each task’s parent records independently. \- A sufficient task may approve only parent IDs supplied for that same task. \- Approved parent IDs are rehydrated directly from SQLite before downstream use; the auditor never generates evidence text. \- Successful tasks retain their approved evidence while only RETRY tasks loop back to Node 02e. \- A cached task that fails sufficiency falls back to live RAG without consuming a live-RAG retry. \- After four failed live retrieval attempts, the task becomes CLARIFICATION\_REQUIRED rather than triggering hard escalation. \- Rejected retrieved parent records are retained as suggestion-only evidence; they cannot support factual answers, instructions, or UI actions. Routing: \- Any RETRY task → Node 02e. \- All tasks SUFFICIENT or CLARIFICATION\_REQUIRED → User Journey v2 presentation planning.

### **5.5 Approved-Evidence Cache**

The approved-evidence cache is session-scoped and stores approved parent IDs only, never cached parent text. Cache key: normalized target\_commercial\_product tag set \+ sorted unique target\_intent values. Rules: \- Node 02e formulates tasks independently before cache comparison. \- Matching tasks are rehydrated from SQLite and revalidated by Node 03c. \- Mixed requests retrieve only cache-miss tasks while cached task evidence remains available for the same sufficiency pass. \- Cache entries are promoted only after the outbound semantic gate approves the selected evidence and generated response. \- Cache insufficiency triggers live RAG and does not decrement the four-attempt live-RAG retry budget.

### **5.6 Terminal Insufficiency and Clarification Evidence**

A task that remains insufficient after four live retrieval attempts becomes CLARIFICATION\_REQUIRED. The system may still answer other tasks whose evidence was approved. For an unresolved task, the exact SQLite parent records returned by the final failed retrieval attempt are passed to the response layer only as possible related-topic suggestions. The response must: \- apologise that relevant FAQ details were not found; \- name the unresolved customer-supplied term clearly in multi-part requests; \- suggest no more than two related topics explicitly supported by rejected retrieved records; \- ask for general clarification if those topics are irrelevant; \- For a terminal clarification response, the current implementation returns no UI options, including when other tasks have approved answers. Rejected evidence cannot support an answer, banking instruction, product claim, or suggested customer action.

### **5.7 Outbound Validation of RAG Evidence**

The outbound semantic gate evaluates: 1\. safety/compliance of all task-level approved evidence; 2\. answer text and answer UI options against the selected approved evidence; 3\. retrieval-clarification topic grounding against rejected suggestion-only evidence; 4\. intent-clarification terms against the supplied user-derived unresolved request; 5\. clarification connection to the unresolved term in the user request; 6\. MAS compliance and unauthorized-advice risk across answer, clarification, and UI options. If a clarification fails validation, it is regenerated up to three times. If only the clarification repeatedly fails while an answer remains independently safe, a deterministic fallback preserves the safe answer and appends a neutral general clarification. Unsafe or ungrounded answer/evidence failures continue to hard escalation.

### 
