# RAG ETL Specification v1.0

**Project:** Horizon Bank Agentic Banking Chatbot  
**Scope:** Offline preparation, staging, and database ingestion of the FAQ knowledge base  
**Runtime consumers:** `backend/app/orchestration/rag_engine.py` and the User Journey v2.0 retrieval workflow

## 1. Purpose and boundaries

This document specifies the **currently active reconciled generic-child pipeline** that turns the Horizon Bank synthetic FAQ corpus into the two local stores used by RAG. It is an operational snapshot, not the complete historical record.

For the three-generation history, the historical tri-angle branch, and artifact ownership, see [ETL History and Artifact Guide](ETL_HISTORY_AND_ARTIFACT_GUIDE.md). The current file layout is indexed in [`backend/data/etl/README.md`](../../backend/data/etl/README.md).

- **SQLite** holds complete, tokenized parent FAQ records for factual generation and numerical substitution.
- **ChromaDB** holds child retrieval vectors and their product/intent metadata for filtered semantic search.

The pipeline is offline and manually run. It is not invoked when a customer sends a chat message.

This is intentionally a parent-child design: Chroma selects a `parent_id`; SQLite then supplies the authoritative, complete parent content for sufficiency evaluation and response generation.

## 2. Authoritative active pipeline

The supported production scripts are `backend/data/etl/active/scripts/6_reconcile_generic_stage.py` and `backend/data/etl/active/scripts/3_db_ingestion.py`. They resolve their paths internally and can be run from the repository root.

```text
history/source_preparation/artifacts/raw_trustbank_faqs.json
  └─ history/source_preparation/scripts/map_and_synthesize.py
     └─ mapped_trustbank_faqs.json
        └─ history/source_preparation/scripts/tokenize_precision_data.py
           └─ tokenized_horizon_faqs.json
              └─ canonical_taxonomy/scripts/1_tag_products_clean.py
                 └─ tagged_horizon_faqs.json
                    └─ canonical_taxonomy/scripts/1b_rectify_tags.py
                       └─ canonical_taxonomy/artifacts/rectified_horizon_faqs.json
                          └─ canonical_taxonomy/scripts/1c_consolidate_intents.py
                             ├─ consolidated_horizon_faqs.json
                             └─ final_product_intent_map.json

backend/data/etl/history/generic_chunking/artifacts/ready_for_ingestion.json
canonical_taxonomy/artifacts/rectified_horizon_faqs.json + canonical_taxonomy/scripts/1c_consolidate_intents.py
  └─ active/scripts/6_reconcile_generic_stage.py
     └─ active/staging/active_generic_ready_for_ingestion.json
        └─ active/scripts/3_db_ingestion.py
           ├─ knowledge_base.db / knowledge_parents
           └─ vector_store / faq_child_chunks
```

`backend/data/etl/active/staging/active_generic_ready_for_ingestion.json` is the final active staging JSON. The generic historical staging artifact and the historical tri-angle source are retained under `backend/data/etl/history/` and must not be overwritten.

## 3. Source and transformation stages

### 3.1 Raw extraction

`FAQ_prep_tools/extract_raw_faqs.py` uses Playwright and BeautifulSoup to crawl the public Trust Bank FAQ structure. It discovers FAQ branches, extracts question-answer pairs, assigns generated `faq_id` values, and writes `raw_trustbank_faqs.json`.

Representative source record:

```json
{
  "faq_id": "faq_example",
  "original_category": "instant-loan",
  "question": "How do I apply?",
  "answer": "Source answer text"
}
```

`FAQ_prep_tools/audit_faqs.py` is a diagnostic helper for source completeness, duplicate questions, short answers, and HTML leakage. It does not transform the corpus.

### 3.2 Synthesis and initial intent classification

`FAQ_prep_tools/map_and_synthesize.py` maps each raw category to a broad historical domain, then uses an LLM to:

- create `intent_families`;
- paraphrase the question as `synth_question`;
- paraphrase and rebrand the answer as `synth_answer`.

It writes `mapped_trustbank_faqs.json`. The `domain` field remains in this historical source artifact but is not used by the active RAG runtime.

### 3.3 Precision tokenisation

`FAQ_prep_tools/tokenize_precision_data.py` reads `mapped_trustbank_faqs.json` and:

- replaces numerical values in `synth_answer` with stable placeholders such as `{{faq_example_001}}`;
- writes the protected result to `tokenized_answer`;
- stores the placeholder-to-exact-value mapping in SQLite table `Precision_Reference_Table`.

It writes `tokenized_horizon_faqs.json`. The unmasked `synth_answer` is retained for semantic vectorisation; `tokenized_answer` is used in the parent answer supplied to the LLM.

### 3.4 Product tagging

`1_tag_products_clean.py` reads `tokenized_horizon_faqs.json` and writes `tagged_horizon_faqs.json`.

It adds `product_tag`, a list of approved product values:

- deterministic mapping for known source categories;
- LLM inference only as a fallback for other categories;
- `ALL` for universal bank-wide content.

Important: this script resumes from the existing output length. It is suitable for an interrupted initial tagging job, but is not a safe way to regenerate an already complete output after editing an upstream record. For a clean full regeneration, remove or archive the generated downstream artifacts first, or start at the exact changed stage.

### 3.5 Product and intent rectification

`1b_rectify_tags.py` reads `tagged_horizon_faqs.json` and writes:

- `rectified_horizon_faqs.json` — product labels normalised to the authorised product vocabulary;
- `clean_product_intent_map.json` — a diagnostic product-to-raw-intent map.

The active product vocabulary is:

```text
ALL, Credit_Card, Debit_Card, Deposit_Account, Personal_Loan,
Stocks, Mutual_Funds, Insurance, Horizon_APP, Rewards_Program
```

Unknown product values are converted to `ALL` to keep retrieval metadata within the approved vocabulary.

### 3.6 Master-intent consolidation

`1c_consolidate_intents.py` reads `rectified_horizon_faqs.json` and converts varied source labels to the finite master intent vocabulary used by runtime filtering. It writes:

- `consolidated_horizon_faqs.json`;
- `final_product_intent_map.json`.

Examples:

```text
Features            → Features And Limits
Loan Application    → Application
Repayment Issue     → Loan And Credit Management
```

This step preserves multi-intent records. An FAQ may have more than one master intent.

### 3.7 Product-intent map status

`1c_consolidate_intents.py` writes `final_product_intent_map.json` as a Product→Intent dictionary. `4_generate_intent_map.py` is stale: it expects a list of FAQ records and cannot consume that dictionary correctly. Do not run it as part of a rebuild.

### 3.8 Active generic-child reconciliation staging

`active/scripts/6_reconcile_generic_stage.py` preserves historical question anchors and intact or splintered question-and-answer chunks from `history/generic_chunking/artifacts/ready_for_ingestion.json`, derives canonical Product×Intent metadata from `canonical_taxonomy/artifacts/rectified_horizon_faqs.json` and the `1c_consolidate_intents.py` mapping in memory, and writes `active/staging/active_generic_ready_for_ingestion.json` with exactly two arrays:

```json
{
  "parent_data": [],
  "child_data": []
}
```

#### Parent record schema

```json
{
  "parent_id": "faq_example",
  "product_tags": "Personal_Loan",
  "intent_families": "Application,Features And Limits",
  "content": "Q: ...\nA: ...{{faq_example_001}}..."
}
```

- `product_tags` and `intent_families` are comma-separated strings for SQLite.
- `content` combines `synth_question` with the tokenized answer.

#### Active child-vector staging schema

```json
{
  "text": "question anchor, intact question-and-answer chunk, or answer fragment",
  "metadata": {
    "parent_id": "faq_example",
    "intent_family": "Application",
    "product_tag": ["Personal_Loan"],
    "chunk_type": "question_anchor"
  }
}
```

Every distinct historical child text is retained once per canonical intent lane. Multi-intent duplication remains intentional; SQLite retains one parent row per FAQ.

### 3.9 Database ingestion

`active/scripts/3_db_ingestion.py` is the sole current database writer. The active command reads:

```text
backend/data/etl/active/staging/active_generic_ready_for_ingestion.json
```

It writes:

| Store | Path / name | Purpose |
| --- | --- | --- |
| SQLite | `backend/data/knowledge_base.db` | complete protected parent evidence |
| SQLite table | `knowledge_parents` | `parent_id`, `product_tags`, `intent_families`, `content` |
| Chroma persistent store | `backend/data/vector_store/` | child-vector index |
| Chroma collection | `faq_child_chunks` | filtered semantic retrieval |

The script drops and recreates only `knowledge_parents`, then deletes and recreates only the `faq_child_chunks` Chroma collection. It does not deliberately remove `Precision_Reference_Table`, which is populated by the tokenisation step.

The database ingestion process is therefore a full replacement of active RAG evidence, not an incremental append.

## 4. Runtime retrieval contract

At runtime, `rag_engine.py` uses only:

```text
SQLite:  backend/data/knowledge_base.db → knowledge_parents
Chroma:  backend/data/vector_store/ → faq_child_chunks
```

For each search task it:

1. builds metadata eligibility filters from valid product/intent pairs;
2. searches the task's semantic query views against eligible generic question-and-answer children in Chroma;
3. applies group-level and task-level reciprocal-rank fusion;
4. selects no more than four parent IDs using the 50% drop-off rule;
5. hydrates the exact parent records from SQLite.

The vector document is only a retrieval hook. Downstream sufficiency, response generation, and groundedness logic use hydrated SQLite parent records identified by `parent_id`.

## 5. Safe operating procedures

### 5.1 Full rebuild

Use this only after a source-level corpus or taxonomy change. Ensure the generated outputs are rebuilt in order, then run the database writer last:

```text
map_and_synthesize.py
tokenize_precision_data.py
1_tag_products_clean.py
1b_rectify_tags.py
1c_consolidate_intents.py
6_reconcile_generic_stage.py
3_db_ingestion.py
```

Because the product tagging script resumes an existing output, a clean full rebuild requires deliberately clearing or archiving generated intermediate outputs first. Do not use an existing complete `tagged_horizon_faqs.json` as if it will be regenerated automatically.

### 5.2 Targeted taxonomy correction

For a correction that starts at an already rectified record and does not change numerical text or product tags:

```text
rectified_horizon_faqs.json
  → 1c_consolidate_intents.py
  → 2_chunk_and_stage.py
  → 3_db_ingestion.py
```

This is safer and faster than rerunning upstream LLM synthesis or tagging. If the correction must survive a future full rebuild, mirror it in the relevant upstream curated record before that rebuild.

### 5.3 Validation after ingestion

Verify all of the following before using the refreshed index:

- `knowledge_parents` record count equals `parent_data` count in `active_generic_ready_for_ingestion.json`.
- Chroma `faq_child_chunks` count equals `child_data` count.
- A selected `parent_id` can be retrieved from SQLite.
- Placeholder-bearing parent content remains tokenized and `Precision_Reference_Table` still resolves those placeholders.
- A representative task retrieves expected parent IDs through the current `rag_engine.py` path.

`backend/data/etl/history/tri_angle/scripts/verify_ingestion.py` is historical and references the retired `domain` column during one check. Do not rely on it as the authoritative validation script without updating it.

## 6. Legacy utilities: do not use for the current runtime

The following files implement older layouts and must not be mixed with the active numbered ingestion pipeline:

```text
backend/data/etl/history/tri_angle/scripts/prepare_chunks.py
backend/data/etl/history/tri_angle/scripts/db_ingestion.py
backend/data/etl/history/tri_angle/scripts/patch_ingestion_json.py
backend/data/etl/history/tri_angle/scripts/verify_ingestion.py
backend/data/etl/history/generic_chunking/scripts/prepare_chunks_legacy.py
backend/data/etl/history/diagnostics/scripts/fix_taxonomy_mapping.py
backend/data/etl/history/diagnostics/scripts/extract_expanded_taxonomy.py
```

They reference historical concepts including `domain`, `horizon_faqs`, `vector_store/chroma_db`, alternate `ready_for_ingestion.json` locations, and the disconnected tri-angle staging branch. The tri-angle artifact is historical rather than absent; it must be normalized into the active schema before it can be used by runtime.

They are retained for historical reference only. Do not run them against the current database.

## 7. Data ownership summary

| Asset | Status | Owner / producer |
| --- | --- | --- |
| `raw_trustbank_faqs.json` | source corpus | extraction script |
| `mapped_trustbank_faqs.json` | curated synthetic source | synthesis/mapping stage |
| `tokenized_horizon_faqs.json` | precision-protected source | tokenisation stage |
| `tagged_*`, `rectified_*`, `consolidated_*` | generated taxonomy artifacts | numbered transformation stages |
| `etl/history/generic_chunking/artifacts/ready_for_ingestion.json` | historical generic staging artifact | `history/generic_chunking/scripts/2_chunk_and_stage.py` |
| `etl/history/tri_angle/artifacts/ready_for_ingestion_with_parent_content.json` | historical tri-angle staging artifact with stale taxonomy | `history/tri_angle/scripts/prepare_chunks.py` |
| `etl/active/staging/active_generic_ready_for_ingestion.json` | active reconciled generic staging contract | `active/scripts/6_reconcile_generic_stage.py` |
| `vector_store/faq_child_chunks_tri_angle_v1` | preserved tri-angle analysis collection | renamed live collection; not runtime default |
| `knowledge_base.db` | generated live evidence store | `3_db_ingestion.py` plus tokenisation table creation |
| `vector_store/` | generated live vector index | `3_db_ingestion.py` |

Do not hand-edit the SQLite `knowledge_parents` table or Chroma metadata for a content correction. Modify the appropriate source or transformation artifact, regenerate the active tri-angle staging file, and re-ingest.
