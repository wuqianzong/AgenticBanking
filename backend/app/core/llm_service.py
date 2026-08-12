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
    is_grounded: bool = Field(description="True if claims are derived from knowledge. Exception: Apologies are True.")
    contains_advice: bool = Field(description="True if the response contains unauthorized financial advice.")
    reason: str = Field(description="A brief sentence explaining the reasoning.")

# 2. SUFFICIENCY AUDITOR (Node 03c)
class SufficiencyEvaluation(BaseModel):
    status: str = Field(description="Must be exactly 'SUFFICIENT' or 'INSUFFICIENT'.")
    reason: str = Field(description="Explanation of why the knowledge is or isn't sufficient.")
    error_level: int = Field(description="1 or 2 if insufficient, otherwise 1.")
    complexity: str = Field(description="Must be 'simple' or 'multi-step'.")

# 3. USER JOURNEY CLASSIFIER / TRIAGE (Node 02d)
class TriageEvaluation(BaseModel):
    triage_status: str = Field(description="Must be 'ESCALATION', 'CLARIFICATION', or 'VALID_JOURNEY'.")
    target_journeys: List[str] = Field(description="Ordered list of target journeys. Empty if not VALID_JOURNEY.")

# 4. 2D MATRIX QUERY FORMULATOR (Node 02e) -> NESTED SCHEMA
class SearchTask(BaseModel):
    procedural: str = Field(description="Comma-separated action verbs and tool nouns.")
    policy: str = Field(description="Comma-separated rule nouns and constraints.")
    conceptual: str = Field(description="Comma-separated definition nouns.")
    target_commercial_product: str = Field(description="The exact Noun from the Product Matrix.")
    target_intent: List[str] = Field(description="List of 1-3 Verbs from the Intent Matrix.")

class QueryFormulation(BaseModel):
    search_tasks: List[SearchTask] = Field(description="Array of semantic search tasks generated from the prompt.")

# 5. SUPERVISOR TOOL ROUTER (Node 03a)
class SupervisorRouting(BaseModel):
    active_tool_routes: List[str] = Field(description="Array of selected tools (e.g., 'n17_rag', 'n17_api').")

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
            if "Evaluate if the Retrieved Knowledge is sufficient" in prompt:
                active_schema = SufficiencyEvaluation
            elif "Evaluate the Generated AI Response" in prompt:
                active_schema = SemanticEvaluation
            elif "Triage the user input based on the stage of their journey" in prompt:
                active_schema = TriageEvaluation
            elif "Act as a Decoupled Database Search Architect" in prompt:
                active_schema = QueryFormulation
            elif "Determine which retrieval tools are necessary" in prompt:
                active_schema = SupervisorRouting
                
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
