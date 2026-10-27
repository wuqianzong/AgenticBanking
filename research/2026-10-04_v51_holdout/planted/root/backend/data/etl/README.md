# ETL Workspace

This directory separates the live RAG rebuild contract from preserved historical
work. Historical artifacts are retained verbatim for reproducibility; they are
not runtime inputs unless an active script explicitly names them.

| Location | Purpose | Safe to run in normal maintenance? |
| --- | --- | --- |
| `active/scripts/6_reconcile_generic_stage.py` | Reconciles historical question/answer child texts with canonical metadata. | Yes; current production staging path. |
| `active/scripts/3_db_ingestion.py` | Rebuilds `knowledge_base.db` evidence and active generic `faq_child_chunks` collection. | Yes, but it replaces live generic RAG data. |
| `active/scripts/5_reconcile_tri_angle_stage.py` | Reconciles tri-angle phrases into an analysis staging artifact. | Only for controlled analysis; it is not the production default. |
| `active/staging/` | Generated, validated input to the active writer. | Generated only; do not hand-edit. |
| `canonical_taxonomy/` | Product and intent normalisation scripts plus their source artifacts. | Only as a deliberate taxonomy rebuild. |
| `history/source_preparation/` | Raw extraction, Horizon synthesis, and precision-tokenisation lineage. | Historical/reproducibility use. |
| `history/generic_chunking/` | Retired generic-child branch and backups. | No; retained for history only. |
| `history/tri_angle/` | Original tri-angle generation branch and disconnected writer. | No; use only as source evidence for reconciliation. |
| `history/diagnostics/` | One-off, stale, or diagnostic utilities. | Only after reviewing each script's contract. |

## Supported rebuild sequence

Run from the repository root so every path remains explicit:

```bash
python backend/data/etl/active/scripts/6_reconcile_generic_stage.py
python backend/data/etl/active/scripts/3_db_ingestion.py
```

The first command preserves historical question and answer child text and
creates `active/staging/active_generic_ready_for_ingestion.json`. The second command
replaces the live `knowledge_parents` SQLite table and active Chroma
`faq_child_chunks` collection. The preserved tri-angle collection is named
`faq_child_chunks_tri_angle_v1` and is not touched by this process. Validate
the generated staging artifact before the second command.

Detailed lineage, contracts, and validation requirements are in
[`docs/etl/ETL_HISTORY_AND_ARTIFACT_GUIDE.md`](../../../docs/etl/ETL_HISTORY_AND_ARTIFACT_GUIDE.md)
and [`docs/etl/RAG_ETL_SPECIFICATION_v1.0.md`](../../../docs/etl/RAG_ETL_SPECIFICATION_v1.0.md).
