"""TEST-ONLY: run a study with the app's "gemini" provider choice pointed at a
different Gemini model, in memory (the in-process test server shares this
process). Usage: python -m research._framework.run_study_model MODEL_ID <run_study args>"""
import sys

from backend.app.core import llm_service

model = sys.argv.pop(1)
llm_service.PROVIDER_MODELS["gemini"] = model
from research._framework import run_study  # noqa: E402

run_study.main()
