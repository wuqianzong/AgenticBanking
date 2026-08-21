# **End-to-End Process Specification (User Journey v2.0)**

This document details the operational execution lifecycle for the Consumer Banking Agentic AI platform. The core runtime pipeline coordinates five processing phases to handle conversational inputs securely, maintain dynamic process states, prevent context drift, and guarantee strict compliance with banking regulations.

## 

## **📂 System Architecture Overview**

Evidence-Driven User Journey V2.0: The system treats the user journey as a response-presentation decision, not an early product-domain routing boundary. It first decomposes the request into business retrieval tasks, validates evidence per task, then selects the presentation experience.  
\- Task-Level Resolution: Each request may contain stable retrieval tasks. Every task has a task\_id, a resolved direct subquery, a target product, one or more intent families, and optional complete three-angle groups reserved for final angle expansion.
\- Post-Evidence Presentation Planning: The planner produces flexible UX guidance after evidence review rather than a rigid task-to-component mapping. The generator may compose coherent `TEXT`, `LIST`, `PROGRESS`, and clarification response blocks in the order that best lowers customer cognitive load.
\- **Clean Graph Session Boundaries:** New graph sessions do not restore prior plans, task evidence, presentation state, retrieval payloads, or retry state. Durable memory remains advisory conversation context only.  
\- Task-Specific Retry Refinement: During an insufficiency retry, Node 02e preserves the failed task\_id and receives task-specific search feedback. It reformulates only unresolved tasks while sufficient task evidence remains intact.  
\- **Per-Task Sufficiency and Retry Loop:** Node 03c evaluates every retrieved task independently as `SUFFICIENT`, `PARTIALLY_SUFFICIENT`, or `INSUFFICIENT`. Partial evidence is retained while only the unresolved part retries. After four live retrieval attempts, a partial task remains answerable for its approved part and carries clarification context; a wholly insufficient task becomes `CLARIFICATION_REQUIRED`. The planner waits until every task is answerable or terminally unresolved.
\- **Logical Entailment Auditing:** The Sufficiency Auditor evaluates RAG payloads using common sense and logical entailment rather than strict literal keyword matching (e.g., inferring that an "instant digital process" satisfies a user asking to apply "right now"), preventing false-positive escalations.  
\- **The Decoupled 2D Matrix (Noun-Verb Mapping):** The product/intent matrix is retrieval metadata only. User Journey v2 presentation is selected later from approved task evidence and is not used as a pre-retrieval product boundary.  
\- **Verified Session Evidence Re-ranking (Search Strategy v2.7):** The graph retains approved parent IDs with their search-task metadata for the latest ten verified turns. For a current task with overlapping product and intent, those IDs join the first live direct-retrieval RRF candidate set. They never bypass live retrieval, SQLite hydration, or current-turn sufficiency.
\- **Direct-First Retrieval & Final Angle Expansion:** Initial retrieval and the first two retries use the stable resolved subquery directly. The retry order is: intent expansion, product expansion, then one complete feedback-guided procedural/policy/conceptual angle group. Product and intent remain matrix-valid metadata filters; final angle RRF is a retrieval representation, not a customer-visible plan.
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
  * **Logic:** Classifies the turn as ESCALATION, CLARIFICATION, or VALID\_REQUEST. It does not select a product-specific user journey and does not create a queue.  
  * **Routing:** ESCALATION → Node 09, CLARIFICATION → Node 04b, VALID\_REQUEST → Node 02e.  
* **Node 02e: Intent Formulator and Task Router**  
  * **Logic:** Decomposes a valid banking request into stable search tasks. Each task includes task\_id, a resolved direct subquery, target\_commercial\_product, target\_intent, and a complete base angle group retained for a possible final expansion. A component that cannot safely map to a Product/Intent task is retained as a user-derived intent clarification task rather than triggering a guessed product search. Search and intent-clarification tasks may be returned together. In retry mode, preserves task\_id, customer component, direct subquery, prior filters, and prior angle groups deterministically. The fixed live-retrieval refinement order is intent expansion, product expansion, then angle expansion. Every search task proceeds to live retrieval.
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
  * **Logic:** Evaluates each task independently using its own candidate parent records and resolved subquery. It classifies evidence as `SUFFICIENT`, `PARTIALLY_SUFFICIENT`, or `INSUFFICIENT`; it is allowed to treat a clear policy boundary, limit, condition, or restriction as response-ready when it supports a useful bounded response. It approves only exact supplied parent IDs and rehydrates them directly from SQLite. Partial approved IDs remain available during later retries. For partial or insufficient results it returns incremental, parent-cited `evidence_delta` facts only; these are retry context, never factual answer evidence. After the bounded attempts, wholly insufficient tasks become `CLARIFICATION_REQUIRED`; terminal rejected parents remain suggestion-only clarification evidence.
  * **Routing:**  
    * Any RETRY task → Node 02e. All tasks SUFFICIENT or CLARIFICATION\_REQUIRED → Node 04a.

### 

### **Phase 3: Evidence-Driven User Journey Planning and Response Generation**

This layer converts approved task-level FAQ evidence into the most suitable customer response experience, including grounded step-by-step guidance where supported.

* **Node 04a: User Journey V2 Presentation Planner**  
  * **Logic:** Runs after task-level evidence selection and produces internal UX guidance: the customer’s primary need, narrative focus, grouping guidance, optional visual hints, clarification guidance, and expected evidence coverage. It does not prescribe one response block per retrieval task or require a fixed response structure. It applies the mental-model principle—text to understand, list to scan, progress to complete a supported process—while letting the generator combine aggregate approved evidence into a coherent conversational answer. Clarification remains response content, not a presentation type.
  * **Routing:** Presentation ready → Node 04b. Planning failure → Node 10\.

* **Node 04b: Response Generator (Model A Generation)**  
  * **Logic:** Uses the planner's UX guidance and the aggregate task-level approved evidence to generate one coherent conversational response as flexible response blocks. A block may be text, checklist, or expandable progress; blocks are organised for comprehension rather than mirroring retrieval-task boundaries. A block can include natural connector text and extended grounded text. `sourceTaskIds` is internal provenance only, not a display contract. All visible facts, titles, items, steps, chips, and extended text remain subject to deterministic placeholder validation, compliance, and groundedness checks. Mixed evidence may be explained together naturally; unresolved intent, retrieval, or decline context is emitted as a separate clarification block.
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

* **Node 06: Outbound Semantic Gate (Parallel Compliance and Groundedness Audits)**
  * **Logic:** A focused compliance auditor checks all visible answer, clarification, and UI content only against the shared MAS regulatory context. In parallel, Groundedness v2.0 uses a normal-answer sub-prompt that treats approved task evidence as the only factual source. It evaluates one natural response through two internal lenses: direct factual support and bounded bridge inference from the evidence to the resolved subquery. Greeting and memory validation retain their separate source-specific prompts. Clarifications are checked against their dedicated permitted sources. Both audits must pass before Node 07.
  * **Routing:**  
    * Pass → Node 07\. Fail with retry count below 3 → Node 04b. Third failure caused only by clarification grounding, connection, or compliance, while the answer remains independently safe → Node 11\. Any other third outbound safety failure → Node 09\.

* **Node 07: Precision Substitution (Deterministic Rule Engine)**  
  * **Logic:** Precision substitution applies to approved answer text and UI options after the semantic pass. Replaces validated placeholders with their exact values from SQLite.  
  * **Routing:** Routes to **Node 08**.

* **Node 08: Soft Escalation Analyzer (Deterministic Telemetry Monitor)**  
  * **Logic:** When turn\_count \>= 5, it appends a non-blocking 'Talk to Agent' option to the response payload.  
  * **Routing:** Concludes the cycle and transfers control to the client interface waiting state (**Node 15**).

* **Node 09: Escalation Confirmation Broker**
  * **Logic:** Presents an escalation-confirmation block before mock transfer. Its case summary is a concise current-session handoff brief: it prioritizes the most recent unresolved banking goal and includes earlier chat only when necessary context for that unresolved issue. Completed or unrelated topics and the customer's transfer request itself are excluded. On confirmation, the system displays the mock transfer-in-progress message.
  * **Routing:** End turn.

* **Node 10: Deterministic Message Router (Deterministic Fallback)**  
  * **Logic:** Returns a safe deterministic rejection for inbound safety failures, routing failures, or non-recoverable deterministic errors.  
  * **Routing:** End turn.  
* **Node 11: Deterministic Clarification Fallback**  
  * **Logic:** Runs only after three failed semantic rewrites of an intent or terminal retrieval clarification. Retains any answer that has independently passed approved-evidence safety, answer groundedness, and advice checks. For partial success, it preserves the answer and appends a neutral clarification. If no valid retrieval task could be formed, it uses a distinct full-intent fallback asking for the product or service and the information the customer wants.
  * **Routing:** End turn.

## 

## **🔗 Current Primary Graph Routing Summary**

Start → Node 00 for INIT, otherwise Node 01\. Node 01 → Node 02c → Node 02d → Node 02e. Node 02e → Node 03a → Node 03b → Node 03c. Node 03c → Node 02e only for unresolved-task retries; otherwise → Node 04a. Node 04a → Node 04b → Node 05 → parallel Node 06 compliance and groundedness audits → audit join → Node 07 → Node 08 → End. Node 06 third clarification-only failure → Node 11 → End. Explicit or hard escalation → Node 09 confirmation → mock transfer on confirmation. Unsafe or non-recoverable deterministic paths → Node 10.

## 
