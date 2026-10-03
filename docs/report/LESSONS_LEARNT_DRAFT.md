# Lessons Learnt: Final (draft)

*Draft for Wu Qianzong to revise. Lessons from his notes are unmarked. Lessons marked **[added for review]** were drawn from the project history; keep, edit or delete them.*

The Horizon chatbot began in June as a single prompt answering account-opening questions. It ended in October as a multi-agent pipeline of about ten specialised model calls, with a measurement framework grounded in Singapore regulation. Most of what I learnt came from things that did not work the first time. Below are the lessons, grouped by area. Each has the evidence behind it and what I would do differently.

## 1. Prompt engineering

**1.1 A prompt should do one task, and its sub-tasks must be of the same kind.** I first put the inbound compliance check inside the triage prompt, which also resolved context, decomposed the request into needs and detected escalation. The compliance judgment kept failing: customers asking "should I share my OTP with a caller?" were refused as non-compliant. In one test the model wrote a correct answer ("No, never share your OTP") in its own reasoning and still returned the refusal verdict. No rewording fixed it inside triage; at best about half of such questions passed. Moved into its own short prompt, the same rule worked: 16 of 18 protective questions passed and all harmful prompts were still blocked. Classification, decomposition and compliance prediction are different kinds of reasoning, and mixing them dilutes the model's attention. Now, before adding a rule to a prompt, I ask whether it is the same kind of task as everything else in that prompt.

**1.2 Make the model reason before it decides.** Structured outputs are generated in field order, so a verdict written before its reason is a guess followed by a justification. Placing a reasoning field first changed outcomes measurably: the sufficiency auditor's `reason`, the formulator's `mapping_reason`, and the compliance screen's `requested_from_horizon` (what is the customer actually asking for?) ahead of its prediction. Adding that last field raised correctly passed protective questions from 40/60 to 46/60, with harmful prompts still blocked 80/80.

**1.3 Prompt-cache engineering saves real money, but the prompt must stay coherent.** Moving to DeepSeek, I reordered every prompt so the fixed rules come first and the per-turn data last. Cache hits rose to about 80–89% and input cost fell sharply. Reordering is not free, though: rules must still read as one coherent instruction, and data must not be referenced before it appears. Cache-friendly layout is a design constraint to plan for from the start, not a late optimisation.

**1.4 Models are bad at prohibition.** Telling a model "never do X", especially with an example of X, puts X at the centre of its attention, and it sometimes does exactly that. Describing the desired behaviour and the principle behind it works better than listing forbidden outputs.

**1.5 Write prompts as principles, not case-by-case rules.** Each failing case tempts you to add a rule or an example copied from it. That fixes the case and breaks a neighbour, and the prompt grows into a patchwork. When the formulator mixed two needs in one search ("replace my ugly card and change my address"), I added the principle "search for the need itself, in general, non-personal terms", not a rule about ugly cards. Principles generalise; patches accumulate.

**1.6 [added for review] Plain, short wording beats elaborate wording.** Long qualified sentences ("carries no details of the request… limited to the listed fields of the logged-in customer") confused the model more than they helped. "An app screen is only a redirect: the transaction takes place on the bank app's own page" did the job.

**1.7 [added for review] Words that look alike confuse the model.** The compliance screen used the sentinel "NONE" to mean "prohibited". When the model concluded that "none of the tools fits" a request, it wrote NONE and wrongly blocked ordinary questions such as "I want a virtual card". Renaming the sentinel to "PROHIBITED" fixed it. Every label in a prompt or schema should mean exactly one thing.

## 2. Working with models

**2.1 Model variation is inevitable, but manageable by how work is split across prompts.** The same question can get different outcomes run to run (exact repeats agreed 16/20 in September). Prompt tuning cannot remove this. What reduced it was architecture: giving each judgment to a focused call, deciding deterministically wherever possible (routing, fallbacks, number substitution), and caching consensus answers. Concentrating work in one prompt multiplies variance; distributing it contains it.

**2.2 Placeholders should carry meaning.** Personal data and exact numbers are replaced by placeholders before any model call, so the model never sees real values. Opaque tokens made the model treat them as missing or broken. Tokens and field names whose words describe what they stand for, such as calling clarification options `clarification_guesses` rather than `candidate_replies`, let the model reason about them correctly. Models work on semantics, so a name is part of the prompt.

**2.3 [added for review] A stronger model is not a substitute for the right information.** Several wrong decisions (the sufficiency auditor calling an address "unavailable" right after it was fetched; the compliance screen imagining the bot executing transfers) came from the model not knowing something: the other tools' results, the bot's real service scope. Supplying the missing context fixed them; changing the model would not have.

## 3. Retrieval (RAG)

**3.1 Cosine similarity means similar in form, not the same in meaning.** A query mixing two needs, or wrapped in personal context, retrieves documents that resemble its wording rather than answer its need. Retrieval quality depends on what you search for as much as on how.

**3.2 Reciprocal Rank Fusion helps in selecting candidates.** Fusing the rankings of several query variants surfaced the right FAQ more reliably than any single ranking.

**3.3 Direct search is usually the best approach.** Searching with the customer's need stated directly gave less drift and less loss of meaning than rewritten queries. The formulator now writes a dedicated `search_query`, the need itself without reasons or personal details: "like telling a librarian what you want, not why".

**3.4 Three-angle matching of answer representations sometimes helps, at a higher cost.** Rephrasing the query from several angles occasionally retrieved an answer the direct query missed. It costs extra calls and can drift, so it is now a fallback, not the default.

**3.5 [added for review] Separate data problems from bot problems before fixing anything.** Of the BANKING77 questions the bot could not fully answer, 86% had no answering FAQ at all. No change to the bot can fix that, and measuring the bot on those questions only distorts its score. I now report performance on questions the FAQ covers and treat coverage as a content backlog. Some misses that looked like retrieval failures were FAQ tagging problems (a physical-card FAQ tagged only as credit card); the fix there was the data, not the code.

## 4. Auditing and compliance

**4.1 Inbound and outbound audits have different jobs.** The outbound audit checks a reply that exists. The inbound check must *predict* whether fulfilling a request would be non-compliant, before any reply is written. That is much harder than I expected.

**4.2 The regulated party is the bank, not the customer.** A customer cannot be non-compliant; MAS regulates Horizon. Framing the inbound check as "is this customer suspicious?" refused people asking for protection. Framing it as "if Horizon fulfilled this, would Horizon's response breach the rules?" answered them. That reframing was the key to the redesign.

**4.3 The inbound check needs to know what the bot can actually do.** The first version of the new compliance screen blocked 64 of 114 ordinary turns, including "What's my balance?". It imagined the bot executing transfers and accessing accounts, and judged that against the conduct rules. Giving it the bot's real service scope (built from the same tool registry the planner uses) and separating "cannot do" from "would breach" fixed it. A compliance judge without context judges the wrong thing.

**4.4 The outbound audit works better as one prompt.** Splitting groundedness and conduct checks across separate calls caused replies to bounce: one check's revision broke the other's. A combined audit, with all findings revised at once, converged faster and fell back less.

**4.5 Generation guidance is a good design for both auditing and generation.** The sufficiency auditor writes a short `generation_guidance` with each verdict, for example "admit there is no information on X, then mention Y". The generator follows it and the audit checks against it. That gives each stage a shared, explicit contract instead of each re-deriving what the reply should say.

## 5. Engineering practice and project management [added for review]

**5.1 Test sets must include the ordinary cases, not just the hard ones.** My compliance-screen tests used harmful prompts and protective questions but almost no plain balance or transfer requests, so the version that blocked core banking flows passed my tests. Every test set now includes a core-flow control group.

**5.2 Measure what you will report, and say how you measured it.** The first containment figure (22.5%) mixed questions the FAQ cannot answer with ones it can. Reporting on the FAQ-covered subset (66–70%), with the raw number beside it, is both fairer and more honest.

**5.3 Logging is what made debugging possible.** Every turn logs its full decision chain. Almost every root cause in this project (63 tracked issues) was found by reading those logs rather than by guessing. Log retention, by contrast, was too short (about 60 MB of rolling logs), so the test harness had to save its own copies.

**5.4 Fix root causes, and decide scope deliberately.** Each defect was traced to its cause before any fix was chosen, and several were consciously put out of scope (self-harm handling, model-failure handling, multi-account support) to protect the delivery. Saying "not in this MVP" clearly is as important as fixing.

**5.5 Free-tier infrastructure shapes the project.** Gemini's per-minute and daily limits stalled test runs, and running two batches in parallel only made it worse. I added a second provider (DeepSeek), key rotation and a clear "model unavailable" message. Operational limits belong in the plan from day one.

## 6. What I would do differently

- Write user stories and a measurement plan in week one, and test against them continuously, rather than designing the metrics framework near the end.
- Give each prompt one job from the start, instead of discovering the limit through failures.
- Build a small regression set of core flows early and run it before every change.
- Plan for model and provider limits (quota, latency, cost) as part of the architecture.
