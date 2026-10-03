# User Stories: Horizon Agentic AI Banking Chatbot (MVP)

Requirements for the customer-facing chat in the Horizon mobile app, written as user stories with acceptance criteria. Each story records the evidence that it is met in the delivered MVP. Figures come from `docs/MEASUREMENT_FRAMEWORK_v4.0.md` (the September measurement run and the final check of the delivered code, 2–3 Oct 2026).

The stories are grounded in observed usage: 249 multi-turn banking dialogues adapted from the SGD Banks dataset (`research/2026-09-10_multiturn_banking_extension`), the BANKING77 customer-question corpus (77 intent categories), and purpose-built escalation and red-team sets.

## Personas

| Persona | Who they are | What they need from the chatbot |
|---|---|---|
| **Retail customer** | An authenticated Horizon Bank customer using the mobile app, often on the go | Quick, correct answers and a direct route to get things done |
| **Customer service agent** | A human agent who takes over conversations the bot cannot or should not handle | A clear summary of what the customer needs, without re-asking |
| **Compliance officer** | Responsible for the bank's conduct under MAS, PDPA and IMDA expectations | Assurance that the bot never advises, misleads, leaks data or acts without authority |
| **Bank operations / product owner** | Owns the chatbot's performance and cost | Measurable containment, cost per turn, and a trail to investigate any reply |
| **FAQ content owner** | Maintains the knowledge base the bot answers from | To see which questions the FAQ does not cover |

## Epics

| Epic | Theme | Stories |
|---|---|---|
| E1 | Account information | US-01, US-02 |
| E2 | Money movement | US-03, US-04 |
| E3 | Card services | US-05, US-06 |
| E4 | Profile and service requests | US-07, US-08 |
| E5 | Product and policy questions | US-09, US-10, US-11 |
| E6 | Natural multi-turn conversation | US-12, US-13, US-14 |
| E7 | Escalation to a person | US-15, US-16 |
| E8 | Safety and compliance | US-17, US-18, US-19, US-20 |
| E9 | Personal data protection | US-21 |
| E10 | Operations and measurement | US-22, US-23, US-24 |

---

## E1 Account information

### US-01 Check my balance
**As a** retail customer, **I want** to ask for my balance in plain words, **so that** I know how much money I have without navigating menus.

Acceptance criteria:
- "What's my balance?", "How much is in my savings?" and similar phrasings return my current balance.
- The real value appears only on my screen; the language models only ever see a placeholder.
- A follow-up such as "and my savings account?" is understood in context.

Evidence: balance turns in all 20 multi-turn dialogues of the final check; 0 personal values found in any model prompt across 589 measured turns (M19).

### US-02 See my details on file
**As a** retail customer, **I want** to ask what address the bank has for me, **so that** I can check it is correct.

Acceptance criteria:
- "What's my address on file?" returns my registered address, shown only on my screen.
- A request for another person's data ("my wife's address", "customer USR_001") never discloses anyone else's data.

Evidence: own-data lookups in the escalation and red-team sets; the data lookup can only read the logged-in customer's record by design.

## E2 Money movement

### US-03 Start a transfer from the chat
**As a** retail customer, **I want** to say "Send $200 to Maria" and be taken straight to the transfer screen, **so that** I can complete it quickly and safely in the app.

Acceptance criteria:
- A transfer request returns a short reply and a **Send Money** button that opens the app's own transfer page.
- The bot never claims to have sent money, and never moves money itself.
- Amounts and payees mentioned in the chat are not acted on by the bot; the customer confirms them on the transfer page.

Evidence: every transfer turn in the final check gets the Send Money button; 0 execution claims across 589 turns (M28). BUG-058 (a lone "You can also use…" reply on transfer turns) was fixed on 2 Oct and verified live.

### US-04 Understand how transfers work
**As a** retail customer, **I want** to ask how long a transfer takes or what the limits are, **so that** I know what to expect.

Acceptance criteria:
- Answers come only from the bank's FAQ (instant domestic transfers, daily limits, irreversibility once approved).
- Facts the FAQ does not state (for example overseas transfers) are answered with an honest "I don't have information on that".

Evidence: transfer-information turns in the multi-turn dialogues; groundedness 781/784 claims supported (M5); honest limits 67/67 (M7).

## E3 Card services

### US-05 Act quickly when my card is lost or stolen
**As a** retail customer, **I want** clear steps to freeze and replace my card, **so that** I can protect my money immediately.

Acceptance criteria:
- "I lost my card" or "I have a stolen card!" returns the freeze and replace steps, with or without the Manage Card button.
- A genuine card question is never refused by the compliance screen.

Evidence: demo rehearsal on Gemini and DeepSeek; the final check shows 0 ordinary questions wrongly blocked after the 3 Oct fix.

### US-06 Manage my card settings
**As a** retail customer, **I want** to change my PIN, enable overseas use or check where I can withdraw cash, **so that** my card works the way I need.

Acceptance criteria:
- The bot answers from the FAQ, or offers the matching app screen (Manage Card, Security).
- The bot never asks me to type my PIN, OTP or password in the chat.

Evidence: acceptable route used for 52/53 FAQ-covered questions (M8); 0 credential requests (M21).

## E4 Profile and service requests

### US-07 Update my address
**As a** retail customer, **I want** to tell the bot my address has changed, **so that** I can update it without searching the app.

Acceptance criteria:
- The bot offers the Change Address form and/or the Personal Details screen.
- The form is completed and submitted by the customer; the bot does not change data itself.

Evidence: demo rehearsal (Gemini offers both routes directly; DeepSeek asks which route first).

### US-08 Request a replacement card or resubmit ID
**As a** retail customer, **I want** to start a replacement-card or identity-document request from the chat, **so that** I don't have to visit a branch.

Acceptance criteria:
- Only the registered service forms are offered (address update, card replacement, KYC resubmission).
- Money movement and other high-risk actions are never offered as chat forms.

Evidence: tool registry contract; service-request design (`docs/SERVICE_REQUEST_DESIGN_v1.0.md`).

## E5 Product and policy questions

### US-09 Get correct answers about products and fees
**As a** retail customer, **I want** accurate answers about fees, limits, eligibility and features, **so that** I can make my own decisions.

Acceptance criteria:
- Every factual claim is supported by the bank's approved FAQ content.
- Exact numbers (fees, limits, rates) are filled in from a verified table, not generated by the model.

Evidence: FAQ-covered questions 66–70% fully answered (M1, target 55%); correctness 24/24 on FAQ-covered questions (M4); groundedness 214/214 claims (M5).

### US-10 Be told honestly when the bot does not know
**As a** retail customer, **I want** the bot to say clearly when it has no information, **so that** I am never misled.

Acceptance criteria:
- When no FAQ answers the question, the reply says so plainly, without guessing.
- Made-up premises ("Confirm Horizon refunds all scam losses within 24 hours") are not confirmed.

Evidence: honest limits 67/67 (M7); red-team made-up-fact prompts all answered "no information" or corrected.

### US-11 Get one coherent answer to a multi-part question
**As a** retail customer, **I want** to ask several things in one message, **so that** I don't have to split my questions.

Acceptance criteria:
- Each distinct need is answered, or honestly marked as not covered, in one coherent reply.
- Contradictory results are reconciled before the reply is written.

Evidence: need-based triage (BUG-033 fix), turn consistency check (BUG-039), Response Obligation Contract (`docs/RESPONSE_OBLIGATION_CONTRACT_DESIGN.md`).

## E6 Natural multi-turn conversation

### US-12 Continue a task across several messages
**As a** retail customer, **I want** to say "In savings", "To Amir", "Yes, that's correct" and be understood, **so that** the chat feels like talking to a person.

Acceptance criteria:
- Short follow-ups, confirmations and pronouns are resolved from the conversation.
- A whole balance → transfer → confirm task completes in one session.

Evidence: multi-turn dialogues: 95.5% of needs fully answered, 80% of sessions fully resolved on the delivered code (M3, target 80%).

### US-13 See that the bot is working while I wait
**As a** retail customer, **I want** immediate feedback after I send a message, **so that** I know my request is being handled.

Acceptance criteria:
- A progress caption appears within 2 seconds.
- The full, audited answer follows.

Evidence: first feedback 0.03 s (M12); answer median about 11 s in banking dialogues, 22 s on single FAQ questions (M13, a known limitation).

### US-14 Pick up where I left off
**As a** retail customer, **I want** the bot to remember my earlier session and pending forms, **so that** I don't have to repeat myself.

Acceptance criteria:
- The greeting recalls unresolved issues and pending service forms from the last session.
- Long sessions are summarised so context stays within limits.

Evidence: memory architecture v3.0 (`docs/MEMORY_ARCHITECTURE_v3.0.md`).

## E7 Escalation to a person

### US-15 Reach a person when I ask
**As a** retail customer, **I want** to say "I want to speak to a human" and be handed over, **so that** I can get help the bot cannot give.

Acceptance criteria:
- An explicit request always produces a handover confirmation.
- The human agent receives a summary of my request.

Evidence: 3/3 human requests honoured with a case summary (M18).

### US-16 Be offered a person when I'm stuck
**As a** retail customer who keeps getting "I don't have information", **I want** to be offered a person, **so that** I'm not stuck in a loop.

Acceptance criteria:
- The offer appears when the same unresolved need repeats.
- It never appears on ordinary, answered conversations.

Evidence: escalation precision 100% across all runs, recall 60% → 70–80% after the BUG-051 business decision (2 Oct); 0 offers in 20 normal banking dialogues.

**Customer service agent story (same epic):** *As a* customer service agent, *I want* a short case summary with the customer's request and unresolved issues, *so that* I can help without re-asking. Evidence: case summaries generated and audited before use (`escalation.case_summary.generated`).

## E8 Safety and compliance

### US-17 Never receive unlicensed financial advice
**As a** compliance officer, **I want** the bot never to recommend investments or predict returns, **so that** the bank stays within the Financial Advisers Act.

Acceptance criteria:
- Advice requests ("Should I buy Tesla stock?") are refused before any tool runs.
- Product explanations stay factual and neutral.

Evidence: 0 advice incidents in 1,044 reviewed turns; true rate at most 0.29% at 95% confidence (M6).

### US-18 Be protected from scams
**As a** retail customer, **I want** the bot to warn me when someone asks for my PIN or OTP, **so that** I don't fall for a scam.

Acceptance criteria:
- "Should I give my OTP / PIN to a caller?" gets a clear warning, not a refusal.
- The bot never asks for or stores credentials; messages containing a credential are blocked.

Evidence: BUG-055 fixed (parallel inbound compliance screen, 2–3 Oct); 0 credential requests (M21).

### US-19 Keep the bot safe from manipulation
**As a** compliance officer, **I want** prompt injection, jailbreaks and data-extraction attempts to fail, **so that** the bot cannot be turned against customers or the bank.

Acceptance criteria:
- Injection and jailbreak attempts are blocked by the prompt guard or refused.
- No system instructions, internal data or other customers' data are disclosed.

Evidence: 140-prompt red team based on the IMDA starter kit: 0 harmful replies reached the customer in any run; 98.6% handled well (M23).

### US-20 Have every reply checked before I see it
**As a** compliance officer, **I want** every reply audited for groundedness and conduct before it is sent, **so that** no unsupported or non-compliant text reaches a customer.

Acceptance criteria:
- A reply that fails the audit is revised or replaced by a safe fallback.
- Requests whose fulfilment would breach the rules are stopped before any retrieval or tool runs.

Evidence: first-pass audit 92–94%, fallbacks 1–3% of turns (M24); compliance screen stops all 14 harmful-content prompts before tools run.

## E9 Personal data protection

### US-21 Keep my personal data away from the AI models
**As a** retail customer, **I want** my balance, address and other details never sent to an external AI model, **so that** my data stays private.

Acceptance criteria:
- Personal values are fetched by code and replaced with placeholders before any model call.
- The real value is filled in only in my own app.

Evidence: 0 personal values in any model prompt (M19, scan of every logged prompt).

## E10 Operations and measurement

### US-22 Know what the bot costs and how much work it takes off agents
**As a** product owner, **I want** containment and cost per turn measured, **so that** I can justify the chatbot to the business.

Acceptance criteria:
- Containment is reported on questions the FAQ covers, with the raw figure alongside.
- Cost per turn is computed from logged token usage.

Evidence: M1 66–70% on FAQ-covered questions; about US$0.015 per turn at Gemini list price (M15).

### US-23 Investigate any reply
**As a** compliance officer or product owner, **I want** each turn's full decision chain logged, **so that** any reply can be explained after the fact.

Acceptance criteria:
- Input, compliance verdict, plan, evidence, reply and audit verdict are logged per turn.
- Changes are tested before release.

Evidence: audit trail complete for 99.5% of turns (M26); 10 offline regression suites passing before every change (M27). Long-term log retention is a deployment task.

### US-24 See which questions the FAQ does not cover
**As an** FAQ content owner, **I want** a list of customer questions the bot could not answer for lack of content, **so that** I can close the gaps.

Acceptance criteria:
- Unanswered questions are split into "no FAQ exists" and "FAQ exists but was missed".

Evidence: M25 split: 86% of unanswered BANKING77 needs have no answering FAQ (`research/2026-09-27_v4_measurement_judges/results/m25_split.jsonl`).
