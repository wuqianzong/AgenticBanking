import sys, types
sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[3]))
from backend.app.core import llm_service as L
L.log_event = lambda *a, **k: None
L.time.sleep = lambda s: None
ok = total = 0
def check(label, cond):
    global ok, total
    total += 1; ok += bool(cond); print(("PASS " if cond else "FAIL ") + label)
calls = []
class FakeModels:
    def __init__(self, err): self.err = err
    def generate_content(self, **k):
        calls.append(L.google_api_key)
        raise self.err
def use(err):
    L.genai.Client = lambda api_key=None: types.SimpleNamespace(models=FakeModels(err))
    calls.clear()
def run():
    try:
        L.call_llm_router("hi", model_id="gemini-3.5-flash-lite"); return None
    except L.ModelUnavailableError as e: return ("unavailable", e.detail)
    except Exception as e: return ("error", str(e))

use(Exception("503 UNAVAILABLE. {'error': {'code': 503, 'message': 'This model is currently experiencing high demand.'}}"))
r = run()
check("503 high demand -> ModelUnavailableError with exact detail", r and r[0] == "unavailable" and "high demand" in r[1])
check("503 high demand -> no retry, no key switch, no model switch (1 call)", len(calls) == 1)
check("ModelUnavailableError is not caught by `except Exception`", not issubclass(L.ModelUnavailableError, Exception))

use(Exception("429 RESOURCE_EXHAUSTED. Quota exceeded: GenerateRequestsPerDayPerProjectPerModel-FreeTier"))
r = run()
pool = len(L._google_key_pool)
check(f"daily quota on every key -> ModelUnavailableError after all keys, both models ({len(calls)} calls, pool {pool})", r and r[0] == "unavailable" and len(calls) == 2 * pool)

use(Exception("500 INTERNAL. An internal error has occurred."))
L.ServerError = type("SE", (Exception,), {})
r = run()
check("other failure (500) -> ordinary error, not 'unavailable'", r and r[0] == "error")

use(ValueError("400 INVALID_ARGUMENT bad schema"))
r = run()
check("400 bad request -> ordinary error (node fallback unchanged)", r and r[0] == "error" and len(calls) == 1)
print(f"\n{ok}/{total}")
