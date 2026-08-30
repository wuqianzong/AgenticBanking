import json
import os
import time
from typing import Any, List
from groq import Groq
from google import genai
from google.genai import types
from pydantic import BaseModel, Field
from backend.app.core.system_logger import log_event

# --- 🧠 MULTI-MODEL ARCHITECTURE REGISTRY ---
#model_A="openai/gpt-oss-120b"  # For response generation
model_A="gemini-3.5-flash-lite"  # For response generation
model_B="gemini-3.1-flash-lite"  # For sufficiency, gourndedness, and compliance audits
model_C="gemini-3.1-flash-lite"  # For triage, decomposition, and journey planning
MODEL_A_GENERATOR = model_A       # Groq: Conversational Engine
MODEL_B_AUDITOR = model_B            # Google: Safety & Sufficiency Judge
MODEL_C_PLANNER = model_C            # Google: Resolution Planner & Tool Calling Generator
MODEL_D_PROMPT_GUARD = "meta-llama/llama-prompt-guard-2-86m"  # Groq: Cyber Security Pre-check
MODEL_E_TRIAGE = model_C             # Google: Journey Classifier
# --------------------------------------------

groq_api_key = os.environ.get("GROQ_API_KEY")
google_api_key = os.environ.get("GOOGLE_API_KEY")

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
    status: str = Field(description="Must be SUFFICIENT, PARTIALLY_SUFFICIENT, or INSUFFICIENT.")
    approved_parent_ids: List[str] = Field(description="Only supplied parent IDs that provide material grounded information for this exact task.")
    reason: str = Field(description="Brief explanation of how completely the candidate evidence answers the task.")
    evidence_delta: List[RetryEvidenceDeltaItem] = Field(
        default_factory=list,
        description=(
            "For PARTIALLY_SUFFICIENT or INSUFFICIENT only: newly observed, materially task-relevant facts from "
            "supplied candidate parents. Empty when no such fact exists."
        ),
    )
    likely_ambiguous_request: bool = Field(
        default=False,
        description=(
            "For INSUFFICIENT only: true when the task genuinely failed because the resolved "
            "customer_component itself is under-specified or could reasonably mean one of several different "
            "things (e.g. 'the fee' without naming which fee, 'the loan' without naming which loan type) -- the "
            "retrieval failed because the request needs more detail, not because the topic is simply "
            "undocumented. False when the request was already specific and clear but the documentation "
            "genuinely does not cover it. Always false for SUFFICIENT or PARTIALLY_SUFFICIENT."
        ),
    )
    unresolved_term: str = Field(
        default="",
        description=(
            "Non-empty only when this task's own evidence review revealed something worth asking the customer "
            "about directly -- either (a) status is PARTIALLY_SUFFICIENT and one meaningful part of the request "
            "has no supporting evidence at all, and an additional customer detail could plausibly unlock a "
            "different, more complete answer for that specific part; or (b) status is INSUFFICIENT and "
            "likely_ambiguous_request is true. A concise phrase naming exactly the ambiguous or unresolved part "
            "-- never the whole customer_component verbatim unless the whole thing is what's unresolved. Leave "
            "empty whenever asking would not genuinely help (e.g. a PARTIALLY_SUFFICIENT gap that's simply "
            "undocumented, not disambiguatable by any customer detail) -- a clarifying question is not required "
            "just because a task didn't fully resolve."
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
    customer_component: str = Field(description="Standalone, self-contained restatement of only that component, with pronouns/ellipsis resolved using conversation context -- optimized for retrieval when resolution is TOOL_ELIGIBLE, or for composing a natural reply when resolution is NON_FINANCIAL.")
    resolution: str = Field(description="Must be exactly TOOL_ELIGIBLE (a real banking need proceeding to retrieval/tools), NON_FINANCIAL (tone-only content with no ask that could ever become a banking need -- a greeting, thanks, feeling, or wording clearly and only about something outside what this assistant addresses), or CLARIFICATION (genuinely undecidable between TOOL_ELIGIBLE and NON_FINANCIAL for this specific component -- never used merely because the component's own content is vague or ambiguous in what it's asking, which is a downstream node's job once the component is treated as TOOL_ELIGIBLE).")
    clarification: IntentClarificationTask | None = Field(default=None, description="Present only when resolution is CLARIFICATION; otherwise null.")

class TriageEvaluation(BaseModel):
    triage_status: str = Field(description="Must be NON_COMPLIANT, ESCALATION, or DECOMPOSED.")
    components: List[TriageComponent] = Field(default_factory=list, description="For DECOMPOSED only: an exhaustive partition of the resolved TRUE REQUEST into one or more standalone components, each tagged with its own resolution. Empty for NON_COMPLIANT/ESCALATION.")
    repetitive_issue_detected: bool = Field(default=False, description="True only when the Trusted Conversation Context shows the customer repeatedly raising substantially the same underlying banking need or issue across multiple recent turns, especially if it still appears unresolved. Judge by meaning, not wording. Independent of triage_status.")

# 4. 2D MATRIX QUERY FORMULATOR (Node 02e) -> NESTED SCHEMA
class SearchTask(BaseModel):
    task_id: str = Field(description="Stable task identifier, e.g. task_1. Preserve it during retries.")
    procedural: str = Field(description="Comma-separated action verbs and tool nouns.")
    policy: str = Field(description="Comma-separated rule nouns and constraints.")
    conceptual: str = Field(description="Comma-separated definition nouns.")
    target_commercial_product: str = Field(description="The exact Noun from the Product Matrix.")
    target_intent: List[str] = Field(description="List of 1-3 Verbs from the Intent Matrix.")

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

def call_llm_router(prompt: str, model_id: str = MODEL_A_GENERATOR, response_schema_override: Any = None) -> str:
    """
    Centralized remote inference client manager.
    Supports Groq LPUs and dynamic Google GenAI Pydantic Structured Outputs.

    response_schema_override lets a caller supply a schema built at request
    time (e.g. one whose enum fields reflect exactly this turn's registered
    ids) instead of relying on the static, prompt-content-matched schema
    below -- the same mechanism that makes tool_name/operation impossible to
    get wrong under structured output, extended to an id field whose valid
    set is only known at call time, not at class-definition time.
    """
    started_at = time.perf_counter()
    log_event("llm.request", model_id=model_id, raw_prompt=prompt)

    try:
        # 🟡 BRANCH 1: Google Gemini Models (Bifurcation Judge & Classifiers)
        if "gemini" in model_id.lower():
            if not google_api_key:
                raise ValueError("GOOGLE_API_KEY is not set.")
            
            client = genai.Client(api_key=google_api_key)
            
            # 🔍 DYNAMIC SCHEMA ROUTING: Read prompt to determine which Node is calling
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
        print(f"🚨 [LLM Service Error]: {e}")
        log_event(
            "llm.error",
            level="ERROR",
            model_id=model_id,
            raw_prompt=prompt,
            error=str(e),
            elapsed_ms=round((time.perf_counter() - started_at) * 1000, 2),
        )
        raise e

# 🔗 BACKWARD COMPATIBILITY ALIAS
call_groq_lpu = call_llm_router


def parse_json_robustly(raw_text: str) -> dict:
    """Parse an LLM's JSON response, tolerating a ```json fenced code block."""
    clean_text = raw_text.strip()
    if clean_text.startswith("```json"): clean_text = clean_text[7:]
    elif clean_text.startswith("```"): clean_text = clean_text[3:]
    if clean_text.endswith("```"): clean_text = clean_text[:-3]
    try: return json.loads(clean_text.strip())
    except Exception as e:
        print(f"🚨 [JSON PARSE ERROR]: {e} \nRaw text was: {raw_text}")
        raise e
