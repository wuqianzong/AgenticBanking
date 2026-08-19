"""Build canonical generic-child staging without changing historical artifacts.

The previous production representation used question anchors plus intact or
splintered question-and-answer chunks.  This script preserves those historical
child texts verbatim while replacing obsolete Product×Intent metadata with the
same canonical taxonomy used by the current runtime matrix.
"""

import ast
import json
import tempfile
from collections import defaultdict
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent
ACTIVE_ETL_DIR = SCRIPT_DIR.parent
ETL_DIR = ACTIVE_ETL_DIR.parent
DATA_DIR = ETL_DIR.parent
PROJECT_ROOT = DATA_DIR.parent.parent
CANONICAL_ETL_DIR = ETL_DIR / "canonical_taxonomy"
HISTORICAL_GENERIC_DIR = ETL_DIR / "history" / "generic_chunking"

GENERIC_SOURCE = HISTORICAL_GENERIC_DIR / "artifacts" / "ready_for_ingestion.json"
RECTIFIED_SOURCE = CANONICAL_ETL_DIR / "artifacts" / "rectified_horizon_faqs.json"
INTENT_CONSOLIDATOR = CANONICAL_ETL_DIR / "scripts" / "1c_consolidate_intents.py"
OUTPUT_FILE = ACTIVE_ETL_DIR / "staging" / "active_generic_ready_for_ingestion.json"
RUNTIME_NODES = PROJECT_ROOT / "backend" / "app" / "orchestration" / "nodes.py"


def _as_clean_list(value: object) -> list[str]:
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list):
        return []
    return list(dict.fromkeys(str(item).strip() for item in value if str(item).strip()))


def _read_literal(path: Path, variable_name: str) -> object:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for statement in tree.body:
        if isinstance(statement, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == variable_name
            for target in statement.targets
        ):
            return ast.literal_eval(statement.value)
    raise ValueError(f"{variable_name} could not be read from {path}")


def _load_canonical_faqs() -> dict[str, dict]:
    matrix = _read_literal(RUNTIME_NODES, "PRODUCT_INTENT_MATRIX")
    intent_mapping = _read_literal(INTENT_CONSOLIDATOR, "INTENT_MAPPING")
    if not isinstance(matrix, dict) or not isinstance(intent_mapping, dict):
        raise ValueError("Runtime matrix or intent mapping has an invalid type.")

    corpus = json.loads(RECTIFIED_SOURCE.read_text(encoding="utf-8"))
    if not isinstance(corpus, list):
        raise ValueError("Rectified corpus must be a JSON array.")

    canonical: dict[str, dict] = {}
    for faq in corpus:
        faq_id = str(faq.get("faq_id", "")).strip()
        products = _as_clean_list(faq.get("product_tag"))
        raw_intents = _as_clean_list(faq.get("intent_families"))
        intents = list(dict.fromkeys(
            intent_mapping.get(raw_intent.replace("_", " ").strip().title(), "General Inquiries")
            for raw_intent in raw_intents
        ))
        if not faq_id or not products or not intents:
            raise ValueError(f"Rectified FAQ lacks id, product, or intent: {faq_id or '<unknown>'}")
        invalid_pairs = [
            f"{product}/{intent}"
            for product in products
            for intent in intents
            if product not in matrix or intent not in matrix[product]
        ]
        if invalid_pairs:
            raise ValueError(f"{faq_id} has invalid Product/Intent pairs: {invalid_pairs}")
        canonical[faq_id] = {**faq, "product_tag": products, "intent_families": intents}
    return canonical


def reconcile_generic_stage() -> None:
    canonical = _load_canonical_faqs()
    historical = json.loads(GENERIC_SOURCE.read_text(encoding="utf-8"))
    historical_parents = historical.get("parent_data", [])
    historical_children = historical.get("child_data", [])
    if not isinstance(historical_parents, list) or not isinstance(historical_children, list):
        raise ValueError("Historical generic artifact must contain parent_data and child_data arrays.")

    historical_ids = {str(parent.get("parent_id", "")).strip() for parent in historical_parents}
    if historical_ids != set(canonical):
        missing_children = sorted(set(canonical) - historical_ids)
        missing_canonical = sorted(historical_ids - set(canonical))
        raise ValueError(
            "Historical/canonical parent ID sets differ. "
            f"Missing generic records: {missing_children[:5]}; "
            f"missing canonical records: {missing_canonical[:5]}"
        )

    # The historical stage duplicates identical chunks for obsolete intent
    # lanes. Keep every unique child text/type once, then attach only current
    # canonical lanes below.
    chunks: dict[str, list[dict]] = defaultdict(list)
    seen_chunks: dict[str, set[tuple[str, str]]] = defaultdict(set)
    for child in historical_children:
        metadata = child.get("metadata", {})
        faq_id = str(metadata.get("parent_id", "")).strip() if isinstance(metadata, dict) else ""
        text = str(child.get("text", "")).strip()
        chunk_type = str(metadata.get("chunk_type", "")).strip() if isinstance(metadata, dict) else ""
        if faq_id not in canonical:
            raise ValueError(f"Historical child references unknown parent: {faq_id or '<missing>'}")
        if not text or not chunk_type:
            raise ValueError(f"Historical child is incomplete for parent: {faq_id}")
        key = (text, chunk_type)
        if key not in seen_chunks[faq_id]:
            seen_chunks[faq_id].add(key)
            chunks[faq_id].append({"text": text, "chunk_type": chunk_type})

    parent_data: list[dict] = []
    child_data: list[dict] = []
    for faq_id, faq in canonical.items():
        question = str(faq.get("synth_question", "")).strip()
        answer = str(faq.get("tokenized_answer", faq.get("synth_answer", ""))).strip()
        if not question or not answer:
            raise ValueError(f"Canonical parent content is incomplete: {faq_id}")
        parent_data.append({
            "parent_id": faq_id,
            "product_tags": json.dumps(faq["product_tag"], ensure_ascii=False),
            "intent_families": json.dumps(faq["intent_families"], ensure_ascii=False),
            "content": f"Question: {question}\n\nAnswer: {answer}",
        })
        if not chunks.get(faq_id):
            raise ValueError(f"Historical generic artifact has no child chunks for {faq_id}")
        for intent in faq["intent_families"]:
            for chunk in chunks[faq_id]:
                child_data.append({
                    "text": chunk["text"],
                    "metadata": {
                        "parent_id": faq_id,
                        "product_tag": faq["product_tag"],
                        "intent_family": intent,
                        "chunk_type": chunk["chunk_type"],
                    },
                })

    output = {"parent_data": parent_data, "child_data": child_data}
    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", dir=OUTPUT_FILE.parent, delete=False
    ) as temporary:
        json.dump(output, temporary, indent=2, ensure_ascii=False)
        temporary_path = Path(temporary.name)
    temporary_path.replace(OUTPUT_FILE)

    print(
        "Prepared active generic staging artifact: "
        f"{len(parent_data)} parents, {len(child_data)} children -> {OUTPUT_FILE}"
    )


if __name__ == "__main__":
    reconcile_generic_stage()
