# User Preferences (Style Personalization)

**Status:** Design in progress (WP-4)

## Purpose
This document outlines the design and implementation for the style personalization features (WP-4). Style personalization determines *how* the bot communicates with the user, without introducing new factual banking claims. Because it only shapes phrasing, it operates outside of the strict evidence-groundedness checks.

## Locked Preferences
The bot will support the following preferences for each customer:

1. **Nickname:** The preferred name the bot should use to address the customer (used only in session greetings, not in standard FAQ responses to maintain a natural flow).
2. **Language:** The customer's preferred language for conversation.
3. **Tone:** The desired tone of the bot's responses (e.g., friendly, professional, concise).
4. **Emoji:** A toggle to enable or disable the use of emojis in responses.
5. **Financial Literacy:** Sets the explanation depth and jargon usage. Allowed values:
   - `beginner`
   - `intermediate`
   
   *Behavioral Definitions:*
   - `beginner`: Use simple financial terms. Explain financial terms when they appear in the response.
   - `intermediate`: Use normal financial terms. Explain only complex financial terms when they appear in the response.
   - `professional`: Use professional financial terms freely. No explanation of financial terms necessary.
   - `professional`

## Architecture & Implementation Plan

1. **Persistent Store**
   - Create a new SQLite table (e.g., `Persistent_Customer_Profile`) in `simulated_ledger.db` to store these fields, keyed by `customer_id`.
   
2. **State Extension**
   - Add a `user_profile: Dict[str, Any]` field to `AgentState` in `state.py`.
   
3. **Graph Loader Node**
   - Introduce a new deterministic node (e.g., `node_01b_load_user_profile`) early in the graph. This node will perform a lightweight DB read to populate `AgentState.user_profile`. No LLM calls will be made in this step.
   
4. **Prompt Injection (Response Generator)**
   - In the response generation phase (`node_04b`), inject the `user_profile` into the prompt as a strict *style-only* instruction block.
   
5. **Setter Mechanism (Frontend & API)**
   - To avoid the risks of free-form LLM extraction and accidental conversational overwrites, preferences will be managed entirely outside of the chat graph.
   - **Frontend:** The UI will provide a dedicated "Preferences" button/menu where the user can explicitly set these 5 fields.
   - **Backend API:** A dedicated REST endpoint (e.g., `POST/PUT /api/preferences`) will receive the updates from the frontend and securely write them directly to the `Persistent_Customer_Profile` table in `simulated_ledger.db`. The table schema must be created during database initialization (`ensure_memory_schema`).


## Frontend i18n & Backend Fallbacks

To provide a fully localized experience:
- **Frontend:** A lightweight translation dictionary (`translations.js`) maps hardcoded UI strings, button labels, dropdown options, and tracker captions to the user's preferred language. The entire UI dynamically updates without requiring a page reload when preferences change.
- **Backend Deterministic Messages:** Rejection, error, and clarification fallback strings (e.g. `node_10_standard_rejection`) are pre-translated in a dictionary (`translations.py`) rather than hardcoded in English, ensuring a consistent language experience even when the LLM is bypassed.
- **Non-Financial Route:** The conversational response node (`node_12`) respects the user's language, tone, and emoji preferences.
- **Escalation Routing:** The AI-generated escalation summary output forces adherence to the user's preferred language.

## Compliance and Groundedness

Language and emojis are explicitly excluded from the scope of the compliance (conduct) and groundedness audits. These layers evaluate the *semantic meaning and behavioral intent* of a response. This allows the bot to speak any language and use friendly emojis without falsely triggering "unprofessional conduct" flags, while still strictly enforcing anti-sales rules.


## Language Translation Boundary

To ensure optimal retrieval against the English-based vector store (FAQ chunks), the system enforces a strict language translation boundary:

1. **Inbound Translation (Node 02d - Triage):** When decomposing the raw user query into `financial_subqueries`, the LLM must translate the semantic intent into **English**.
2. **Internal Processing (English-Only):** All subsequent internal orchestration—including intent formulation (`node_02e`), RAG retrieval (`node_03b`), and task sufficiency evaluation (`node_03c`)—operates entirely in English using the translated subqueries.
3. **Outbound Translation (Node 04b - Response Generator):** The response generator uses the `user_profile.language` preference to natively generate the final customer-facing response in the user's preferred language, utilizing the English evidence provided by the orchestration layer.
