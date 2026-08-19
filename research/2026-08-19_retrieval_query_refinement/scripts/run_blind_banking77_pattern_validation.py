"""Blind semantic validation of corpus-pattern retrieval on fresh BANKING77 rows.

This is an evaluation harness only.  It never writes to the production Chroma
store or SQLite knowledge base.  Its safeguards are deliberate:

* evaluation questions are sampled from BANKING77 test rows not used by the
  prior retrieval-development fixture;
* FAQ-pattern generation receives only a Product x Intent scope and corpus
  text in that scope--never a customer question, expected parent ID, or prior
  retrieval result;
* Gemini and GPT judge anonymised, shuffled hydrated evidence sets without
  knowing the strategy or parent IDs.

The harness compares direct retrieval with direct-anchor-plus-corpus-pattern
retrieval.  It checkpoints every remote result, allowing safe continuation
after an API rate limit.
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import json
import random
import re
import signal
import sqlite3
import sys
import time
from collections import defaultdict
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[3]
RESEARCH_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.app.core.llm_service import call_llm_router
from backend.app.orchestration.rag_engine import RAGEngine
from benchmark_retrieval import (
    _combined_clean_query,
    _get_or_build_generic_collection,
    _run_query_view_rrf_case,
    _run_single_query_case,
)
from build_banking77_horizon_eval import MAPPINGS

BANKING77_TEST = RESEARCH_ROOT / "data" / "banking77_test.csv"
DEVELOPMENT_FIXTURE = RESEARCH_ROOT / "fixtures" / "banking77_horizon_retrieval_eval_v1.json"
OUTPUT = RESEARCH_ROOT / "results" / "blind_banking77_pattern_validation_v1.json"
SAMPLE_PER_CATEGORY = 5
SAMPLE_SEED = 20260820
# Use GPT for corpus-pattern distillation so the Gemini judge quota remains
# available for an independent blinded assessment.
PATTERN_MODEL = "openai/gpt-oss-120b"
PATTERN_FALLBACK_MODEL = "gemini-3.1-flash-lite"
JUDGE_MODELS = {
    # 3.5 intermittently timed out during this large evidence-set evaluation;
    # use the user-approved 3.1 fallback for unfinished checkpoint entries.
    "gemini": "gemini-3.1-flash-lite",
    "gpt": "openai/gpt-oss-120b",
    "gemini_31_backup": "gemini-3.1-flash-lite",
}
GEMINI_JUDGE_FALLBACK_MODEL = "gemini-3.1-flash-lite"
REMOTE_CALL_TIMEOUT_SECONDS = 20


def _parse_json(raw: str) -> dict[str, Any]:
    clean = raw.strip()
    if clean.startswith("```json"):
        clean = clean[7:]
    elif clean.startswith("```"):
        clean = clean[3:]
    if clean.endswith("```"):
        clean = clean[:-3]
    parsed = json.loads(clean.strip())
    if not isinstance(parsed, dict):
        raise ValueError("LLM result was not a JSON object")
    return parsed


class _RemoteCallTimeout(TimeoutError):
    pass


def _call_remote(prompt: str, model_id: str) -> str:
    """Bound a remote request so a rate-limited model can use its fallback."""
    previous_handler = signal.getsignal(signal.SIGALRM)

    def on_timeout(_signum, _frame):
        raise _RemoteCallTimeout(f"{model_id} exceeded {REMOTE_CALL_TIMEOUT_SECONDS}s")

    signal.signal(signal.SIGALRM, on_timeout)
    signal.alarm(REMOTE_CALL_TIMEOUT_SECONDS)
    try:
        return call_llm_router(prompt, model_id=model_id)
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, previous_handler)


def _scope_key(scope_pairs: list[dict]) -> str:
    return json.dumps(scope_pairs, sort_keys=True, separators=(",", ":"))


def _load_fresh_cases() -> list[dict]:
    prior = json.loads(DEVELOPMENT_FIXTURE.read_text(encoding="utf-8"))
    used_customer_queries = {case["customer_query"] for case in prior}
    with BANKING77_TEST.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))

    rng = random.Random(SAMPLE_SEED)
    cases = []
    for category, mapping in MAPPINGS.items():
        remaining = [
            row["text"].strip()
            for row in rows
            if row["category"] == category and row["text"].strip() not in used_customer_queries
        ]
        if len(remaining) < SAMPLE_PER_CATEGORY:
            raise ValueError(f"Not enough unseen BANKING77 rows for {category}")
        for number, customer_query in enumerate(rng.sample(remaining, SAMPLE_PER_CATEGORY), start=1):
            cases.append({
                "id": f"blind_banking77_{category}_{number:02d}",
                "source_intent": category,
                "customer_query": customer_query,
                "direct_query": customer_query,
                "scope_pairs": mapping["scope_pairs"],
                # These are retained only for post-hoc diagnostic reporting;
                # no remote pattern or judge prompt receives them.
                "expected_parent_id": mapping["acceptable_parent_ids"][0],
                "diagnostic_acceptable_parent_ids": mapping["acceptable_parent_ids"],
            })
    return cases


def _scope_parent_texts(scope_pairs: list[dict]) -> list[str]:
    connection = sqlite3.connect(PROJECT_ROOT / "backend" / "data" / "knowledge_base.db")
    try:
        rows = connection.execute(
            "SELECT product_tags, intent_families, content FROM knowledge_parents"
        ).fetchall()
    finally:
        connection.close()

    included = []
    for product_tags, intent_families, content in rows:
        products = set(json.loads(product_tags))
        intents = set(json.loads(intent_families))
        if any(
            pair["product"] in products and set(pair["intents"]).intersection(intents)
            for pair in scope_pairs
        ):
            included.append(content)
    if not included:
        raise ValueError(f"No parent FAQ text available for scope {scope_pairs}")
    return included


def _pattern_prompt(scope_pairs: list[dict], parent_texts: list[str]) -> str:
    rendered_scope = json.dumps(scope_pairs)
    # Pattern distillation needs the corpus vocabulary, not every long answer
    # verbatim.  Keeping each FAQ's question plus a short answer lead makes
    # the remote call stable while still preventing query/answer-label leakage.
    def compact(text: str) -> str:
        question, separator, answer = text.partition("Answer:")
        answer_lead = re.sub(r"\s+", " ", answer).strip()[:260]
        return f"{question.strip()}\nAnswer: {answer_lead}" if separator else text[:420]

    rendered_evidence = "\n\n".join(
        f"FAQ {index}:\n{compact(text)}" for index, text in enumerate(parent_texts, start=1)
    )
    return f"""
CORPUS PATTERN DISTILLATION FOR RETRIEVAL EVALUATION

You are given Horizon FAQ records inside this retrieval boundary:
{rendered_scope}

Write 2 to 4 concise, complete search-language patterns that capture recurring
bank terminology, named actions, and distinct customer outcomes actually
present in the FAQ records. Preserve the source meaning. Do not invent a
product, eligibility rule, procedure, or service not present in the records.

You are NOT given a customer query. Do not assume one particular FAQ is the
target and do not mention identifiers, scores, or this evaluation.

Output JSON only:
{{"patterns": ["complete FAQ-shaped retrieval phrase"]}}

FAQ RECORDS:
{rendered_evidence}
"""


def _generate_scope_patterns(scope_pairs: list[dict]) -> dict[str, Any]:
    parent_texts = _scope_parent_texts(scope_pairs)
    prompt = _pattern_prompt(scope_pairs, parent_texts)
    last_error = None
    for model in (PATTERN_MODEL, PATTERN_FALLBACK_MODEL):
        try:
            result = _parse_json(_call_remote(prompt, model))
            patterns = [str(item).strip() for item in result.get("patterns", []) if str(item).strip()]
            if not 2 <= len(patterns) <= 4:
                raise ValueError(f"Expected 2-4 patterns, received {len(patterns)}")
            return {
                "model": model,
                "patterns": list(dict.fromkeys(patterns)),
                "source_parent_count": len(parent_texts),
            }
        except Exception as error:  # fallback is intentional for rate limits
            last_error = error
            print(f"Pattern model {model} failed: {error}")
    raise RuntimeError(f"Could not generate corpus patterns: {last_error}")


def _fetch_parent_contents(parent_ids: list[str]) -> list[str]:
    if not parent_ids:
        return []
    connection = sqlite3.connect(PROJECT_ROOT / "backend" / "data" / "knowledge_base.db")
    try:
        rows = connection.execute(
            "SELECT parent_id, content FROM knowledge_parents WHERE parent_id IN ({})".format(
                ",".join("?" * len(parent_ids))
            ),
            parent_ids,
        ).fetchall()
    finally:
        connection.close()
    by_id = dict(rows)
    return [by_id[parent_id] for parent_id in parent_ids if parent_id in by_id]


def _anonymise_parent_text(text: str) -> str:
    return re.sub(r"\{\{faq_[^}]+\}\}", "[exact value]", text)


def _judge_prompt(customer_query: str, parent_texts: list[str]) -> str:
    rendered_evidence = "\n\n".join(
        f"EVIDENCE {index}:\n{_anonymise_parent_text(text)}"
        for index, text in enumerate(parent_texts, start=1)
    )
    return f"""
BLIND RETRIEVAL EVIDENCE EVALUATION

Customer message:
{customer_query}

Assess only whether the supplied evidence set can support a read-only bank FAQ
answer to this message. Use no external banking knowledge and do not infer
facts absent from the evidence.

Definitions:
- full: the evidence set directly answers the material customer need.
- partial: it provides useful related information but misses a material part.
- none: it cannot sensibly answer the need.

Output JSON only:
{{
  "answerability": "full" | "partial" | "none",
  "relevant_evidence_indexes": [1],
  "missing_information": "brief factual description, or empty string for full",
  "reason": "brief explanation based only on supplied evidence"
}}

EVIDENCE SET:
{rendered_evidence}
"""


def _judge_once(prompt: str, judge_name: str) -> dict[str, Any]:
    model = JUDGE_MODELS[judge_name]
    try:
        parsed = _parse_json(_call_remote(prompt, model))
        actual_model = model
    except Exception:
        if judge_name != "gemini":
            raise
        parsed = _parse_json(_call_remote(prompt, GEMINI_JUDGE_FALLBACK_MODEL))
        actual_model = GEMINI_JUDGE_FALLBACK_MODEL
    answerability = str(parsed.get("answerability", "")).lower()
    if answerability not in {"full", "partial", "none"}:
        raise ValueError(f"Invalid answerability from {judge_name}: {answerability}")
    indexes = parsed.get("relevant_evidence_indexes", [])
    if not isinstance(indexes, list):
        indexes = []
    return {
        "model": actual_model,
        "answerability": answerability,
        "relevant_evidence_indexes": indexes,
        "missing_information": str(parsed.get("missing_information", "")),
        "reason": str(parsed.get("reason", "")),
    }


def _save(output: dict[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(output, indent=2), encoding="utf-8")


async def _retrieve_cases(cases: list[dict], scope_patterns: dict[str, dict[str, Any]]) -> dict[str, dict[str, dict]]:
    collection = _get_or_build_generic_collection(rebuild=False)
    engine = RAGEngine(collection=collection)
    results: dict[str, dict[str, dict]] = {}
    for index, case in enumerate(cases, start=1):
        scope = scope_patterns[_scope_key(case["scope_pairs"])]
        direct = await _run_single_query_case(engine, case, _combined_clean_query(case))
        views = [case["customer_query"], *[
            f"{case['customer_query']}; {pattern}" for pattern in scope["patterns"]
        ]]
        corpus_patterns = await _run_query_view_rrf_case(engine, case, views)
        results[case["id"]] = {
            "direct_anchor": direct,
            "corpus_pattern_narrative": corpus_patterns,
        }
        print(f"Retrieved {index}/{len(cases)}: {case['id']}")
    return results


def _randomised_evidence(case_id: str, strategy: str, parent_ids: list[str]) -> list[str]:
    evidence = _fetch_parent_contents(parent_ids)
    rng = random.Random(f"{SAMPLE_SEED}:{case_id}:{strategy}")
    rng.shuffle(evidence)
    return evidence


def _judge_all(
    output: dict[str, Any],
    output_path: Path,
    pause_seconds: float,
    max_new_judgments: int | None,
    judge_names: list[str],
) -> None:
    total = len(output["cases"]) * 2 * len(judge_names)
    complete = sum(
        1
        for case in output["cases"]
        for strategy in case["strategies"].values()
        for judge in judge_names
        if judge in strategy.get("judgments", {})
    )
    new_judgments = 0
    for case in output["cases"]:
        for strategy_name, strategy in case["strategies"].items():
            evidence = _randomised_evidence(case["id"], strategy_name, strategy["selected_parent_ids"])
            strategy["evidence_count"] = len(evidence)
            prompt = _judge_prompt(case["customer_query"], evidence)
            for judge_name in judge_names:
                prior_judgment = strategy.setdefault("judgments", {}).get(judge_name, {})
                if prior_judgment.get("answerability") in {"full", "partial", "none"}:
                    continue
                try:
                    strategy["judgments"][judge_name] = _judge_once(prompt, judge_name)
                except Exception as error:
                    strategy["judgments"][judge_name] = {"error": str(error), "model": JUDGE_MODELS[judge_name]}
                complete += 1
                new_judgments += 1
                _save(output, output_path)
                print(f"Judged {complete}/{total}: {case['id']} / {strategy_name} / {judge_name}")
                if pause_seconds:
                    time.sleep(pause_seconds)
                if max_new_judgments and new_judgments >= max_new_judgments:
                    return


def _summarize(output: dict[str, Any]) -> dict[str, Any]:
    by_strategy: dict[str, dict[str, Any]] = defaultdict(lambda: {
        "sets": 0, "agreement_sets": 0, "disagreement_sets": 0,
        **{judge_name: defaultdict(int) for judge_name in JUDGE_MODELS},
    })
    paired = defaultdict(int)
    for case in output["cases"]:
        direct = case["strategies"]["direct_anchor"]["judgments"]
        corpus = case["strategies"]["corpus_pattern_narrative"]["judgments"]
        for name, strategy in case["strategies"].items():
            record = by_strategy[name]
            record["sets"] += 1
            answers = []
            for judge_name in JUDGE_MODELS:
                verdict = strategy["judgments"].get(judge_name, {})
                answer = verdict.get("answerability")
                if answer:
                    record[judge_name][answer] += 1
                    answers.append(answer)
            if len(answers) == 2:
                if answers[0] == answers[1]:
                    record["agreement_sets"] += 1
                else:
                    record["disagreement_sets"] += 1
        for judge_name in JUDGE_MODELS:
            before = direct.get(judge_name, {}).get("answerability")
            after = corpus.get(judge_name, {}).get("answerability")
            if before and after:
                paired[f"{judge_name}:{before}_to_{after}"] += 1

    return {
        "strategy_summary": {
            name: {
                **{key: value for key, value in record.items() if key not in JUDGE_MODELS},
                **{judge_name: dict(record[judge_name]) for judge_name in JUDGE_MODELS},
            }
            for name, record in by_strategy.items()
        },
        "paired_judge_outcomes": dict(paired),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=OUTPUT)
    parser.add_argument("--pause-seconds", type=float, default=0.0)
    parser.add_argument("--retrieve-only", action="store_true")
    parser.add_argument("--max-new-judgments", type=int, default=None)
    parser.add_argument("--judges", nargs="+", choices=sorted(JUDGE_MODELS), default=sorted(JUDGE_MODELS))
    args = parser.parse_args()

    if args.output.exists():
        output = json.loads(args.output.read_text(encoding="utf-8"))
        print(f"Resuming {args.output}")
    else:
        cases = _load_fresh_cases()
        scope_patterns = {}
        for scope in { _scope_key(case["scope_pairs"]): case["scope_pairs"] for case in cases }.values():
            key = _scope_key(scope)
            print(f"Distilling corpus patterns for {scope}")
            scope_patterns[key] = _generate_scope_patterns(scope)
        retrievals = asyncio.run(_retrieve_cases(cases, scope_patterns))
        output = {
            "metadata": {
                "purpose": "Fresh BANKING77 blind semantic validation of corpus-pattern retrieval",
                "sample_seed": SAMPLE_SEED,
                "sample_per_category": SAMPLE_PER_CATEGORY,
                "pattern_generation_rule": "scope and FAQ corpus only; no customer query or expected parent ID",
                "judge_blinding_rule": "anonymised, shuffled hydrated evidence; no parent IDs or strategy labels",
            },
            "scope_patterns": scope_patterns,
            "cases": [
                {
                    **case,
                    "strategies": {
                        name: {
                            "selected_parent_ids": result["selected_parent_ids"],
                            "query_view_count": len(result.get("query_views", [result.get("query_text", "")])),
                            "judgments": {},
                        }
                        for name, result in retrievals[case["id"]].items()
                    },
                }
                for case in cases
            ],
        }
        _save(output, args.output)

    if not args.retrieve_only:
        _judge_all(output, args.output, args.pause_seconds, args.max_new_judgments, args.judges)
    output["summary"] = _summarize(output)
    _save(output, args.output)
    print(json.dumps(output["summary"], indent=2))


if __name__ == "__main__":
    main()
