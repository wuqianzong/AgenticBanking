import concurrent.futures
import contextvars
import copy
import hashlib
import json
import os
import threading
import time
from pathlib import Path
from typing import Any, List
from groq import Groq
from openai import OpenAI
from google import genai
from google.genai import types
from google.genai.errors import ServerError
from pydantic import BaseModel, Field, ValidationError
from backend.app.core.system_logger import log_event, request_context

# --- 🧠 MULTI-MODEL ARCHITECTURE REGISTRY ---
#model_A="openai/gpt-oss-120b"  # For response generation
model_A="deepseek-flash"  # For response generation
model_B="deepseek-flash"  # For sufficiency, gourndedness, and compliance audits
model_C="deepseek-flash"  # For triage, decomposition, and journey planning
# Previous (free-tier Gemini): "gemini-3.5-flash-lite" for all three.
MODEL_A_GENERATOR = model_A       # Groq: Conversational Engine
MODEL_B_AUDITOR = model_B            # Google: Safety & Sufficiency Judge
MODEL_C_PLANNER = model_C            # Google: Resolution Planner & Tool Calling Generator
MODEL_D_PROMPT_GUARD = "meta-llama/llama-prompt-guard-2-86m"  # Groq: Cyber Security Pre-check
MODEL_E_TRIAGE = model_C             # Google: Journey Classifier
# The provider is chosen by the model id alone (see _call_llm_router_impl):
# "gemini-*" -> Google, "deepseek-*" -> DeepSeek, the prompt guard and any
# other id -> Groq. Each role above can be pointed at any provider
# independently, e.g. model_A="deepseek-flash".
# --------------------------------------------

groq_api_key = os.environ.get("GROQ_API_KEY")
google_api_key = os.environ.get("GOOGLE_API_KEY")


def _load_deepseek_api_key() -> str:
    """DEEPSEEK_API_KEY from the environment, else the git-ignored
    `.deepseek_api_key` file at the repo root -- never `.env`, which is
    tracked in git."""
    key = os.environ.get("DEEPSEEK_API_KEY", "").strip()
    if key:
        return key
    key_file = Path(__file__).resolve().parents[3] / ".deepseek_api_key"
    return key_file.read_text().strip() if key_file.exists() else ""


deepseek_api_key = _load_deepseek_api_key()
# /beta is required for strict tool calling (schema-enforced output); plain
# JSON-mode calls use the stable endpoint.
_DEEPSEEK_BASE_URL = "https://api.deepseek.com"
_DEEPSEEK_BETA_BASE_URL = "https://api.deepseek.com/beta"
# Non-thinking mode defaults to 8K output tokens; the largest structured
# outputs (e.g. task sufficiency over many tasks) need explicit headroom.
_DEEPSEEK_MAX_TOKENS = 16384

# --- 🔁 GOOGLE API KEY POOL ---
# Same rotation pool used by the research test harness (research/.research_api_keys),
# shared by design so both draw from one set of keys. Optional: if the file is
# absent, falls back to the single GOOGLE_API_KEY above exactly as before this
# existed -- no behavior change for any deployment that doesn't have it.
_GOOGLE_KEY_POOL_PATH = Path(__file__).resolve().parents[3] / "research" / ".research_api_keys"


def _load_google_key_pool() -> List[str]:
    if _GOOGLE_KEY_POOL_PATH.exists():
        keys = [line.strip() for line in _GOOGLE_KEY_POOL_PATH.read_text().splitlines() if line.strip()]
        if keys:
            return keys
    return [google_api_key] if google_api_key else []


_google_key_pool = _load_google_key_pool()
_google_key_pool_index = 0
_google_key_pool_lock = threading.Lock()
if _google_key_pool:
    google_api_key = _google_key_pool[_google_key_pool_index]


def _rotate_google_api_key(observed_index: int) -> bool:
    """Advance past a key that just failed at observed_index. Returns False
    once every key in the pool has been tried. Compare-and-swap against
    observed_index rather than unconditionally incrementing: several
    concurrent live requests can hit the same exhausted key at once, and
    without this check each of them would call rotate() and each would
    advance the index by one, skipping past several still-good keys for a
    single actual exhaustion event. Whichever request's compare succeeds
    performs the one real rotation; every other concurrent request sees
    its own observed_index is already stale and just retries against
    whatever key is current now, without moving the index again."""
    global _google_key_pool_index, google_api_key
    with _google_key_pool_lock:
        if _google_key_pool_index != observed_index:
            return _google_key_pool_index < len(_google_key_pool)
        if _google_key_pool_index + 1 >= len(_google_key_pool):
            return False
        _google_key_pool_index += 1
        google_api_key = _google_key_pool[_google_key_pool_index]
        return True


def _reset_google_key_pool() -> None:
    """Restart the rotation cycle from key #1. Used when switching to a
    fallback model (see _GEMINI_MODEL_FALLBACK): the quota being exhausted
    is per (key, model) pair, not just per key -- confirmed live in the
    429 message itself ("model: gemini-3.5-flash-lite", a separate 500/day
    counter from gemini-3.1-flash-lite on the same key) -- so a key that's
    exhausted for the model just given up on may have full headroom left
    for the fallback model, and should be tried again from the start."""
    global _google_key_pool_index, google_api_key
    with _google_key_pool_lock:
        _google_key_pool_index = 0
        if _google_key_pool:
            google_api_key = _google_key_pool[0]


_GEMINI_MODEL_FALLBACK = {
    "gemini-3.5-flash-lite": "gemini-3.1-flash-lite",
    "gemini-3.1-flash-lite": "gemini-3.5-flash-lite",
}


def _is_rate_limited_error(error: Exception) -> bool:
    """A 429/RESOURCE_EXHAUSTED from the model provider -- daily quota or
    per-minute throughput, doesn't matter which for a live request: unlike
    the research harness (which can afford to wait out a per-minute limit
    on the same key), a live customer can't wait, so any rate limit here
    just means "try the next key immediately" rather than "wait and retry
    the same one."""
    code = getattr(error, "code", None)
    status = getattr(error, "status", None)
    message = str(error)
    return code == 429 or status == "RESOURCE_EXHAUSTED" or "429" in message or "RESOURCE_EXHAUSTED" in message


_LLM_CALL_TIMEOUT_SECONDS = 45
_LLM_CALL_EXECUTOR = concurrent.futures.ThreadPoolExecutor(max_workers=16, thread_name_prefix="llm_call_guard")

# =====================================================================
# 📝 PYDANTIC SCHEMAS FOR GOOGLE GEMINI STRUCTURED OUTPUTS
# =====================================================================

# 1. OUTBOUND SEMANTIC GATE (Node 06)
class SemanticEvaluation(BaseModel):
    is_grounded: bool = Field(description="True if answer text and answer-related UI options are derived from approved knowledge. Empty answers are True.")
    is_answer_compliant: bool = Field(description="True if answer text and answer-related UI options are safe, neutral, and contain no unauthorized financial advice or non-compliant instruction. Empty answers are True.")
    contains_advice: bool = Field(description="True if the response contains unauthorized financial advice.")
    is_clarification_grounded: bool = Field(description="True if retrieval-based clarification suggestions are supported by supplied unapproved evidence, and intent-level clarification names only the supplied user-derived unresolved term without adding product facts. A generic apology and request for clarification are True.")
    is_clarification_connected: bool = Field(description="True if every clarification explicitly names or unambiguously identifies its supplied unresolved customer term, rather than using ambiguous references such as 'that' or 'it'.")
    is_clarification_compliant: bool = Field(description="True if clarification text is safe, neutral, and contains no unsupported banking claims or instructions.")
    is_approved_knowledge_compliant: bool = Field(description="True if the selected approved FAQ evidence is safe and compliant for use in a read-only banking response.")
    reason: str = Field(description="A brief sentence explaining the reasoning.")


class GroundingViolation(BaseModel):
    """Internal-only explanation of one customer-visible grounding failure."""

    task_id: str = Field(description="The related task ID, or an empty string when not task-specific.")
    scope: str = Field(description="Must be answer, ui_payload, or clarification.")
    audit_layer: str = Field(
        description="Must be evidence for a direct factual-support failure, bridge for an unsupported inference, or other for a non-answer-specific failure."
    )
    violation_type: str = Field(
        description="A concise failure category such as unsupported_factual_claim, unsupported_bridge_inference, wrong_task_evidence, or unsupported_clarification_suggestion."
    )
    unsupported_content: List[str] = Field(
        default_factory=list,
        description="Exact customer-facing phrase(s) that failed the audit."
    )
    evidence_gap: str = Field(
        description="Why the permitted evidence does not support the phrase. This is an internal constraint, never new evidence."
    )


class AnswerGroundednessEvaluation(BaseModel):
    """Structured result for the normal-answer groundedness v2 audit only."""

    passed: bool = Field(description="True only when every answer, clarification, and visible UI field passes its applicable grounding check.")
    answer_grounded: bool = Field(description="True only when both direct factual evidence and inferential bridge checks pass for the answer and answer UI.")
    answer_evidence_grounded: bool = Field(description="True only when all explicit answer and answer-UI facts are directly supported by task-level approved evidence.")
    answer_bridge_grounded: bool = Field(description="True only when every conclusion or logical connection in the answer is a bounded inference from task-level approved evidence.")
    clarification_grounded: bool = Field(description="True when clarification factual content uses only its permitted clarification sources.")
    clarification_connected: bool = Field(description="True when each clarification explicitly identifies its supplied unresolved term.")
    response_recovery_strategy: str = Field(
        default="NO_STRATEGY",
        description="Must be NO_STRATEGY, REMOVE_UNSUPPORTED_CONTENT, NARROW_TO_EVIDENCE_SCOPE, or CLARIFY_FROM_ALLOWED_CONTEXT. Internal retry constraint only; never evidence or customer wording."
    )
    reason: str = Field(description="Concise overall internal audit result.")
    grounding_violations: List[GroundingViolation] = Field(default_factory=list, description="Every material visible grounding failure, labelled by audit layer.")

# 2. SUFFICIENCY AUDITOR (Node 03c)
class RetryEvidenceDeltaItem(BaseModel):
    """One newly observed, task-relevant fact from an unapproved candidate."""

    parent_id: str = Field(
        description="An exact parent ID supplied in the corresponding task's candidate parents."
    )
    fact: str = Field(
        description="One concise, materially relevant factual proposition supported by that parent."
    )


class VerificationResult(BaseModel):
    """Resolution of one specific, checkable claim the intent formulator
    flagged as unverifiable at formulation time (see
    SearchTask.verification_items) -- the sufficiency auditor's own,
    independent, evidence-grounded answer to it. General-purpose: covers a
    named brand/network exactly as well as a premise, condition, or scope
    claim -- whatever specific thing classification alone could not settle."""
    item: str = Field(description="The exact string from this task's own verification_items this entry resolves.")
    confirmed: bool = Field(description="True ONLY when some supplied parent's own text actually states this exact claim, or explicit equivalent/universal coverage -- never merely because a parent covers the general topic or is silent on this specific point.")
    supporting_parent_id: str = Field(default="", description="The parent ID that confirms it, when confirmed is true. Empty when confirmed is false.")


class ClarificationGuess(BaseModel):
    """A possible customer intention, based on the current context, that
    could resolve this ambiguity if correct -- a prediction of what the
    customer wants, not a report of what they've said. Reusable by any
    node that triggers a clarification, not specific to product
    ambiguity."""
    label: str = Field(description="Short chip label shown to the customer, e.g. 'Personal Loan'.")
    message: str = Field(description="The complete, natural sentence sent to the assistant if this chip is tapped, e.g. 'I'm asking about a personal loan.' Must read as something a customer would actually say -- never a bare word or phrase.")
    reason: str = Field(description="Internal only, never shown to the customer. A concise justification for why this is a plausible customer intention given the current context -- grounded in the Product/Intent Matrix or the conversation so far. If no concrete basis fits the current context, do not offer this guess at all -- leave clarification_guesses empty instead.")

class TaskSufficiencyItem(BaseModel):
    task_id: str = Field(description="The exact task_id supplied in the task input.")
    # Declared before status/approved_parent_ids on purpose: Gemini's
    # structured output generates fields in declaration order, so reasoning
    # through how completely the candidate evidence answers the task BEFORE
    # committing to a verdict lets that reasoning actually inform the
    # verdict, rather than just narrating a decision already made -- the
    # reverse order (reason declared after status, as this schema used to
    # have it) only ever produces a post-hoc rationalization of a verdict
    # the model already committed to.
    reason: str = Field(description="Brief explanation of how completely the candidate evidence answers the task -- reasoned through before deciding status/approved_parent_ids below, not a restatement of them.")
    status: str = Field(description="Must be SUFFICIENT, RELEVANT, or IRRELEVANT.")
    approved_parent_ids: List[str] = Field(description="Only supplied parent IDs that provide material grounded information for this exact task.")
    # A general-purpose writing directive for whoever composes the customer-
    # facing text from this evidence -- not a sufficiency-specific concept,
    # just the one node with enough context to notice when approved evidence
    # needs a caveat generation wouldn't otherwise reliably add on its own.
    # The canonical case this exists for: an approved parent's own stated
    # premise or circumstance (e.g. a card-replacement FAQ that assumes the
    # card is damaged) does not match the customer's own stated situation
    # (e.g. they dislike its appearance) -- reusing the evidence's steps
    # without this note reads as a complete, correct answer when it silently
    # isn't. Deliberately free-text and general, not a typed flag for this
    # one case, so it can carry whatever caveat is actually needed here
    # without requiring a new field for every future nuance discovered --
    # see GROUNDEDNESS_v2.0.md's Bridge/Applicability discussion. Empty when
    # the evidence needs no special framing at all, which is the common case.
    generation_guidance: str = Field(
        default="",
        description=(
            "A directive for whoever writes the customer-facing text from this evidence, only when this "
            "specific evidence needs special framing or a caveat to be presented honestly -- e.g. its own "
            "stated premise or circumstance may not match what the customer actually described. Never a new "
            "fact, and never customer-facing wording itself -- an instruction to the writer, not content to "
            "relay verbatim. Leave empty when the evidence can be used as-is with no special framing. MUST be "
            "empty when approved_parent_ids is empty -- this field frames real, approved evidence, it is never "
            "a substitute for one. A task with no approved parent is IRRELEVANT; that status already gets a "
            "safe, fixed 'no information available' statement with no LLM-generated content at all, so there "
            "is nothing for this field to add and no reason to write anything here."
        ),
    )
    evidence_delta: List[RetryEvidenceDeltaItem] = Field(
        default_factory=list,
        description=(
            "For RELEVANT or IRRELEVANT only: newly observed, materially task-relevant facts from "
            "supplied candidate parents. Empty when no such fact exists."
        ),
    )
    verification_results: List[VerificationResult] = Field(
        default_factory=list,
        description=(
            "REQUIRED, one entry per string in this task's own verification_items (empty list when "
            "verification_items is empty). verification_items was flagged by the intent formulator before any "
            "evidence existed -- a specific, checkable claim (a named brand/network, a premise or condition, a "
            "scope boundary, or any other specific detail classification alone could not settle) the customer's "
            "own wording implied, which could not be checked then. Resolve each one now, independently, against "
            "the supplied candidate parents' own text. An item left unconfirmed caps this task's status at "
            "RELEVANT, never SUFFICIENT, regardless of status below -- this is enforced deterministically "
            "downstream, so do not mark confirmed=true unless some parent genuinely states it."
        ),
    )
    scope_expansion_lead_reason: str = Field(
        default="",
        description=(
            "Extra Effort v2.0, for RELEVANT or IRRELEVANT only: reasoned BEFORE naming the "
            "candidate below. A concise, statable reason why ONE specific supplied candidate parent -- even "
            "though it does NOT materially answer this task's own customer_component -- has a genuine, real "
            "connection worth pursuing as a related topic if this task's literal request turns out "
            "undocumented (e.g. it covers a closely adjacent product, process, or need). The connection must be "
            "concrete and content-based, never merely shared vocabulary, category, or menu placement -- the "
            "same discipline this schema already requires of a clarification guess. If you cannot point to a "
            "real, statable connection, leave this and scope_expansion_lead_parent_id both empty -- a forced "
            "lead here is worse than none, since it would send the next retrieval attempt in a wrong direction "
            "instead of a genuinely reasoned one."
        ),
    )
    scope_expansion_lead_parent_id: str = Field(
        default="",
        description="The exact supplied candidate parent ID scope_expansion_lead_reason justifies. Populate only when scope_expansion_lead_reason is non-empty; otherwise leave empty.",
    )
    unresolved_term: str = Field(
        default="",
        description=(
            "Non-empty only when BOTH hold, regardless of whether status is RELEVANT or IRRELEVANT: (1) a "
            "specific, real detail is genuinely missing from the customer's own words or conversation history -- "
            "never a gap in the evidence, an undocumented topic, or a hard judgment call you had to make "
            "yourself (e.g. 'the fee' without naming which fee, 'the loan' without naming which loan type); and "
            "(2) supplying that detail would plausibly change the outcome -- unlocking a materially different, "
            "fuller answer for a RELEVANT task, or turning an otherwise-unanswerable IRRELEVANT task into an "
            "answerable one. A concise phrase naming exactly the ambiguous or unresolved part -- never the whole "
            "customer_component verbatim unless the whole thing is what's unresolved. Leave empty whenever this "
            "task's evidence is simply incomplete or the topic is undocumented -- that is not an ambiguity, and "
            "a clarifying question is not required just because a task didn't fully resolve."
        ),
    )
    clarification_reason: str = Field(
        default="",
        description=(
            "Populate only when unresolved_term is non-empty. A concise, internal-only explanation of why THIS "
            "specific part needs clarification -- distinct from `reason` above, which explains the evidence "
            "verdict generally (what the candidate parents did or didn't cover) and is used for every status, "
            "not just this one. This field must name the specific detail in Original User Message or Trusted "
            "Conversation Context that makes the request ambiguous or incomplete -- e.g. 'the customer asked "
            "about documents but Original User Message never specifies online vs in-person submission' -- not "
            "a restatement of what the evidence contained. If you cannot point to a specific real detail in the "
            "user's own input or history, unresolved_term is not genuinely justified -- leave both empty."
        ),
    )
    clarification_guesses: List[ClarificationGuess] = Field(
        default_factory=list,
        description=(
            "Populate only when unresolved_term is non-empty, and only when genuinely inferable: 0-3 entries, "
            "each a reasonable, grounded PREDICTION of what the customer likely means by the unresolved part -- "
            "never a report of anything the customer has actually said -- offered as a tappable option that "
            "would unblock this task if correct. Order most likely first. Ground each guess in the specific "
            "candidate evidence you just reviewed for this task (e.g. two of the retrieved parents cover "
            "different sub-topics the customer might have meant) or in Trusted Conversation Context -- never in "
            "the Product/Intent Matrix directly, since this auditor's job is evidence review, not product "
            "classification. Leave empty when unresolved_term is empty, or when you have no real basis to guess "
            "even though a clarifying question is still worth asking."
        ),
    )

class TaskSufficiencyEvaluation(BaseModel):
    task_results: List[TaskSufficiencyItem] = Field(description="One sufficiency result for every supplied task.")

# 3. USER JOURNEY CLASSIFIER / TRIAGE (Node 02d) -> DECOMPOSITION + COMPLIANCE/RELEVANCE SCREEN
class IntentClarificationTask(BaseModel):
    unresolved_request: str = Field(description="The exact ambiguous product, service, or request component from the user's wording.")
    clarification_reason: str = Field(description="A concise, internal-only explanation of why a valid search task cannot be formed without clarification -- scoped to this node's own job (product/intent identification against the Matrix), and naming the specific detail in Latest User Input or Recent Conversation that makes Product, Intent, or both missing/ambiguous -- e.g. 'customer named no product; Recent Conversation has no prior product either' -- not a generic restatement like 'the request is too vague.'")
    clarification_guesses: List[ClarificationGuess] = Field(default_factory=list, description="0-3 entries, each a reasonable, grounded PREDICTION of what the customer likely means -- about the product, the intent, or whatever is actually ambiguous here -- never a report of anything the customer has actually said, offered as a tappable option that would unblock this request if correct. Order most likely first. Populate only when genuinely inferable from context (e.g. a term with only one plausible matrix match); leave empty when there is no real basis to guess.")

class TriageComponent(BaseModel):
    subquery_id: str = Field(description="Stable identifier for one distinct component of the resolved TRUE REQUEST, e.g. subquery_1.")
    # Declared before customer_component on purpose (reasoning-before-output,
    # as elsewhere in this module): naming the one need first is what the
    # need-based decomposition rule turns on -- sentences serving the same
    # need share a component -- and the phrase is also reused verbatim in
    # the deterministic no-info statement (BUG-035), so it must read
    # naturally after "I don't have information available on".
    need: str = Field(default="", description="Short noun phrase naming the one distinct customer need this component serves, written to the customer in the second person, in English, with no leading imperative verb and no trailing punctuation -- e.g. 'the current balance of your savings account', 'freezing your card', 'opening a new savings account', 'your thanks'. No two components may share the same need.")
    customer_component: str = Field(description="Standalone, self-contained restatement of only that component, with pronouns/ellipsis resolved using conversation context -- optimized for retrieval when resolution is TOOL_ELIGIBLE, or for composing a natural reply when resolution is NON_FINANCIAL.")
    resolution: str = Field(description="Must be exactly TOOL_ELIGIBLE (a real banking need proceeding to retrieval/tools), NON_FINANCIAL (tone-only content with no ask that could ever become a banking need -- a greeting, thanks, feeling, or wording clearly and only about something outside what this assistant addresses), or CLARIFICATION (genuinely undecidable between TOOL_ELIGIBLE and NON_FINANCIAL for this specific component -- never used merely because the component's own content is vague or ambiguous in what it's asking, which is a downstream node's job once the component is treated as TOOL_ELIGIBLE).")
    clarification: IntentClarificationTask | None = Field(default=None, description="Present only when resolution is CLARIFICATION; otherwise null.")

class TriageEvaluation(BaseModel):
    triage_status: str = Field(description="Must be NON_COMPLIANT, ESCALATION, or DECOMPOSED.")
    components: List[TriageComponent] = Field(default_factory=list, description="For DECOMPOSED only: one standalone component per distinct customer NEED in the resolved TRUE REQUEST (not per sentence -- sentences serving the same need share one component; components never overlap), together covering everything the customer asked, each tagged with its own resolution. Empty for NON_COMPLIANT/ESCALATION.")
    repetitive_issue_detected: bool = Field(default=False, description="True only when the Trusted Conversation Context shows the customer repeatedly raising substantially the same underlying banking need or issue across multiple recent turns, especially if it still appears unresolved. Judge by meaning, not wording. Independent of triage_status.")

# TURN CONSISTENCY CHECK (Node 04a) -- semantic conflicts between one
# turn's collected results, judged once, after tool calling has finished and
# before response generation. The model names each conflict and decides
# which side to drop; code only validates the decision (node_04a).
class TurnConsistencyItem(BaseModel):
    item_id: str = Field(description="The exact id of one item from the Items list.")
    # Reasoning before verdict, as elsewhere in this module.
    reason: str = Field(description="Which other items, if any, concern the same customer need, and whether anything they say contradicts or undercuts this item. Written before conflicts_with.")
    conflicts_with: List[str] = Field(default_factory=list, description="Ids of the other items this item conflicts with -- presenting both in one response would contradict or undercut each other. Empty when it conflicts with nothing.")
    # Decided last, after the conflict itself is established.
    decision: str = Field(default="keep", description="Exactly 'keep' or 'drop'. 'drop' only for an item that conflicts with another and is the one the customer should not be shown -- the less accurate, less direct, or less certain side of that conflict. An item that conflicts with nothing is always 'keep'.")

class TurnConsistencyEvaluation(BaseModel):
    items: List[TurnConsistencyItem] = Field(default_factory=list, description="One entry per item in the Items list.")

# 4. 2D MATRIX QUERY FORMULATOR (Node 02e) -> NESTED SCHEMA
class SearchTask(BaseModel):
    task_id: str = Field(description="Stable task identifier, e.g. task_1. Preserve it during retries.")
    # Declared before target_commercial_product/target_intent on purpose,
    # same reasoning-before-verdict principle as TaskSufficiencyItem.reason
    # above: committing to a product/intent pairing first and justifying it
    # afterward only ever produces a post-hoc rationalization, never a
    # reason that could have shaped the choice itself.
    mapping_reason: str = Field(
        description=(
            "Reasoned through FIRST, before choosing target_commercial_product/target_intent below: why this "
            "product/intent pairing fits the component, grounded in the Product/Intent Matrix and Trusted "
            "Conversation Context. For a scope-expansion companion task (search_territory=adjacent_scope) "
            "specifically, name the concrete, statable basis for this genuinely different but related "
            "product/intent pairing -- never a generic restatement like 'this seems related.'"
        )
    )
    procedural: str = Field(description="Comma-separated action verbs and tool nouns.")
    policy: str = Field(description="Comma-separated rule nouns and constraints.")
    conceptual: str = Field(description="Comma-separated definition nouns.")
    target_commercial_product: str = Field(description="The exact Noun from the Product Matrix.")
    target_intent: List[str] = Field(description="List of 1-3 Verbs from the Intent Matrix.")
    verification_items: List[str] = Field(
        default_factory=list,
        description=(
            "Every specific, checkable claim this component's own wording implies that PRODUCT/INTENT "
            "classification does not need to resolve, but that real evidence must confirm before the request "
            "can be called fully answered -- e.g. a named brand or network ('the card network is Visa'), a "
            "premise or condition ('the replacement procedure covers cosmetic damage, not just physical "
            "damage'), a scope boundary ('the stated limit applies per application, not per customer'), or any "
            "other specific detail the customer's own wording turns on. Phrase each as a short, precise, "
            "self-contained sentence describing exactly what needs verifying -- never a bare category label. "
            "Never affects target_commercial_product/target_intent above -- you cannot know from here whether "
            "evidence will confirm any of these, since none has been retrieved yet. Empty when the component "
            "raises no such specific, checkable claim beyond its general topic."
        ),
    )
    # Extra Effort v2.1: computed ONCE here, at initial/fresh formulation
    # time, never re-derived at retry time -- this is a Matrix/context
    # question answerable from the customer's request alone ("what are my
    # top candidate classifications"), not one that benefits from having
    # already seen a failed retrieval attempt. Pre-computing it here is
    # what makes the backend's own later filter-widening step (Territory A)
    # fully deterministic again, with zero LLM call needed for it --
    # mirroring the same "pre-generate now, use later or not" pattern
    # already used for procedural/policy/conceptual above.
    alternate_product_reason: str = Field(
        default="",
        description=(
            "Reasoned through FIRST, after committing to target_commercial_product/target_intent above: could "
            "this exact same literal request ALSO plausibly be served by a genuinely different commercial "
            "product if this primary classification's own documentation turns out not to cover it? Strongly "
            "prefer a cross-cutting FUNCTIONAL product (Horizon_APP, Rewards_Program) over another genuine "
            "financial-instrument product (e.g. Credit_Card, Deposit_Account, Personal_Loan, Insurance, "
            "Mutual_Funds, Stocks, Debit_Card) as the alternate -- a request genuinely spanning two financial "
            "products should already be reflected in target_commercial_product or split into a separate "
            "component upstream, never deferred to this fallback list. Only name a second financial-instrument "
            "product here when you can state a real, specific reason the SAME literal request is ALSO plausibly "
            "filed under it -- never merely because it feels topically nearby. Name the concrete basis either "
            "way. This is a fallback candidate list only ever used later, if a search under the primary "
            "classification finds nothing -- leave this and alternate_products both empty when nothing is "
            "genuinely plausible; a forced alternate is worse than none."
        ),
    )
    alternate_products: List[str] = Field(
        default_factory=list,
        description="0-2 Matrix product nouns alternate_product_reason justifies, distinct from target_commercial_product. Empty when alternate_product_reason found nothing plausible.",
    )
    search_territory: str = Field(
        default="in_scope",
        description=(
            "Must be 'in_scope' for a normal SEARCH task. Must be 'adjacent_scope' only for a scope-expansion "
            "companion task constructed in RETRY MODE (Extra Effort v2.0) -- evidence found under an "
            "adjacent_scope task can never be graded SUFFICIENT downstream, only offered as related information, "
            "since by construction it is not the customer's literal request."
        ),
    )
    source_task_id: str = Field(
        default="",
        description="For a scope-expansion companion task only (search_territory=adjacent_scope): the exact original failed task_id this one was constructed alongside. Empty for a normal SEARCH task.",
    )

class DeclineTask(BaseModel):
    reason: str = Field(description="A short, factual statement for the decline.")
    category: str = Field(default="out_of_scope_product", description="Must be exactly 'out_of_scope_product' when the base product family has no Matrix entry at all, or 'individual_customers_only' when the base product exists but the component names a non-individual customer segment (business, corporate, institutional) rather than an individual retail customer.")

class RequestComponentResolution(BaseModel):
    component_id: str = Field(description="Stable identifier for one distinct component of the latest user input, e.g. component_1.")
    customer_component: str = Field(description="A concise faithful restatement of only that user-request component.")
    resolution: str = Field(description="Must be exactly SEARCH, CLARIFY, or DECLINE. Every component has exactly one resolution.")
    search_task: SearchTask | None = Field(default=None, description="Present only when resolution is SEARCH; otherwise null.")
    clarification: IntentClarificationTask | None = Field(default=None, description="Present only when resolution is CLARIFY; otherwise null.")
    decline: DeclineTask | None = Field(default=None, description="Present only when resolution is DECLINE; otherwise null.")

class QueryFormulation(BaseModel):
    components: List[RequestComponentResolution] = Field(description="An exhaustive, mutually exclusive partition of the pre-decomposed components supplied by triage.")

# 4b. EXTRA EFFORT v2.1 -- TERRITORY B COMPANION CONSTRUCTION (Node 02e,
# invoked only for a task that has ALSO exhausted Territory A's deterministic
# filter-widen attempt AND has no direct-adopt companion already covering it
# -- see node_03c's direct-adopt block, which handles the evidence-grounded
# case for free, with no LLM call at all. This call's only remaining job is
# the genuinely speculative case: reason a plausible, different topic when
# nothing at all was even tangentially flagged.
class TerritoryBCompanion(BaseModel):
    source_task_id: str = Field(description="The exact failed task_id (from Failed Tasks) this companion was constructed for. Must match one supplied failed task_id exactly.")
    search_task: SearchTask = Field(description="The new scope-expansion task itself. Its own search_territory must be 'adjacent_scope' and its own source_task_id must repeat the value above.")

class TerritoryBPlan(BaseModel):
    companions: List[TerritoryBCompanion] = Field(
        default_factory=list,
        description=(
            "At most one companion for each supplied failed task -- never a revision of the failed task itself; "
            "never more than one companion per failed task. Omit a failed task entirely (no companion) when no "
            "genuinely different product/intent with a concrete, statable basis exists for it -- a forced, remote "
            "guess is worse than none, since everything built on top of it inherits the same lack of grounding."
        ),
    )

# 5. USER JOURNEY PLANNER
class PresentationTask(BaseModel):
    task_id: str = Field(description="The exact task_id supplied to the planner.")
    display_mode: str = Field(description="Legacy presentation metadata.")
    evidence_parent_ids: List[str] = Field(description="Only approved parent IDs belonging to this task.")
    section_title: str = Field(description="Short user-facing title.")

class ChipOption(BaseModel):
    label: str = Field(description="Concise visible chip label of 2-6 words.")
    message: str = Field(description="Complete, unambiguous customer message submitted when the chip is clicked.")

class VisualHint(BaseModel):
    type: str = Field(description="Optional visual type: LIST, PROGRESS, or OPTIONS.")
    purpose: str = Field(description="Why this visual would materially help the customer understand the response.")

# 5. SUPERVISOR TOOL ROUTER (Node 03a)
class SupervisorRouting(BaseModel):
    active_tool_routes: List[str] = Field(description="Array of selected tools (e.g., 'n17_rag', 'n17_api').")

class ToolTaskInput(BaseModel):
    # Gemini's controlled generation (response_schema below) constrains the
    # model to ONLY the fields declared here -- a field missing from this
    # schema is not merely discouraged by the prompt, it is structurally
    # impossible for the model to emit at all, regardless of what the
    # prompt's rules say. This model used to declare only
    # customer_component, which meant a "service_request"/"deeplink"/"api"
    # tool_task could never carry its required id field no matter how the
    # prompt was worded -- the model was forced to echo customer_component
    # for every tool_name because that was the only field the decoder
    # could produce. One field per non-"rag" tool type, all optional since
    # any single tool_task only ever populates the one its tool_name needs.
    customer_component: str = Field(default="", description="Resolved customer component for a 'rag' tool_task's input. Never populated for service_request/deeplink/api.")
    service_request_id: str = Field(default="", description="Registered service_request id for a 'service_request' tool_task's input, copied verbatim from that tool's available_requests registry. Never populated for other tool_names.")
    destination_id: str = Field(default="", description="Registered deeplink destination id for a 'deeplink' tool_task's input, copied verbatim from that tool's available_destinations registry. Never populated for other tool_names.")
    field_id: str = Field(default="", description="Registered api field id for an 'api' tool_task's input, copied verbatim from that tool's available_fields registry. Never populated for other tool_names.")

class ToolCallingTask(BaseModel):
    subquery_id: str = Field(description="Exact financial subquery identifier.")
    tool_name: str = Field(description="Registered enabled tool name.")
    operation: str = Field(description="Registered operation for the tool.")
    input: ToolTaskInput = Field(default_factory=ToolTaskInput, description="Validated task input object.")

class ToolCallingPlan(BaseModel):
    tool_tasks: List[ToolCallingTask] = Field(
        default_factory=list,
        description="Additive registered tool tasks, each with subquery_id, tool_name, operation, and input."
    )

# 6. MEMORY SUMMARY VALIDATOR
class MemorySummaryEvaluation(BaseModel):
    is_grounded: bool = Field(description="True only when every retained claim is supported by the source text.")
    has_non_compliant_content: bool = Field(description="True if candidate contains unauthorized banking advice, transaction handling, or non-compliant claims.")
    has_clarification_dependent_content: bool = Field(description="True if a durable last-session or baseline candidate retains an unresolved, ambiguous, incomplete, or clarification-dependent issue that would require a new-session user response before it can be answered.")
    reason: str = Field(description="Brief validation reason or correction guidance.")

# 7. ESCALATION CONFIRMATION CASE SUMMARY
class EscalationCaseSummary(BaseModel):
    main_pursuit: str = Field(
        description="A concise factual description of the customer's main current-session banking pursuit."
    )
    unresolved_issues: List[str] = Field(
        default_factory=list,
        description="Current-session unresolved issues only. Empty when none are present."
    )

# =====================================================================

class ModelUnavailableError(BaseException):
    """The model cannot serve this call and neither another retry nor
    another key would change that (BUG-030, per the user): the provider
    reports it overloaded (503 UNAVAILABLE / "high demand"), or every key's
    daily quota is used up for both Gemini models. The turn is abandoned
    and the customer is told the model is unavailable and to try again
    later (see api_gateway.chat_stream) -- retrying is left to them.

    Deliberately a BaseException, not an Exception: every node wraps its
    LLM calls in `except Exception` and degrades to a fallback (triage
    reading the message as small talk, a check suppressing nothing, ...),
    which is exactly how an outage used to turn into a misleading answer
    instead of an honest one. This way no node can swallow it; it stops
    the graph and reaches the gateway. `detail` is the provider's exact
    error message, shown only with the developer reasoning display on.
    """

    def __init__(self, detail: str):
        super().__init__(detail)
        self.detail = detail


def _is_model_unavailable_error(error: Exception) -> bool:
    """503 UNAVAILABLE / "high demand" -- the provider saying the model is
    overloaded right now. Not retried: an immediate retry or another key
    meets the same overloaded model (BUG-030)."""
    if getattr(error, "code", None) == 503 or getattr(error, "status", None) == "UNAVAILABLE":
        return True
    # DeepSeek (OpenAI SDK errors carry status_code): 503 "server busy", and
    # 402 "Insufficient Balance" -- the paid account's equivalent of a used-up
    # quota; nothing inside the turn can restore either.
    if getattr(error, "status_code", None) in {402, 503}:
        return True
    message = str(error)
    return any(marker in message for marker in ("UNAVAILABLE", "high demand", " 503", "Service Unavailable", "Insufficient Balance"))


class EmptyModelOutputError(Exception):
    """A provider returned no content -- DeepSeek documents that JSON mode
    "has a probability of returning empty content". Retried once like any
    transient failure (see _is_transient_llm_error)."""


class SchemaValidationError(Exception):
    """A DeepSeek reply that does not satisfy its Pydantic schema even
    after one corrective re-ask (see _call_deepseek). Not retried further:
    the caller's own fallback runs, as for any other failed call."""


def _deepseek_strict_schema(schema_model: Any) -> dict:
    """Convert a Pydantic model into the JSON Schema DeepSeek's strict tool
    calling accepts (beta), per live probes (2026-09-25): every object lists
    all its properties as required with additionalProperties false; every
    `$ref` is expanded in place and `$defs` removed (a `$ref` inside
    `anyOf` is rejected with "missing field `type`", while the same object
    written inline is accepted); a single-value `const` becomes a one-value
    `enum`; `title` and `default` are dropped. Optional fields stay `anyOf`
    with null.

    The same probes showed strict mode does NOT enforce the schema on the
    output (an out-of-enum value and an extra property both came back), so
    it only shapes the reply -- _call_deepseek validates every reply
    against the Pydantic model itself.
    """
    schema = copy.deepcopy(schema_model.model_json_schema())
    definitions = schema.pop("$defs", {})

    def _walk(node: Any) -> Any:
        if isinstance(node, list):
            return [_walk(item) for item in node]
        if not isinstance(node, dict):
            return node
        if "$ref" in node:
            target = definitions[node["$ref"].split("/")[-1]]
            extra = {k: v for k, v in node.items() if k != "$ref"}
            return _walk({**copy.deepcopy(target), **extra})
        node = {k: _walk(v) for k, v in node.items() if k not in {"title", "default"}}
        if "const" in node:
            node["enum"] = [node.pop("const")]
        if node.get("type") == "object" or "properties" in node:
            properties = node.get("properties", {})
            node["type"] = "object"
            node["properties"] = properties
            node["required"] = list(properties)
            node["additionalProperties"] = False
        return node

    return _walk(schema)


def _missing_schema_fields(value: Any, schema: dict, path: str = "$") -> list[str]:
    """Paths of fields the strict schema requires but the reply left out.
    Pydantic validation alone accepts an omitted field that has a default,
    and strict mode does not enforce `required` -- seen live: the tool
    planner's reply dropped `subquery_domains`, its reasoning-first field.
    Gemini's response_schema always returns every field, so this keeps
    DeepSeek to the same contract -- except for nullable fields, where
    omission and null are equivalent."""
    if "anyOf" in schema:
        if value is None:
            return []
        branch = next((b for b in schema["anyOf"] if b.get("type") == "object"), None) if isinstance(value, dict) else None
        return _missing_schema_fields(value, branch, path) if branch else []
    if schema.get("type") == "object" and isinstance(value, dict):
        missing = []
        for key, sub in schema.get("properties", {}).items():
            if key not in value:
                # A nullable field left out means the same as null (seen
                # live: the tool planner omits the id fields that don't
                # apply to a task's tool); only a non-nullable one is a gap.
                if not any(branch.get("type") == "null" for branch in sub.get("anyOf", [])):
                    missing.append(f"{path}.{key}")
            else:
                missing.extend(_missing_schema_fields(value[key], sub, f"{path}.{key}"))
        return missing
    if schema.get("type") == "array" and isinstance(value, list) and isinstance(schema.get("items"), dict):
        return [m for i, item in enumerate(value) for m in _missing_schema_fields(item, schema["items"], f"{path}[{i}]")]
    return []


def _deepseek_user_id() -> str | None:
    """DeepSeek's `user_id` isolates KV-cache and content-safety scope per
    end user. A one-way hash of the session id -- never the customer id or
    any personal data (DeepSeek's own guidance)."""
    session_id = (request_context.get() or {}).get("session_id")
    if not session_id:
        return None
    return hashlib.sha256(str(session_id).encode()).hexdigest()[:32]


def _call_deepseek(prompt: str, model_id: str, active_schema: Any) -> str:
    """One DeepSeek chat completion. Thinking mode is explicitly disabled
    (it is on by default, and while on it ignores temperature -- every call
    would reason first, slower, billed as output, and non-deterministic).
    With a schema, output is enforced through strict tool calling: the
    schema becomes the only function and the model is required to call it,
    so the returned arguments are the JSON. Without one, JSON mode (every
    such prompt already says "json", which the mode requires). The SDK's own
    retries are disabled -- call_llm_router owns retry/timeout policy.
    """
    if not deepseek_api_key:
        raise ValueError("DEEPSEEK_API_KEY is not set.")
    extra_body = {"thinking": {"type": "disabled"}}
    user_id = _deepseek_user_id()
    if user_id:
        extra_body["user_id"] = user_id
    common = {
        "model": model_id,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.0,
        "max_tokens": _DEEPSEEK_MAX_TOKENS,
        "extra_body": extra_body,
    }
    if active_schema is not None:
        client = OpenAI(api_key=deepseek_api_key, base_url=_DEEPSEEK_BETA_BASE_URL, timeout=_LLM_CALL_TIMEOUT_SECONDS, max_retries=0)
        strict_schema = _deepseek_strict_schema(active_schema)
        tools = [{
            "type": "function",
            "function": {
                "name": "respond",
                "description": "Return the complete response in exactly this structure.",
                "parameters": strict_schema,
                "strict": True,
            },
        }]
        messages = common["messages"]
        for correction in range(2):
            completion = client.chat.completions.create(
                **{**common, "messages": messages},
                tools=tools,
                tool_choice={"type": "function", "function": {"name": "respond"}},
            )
            message = completion.choices[0].message
            result = message.tool_calls[0].function.arguments if message.tool_calls else ""
            if not result.strip():
                break
            # Strict mode does not guarantee the schema (see
            # _deepseek_strict_schema), so the Pydantic model -- the same
            # contract Gemini's response_schema enforces -- is checked here.
            # One corrective re-ask with the exact errors; a second failure
            # is left to the caller's own fallback.
            try:
                active_schema.model_validate_json(result)
                missing = _missing_schema_fields(json.loads(result), strict_schema)
                problem = f"Required fields missing: {', '.join(missing)}" if missing else ""
                logged = problem
            except ValidationError as error:
                problem = str(error)
                logged = error.errors(include_url=False, include_input=False)
            if not problem:
                break
            log_event(
                "llm.schema_invalid", level="WARNING", model_id=model_id,
                schema=getattr(active_schema, "__name__", ""), attempt=correction + 1, errors=logged,
            )
            if correction == 1:
                raise SchemaValidationError(f"{model_id} reply failed {getattr(active_schema, '__name__', 'schema')} validation twice: {problem}")
            messages = [{
                "role": "user",
                "content": prompt + "\n\nYOUR PREVIOUS REPLY WAS INVALID and has been discarded. "
                "It failed these schema checks:\n" + problem + "\nReturn a complete, corrected reply "
                "that includes every field and uses only the allowed values.",
            }]
    else:
        client = OpenAI(api_key=deepseek_api_key, base_url=_DEEPSEEK_BASE_URL, timeout=_LLM_CALL_TIMEOUT_SECONDS, max_retries=0)
        completion = client.chat.completions.create(**common, response_format={"type": "json_object"})
        result = completion.choices[0].message.content or ""
    if not result.strip():
        raise EmptyModelOutputError(f"{model_id} returned empty content (finish_reason={completion.choices[0].finish_reason}).")
    return result


def _is_transient_llm_error(error: Exception) -> bool:
    """True for an upstream availability hiccup (a 5xx, or a message
    unmistakably describing one) -- never for a 4xx (bad request, invalid
    schema, rate limit) or a local config error (missing API key), where
    retrying identically would either waste a call or make a rate limit
    worse. BUG-003: this is what call_llm_router uses to distinguish "the
    model itself never got a chance to respond" from "the model responded
    and genuinely found nothing" -- a caller (e.g. the Tool-Calling
    Planner) that only sees an empty result either way has no way to draw
    that distinction on its own.
    """
    if isinstance(error, (ServerError, EmptyModelOutputError)):
        return True
    if getattr(error, "status_code", None) in {500, 502, 504}:
        return True
    message = str(error)
    return any(marker in message for marker in ("UNAVAILABLE", "high demand", " 503", "Service Unavailable"))


def _call_llm_router_impl(prompt: str, model_id: str = MODEL_A_GENERATOR, response_schema_override: Any = None, _max_attempts: int = 2) -> str:
    """
    Centralized remote inference client manager.
    Supports Groq LPUs and dynamic Google GenAI Pydantic Structured Outputs.

    response_schema_override lets a caller supply a schema built at request
    time (e.g. one whose enum fields reflect exactly this turn's registered
    ids) instead of relying on the static, prompt-content-matched schema
    below -- the same mechanism that makes tool_name/operation impossible to
    get wrong under structured output, extended to an id field whose valid
    set is only known at call time, not at class-definition time.

    BUG-003 fix: a transient upstream failure (a real 503 "experiencing
    high demand," confirmed live) is retried once, here, centrally, before
    ever reaching a caller -- previously every caller's own except-block
    treated "the model call itself failed" identically to "the model
    responded with nothing," which is a materially different situation
    (e.g. the Tool-Calling Planner silently gave up on a subquery's tool
    dispatch entirely rather than trying again). Never retried for a 4xx
    (rate limit, bad schema) or a local config error -- see
    _is_transient_llm_error.

    BUG-030: a 503 UNAVAILABLE / "high demand" is no longer retried here --
    it raises ModelUnavailableError at once (the retry only ever met the
    same overloaded model). Other transient 5xx errors keep the one retry.
    """
    started_at = time.perf_counter()
    log_event("llm.request", model_id=model_id, raw_prompt=prompt)

    for attempt in range(1, _max_attempts + 1):
      try:
        # 🔍 DYNAMIC SCHEMA ROUTING: read the prompt to determine which node is
        # calling -- shared by every provider that enforces a schema (Gemini's
        # response_schema, DeepSeek's strict tool calling).
        active_schema = None
        if "TASK-LEVEL SUFFICIENCY AND EVIDENCE SELECTION" in prompt:
            active_schema = TaskSufficiencyEvaluation
        elif "ANSWER GROUNDEDNESS V2 AUDIT" in prompt or "NON-FINANCIAL CONVERSATION GROUNDEDNESS AUDIT" in prompt:
            active_schema = AnswerGroundednessEvaluation
        elif "APPROVED-KNOWLEDGE SAFETY:" in prompt:
            active_schema = SemanticEvaluation
        elif "TRIAGE A BANKING REQUEST" in prompt or "WHOLE-TURN FINANCIAL TRIAGE" in prompt:
            active_schema = TriageEvaluation
        elif "Act as a Decoupled Database Search Architect" in prompt:
            active_schema = QueryFormulation
        elif "INCREMENTAL TOOL-CALLING PLANNER" in prompt:
            active_schema = ToolCallingPlan
        elif "Determine which retrieval tools are necessary" in prompt:
            active_schema = SupervisorRouting
        elif "MEMORY SUMMARY SECURITY VALIDATION" in prompt:
            active_schema = MemorySummaryEvaluation
        elif "CURRENT SESSION ESCALATION CASE SUMMARY" in prompt:
            active_schema = EscalationCaseSummary

        if response_schema_override is not None:
            active_schema = response_schema_override

        # 🟡 BRANCH 1: Google Gemini Models (Bifurcation Judge & Classifiers)
        if "gemini" in model_id.lower():
            if not google_api_key:
                raise ValueError("GOOGLE_API_KEY is not set.")

            client = genai.Client(api_key=google_api_key)

            config_kwargs = {
                "response_mime_type": "application/json",
                "temperature": 0.0
            }
            if active_schema:
                config_kwargs["response_schema"] = active_schema

            response = client.models.generate_content(
                model=model_id,
                contents=prompt,
                config=types.GenerateContentConfig(**config_kwargs),
            )
            result = response.text

        # 🟣 BRANCH 1b: DeepSeek (paid; schema via strict tool calling)
        elif model_id.lower().startswith("deepseek"):
            result = _call_deepseek(prompt, model_id, active_schema)

        # 🟢 BRANCH 2: Groq Text Classifier Model (Prompt Guard)
        elif model_id == MODEL_D_PROMPT_GUARD:
            if not groq_api_key:
                raise ValueError("GROQ_API_KEY is not set.")
                
            client = Groq(api_key=groq_api_key)
            chat_completion = client.chat.completions.create(
                model=model_id,
                messages=[{"role": "user", "content": prompt}]
            )
            result = chat_completion.choices[0].message.content
            
        # 🔵 BRANCH 3: Groq Generative Models (JSON Mode)
        else:
            if not groq_api_key:
                raise ValueError("GROQ_API_KEY is not set.")
                
            client = Groq(api_key=groq_api_key)
            chat_completion = client.chat.completions.create(
                model=model_id,
                messages=[{"role": "user", "content": prompt}],
                response_format={"type": "json_object"},
                temperature=0.0, 
            )
            result = chat_completion.choices[0].message.content

        log_event(
            "llm.response",
            model_id=model_id,
            raw_response=result,
            elapsed_ms=round((time.perf_counter() - started_at) * 1000, 2),
        )
        return result

      except Exception as e:
        if _is_model_unavailable_error(e):
            print(f"🚨 [LLM Service]: model unavailable, abandoning the turn: {e}")
            log_event(
                "llm.unavailable",
                level="ERROR",
                model_id=model_id,
                error=str(e),
                elapsed_ms=round((time.perf_counter() - started_at) * 1000, 2),
            )
            raise ModelUnavailableError(str(e)) from e
        transient = _is_transient_llm_error(e) or (
            # DeepSeek's 429 is its per-account concurrency cap -- momentary,
            # unlike a Gemini quota -- so it gets the one retry.
            model_id.lower().startswith("deepseek") and getattr(e, "status_code", None) == 429
        )
        if transient and attempt < _max_attempts:
            print(f"⏳ [LLM Service Transient Error, retrying]: {e}")
            log_event(
                "llm.retry",
                level="WARNING",
                model_id=model_id,
                attempt=attempt,
                error=str(e),
            )
            time.sleep(2)
            continue
        print(f"🚨 [LLM Service Error]: {e}")
        log_event(
            "llm.error",
            level="ERROR",
            model_id=model_id,
            raw_prompt=prompt,
            error=str(e),
            transient=transient,
            elapsed_ms=round((time.perf_counter() - started_at) * 1000, 2),
        )
        raise e


def call_llm_router(prompt: str, model_id: str = MODEL_A_GENERATOR, response_schema_override: Any = None, _max_attempts: int = 2) -> str:
    """Public entrypoint: wraps _call_llm_router_impl with the two live-
    traffic resilience layers that don't belong in that function's own
    per-attempt retry loop (which is specifically for a transient upstream
    5xx, see _is_transient_llm_error):

    - Key rotation: a 429/RESOURCE_EXHAUSTED (from either key's daily quota
      or a shorter per-minute limit) rotates _google_key_pool_index and
      retries immediately with the next key -- no waiting, since a live
      request can't be kept waiting the way an unattended batch job can.
      Bounded at len(_google_key_pool) total attempts; once every key has
      been tried for both models (see Cross-model fallback below), this
      raises ModelUnavailableError (BUG-030) -- no node fallback runs; the
      turn ends with the "model unavailable" message.
    - Hang guard: the underlying client library sets no read timeout, so a
      dropped/half-closed connection can block _call_llm_router_impl
      forever with no exception ever raised to retry from (confirmed live
      in the research harness -- the same client is used here). Each
      attempt runs in a worker thread with a _LLM_CALL_TIMEOUT_SECONDS cap;
      a stuck attempt is abandoned (its thread leaks quietly, since Python
      cannot forcibly cancel a blocked thread) and a fresh attempt is
      retried, up to the same attempt budget as key rotation.

    contextvars.copy_context() matters here for the same reason it did in
    the research harness: request_context (session_id, user_id --
    everything log_event attaches to a live request's own log lines) is
    thread-local and does NOT cross into the worker thread on its own;
    without re-applying the calling context inside that thread, every
    llm.request/llm.response log line from a live request would silently
    lose its session_id.

    A fresh Context is captured on every attempt inside the loop, not once
    before it: reusing one Context object across retries raises "cannot
    enter context: already entered" the moment a retry's ctx.run(...) is
    invoked while an earlier, timed-out-but-still-running attempt's
    ctx.run(...) on that same object hasn't finished yet (Python cannot
    forcibly cancel a blocked thread, so a hung attempt keeps running for
    as long as its own network call takes, which can easily outlast
    _LLM_CALL_TIMEOUT_SECONDS) -- confirmed live. copy_context() is cheap
    and request_context doesn't change mid-call, so this has no downside.

    Cross-model fallback: the daily quota is tracked per (key, model) pair,
    not just per key -- the 429 message itself names the specific model
    ("GenerateRequestsPerDayPerProjectPerModel-FreeTier"). So exhausting
    every key for gemini-3.5-flash-lite says nothing about
    gemini-3.1-flash-lite's own quota on those same keys, and vice versa.
    Once every key is exhausted for the model actually requested, this
    switches to _GEMINI_MODEL_FALLBACK's other Gemini model and restarts
    the key cycle from key #1 for it, using the identical prompt and
    schema (the API only cares which model to route to, not which of the
    two callers originally asked for) -- only after BOTH models have
    exhausted every key does this finally give up. A non-Gemini model_id
    (e.g. the Groq prompt guard) has no fallback and behaves exactly as
    before.
    """
    current_model_id = model_id
    models_to_try = [model_id] + ([_GEMINI_MODEL_FALLBACK[model_id]] if model_id in _GEMINI_MODEL_FALLBACK else [])
    last_error: Exception | None = None
    for model_index, current_model_id in enumerate(models_to_try):
        if model_index > 0:
            _reset_google_key_pool()
            log_event("llm.model_fallback", level="WARNING", from_model=models_to_try[model_index - 1], to_model=current_model_id)
        max_attempts = max(len(_google_key_pool), 1)
        for attempt in range(1, max_attempts + 1):
            observed_index = _google_key_pool_index
            try:
                ctx = contextvars.copy_context()
                future = _LLM_CALL_EXECUTOR.submit(ctx.run, _call_llm_router_impl, prompt, current_model_id, response_schema_override, _max_attempts)
                return future.result(timeout=_LLM_CALL_TIMEOUT_SECONDS)
            except concurrent.futures.TimeoutError:
                last_error = TimeoutError(f"{current_model_id} call did not respond within {_LLM_CALL_TIMEOUT_SECONDS}s (likely a dead connection).")
                log_event("llm.call_timeout", level="WARNING", model_id=current_model_id, timeout_seconds=_LLM_CALL_TIMEOUT_SECONDS, attempt=attempt)
            except Exception as error:
                # Key rotation is a Gemini free-tier mechanism; a DeepSeek 429
                # is its concurrency cap, handled as transient in
                # _call_llm_router_impl.
                if _is_rate_limited_error(error) and _google_key_pool and "gemini" in current_model_id.lower():
                    last_error = error
                    if _rotate_google_api_key(observed_index):
                        log_event("llm.key_rotated", level="WARNING", model_id=current_model_id, from_index=observed_index, attempt=attempt)
                        continue
                    break
                raise
    # Every key's quota is used up for both models: unavailable until the
    # quota resets, which no retry inside this turn can change (BUG-030).
    if last_error is not None and _is_rate_limited_error(last_error):
        log_event("llm.unavailable", level="ERROR", model_id=model_id, error=str(last_error), reason="quota_exhausted_all_keys")
        raise ModelUnavailableError(str(last_error)) from last_error
    raise last_error


# 🔗 BACKWARD COMPATIBILITY ALIAS
call_groq_lpu = call_llm_router


def parse_json_robustly(raw_text: str) -> dict:
    """Parse an LLM's JSON response, tolerating a ```json fenced code block.

    Also tolerates the whole response being wrapped in a single-element
    list -- confirmed live that a structured-output call can occasionally
    return `[{...}]` instead of the expected bare `{...}` for the exact
    same schema/prompt shape that normally comes back unwrapped. Every
    caller immediately does `.get(...)` on this function's return value,
    so an unwrapped list crashes with "'list' object has no attribute
    'get'" with no indication of what actually went wrong. Only the
    single-dict-element case is unwrapped; any other list shape (empty,
    multiple elements, non-dict elements) is left as-is and still surfaces
    as a clear downstream error rather than being guessed at further.
    """
    clean_text = raw_text.strip()
    if clean_text.startswith("```json"): clean_text = clean_text[7:]
    elif clean_text.startswith("```"): clean_text = clean_text[3:]
    if clean_text.endswith("```"): clean_text = clean_text[:-3]
    try:
        parsed = json.loads(clean_text.strip())
    except Exception as e:
        print(f"🚨 [JSON PARSE ERROR]: {e} \nRaw text was: {raw_text}")
        raise e
    if isinstance(parsed, list) and len(parsed) == 1 and isinstance(parsed[0], dict):
        return parsed[0]
    return parsed
