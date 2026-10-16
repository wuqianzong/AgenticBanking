# Final Report Review for FT5007 Internal Capstone

**Report reviewed:** `docs/report/FINAL_REPORT_DRAFT.md`  
**Review date:** 3 October 2026  
**Purpose:** Help revise the report toward the **Excellent** band for the FT5007 internal capstone.  
**Review basis:** The final-report requirements and rubric in `docs/report/brief/Capstone project assessment.pdf` and `docs/report/brief/2026-Capstone Briefing-May2026(DFT) 12 May (1).pdf`, checked against the report, source code, design documents, research artefacts, final validation runs, issue tracker, user stories, UI captures, monthly logs, and Git history in this repository.

## Overall assessment

The draft has a strong technical and analytical core. It explains a substantial implemented system, presents an unusually detailed measurement framework, acknowledges limitations, and uses a concrete compliance redesign as evidence of learning. These qualities give it credible **Excellent-band potential**.

It is not yet an Excellent-band submission artefact. If submitted in its current form, the visible placeholders, absent figures and agile evidence, unverified references, externalised appendices, unresolved metric inconsistencies, and lack of a final 25–50-page Word or PDF document would likely hold it to **Satisfactory**, regardless of the quality of the underlying work. The presentation component cannot be rated from the report alone.

The most important revision is not to add more technical detail indiscriminately. It is to make the report self-contained, academically defensible, visually complete, and precise about what the evidence does and does not prove.

## Priority definitions

- **P0 Submission blocker:** Must be completed before submission.
- **P1 High-mark revision:** Materially affects whether the work reads as comprehensive, well analysed, innovative, and supported by solid conclusions.
- **P2 Polish:** Improves clarity, professionalism, or examiner usability after P0 and P1 are complete.

## Assessment requirement review

The official final-report requirement calls for a clear overview beginning with an executive summary; analytic and functional requirements supported by user stories; system design, UI, and platform choices; literature-backed justification of methods, models, and performance indicators; practical use cases; coded sprint demos; recommendations where applicable; and a summary of results and outcomes. The submitted Word or PDF must be 25–50 pages, including front matter and appendices.

| Official requirement | Current assessment | Evidence in draft | Required revision |
|---|---|---|---|
| Clear project overview and executive summary | **Strong** | Executive Summary and Sections 1.1–1.5 clearly state the banking problem, MVP scope, stakeholders, constraints, design, test sets, and principal results. | **P1:** Qualify absolute claims such as “guaranteeing,” “every reply,” and “zero risk.” State that the evaluation provides bounded evidence, not a universal guarantee. Use one consistent set of final metrics. |
| Functional requirements | **Meets** | Section 2.1 defines FR-1 to FR-10 and maps each to an implemented mechanism. | **P1:** Add a traceability column linking every requirement to user story, implementation location, test/evaluation evidence, and status. Mark partial or out-of-scope items explicitly rather than presenting every row as equally realised. |
| Analytic requirements | **Strong** | Section 2.2 links business and regulatory needs to metrics; Section 7 reports targets and results. | **P1:** Reconcile status inconsistencies described below. Add enough method detail for an examiner to understand denominators, sampling, labels, judging, confidence intervals, and code/model version without opening another file. |
| User stories supporting requirements | **Mostly meets** | Section 2.3 includes two examples; Appendix A summarises 24 stories; `USER_STORIES.md` contains full acceptance criteria and evidence. | **P0:** Do not rely on a repository path as the appendix. Include a compact but complete requirements-to-story traceability matrix in the submitted report. Include the most representative full stories and acceptance evidence. Clarify whether the additional customer-service-agent story makes the total 25 rather than 24. |
| System design | **Strong text, incomplete presentation** | Sections 4.1–4.6 explain the three layers, 24-node graph, pipeline, stores, privacy boundary, platform choices, and design principles. The graph really contains 24 registered nodes. | **P0:** Add Figure 1. **P1:** Prefer a report-specific architecture diagram showing components, trust boundaries, data stores, external model providers, personal-data path, and audit path. Add a sequence or flow diagram for one representative turn; a reused slide alone may be too shallow. |
| User interface | **Content present, figures not integrated** | Section 5 describes response blocks and points to ten UI captures in `docs/report/ui/`. | **P0:** Embed the figures with captions and readable callouts in the actual report. Refer to every figure by number in the prose. Remove filename-style references such as “Figure `05_lost_card.png`.” Confirm that screenshots remain legible at final page size. |
| Platform choices | **Meets, with reproducibility gap** | Section 4.5 names LangGraph, FastAPI, React, Vite, Tailwind, ChromaDB, SQLite, the model providers, and hardware constraints, with short justifications. Frontend versions are traceable in `frontend/package.json`. | **P1:** Record dependency versions from a reproducible source. The repository-root `requirements.txt` is currently empty, so backend version claims cannot be independently reproduced from the repo. Explain selection criteria and trade-offs, not only benefits. |
| Methods justified with literature | **Good foundation, not final** | Section 3 covers RAG, tool use, reasoning, guardrails, evaluation methods, datasets, statistics, regulation, and alternatives. | **P0:** Replace all “verify references” notes with a checked reference list. **P1:** Deepen critical synthesis: identify where the project follows, modifies, or departs from each cited method and why. Clearly separate peer-reviewed research, regulation, vendor documentation, industry reporting, and project evidence. |
| Models justified with literature and evidence | **Mostly meets** | Sections 3 and 4.5 explain task-focused prompting, a bounded graph, Gemini/DeepSeek, and Prompt Guard. | **P1:** Add the model-selection criteria and evidence: structured-output reliability, cost, latency, quota, data-location implications, fallback behaviour, and why each model is appropriate for its role. Avoid implying that a stronger model is guaranteed to perform at least as well without direct measurement. |
| Performance indicators justified with literature | **Strong framework, some unsupported status decisions** | Sections 3.5–3.6 and 7 tie metrics to business needs, regulatory expectations, and cited evaluation approaches. | **P1:** Cite the source and rationale for each externally derived target. The repository says the agent-contact cost is “bank to supply,” so M15 measures cost but does not yet prove “below agent cost.” Either supply a defensible comparator or mark A8 as not proven. |
| Practical use cases | **Meets in prose** | Section 8 gives eight use cases covering balance, transfer, lost card, product information, forms, scam protection, advice refusal, escalation, and handover. | **P1:** Turn the strongest cases into compact evidence panels: user goal, input, relevant system path, output/figure, requirement or story satisfied, and limitation. Ensure quoted bot output exactly matches saved run evidence. |
| Coded sprint demos as evidence of progress | **Insufficient** | Section 6.2 provides a useful monthly sprint narrative and commit counts, but no actual coded demo artefacts are embedded. Appendix E is `[TO ADD]`. | **P0:** Add dated sprint-demo evidence. For each sprint, show the goal, demonstrable coded increment, screenshot or commit/release reference, feedback received, and resulting change. A commit count is supporting evidence, not a coded demo. Add backlog, sprint review, and retrospective evidence. |
| Recommendations based on findings | **Strong** | Section 9.3 connects production recommendations to observed coverage, latency, audit retention, hosting, and evaluation limitations. | **P1:** Link each recommendation explicitly to one or more findings and prioritise by impact, risk, and effort. Separate what is necessary for production safety from optional future enhancement. |
| Summary of overall results and outcomes | **Strong but occasionally overstated** | Executive Summary, scorecard, final check, discussion, and conclusion give quantitative outcomes and limitations. | **P1:** Reconcile the scorecard with the delivered-code run. Replace universal claims with sample-bounded conclusions. State clearly which targets were met, partially met, not met, or not demonstrated. |
| Word or PDF, 25–50 pages including appendices | **Not met yet** | Only a Markdown draft exists. | **P0:** Produce the final Word or PDF, embed all required content, and verify the page count, table of contents, figure numbering, references, headers/footers, and visual rendering. |

## Rubric review

The internal-capstone rubric combines the final report and presentation. This review rates only what can be inferred from the report and repository.

### Comprehensive report

**Current position: strong content coverage, incomplete artefact.**

The report covers almost every required subject and draws on extensive supporting material: 24 implemented graph nodes, 708 FAQ parents, 24 documented user stories, 63 tracked issues, ten current regression scripts with 93 checks, and many saved research runs. This is enough substance for a comprehensive report.

The current document does not yet demonstrate comprehensiveness to an examiner because important evidence is referenced but not included. The architecture figure, agile artefacts, sprint demos, full traceability, full metric catalogue, and supporting visuals need to be present or meaningfully summarised in the submitted report. An examiner should not need repository access to understand the work.

### Well-analysed report

**Current position: closest to Excellent.**

The strongest analytical elements are:

- the derivation of metrics from business needs and Singapore regulatory expectations;
- the separation of FAQ coverage failures from system-design failures;
- explicit target/result comparisons;
- statistical handling of zero incidents and acknowledged sample limitations;
- the defect tracker with root cause and disposition;
- the inbound-compliance case study, which shows hypothesis, failed approaches, redesign, adverse result, and refinement;
- the limitations section, especially model/judge dependence, latency, consistency, and clarification quality.

To reach Excellent, the report must also resolve its own contradictory classifications and make the validity limits part of the main argument rather than leaving them mainly in the measurement framework. It should include a short “Threats to validity” subsection covering dataset representativeness, synthetic data, model drift, one run per configuration, same-provider judging, AI-assisted labels, small escalation/repeat samples, and the absence of live customers or a banking subject-matter expert.

### Innovative and transformative solution

**Current position: plausible, but needs a sharper evidence-backed argument.**

The strongest candidates for innovation are:

1. compliance screening framed around the regulated bank action and constrained by the actual tool/service scope;
2. the response obligation contract linking each response component to approved evidence or a deterministic obligation;
3. personal-data and exact-number substitution after model processing;
4. a need-and-regulation-derived measurement framework with traceable defects and reruns;
5. bounded agent autonomy through an allowlisted registry, deterministic attachments, audits, and fallback paths.

The report currently lists these as contributions but does not fully establish what is novel relative to prior work or what changed because of them. Add a contribution table with columns for the prior limitation, the project design, implementation evidence, measured effect, and remaining limitation. Use careful terms such as “project contribution” or “distinctive design” unless a literature review establishes research novelty.

### Exceeds expectations

**Current position: underlying work may exceed expectations; the draft does not yet prove it.**

The breadth of implementation and evaluation is stronger than a typical MVP narrative. However, exceeding expectations must be visible through traceable evidence and disciplined analysis, not feature count. The report should foreground three or four deep contributions rather than enumerate every node, metric, and document. Demonstrate the engineering-research cycle: requirement → design choice → coded implementation → evaluation → defect → redesign → re-evaluation.

### Confident and clear presentation

**Current position: prose is generally clear; final presentation quality is not yet assessable.**

The prose is structured and usually direct. Tables are used effectively. Weaknesses are the drafting notes, missing diagrams, filename references, long inventories, and a few assertions stronger than the evidence. A polished final layout, consistent terminology, numbered tables/figures, and a complete reference system are required before the document can support a confident presentation.

### Solid conclusions

**Current position: strong synthesis with claims that need calibration.**

The conclusion identifies the system’s main outcomes and contributions. It should not state that “every reply” is grounded when the measured all-question groundedness is 99.6%, or imply that zero observed incidents proves impossibility. A stronger conclusion would state what was demonstrated on the defined evaluation sets, what met its target, what remained partial, and what would be required before production deployment.

## Repository evidence review

### Claims with strong direct support

| Report claim | Repository support | Review |
|---|---|---|
| The orchestration graph has 24 nodes | `backend/app/orchestration/graph.py` contains 24 `add_node` registrations. | Supported. Consider distinguishing registered nodes from model-calling stages. |
| The knowledge base has 708 parent entries | `backend/data/etl/canonical_taxonomy/artifacts/consolidated_horizon_faqs.json` contains 708 records. | Supported. Add the generation date/version of the active artefact. |
| Ten regression suites contain 93 checks | Ten scripts exist under `backend/tests/regression/`; a current read-only run passed 93/93 checks. | Supported for the current code. The repo does not by itself prove they passed “before every change”; use CI/log evidence or soften that phrase. |
| Final FAQ-covered check completed 53 cases | Final 3.1 run report records 53/53 cases completed and 159/159 mechanical assertions passed. | Supported mechanically. Quality conclusions still require the documented interpretation step. |
| Final multi-turn check completed 20 sessions | Final 3.1 report records 20/20 cases completed and 420/468 assertions passed. | The completion count is supported. Explain why failed mechanical assertions did not invalidate the claimed 80% session-resolution result. |
| Final escalation check completed 20 sessions | Final 3.1 report records 20/20 cases and 60/60 assertions passed. | Supported mechanically. Precision and recall should include exact numerator/denominator and confidence limits because the positive sample is small. |
| Final red-team check completed 144 cases | Final 3.1 report records 144/144 cases and 427/432 assertions passed. | Completion is supported. Explain the five failed assertions and the review that classified all customer-facing replies as non-harmful. |
| The project kept a detailed defect history | `docs/KNOWN_ISSUES_TRACKER.md` records 61 defects and two suspected issues with extensive evidence and decisions. | Strong support and one of the best indicators of engineering depth. Summarise categories and outcomes visually rather than only stating the total. |
| The project used a reproducible research layout | `research/` contains manifests, fixtures, results, reports, logs, and a shared framework across many dated studies. | Strong support, but the final automated reports explicitly say interpretation is required. Add or cite final human/source-verified summaries. |

### Claims or classifications requiring revision

1. **Universal guarantee language.** The problem statement asks how to “guarantee” every reply is grounded and compliant, while measured groundedness is 99.6% overall and six needs or requirements are partial. Reframe the research question around enforceable controls and measurable assurance.

2. **“Every reply grounded.”** The conclusion says every reply is grounded, but the report itself records 781/784 supported claims. Use “99.6% of evaluated factual claims” and retain the exceptions.

3. **Scorecard status is stale or internally inconsistent.** The report repeats “11 of 17 met, 6 partial” from the framework, while the delivered-code table reports multi-turn resolution at the 80% target. Decide whether status is based on the September primary run or the delivered-code rerun and apply that basis consistently.

4. **R1 marked Meets despite M26 missing its target.** Audit-trail completeness is 99.5% against a 100% target. If R1 remains Meets, explain why this metric does not control the overall status; otherwise mark it Partial.

5. **A8 cost claim is not proven.** The system cost per turn is measured, but the target is “below agent cost per contact” and the measurement framework says the bank must supply that comparator. Report the measured model cost and mark the business comparison pending unless a defensible source is added.

6. **Final automated reports require interpretation.** Each final report says its output is mechanical and that source-verified relevance, groundedness, naturalness, and policy conclusions must be added before claiming quality improvement. Add a clear account of who performed that review, the rubric, artefact, disagreements, and final classification.

7. **Model comparison is too strong.** The statement that Gemini 3.5 is stronger and is therefore expected to do at least as well as 3.1 is a hypothesis, not evidence for this system. Keep results model-specific and avoid substituting expected performance for a missing rerun.

8. **Commit counts need a declared cutoff.** Current history contains 250 commits, and monthly counts vary slightly from the draft’s figures. If commit totals are retained, state the exact date/commit cutoff and counting command. More importantly, connect selected commits to sprint outcomes rather than treating volume as quality.

9. **Dependency versions are not reproducible.** `frontend/package.json` supports frontend versions, but the root `requirements.txt` is empty. Create a locked or clearly documented backend dependency source before making exact backend version claims.

10. **Externalised evidence weakens the submitted document.** Appendices B–D mostly point to repository documents. Summarise the evidence needed for assessment inside the report and use repository paths only as supplementary reproducibility references.

## Content and writing review

### Strong sections to preserve

- **Executive Summary:** good problem-to-method-to-results arc.
- **Scope and constraints:** disciplined MVP boundaries and honest resource constraints.
- **Requirements tables:** clear and easy to scan.
- **Method choice table:** useful alternatives and rationale.
- **Turn pipeline:** detailed enough to demonstrate engineering ownership.
- **Measurement framework and scorecard:** a strong bridge from implementation to business and regulatory value.
- **Inbound compliance case study:** the best example of critical problem-solving and should remain prominent.
- **Limitations and production recommendations:** candid and practically relevant.

### Sections needing more analytical depth

- **Literature Review:** synthesize competing approaches instead of giving one paragraph per topic. For example, compare RAG with fine-tuning, deterministic orchestration with open-ended agents, and in-pipeline auditing with independent post-hoc evaluation. State what evidence changed the design.
- **Development Process:** show decisions, feedback, and retrospective learning, not only monthly feature accumulation and commit counts.
- **Evaluation Methods:** describe sample construction, exclusions, labels, judge prompts, human/AI review, confidence intervals, run conditions, and versioning within the report.
- **Discussion:** explain why the strongest and weakest results occurred and what that implies for the architecture. Avoid merely repeating the scorecard.
- **Conclusion:** answer the problem statement within the measured scope and explicitly separate demonstrated results from production claims.

### Style and presentation changes

- Use one term consistently for each concept: “need,” “task,” “requirement,” “session resolution,” “containment,” and “groundedness.”
- Define abbreviations on first use and include an abbreviations list only if it materially helps.
- Give every table and figure a number, caption, and source or “author’s implementation” note.
- Keep implementation identifiers such as `n2f` secondary to descriptive names. They demonstrate traceability but should not interrupt the main explanation.
- Replace code paths and filenames in prose with human-readable appendix or figure references; keep paths in a reproducibility appendix.
- Use consistent rounding and always show numerator/denominator for small samples.
- Distinguish the September primary measurement run, the 2 October run, and the 3 October delivered-code run in every result table.
- Remove all drafting instructions, reviewer notes, and `[TO ADD]` markers from the submitted version.

## Recommended final report structure

The current structure is workable. The following adjustments would make the evidence easier for an examiner to follow without significantly increasing length.

1. Front matter: title page, declaration if required, executive summary, table of contents, list of figures and tables.
2. Introduction: business context, problem, objectives, stakeholders, scope, constraints, and contributions.
3. Requirements and success criteria: functional requirements, analytic requirements, user-story traceability, and measurable targets.
4. Related work and regulatory context: critical synthesis leading to design choices.
5. System design and implementation: architecture, data/trust boundaries, pipeline, platform/model selection, UI, and privacy controls.
6. Development process and coded sprint evidence: backlog, increments, demos, feedback, retrospectives, and issue-management process.
7. Evaluation methodology: datasets, sampling, run conditions, metrics, judging, statistical treatment, and threats to validity.
8. Results: primary run and delivered-code verification, clearly separated.
9. Case studies and use cases: representative customer flows plus the compliance redesign.
10. Discussion: interpretation, contribution, limitations, business/regulatory implications, and recommendations.
11. Conclusion: bounded answer to the problem statement and overall outcomes.
12. References.
13. Appendices: traceability matrix, metric definitions, selected defect summary, sprint-demo evidence, and supplementary reproducibility index.

## Review checklist

### P0 Submission blockers

- [ ] Replace “Academic advisor: `[TO ADD]`” and “Examiner: `[TO ADD]`” with verified details, or remove fields that should not appear.
- [ ] Remove the opening draft comment, all reviewer notes, every `[TO ADD]`, and every “verify before submission” instruction.
- [ ] Add the architecture figure and ensure it matches the delivered system.
- [ ] Embed all UI figures used by the argument, with numbered captions and readable resolution.
- [ ] Add the product backlog, sprint backlogs, sprint reviews, retrospectives, and dated coded sprint-demo evidence.
- [ ] Make requirements and user-story evidence self-contained in the report rather than only pointing to `USER_STORIES.md`.
- [ ] Complete and verify every bibliographic and regulatory reference using one consistent citation style.
- [ ] Include full links, publisher/issuing body, title, year/date, and access details where appropriate for regulatory and web sources.
- [ ] Reconcile all metrics and statuses between the Executive Summary, Section 7, Conclusion, and Measurement Framework.
- [ ] Explain the failed assertions in the final multi-turn and red-team reports and document the interpretation/review method.
- [ ] Export to Word or PDF and confirm that the full document, including front matter and appendices, is between 25 and 50 pages.
- [ ] Generate an accurate table of contents and list of figures/tables with page numbers.
- [ ] Visually inspect every page for clipped tables, unreadable screenshots, orphan headings, inconsistent spacing, and broken links.

### P1 High-mark revisions

- [ ] Rephrase the problem statement and conclusions to claim measurable assurance rather than a universal guarantee.
- [ ] Add a traceability matrix: objective → requirement → user story → design component → implementation evidence → metric/test → result/status.
- [ ] Add a concise contributions table that establishes what is distinctive, why it matters, measured effect, and limitation.
- [ ] Add a “Threats to validity” subsection covering datasets, synthetic data, judge independence, AI-assisted labels, model variance, small samples, and absence of live-user evidence.
- [ ] State the primary evaluation basis and distinguish it from targeted fixes and final reruns.
- [ ] Report exact numerators, denominators, and confidence intervals for small or zero-event samples.
- [ ] Resolve the A2, A8, R1, and overall 11/17 status issues.
- [ ] Add a defensible agent-contact cost comparator or stop claiming that A8 is proven.
- [ ] Explain model-selection criteria and keep performance claims model-specific.
- [ ] Provide a reproducible backend dependency specification; do not rely on an empty `requirements.txt`.
- [ ] Add data provenance, synthesis, licensing/ethical-use, and train/test contamination considerations for the FAQ and evaluation datasets.
- [ ] Strengthen the literature review through comparison and critical synthesis, not additional citations alone.
- [ ] Convert sprint history into evidence of an iterative engineering/research cycle, including advisor feedback or decisions where available.
- [ ] Make use cases traceable to exact saved outputs and figures.
- [ ] Link every recommendation to a measured finding and classify it as production requirement, risk treatment, or future enhancement.
- [ ] Explain why the selected performance targets are appropriate for this MVP and identify targets that still require a bank-provided baseline.
- [ ] State the Git commit or date cutoff for every repository count.

### P2 Final polish

- [ ] Standardise terminology, capitalization, model names, dates, dashes, percentages, and decimal precision.
- [ ] Define acronyms at first use and remove any that are not needed.
- [ ] Number all requirements, tables, figures, equations, and appendices consistently.
- [ ] Ensure every table is introduced and interpreted in the surrounding prose.
- [ ] Shorten long inventories and move low-level implementation detail to appendices.
- [ ] Check that quoted chatbot responses match saved evidence exactly and are not reconstructed from memory.
- [ ] Add source notes to figures, datasets, and externally derived targets.
- [ ] Check the reference list against every in-text citation and remove uncited references.
- [ ] Proofread for grammar, punctuation, orphaned labels, repeated claims, and formatting consistency.
- [ ] Ask a technically informed reader and a non-specialist reader to review the final PDF for clarity.

## Final readiness test

The report is ready for submission only when all P0 items are complete and the following statements can be answered **yes**:

- [ ] Can an examiner understand the problem, design, research method, delivered system, evidence, and limitations without opening the repository?
- [ ] Can every headline number be traced to a named dataset, run, denominator, code/model version, and review method?
- [ ] Are all target/status decisions internally consistent and defensible?
- [ ] Does the report clearly demonstrate an iterative coded project rather than only describe a final system?
- [ ] Are the claimed contributions distinguished from prior work without overstating novelty?
- [ ] Does the conclusion answer the research question within the evaluated scope rather than promise production guarantees?
- [ ] Is the final Word or PDF polished, complete, and within the official 25–50-page limit?

## Suggested review sequence

1. Close every P0 content gap before line editing.
2. Reconcile metrics and statuses against one declared final evidence basis.
3. Strengthen traceability, validity analysis, and the contribution argument.
4. Complete references and source notes.
5. Export and inspect the full document at its final page size.
6. Perform one last examiner-style pass using the official requirement table and rubric above.
