import os
import time
from typing import List
from groq import Groq
from google import genai
from google.genai import types
from pydantic import BaseModel, Field
from backend.app.core.system_logger import log_event

# --- 🧠 MULTI-MODEL ARCHITECTURE REGISTRY ---
MODEL_A_GENERATOR = "openai/gpt-oss-120b"       # Groq: Conversational Engine
MODEL_B_AUDITOR = "gemini-3.5-flash-lite"            # Google: Safety & Sufficiency Judge
MODEL_C_PLANNER = "gemini-3.5-flash-lite"            # Google: Resolution Planner & Tool Calling Generator
MODEL_D_PROMPT_GUARD = "meta-llama/llama-prompt-guard-2-86m"  # Groq: Cyber Security Pre-check
MODEL_E_TRIAGE = "gemini-3.5-flash-lite"             # Google: Journey Classifier
# --------------------------------------------

groq_api_key = os.environ.get("GROQ_API_KEY")
google_api_key = os.environ.get("GOOGLE_API_KEY", "AQ.Ab8RN6JT4AL6142h9RH5kjIvS9cxNdzmty-akxr7PmfUV1U4yg")

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

# 3. USER JOURNEY CLASSIFIER / TRIAGE (Node 02d)
class TriageEvaluation(BaseModel):
    triage_status: str = Field(description="Must be ESCALATION, CLARIFICATION, or VALID_REQUEST.")

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

class QueryFormulation(BaseModel):
    search_tasks: List[SearchTask] = Field(description="Array of semantic search tasks generated from the prompt.")
    clarification_tasks: List[IntentClarificationTask] = Field(description="User-derived unresolved components that cannot be safely formulated as search tasks.")

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
