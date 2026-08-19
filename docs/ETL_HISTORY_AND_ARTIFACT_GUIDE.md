# ETL History and Artifact Guide

**Project:** Horizon Bank Agentic Banking Chatbot  
**Purpose:** Prevent future ETL work from mixing historical data branches, staging contracts, and vector stores.  
**Status:** Historical reconstruction verified from source code, JSON schemas, live collection layouts, and Git history.

## 1. Read this before changing RAG data

This repository contains multiple generations of data preparation and chunking. File proximity does **not** establish a valid pipeline relationship.

The current repository has:

1. one common raw/synthetic FAQ lineage;
2. an early generic parent-child chunking generation;
3. a later Product×Intent rectification plus generic-chunk generation, used by the active runtime; and
4. a later tri-angle generation branch that contains useful vector text but stale taxonomy and is stored in a disconnected collection.

Do not run a chunker or ingestion writer until its input schema, output schema, and target collection have been checked together.

## 2. Core terminology

| Term | Meaning |
| --- | --- |
| Parent | A full FAQ record stored in SQLite and addressed by `parent_id`. |
| Child | A vector-search record that points to one parent. |
| Generic child | A question anchor, intact answer, or answer fragment. |
| Tri-angle child | A compact `procedural`, `policy`, or `conceptual` search phrase. |
| Canonical taxonomy | The Product×Intent labels produced by the numbered rectification pipeline. |
| Active runtime store | `backend/data/vector_store/`, collection `faq_child_chunks`. |

## 3. Shared source lineage

The common ancestor is the Trust Bank raw corpus.

```text
backend/data/raw_sources/raw_trustbank_faqs.json
  → FAQ_prep_tools/map_and_synthesize.py
  → mapped_trustbank_faqs.json
  → FAQ_prep_tools/tokenize_precision_data.py
  → tokenized_horizon_faqs.json
```

### 3.1 Raw corpus

**File:** `backend/data/raw_sources/raw_trustbank_faqs.json`  
**Producer:** `backend/data/raw_sources/FAQ_prep_tools/extract_raw_faqs.py`

This is the 708-record scraped Trust Bank FAQ corpus. It carries original source text, category, historical domain, and an early singular `intent_family` field.

### 3.2 Synthetic Horizon mapping

**Script:** `backend/data/raw_sources/FAQ_prep_tools/map_and_synthesize.py`  
**Input:** `raw_trustbank_faqs.json`  
**Output:** `mapped_trustbank_faqs.json`

This LLM stage rewrites/rebrands FAQ text for Horizon Bank and creates the early free-form `intent_families` array. It retains source facts and creates `synth_question` and `synth_answer`.

### 3.3 Precision tokenisation

**Script:** `backend/data/raw_sources/FAQ_prep_tools/tokenize_precision_data.py`  
**Input:** `mapped_trustbank_faqs.json`  
**Output:** `tokenized_horizon_faqs.json`

This stage replaces numerical values in the synthesized answer with placeholders and writes `tokenized_answer`. The corresponding exact values are stored in `Precision_Reference_Table` in SQLite.

Two current copies of this 708-record artifact are byte-identical:

```text
backend/data/tokenized_horizon_faqs.json
backend/data/raw_sources/tokenized_horizon_faqs.json
```

They are source-level FAQ records. They contain no child chunks, no angle phrases, and no canonical product tags.

## 4. Generation A: early generic parent-child chunking

This was the first parent-child RAG design, introduced around the v0.5 chunking work.

```text
tokenized_horizon_faqs.json
  → FAQ_prep_tools/prepare_chunks.py
  → raw_sources/ready_for_ingestion.json
  → early app/services/db_ingestion.py
  → faq_child_chunks
```

**Chunker:** `backend/data/raw_sources/FAQ_prep_tools/prepare_chunks.py`

It creates generic child records:

- `question_anchor`
- `intact_answer`
- `splintered_answer_part_N`

Long answers are split with an approximately 800-character chunk size and 150-character overlap. Each answer fragment is prepended with its FAQ question. Records are duplicated for every available early intent.

This generation uses the historical `domain` and free-form intent taxonomy. The following are historical artifacts from it:

```text
backend/data/raw_sources/Backup/raw_trustbank_faqs.json
backend/data/raw_sources/Backup/mapped_trustbank_faqs.json
backend/data/raw_sources/Backup/tokenized_horizon_faqs.json
backend/data/raw_sources/Backup/ready_for_ingestion.json
```

Do not use these backup files as an input to a current rebuild.

## 5. Generation B: canonical Product×Intent taxonomy plus generic chunks

This is the numbered rectification pipeline. It was introduced after Generation A and is the source of the current runtime taxonomy.

```text
raw_sources/tokenized_horizon_faqs.json
  → 1_tag_products_clean.py
  → tagged_horizon_faqs.json
  → 1b_rectify_tags.py
  → rectified_horizon_faqs.json
  → 1c_consolidate_intents.py
  → consolidated_horizon_faqs.json
  → 2_chunk_and_stage.py
  → raw_sources/ready_for_ingestion.json
  → 3_db_ingestion.py
  → vector_store / faq_child_chunks
```

### 5.1 Numbered taxonomy scripts

| Script | Input | Output | Role |
| --- | --- | --- | --- |
| `1_tag_products_clean.py` | `tokenized_horizon_faqs.json` | `tagged_horizon_faqs.json` | Adds singular `product_tag`. |
| `1b_rectify_tags.py` | `tagged_horizon_faqs.json` | `rectified_horizon_faqs.json` | Normalizes product labels. |
| `1c_consolidate_intents.py` | `rectified_horizon_faqs.json` | `consolidated_horizon_faqs.json`, `final_product_intent_map.json` | Converts early/free-form labels to the runtime master intent vocabulary. |
| `2_chunk_and_stage.py` | `consolidated_horizon_faqs.json` | `raw_sources/ready_for_ingestion.json` | Creates generic parent-child staging data. |
| `3_db_ingestion.py` | `raw_sources/ready_for_ingestion.json` | SQLite plus `faq_child_chunks` | Rebuilds the active runtime evidence stores. |

### 5.2 Important schema convention

The canonical numbered pipeline writes:

```json
"product_tag": ["Personal_Loan"]
```

The field is singular: `product_tag`, despite holding a list.

The master intent example for the Instant Loan FAQ is:

```json
"intent_families": ["Features And Limits"]
```

### 5.3 Current active runtime collection

**Writer:** `backend/data/raw_sources/3_db_ingestion.py`  
**SQLite:** `backend/data/knowledge_base.db`, table `knowledge_parents`  
**Chroma path:** `backend/data/vector_store/`  
**Chroma collection:** `faq_child_chunks`

`backend/app/orchestration/rag_engine.py` currently reads this collection. The child vectors in it are generic question/answer chunks, not tri-angle vectors.

### 5.4 Stale numbered utility

`backend/data/raw_sources/4_generate_intent_map.py` is not a valid final pipeline stage in its present form. `1c_consolidate_intents.py` writes `final_product_intent_map.json` as a Product→Intent dictionary, but `4_generate_intent_map.py` treats it as a list of FAQ objects and calls `.get()` on each item.

Do not run it without repairing its contract.

## 6. Generation C: tri-angle vectors with stale taxonomy

This branch was added after Generation B, but it branched from the earlier tokenized source instead of the canonical numbered output.

```text
raw_sources/tokenized_horizon_faqs.json
  → app/services/prepare_chunks.py
  → backend/data/ready_for_ingestion.json
  → app/services/db_ingestion.py
  → vector_store/chroma_db / horizon_faqs
```

### 6.1 Tri-angle generation

**Script:** `backend/app/services/prepare_chunks.py`

For each FAQ:

1. use the whole `synth_answer` if its length is at most 1,000 characters;
2. otherwise split the answer into approximately 800-character fragments with 150-character overlap;
3. prepend `synth_question` to each fragment;
4. call the LLM once for that question+fragment to create:
   - `procedural`
   - `policy`
   - `conceptual`;
5. duplicate those three phrases for every intent carried by the source record.

The angle text is generated once per fragment before the per-intent duplication. It is not intent-conditioned.

### 6.2 Tri-angle artifact

**Primary file:** `backend/data/ready_for_ingestion.json`

Its child records use this schema:

```json
{
  "child_id": "faq_example_frag0_procedural_Features",
  "parent_id": "faq_example",
  "product_tag": ["ALL"],
  "intent_family": "Features",
  "search_text": "compact semantic phrase"
}
```

The angle is encoded in `child_id`; there is no dedicated `angle` metadata field.

The artifact contains 708 parents and 3,561 children. It has 1,187 parent/fragment/intent units, each with all three angles. Thirty-six parents have more than one answer fragment.

### 6.3 Why its metadata is stale

The tri-angle script reads the pre-rectification `tokenized_horizon_faqs.json`, then looks for:

```python
faq.get("product_tags", ...)
```

That plural field does not exist in its input, and canonical processing later uses singular `product_tag`. Therefore:

- tri-angle child records default to `product_tag: ["ALL"]`;
- tri-angle parent records receive an empty product array; and
- child records preserve old free-form intents such as `Features`, rather than current master intents such as `Features And Limits`.

The tri-angle phrases themselves remain useful because they were created from the FAQ question and answer fragments, not from the taxonomy labels.

### 6.4 Disconnected vector store

**Writer:** `backend/app/services/db_ingestion.py`  
**Chroma path:** `backend/data/vector_store/chroma_db/`  
**Chroma collection:** `horizon_faqs`

This is not the active collection read by `rag_engine.py`.

### 6.5 Root staging-file variants

| File | Meaning |
| --- | --- |
| `backend/data/ready_for_ingestion 2.json` | Preserved original tri-angle artifact. |
| `backend/data/ready_for_ingestion.json` | Same child array plus parent `content` repaired to include question + tokenized answer. |

**Repair utility:** `backend/app/services/patch_ingestion_json.py`

It changes only parent `content`; it deliberately retains the existing tri-angle children unchanged.

## 7. Why the current state is confusing

The historical tri-angle staging artifact was temporarily placed at `raw_sources/ready_for_ingestion.json`. Commit `044e1bc` later replaced that file with the current generic staging artifact from Generation B.

Therefore, the repository now contains:

```text
Canonical Product×Intent taxonomy + generic active children
Tri-angle child text + stale taxonomy + disconnected Chroma collection
```

This is a branch-alignment problem, not an absence of tri-angle vector data.

## 8. Future implementation rule

Do not regenerate the tri-angle phrases unless the underlying FAQ question/answer text changes.

The intended unification direction is:

```text
preserved tri-angle search_text
  + canonical `product_tag`
  + canonical master `intent_families`
  + parent content built from question + tokenized answer
  → one staging contract
  → backend/data/vector_store / faq_child_chunks
```

The normalization must:

1. retain the existing `search_text` unchanged;
2. derive an explicit `angle` metadata field from each `child_id`;
3. replace stale child Product/Intent metadata with the canonical record’s values;
4. duplicate each distinct fragment+angle phrase only for canonical intent lanes;
5. write the same parent IDs to SQLite;
6. rebuild only the active `faq_child_chunks` collection after validation.

Do not ingest the tri-angle artifact through `app/services/db_ingestion.py` unless that writer is deliberately realigned to the active runtime path and schema.

## 9. File classification reference

| Category | Files |
| --- | --- |
| Raw/synthetic preparation | `FAQ_prep_tools/extract_raw_faqs.py`, `map_and_synthesize.py`, `tokenize_precision_data.py` |
| Historical generic chunker | `FAQ_prep_tools/prepare_chunks.py` |
| Canonical taxonomy | `1_tag_products_clean.py`, `1b_rectify_tags.py`, `1c_consolidate_intents.py` |
| Active generic staging/ingestion | `2_chunk_and_stage.py`, `3_db_ingestion.py` |
| Historical tri-angle staging/ingestion | `app/services/prepare_chunks.py`, `app/services/db_ingestion.py`, `app/services/patch_ingestion_json.py` |
| Diagnostic/one-off/stale helpers | `audit_faqs.py`, `list_categories.py`, `extract_expanded_taxonomy.py`, `fix_taxonomy_mapping.py`, `verify_ingestion.py`, `4_generate_intent_map.py` |

## 10. Validation checklist for any future rebuild

Before publishing a rebuilt index, verify:

1. Every source/canonical/staging parent set has exactly the same `faq_id` set.
2. Every child refers to an existing parent.
3. Every Product×Intent pair is valid under the runtime matrix.
4. Every tri-angle fragment has exactly one procedural, policy, and conceptual child per canonical intent lane.
5. Parent content contains the question plus tokenized answer.
6. Child text contains no precision placeholder requirement; it is retrieval-only evidence.
7. SQLite `knowledge_parents` count equals staged parent count.
8. Chroma `faq_child_chunks` count equals staged child count.
9. The collection used by `rag_engine.py` is the one rebuilt.
10. Representative target FAQs can be retrieved and hydrated through the runtime code path.

