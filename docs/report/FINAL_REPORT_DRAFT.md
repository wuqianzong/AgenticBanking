<!-- DRAFT for Wu Qianzong. Sources: docs/MEASUREMENT_FRAMEWORK_v4.0.md (all figures),
docs/report/USER_STORIES.md, docs/KNOWN_ISSUES_TRACKER.md, the design specifications in docs/,
monthly logs, and docs/report/ui/ (figures). Literature references: verify each entry's exact
bibliographic details before submission. Items marked [TO ADD] need input from Wu Qianzong
(agile artefacts, lessons learnt, personal reflections). -->

# Horizon: An Agentic AI Chatbot for Consumer Banking

**FT5007 FinTech Capstone Project: Final Report**

MSc in Digital Financial Technology, School of Computing, National University of Singapore

Student: Wu Qianzong (A0327426A)
Academic advisor: [TO ADD]
Examiner: [TO ADD]
Project period: May to October 2026 (internal capstone)
Submission: Instructional Week 11, AY2026/2027 Semester 1

---

## Executive Summary

Banks deploy service chatbots to take routine demand off human agents, serve customers at any hour, and do so without adding conduct or data risk. Generative AI makes chatbots far more capable than scripted ones, but it also brings risks that a regulated bank cannot accept: invented facts, unlicensed financial advice, leakage of personal data, and manipulation through prompt injection. In Singapore, these expectations come from the Monetary Authority of Singapore (MAS), the Personal Data Protection Commission (PDPC) and the Infocomm Media Development Authority (IMDA).

This project designed, built and evaluated **Horizon**, an agentic AI chatbot for the customer-facing mobile app of a fictional Singapore digital bank. It is a minimum viable product (MVP). Horizon answers product and policy questions from the bank's FAQ, shows customers their own account data, routes them to the right app screen or service form, and hands over to a human agent when needed. It does this while keeping personal data away from the language models and auditing every reply before it is sent.

The system is a multi-agent pipeline of 24 nodes built on LangGraph:

- **Inbound safety:** a deterministic word filter, a prompt-injection guard, and a dedicated compliance screen that runs in parallel with triage. The screen judges whether *the bank* would breach regulation if it fulfilled the request.
- **Need-based triage and tool planning:** over a registry of tools (FAQ search, own-data lookup, app screens, service forms).
- **Retrieval-augmented generation:** over 708 FAQ entries, with a sufficiency auditor that decides what evidence may be used.
- **Contract-based generation:** a response obligation contract, then a combined outbound audit for groundedness and conduct.
- **Post-processing:** deterministic substitution of exact numbers and personal-data placeholders, plus soft and hard escalation.

Evaluation followed a measurement framework (v4.0) derived from eight business needs and Singapore's regulatory expectations, using 28 metrics. The bot was tested through its real API on:

- the BANKING77 customer-question corpus;
- 20 multi-turn banking dialogues adapted from the Schema-Guided Dialogue dataset;
- a purpose-built escalation set;
- a 140-prompt red team based on the IMDA starter kit.

Key results on the delivered code:

- **66–70%** of questions the FAQ covers are fully answered (target 55%). Multi-turn banking dialogues resolve **95.5%** of needs and **80%** of whole sessions.
- **99.6%** of factual claims are supported by approved evidence. Every unanswerable question got an honest "I don't have that information" (67/67).
- **Zero** unlicensed-advice incidents, personal values sent to a model, credential requests, or execution claims. The true advice-incident rate is at most 0.29% at 95% confidence.
- **No harmful reply** reached the customer across the red team. Harmful-content prompts are stopped before any tool runs.
- About **US$0.015** per turn at list price.

The scorecard shows 11 of 17 needs and requirements met and 6 partially met. The partial items are documented limitations: latency (about 22 s median on the free tier), paraphrase consistency, clarification quality, and two items limited by model variation.

The project's main contributions are:
1. A compliance design in which the inbound check predicts the bank's action within its real service scope, rather than judging the customer.
2. A response obligation contract that makes every part of a reply traceable to evidence.
3. A measurement framework that ties each metric to a business need or a Singapore regulatory expectation.
4. An evidence base of 63 tracked issues (61 defects, 2 suspected), each root-caused and decided.

---

## Table of Contents

1. Introduction
2. Requirements
3. Literature Review and Method Justification
4. System Design
5. User Interface
6. Development Process
7. Evaluation
8. Use Cases
9. Discussion and Recommendations
10. Conclusion
References
Appendix A: User Stories
Appendix B: Metric Catalogue
Appendix C: Defect Summary
Appendix D: Project Documentation Index

---

## 1. Introduction

### 1.1 Background

Customer service is one of the largest operating costs of a retail bank, and much of the demand is routine: balances, card problems, transfer limits, fees, profile changes. Banks have used scripted chatbots for years, but these handle only questions their designers anticipated. Large language models (LLMs) can understand free-form questions and compose natural answers, and regional banks report material gains: Trust Bank Singapore reported a 50% reduction in human-handled chats and about 40% fewer complaints after deploying a generative AI chatbot.

The same capability creates new risks. An LLM can state facts that are not true (hallucination), drift into investment advice that only licensed advisers may give, reveal data it should not, or be manipulated by crafted input. A bank is accountable for every word its chatbot says. A chatbot that is helpful but occasionally wrong or non-compliant is not deployable.

### 1.2 Problem statement

> How can a bank deploy a generative AI chatbot that resolves routine customer needs naturally and efficiently, while guaranteeing that every reply is grounded in approved information, compliant with Singapore's financial regulation, and free of personal-data exposure, and how can it prove this with measurable evidence?

### 1.3 Objectives

1. **Resolve routine needs** in natural conversation: account information, card services, transfers, profile changes and product questions, across multiple turns.
2. **Ground every reply** in the bank's approved FAQ content, and admit honestly when no information exists.
3. **Act through safe channels only:** show the customer's own data and route to app screens and forms, never executing transactions or giving advice.
4. **Protect personal data:** no personal value ever reaches a language model.
5. **Enforce compliance by design:** inbound screening of what the bank would be asked to do, and outbound auditing of every reply.
6. **Hand over to people** when the customer asks or is stuck.
7. **Measure it:** define metrics from business needs and regulation, and evaluate the system against them.

### 1.4 Stakeholders

| Stakeholder | Role | Interest |
|---|---|---|
| Retail customers | Users of the chat in the Horizon app | Fast, correct answers and a direct route to act |
| Customer service agents | Receive escalated conversations | A clear case summary |
| Compliance and risk | Accountable for conduct under MAS, PDPA, IMDA | No advice, no misinformation, no data leakage, auditability |
| Operations / product owner | Owns performance and cost | Containment, cost per turn, measurable quality |
| FAQ content owner | Maintains the knowledge base | Visibility of uncovered questions |

### 1.5 Scope and constraints

**In scope (MVP):**
- the customer-facing chat in the mobile app;
- a single authenticated test customer with synthetic data;
- an FAQ knowledge base synthesised from public bank FAQs;
- read-only own-data lookups (balance, address, loyalty tier);
- six app-screen deep links and three service forms;
- English, Chinese and Spanish interfaces.

**Out of scope, by decision:**
- multi-customer isolation;
- the human-agent and admin portals (designed but not built);
- multiple accounts per customer;
- self-harm and distress handling;
- handling of model-provider failure modes beyond a clear "model unavailable" message;
- adding FAQ content to close coverage gaps.

**Constraints:**
- a single student developer on a 16 GB laptop;
- cloud model APIs on free or low-cost tiers (Gemini, DeepSeek, Groq), whose rate limits shaped both latency and testing;
- synthetic data only, since no real bank data was available.

---

## 2. Requirements

### 2.1 Functional requirements

| ID | Requirement | Realised by |
|---|---|---|
| FR-1 | Understand a customer message in context, including follow-ups, confirmations and multiple needs in one message | Context resolution and need-based triage (node_02d) |
| FR-2 | Answer product and policy questions from approved FAQ content only | Query formulation, retrieval, sufficiency audit, generation |
| FR-3 | Show the customer their own account data without exposing it to models | Own-data lookup with placeholder substitution |
| FR-4 | Route action requests to the bank app's own screens or service forms | Tool registry, planner, deep-link and service-request tools |
| FR-5 | Refuse requests whose fulfilment would breach regulation or conduct rules | Parallel inbound compliance screen (node_02f/02g) |
| FR-6 | Block prompt injection and credential disclosure | Prompt guard (Llama Prompt Guard 2), deterministic word filter |
| FR-7 | Audit every reply for groundedness and conduct before sending | Combined outbound audit (node_06), reviser, safe fallback |
| FR-8 | Escalate to a human on request, and offer escalation when the customer is stuck | Hard escalation broker (node_09), soft escalation analyser (node_08) |
| FR-9 | Remember the session and recall unresolved issues at the next login | Rolling compaction, session summary, greeting recall |
| FR-10 | Show progress while the answer is prepared | Streamed progress events from `/api/chat` |

### 2.2 Analytic requirements

The analytic requirements define what must be measured to know whether the system meets its goals. They are summarised from the Measurement Framework v4.0 (Appendix B). Each business need A1–A8 and each Singapore requirement R1–R8, R10 is linked to one or more metrics.

| Need | Analytic requirement | Key metrics |
|---|---|---|
| Take routine demand off agents | Share of needs fully resolved without a human, split into "no FAQ" vs "bot" causes | M1, M2, M25 |
| Resolve the whole need in one session | Share of multi-turn sessions fully resolved | M3 |
| Correct, relevant answers | Answer correctness; claim-level groundedness against evidence | M4, M5, M7 |
| Respond fast enough | Time to first feedback; time to answer | M12, M13 |
| Treat the same question the same way | Outcome consistency across repeats and paraphrases | M11 |
| Hand over at the right time | Escalation precision and recall; human request honoured | M16–M18 |
| Cost less than the work replaced | Cost per turn from logged token usage | M15 |
| Regulatory compliance | Advice incidents, personal data exposure, credential capture, adversarial robustness, action-boundary violations, audit trail | M6, M19, M21–M24, M26–M28 |

### 2.3 User stories

Twenty-four user stories across ten epics capture the requirements from the stakeholders' perspectives. They are grounded in observed usage: 249 multi-turn banking dialogues, the 77 BANKING77 intent categories, and the escalation and red-team sets. Each story has acceptance criteria and recorded evidence. Appendix A lists all stories; a sample:

> **US-03 Start a transfer from the chat.** *As a* retail customer, *I want* to say "Send $200 to Maria" and be taken straight to the transfer screen, *so that* I can complete it quickly and safely in the app.
> *Acceptance:* the reply carries a Send Money button that opens the app's own transfer page; the bot never claims to have sent money. *Evidence:* every transfer turn in the final check returns the button; 0 execution claims in 589 turns.

> **US-18 Be protected from scams.** *As a* retail customer, *I want* the bot to warn me when someone asks for my PIN or OTP, *so that* I don't fall for a scam.
> *Acceptance:* the question gets a clear warning, not a refusal; the bot never asks for credentials. *Evidence:* inbound compliance redesign (2 Oct); 0 credential requests.

### 2.4 Non-functional requirements

| Category | Requirement |
|---|---|
| Compliance | No unlicensed advice (Financial Advisers Act); fair, accurate information (MAS Fair Dealing); anti-scam duties (Shared Responsibility Framework) |
| Privacy | No personal value in any model prompt or model-visible history (PDPA) |
| Safety | Resistant to injection, jailbreaks and data extraction (IMDA starter kit risk areas) |
| Auditability | Each turn's decision chain reconstructable from logs (MAS AI risk management) |
| Performance | First feedback ≤ 2 s; answer median ≤ 15 s and p90 ≤ 30 s (MVP targets) |
| Cost | Cost per turn measured and below the cost of an agent contact |
| Maintainability | Offline regression suites pass before every change |

---

## 3. Literature Review and Method Justification

*[Reviewer note: verify exact bibliographic details for each reference before submission.]*

### 3.1 Retrieval-augmented generation

Retrieval-augmented generation (RAG) conditions a language model on documents retrieved for each query, so answers draw on a controlled knowledge source rather than on the model's parametric memory (Lewis et al., 2020). Surveys identify RAG as the main practical mitigation for hallucination in knowledge-intensive tasks (Gao et al., 2023; Ji et al., 2023). For a bank, RAG is the natural fit: the FAQ is the approved source of truth, and grounding every claim in it is what conduct rules require.

Horizon extends plain RAG in three ways the literature supports:
- **Parent-child chunking:** small child chunks are embedded for precise matching, while full parent FAQ entries are returned for context.
- **Rank fusion across query variants:** results are combined with Reciprocal Rank Fusion (Cormack, Clarke & Büttcher, 2009), a simple, robust way to combine rankings without score calibration.
- **An explicit sufficiency audit** decides which retrieved evidence actually answers the need before generation, addressing the documented weakness that retrieved context may be irrelevant or partial (Gao et al., 2023).

Long, mixed contexts degrade how well models use information (Liu et al., 2024). This motivated both the evidence selection step and the decision to give each prompt one focused task.

### 3.2 Agentic tool use and orchestration

Agentic systems let a model decide which tools to call and combine reasoning with actions (Yao et al., 2023, ReAct). Horizon uses a constrained form of this pattern:
- The planner chooses tools from a declarative **registry** (FAQ search, own-data lookup, app screen, service form).
- Each tool is read-only or hands the action to the customer.
- Orchestration uses LangGraph, a graph-based framework in which each node is a function and edges encode routing. This made parallel branches (triage and compliance screening) and bounded retry loops explicit and testable.

This bounded autonomy follows IMDA's Model AI Governance Framework for Agentic AI, which asks deployers to limit an agent's tool access up front and keep humans accountable.

### 3.3 Reasoning before decision

Prompting a model to reason step by step before answering improves judgment quality (Wei et al., 2022). Horizon applies this structurally: every judging output schema declares its reasoning field before its verdict, so the verdict is generated after the reasoning rather than justified afterwards. For example, the sufficiency auditor's `reason` precedes `status`, and the compliance screen's `requested_from_horizon` precedes its prediction and verdict.

### 3.4 Guardrails, prompt injection and compliance

Prompt injection, both direct and through retrieved content, is a recognised attack on LLM applications (Greshake et al., 2023). Dedicated classifier models detect injection and unsafe content more cheaply and consistently than a general model (Inan et al., 2023, Llama Guard). Horizon layers:

1. a deterministic filter for disclosed credentials;
2. Meta's Llama Prompt Guard 2 classifier for injection and jailbreaks;
3. an LLM compliance screen for regulatory judgment.

Regulatory judgment is framed from the regulated party's side. MAS regulates the bank, not the customer, so the screen predicts the bank's response and judges that.

### 3.5 Evaluation methods

- **LLM-as-a-judge.** Using a strong LLM to judge outputs correlates well with human judgment when the judge is given clear criteria, though judges can share biases with the generator (Zheng et al., 2023). Horizon's groundedness metric uses a judge model claim by claim, with a sensitivity check: the judge caught 12/12 planted fabrications. It records the reduced independence of a same-provider judge.
- **RAGAS.** Decomposes RAG evaluation into retrieval and generation components such as faithfulness and context relevance (Es et al., 2024). Horizon's metrics follow the same split (M1/M25 for coverage, M5 for faithfulness).
- **τ-bench.** Evaluates tool-using agents in realistic multi-turn service tasks (Yao et al., 2024). Horizon's multi-turn study is structured the same way: a simulated customer pursues a balance → transfer → confirm goal against the agent.
- **Datasets.** BANKING77 (Casanueva et al., 2020) provides 13,083 real-style customer questions over 77 banking intents. The Schema-Guided Dialogue dataset (Rastogi et al., 2020) provides the multi-turn banking dialogues.
- **Statistical treatment.** Rates carry Wilson intervals. Zero-tolerance incidents are reported as exact one-sided Clopper–Pearson upper bounds (Clopper & Pearson, 1934).

### 3.6 Performance indicators and regulation

Industry chatbot KPIs (containment, resolution, escalation, cost) measure business value, but each can be gamed in isolation. Measurement Framework v4.0 therefore derives every metric from either a business need or a Singapore regulatory expectation:

- MAS proposed Guidelines on AI Risk Management (2025) and the Information Paper on AI Model Risk Management (2024);
- MAS FEAT Principles (2018);
- MAS Guidelines on Fair Dealing (2024);
- Financial Advisers Act 2001;
- PDPA 2012 and the PDPC Advisory Guidelines on personal data in AI systems (2024);
- MAS Technology Risk Management Guidelines (2021);
- the MAS–IMDA Shared Responsibility Framework (2024);
- IMDA's Model AI Governance Frameworks for Generative AI (2024) and Agentic AI (2026);
- the IMDA starter kit for testing LLM applications.

### 3.7 Summary of method choices

| Choice | Alternative considered | Why this choice |
|---|---|---|
| RAG over a fixed FAQ | Fine-tuning a model on FAQ content | Grounding is auditable claim by claim; content updates need no retraining; model tuning was out of scope |
| Multi-node pipeline, one task per prompt | One large prompt | Focused prompts judged more reliably; the compliance check failed inside the triage prompt and succeeded alone (Section 7.6) |
| LangGraph | Hand-written control flow; autonomous agent loop | Explicit graph gives bounded retries, parallel branches and testable routing |
| Deterministic substitution of numbers and personal data | Letting the model write values | Exact values cannot be hallucinated; personal values never reach the model |
| Parallel inbound compliance screen | Rule inside triage; outbound audit only | Stops prohibited requests before any retrieval or tool call; stays accurate as a focused prompt |
| Measurement framework from needs and regulation | Generic chatbot KPIs | Each number answers a business or regulatory question |

---

## 4. System Design

### 4.1 Architecture overview

Horizon has three layers:

- **Frontend:** a React 19 single-page app styled as the Horizon mobile app (Vite, Tailwind CSS).
- **API gateway:** FastAPI. `/api/chat` streams progress events and the final reply; history and service-request endpoints serve the app.
- **Orchestration:** a LangGraph state graph of 24 nodes. Supporting stores are SQLite (FAQ parents, exact-value table, chat records, service requests, simulated ledger), ChromaDB (FAQ child-chunk embeddings and an answer cache), and a structured JSON event log.

Model roles:

| Role | Model | Used for |
|---|---|---|
| Generator, auditors, planner/triage | Gemini 3.5 Flash-Lite (default) or DeepSeek, selectable per session | Reasoning and generation |
| Fallback | Gemini 3.1 Flash-Lite | Used automatically when 3.5 quota is exhausted |
| Prompt guard | Meta Llama Prompt Guard 2 (86M) on Groq | Injection and jailbreak classification |

*[Figure 1: architecture diagram — reuse the Architecture slide from the presentation deck.]*

### 4.2 Turn pipeline

Each customer message passes through the following stages.

1. **Ingest and deterministic gate (n3).** Stores the message and blocks disclosed credentials (for example "my PIN is 1234") with a rule-based filter.
2. **Prompt guard (n16).** Llama Prompt Guard 2 scores injection and jailbreak risk; messages at or above 0.10 are blocked. It fails closed if the guard cannot be reached.
3. **Triage and compliance screen, in parallel (n2d, n2f → join n2g).**
   - *Triage* resolves the customer's true request from context and decomposes it by need. Each need is tagged as tool-eligible, conversational or needing clarification; triage also detects explicit escalation requests.
   - The *compliance screen* runs at the same time. Given the bot's service scope (built from the tool registry), it records what the customer is asking Horizon for, predicts the truthful, compliant response Horizon would give, and returns PROHIBITED only if the request can only be fulfilled by output that breaches the MAS context or the bank conduct rules.
   - The join applies the compliance verdict first; a blocked turn ends with a fixed refusal.
4. **Tool planning (n14) and dispatch (n17).** The planner chooses tools by the service each registry entry offers: FAQ search, own-data lookup, app screen or service form. A need left without a tool gets FAQ search by default.
5. **Query formulation and retrieval (n2e, n17_rag).**
   - The formulator maps each search need to a product and intent from the bank's taxonomy and writes a direct search query (the need itself, without personal details).
   - Retrieval combines rankings across query variants with Reciprocal Rank Fusion and returns parent FAQ entries.
   - Bounded retries widen the search if needed.
6. **Sufficiency audit (n18).** Decides, per need, whether the evidence is sufficient, relevant (partial) or insufficient. It selects the approved parent entries and writes generation guidance (for example, "admit there is no information on X, then mention Y"). It sees the whole turn's tool results, so it never calls something "unavailable" that another tool has answered.
7. **Resolution planning (n19).** Computes each need's status deterministically and runs a turn-level consistency check that drops contradictory results.
8. **Generation (n8).**
   - Every obligation of the turn becomes a tracked entry under the response obligation contract: evidence, personal data, shortcuts, clarifications, declines, no-information notices. Each entry is linked to a named rule section.
   - The generator writes one coherent reply citing the entries it addresses, and a completeness net repairs anything omitted.
   - Turns with no FAQ evidence go to a narrow sub-prompt that only phrases pre-decided statements. This includes turns answered only by an app screen or form.
9. **Outbound audit (n21_audit, n21) and revision (n8b).**
   - One combined audit checks groundedness against the approved evidence and conduct against the compliance rules.
   - Flagged blocks are revised, up to three rounds; a deterministic safe fallback is used if the reply still fails.
10. **Post-processing.**
    - Precision substitution (n22) replaces exact-number placeholders with verified values from the reference table.
    - Personal-data placeholders are resolved only in the customer's browser.
    - Buttons and forms are attached from tool results, independent of the generated text.
11. **Escalation (n24, n23).**
    - The soft escalation analyser offers "Talk to an agent" when the same unresolved need repeats or confidence stays low, and never on a turn that asks a clarifying question.
    - The hard escalation broker prepares an audited case summary when the customer asks for a person.

### 4.3 Knowledge base and data pipeline

- **Source.** FAQ content was synthesised from a Singapore digital bank's public FAQs (for proprietary reasons the raw text is not used directly) and mapped to a product–intent taxonomy, for example Credit_Card, Deposit_Account, Debit_Card, Horizon_APP.
- **Size.** 708 parent entries, 2,120 embedded child chunks, and 1,152 exact-value placeholders.
- **Chunking.** Child chunks are question chunks plus answer chunks, with 150-character overlaps for long answers and the question prepended to each answer chunk for semantic continuity.
- **Exact values.** Numbers in answers (fees, limits, rates) are stored as placeholders resolved after the audit, so they can never be misquoted.
- **Product briefing.** A shared prompt briefing states business facts the FAQ assumes but never says: one physical card with debit and credit functions, one savings account per customer, shared services in the app.

### 4.4 Personal data protection

Own-data lookups return a placeholder token (for example `{{data-…}}`) to the pipeline. Models, audits and the model-visible chat history see only the token. The real value is kept in a display-only payload and substituted in the customer's browser. A scan of every logged prompt in the measurement run found no personal value (M19 = 0).

### 4.5 Platform choices

| Component | Choice | Justification |
|---|---|---|
| Orchestration | LangGraph 1.2 | Explicit graph of nodes and edges; parallel branches; bounded loops; state inspectable for audit |
| API | FastAPI 0.136 | Async streaming of progress events; simple typed endpoints |
| Frontend | React 19, Vite 8, Tailwind 4 | Fast iteration on a mobile-style UI with rich response blocks |
| Vector store | ChromaDB 1.5 | Local, embedded, metadata filtering by product and intent |
| Structured store | SQLite | Zero-ops local store for FAQ parents, exact values, ledger, chat records |
| Main model | Gemini 3.5 Flash-Lite | Strong structured output at very low cost; free tier for development |
| Alternative model | DeepSeek (deepseek-flash) | Paid, inexpensive, no free-tier rate limits; prompt caching cut input cost (about 80–89% cache hits) |
| Prompt guard | Llama Prompt Guard 2 on Groq | Fast, specialised injection classifier |
| Hardware | 16 GB laptop; models via cloud APIs | Local hosting of two LLMs plus the stack was not feasible; production would use a Singapore-hosted or bank-internal model (PDPA s.26 note) |

### 4.6 Design principles

1. **One task per prompt.** Each model call does one kind of judgment or one kind of engineering work.
2. **Reasoning before verdict** in every judging schema.
3. **Deterministic where possible:** routing, statuses, fallbacks, numbers, buttons and personal data are decided by code, not by a model.
4. **Principles, not case rules,** in prompts.
5. **Give judging nodes the whole picture.** Fix wrong decisions by supplying missing context or correcting a rule, not by withholding information.
6. **Generation guidance as a contract** between the auditor, the generator and the outbound audit.

---

## 5. User Interface

The customer interacts with Horizon inside a mobile-style app shell (Figure 2): a header with the assistant's identity and a "secure session" indicator, a customer status bar (masked account number, pending service requests), the conversation, and an input bar with the footer notice "AI-generated responses may not be fully accurate". The interface is available in English, Chinese and Spanish, and supports per-customer preferences (nickname, tone, financial-literacy level, emoji use).

Replies are rendered as typed response blocks rather than plain text, so the presentation fits the content: paragraphs, step-by-step progress lists, comparisons, accordions, clarification questions with tappable options, and alerts. Interactive elements are attached deterministically from tool results:

| Element | Purpose | Figure |
|---|---|---|
| Progress captions | Show each pipeline stage within 0.03 s of sending ("Understanding request…") | Fig. 3 |
| Personal-data value | The customer's own value (e.g. balance) shown only in the browser, marked "Data protected" | Fig. 4 |
| Deep-link button | Opens the bank app's own screen (e.g. Send Money, Manage Card, Update Profile) | Fig. 5 |
| Embedded service form | A multi-step form (e.g. Change Address) completed by the customer inside the chat | Fig. 6 |
| Soft escalation banner | "It looks like this might need extra help — Talk to an agent" | Fig. 9 |
| Handover confirmation | An audited case summary and a "Confirm transfer" button | Fig. 10 |
| Feedback controls | Thumbs up/down per reply | Fig. 4 |

Figures (in `docs/report/ui/`):

- Figure 2 — `02_greeting.png`: greeting with recall of pending requests
- Figure 3 — `03a_progress_captions.png`: progress captions during processing
- Figure 4 — `03_balance.png`: balance shown only on the customer's screen
- Figure 5 — `04_transfer_send_money_button.png`: transfer request routed to the Send Money screen
- Figure 6 — `06_address_change_options.png`: embedded Change Address form and Update Profile button
- Figure 7 — `07_anti_scam_warning.png`: warning against sharing a PIN with a caller
- Figure 8 — `08_compliance_refusal.png`: investment advice request refused
- Figure 9 — `09_soft_escalation_offer.png`: escalation offered after a repeated unanswered need
- Figure 10 — `10_human_handover.png`: handover confirmation with case summary
- (also `01_logged_out.png`, `05_lost_card.png`)

---

## 6. Development Process

### 6.1 Agile approach

The project ran as one-person agile development with monthly sprints planned with the academic advisor, a product backlog on GitHub Projects, and monthly logs reporting achievements, plans, challenges and mitigations. *[TO ADD: screenshots of the product backlog, sprint backlogs and sprint reviews/retrospectives from GitHub Projects — Appendix E.]*

### 6.2 Sprint history

| Sprint | Goal | Delivered (evidence) |
|---|---|---|
| June | Scope and feasibility | Three-portal product scope narrowed to the customer portal MVP; scope, data and architecture documents (v3.5); feasibility on a 16 GB laptop with remote LLMs; React UI demo of progressive disclosure, precision substitution, deep links and escalation (June log) |
| July | End-to-end conversation for one intent | LangGraph workflow from input to response; RAG pipeline with synthesised FAQ data, product–intent mapping, placeholders and parent–child chunking; deterministic post-check and precision substitution (July log; End-to-End Process Specification, RAG Specification) |
| August | Full multi-tool MVP | Tool Calling v2.0/v3.0 with a tool registry; deep links, personal-data lookup and service-request forms; Prompt Guard; groundedness and conduct audit; hard and soft escalation; memory and session recall; response blocks UI (100 commits; August log) |
| September | Validation and refinement | Research harness; BANKING77 full-coverage run (3,080 cases) and multi-turn campaign (249 dialogues); Known Issues Tracker (63 items); Response Obligation Contract; need-based triage; DeepSeek provider with prompt caching; Measurement Framework v4.0 and its measurement run (119 commits; September log) |
| October | Close findings and final check | Parallel inbound compliance screen (BUG-055); tool-only replies (BUG-058); soft escalation on repeated no-information turns (BUG-051); Groq key rotation; full final check on the delivered code; demo rehearsals on both models (16 commits) |

Every change was committed with a descriptive message (249 commits from 14 June); the history is the coded evidence of each sprint's demo.

### 6.3 Quality process

- **Known Issues Tracker.** Every issue found — 63 in total (61 defects, 2 suspected) — is recorded with its evidence, root cause (deterministic code, model judgment, or data), and decision. Each was explained and discussed before any fix was chosen; several were deliberately put out of scope.
- **Offline regression suites.** Ten suites (93 checks) replay traced cases without model calls and run before every change.
- **Research harness.** Studies are manifest-driven, run through the real `/api/chat` endpoint, and save each case's full event log, so results are reproducible and auditable.
- **Live checks.** After each fix, a targeted live check on the affected cases, then a full re-check of the delivered code.

---

## 7. Evaluation

### 7.1 Measurement framework

Measurement Framework v4.0 starts from two questions: *What does a bank need a chatbot to do, and does this one do it?* and *What do Singapore's regulators expect of an agentic AI chatbot at a bank, and does it meet them?* Eight business needs (A1–A8) and nine regulatory requirements (R1–R8, R10) are each linked to metrics M1–M28 with a target and a method (Appendix B). Performance metrics are reported on questions the FAQ fully covers (53 of 151 BANKING77 questions), because questions with no answering FAQ say nothing about the bot; the raw figure is shown alongside. Safety and compliance metrics are reported on all turns.

### 7.2 Test sets

| Test set | Size | Purpose |
|---|---|---|
| BANKING77, 2 questions per category, labelled with acceptable routes | 154 questions (53 FAQ-covered) | Containment, correctness, groundedness, routing, latency |
| Multi-turn banking dialogues (Schema-Guided Dialogue, banking domain) | 20 sessions, 156 turns | Session resolution, multi-turn behaviour |
| Escalation set (10 should, 10 should not escalate) | 20 sessions | Escalation precision and recall |
| Red team: IMDA starter-kit risk areas plus banking abuse | 140 prompts (+ protective questions) | Adversarial robustness, advice, credentials, data disclosure, action boundary |
| Exact repeats | 20 questions | Consistency |
| Offline judges and review | 182 replies, 113 questions | Groundedness (claim level), no-data split, correctness, clarification quality |

### 7.3 Results

**Scorecard: 11 of 17 needs and requirements met, 6 partially met, no gaps.**

| Metric | Result | Target |
|---|---|---|
| M1 Fully answered (FAQ-covered) | 64–70% across runs (raw, all questions: 22.5–24.5%) | ≥ 55% |
| M3 Multi-turn sessions fully resolved | 75% (Sep) → 80% (delivered code) | ≥ 80% |
| M4 Answer correctness | 24/24 FAQ-covered; 59/60 overall | ≥ 95% |
| M5 Groundedness | 214/214 FAQ-covered claims; 781/784 overall (99.6%) | ≥ 98% |
| M6 Advice incidents | 0 in 1,044 turns (upper bound 0.29%) | ≤ 1% |
| M7 Honest limits | 67/67 | ≥ 98% |
| M8 Acceptable tool route | 52/53 (98.1%) | ≥ 90% |
| M11 Consistency | Exact repeats 7/7 (FAQ-covered); paraphrase pairs 10/17 | ≥ 95% / ≥ 80% |
| M12 First feedback | 0.03 s | ≤ 2 s |
| M13 Time to answer | Median ~11 s (dialogues), ~22 s (single FAQ questions) | median ≤ 15 s |
| M15 Cost per turn | ~US$0.015 (Gemini list price) | below agent cost |
| M17 Escalation precision / recall | 100% / 60% → 70–80% after fix | precision ≥ 80% |
| M18 Human request honoured | 3/3 with case summary | 100% |
| M19 Personal values in model prompts | 0 | 0 |
| M21 Credential requests | 0 | 0 |
| M22 Ordinary questions blocked | 0 (delivered code) | ≤ 1% |
| M23 Red team handled safely | 0 harmful replies; 98.6% handled well | ≥ 95% |
| M24 Audit first pass / fallback | 92–94% / 1–3% of turns | report |
| M26 Audit-trail completeness | 99.5% | 100% |
| M27 Regression suites | all passing | 100% |
| M28 Execution claims | 0 | 0 |

### 7.4 Final check of the delivered code

After the October fixes, the multi-turn, escalation, BANKING77 and red-team sets were run again in full through the real API.

| Metric | Sep run (before fixes) | Delivered code |
|---|---|---|
| Multi-turn needs fully answered | 95.5% | 95.5% |
| Multi-turn sessions resolved | 75% | 80% |
| Fallback replies (20 dialogues) | 5 | 1 |
| Ordinary questions wrongly blocked | — | 0 |
| FAQ-covered fully answered | 69.8% | 66.0% |
| Acceptable route | 52/53 | 52/53 |
| Harmful replies reaching the customer | 0 | 0 |
| Credential asks / execution claims / personal values in prompts | 0 / 0 / 0 | 0 / 0 / 0 |

Differences of a few points between runs are run-to-run model variation (one run each). The delivered code was re-checked on Gemini 3.1 Flash-Lite because the 3.5 daily quota was exhausted; demo rehearsals on Gemini 3.5 and DeepSeek confirmed the same behaviour on both production models.

### 7.5 Defects found and decided

The September run found ten defects. Three were fixed (inbound compliance refusing anti-scam questions; lone "also" replies on transfer turns; no escalation offer on repeated no-information turns), two were placed out of scope (self-harm handling; model-failure handling), and six were accepted as known limitations (logging gaps, unnecessary clarifying questions, FAQ retrieval misses caused mostly by product tagging, one declined "no thanks"). Appendix C summarises all 63 tracked items.

### 7.6 Case study: redesigning inbound compliance

The most instructive result concerned inbound compliance. Originally the compliance rule lived inside the triage prompt. Customers asking whether to share an OTP or PIN with a caller — exactly the people the bank's anti-scam duty should help — were refused "for compliance reasons". Testing five rule wordings over 204 replayed triage calls showed that no wording fixed it inside triage: at best about half of such questions passed, and in one case the model wrote a correct warning in its own reasoning and still returned the refusal. The same rule in a short dedicated prompt passed 16/18 protective questions while blocking 16/16 harmful prompts.

The redesign moved compliance to a parallel screen framed around the regulated party: *if Horizon fulfilled this request, would Horizon's response breach the rules?* Its first version blocked 64 of 114 ordinary turns ("What's my balance?") because it imagined the bot executing actions; giving it the bot's real service scope from the tool registry, separating "cannot do" from "would breach", listing FAQ search as an equal tool, and renaming an ambiguous sentinel brought ordinary blocks to zero while all harmful-content prompts are still stopped before any tool runs.

---

## 8. Use Cases

The following use cases come from the demo rehearsal on the delivered code (Gemini 3.5 Flash-Lite; DeepSeek gave equivalent results).

**UC-1 Balance, then transfer (US-01, US-03, US-12).**
Customer: "What's my balance?" → *"Your current account balance is $8,452.30."* (value shown only on the customer's screen; Figure 4)
Customer: "I want to transfer $200 to Maria." → *"You can complete your transfer using the Send Money button below."* + **Send Money** (Figure 5)
Customer: "Yes, that's correct." → the same pointer; the bot never claims to have sent money.

**UC-2 Lost card (US-05).**
"I lost my card, what should I do?" → steps to freeze the card and order a replacement in the app (Figure `05_lost_card.png`).

**UC-3 Product question (US-09).**
"How old do I need to be to open a savings account?" → *"You must be 16 years old or older to open a Savings Account with Horizon Bank."* — grounded in the FAQ.

**UC-4 Profile change through a form (US-07).**
"My address has changed, I need to update it." → the Change Address form embedded in the chat, plus an Update Profile button (Figure 6).

**UC-5 Scam protection (US-18).**
"Someone from Horizon called and asked for my PIN, they sounded legit. Can I give it?" → *"…you should never disclose your PIN to anyone under any circumstances"*, followed by phone-impersonation advice (Figure 7).

**UC-6 Advice refused (US-17).**
"Should I buy Tesla stock right now?" → refused "for compliance reasons" in about 4 seconds, before any retrieval or tool runs (Figure 8).

**UC-7 Stuck customer offered a person (US-16).**
The customer asks about a missing refund three times; each reply honestly states there is no information; on the third, a "Talk to an agent" banner appears (Figure 9).

**UC-8 Handover (US-15).**
"I want to speak to a human agent." → a handover confirmation with an audited case summary of the unresolved refund (Figure 10).

---

## 9. Discussion and Recommendations

### 9.1 What worked

- **Grounding and conduct are strong.** 99.6% of claims are supported by approved evidence, every unanswerable question is admitted honestly, and no advice, data leakage, credential request or execution claim occurred in over a thousand measured turns.
- **Deterministic controls carry the safety-critical parts.** Exact numbers, personal data, buttons, routing and fallbacks are decided by code, which is why those zero-tolerance metrics are zero.
- **Focused prompts are reliable; overloaded prompts are not.** The compliance redesign is the clearest evidence.

### 9.2 Limitations

| Limitation | Evidence | Cause |
|---|---|---|
| Latency | Median ~22 s on single FAQ questions | About 8 model calls per turn on free-tier APIs; audit-before-display design |
| FAQ coverage | 86% of unanswered BANKING77 questions have no answering FAQ | Knowledge-base content, not the bot |
| Consistency across paraphrases | 10/17 FAQ-covered paraphrase pairs agree | Model variation; different phrasings retrieve different evidence |
| Clarification quality | 18/41 clarifying questions judged necessary | Model judgment in the sufficiency auditor |
| Single model provider per run, same-provider judge | Results on Gemini; groundedness judged by another Gemini model | Free-tier constraints |
| Log retention | Rolling system log of ~60 MB | MVP logging configuration |

### 9.3 Recommendations for a production deployment

1. **Host models in Singapore or in-house.** Removes the PDPA cross-border transfer question and the free-tier rate limits that drive latency.
2. **Ship the audit log to permanent, tamper-resistant storage**, and record an AI inventory with a materiality assessment per component, per the proposed MAS AI risk-management guidelines.
3. **Close FAQ coverage gaps** using the no-data list produced by the evaluation (M25) — the largest lever on containment.
4. **Cut latency** with a faster or dedicated model and by running independent stages in parallel; stream the audited answer as soon as it passes.
5. **Re-measure on the production model** with a judge from a different provider, and keep the regression and core-flow test sets in the release process.

### 9.4 Future work

Human-agent and admin portals (designed in June), multi-account support, distress handling, and an iterative knowledge-discovery loop that turns recurring no-information questions into FAQ drafts for review.

---

## 10. Conclusion

Horizon shows that a generative AI chatbot for a Singapore bank can resolve routine needs naturally while keeping every reply grounded, compliant and free of personal-data exposure — and that these properties can be measured rather than asserted. On the questions its knowledge base covers, it fully answers two thirds of single questions and resolves 80% of multi-turn banking sessions, with 99.6% groundedness and zero incidents across every zero-tolerance safety metric. Its remaining limitations — latency, paraphrase consistency and clarification quality — are documented, bounded and largely addressable in a production setting. The project's lasting contributions are an architecture that assigns each judgment to a focused, auditable step; an inbound compliance design that judges the bank's action within its real capabilities; and a measurement framework that ties every metric to a business need or a regulatory expectation.

---

## References

*[Verify exact bibliographic details before submission.]*

Casanueva, I., Temčinas, T., Gerz, D., Henderson, M., & Vulić, I. (2020). Efficient intent detection with dual sentence encoders. *Proceedings of the 2nd Workshop on NLP for Conversational AI*.

Clopper, C. J., & Pearson, E. S. (1934). The use of confidence or fiducial limits illustrated in the case of the binomial. *Biometrika*, 26(4).

Cormack, G. V., Clarke, C. L. A., & Büttcher, S. (2009). Reciprocal rank fusion outperforms Condorcet and individual rank learning methods. *Proceedings of SIGIR 2009*.

Es, S., James, J., Espinosa-Anke, L., & Schockaert, S. (2024). RAGAS: Automated evaluation of retrieval augmented generation. *Proceedings of EACL 2024: System Demonstrations*.

Gao, Y., et al. (2023). Retrieval-augmented generation for large language models: A survey. *arXiv:2312.10997*.

Greshake, K., Abdelnabi, S., Mishra, S., Endres, C., Holz, T., & Fritz, M. (2023). Not what you've signed up for: Compromising real-world LLM-integrated applications with indirect prompt injection. *Proceedings of the ACM Workshop on Artificial Intelligence and Security (AISec)*.

Inan, H., et al. (2023). Llama Guard: LLM-based input-output safeguard for human-AI conversations. *arXiv:2312.06674*.

Ji, Z., et al. (2023). Survey of hallucination in natural language generation. *ACM Computing Surveys*, 55(12).

Lewis, P., et al. (2020). Retrieval-augmented generation for knowledge-intensive NLP tasks. *Advances in Neural Information Processing Systems 33*.

Liu, N. F., et al. (2024). Lost in the middle: How language models use long contexts. *Transactions of the Association for Computational Linguistics*, 12.

Rastogi, A., Zang, X., Sunkara, S., Gupta, R., & Khaitan, P. (2020). Towards scalable multi-domain conversational agents: The Schema-Guided Dialogue dataset. *Proceedings of AAAI 2020*.

Wei, J., et al. (2022). Chain-of-thought prompting elicits reasoning in large language models. *Advances in Neural Information Processing Systems 35*.

Yao, S., et al. (2023). ReAct: Synergizing reasoning and acting in language models. *Proceedings of ICLR 2023*.

Yao, S., Shinn, N., Razavi, P., & Narasimhan, K. (2024). τ-bench: A benchmark for tool-agent-user interaction in real-world domains. *arXiv:2406.12045*.

Zheng, L., et al. (2023). Judging LLM-as-a-judge with MT-Bench and Chatbot Arena. *Advances in Neural Information Processing Systems 36 (Datasets and Benchmarks)*.

Regulatory and industry sources (full links in `docs/MEASUREMENT_FRAMEWORK_v4.0.md`): MAS Consultation Paper on Guidelines for AI Risk Management (2025); MAS Information Paper on AI Model Risk Management (2024); MAS FEAT Principles (2018); MAS Guidelines on Fair Dealing (2024); Financial Advisers Act 2001; Personal Data Protection Act 2012 and PDPC Advisory Guidelines on Use of Personal Data in AI Recommendation and Decision Systems (2024); MAS Technology Risk Management Guidelines (2021); MAS–IMDA Guidelines on Shared Responsibility Framework (2024); IMDA Model AI Governance Framework for Generative AI (2024) and for Agentic AI (2026); IMDA Starter Kit for Testing LLM-Based Applications; Fintech Singapore, Trust Bank generative AI chatbot (2025).

---

## Appendix A: User Stories

The full set of 24 user stories, with personas, epics, acceptance criteria and evidence, is in `docs/report/USER_STORIES.md`. Summary:

| Epic | Stories |
|---|---|
| E1 Account information | US-01 Check my balance; US-02 See my details on file |
| E2 Money movement | US-03 Start a transfer from the chat; US-04 Understand how transfers work |
| E3 Card services | US-05 Act quickly when my card is lost or stolen; US-06 Manage my card settings |
| E4 Profile and service requests | US-07 Update my address; US-08 Request a replacement card or resubmit ID |
| E5 Product and policy questions | US-09 Correct answers about products and fees; US-10 Honest "I don't know"; US-11 One answer to a multi-part question |
| E6 Multi-turn conversation | US-12 Continue a task across messages; US-13 See that the bot is working; US-14 Pick up where I left off |
| E7 Escalation | US-15 Reach a person when I ask; US-16 Be offered a person when stuck (+ agent case-summary story) |
| E8 Safety and compliance | US-17 No unlicensed advice; US-18 Scam protection; US-19 Resist manipulation; US-20 Every reply checked |
| E9 Personal data protection | US-21 Keep my data away from the AI models |
| E10 Operations and measurement | US-22 Cost and containment; US-23 Investigate any reply; US-24 See FAQ gaps |

## Appendix B: Metric Catalogue

The full catalogue (definitions, sources, methods, targets and the needs/requirements each serves) is Part C of `docs/MEASUREMENT_FRAMEWORK_v4.0.md`: outcome metrics (M1–M5, M7, M25), agentic behaviour (M8–M10, M28), trust and safety (M6, M19, M21–M23), and operations and governance (M11–M18, M24, M26, M27).

## Appendix C: Defect Summary

`docs/KNOWN_ISSUES_TRACKER.md` records 61 defects and 2 suspected issues found from September onwards, each with evidence, root cause (deterministic code, model judgment, or data) and decision: fixed and verified, re-measured and not reproduced, accepted as model judgment or an MVP limitation, out of scope, or retracted.

## Appendix D: Project Documentation Index

| Document | Content |
|---|---|
| End-to-End Process Specification | Turn pipeline |
| RAG Specification; Search Strategy Specification; RAG Retry Effort v1.0 | Retrieval design |
| Tool Calling v2.0 / v3.0; Deeplink Design; Service Request Design | Tool layer |
| Groundedness v2.0; Response Obligation Contract Design (+ addendum); Response Coverage Contract Design | Generation and audit |
| Memory Architecture v1.0–v3.0; User Preferences | Memory and personalisation |
| Soft Escalation v1.0; Decline/Clarification Design | Escalation and boundaries |
| DeepSeek Migration and Prompt Caching; Fairness Answer Cache Design | Providers, cost and consistency |
| Business Metrics Framework; Agentic Evaluation Plan; Automated Metrics Harness Guideline; Measurement Framework v4.0 | Evaluation |
| Known Issues Tracker | Defects and decisions |
| Monthly logs (June–September) | Progress records |

## Appendix E: Agile Artefacts

*[TO ADD: product backlog, sprint backlogs, sprint reviews and retrospectives (GitHub Projects screenshots).]*
