import os
import re
import threading
import time
from collections import defaultdict, deque
from typing import List
from groq import Groq
from google import genai
from google.genai import types
from pydantic import BaseModel, Field
from backend.app.core.system_logger import log_event, request_context

# --- 🧠 MULTI-MODEL ARCHITECTURE REGISTRY ---
MODEL_A_GENERATOR = "openai/gpt-oss-120b"       # Groq: Conversational Engine
#MODEL_A_GENERATOR = "gemini-3.1-flash-lite"
MODEL_B_AUDITOR = "gemini-3.5-flash-lite"            # Google: Safety & Sufficiency Judge
MODEL_C_PLANNER = "gemini-3.5-flash-lite"            # Google: Resolution Planner & Tool Calling Generator
MODEL_D_PROMPT_GUARD = "meta-llama/llama-prompt-guard-2-86m"  # Groq: Cyber Security Pre-check
MODEL_E_TRIAGE = "gemini-3.5-flash-lite"             # Google: Journey Classifier
# --------------------------------------------

# Retrieval evaluation can spread independent Gemini calls across these two
# compatible Flash Lite versions.  Every event records the effective model so
# analysis can stratify by judge version if needed.
_GEMINI_ROTATION_MODELS = ("gemini-3.5-flash-lite", "gemini-3.1-flash-lite")
EVALUATION_GEMINI_ROTATION_TAG = "retrieval_cosine_eval"

groq_api_key = os.environ.get("GROQ_API_KEY")
google_api_key = os.environ.get("GOOGLE_API_KEY", "AQ.Ab8RN6JT4AL6142h9RH5kjIvS9cxNdzmty-akxr7PmfUV1U4yg")
data_automation_google_api_key = os.environ.get("DATA_AUTOMATION_GOOGLE_API_KEY")

# The Gemini free tier accepts 15 generation requests per minute.  This is a
# process-wide guard: a turn can make several model calls and multiple turns
# may overlap, so pacing only the HTTP client would not protect the quota.
_RATE_LIMIT_WINDOW_SECONDS = 60.0
_GEMINI_REQUESTS_PER_WINDOW = 15
_RATE_LIMIT_LOCK = threading.Lock()
_MODEL_REQUEST_TIMES: dict[str, deque[float]] = defaultdict(deque)
_MODEL_COOLDOWN_UNTIL: dict[str, float] = defaultdict(float)
_GEMINI_ROTATION_INDEX = 0
_RETRY_DELAY_PATTERNS = (
    re.compile(r"(?:retry|try again|wait).*?([0-9]+)m([0-9]+(?:\.[0-9]+)?)s", re.IGNORECASE),
    re.compile(r"retryDelay[\"':\s]+([0-9]+(?:\.[0-9]+)?)s", re.IGNORECASE),
    re.compile(r"(?:retry|wait) (?:in |after )?([0-9]+(?:\.[0-9]+)?)s", re.IGNORECASE),
)


def _is_rate_limited(error: Exception) -> bool:
    message = str(error).lower()
    return "resource_exhausted" in message or "rate limit" in message or "status 429" in message


def _retry_delay_seconds(error: Exception) -> float:
    """Use the provider's RetryInfo delay, with a small safe fallback."""
    message = str(error)
    for pattern in _RETRY_DELAY_PATTERNS:
        match = pattern.search(message)
        if match:
            if len(match.groups()) == 2:
                return (float(match.group(1)) * 60) + float(match.group(2)) + 0.5
            return float(match.group(1)) + 0.5
    return 5.0


def _wait_for_model_slot(model_id: str) -> None:
    """Pace only explicitly tagged evaluation traffic."""
    if (
        "gemini" not in model_id.lower()
        or request_context.get().get("evaluation_tag") != EVALUATION_GEMINI_ROTATION_TAG
    ):
        # Groq only generates presentation/safety plumbing in this evaluation.
        # It is deliberately not a collection gate; retrieval and Gemini
        # sufficiency evidence must remain observable even if its quota is out.
        return

    while True:
        with _RATE_LIMIT_LOCK:
            now = time.monotonic()
            recent = _MODEL_REQUEST_TIMES[model_id]
            while recent and now - recent[0] >= _RATE_LIMIT_WINDOW_SECONDS:
                recent.popleft()
            cooldown_wait = _MODEL_COOLDOWN_UNTIL[model_id] - now
            quota_wait = (_RATE_LIMIT_WINDOW_SECONDS - (now - recent[0])) if len(recent) >= _GEMINI_REQUESTS_PER_WINDOW else 0.0
            wait_seconds = max(0.0, cooldown_wait, quota_wait)
            if wait_seconds <= 0:
                recent.append(now)
                return
        log_event("llm.rate_limit.wait", model_id=model_id, reason="gemini_request_pacing", wait_seconds=round(wait_seconds, 2))
        time.sleep(wait_seconds)


def _set_provider_cooldown(model_id: str, delay_seconds: float) -> None:
    with _RATE_LIMIT_LOCK:
        _MODEL_COOLDOWN_UNTIL[model_id] = max(
            _MODEL_COOLDOWN_UNTIL[model_id], time.monotonic() + delay_seconds
        )


def _select_effective_model(model_id: str) -> str:
    """Rotate Gemini only for an explicitly tagged retrieval evaluation."""
    global _GEMINI_ROTATION_INDEX
    if (
        "gemini" not in model_id.lower()
        or request_context.get().get("evaluation_tag") != EVALUATION_GEMINI_ROTATION_TAG
    ):
        return model_id
    with _RATE_LIMIT_LOCK:
        now = time.monotonic()
        # Preserve round-robin when both models are healthy. If a provider has
        # supplied a cooldown, bypass that model rather than spending its wait
        # time before the retry can reach the other model.
        for offset in range(len(_GEMINI_ROTATION_MODELS)):
            index = (_GEMINI_ROTATION_INDEX + offset) % len(_GEMINI_ROTATION_MODELS)
            candidate = _GEMINI_ROTATION_MODELS[index]
            if _MODEL_COOLDOWN_UNTIL[candidate] <= now:
                _GEMINI_ROTATION_INDEX = (index + 1) % len(_GEMINI_ROTATION_MODELS)
                return candidate

        # Both are cooling down; let the regular wait logic sleep only until
        # the earliest available model, rather than bypassing a provider limit.
        index = min(
            range(len(_GEMINI_ROTATION_MODELS)),
            key=lambda candidate_index: _MODEL_COOLDOWN_UNTIL[_GEMINI_ROTATION_MODELS[candidate_index]],
        )
        _GEMINI_ROTATION_INDEX = (index + 1) % len(_GEMINI_ROTATION_MODELS)
    return _GEMINI_ROTATION_MODELS[index]


def _google_api_key_for_request() -> tuple[str | None, str]:
    """Keep the dedicated evaluation credential isolated from production."""
    if (
        request_context.get().get("evaluation_tag") == EVALUATION_GEMINI_ROTATION_TAG
        and data_automation_google_api_key
    ):
        return data_automation_google_api_key, "data_automation"
    return google_api_key, "default"

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

# 2. SUFFICIENCY AUDITOR (Node 03c)
class TaskSufficiencyItem(BaseModel):
    task_id: str = Field(description="The exact task_id supplied in the task input.")
    status: str = Field(description="Must be SUFFICIENT, RETRY, or UNAVAILABLE.")
    approved_parent_ids: List[str] = Field(description="Only parent IDs supplied for this exact task and directly relevant to it.")
    reason: str = Field(description="Brief evidence or insufficiency reason.")
    search_feedback: str = Field(description="Concise search refinement for RETRY, otherwise an empty string.")

class TaskSufficiencyEvaluation(BaseModel):
    task_results: List[TaskSufficiencyItem] = Field(description="One sufficiency result for every supplied task.")

# 3. USER JOURNEY CLASSIFIER / TRIAGE (Node 02d) -> DECOMPOSITION + COMPLIANCE/RELEVANCE SCREEN
class TriageComponentResolution(BaseModel):
    component_id: str = Field(description="Stable identifier for one distinct component of the latest user input, e.g. component_1.")
    customer_component: str = Field(description="A standalone, self-contained restatement of only that component, with pronouns/ellipsis resolved using conversation context.")
    resolution: str = Field(description="Must be exactly VALID or DECLINE.")
    decline_reason_category: str = Field(default="", description="Present only when resolution is DECLINE. Must be exactly non_compliant or non_financial, otherwise an empty string.")
    decline_detail: str = Field(default="", description="Present only when resolution is DECLINE. A brief factual reason, otherwise an empty string.")

class TriageEvaluation(BaseModel):
    triage_status: str = Field(description="Must be ESCALATION, CLARIFICATION, or VALID_REQUEST.")
    components: List[TriageComponentResolution] = Field(default_factory=list, description="Populated only when triage_status is VALID_REQUEST: an exhaustive decomposition of the latest user input into VALID or DECLINE components.")

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

class ResponseBlockPlan(BaseModel):
    task_id: str = Field(description="The exact task_id supplied to the planner.")
    presentation_type: str = Field(description="Must be exactly TEXT, PROGRESS, LIST, or ESCALATION.")
    evidence_parent_ids: List[str] = Field(description="Only approved parent IDs belonging to this task.")
    section_title: str = Field(description="Short user-facing title for this block.")
    steps: List[str] = Field(description="Grounded short step titles for PROGRESS only; otherwise empty.")
    list_title: str = Field(description="Concise customer-facing title for LIST only; otherwise empty.")
    options: List[ChipOption] = Field(description="At most two grounded answer-related chips for this block.")
    reason: str = Field(description="Brief explanation of this presentation choice.")

class UserJourneyPlan(BaseModel):
    response_blocks: List[ResponseBlockPlan] = Field(description="Ordered presentation plans, one for every answerable task.")

# 5. SUPERVISOR TOOL ROUTER (Node 03a)
class SupervisorRouting(BaseModel):
    active_tool_routes: List[str] = Field(description="Array of selected tools (e.g., 'n17_rag', 'n17_api').")

# 6. MEMORY SUMMARY VALIDATOR
class MemorySummaryEvaluation(BaseModel):
    is_grounded: bool = Field(description="True only when every retained claim is supported by the source text.")
    has_non_compliant_content: bool = Field(description="True if candidate contains unauthorized banking advice, transaction handling, or non-compliant claims.")
    has_clarification_dependent_content: bool = Field(description="True if a durable last-session or baseline candidate retains an unresolved, ambiguous, incomplete, or clarification-dependent issue that would require a new-session user response before it can be answered.")
    reason: str = Field(description="Brief validation reason or correction guidance.")

# =====================================================================

def call_llm_router(prompt: str, model_id: str = MODEL_A_GENERATOR) -> str:
    """
    Centralized remote inference client manager.
    Supports Groq LPUs and dynamic Google GenAI Pydantic Structured Outputs.
    """
    started_at = time.perf_counter()
    # Only retry explicit provider throttling.  Other failures must remain
    # visible to the workflow instead of being masked as a slow answer.
    for attempt in range(1, 4):
        effective_model_id = _select_effective_model(model_id)
        credential_route = (
            _google_api_key_for_request()[1]
            if "gemini" in effective_model_id.lower()
            else "default"
        )
        _wait_for_model_slot(effective_model_id)
        log_event(
            "llm.request",
            model_id=effective_model_id,
            requested_model_id=model_id,
            credential_route=credential_route,
            raw_prompt=prompt,
            attempt=attempt,
        )
        try:
            # 🟡 BRANCH 1: Google Gemini Models (Bifurcation Judge & Classifiers)
            if "gemini" in effective_model_id.lower():
                active_google_api_key, credential_route = _google_api_key_for_request()
                if not active_google_api_key:
                    raise ValueError("GOOGLE_API_KEY is not set.")
            
                client = genai.Client(api_key=active_google_api_key)
            
                # 🔍 DYNAMIC SCHEMA ROUTING: Read prompt to determine which Node is calling
                active_schema = None
                if "TASK-LEVEL SUFFICIENCY AND EVIDENCE SELECTION" in prompt:
                    active_schema = TaskSufficiencyEvaluation
                elif "APPROVED-KNOWLEDGE SAFETY:" in prompt:
                    active_schema = SemanticEvaluation
                elif "TRIAGE A BANKING REQUEST" in prompt:
                    active_schema = TriageEvaluation
                elif "Act as a Decoupled Database Search Architect" in prompt:
                    active_schema = QueryFormulation
                elif "USER JOURNEY V2.0 PRESENTATION PLANNER" in prompt:
                    active_schema = UserJourneyPlan
                elif "Determine which retrieval tools are necessary" in prompt:
                    active_schema = SupervisorRouting
                elif "MEMORY SUMMARY SECURITY VALIDATION" in prompt:
                    active_schema = MemorySummaryEvaluation
                
                config_kwargs = {
                    "response_mime_type": "application/json",
                    "temperature": 0.0
                }
                if active_schema:
                    config_kwargs["response_schema"] = active_schema

                response = client.models.generate_content(
                    model=effective_model_id,
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
                model_id=effective_model_id,
                requested_model_id=model_id,
                credential_route=credential_route,
                raw_response=result,
                attempt=attempt,
                elapsed_ms=round((time.perf_counter() - started_at) * 1000, 2),
            )
            return result
        except Exception as error:
            retryable = (
                request_context.get().get("evaluation_tag") == EVALUATION_GEMINI_ROTATION_TAG
                and "gemini" in effective_model_id.lower()
                and _is_rate_limited(error)
                and attempt < 3
            )
            delay_seconds = _retry_delay_seconds(error) if retryable else 0.0
            print(f"🚨 [LLM Service Error]: {error}")
            log_event(
                "llm.error",
                level="WARNING" if retryable else "ERROR",
                model_id=effective_model_id,
                requested_model_id=model_id,
                credential_route=credential_route,
                raw_prompt=prompt,
                error=str(error),
                attempt=attempt,
                will_retry=retryable,
                retry_delay_seconds=round(delay_seconds, 2) if retryable else None,
                elapsed_ms=round((time.perf_counter() - started_at) * 1000, 2),
            )
            if not retryable:
                raise
            _set_provider_cooldown(effective_model_id, delay_seconds)
            log_event(
                "llm.rate_limit.retry_scheduled",
                model_id=effective_model_id,
                requested_model_id=model_id,
                attempt=attempt,
                wait_seconds=round(delay_seconds, 2),
            )

# 🔗 BACKWARD COMPATIBILITY ALIAS
call_groq_lpu = call_llm_router
