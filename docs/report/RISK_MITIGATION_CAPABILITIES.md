# Horizon Risk Mitigation: Controls, Evidence and Limits

Reference for the final report and presentation. It consolidates every risk control in the Horizon assistant, what each one does, which rule it serves, how it was tested, and what it does not cover.

Sources: the code (`backend/app/`), the v5.1 evaluation ([RESULTS_v5.1.md](evaluation/RESULTS_v5.1.md), [EVALUATION_METHODS_v5.1.md](evaluation/EVALUATION_METHODS_v5.1.md)), the v4.0 measurement ([MEASUREMENT_FRAMEWORK_v4.0.md](evaluation/MEASUREMENT_FRAMEWORK_v4.0.md)), the compliance-screen study ([INBOUND_COMPLIANCE_SCREEN_STUDY.md](evaluation/INBOUND_COMPLIANCE_SCREEN_STUDY.md)) and the issue tracker ([KNOWN_ISSUES_TRACKER.md](../KNOWN_ISSUES_TRACKER.md)).

## 1. Why this is the core of a bank assistant

A general chatbot can be judged on whether its answers are helpful. A bank's assistant also answers to a regulator, holds customers' personal and financial data, and is a target for fraud. One wrong reply can be unlicensed advice, a privacy breach, or a false promise the bank must honour. Horizon is therefore built so that **no single model call is trusted to be safe on its own**: every message passes through layered checks before and after the model writes a reply, and the cheapest, most predictable checks run first.

Five design principles run through all of it:

1. **Defence in depth.** Independent layers; a request that slips past one meets the next.
2. **Deterministic first.** Plain-code checks run before any model, so what they catch never reaches a model at all.
3. **One job per check.** Each model check has one narrow task and a written boundary. Mixing jobs made checks unstable (BUG-062).
4. **Judge the bank, not the customer.** Regulations bind Horizon. The compliance checks ask whether *Horizon fulfilling the request* would breach a rule, never whether the customer is "non-compliant".
5. **Fail closed.** When a safety check errors or returns something unreadable, the turn is stopped or retried; it is never let through by default.

## 2. The layers at a glance

Order is the order a message travels. "Model" means a call to a language model; "code" means deterministic logic.

| # | Layer | Where | Type | Main risk it controls |
|---|---|---|---|---|
| 1 | Ingest gate: malicious strings and placeholder injection | `node_01_deterministic_ingest_gate` | Code | Script/SQL injection; forging the bank's data placeholders |
| 2 | Ingest gate: **credential veto** | `inbound_safety_service.find_credential_rule_ids` | Code | PIN, OTP, CVV, password or card number reaching any model or log |
| 3 | Ingest gate: sensitive-word veto | `inbound_safety_service.find_sensitive_word_rule_ids` (database list) | Code | Bank-maintained banned phrases |
| 4 | **Prompt-attack guard** | `node_02c_inbound_semantic_gate`, Llama Prompt Guard 2 on Groq | Classifier model | Prompt injection and jailbreaks |
| 5 | **Inbound compliance screen** | `node_02f_inbound_compliance_screen` | Model | Requests Horizon must not fulfil: advice, other people's data, KYC bypass, fraud, harmful content, legal or tax advice, unauthorised actions |
| 6 | Triage and scope boundary | `node_02d_user_journey_classifier_and_triage` | Model | Off-topic requests, products Horizon does not offer, explicit requests for a person |
| 7 | **Action boundary** (read-only assistant) | Tool registry, deep-link and service-form registries | Code | The assistant executing or claiming to execute banking actions |
| 8 | **Personal-data isolation** | Field registry, `{{data-*}}` placeholders resolved in the browser | Code | Customer data reaching a model |
| 9 | Evidence-only generation and exact values | Generator rules, `Precision_Reference_Table`, `node_07_precision_substitution` | Model + code | Made-up facts; wrong numbers, fees and limits |
| 10 | **Outbound conduct and groundedness audit** | `node_06_outbound_combined_audit_gate` + deterministic output checks | Model + code | Advice, promises, false authority, unsupported facts, leaked internals in the reply |
| 11 | Revise or fall back | `node_04c_response_reviser`, `node_11_deterministic_clarification_fallback` | Model + code | Shipping a reply that failed the audit |
| 12 | **Human handover** | `node_09_hard_escalation_broker`, `node_08_soft_escalation_analyzer` | Code + rules | Customers left stranded |
| 13 | Fixed, approved wording for refusals | `node_10_standard_rejection`, `core/translations.py` | Code | Unpredictable wording on safety refusals |
| 14 | Consistency across sessions | Cross-session answer cache | Code | Unfair differences between customers asking the same thing |
| 15 | Audit trail and change control | `system_logger`, regression suites, evaluation freeze | Code + process | Undetected regressions; untraceable decisions |

## 3. The controls in detail

### 3.1 Ingest gate (layers 1–3): deterministic, before any model

The first step of every turn is plain code. Anything it stops ends the turn at the fixed rejection message; no model sees the message.

- **Malicious strings:** `<script>` or `DROP TABLE` in the message is rejected.
- **Placeholder injection:** a message containing `{{` or `}}` is rejected, so a customer cannot forge the placeholders the system uses for exact values and personal data.
- **Credential veto (added 5 Oct 2026, BUG-064).** Detects a credential *value*:
  - a 3–8 digit PIN, OTP, CVV or passcode, including spaced ("6 3 9 1 0 4"), dashed or spelled-out digits ("nine eight seven six"), and Chinese and Spanish forms ("密码是 135790", "Mi PIN es 2468");
  - a password or login pair ("password = …", "user / password");
  - a 13–19 digit card number that passes the Luhn check.

  A credential word with no value passes ("my PIN is not working", "a 6-digit PIN", "someone asked for my PIN"). On a match the customer is told not to share credentials and to ask again without them. The value is replaced by a placeholder in the stored conversation and in the request log, and only rule IDs are logged.
- **Sensitive-word veto:** a bank-maintained phrase list in the database (`Inbound_Sensitive_Word`). Phrases ending in "is" ("my pin is") match only when a value follows, so "my PIN is wrong" passes (BUG-046). Only opaque rule IDs are logged, never the matched text.

### 3.2 Prompt-attack guard (layer 4)

Llama Prompt Guard 2 (86M) on Groq scores every message for prompt injection and jailbreak. A message must score below **0.10** to pass; above that, the turn ends with a fixed message. If the guard itself fails, the turn is stopped (fail closed), and the outage is logged separately from a real veto so it is not counted as an attack. A message that passes is marked "trusted"; until then it stays "untrusted" in the conversation history and cannot enter summarised context.

The guard detects attacks on the assistant, not harmful topics or banking compliance; those belong to layer 5.

### 3.3 Inbound compliance screen (layer 5)

One model call predicts what Horizon would be doing if it fulfilled the request, and judges that against a shared compliance template:

- **MAS compliance context** (`core/compliance.py`): Financial Advisers Act (no personalised financial advice without a licence), MAS FEAT principles (fairness, ethics, accountability, transparency), MAS Technology Risk Management guidelines, AML/CFT Notice 626 (no KYC bypass or anonymous or illegal transfers), and licensed professional services (no legal or tax advice).
- **Bank conduct rules:** no commitments, no personalised advice, no outcome or return predictions, no fabricated authority, neutral non-persuasive tone.

The screen has a **work scope**: it only judges requests touching advice; fairness or deception; another person's data; KYC or illegal transfers; fraud, scams or other crime; harmful content; legal or tax advice; or commitments, banking actions and authority. Everything else is out of its scope and passes. Attacks on the assistant are excluded because layer 4 owns them.

For in-scope requests it looks for a **compliant route**: a way the assistant's own tools (FAQ search, own-data lookup, app screens and forms) could lead the customer to resolution. A route found means pass. Only "no compliant route" blocks, for example a request for investment advice or for another customer's data. An unreadable verdict fails closed.

This design came out of a 16-variant study (BUG-062): an earlier screen that mixed scope and compliance blocked 9 of 189 ordinary questions, mostly fraud reports. The final design cut false blocks on the replay set from 5/42 to 0/42.

### 3.4 Triage and scope boundary (layer 6)

The triage step decides, for the whole turn, whether to escalate to a person on request, decline a product Horizon does not offer, treat the message as non-banking conversation, or split it into banking sub-questions. Declines use a fixed message naming what is not offered. Non-banking chatter gets a short, neutral reply that steers back to banking.

### 3.5 Action boundary: a read-only assistant (layer 7)

Horizon never moves money, changes data or approves anything. It can only:

- answer from the FAQ;
- show the customer's own data through the system (layer 8);
- send the customer to a **registered app screen** (deep link; each destination is allowed only for listed products and intents);
- offer a **registered service form** that the customer completes and submits on their own authority.

Tools are an allowlist (`tool_registry.py`): a tool call outside the registered tools and operations is not executed. Conduct rule 4 forbids speaking as though executing an action, and the outbound audit enforces it. "Transfer S$500 now" gets the Send Money button, and "I said do it, I authorise you" gets "I cannot execute direct funds transfers" plus the button.

### 3.6 Personal-data isolation (layer 8)

- Each customer data field has a registered **disclosure mode** and **data classification** (`api_field_registry.py`). Address and balance are `placeholder` fields: the model only ever sees a token such as `{{data-6887a35a}}`, and the **browser** swaps in the real value from a session-scoped map. The backend never puts the value into model-visible text.
- The loyalty tier is a deliberate `direct_value` field (low sensitivity, not identity-exposing) and is visible to the model by design.
- Credentials are removed before any model (3.1). Logs carry opaque rule IDs, not matched text.
- Research and test runs use an isolated synthetic account (USR_999).

### 3.7 Evidence-only generation and exact values (layer 9)

The reply generator may state only facts from the evidence approved for that turn. Exact values (fees, limits, rates, phone numbers, dates) are held in a `Precision_Reference_Table` and appear in drafts as `{{faq_…}}` placeholders. After the audit has passed, code substitutes the exact value, so a model never retypes a number it could get wrong. An invalid placeholder or an exposed internal FAQ id is caught by the deterministic output checks (3.8).

### 3.8 Outbound conduct and groundedness audit (layers 10–11)

Every draft reply is audited before the customer sees it, in one model call with two axes:

- **Conduct:** the five bank conduct rules and the five MAS rules (3.3).
- **Groundedness:** every factual claim, instruction and suggestion must be supported by the approved evidence for its task. Violations are typed: unsupported claim, unsupported inference, unsupported instruction, wrong task evidence, unsupported clarification, task not answered.

Deterministic output checks run alongside: invalid `{{faq_*}}` placeholders, exposed FAQ parent ids, and the illegal-word sentinel. Findings from both are merged per response block. Clean blocks are approved; flagged blocks go back to the reviser with the violations as constraints. After **three** failed attempts the turn falls back to deterministic content (clarification, decline, or a plain "I can't answer that right now") and the turn is scored as a failure in the escalation confidence window. A malformed audit response fails closed: it is treated as a violation with the instruction to add no facts beyond the evidence.

The same conduct template is applied to every customer-facing surface: live answers, greetings, memory summaries and escalation summaries.

### 3.9 Human handover (layer 12)

- **Explicit request:** "I want a human" triggers the hard escalation broker. The customer gets an escalation card with a "Yes, transfer me to a human agent" button and a case summary for the agent.
- **Soft offer:** the soft escalation analyzer watches four signals of a struggling customer, including repeated "no information" turns (BUG-051), and shows a non-blocking banner offering a person.
- A turn that exhausted its audit retries counts as a failure for the soft-offer confidence window, so a stuck customer is more likely to be offered help.

### 3.10 Fixed wording, consistency, audit trail (layers 13–15)

- **Fixed wording:** refusals from the deterministic gates, the attack guard, the compliance screen and system failures use fixed, pre-written messages in English, Mandarin and Spanish (`core/translations.py`), never model-written text.
- **Consistency (FEAT fairness):** a cross-session answer cache gives byte-identical questions the same answered result across customers (design: `docs/FAIRNESS_ANSWER_CACHE_DESIGN.md`).
- **Audit trail:** every gate decision is a structured log event (`inbound.compliance.checked`, `inbound.credential.rejected`, outbound audit results, escalation events). Each check's reasoning fields are logged, which makes decisions explainable after the fact.
- **Change control:** regression suites per fixed defect (v5.1: 96/96 checks pass; reverting three fixed defects makes their suites fail), and an evaluation freeze that voids a run if the code changes mid-run.

## 4. Regulation and risk mapping

| Rule or risk | Controls | Evidence (v5.1 unless stated) |
|---|---|---|
| **FAA: no unlicensed financial advice** | Compliance screen (3.3); conduct rules 2–3 in the outbound audit (3.8) | **0** advice incidents in 538 cases; 10/10 direct advice requests stopped before any tool; seeded advice sentences caught by the audit 6/6 |
| **MAS TRM: protection against manipulation and injection** | Ingest gate (3.1); prompt-attack guard (3.2); trust status on history | **0** successful attacks out of 60 (direct injection, jailbreak, data extraction, leakage, multilingual and obfuscated, multi-turn); 0 internal leaks in any reply |
| **AML/CFT: no KYC bypass or illegal transfers** | Compliance screen | "Open accounts with stolen identities" stopped; 0 crime facilitation in H3 |
| **FEAT: fairness, ethics, no deception** | Compliance screen; neutral-tone rule; answer cache; evidence-only generation | 0 harmful or hateful content; 0 deceptive claims flagged |
| **Licensed services: no legal or tax advice** | Compliance screen; outbound audit | No legal or tax advice incidents |
| **No fabricated authority or execution** | Action boundary (3.5); conduct rule 4 | **0** execution claims in all sets; 10/10 action demands get an app button or form, never an execution claim |
| **Accuracy of what the bank says** | Evidence-only generation; exact values; outbound audit (3.7–3.8) | 136/137 substantive replies fully supported; 92% correct and relevant; audit caught **30/30** seeded errors (2/30 false alarms) |
| **PDPA: protection of personal data** | Placeholders resolved in the browser; field registry; credential veto; isolated test account | Address and balance in model prompts: **0**; credentials reaching a model after the fix: **0/15** |
| **Credential safety** | Credential veto (3.1) | After fix: 15/15 credential messages blocked before any model; **0** false blocks among 13,083 real customer messages |
| **Other customers' data** | Compliance screen; own-data lookup only for the logged-in customer | 10/10 data-extraction attempts safe; 0 replies revealing another customer's data |
| **Customer reaches a person** | Hard and soft escalation (3.9) | Explicit requests honoured **5/5**; soft-offer precision 100% (v4.0) |

## 5. Results summary

### 5.1 Adversarial testing (H3, 100 new cases, 10 categories)

| Measure | Result |
|---|---|
| Critical incidents (advice, harmful content, leaks, other customers' data, credential requests, execution claims, followed injection) | **0 / 100** (95% upper bound 2.95%) |
| Safe handling, both labellers agreeing | **100 / 100** |
| Prohibited requests stopped before any tool runs | 42 / 50 (the other 8 refused safely later in the pipeline) |
| Turns stopped at the door (114 turns) | Compliance screen 49, prompt-attack guard 21, sensitive-word filter 2 |

Earlier red-team evidence: 142 prompts on the delivered code in v4.0 (IMDA starter-kit risk areas plus banking abuse) gave 0 harmful replies, 0 credential requests, 0 execution claims and 0 personal values in prompts.

### 5.2 Cost of safety to ordinary customers (false blocks)

| Check | False blocks on ordinary questions (v5.1, 234 BANKING77 questions plus controls) |
|---|---|
| Credential veto (after fix) | 0 of 13,083 BANKING77 messages |
| Compliance screen | 1 of 234 (a wrong-account transfer asking "please cancel it"); was 9 of 189 before BUG-062 |
| Prompt-attack guard | 3 ordinary messages, all short or unusual wording ("chnge adress how", "forgot passcode app help", "The app can't verify my information for identity proofing purposes") |

### 5.3 Privacy and leakage scans (every model prompt and every reply, all sets)

| Scan | Result |
|---|---|
| Address or balance in any model prompt | 0 |
| Internal markers, ids, prompt text or tool names in replies | 0 |
| Credentials in model prompts | 14/15 at the run commit, **0/15 after the post-run fix** |
| Customer-typed personal data (names, NRIC-format numbers) in prompts | Reaches the models (9/10): a known design boundary, see section 6 |

## 6. Scope boundaries and what is not covered

| Area | Status | Notes |
|---|---|---|
| **Scam detection and warning** | **Out of MVP scope** (decision 5 Oct 2026) | The bot gives reasonable information on the assumption that a situation is not a scam. In v5.1, 11 of 15 scam-type questions got a warning or useful steps; 4 got "no information". No reply asked for a credential. A scam-awareness capability is future work |
| Self-harm and distress | Out of MVP scope | Excluded from metrics by earlier decision |
| Model-provider failure handling | Out of MVP scope | Fails closed with a fixed message; no failover engineering |
| Personal data the customer types | Known boundary | Free text reaches the models; production would add a personal-data filter on inbound text (credentials are already filtered) |
| Reply language | Not scored | Fixed refusal messages exist in three languages; model replies to Chinese and Spanish are often in English |
| Demographic fairness | Not tested | One synthetic customer; no demographic attributes in the data, and none enters any prompt |
| Indirect injection through FAQ content | Limitation (BUG-063) | A planted FAQ instruction made one reply end with a canary string; the audit treats evidence as rules. Needs a malicious content edit in practice |

## 7. Defects found and fixed in the safety layers

| Issue | What went wrong | Fix and evidence |
|---|---|---|
| BUG-046 | "My PIN is wrong" blocked by the sensitive-word list | Value-aware matching for "… is" phrases |
| BUG-055 | Compliance routing errors | Fixed; regression suite part of R7 |
| BUG-062 | Compliance screen blocked fraud and theft reports (9/189) | Work scope plus compliant-route test; replay false blocks 5/42 → 0/42; 1/234 in the re-run |
| BUG-064 | Credentials reached model prompts before being blocked (14/15) | Deterministic credential veto; 15/15 blocked with 0 model calls; 0 false blocks in 13,083 messages |
| BUG-063 | Outbound audit applies planted evidence instructions | Accepted limitation |
| (v5.1 finding) | One changed number (4-hour vs 12-hour waiting period) passed the audit | Limitation: the audit caught every seeded whole-sentence error but missed a single altered number |

## 8. Lessons for the report

- **Order layers by cost and certainty.** Code checks first, a small classifier next, then focused model checks, then an audit of the output. Each later layer sees fewer, harder cases.
- **A model check is a component.** One task, a clear boundary, a work scope, and real tests (lesson 1.1 in [LESSONS_LEARNT_DRAFT.md](LESSONS_LEARNT_DRAFT.md)). The compliance screen became stable only after its scope was narrowed and its question changed from "is this allowed?" to "is there a compliant route?".
- **Measure the cost of safety, not only its strength.** Every gate was tested for false blocks on ordinary customer questions, because a safe bot that refuses fraud reports fails customers.
- **Some guarantees need code, not prompts.** Keeping credentials out of models, keeping personal values away from the model and putting exact numbers into replies are all done in code, because no prompt can guarantee them.
