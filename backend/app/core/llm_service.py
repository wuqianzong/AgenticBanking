import os
import time
from typing import List
from groq import Groq
from google import genai
from google.genai import types
from pydantic import BaseModel, Field
from backend.app.core.system_logger import log_event

# --- 🧠 MULTI-MODEL ARCHITECTURE REGISTRY ---
model_A="openai/gpt-oss-120b"  # For response generation
model_B="gemini-3.5-flash-lite"  # For sufficiency, gourndedness, and compliance audits
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

class TaskSufficiencyEvaluation(BaseModel):
    task_results: List[TaskSufficiencyItem] = Field(description="One sufficiency result for every supplied task.")

# 3. USER JOURNEY CLASSIFIER / TRIAGE (Node 02d) -> DECOMPOSITION + COMPLIANCE/RELEVANCE SCREEN
class TriageComponentResolution(BaseModel):
    component_id: str = Field(description="Stable identifier for one distinct component of the latest user input, e.g. component_1.")
    customer_component: str = Field(description="A standalone, self-contained restatement of only that component, with pronouns/ellipsis resolved using conversation context.")
    resolution: str = Field(description="Must be exactly VALID or DECLINE.")
    decline_reason_category: str = Field(default="", description="Present only when resolution is DECLINE. Must be exactly non_compliant or non_financial, otherwise an empty string.")
    decline_detail: str = Field(default="", description="Present only when resolution is DECLINE. A brief factual reason, otherwise an empty string.")

class FinancialSubquery(BaseModel):
    subquery_id: str = Field(description="Stable identifier for one financial subquery.")
    customer_component: str = Field(description="Standalone compliant banking request component.")

class TriageEvaluation(BaseModel):
    triage_status: str = Field(description="Must be NON_COMPLIANT, ESCALATION, VALID_REQUEST, or NON_FINANCIAL.")
    financial_subqueries: List[FinancialSubquery] = Field(default_factory=list, description="For VALID_REQUEST only: financial banking components. Empty otherwise.")
    has_non_financial_content: bool = Field(default=False, description="True only when a VALID_REQUEST also contained unrelated wording that was omitted from financial_subqueries.")
    non_financial_context: List[str] = Field(default_factory=list, description="For VALID_REQUEST only: short internal attention cues for non-factual conversational asides omitted from financial_subqueries. Empty otherwise; never customer-facing source text.")

# 4. 2D MATRIX QUERY FORMULATOR (Node 02e) -> NESTED SCHEMA
class SearchTask(BaseModel):
    task_id: str = Field(description="Stable task identifier, e.g. task_1. Preserve it during retries.")
    procedural: str = Field(description="Comma-separated action verbs and tool nouns.")
    policy: str = Field(description="Comma-separated rule nouns and constraints.")
    conceptual: str = Field(description="Comma-separated definition nouns.")
    target_commercial_product: str = Field(description="The exact Noun from the Product Matrix.")
    target_intent: List[str] = Field(description="List of 1-3 Verbs from the Intent Matrix.")

class IntentClarificationTask(BaseModel):
    clarification_id: str = Field(description="Stable identifier, e.g. intent_clarification_1.")
    unresolved_request: str = Field(description="The exact ambiguous product, service, or request component from the user's wording.")
    reason: str = Field(description="A concise internal explanation of why a valid search task cannot be formed without clarification.")

class DeclineTask(BaseModel):
    reason: str = Field(description="A short, factual statement naming the product or service Horizon does not offer.")

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

class ToolGap(BaseModel):
    subquery_id: str = Field(description="Exact supplied financial subquery identifier.")
    tool_name: str = Field(description="Registered tool name that could address the gap.")
    operation: str = Field(description="Registered operation for the tool.")
    unresolved_need: str = Field(description="Internal non-factual capability gap.")

class UserJourneyPlan(BaseModel):
    resolution_status: str = Field(default="RESOLVED", description="Must be RESOLVED, NEEDS_TOOLS, or EXHAUSTED.")
    tool_gaps: List[ToolGap] = Field(default_factory=list, description="Internal capability gaps only.")
    validated_task_ids: List[str] = Field(
        default_factory=list,
        description="Only supplied answerable task IDs whose validated results contribute to global resolution."
    )
    unresolved_topics: List[str] = Field(
        default_factory=list,
        description="Concise unresolved customer topics used only as internal scope anchors, never as facts or response wording."
    )

# 5. SUPERVISOR TOOL ROUTER (Node 03a)
class SupervisorRouting(BaseModel):
    active_tool_routes: List[str] = Field(description="Array of selected tools (e.g., 'n17_rag', 'n17_api').")

class ToolTaskInput(BaseModel):
    customer_component: str = Field(default="", description="Resolved customer component for the task.")

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

def call_llm_router(prompt: str, model_id: str = MODEL_A_GENERATOR) -> str:
    """
    Centralized remote inference client manager.
    Supports Groq LPUs and dynamic Google GenAI Pydantic Structured Outputs.
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
            elif ("GLOBAL RESPONSE SUFFICIENCY" in prompt or "FLEXIBLE USER EXPERIENCE PLANNER" in prompt
                  or "USER JOURNEY V2.0 PRESENTATION PLANNER" in prompt):
                active_schema = UserJourneyPlan
            elif "INCREMENTAL TOOL-CALLING PLANNER" in prompt:
                active_schema = ToolCallingPlan
            elif "Determine which retrieval tools are necessary" in prompt:
                active_schema = SupervisorRouting
            elif "MEMORY SUMMARY SECURITY VALIDATION" in prompt:
                active_schema = MemorySummaryEvaluation
            elif "CURRENT SESSION ESCALATION CASE SUMMARY" in prompt:
                active_schema = EscalationCaseSummary
                
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
