# ETL History and Artifact Guide

**Project:** Horizon Bank Agentic Banking Chatbot  
**Purpose:** Prevent future ETL work from mixing historical data branches, staging contracts, and vector stores.  
**Status:** Historical reconstruction verified from source code, JSON schemas, live collection layouts, and Git history.

## Repository layout after the ETL reorganisation

The historical names and relationships in this guide are preserved, but their
files now live under `backend/data/etl/` so that the active rebuild path cannot
be mistaken for a legacy script.

```text
backend/data/etl/
├── active/
│   ├── scripts/                 # only supported reconciliation and ingestion scripts
│   └── staging/                 # generated active input; never hand-edit
├── canonical_taxonomy/
│   ├── scripts/                 # 1, 1b, 1c Product×Intent stages
│   └── artifacts/               # tagged, rectified, consolidated, maps
└── history/
    ├── source_preparation/      # raw extraction, synthesis, tokenisation
    ├── generic_chunking/        # retired generic-child branch and backups
    ├── tri_angle/               # original stale-taxonomy tri-angle branch
    └── diagnostics/             # one-off and stale helpers
```

The live runtime outputs remain deliberately outside this workspace:
`backend/data/knowledge_base.db` and `backend/data/vector_store/`.

For a runnable entry point, see [`backend/data/etl/README.md`](../../backend/data/etl/README.md).

## 1. Read this before changing RAG data

This repository contains multiple generations of data preparation and chunking. File proximity does **not** establish a valid pipeline relationship.

The current repository has:

1. one common raw/synthetic FAQ lineage;
2. an early generic parent-child chunking generation;
3. a later Product×Intent rectification plus generic-chunk generation; and
4. a later tri-angle generation branch, now reconciled into the active runtime without modifying its historical source artifacts.

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
backend/data/etl/history/source_preparation/artifacts/raw_trustbank_faqs.json
  → history/source_preparation/scripts/map_and_synthesize.py
  → mapped_trustbank_faqs.json
  → history/source_preparation/scripts/tokenize_precision_data.py
  → tokenized_horizon_faqs.json
```

### 3.1 Raw corpus

**File:** `backend/data/etl/history/source_preparation/artifacts/raw_trustbank_faqs.json`  
**Producer:** `backend/data/etl/history/source_preparation/scripts/extract_raw_faqs.py`

This is the 708-record scraped Trust Bank FAQ corpus. It carries original source text, category, historical domain, and an early singular `intent_family` field.

### 3.2 Synthetic Horizon mapping

**Script:** `backend/data/etl/history/source_preparation/scripts/map_and_synthesize.py`  
**Input:** `raw_trustbank_faqs.json`  
**Output:** `mapped_trustbank_faqs.json`

This LLM stage rewrites/rebrands FAQ text for Horizon Bank and creates the early free-form `intent_families` array. It retains source facts and creates `synth_question` and `synth_answer`.

### 3.3 Precision tokenisation

**Script:** `backend/data/etl/history/source_preparation/scripts/tokenize_precision_data.py`  
**Input:** `mapped_trustbank_faqs.json`  
**Output:** `tokenized_horizon_faqs.json`

This stage replaces numerical values in the synthesized answer with placeholders and writes `tokenized_answer`. The corresponding exact values are stored in `Precision_Reference_Table` in SQLite.

Two preserved copies of this 708-record artifact are byte-identical:

```text
backend/data/etl/history/source_preparation/artifacts/tokenized_horizon_faqs.json
backend/data/etl/history/tri_angle/artifacts/tokenized_horizon_faqs.json
```

They are source-level FAQ records. They contain no child chunks, no angle phrases, and no canonical product tags.

## 4. Generation A: early generic parent-child chunking

This was the first parent-child RAG design, introduced around the v0.5 chunking work.

```text
tokenized_horizon_faqs.json
  → history/generic_chunking/scripts/prepare_chunks_legacy.py
  → history/generic_chunking/artifacts/ready_for_ingestion.json
  → early generic database writer
  → faq_child_chunks
```

**Chunker:** `backend/data/etl/history/generic_chunking/scripts/prepare_chunks_legacy.py`

It creates generic child records:

- `question_anchor`
- `intact_answer`
- `splintered_answer_part_N`

Long answers are split with an approximately 800-character chunk size and 150-character overlap. Each answer fragment is prepended with its FAQ question. Records are duplicated for every available early intent.

This generation uses the historical `domain` and free-form intent taxonomy. The following are historical artifacts from it:

```text
backend/data/etl/history/generic_chunking/artifacts/backup/raw_trustbank_faqs.json
backend/data/etl/history/generic_chunking/artifacts/backup/mapped_trustbank_faqs.json
backend/data/etl/history/generic_chunking/artifacts/backup/tokenized_horizon_faqs.json
backend/data/etl/history/generic_chunking/artifacts/backup/ready_for_ingestion.json
```

Do not use these backup files as an input to a current rebuild.

## 5. Generation B: canonical Product×Intent taxonomy plus generic chunks

This is the numbered rectification pipeline. It was introduced after Generation A and is the source of the current runtime taxonomy.

```text
history/source_preparation/artifacts/tokenized_horizon_faqs.json
  → canonical_taxonomy/scripts/1_tag_products_clean.py
  → tagged_horizon_faqs.json
  → canonical_taxonomy/scripts/1b_rectify_tags.py
  → rectified_horizon_faqs.json
  → canonical_taxonomy/scripts/1c_consolidate_intents.py
  → consolidated_horizon_faqs.json
  → history/generic_chunking/scripts/2_chunk_and_stage.py
  → history/generic_chunking/artifacts/ready_for_ingestion.json
  → former generic database writer
  → vector_store / faq_child_chunks
```

### 5.1 Numbered taxonomy scripts

| Script | Input | Output | Role |
| --- | --- | --- | --- |
| `1_tag_products_clean.py` | `tokenized_horizon_faqs.json` | `tagged_horizon_faqs.json` | Adds singular `product_tag`. |
| `1b_rectify_tags.py` | `tagged_horizon_faqs.json` | `rectified_horizon_faqs.json` | Normalizes product labels. |
| `1c_consolidate_intents.py` | `rectified_horizon_faqs.json` | `consolidated_horizon_faqs.json`, `final_product_intent_map.json` | Converts early/free-form labels to the runtime master intent vocabulary. |
| `history/generic_chunking/scripts/2_chunk_and_stage.py` | `consolidated_horizon_faqs.json` | `history/generic_chunking/artifacts/ready_for_ingestion.json` | Creates generic parent-child staging data. |
| former generic database writer | generic historical staging | SQLite plus `faq_child_chunks` | Retired generic runtime rebuild path. |

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

**Writer:** `backend/data/etl/active/scripts/3_db_ingestion.py`  
**SQLite:** `backend/data/knowledge_base.db`, table `knowledge_parents`  
**Chroma path:** `backend/data/vector_store/`  
**Chroma collection:** `faq_child_chunks`

This was the active runtime contract before the reconciliation stage described in section 8. The generic staging artifact is retained as a historical rebuild input, but it is no longer the active vector schema.

### 5.4 Stale numbered utility

`backend/data/etl/history/diagnostics/scripts/4_generate_intent_map.py` is not a valid final pipeline stage in its present form. `1c_consolidate_intents.py` writes `final_product_intent_map.json` as a Product→Intent dictionary, but the utility treats it as a list of FAQ objects and calls `.get()` on each item.

Do not run it without repairing its contract.

## 6. Generation C: tri-angle vectors with stale taxonomy

This branch was added after Generation B, but it branched from the earlier tokenized source instead of the canonical numbered output.

```text
history/tri_angle/artifacts/tokenized_horizon_faqs.json
  → history/tri_angle/scripts/prepare_chunks.py
  → history/tri_angle/artifacts/ready_for_ingestion_with_parent_content.json
  → history/tri_angle/scripts/db_ingestion.py
  → vector_store/chroma_db / horizon_faqs
```

### 6.1 Tri-angle generation

**Script:** `backend/data/etl/history/tri_angle/scripts/prepare_chunks.py`

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

**Primary file:** `backend/data/etl/history/tri_angle/artifacts/ready_for_ingestion_with_parent_content.json`

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

**Writer:** `backend/data/etl/history/tri_angle/scripts/db_ingestion.py`  
**Chroma path:** `backend/data/vector_store/chroma_db/`  
**Chroma collection:** `horizon_faqs`

This is not the active collection read by `rag_engine.py`.

### 6.5 Root staging-file variants

| File | Meaning |
| --- | --- |
| `backend/data/etl/history/tri_angle/artifacts/ready_for_ingestion_original.json` | Preserved original tri-angle artifact. |
| `backend/data/etl/history/tri_angle/artifacts/ready_for_ingestion_with_parent_content.json` | Same child array plus parent `content` repaired to include question + tokenized answer. |

**Repair utility:** `backend/data/etl/history/tri_angle/scripts/patch_ingestion_json.py`

It changes only parent `content`; it deliberately retains the existing tri-angle children unchanged.

## 7. Why the current state is confusing

The historical tri-angle staging artifact was temporarily placed at `raw_sources/ready_for_ingestion.json`. Commit `044e1bc` later replaced that file with the current generic staging artifact from Generation B.

Therefore, the repository now contains:

```text
Canonical Product×Intent taxonomy + generic active children
Tri-angle child text + stale taxonomy + disconnected Chroma collection
```

This is a branch-alignment problem, not an absence of tri-angle vector data.

## 8. Reconciled tri-angle analysis contract and active generic runtime

Do not regenerate the tri-angle phrases unless the underlying FAQ question/answer text changes.

The tri-angle reconciliation path is retained for controlled analysis:

```text
preserved tri-angle search_text
  + preserved `rectified_horizon_faqs.json` product tags
  + preserved `1c_consolidate_intents.py` master-intent rules
  + parent content built from question + tokenized answer
  → `active_tri_angle_ready_for_ingestion.json`
  → `faq_child_chunks_tri_angle_v1` (preserved analysis collection)
```

**Reconciliation script:** `backend/data/etl/active/scripts/5_reconcile_tri_angle_stage.py`
**Derived output:** `backend/data/etl/active/staging/active_tri_angle_ready_for_ingestion.json`

The active runtime has returned to canonical generic children: question anchors,
intact question-and-answer chunks, and answer fragments when necessary.
`backend/data/etl/active/scripts/6_reconcile_generic_stage.py` preserves those
historical child texts, applies canonical metadata, and creates
`active/staging/active_generic_ready_for_ingestion.json`. The default
`3_db_ingestion.py` invocation writes it to runtime collection
`faq_child_chunks`.

The reconciliation:

1. retain the existing `search_text` unchanged;
2. derive an explicit `angle` metadata field from each `child_id`;
3. derive canonical master intents in memory from the rectified records and the existing `1c` mapping, without overwriting `consolidated_horizon_faqs.json`;
4. duplicate each distinct fragment+angle phrase only for canonical intent lanes;
5. write the same parent IDs to SQLite;
6. rebuild only the active `faq_child_chunks` collection after validation.

The preserved analysis collection is `schema_version: tri_angle_v1` and is named
`faq_child_chunks_tri_angle_v1`. The active runtime collection is
`faq_child_chunks` with `schema_version: generic_v1`; `rag_engine.py` therefore
uses generic compatibility mode and does not add an angle metadata filter. Do
not ingest the historical artifact through `history/tri_angle/scripts/db_ingestion.py`; it still writes the disconnected legacy collection.

## 9. File classification reference

| Category | Files |
| --- | --- |
| Raw/synthetic preparation | `history/source_preparation/scripts/` |
| Historical generic chunker | `history/generic_chunking/scripts/prepare_chunks_legacy.py` |
| Canonical taxonomy | `canonical_taxonomy/scripts/` |
| Historical generic staging | `history/generic_chunking/scripts/2_chunk_and_stage.py` |
| Active generic reconciliation/ingestion | `active/scripts/6_reconcile_generic_stage.py`, `active/scripts/3_db_ingestion.py` |
| Preserved tri-angle analysis path | `active/scripts/5_reconcile_tri_angle_stage.py`, `active/staging/active_tri_angle_ready_for_ingestion.json` |
| Historical tri-angle staging/ingestion | `history/tri_angle/scripts/` |
| Diagnostic/one-off/stale helpers | `history/diagnostics/scripts/` |

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
