# **End-to-End Process Specification (User Journey v2.0)**

This document details the operational execution lifecycle for the Consumer Banking Agentic AI platform. The core runtime pipeline coordinates five processing phases to handle conversational inputs securely, maintain dynamic process states, prevent context drift, and guarantee strict compliance with banking regulations.

## 

## **📂 System Architecture Overview**

Evidence-Driven User Journey V2.0: The system treats the user journey as a response-presentation decision, not an early product-domain routing boundary. It first decomposes the request into business retrieval tasks, validates evidence per task, then selects the presentation experience.  
\- Task-Level Resolution: Each request may contain stable retrieval tasks. Every task has a task\_id, one or more complete three-angle groups, a target product, and one or more intent families.
\- Post-Evidence Presentation Planning: The planner selects one presentation type only after evidence review: TEXT, PROGRESS, LIST, or ESCALATION. Clarification is response content that may accompany TEXT, PROGRESS, or LIST.  
\- **Clean Graph Session Boundaries:** New graph sessions do not restore prior plans, task evidence, presentation state, retrieval payloads, or retry state. Durable memory remains advisory conversation context only.  
\- Task-Specific Retry Refinement: During an insufficiency retry, Node 02e preserves the failed task\_id and receives task-specific search feedback. It reformulates only unresolved tasks while sufficient task evidence remains intact.  
\- **Per-Task Sufficiency and Retry Loop:** Node 03c evaluates every retrieved task independently. Each task can be SUFFICIENT, RETRY, or terminally unresolved (CLARIFICATION\_REQUIRED) after four live retrieval failures. Only failed tasks return to Node 02e in retry mode; successful tasks retain their approved evidence. The planner waits until every task is either sufficient or terminally unresolved.
\- **Logical Entailment Auditing:** The Sufficiency Auditor evaluates RAG payloads using common sense and logical entailment rather than strict literal keyword matching (e.g., inferring that an "instant digital process" satisfies a user asking to apply "right now"), preventing false-positive escalations.  
\- **The Decoupled 2D Matrix (Noun-Verb Mapping):** The product/intent matrix is retrieval metadata only. User Journey v2 presentation is selected later from approved task evidence and is not used as a pre-retrieval product boundary.  
\- **Approved Evidence Cache:** Cache entries are session-scoped and contain approved SQLite parent IDs only. The cache key is a normalized, case-insensitive product plus an unordered set of intent families. Node 02e independently formulates fresh tasks first; deterministic backend logic then compares task keys. Matching tasks rehydrate their exact parent records from SQLite and proceed to task-level sufficiency review. Cache misses run live RAG. A cache insufficiency does not consume a live-RAG retry.  
\- **Angle-Group Query Decomposition & Fan-Out:** Node 02e creates a complete base group of Procedural (the 'How'), Policy (the 'Rules'), and Conceptual (the 'Nouns') angles. Retrieval feedback may append complete groups rather than diluting prior query strings. Node 03b fires three concurrent searches per angle group, all under one shared matrix-valid product/intent filter.
\- **Two-Level Parent-Centric Reciprocal Rank Fusion (RRF):** Node 03b first calculates RRF (sum 1/(60 \+ Rank)) within each group, then equal-normalises group scores into one task score per parent ID. It applies the existing 50% drop-off selection rule and MAX\_PARENTS \= 4 before SQLite hydration. There is no direct-hit bypass.
\- **Atomic Ledger Updates:** Step progression and ledger logging (completed\_steps and current\_step\_index) are decoupled from text generation and executed atomically inside Node 06 after passing all downstream safety gates, eliminating state drift during safety retries.  
\- **Volatile State Reset Engine:** Implements a universal VOLATILE\_STATE\_RESET dictionary at the start of every turn (Nodes 00 and 01). It cleanly wipes legacy artifacts (retry counts, feedback histories, pending advances) to prevent cross-turn memory leaks and "Dangling State" bugs.  
\- **Global MAS Regulatory Context:** The shared MAS regulation variable is injected into the outbound semantic gate and memory-summary safety validation. Inbound prompt-injection assessment is performed by the dedicated Prompt Guard model using an attack-risk probability cutoff, rather than a full MAS compliance judgment.  
\- **Clarification Safety Degradation:** Terminal retrieval clarification is retried through the semantic gate up to three times. If it cannot produce a compliant, grounded, and connected clarification, Node 11 returns a deterministic general clarification while preserving any separately safe approved answer.  
\- **Approved Versus Suggestion-Only Evidence:** Approved parent records may support customer answers. Rejected retrieved parents may support only clearly labelled related-topic suggestions during clarification. They cannot support factual answers, instructions, UI actions, or product claims.  
\- **Task-Level Partial Success:** A request with several tasks can return grounded answers for sufficient tasks while requesting clarification only for failed tasks. One unsupported task does not invalidate approved answers for other tasks.

## **🛠️ Phase-by-Phase Technical Specifications**

### **Phase 0: Chatbot Initialization & User Inputs**

This phase manages chatbot initialization at turn 1 by generating a greeting with a summary of past memory (if any) for continuation of the conversation, and enforces deterministic check on all user inputs.

* **Node 00: Reset past-term data & Initialization with greeting**  
  * **Logic:** It hydrates advisory long-term/session-summary context and optional baseline memory. It begins a clean graph session and initializes empty task evidence, task retries, presentation state, and approved-evidence cache. It generates a greeting and routes it through the same outbound validation pipeline.

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
  * **Logic:** Classifies the turn as ESCALATION, CLARIFICATION, or VALID\_REQUEST. It does not select a product-specific user journey and does not create a queue.  
  * **Routing:** ESCALATION → Node 09, CLARIFICATION → Node 04b, VALID\_REQUEST → Node 02e.  
* **Node 02e: Intent Formulator and Task Router**  
  * **Logic:** Decomposes a valid banking request into stable search tasks. Each task includes task\_id, a base procedural/policy/conceptual angle group, target\_commercial\_product, and target\_intent. A component that cannot safely map to a Product/Intent task is retained as a user-derived intent clarification task rather than triggering a guessed product search. Search and intent-clarification tasks may be returned together. In retry mode, preserves task\_id and all prior task data deterministically: angle expansion appends one group, intent expansion adds only valid intents, and product expansion adds only valid products. Every search task proceeds to live retrieval; no product/intent cache may bypass the current task query.
  * **Routing:** Every search task → Node 03a. Intent clarification tasks bypass RAG and join the presentation layer with approved evidence. No search task with an active plan → Node 04a. Intent clarification only → Node 04a for a plain TEXT response with clarification content.

### 

### **Phase 2: Task-Level Retrieval, Evidence Selection, and Sufficiency**

This phase retrieves and validates FAQ evidence per task. All new and retry tasks are retrieved concurrently through RAG. Verified parents from the last ten session turns may join a matching task's first direct-retrieval RRF ranking, but never bypass current retrieval or sufficiency validation. Evidence remains grouped by task until presentation planning.

* **Node 03a: Retrieval Dispatcher**  
  * **Logic:** Identifies unresolved tasks, clears transient payload buffers, and dispatches the RAG worker for live retrieval.
  * **Routing:** Node 03a dispatches Node 03b whenever unresolved search tasks exist; Node 03b then routes to Node 03c.

* **Node 03b: Task-Level RAG Worker**  
  * **Logic:** Retrieves unresolved tasks concurrently. Direct retrieval is always the first attempt. When a task overlaps both product and intent with verified evidence from one of the previous ten session turns, those parent IDs are independently re-ranked against the current direct subquery and join the live result through RRF. They remain candidates only and must pass the current sufficiency audit. Later retries use their defined query-refinement strategy. RRF applies the existing selection contract, hydrates parent records from SQLite, and returns them grouped by task\_id.
  * **Routing:** Routes directly to Node 03c for task-level sufficiency evaluation.

* **Node 03c: Task-Level Sufficiency Auditor and Evidence Selector**  
  * **Logic:** Evaluates each task independently using its own candidate parent records. For a sufficient task, approves only exact supplied parent IDs and rehydrates those IDs directly from SQLite. Builds approved evidence only from approved parent records. For an insufficient task, stores query-refinement feedback and loops only that task back to Node 02e. After the bounded retrieval attempts, marks only that task CLARIFICATION\_REQUIRED rather than escalating the entire request. Preserves the exact SQLite parent records returned by the final failed retrieval attempt as suggestion-only evidence for the clarification flow. They are never used as answer evidence.
  * **Routing:**  
    * Any RETRY task → Node 02e. All tasks SUFFICIENT or CLARIFICATION\_REQUIRED → Node 04a.

### 

### **Phase 3: Evidence-Driven User Journey Planning and Response Generation**

This layer converts approved task-level FAQ evidence into the most suitable customer response experience, including grounded step-by-step guidance where supported.

* **Node 04a: User Journey V2 Presentation Planner**  
  * **Logic:** Runs after task-level evidence selection, not before retrieval. Selects one presentation type by visualizing the customer’s mental model: TEXT to understand, LIST to scan or remember independent items, PROGRESS to know where they are in or complete a process, and ESCALATION when required. The customer’s higher-level intent chooses the experience while approved evidence constrains every displayed fact. Account-opening and application/onboarding requests prefer PROGRESS when at least two evidence-supported actions, prerequisites, or constraints can be presented honestly as preparation stages; it must not invent app navigation or claim an unsupported complete workflow. Chooses LIST for three or more independent grounded items, such as eligibility criteria or required documents, and supplies a concise dynamic list title. It may append up to two grounded choice chips where the customer needs to choose a supported next action or topic. When several topics could be progress processes, it selects the single most helpful primary process from customer context and presents other supported topics as concise supporting information; it never asks the customer to choose a UI presentation or workflow. Clarification is response content rather than a presentation type. Marks unresolved tasks unavailable without inventing facts or steps.
  * **Routing:** Presentation ready → Node 04b. Planning failure → Node 10\.

* **Node 04b: Response Generator (Model A Generation)**  
  * **Logic:** Generates answers only from selected approved evidence. Uses a progress/stepper payload only for a supported PROGRESS plan with multiple steps. For progress processes, returns one grounded detail for every planned step; the frontend initially expands the first step and allows customers to expand any step. Stored step details may be reused for later customer interaction, but are regenerated on a safety rewrite so the semantic gate never rechecks a previously rejected detail set. For LIST, returns three to eight independent grounded items plus a dynamic title; the frontend displays each with a green check marker. List items, titles, introductions, and step details are included in outbound grounding, deterministic placeholder, and precision-substitution checks. TEXT remains normal conversational content. For mixed results, answers sufficient tasks and separately clarifies unresolved tasks. It combines intent-formulation clarification tasks with retrieval-insufficiency clarification where both occur. An intent clarification must explicitly name the user-derived unresolved term and must not guess a product, fact, or policy. For terminally unresolved tasks, begins with an apology, may suggest up to two related topics only from rejected retrieved SQLite records, and asks for a general clarification. It must not use ambiguous references such as “that” or “it”. Any unresolved task returns no UI options, including when other tasks have approved answers. Does not request unsupported specifics such as amount, term, purpose, eligibility, or application details unless those appear in the rejected suggestion evidence.
  * **Routing:** Moves output downstream to **Node 05**. 

### 

### **Phase 4: Output Guardrails & Escalation Handler**

This multi-tier verification layer checks outbound responses to maintain accuracy, groundedness, compliance and handle edge case failures safely.

* **Node 05: Outbound Deterministic Gate (Deterministic Evaluator)**  
  * **Logic:** Performs "Full-Payload Sweeping" by combining final\_draft string and UI options array (chips/buttons) to sweep for illegal placeholders, validating against the SQLite Precision\_Reference\_Table.  
  * **Routing:**  
    * Pass: Moves to **Node 06**.  
    * Fail (Retry \< 3): Logs an error to draft\_feedback\_history and routes back to **Node 04b** for generation adjustment.  
    * Fail (Max Retries): Routes directly to **Node 09**.

* **Node 06: Outbound Semantic Gate (Model B Compliance Guard)**  
  * **Logic:** The Judge evaluates BOTH text and UI buttons against the shared Global MAS Regulatory Context. Evaluates the approved evidence itself for compliance. Evaluates answer text and answer UI options for groundedness against selected approved evidence. Evaluates retrieval clarification grounding against rejected suggestion-only retrieval evidence and intent clarification terms against their user-derived unresolved request. It also checks clear connection to each unresolved term plus safety and MAS compliance. Checks all answer text, clarification text, and UI options for unauthorized advice. Promotes approved-evidence cache entries only after a successful semantic pass. Advances a guided-process ledger only after successful downstream validation.
  * **Routing:**  
    * Pass → Node 07\. Fail with retry count below 3 → Node 04b. Third failure caused only by clarification grounding, connection, or compliance, while the answer remains independently safe → Node 11\. Any other third outbound safety failure → Node 09\.

* **Node 07: Precision Substitution (Deterministic Rule Engine)**  
  * **Logic:** Precision substitution applies to approved answer text and UI options after the semantic pass. Replaces validated placeholders with their exact values from SQLite.  
  * **Routing:** Routes to **Node 08**.

* **Node 08: Soft Escalation Analyzer (Deterministic Telemetry Monitor)**  
  * **Logic:** When turn\_count \>= 5, it appends a non-blocking 'Talk to Agent' option to the response payload.  
  * **Routing:** Concludes the cycle and transfers control to the client interface waiting state (**Node 15**).

* **Node 09: Hard Escalation**  
  * **Logic:** Returns a human-agent transfer response and diagnostic case-summary payload when the request cannot safely continue.  
  * **Routing:** End turn.

* **Node 10: Deterministic Message Router (Deterministic Fallback)**  
  * **Logic:** Returns a safe deterministic rejection for inbound safety failures, routing failures, or non-recoverable deterministic errors.  
  * **Routing:** End turn.  
* **Node 11: Deterministic Clarification Fallback**  
  * **Logic:** Runs only after three failed semantic rewrites of an intent or terminal retrieval clarification. Retains any answer that has independently passed approved-evidence safety, answer groundedness, and advice checks. For partial success, it preserves the answer and appends a neutral clarification. If no valid retrieval task could be formed, it uses a distinct full-intent fallback asking for the product or service and the information the customer wants.
  * **Routing:** End turn.

## 

## **🔗 Current Primary Graph Routing Summary**

Start → Node 00 for INIT, otherwise Node 01\. Node 01 → Node 02c → Node 02d → Node 02e. Node 02e → Node 03a for retrieval, or Node 03c for all-cache validation. Node 03a → Node 03b → Node 03c. Node 03c → Node 02e for task retries, otherwise Node 04a. Node 04a → Node 04b → Node 05 → Node 06 → Node 07 → Node 08 → End. Node 06 third clarification-only failure → Node 11 → End. Unsafe or non-recoverable paths → Node 09 or Node 10 → End.

## 
