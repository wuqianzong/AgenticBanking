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

* task\_results (List\[Dict\]): Per-task retrieval and evaluation state, including `SUFFICIENT`, `PARTIALLY_SUFFICIENT`, or `CLARIFICATION_REQUIRED`, candidate parents, approved parent IDs, and source origin.
* task\_retry\_counts (Dict\[str, int\]): Live-RAG insufficiency count per task ID.
* task\_feedback (Dict\[str, List\[Dict\]\]): Incremental parent-cited `evidence_delta` facts used only when Node 02e refines unresolved tasks. They are not answer evidence or search instructions.
* task\_partial\_evidence (Dict\[str, List\[str\]\]): Approved parent IDs retained while a partially sufficient task retries its unresolved portion.
* task\_sufficiency\_history (Dict\[str, List\[Dict\]\]): Per-attempt sufficiency outcomes supplied to the auditor to support internally consistent later judgments.
* verified\_task\_history (List\[Dict\]): Bounded session-only record of the prior ten verified task snapshots and their approved parent IDs. On a current task with overlapping product and intent, these parent IDs are re-ranked with the live direct result through RRF; they never bypass retrieval or current-turn sufficiency validation.
* task\_resolution\_instructions (List\[Dict\]): Terminal retrieval context for unresolved tasks. It carries only a resolved topic and terminal status; it never carries rejected parent content downstream.
* intent\_clarification\_tasks (List\[Dict\]): User-derived request components that Node 02e cannot safely map to a valid Product/Intent retrieval task. They bypass RAG and may only support a neutral clarification.
* Approved evidence remains parent-ID based. The presentation planner, response generator, and outbound answer-groundedness check rehydrate only task-scoped parent IDs that Node 03c approved from the authoritative SQLite knowledge database.

### **5.2 Tool Calling v2.0 and Node 02e RAG-Internal Formulation**

* **Generic task contract:** Triage emits compliant banking-only `financial_subqueries`. Node 03a turns them into append-only `tool_tasks`; `tool_results` carries only terminal validated outputs. The registry exposes enabled capabilities: RAG, registered read-only API fields, centrally resolved deeplinks, and service requests. Deeplink tool results retain only an opaque request ID; routes remain in the central request ledger and never enter chat payloads. See `DEEPLINK_DESIGN_v1.0.md`.
* **Outer loop:** Node 04a may identify a concrete, enabled, non-duplicate capability gap and return it to Node 03a for an additive task, subject to bounded task and planning-round budgets. Raw RAG candidates and retry feedback never leave the RAG-internal subgraph.

* **Node 02e Function:** RAG-internal task-level semantic query formulation.

* **Function:** Task-level semantic query formulation.  
* **Logic:** \- Decomposes a valid request into stable tasks with a resolved `direct_query`, matrix-valid product and intent filters, and a retained base angle group. \- Angle groups are generated as 10-20 word natural sentence fragments that read like an FAQ answer (targeting the answer-side of the vector space), rather than diffuse keyword arrays. \- When a valid request component cannot be safely mapped to a Product/Intent task, records it as an intent clarification task instead of guessing a product. \- May return searchable and intent-clarification tasks together. \- Preserves task IDs, customer components, direct query, and prior valid scope during retry mode. \- Applies a four-attempt additive ladder: direct retrieval (targets question-side vectors); intent expansion; product expansion; and a final feedback-guided angle-group expansion (targets answer-side vectors). Earlier filters and groups are retained rather than replaced. \- A product-expansion task may carry an ordered product-tag array, preserving the original product first and adding only a plausible matrix-valid related or functional product. \- Intent clarification tasks bypass RAG and join the response layer with approved evidence. \- Every attempt performs live RAG; no stale parent set may bypass the current task query.

### **5.3 Node 03a/03b: Generic Dispatch and Task-Level RAG**

* Node 03a plans generic tasks; Node 03b dispatches them without graph-level fan-out. A running RAG task enters Node 02e and then the RAG worker. The worker retrieves eligible tasks concurrently. For each task, it: 1\. builds one shared metadata filter from matrix-valid product/intent pairs, so product and intent remain constraints rather than independent RRF branches; 2\. uses the task's resolved `direct_query` for attempts one through three; 3\. on the final angle-expansion attempt, executes concurrent procedural, policy, and conceptual queries for every complete angle group and combines equal-normalised group RRF scores at task level; 4\. on the first direct attempt only, re-ranks matching approved parent IDs from the prior ten verified session turns with the live candidates through the same RRF branch; 5\. applies the 50% drop-off rule and MAX\_PARENTS \= 4; 6\. hydrates selected parent IDs from SQLite; 7\. returns parent records grouped by task\_id. There is no direct-hit bypass and session evidence remains a candidate, not a cached answer.

### **5.4 Node 03c: Task-Level Sufficiency Auditor and Evidence Selector**

* **Function:** Gemini task-level evidence evaluation.  
* **Logic:** \- Evaluates each task’s parent records independently against its resolved customer component. \- Classifies each result as `SUFFICIENT`, `PARTIALLY_SUFFICIENT`, or `INSUFFICIENT`. \- A sufficient or partial task may approve only parent IDs supplied for that same task. \- The auditor retains a parent as `PARTIALLY_SUFFICIENT` when it supports a useful, bounded fact about a meaningful aspect of the request, even if it cannot establish the customer's desired outcome, permission, eligibility, exception, legality, or preferred action. `INSUFFICIENT` means no supplied parent contains materially presentable task-relevant evidence. \- Approved parent IDs are rehydrated directly from SQLite before downstream use; the auditor never generates evidence text. \- A partial task retains its approved evidence while the unresolved portion retries. \- For partial or insufficient outcomes, the auditor produces only parent-cited incremental `evidence_delta` facts; Node 02e decides how to refine the current stage. \- After four live attempts, a wholly insufficient task becomes `CLARIFICATION_REQUIRED`; a partial task remains answerable with clarification for the gap. \- Rejected retrieved parent records remain internal retry-only data. Terminal RAG outcomes become validated generic `tool_results`. Routing: \- Any unresolved retry task → Node 02e. \- All answerable or terminally unresolved tasks → Resolution Planner.

### **5.5 Verified Session Evidence Re-ranking (Search Strategy v2.7)**

The active runtime does not use the former scope-keyed approved-evidence cache. Instead, after an outbound pass, it stores a bounded session-only history of the task object and its approved parent IDs for the latest ten verified turns. On a new task's **first live direct retrieval**, deterministic code identifies historical task records with overlapping normalized product and intent scope. Their parent IDs are rehydrated from SQLite and join the current live candidates in RRF. This gives prior verified evidence a chance to resurface without treating it as automatically relevant. Every selected parent must still pass the current task's sufficiency audit. The history contains no hydrated text, unapproved candidates, drafts, or durable cross-session cache entries.

### **5.6 Terminal Insufficiency and Clarification Evidence**

A task that remains insufficient after four live retrieval attempts becomes CLARIFICATION\_REQUIRED. The system may still answer other tasks whose evidence was approved. The response layer receives only the resolved customer topic and terminal status for an unresolved task. It may acknowledge that no verified information was found for that topic and ask a general clarification, but it cannot suggest products or assert facts from rejected candidates. A terminal clarification response does not create UI options, including when another task has approved answer evidence.

### **5.7 Outbound Validation of RAG Evidence**

The outbound audit is split into parallel focused checks, both built on the shared Groundedness v2.0 functions in `backend/app/core/groundedness.py` (see `docs/GROUNDEDNESS_v2.0.md`). The compliance auditor checks visible answer, clarification, and UI content against `BANK_CONDUCT_CONTEXT` and MAS rules. The evidence check uses approved task evidence as the only factual source for normal answers and audits the natural response through two internal lenses: **direct factual support** and a **bounded bridge inference** from evidence to the resolved customer component. Greeting and memory summaries use the same shared functions with their own route-specific evidence (greeting memory, or the memory candidate's source transcript) rather than separate bespoke prompts. Retrieval clarification uses only its resolved user-derived topic and terminal status; intent clarifications use only their user-derived unresolved terms. If a clarification fails validation, it is regenerated up to three times. If only the clarification repeatedly fails while an answer remains independently safe, a deterministic fallback preserves the safe answer and appends a neutral general clarification. Unsafe or ungrounded answer/evidence failures continue to escalation.

### 
