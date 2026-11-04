"""Groundedness v2.0: shared factual-evidence and conduct/compliance checks.

Any content that will eventually reach a customer -- a live answer, a
greeting, a stored memory summary that later shapes a future greeting, or an
escalation case summary shown before a human handoff -- must pass the same
two independent checks:

1. Evidence groundedness (factual axis): is every claim in the content
   traceable to its declared permitted evidence? "Evidence" is whatever the
   caller's route defines it as -- approved RAG parents, a chat transcript,
   a memory source, a greeting-memory summary -- the check itself is the
   same reasoning shape regardless of what the evidence actually is.
2. Conduct compliance (conduct axis): is the content's behavior consistent
   with the bank's own conduct standards and MAS regulation? This axis is
   evidence-independent -- a perfectly grounded statement can still be a
   conduct violation, and a conduct-compliant statement can still be
   ungrounded.

Callers supply what "evidence" means for their route and any route-specific
reasoning nuance or extra output fields; the prompt shell, the shared rule
content, the model call, and the fail-closed error handling live here once.
"""
import json
from backend.app.core.llm_service import call_groq_lpu, parse_json_robustly, MODEL_B_AUDITOR
from backend.app.core.compliance import REGULATORY_LICENSING_CONTEXT, BANK_CONDUCT_CONTEXT, PRECISION_REFERENCE_CONTRACT, DATA_FOLLOWS


def check_evidence_groundedness(
    content_block: str,
    evidence_bundle,
    evidence_description: str,
    route_specific_rules: str = "",
    extra_output_fields: str = "",
) -> dict:
    """Factual axis: every claim in `content_block` must trace to `evidence_bundle`.

    `content_block` is the caller-formatted customer-facing text to assess
    (a single field, or several labeled fields such as answer + clarification
    + UI payload). `evidence_bundle` may be any JSON-serializable evidence;
    it is rendered as-is if already a string. `extra_output_fields` lets a
    route request additional JSON fields beyond the base
    evidence_grounded/reason/evidence_violations contract, which is always
    present even on a failed or unparsable model call.
    """
    evidence_text = evidence_bundle if isinstance(evidence_bundle, str) else json.dumps(evidence_bundle, ensure_ascii=False)
    prompt = f"""
    GROUNDEDNESS v2.0 — EVIDENCE AUDIT
    You are an evidence-groundedness auditor for a read-only Horizon Bank assistant.

    {PRECISION_REFERENCE_CONTRACT}

    TASK: Assess EVIDENCE GROUNDEDNESS ONLY. Do not assess conduct, tone,
    regulatory compliance, or completeness — a separate auditor handles those.
    Every factual claim, instruction, figure, or suggestion in the Customer-Facing
    Content must be directly supported by the Permitted Evidence; nothing
    outside it may be used as a factual source. The rules for this route, the
    Permitted Evidence, and the Customer-Facing Content are given at the end.

    Output JSON only:
    {{"evidence_grounded": true/false, "reason": "...", "evidence_violations": [{{"scope": "...", "unsupported_content": ["verbatim phrase"], "evidence_gap": "..."}}]{extra_output_fields}}}

    {DATA_FOLLOWS}

    {route_specific_rules}

    Permitted Evidence ({evidence_description}):
    {evidence_text}

    Customer-Facing Content:
    {content_block}
    """
    try:
        result = parse_json_robustly(call_groq_lpu(prompt, model_id=MODEL_B_AUDITOR))
        result.setdefault("evidence_grounded", False)
        result.setdefault("evidence_violations", [])
        result.setdefault("reason", "Evidence groundedness audit did not return a reason.")
        return result
    except Exception as error:
        print(f"🚨 [EVIDENCE GROUNDEDNESS ERROR]: {error}")
        return {
            "evidence_grounded": False,
            "reason": "Evidence groundedness audit failed.",
            "evidence_violations": [{
                "scope": "answer",
                "unsupported_content": [],
                "evidence_gap": "The groundedness audit was unavailable; no additional customer-facing facts or actions may be added.",
            }],
        }


def check_conduct_compliance(
    content_block: str,
    route_specific_rules: str = "",
    extra_output_fields: str = "",
) -> dict:
    """Conduct axis: bank-conduct and MAS-regulatory compliance, evidence-independent.

    Upgrading the bank's own conduct standards means editing
    BANK_CONDUCT_CONTEXT in compliance.py once; every caller of this function
    picks up the change automatically.
    """
    prompt = f"""
    GROUNDEDNESS v2.0 — CONDUCT & COMPLIANCE AUDIT
    You are an independent conduct and regulatory compliance auditor for a
    Singaporean Bank.

    Bank Conduct Rules:
    {BANK_CONDUCT_CONTEXT}

    Regulatory Context:
    {REGULATORY_LICENSING_CONTEXT}

    Language and emoji usage are outside the scope of this conduct audit.
    Evaluate only the semantic meaning and behavioral intent of the content,
    regardless of what language it is written in.

    TASK: Assess CONDUCT & COMPLIANCE ONLY. Do not assess whether facts are
    supported by evidence — a separate auditor handles that. The rules for
    this route and the Customer-Facing Content are given at the end.

    Output JSON only:
    {{"conduct_compliant": true/false, "reason": "...", "conduct_violations": [{{"rule": "bank_conduct" | "regulation", "violation": "...", "unsupported_content": ["verbatim phrase"]}}]{extra_output_fields}}}

    {DATA_FOLLOWS}

    {route_specific_rules}

    Customer-Facing Content:
    {content_block}
    """
    try:
        result = parse_json_robustly(call_groq_lpu(prompt, model_id=MODEL_B_AUDITOR))
        result.setdefault("conduct_compliant", False)
        result.setdefault("conduct_violations", [])
        result.setdefault("reason", "Conduct compliance audit did not return a reason.")
        return result
    except Exception as error:
        print(f"🚨 [CONDUCT COMPLIANCE ERROR]: {error}")
        return {
            "conduct_compliant": False,
            "reason": "Conduct compliance audit failed.",
            "conduct_violations": [],
        }
