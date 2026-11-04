"""Build active tri-angle staging data without changing historical artifacts.

The historical tri-angle artifact already contains the expensive LLM-generated
search phrases. This script preserves those phrases verbatim, joins each FAQ
to preserved rectified tags and canonical intent-map rules, and emits a new
staging file compatible with the active database writer. It deliberately does
not overwrite any historical artifact.
"""

import ast
import json
import re
import tempfile
from collections import defaultdict
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent
ACTIVE_ETL_DIR = SCRIPT_DIR.parent
ETL_DIR = ACTIVE_ETL_DIR.parent
DATA_DIR = ETL_DIR.parent
PROJECT_ROOT = DATA_DIR.parent.parent
CANONICAL_ETL_DIR = ETL_DIR / "canonical_taxonomy"
HISTORICAL_TRI_ANGLE_DIR = ETL_DIR / "history" / "tri_angle"

TRI_ANGLE_SOURCE = HISTORICAL_TRI_ANGLE_DIR / "artifacts" / "ready_for_ingestion_with_parent_content.json"
# `consolidated_horizon_faqs.json` is a historic materialisation. Its
# checked-in copy predates a few current intent labels, so the rectified
# records plus the preserved `1c` map are the reproducible taxonomy inputs.
RECTIFIED_SOURCE = CANONICAL_ETL_DIR / "artifacts" / "rectified_horizon_faqs.json"
INTENT_CONSOLIDATOR = CANONICAL_ETL_DIR / "scripts" / "1c_consolidate_intents.py"
OUTPUT_FILE = ACTIVE_ETL_DIR / "staging" / "active_tri_angle_ready_for_ingestion.json"
RUNTIME_NODES = PROJECT_ROOT / "backend" / "app" / "orchestration" / "nodes.py"
ANGLE_PATTERN = re.compile(
    r"_frag(?P<fragment_index>\d+)_(?P<angle>procedural|policy|conceptual)_",
    re.IGNORECASE,
)
ANGLE_ORDER = ("procedural", "policy", "conceptual")


def _as_clean_list(value: object) -> list[str]:
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list):
        return []
    return list(dict.fromkeys(str(item).strip() for item in value if str(item).strip()))


def _load_runtime_matrix() -> dict[str, list[str]]:
    """Read the matrix literal without importing nodes and opening Chroma."""
    tree = ast.parse(RUNTIME_NODES.read_text(encoding="utf-8"), filename=str(RUNTIME_NODES))
    for statement in tree.body:
        if isinstance(statement, ast.Assign):
            if any(isinstance(target, ast.Name) and target.id == "PRODUCT_INTENT_MATRIX" for target in statement.targets):
                matrix = ast.literal_eval(statement.value)
                if isinstance(matrix, dict):
                    return matrix
    raise ValueError("PRODUCT_INTENT_MATRIX could not be read from runtime nodes.py")


def _load_intent_mapping() -> dict[str, str]:
    """Read the existing master map without importing its file or writing data."""
    tree = ast.parse(
        INTENT_CONSOLIDATOR.read_text(encoding="utf-8"),
        filename=str(INTENT_CONSOLIDATOR),
    )
    for statement in tree.body:
        if isinstance(statement, ast.Assign):
            if any(isinstance(target, ast.Name) and target.id == "INTENT_MAPPING" for target in statement.targets):
                mapping = ast.literal_eval(statement.value)
                if isinstance(mapping, dict):
                    return mapping
    raise ValueError("INTENT_MAPPING could not be read from 1c_consolidate_intents.py")


def _load_canonical_faqs(matrix: dict[str, list[str]]) -> dict[str, dict]:
    corpus = json.loads(RECTIFIED_SOURCE.read_text(encoding="utf-8"))
    if not isinstance(corpus, list):
        raise ValueError("Rectified corpus must be a JSON array.")
    intent_mapping = _load_intent_mapping()

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
        canonical[faq_id] = {
            **faq,
            "product_tag": products,
            "intent_families": intents,
        }
    return canonical


def _parse_angle(child_id: str) -> tuple[int, str]:
    match = ANGLE_PATTERN.search(child_id)
    if not match:
        raise ValueError(f"Cannot parse tri-angle child ID: {child_id}")
    return int(match.group("fragment_index")), match.group("angle").lower()


def reconcile_tri_angle_stage() -> None:
    matrix = _load_runtime_matrix()
    canonical = _load_canonical_faqs(matrix)
    historical = json.loads(TRI_ANGLE_SOURCE.read_text(encoding="utf-8"))
    historical_parents = historical.get("parent_data", [])
    historical_children = historical.get("child_data", [])
    if not isinstance(historical_parents, list) or not isinstance(historical_children, list):
        raise ValueError("Historical tri-angle artifact must contain parent_data and child_data arrays.")

    historical_ids = {str(parent.get("parent_id", "")).strip() for parent in historical_parents}
    if historical_ids != set(canonical):
        missing_angles = sorted(set(canonical) - historical_ids)
        missing_canonical = sorted(historical_ids - set(canonical))
        raise ValueError(
            "Historical/canonical parent ID sets differ. "
            f"Missing tri-angle records: {missing_angles[:5]}; "
            f"missing canonical records: {missing_canonical[:5]}"
        )

    # Historical children are duplicated for obsolete intent labels. Preserve
    # each phrase once by FAQ/fragment/angle and reject any conflicting phrase
    # rather than silently choosing one.
    phrases: dict[str, dict[tuple[int, str], str]] = defaultdict(dict)
    for child in historical_children:
        faq_id = str(child.get("parent_id", "")).strip()
        if faq_id not in canonical:
            raise ValueError(f"Historical child references unknown parent: {faq_id}")
        fragment_index, angle = _parse_angle(str(child.get("child_id", "")))
        phrase = str(child.get("search_text", "")).strip()
        if not phrase:
            raise ValueError(f"Historical child has empty search_text: {child.get('child_id')}")
        key = (fragment_index, angle)
        previous = phrases[faq_id].get(key)
        if previous is not None and previous != phrase:
            raise ValueError(f"Conflicting historical phrases for {faq_id}, fragment {fragment_index}, angle {angle}")
        phrases[faq_id][key] = phrase

    parent_data: list[dict] = []
    child_data: list[dict] = []
    for faq_id, faq in canonical.items():
        products = _as_clean_list(faq["product_tag"])
        intents = _as_clean_list(faq["intent_families"])
        question = str(faq.get("synth_question", "")).strip()
        answer = str(faq.get("tokenized_answer", faq.get("synth_answer", ""))).strip()
        if not question or not answer:
            raise ValueError(f"Canonical parent content is incomplete: {faq_id}")
        parent_data.append({
            "parent_id": faq_id,
            "product_tags": json.dumps(products, ensure_ascii=False),
            "intent_families": json.dumps(intents, ensure_ascii=False),
            "content": f"Question: {question}\n\nAnswer: {answer}",
        })

        faq_phrases = phrases.get(faq_id, {})
        fragment_indexes = sorted({fragment for fragment, _ in faq_phrases})
        for fragment_index in fragment_indexes:
            angles = {angle: faq_phrases.get((fragment_index, angle)) for angle in ANGLE_ORDER}
            missing_angles = [angle for angle, phrase in angles.items() if not phrase]
            if missing_angles:
                raise ValueError(f"{faq_id} fragment {fragment_index} is missing angles: {missing_angles}")
            for intent in intents:
                for angle in ANGLE_ORDER:
                    child_data.append({
                        "text": angles[angle],
                        "metadata": {
                            "parent_id": faq_id,
                            "product_tag": products,
                            "intent_family": intent,
                            "angle": angle,
                            "fragment_index": fragment_index,
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
        "Prepared active tri-angle staging artifact: "
        f"{len(parent_data)} parents, {len(child_data)} children -> {OUTPUT_FILE}"
    )


if __name__ == "__main__":
    reconcile_tri_angle_stage()
