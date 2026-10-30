# Horizon: An Agentic AI Chatbot for Consumer Banking

FT5007 Capstone, NUS School of Computing (MSc in Digital and Financial Technology)
Author: Wu Qianzong (Benson)

Horizon is a minimum viable product of an agentic AI chatbot for the mobile app of a fictional
Singapore digital bank. It answers product and policy questions from the bank's approved FAQ, shows
customers their own account data, routes action requests to the app's own screens and service forms,
and hands over to a human agent when needed. It does not give financial advice and does not execute
transactions.

The full design, evaluation and results are in the final report
(`docs/report/FT5007_Final_Report_Wu_Qianzong.pdf`).

## Repository layout

| Folder | Contents |
| --- | --- |
| `backend/` | FastAPI gateway and the LangGraph orchestration pipeline (`app/`), the FAQ knowledge base and vector index (`data/`), and offline regression tests (`tests/regression/`) |
| `frontend/` | React + Vite mobile chat interface |
| `requirements.txt` | Pinned Python dependencies used for the September–October 2026 evaluation |
| `docs/` | Design specifications, the Known Issues Tracker and the report materials (`docs/report/`) |
| `research/` | Evaluation evidence: study manifests, labels, answer keys, scripts and per-case results |

## Requirements

- Python 3.11
- Node.js 20 or later, with npm
- A Google Gemini API key (the default model for every pipeline role is `gemini-3.5-flash-lite`)
- A Groq API key, used for the inbound Prompt Guard screen (`meta-llama/llama-prompt-guard-2-86m`)
- Optional: a local Redis server on port 6379. Without it, the backend keeps session state in memory.

## Setup

Run all commands from the repository root.

### 1. Backend

```bash
python3.11 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
pip install redis
```

`redis` is the Python client the gateway imports. It is needed even when no Redis server is running.

### 2. API keys

Set the keys as environment variables in the shell that starts the backend:

```bash
export GOOGLE_API_KEY="your-gemini-key"
export GROQ_API_KEY="your-groq-key"
```

Optional:

- `DEEPSEEK_API_KEY` enables DeepSeek as an alternative provider. Choose it in the app's Settings, or
  per role with `HORIZON_MODEL_A`, `HORIZON_MODEL_B` and `HORIZON_MODEL_C`.
- The Groq and DeepSeek keys can instead be placed in git-ignored files at the repository root
  (`.groq_api_key`, `.deepseek_api_key`).

No API keys are included in this submission.

### 3. Frontend

```bash
cd frontend
npm install
```

## Running the app

Start the backend from the repository root (the code imports itself as the `backend` package):

```bash
uvicorn backend.app.api_gateway:app --host 127.0.0.1 --port 8000
```

In a second terminal, start the frontend:

```bash
cd frontend
npm run dev
```

Open http://localhost:5173. The frontend calls the backend at `http://localhost:8000`, and the backend
accepts requests only from `http://localhost:5173`, so keep both default ports.

The app signs in as the test customer `USR_999`, whose account data comes from a simulated ledger in
`backend/data/`. No real customer data is used.

## Example questions

- "How do I increase my daily transfer limit?" (answer from the FAQ, with app steps)
- "What is my account balance?" (own account data, shown by the app, never sent to a model)
- "I lost my card." (FAQ answer plus a button to the card screen)
- "I want to change my address." (service form with a step-up identity check)
- "Should I buy Tesla shares?" (refused: no financial advice)
- "Transfer me to customer service." (handover to a human agent)

## Running the offline regression tests

The regression suites check deterministic behaviour and make no model calls. Each test is a script:

```bash
python backend/tests/regression/test_bug076_no_info_shortcut_conflict.py
```

Each check prints `PASS` or `FAIL`. Run every suite with:

```bash
for t in backend/tests/regression/test_*.py; do python "$t"; done
```

## Evaluation

The system was evaluated against Measurement Framework v5.1, which was frozen before any run:

- Metric definitions: `docs/report/evaluation/METRICS_DESIGN_v5.1.md`
- Methods: `docs/report/evaluation/EVALUATION_METHODS_v5.1.md`
- Results: `docs/report/evaluation/RESULTS_v5.1.md`. The final reported figures are in section 00
  (independent blind rescore of the release replies, 29 Oct 2026).
- Evidence: `research/2026-10-04_v51_holdout/` (frozen run) and
  `research/2026-10-15_final_release_regression/` (release run and rescore)

## Known limitations

Open and accepted issues are logged with their evidence in `docs/KNOWN_ISSUES_TRACKER.md`. The main
ones:

- The same question can get a different outcome on different runs (BUG-068).
- The outbound audit can follow an instruction planted inside FAQ evidence (BUG-063).
- Self-harm handling, scam warnings and model-provider failure handling are outside the MVP scope.
