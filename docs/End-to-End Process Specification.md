# **End-to-End Process Specification (User Journey v2.0)**

This document details the operational execution lifecycle for the Consumer Banking Agentic AI platform. The core runtime pipeline coordinates five processing phases to handle conversational inputs securely, maintain dynamic process states, prevent context drift, and guarantee strict compliance with banking regulations.

## 

## **📂 System Architecture Overview**

Evidence-Driven User Journey V2.0 with Tool Calling v2.0: The system treats the user journey as a response-presentation decision, not an early product-domain routing boundary. It first decomposes the request into safe financial subqueries, plans generic tool tasks, validates each tool result, then selects the presentation experience.
\- Task-Level Resolution: Each request may contain stable retrieval tasks. Every task has a task\_id, a resolved direct subquery, a target product, one or more intent families, and optional complete three-angle groups reserved for final angle expansion.
\- Post-Evidence Response Composition: Node 04a performs global resolution sufficiency only; Node 04b alone composes coherent `TEXT`, `LIST`, `PROGRESS`, and clarification response blocks in the order that best lowers customer cognitive load.
\- **Clean Graph Session Boundaries:** New graph sessions do not restore prior plans, task evidence, presentation state, retrieval payloads, or retry state. Durable memory remains advisory conversation context only.  
\- Task-Specific Retry Refinement: During an insufficiency retry, Node 02e preserves the failed task\_id and receives task-specific search feedback. It reformulates only unresolved tasks while sufficient task evidence remains intact.  
\- **Per-Task Sufficiency and Retry Loop:** Node 03c evaluates every retrieved task independently as `SUFFICIENT`, `PARTIALLY_SUFFICIENT`, or `INSUFFICIENT`. Partial evidence is retained while only the unresolved part retries. After four live retrieval attempts, a partial task remains answerable for its approved part and carries clarification context; a wholly insufficient task becomes `CLARIFICATION_REQUIRED`. The planner waits until every task is answerable or terminally unresolved.
\- **Logical Entailment Auditing:** The Sufficiency Auditor evaluates RAG payloads using common sense and logical entailment rather than strict literal keyword matching (e.g., inferring that an "instant digital process" satisfies a user asking to apply "right now"), preventing false-positive escalations.  
\- **The Decoupled 2D Matrix (Noun-Verb Mapping):** The product/intent matrix is retrieval metadata only. User Journey v2 presentation is selected later from approved task evidence and is not used as a pre-retrieval product boundary.  
\- **Verified Session Evidence Re-ranking (Search Strategy v2.7):** The graph retains approved parent IDs with their search-task metadata for the latest ten verified turns. For a current task with overlapping product and intent, those IDs join the first live direct-retrieval RRF candidate set. They never bypass live retrieval, SQLite hydration, or current-turn sufficiency.
- **Direct-First Retrieval & Final Answer-Side Angle Expansion:** Initial retrieval and the first two retries use the stable resolved subquery directly to target question-side vector chunks. The retry order is: intent expansion, product expansion, then one complete feedback-guided procedural/policy/conceptual angle group. These final angles are formulated as natural sentence fragments to target the answer-side of the vector space. Product and intent remain matrix-valid metadata filters; final angle RRF is a retrieval representation, not a customer-visible plan.
\- **Two-Level Parent-Centric Reciprocal Rank Fusion (RRF):** Node 03b first calculates RRF (sum 1/(60 \+ Rank)) within each group, then equal-normalises group scores into one task score per parent ID. It applies the existing 50% drop-off selection rule and MAX\_PARENTS \= 4 before SQLite hydration. There is no direct-hit bypass.
\- **Atomic Ledger Updates:** Step progression and ledger logging (completed\_steps and current\_step\_index) are decoupled from text generation and executed atomically inside Node 06 after passing all downstream safety gates, eliminating state drift during safety retries.  
\- **Volatile State Reset Engine:** Implements a universal VOLATILE\_STATE\_RESET dictionary at the start of every turn (Nodes 00 and 01). It cleanly wipes legacy artifacts (retry counts, feedback histories, pending advances) to prevent cross-turn memory leaks and "Dangling State" bugs.  
\- **Groundedness v2.0 (Evidence \+ Conduct):** Every customer-facing surface — normal answers, greetings, non-financial replies, durable memory summaries, and the escalation case summary — is checked by the same two shared functions in `backend/app/core/groundedness.py`: evidence groundedness against that route's own declared evidence, and conduct compliance against the shared `BANK_CONDUCT_CONTEXT` and `MAS_REGULATIONS_CONTEXT` from `backend/app/core/compliance.py`. See `docs/GROUNDEDNESS_v2.0.md`. Inbound prompt-injection assessment remains a separate, dedicated Prompt Guard attack-risk probability cutoff, not a MAS compliance judgment.  
\- **Clarification Safety Degradation:** Terminal retrieval clarification is retried through the semantic gate up to three times. If it cannot produce a compliant, grounded, and connected clarification, Node 11 returns a deterministic general clarification while preserving any separately safe approved answer.  
\- **Approved-Evidence Boundary:** Only parent IDs approved by Node 03c may cross the RAG boundary. Rejected candidates, raw retrieval text, and retry evidence remain internal to RAG and cannot support answers, clarification suggestions, instructions, UI actions, or product claims.
\- **Generic Tool Ledger:** `tool_tasks` and `tool_results` carry validated work between the Tool-Calling Planner and Resolution Planner. RAG is the only enabled executor in this release; API and deeplink contracts are registered but disabled until a read-only executor and validation contract are implemented.
\- **Task-Level Partial Success:** A request with several tasks can return grounded answers for sufficient tasks while requesting clarification only for failed tasks. One unsupported task does not invalidate approved answers for other tasks.

## **🛠️ Phase-by-Phase Technical Specifications**

### **Phase 0: Chatbot Initialization & User Inputs**

This phase manages chatbot initialization at turn 1 by generating a greeting with a summary of past memory (if any) for continuation of the conversation, and enforces deterministic check on all user inputs.

* **Node 00: Reset past-term data & Initialization with greeting**  
  * **Logic:** It hydrates advisory long-term/session-summary context and optional baseline memory. It begins a clean graph session and initializes empty task evidence, task retries, presentation state, and verified-session evidence history. It generates a greeting and routes it through the same outbound validation pipeline.

* **User Input**  
  * **Logic:** Captures incoming multi-turn user text submissions from the frontend chatbot interface during standard ongoing conversations (starts at Turns 2+ as Turn 1 does not have user input).  
  * **Routing:** Transitions the user input text directly to **Node 01**.

* **Node 01: Deterministic Ingest Gate (Deterministic Security Rule)**  
  * **Logic:** User messages are retained in visible chat history as untrusted until inbound safety validation promotes them. It performs deterministic screening for hardcoded malicious strings and placeholder injection. On pass, it routes directly to Node 02c. On failure, it routes to Node 10\.  
  * **Routing:**  
    * Pass: Directs execution flow to **Node 02c**.  
    * Fail: Routes directly to the rejection node (**Node 10**), which is designed to deterministically return a rejection without much information regarding the cause for safety reasons.

### 

### **Phase 1: Inbound Safety, Request Triage, and Task Decomposition**

* **Node 02c: Inbound Prompt Guard (Model D Security Gate)**  
  * **Logic:** Sends only the raw user input to the Prompt Guard model. The returned attack probability is compared against the configured safety threshold. Errors fail closed.  
  * **Routing:** Pass → Node 02d. Unsafe → Node 10\.

* **Node 02d: Request Triage**  
  * **Logic:** First resolves the customer's true request from the latest message and trusted current-session conversation context. It then applies the whole-turn boundary to that resolved meaning: `NON_COMPLIANT` only when the customer asks Horizon or the assistant to perform, facilitate, provide tactics for, or advise on conduct whose fulfilment would violate the shared MAS compliance context; then `ESCALATION`, then `VALID_REQUEST` with financial-only subqueries, otherwise `NON_FINANCIAL`. A customer asking whether an action is allowed, describing a possible improper action, or correcting a prior premise is not by itself non-compliant; a banking-related question continues through the evidence-bound path without permitting assistance for prohibited conduct. A short confirmation, correction, reason, preference, pronoun, or ellipsis can continue an immediately relevant financial, escalation, or prohibited request. A mixed turn retains only compliant banking subqueries for tools. It may retain a short internal attention cue for a preference, feeling, greeting, or similar aside as `non_financial_context` for response tone only. The original user input remains the sole source of customer-facing wording; the cue is not evidence, a banking request, tool input, or text to quote/paraphrase. It does not select a product-specific user journey or a tool.
  * **Routing:** NON_COMPLIANT → Node 10, ESCALATION → Node 09, NON_FINANCIAL → Node 12, VALID\_REQUEST → Node 03a.
* **Node 02e: Intent Formulator and Task Router**  
  * **Logic:** Is the RAG-internal intent formulator. It receives only financial subqueries assigned to a running RAG tool task and produces stable RAG search tasks or user-derived intent clarification tasks. In retry mode it preserves task identity and refines only unresolved RAG tasks.
  * **Routing:** Search tasks → Node 03b RAG worker. Intent-clarification-only outcomes → Node 04a.

### 

### **Phase 2: Task-Level Retrieval, Evidence Selection, and Sufficiency**

This phase retrieves and validates FAQ evidence per task. All new and retry tasks are retrieved concurrently through RAG. Verified parents from the last ten session turns may join a matching task's first direct-retrieval RRF ranking, but never bypass current retrieval or sufficiency validation. Evidence remains grouped by task until presentation planning.

* **Node 03a: Generic Tool-Calling Planner**
  * **Logic:** Creates an additive, idempotent ledger of tool tasks from financial subqueries. The initial release creates one RAG task per subquery. On a later resolution loop it may add only a registered, enabled, non-duplicate capability task. The loop is bounded by planning-round and task budgets.
  * **Routing:** Pending tasks → generic Node 03b dispatcher; no safe additional task → Node 04a.

* **Node 03b: Generic Tool Dispatcher**
  * **Logic:** Starts registered tasks without graph-level fan-out. Running RAG tasks enter the RAG-internal Node 02e → RAG worker → sufficiency subgraph. A disabled or unavailable tool records a terminal unavailable result and cannot block the turn.
  * **Routing:** RAG task → Node 02e; otherwise → Node 04a.

* **Node 03b: Task-Level RAG Worker**  
  * **Logic:** Retrieves unresolved tasks concurrently. Direct retrieval is always the first attempt. When a task overlaps both product and intent with verified evidence from one of the previous ten session turns, those parent IDs are independently re-ranked against the current direct subquery and join the live result through RRF. They remain candidates only and must pass the current sufficiency audit. Later retries use their defined query-refinement strategy. RRF applies the existing selection contract, hydrates parent records from SQLite, and returns them grouped by task\_id.
  * **Routing:** Routes directly to Node 03c for task-level sufficiency evaluation.

* **Node 03c: Task-Level Sufficiency Auditor and Evidence Selector**  
  * **Logic:** Evaluates each task independently using its own candidate parent records and resolved subquery. It classifies evidence as `SUFFICIENT`, `PARTIALLY_SUFFICIENT`, or `INSUFFICIENT`; it is allowed to treat a clear policy boundary, limit, condition, or restriction as response-ready when it supports a useful bounded response. A parent that supports a useful, bounded fact about a meaningful aspect of the request is retained as partial evidence even when it cannot establish the customer's desired outcome, permission, eligibility, exception, legality, or preferred action. `INSUFFICIENT` is reserved for no materially presentable task-relevant evidence. It approves only exact supplied parent IDs and rehydrates them directly from SQLite. Partial approved IDs remain available during later retries. For partial or insufficient results it returns incremental, parent-cited `evidence_delta` facts only; these are retry context, never factual answer evidence. After the bounded attempts, wholly insufficient tasks become `CLARIFICATION_REQUIRED`; rejected candidates remain internal to RAG and terminal results expose only a topic/status clarification anchor.
  * **Routing:**  
    * Any RETRY task → Node 02e. All tasks SUFFICIENT or CLARIFICATION\_REQUIRED → Node 04a.

### 

### **Phase 3: Evidence-Driven User Journey Planning and Response Generation**

This layer converts approved task-level FAQ evidence into the most suitable customer response experience, including grounded step-by-step guidance where supported.

* **Node 04a: Global Resolution Sufficiency Check**
  * **Logic:** Receives only validated terminal tool results and approved RAG parent IDs. It decides whether the complete available result is resolved, exhausted, or has a concrete gap addressable by a registered enabled non-duplicate tool. It records only internal validated-task IDs and unresolved-topic anchors. It does not plan wording, grouping, titles, blocks, or UI.
  * **Routing:** Valid capability gap → Node 03a for an additive tool task; otherwise presentation ready → Node 04b.

* **Node 04b: Response Generator (Model A Generation)**  
  * **Logic:** Uses the complete current request and trusted conversation context to understand the customer's situation, then uses only aggregate task-level approved evidence for banking facts. It alone chooses the response organisation and UI. A block may be text, checklist, or expandable progress; blocks are organised for comprehension rather than mirroring retrieval-task boundaries. A workflow is rendered only when its evidence-supported conditions are relevant to the customer's stated situation; otherwise it may be narrowly scoped as reference or omitted. When approved evidence is only partially related, the generator explains its verified scope without filling the remaining gap with a conclusion, recommendation, warning, or implication; the customer makes any unsupported decision independently. In a mixed financial/non-financial turn, the response naturally connects one brief social acknowledgement of a relevant preference or feeling to the applicable evidence-bound guidance; the acknowledgement cannot imply a banking outcome. `sourceTaskIds` is internal provenance only, not a display contract. All visible facts, titles, items, steps, chips, and extended text remain subject to deterministic placeholder validation, compliance, and groundedness checks. Mixed evidence may be explained together naturally; unresolved intent, retrieval, or decline context is emitted as a separate clarification block.
  * **Routing:** Moves output downstream to **Node 05**. 

  * **Groundedness recovery:** When the outbound groundedness audit returns `NARROW_TO_EVIDENCE_SCOPE`, the generator must reduce the retry to a short plain-text restatement of the explicit approved-evidence scope. It must not answer the broader customer question, infer exclusivity, render a conditional procedure, ask another question, or add advice, a warning, or a recommendation. This strategy is an internal behaviour constraint, never evidence or customer-facing wording.

* **Node 12: Non-Financial Conversational Response**
  * **Logic:** Produces a short, polite, non-material conversational reply for a `NON_FINANCIAL` turn. It does not provide banking information, make a material suggestion or commitment, ask the customer to explore an offering, or render UI content. Its dedicated groundedness audit permits only a natural acknowledgement of the original message or a generic Horizon Bank scope statement.
  * **Routing:** Node 05 and Node 06 audit retries return to Node 12 rather than Node 04b. After three failed conversational attempts, a deterministic non-material fallback completes the turn; it never falls into the FAQ generator or escalates solely because a courtesy reply failed audit.

### 

### **Phase 4: Output Guardrails & Escalation Handler**

This multi-tier verification layer checks outbound responses to maintain accuracy, groundedness, compliance and handle edge case failures safely.

* **Node 05: Outbound Deterministic Gate (Deterministic Evaluator)**  
  * **Logic:** Performs "Full-Payload Sweeping" by combining final\_draft string and UI options array (chips/buttons) to sweep for illegal placeholders, validating against the SQLite Precision\_Reference\_Table.  
  * **Routing:**  
    * Pass: Moves to **Node 06**.  
    * Fail (Retry \< 3): Logs an error to draft\_feedback\_history and routes back to **Node 04b** for generation adjustment.  
    * Fail (Max Retries): Routes directly to **Node 09**.

* **Node 06: Outbound Semantic Gate (Parallel Compliance and Groundedness Audits)**
  * **Logic:** A focused compliance auditor checks all visible answer, clarification, and UI content against `BANK_CONDUCT_CONTEXT` and the shared MAS regulatory context. In parallel, Groundedness v2.0's evidence check treats approved task evidence as the only factual source for a normal answer. It separately permits a brief non-material courtesy acknowledgement when it faithfully reflects the original user message and makes no banking claim; the original message can never ground financial content. It evaluates the remaining financial response through two internal lenses: direct factual support and bounded bridge inference from the evidence to the resolved subquery. Greeting and non-financial replies use the same shared evidence-groundedness and conduct-compliance functions with their own route-specific evidence and rules, not separate bespoke prompts — see `docs/GROUNDEDNESS_v2.0.md`. Clarifications are checked against their dedicated permitted sources. Both audits must pass before Node 07.
  * **Routing:**  
    * Pass → Node 07\. Fail with retry count below 3 → Node 04b. Third failure caused only by clarification grounding, connection, or compliance, while the answer remains independently safe → Node 11\. Any other third outbound safety failure → Node 09\.

* **Node 07: Precision Substitution (Deterministic Rule Engine)**  
  * **Logic:** Precision substitution applies to approved answer text and UI options after the semantic pass. Replaces validated placeholders with their exact values from SQLite.  
  * **Routing:** Routes to **Node 08**.

* **Node 08: Soft Escalation Analyzer (Deterministic Telemetry Monitor)**  
  * **Logic:** When turn\_count \>= 5, it appends a non-blocking 'Talk to Agent' option to the response payload.  
  * **Routing:** Concludes the cycle and transfers control to the client interface waiting state (**Node 15**).

* **Node 09: Escalation Confirmation Broker**
  * **Logic:** Presents an escalation-confirmation block before mock transfer. Its case summary is a concise current-session handoff brief: it prioritizes the most recent unresolved banking goal and includes earlier chat only when necessary context for that unresolved issue. Completed or unrelated topics and the customer's transfer request itself are excluded. Before use, the generated summary passes the same Groundedness v2.0 evidence and conduct checks as any other customer-facing content, evaluated against the trusted current-session transcript; a failure falls back to the existing deterministic summary rather than retrying. On confirmation, the system displays the mock transfer-in-progress message.
  * **Routing:** End turn.

* **Node 10: Deterministic Message Router (Deterministic Fallback)**  
  * **Logic:** Returns a safe deterministic rejection for inbound safety failures, routing failures, or non-recoverable deterministic errors.  
  * **Routing:** End turn.  
* **Node 11: Deterministic Clarification Fallback**  
  * **Logic:** Runs only after three failed semantic rewrites of an intent or terminal retrieval clarification. Retains any answer that has independently passed approved-evidence safety, answer groundedness, and advice checks. For partial success, it preserves the answer and appends a neutral clarification. If no valid retrieval task could be formed, it uses a distinct full-intent fallback asking for the product or service and the information the customer wants.
  * **Routing:** End turn.

## 

## **🔗 Current Primary Graph Routing Summary**

Start → Node 00 for INIT, otherwise Node 01\. Node 01 → Node 02c → Node 02d. A valid financial turn → Node 03a Tool-Calling Planner → Node 03b Generic Dispatcher. A RAG task enters Node 02e → Node 03b RAG Worker → Node 03c; Node 03c loops unresolved RAG tasks to Node 02e and converts terminal outcomes into validated tool results. Node 04a joins validated results, optionally loops to Node 03a for an additive enabled capability, otherwise → Node 04b → Node 05 → parallel Node 06 compliance and groundedness audits → audit join → Node 07 → Node 08 → End. Node 06 third clarification-only failure → Node 11 → End. Explicit or hard escalation → Node 09 confirmation → mock transfer on confirmation. A compliant non-financial turn → Node 12 → outbound gates; non-compliant, unsafe, or non-recoverable deterministic paths → Node 10.

## 
