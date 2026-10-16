# Literature Record

Every paper, regulation and industry source the project has studied, grouped by topic. For each source the record gives what it says that mattered to us and where the project used it. It is the working source list for the final report's literature review (section 3) and reference list.

How to read the entries:

- **Cited in report** means the source is already in the References of [FINAL_REPORT_DRAFT.md](FINAL_REPORT_DRAFT.md).
- **Not yet cited** means it was studied for a design decision or a bug and could be added.
- Where a source was found only by title and link during a study, the entry gives the title, link and arXiv ID, and not authors or years we have not checked.

Last updated 16 Oct 2026.

---

## 1. Retrieval-augmented generation (RAG) and retrieval

| Source | What it says that mattered | Where we used it | Status |
|---|---|---|---|
| Lewis, P., et al. (2020). Retrieval-augmented generation for knowledge-intensive NLP tasks. *NeurIPS 33*, 9459–9474. | Defines RAG: retrieve documents for each query and condition the answer on them. | Report 3.1: why we chose RAG over fine-tuning. | Cited in report |
| Gao, Y., et al. (2023). Retrieval-augmented generation for large language models: A survey. arXiv:2312.10997. https://arxiv.org/abs/2312.10997 | RAG is the main practical mitigation for hallucination. Retrieved context may be irrelevant or partial. | Report 3.1: RAG choice, and the reason for the sufficiency audit. | Cited in report |
| Ji, Z., et al. (2023). Survey of hallucination in natural language generation. *ACM Computing Surveys, 55*(12), 248. https://doi.org/10.1145/3571730 | Taxonomy of hallucination; grounding in sources as a mitigation. | Report 3.1; groundedness design ([GROUNDEDNESS_v2.0.md](../GROUNDEDNESS_v2.0.md)). | Cited in report |
| Cormack, G. V., Clarke, C. L. A., & Büttcher, S. (2009). Reciprocal rank fusion outperforms Condorcet and individual rank learning methods. *SIGIR '09*, 758–759. https://doi.org/10.1145/1571941.1572114 | Merges several rankings without calibrating scores. | Report 3.1: fusing results from several query variants ([RAG Specification](design/RAG%20Specification.md)). | Cited in report |
| Liu, N. F., et al. (2024). Lost in the middle: How language models use long contexts. *TACL, 12*, 157–173. https://doi.org/10.1162/tacl_a_00638 | Models use long, mixed contexts poorly, especially information in the middle. | Report 3.1: the evidence-selection step, and keeping each prompt narrow. | Cited in report |

## 2. Agents and orchestration

| Source | What it says that mattered | Where we used it | Status |
|---|---|---|---|
| Yao, S., et al. (2023). ReAct: Synergizing reasoning and acting in language models. *ICLR 2023*. https://openreview.net/forum?id=WE_vluYUL-X | A model interleaves reasoning with tool calls and chooses its own next step. | Report 3.2: the open-ended alternative we did not use; we chose a bounded graph instead. | Cited in report |
| Yao, S., Shinn, N., Razavi, P., & Narasimhan, K. (2024). τ-bench: A benchmark for tool-agent-user interaction in real-world domains. arXiv:2406.12045. https://arxiv.org/abs/2406.12045 | Evaluates tool-using service agents on end-to-end task completion in multi-turn dialogue. | Report 3.2: framing of the multi-turn study. | Cited in report |

## 3. Reasoning and structured outputs

| Source | What it says that mattered | Where we used it | Status |
|---|---|---|---|
| Wei, J., et al. (2022). Chain-of-thought prompting elicits reasoning in large language models. *NeurIPS 35*, 24824–24837. | Asking a model to reason before answering improves its judgments. | Report 3.3: every judging schema puts its reasoning field before its verdict. | Cited in report |
| Tam, Z. R., et al. (2024). Let me speak freely? A study on the impact of format restrictions on performance of large language models. *EMNLP 2024 Industry Track*. arXiv:2408.02442. https://arxiv.org/abs/2408.02442 | Strict output formats (JSON mode, schemas) reduce reasoning quality, while helping classification tasks. The order of keys in the format affects results. | BUG-070 and Lesson 2.5: why the reviser got worse under a schema while the auditor and writers did not. | Not yet cited |
| Park, K., et al. (2024). Grammar-aligned decoding. *NeurIPS 2024*. arXiv:2405.21047. https://arxiv.org/abs/2405.21047 | Constrained decoding guarantees valid output but distorts the model's own distribution, so the output can differ in quality from what the model would otherwise write. | BUG-070 and Lesson 2.5: a schema is not neutral; the decoder enforces only valid tokens. | Not yet cited |
| Practitioner write-ups on field order in structured output: DEV Community, "Why JSON schema field order breaks structured output accuracy" (https://dev.to/ji_ai/why-json-schema-field-order-breaks-structured-output-accuracy-2985); D. Castillo, "Gemini structured outputs" (https://dylancastillo.co/posts/gemini-structured-outputs.html); K. Aleksiev, "Does field ordering affect model performance?" (https://tiny.write.as/kallyaleksiev/does-field-ordering-affect-model-performance); GetCoAI, "Why field order may not improve model reasoning" (https://getcoai.com/news/why-field-order-may-not-improve-model-reasoning) | Under a schema, fields are generated in their declared order, so order changes results. The effect on reasoning is debated: some report gains from reasoning-first fields, others find little. | Background for BUG-070. Matches our result that moving the text field last barely helped (4 of 5 runs still failed) while a decide-first field helped somewhat (2 of 5). Blogs, not peer-reviewed: use as background only. | Not yet cited |

## 4. Safety, guardrails and over-refusal

| Source | What it says that mattered | Where we used it | Status |
|---|---|---|---|
| Greshake, K., et al. (2023). Not what you've signed up for: Compromising real-world LLM-integrated applications with indirect prompt injection. *AISec '23*, 79–90. https://doi.org/10.1145/3605764.3623985 | Prompt injection, including instructions hidden in retrieved content, is a real attack on LLM applications. | Report 3.4: why there is a dedicated prompt-attack layer. | Cited in report |
| Inan, H., et al. (2023). Llama Guard: LLM-based input-output safeguard for human-AI conversations. arXiv:2312.06674. https://arxiv.org/abs/2312.06674 | Dedicated classifier models police input and output more cheaply and consistently than a general model asked to police itself. | Report 3.4: the layered guard design. | Cited in report |
| Meta. (2025). Llama Prompt Guard 2 [Model card]. https://huggingface.co/meta-llama/Llama-Prompt-Guard-2-86M | Classifier for injection and jailbreak attempts; it does not target harmful content. | The inbound prompt-attack check (Groq); the reason fraud help and harmful content were added to the compliance screen's whitelist ([INBOUND_COMPLIANCE_SCREEN_STUDY.md](evaluation/INBOUND_COMPLIANCE_SCREEN_STUDY.md)). | Cited in report |
| Röttger et al. XSTest. Summary: https://api.emergentmind.com/topics/xstest | Hand-built suite of safe prompts that look unsafe; measures exaggerated safety. | BUG-062 compliance-screen study, section 4: over-refusal is a known failure. | Not yet cited |
| Cui et al. OR-Bench: an over-refusal benchmark. https://orbench-llm-or-bench.static.hf.space/index.html | 80,000 seemingly toxic but benign prompts; safety-tuned models refuse up to 80% of the hard ones. | BUG-062 study, section 4. | Not yet cited |
| Understanding and mitigating overrefusal in LLMs from an unveiling perspective of safety decision boundary. arXiv:2505.18325. https://arxiv.org/html/2505.18325v3 | Explains over-refusal as misclassification near the model's safety decision boundary. | BUG-062 study, section 4. | Not yet cited |
| Judge, retrieve, or abstain: Uncertainty-guarded LLM judging with provable risk guarantees. arXiv:2608.17994. https://arxiv.org/pdf/2608.17994 | A judge can decide, retrieve more evidence, or abstain and escalate. | BUG-062 study: the "unsure" outcome considered for the screen. | Not yet cited |
| LLM abstention can be a prompt artifact, in addition to genuine uncertainty. arXiv:2507.16199. https://arxiv.org/pdf/2507.16199 | Adding an "unknown" option can cause abstention inflation; abstention may come from the prompt rather than real uncertainty. | BUG-062 study: why the screen has no "unsure" verdict. | Not yet cited |
| Improving LLM reliability through hybrid abstention and adaptive detection. arXiv:2602.15391. https://arxiv.org/html/2602.15391v1 | Hybrid abstention approaches for reliability. | BUG-062 study, section 4. | Not yet cited |
| Uncertainty-based abstention in LLMs improves safety and reduces hallucinations. arXiv:2404.10960. https://arxiv.org/html/2404.10960v1 | Abstention can improve safety, but scalar confidence thresholds are brittle. | BUG-062 study: why we did not use a confidence threshold. | Not yet cited |
| Google ShieldGemma model card. https://docs.api.nvidia.com/nim/reference/google-shieldgemma-9b | Policy-conditioned classifiers are highly sensitive to how the policy is worded and may struggle with ambiguity. | BUG-062 study: explains the instability across screen variants v1–v4. | Not yet cited |
| PL-Guard: Probabilistic logic reasoning for LLM guardrails. arXiv:2608.15673. https://arxiv.org/pdf/2608.15673 | Do not refuse benign requests merely because they mention a sensitive topic. | BUG-062 study: the "mentioning is not requesting" principle in the final screen. | Not yet cited |
| Granite 4.1 8B banking input guardrails [Model card]. https://friendli.ai/models/sksanjoo23/granite-4.1-8b-banking-input-guardrails | A banking guardrail model is not recommended as the sole guardrail because of its false-block and false-allow rates. | BUG-062 study: a guardrail is one layer, and false blocks must be measured (metric B6/M22). | Not yet cited |
| Banking done right: Redefining retail banking with language-centric AI. arXiv:2510.07645. https://arxiv.org/pdf/2510.07645 | Banking guardrail work includes a benign-prompt category to measure false refusals. | BUG-062 study: measuring the benign-block rate. | Not yet cited |
| Arize. Designing real-time guardrails. https://phoenix.arize.com/docs/ax/cookbooks/observe/designing-realtime-guardrails.md | A guardrail that blocks a real user is often worse than the harm it prevents. | BUG-062 study, section 4. | Not yet cited |
| Think before refusal. arXiv:2503.17882. https://arxiv.org/pdf/2503.17882 | Safety reflection before refusing reduces over-refusal, but needs retraining. | BUG-062 study: noted and set aside, since the project does not fine-tune. | Not yet cited |
| Discern truth from falsehood: Reducing over-refusal via contrastive refinement. ICLR 2026 poster. https://iclr.cc/virtual/2026/poster/10010479 | Contrastive refinement of the decision boundary; needs retraining. | BUG-062 study: noted and set aside. | Not yet cited |

## 5. Evaluation and statistics

| Source | What it says that mattered | Where we used it | Status |
|---|---|---|---|
| Zheng, L., et al. (2023). Judging LLM-as-a-judge with MT-Bench and Chatbot Arena. *NeurIPS 36 Datasets and Benchmarks*. | An LLM judge agrees well with humans given clear criteria, but can share biases with the system it judges. | Report 3.5; the two-labeller design and owner review in v5.1 ([EVALUATION_METHODS_v5.1.md](evaluation/EVALUATION_METHODS_v5.1.md)). | Cited in report |
| Es, S., James, J., Espinosa-Anke, L., & Schockaert, S. (2024). RAGAS: Automated evaluation of retrieval augmented generation. *EACL 2024 Demos*, 150–158. https://aclanthology.org/2024.eacl-demo.16 | Splits RAG evaluation into retrieval and generation parts such as faithfulness. | Report 3.5; the claim-level groundedness judge. | Cited in report |
| Wilson, E. B. (1927). Probable inference, the law of succession, and statistical inference. *JASA, 22*(158), 209–212. https://doi.org/10.1080/01621459.1927.10502953 | Score interval for a proportion that behaves well at small samples. | Confidence intervals on every rate (v4.0 and v5.1). | Cited in report |
| Clopper, C. J., & Pearson, E. S. (1934). The use of confidence or fiducial limits illustrated in the case of the binomial. *Biometrika, 26*(4), 404–413. https://doi.org/10.1093/biomet/26.4.404 | Exact binomial bound. | One-sided 95% bounds for zero-incident results, and the minimum pass counts that "demonstrate" a target in v5.1 (`score_misc.py`, `pass_counts.json`). | Cited in report |

## 6. Datasets

| Source | What it says that mattered | Where we used it | Status |
|---|---|---|---|
| Casanueva, I., et al. (2020). Efficient intent detection with dual sentence encoders. *NLP4ConvAI 2020*, 38–45. https://doi.org/10.18653/v1/2020.nlp4convai-1.5 | Introduces BANKING77: 13,083 real banking customer queries over 77 intents. | Source of single-turn test questions (v4.0 and the v5.1 holdout). | Cited in report |
| Rastogi, A., et al. (2020). Towards scalable multi-domain conversational agents: The Schema-Guided Dialogue dataset. *AAAI 34*(05), 8689–8696. https://doi.org/10.1609/aaai.v34i05.6394 | Multi-domain, multi-turn dialogues including banking. | Source of multi-turn test dialogues. | Cited in report |

## 7. Singapore regulation and guidance

| Source | What it says that mattered | Where we used it | Status |
|---|---|---|---|
| IMDA. (2024). Model AI governance framework for generative AI. https://www.imda.gov.sg/-/media/imda/files/news-and-events/media-room/media-releases/2024/05/annex-a-nine-dimensions-of-the-model-ai-governance-framework-for-generative-ai.pdf | Nine dimensions including accountability, trusted data, incident reporting, testing and assurance. | Compliance matrix in [MEASUREMENT_FRAMEWORK_v4.0.md](evaluation/MEASUREMENT_FRAMEWORK_v4.0.md) and [METRICS_DESIGN_v5.1.md](evaluation/METRICS_DESIGN_v5.1.md). | Cited in report |
| IMDA. (2026). Model AI governance framework for agentic AI. https://www.imda.gov.sg/-/media/imda/files/about/emerging-tech-and-research/artificial-intelligence/mgf-for-agentic-ai.pdf. May 2026 update summarised by Baker McKenzie: https://www.bakermckenzie.com/en/insight/publications/2026/06/singapore-imda-updates-model-ai-governance-framework-for-agentic-ai | Limit an agent's autonomy and tool access up front; keep humans accountable. | Report 3.2: bounded graph, allowlisted tools, actions handed to the customer. | Cited in report (Baker McKenzie update cited in v4.0 only) |
| IMDA. (n.d.). Starter kit for testing LLM-based applications for safety and reliability. https://www.imda.gov.sg/-/media/imda/files/about/emerging-tech-and-research/artificial-intelligence/large-language-model-starter-kit.pdf | Test four risk areas: hallucination, undesirable content, data disclosure, adversarial prompts. | Red-team set and the adversarial-robustness metric (R10, M23). | Cited in report |
| AI Verify Foundation. Project Moonshot. https://aiverifyfoundation.sg/project-moonshot/ | Open toolkit and attack sets for LLM red-teaming. | Style of the red-team attack set (M23). | Not yet cited (cited in v4.0) |
| MAS. (2018). FEAT principles (fairness, ethics, accountability, transparency). https://www.mas.gov.sg/publications/monographs-or-information-paper/2018/feat | Fair, ethical, accountable and transparent use of AI in finance. | Compliance matrix; the cross-session answer cache for consistent answers ([FAIRNESS_ANSWER_CACHE_DESIGN.md](../FAIRNESS_ANSWER_CACHE_DESIGN.md)). | Cited in report |
| MAS. (2021). Technology risk management guidelines. https://www.mas.gov.sg/-/media/MAS/Regulations-and-Financial-Stability/Regulatory-and-Supervisory-Framework/Risk-Management/TRM-Guidelines-18-January-2021.pdf | Resilience, logging and change management for technology systems. | Audit trail, regression suites, availability metric. | Cited in report |
| MAS. (2024a). Guidelines on fair dealing (revised 30 May 2024). https://www.mas.gov.sg/-/media/mas-media-library/fair-dealing-guidelines-30-may-2024.pdf | Fair dealing outcomes to customers; clear, accurate information. | Outbound conduct audit; no misleading or unfair claims. | Cited in report |
| MAS. (2024b). Artificial intelligence model risk management: Observations from a thematic review. https://www.mas.gov.sg/publications/monographs-or-information-paper/2024/artificial-intelligence-model-risk-management | Banks' practices for AI model risk: inventory, testing, monitoring, human oversight. | Compliance matrix; evaluation design. | Cited in report |
| MAS. (2025). Consultation paper on guidelines on AI risk management. https://www.mas.gov.sg/publications/consultations/2025/consultation-paper-on-guidelines-on-artificial-intelligence-risk-management. Media release: https://www.mas.gov.sg/news/media-releases/2025/mas-guidelines-for-artificial-intelligence-risk-management | Proposed supervisory expectations for AI risk management (still a consultation when cited). | Compliance matrix. | Cited in report (media release in v4.0 only) |
| MAS. (2026). Project MindForge AI risk management operationalisation handbook. https://www.mas.gov.sg/-/media/mas-media-library/schemes-and-initiatives/ftig/project-mindforge/mindforge-ai-risk-management-operationalisation-handbook.pdf | Industry handbook for putting AI risk management into practice. | v4.0 compliance matrix. | Not yet cited (cited in v4.0) |
| MAS & IMDA. (2024). Guidelines on shared responsibility framework (effective 16 Dec 2024). https://www.mas.gov.sg/regulation/guidelines/guidelines-on-shared-responsibility-framework | Duties of banks and telcos to protect customers from phishing scams. | Credential-capture controls (R3, M21); scam-related questions. Scam detection itself is out of MVP scope. | Cited in report |
| PDPC. (2024). Advisory guidelines on use of personal data in AI recommendation and decision systems. https://www.pdpc.gov.sg/organisations/regulations-decisions/regulatory-guidance/advisory-guidelines-on-use-of-personal-data-in-ai-recommendation-and-decision-systems | Use of personal data in AI systems; data minimisation. | Personal values never reach a model (R2/M19); PDPA s.26 transfer limit noted in v4.0. | Cited in report |
| Financial Advisers Act 2001. https://sso.agc.gov.sg/Act/FAA2001 | Financial advice is a regulated activity. | Compliance screen blocks investment advice; outbound audit. | Cited in report |
| Personal Data Protection Act 2012. https://sso.agc.gov.sg/Act/PDPA2012 | Consent, purpose limitation, protection and transfer of personal data. | Personal-data handling design. | Cited in report |

## 8. Industry sources and benchmarks

| Source | What it says that mattered | Where we used it | Status |
|---|---|---|---|
| Fintech Singapore. (2025). Trust Bank's generative AI chatbot. https://fintechnews.sg/131890/digital-banking-news-singapore/trust-gen-ai-chatbot/ | A Singapore digital bank deploying a generative AI chatbot. | Business context and containment benchmark. | Cited in report |
| Netguru. Chatbot KPIs with benchmarks. https://www.netguru.com/blog/chatbot-kpis | Typical chatbot KPI ranges. | Targets in the business metrics framework. | Cited in report |
| Alhena. AI chatbot containment vs deflection rate. https://alhena.ai/blog/ai-chatbot-containment-vs-deflection-rate/ | Difference between containment and deflection, with benchmark ranges. | Containment metric definition and target. | Cited in report |

---

## Notes for the final report

- **The structured-output finding links two parts of the report.** Section 3.3 already says the judging schemas put reasoning before the verdict because fields are generated in order. BUG-070 shows the other side: the same ordering makes a schema harmful for an editing step that must decide what to delete before it writes. Tam et al. (2024) and Park et al. (2024) support both points and could be added to section 3.3.
- **The over-refusal literature is not yet in the report.** It shaped the final compliance-screen design (BUG-062). If section 3.4 is expanded, XSTest, OR-Bench and the abstention papers are the strongest additions.
- **Check before citing.** Entries found only by title and link need authors and years checked before they go into the reference list.
