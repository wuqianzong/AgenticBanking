# Report Materials Index

Everything needed to write the final report and presentation, in one place.

## Report drafts (this folder)

| File | What it is |
|---|---|
| [FINAL_REPORT_DRAFT.md](FINAL_REPORT_DRAFT.md) | Final report draft (source for the Word build) |
| [FT5007_Final_Report_Wu_Qianzong.docx](FT5007_Final_Report_Wu_Qianzong.docx) / [.pdf](FT5007_Final_Report_Wu_Qianzong.pdf) | Rendered final report |
| [FINAL_REPORT_REVIEW.md](FINAL_REPORT_REVIEW.md) | Review of the report draft |
| [LESSONS_LEARNT_DRAFT.md](LESSONS_LEARNT_DRAFT.md) | Lessons learnt |
| [LITERATURE_REVIEW.md](LITERATURE_REVIEW.md) | **Every source studied** (papers, regulation, industry), by topic, with what it says and where the project used it |
| [USER_STORIES.md](USER_STORIES.md) | User stories |
| [RISK_MITIGATION_CAPABILITIES.md](RISK_MITIGATION_CAPABILITIES.md) | **All risk controls in one place**: layers, how each works, regulation mapping, test results, scope limits |
| [ui/](ui/) | UI screenshots used as figures |

## Project brief and assessment ([brief/](brief/))

| File | What it is |
|---|---|
| [AgenticAI Chatbot for Consumer Banking.pdf](brief/AgenticAI%20Chatbot%20for%20Consumer%20Banking.pdf) | Project brief: purpose, scope, capability requirements, MVP intent taxonomy |
| [Capstone project assessment.pdf](brief/Capstone%20project%20assessment.pdf) | Assessment criteria |
| [2026-Capstone Briefing-May2026(DFT) 12 May (1).pdf](brief/2026-Capstone%20Briefing-May2026(DFT)%2012%20May%20(1).pdf) | Capstone briefing slides |

## Evaluation and metrics ([evaluation/](evaluation/))

| File | What it is |
|---|---|
| [METRICS_DESIGN_v5.1.md](evaluation/METRICS_DESIGN_v5.1.md) | **Frozen v5.1 evaluation design** (metrics, data scope, formulas, tests, statistics, compliance matrix) |
| [RESULTS_v5.1.md](evaluation/RESULTS_v5.1.md) | **v5.1 results**: headline table, results against the brief, every metric, labeller agreement, defects and limitations |
| [EVALUATION_METHODS_v5.1.md](evaluation/EVALUATION_METHODS_v5.1.md) | **How every v5.1 metric was produced** (holdout construction, labelling, keys, pass counts, runs, the BUG-062 pause and re-freeze) |
| [INBOUND_COMPLIANCE_SCREEN_STUDY.md](evaluation/INBOUND_COMPLIANCE_SCREEN_STUDY.md) | BUG-062 study: compliance-screen over-blocking, sixteen variants, research, final design |
| [MEASUREMENT_FRAMEWORK_v4.0.md](evaluation/MEASUREMENT_FRAMEWORK_v4.0.md) | v4.0 measurement framework and results (earlier evaluation) |
| [METRICS_DESIGN_v5.0.md](evaluation/METRICS_DESIGN_v5.0.md), [v5.0 review](evaluation/METRICS_DESIGN_v5.0_REVIEW.md), [v5.1 review](evaluation/METRICS_DESIGN_v5.1_REVIEW.md) | Design history of v5.1 |
| [BUSINESS_METRICS_FRAMEWORK_v1.md](evaluation/BUSINESS_METRICS_FRAMEWORK_v1.md), [business_metrics_artifact.html](evaluation/business_metrics_artifact.html) | Business metrics framework |
| [AGENTIC_EVALUATION_PLAN_v1.md](evaluation/AGENTIC_EVALUATION_PLAN_v1.md), [AUTOMATED_METRICS_HARNESS_GUIDELINE_v1.0.md](evaluation/AUTOMATED_METRICS_HARNESS_GUIDELINE_v1.0.md) | Earlier evaluation plan and harness guideline |
| [Metrics & Telemetry Reference Guide v3.5.docx](evaluation/Metrics%20&%20Telemetry%20Reference%20Guide_%20Agentic%20AI%20Chatbot%20v3.5.docx) | Metrics and telemetry reference |

Evidence (run data, labels, scripts) lives in `research/` and is not committed; each evaluation document names its evidence folder.

## Design ([design/](design/) and docs/)

Moved here (not referenced from code):

| File | Topic |
|---|---|
| [End-to-End Process Specification.md](design/End-to-End%20Process%20Specification.md) | End-to-end process |
| [RAG Specification.md](design/RAG%20Specification.md), [Search Strategy Specification.md](design/Search%20Strategy%20Specification.md) | Retrieval |
| [MEMORY_ARCHITECTURE_v1.0.md](design/MEMORY_ARCHITECTURE_v1.0.md), [v2.0](design/MEMORY_ARCHITECTURE_v2.0.md) | Memory (earlier versions) |
| [TOOL_CALLING_v2.0.md](design/TOOL_CALLING_v2.0.md) | Tool calling (earlier version) |
| [DEEPLINK_DESIGN_v1.0.md](design/DEEPLINK_DESIGN_v1.0.md) | App-screen deep links |
| [RESPONSE_COVERAGE_CONTRACT_DESIGN.md](design/RESPONSE_COVERAGE_CONTRACT_DESIGN.md), [obligation addendum](design/RESPONSE_OBLIGATION_CONTRACT_ADDENDUM_CLARIFICATION_AUTHORING.md) | Response contracts |
| [DEEPSEEK_MIGRATION_AND_PROMPT_CACHING.md](design/DEEPSEEK_MIGRATION_AND_PROMPT_CACHING.md) | Second provider and prompt caching |
| [USER_JOURNEY_v2.0.md](design/USER_JOURNEY_v2.0.md), [USER_PREFERENCES.md](design/USER_PREFERENCES.md) | User journey and preferences |

Still in `docs/`, because code comments refer to them by path (they can be moved after the v5.1 run, together with those comments):

| File | Topic |
|---|---|
| [GROUNDEDNESS_v2.0.md](../GROUNDEDNESS_v2.0.md) | Outbound groundedness and conduct audit |
| [TOOL_CALLING_v3.0.md](../TOOL_CALLING_v3.0.md) | Tool calling (current) |
| [MEMORY_ARCHITECTURE_v3.0.md](../MEMORY_ARCHITECTURE_v3.0.md) | Memory (current) |
| [RAG_RETRY_EFFORT_v1.0.md](../RAG_RETRY_EFFORT_v1.0.md) | Retrieval retries |
| [SERVICE_REQUEST_DESIGN_v1.0.md](../SERVICE_REQUEST_DESIGN_v1.0.md) | Service request forms |
| [DECLINE_CLARIFICATION_DESIGN_v1.0.md](../DECLINE_CLARIFICATION_DESIGN_v1.0.md) | Declines and clarifications |
| [RESPONSE_OBLIGATION_CONTRACT_DESIGN.md](../RESPONSE_OBLIGATION_CONTRACT_DESIGN.md) | Response obligation contract |
| [SOFT_ESCALATION_v1.0.md](../SOFT_ESCALATION_v1.0.md) | Soft escalation (offer of a person) |
| [FAIRNESS_ANSWER_CACHE_DESIGN.md](../FAIRNESS_ANSWER_CACHE_DESIGN.md) | Cross-session answer cache |
| [AGENTIC_CAPABILITY_ROADMAP_v1.0.md](../AGENTIC_CAPABILITY_ROADMAP_v1.0.md) | Capability roadmap |
| [KNOWN_ISSUES_TRACKER.md](../KNOWN_ISSUES_TRACKER.md) | All tracked issues (BUG-001 to BUG-074) |

## Monthly logs ([monthly_logs/](monthly_logs/))

Individual monthly logs (June–September) and the template.
