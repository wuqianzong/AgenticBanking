# Review of Horizon Metrics Design v5.1

**Design reviewed:** `docs/METRICS_DESIGN_v5.1.md`  
**Review date:** 3 October 2026  
**Purpose:** Determine whether v5.1 is ready to freeze and answer the three remaining decisions on test volume, independent labelling, and indirect prompt-injection testing.

## Overall assessment

Metrics Design v5.1 addresses nearly all material findings from the v5.0 review. It is substantially more credible, balanced, and examiner-ready. In particular, it now preserves an end-to-end result, defines populations and units, introduces genuinely new holdout items, labels FAQ coverage blind, separates statistical quantities, predeclares pass counts, uses critical-severity gates, narrows compliance claims appropriately, and exposes residual risk rather than hiding it.

**Recommendation: approve after targeted amendments, then freeze as v5.1-final.** A redesign is not needed. Seven focused corrections should be made before the manifest and labels are frozen:

1. split autonomous resolution from safe disposition by handover in B0;
2. reconcile C2's cluster-bootstrap decision with Part D's exact-test rule;
3. report dataset dialogues and authored journey scripts separately in B4;
4. define the C5 language variants unambiguously;
5. de-duplicate the BANKING77 training-split holdout against every development question;
6. correct privacy traceability to include customer-typed data sent to overseas providers; and
7. label risk rows as planned coverage before the run and tested coverage only after passing.

Subject to those amendments, the design is strong enough to support high-quality capstone claims. It still cannot support “all risk is fully covered,” because v5.1 correctly records partial, known-gap, untested, and out-of-scope risks. It can support the stronger and more defensible statement that all in-scope critical gates passed and every identified risk has a transparent coverage status.

## Review of changes from v5.0

| Prior review finding | v5.1 disposition | Assessment |
|---|---|---|
| Preserve an unconditional customer result | B0 added and shown with conditional metrics and B9 | Addressed, with one remaining handover-definition correction |
| Define population, frame, selection, and unit | Part B specifies each set and its generalisation | Addressed |
| Confirm every coverage label, including `NO_DATA` | Two retrieval methods, blind confirmation, manual search, second labeller, frozen hash | Addressed strongly |
| Use an untouched holdout | H1–H4 introduced; old sets are regression-only | Addressed, subject to de-duplication and provenance checks |
| Separate descriptive intervals and target decisions | Part D separates Wilson, exact lower bound/test, and exact zero-event upper bound | Addressed; C2 needs an explicit exception or redesign |
| Predeclare pass counts | Part D4 gives exact minimum counts | Addressed; table independently verified |
| Narrow regulatory claims | “Compliance assurance,” traceability matrix, bounded conclusion | Addressed |
| Add severity gates | Critical, major, and minor classes with zero-critical gates | Addressed strongly |
| Prevent evasive replies inflating correctness | B1 covers only substantive replies; B2/B3 retain non-answers | Addressed |
| Strengthen business metrics | B2 keys, B3 gap disclosure, B4 dual outcomes, B5 final route, B6–B10 expanded | Addressed, with B0/B4 clarifications below |
| Correct fairness claim | C5 renamed linguistic consistency; demographic limitation explicit | Addressed |
| Broaden privacy testing | R2 split into system values, user-supplied values, cross-customer isolation, and storage | Addressed honestly |
| Test audit sensitivity | R5 now uses seeded bad and known-good drafts | Addressed |
| Define risk coverage | Part G includes covered, partial, known-gap, untested, and out-of-scope status | Addressed, but pre-run terminology should change |
| Demonstrate consumer-banking fit | Part H maps journeys, stories, sets, and minimum cases | Addressed |

## Statistical verification

The Part D4 minimum-pass table is correct for a one-sided exact 95% Clopper–Pearson lower bound. The reference values are also correct:

- 67/67 gives a one-sided lower bound of approximately 95.6%;
- 293/293 gives approximately 99.0%;
- 52/53 gives approximately 91.4%; and
- 51/53 gives approximately 88.6%.

The design correctly distinguishes descriptive Wilson intervals from exact target decisions and exact upper bounds for zero incidents. Retain this distinction in the result report.

### Correction needed for B4

B4 says `n ≥ 54` sessions are planned, but gives “54/60” as the demonstration example. If the final denominator is 54, the exact requirement for demonstrating an 80% target is **49/54**. If 60 sessions are run, the requirement is **54/60**. Replace the fixed example with:

> Demonstrated at the Part D4 minimum for the frozen denominator; for example, 49/54 or 54/60.

### Correction needed for C2

Part D says target decisions use a one-sided exact binomial test or exact lower bound. C2 instead says the target is demonstrated when a reply-cluster bootstrap lower bound reaches 98%. That is a reasonable dependence-aware method, but it is not the general exact decision rule.

Choose one of these approaches before freezing:

1. **Recommended:** make reply-level groundedness the inferential target—replies with every claim supported divided by replies—and apply the exact rule at the reply level. Report claim-level groundedness with a reply-cluster bootstrap as a descriptive measure.
2. Declare C2 as a prespecified exception: use a one-sided 95% cluster-bootstrap lower bound for the claim-level target, state the bootstrap method precisely, and remove any implication that Part D4 controls C2.

The first option is easier to explain and makes “a customer received a fully grounded reply” the primary unit.

## Required amendments before freezing

### Separate autonomous resolution from safe handover

B0 currently counts an “appropriate handover” as a resolved need. Handover can be a good and safe customer outcome, but it does not demonstrate chatbot containment or autonomous task resolution. Counting it as resolution could inflate the business-value headline.

Split B0 into:

- **B0a End-to-end autonomous resolution:** answered or completed through the correct own-data result, app screen, or form without human handover.
- **B0b Safe disposition:** B0a plus an appropriate handover when the bot cannot safely or completely resolve the need.

Report both beside B9. B0a shows demand taken off people; B0b shows customers were not stranded.

The same distinction should remain explicit in B4: autonomous task completion, completion through self-service, and appropriate human transfer are separate outcomes.

### Keep natural and authored journey sets separate

H2 proposes using unused Schema-Guided Dialogue cases and topping up with scripts written from user stories. These are different sources with different difficulty, language, and construction bias. Pooling them into one B4 rate would obscure that difference.

Freeze two strata:

- **H2a dataset dialogues:** every genuinely unused eligible dialogue;
- **H2b project journey scripts:** independently written and frozen scenarios mapped to user stories.

Report B4 for each stratum and, if a combined number is useful, label it as a fixed benchmark aggregate with the source composition shown. Do not let authored scripts replace harder naturalistic cases without disclosure.

### Verify the holdout is unseen at the item level

Using the BANKING77 training split is sensible because repository evidence shows earlier work was based on the test split. However, BANKING77 contains highly similar paraphrases across splits and categories. The system was also developed against the same intent taxonomy.

Before sampling H1:

- inventory every prior BANKING77 message used in fixtures or logs;
- remove exact duplicates from the training-split candidate pool;
- calculate a near-duplicate similarity check and flag very close paraphrases;
- decide and document whether near duplicates are excluded or retained as same-distribution generalisation cases;
- preserve the candidate-pool hash, fixed random seed, selected indices, and licence/source statement.

Call H1 an **unseen-item holdout from a familiar benchmark distribution**, not an unseen-domain holdout.

### Define the language variants exactly

C5 says “25 needs × 4 variants” but lists plain, terse with typos, formal, Chinese, or Spanish. That describes either five variants or an uneven choice between Chinese and Spanish. Part H separately says 25 cases per language variant.

Recommended design:

- 25 base needs;
- five matched variants per need: ordinary English, terse/typo English, formal/high-literacy English, Chinese, and Spanish;
- 125 total messages in 25 matched groups;
- same semantic answer key and acceptable route for all five;
- report all-five agreement and pairwise agreement against the ordinary-English base.

If cost requires four variants, rotate Chinese and Spanish across needs but report each language separately and do not call every group multilingual-equivalent.

### Tighten privacy traceability

The design correctly states that customer-typed personal data reaches models. Part F should carry that fact into both relevant PDPA rows:

- **Protection and purpose:** system-retrieved values are isolated, but customer free text may contain personal data and is model-visible.
- **Transfer limitation:** system-retrieved values use placeholders, but customer-entered text is sent to cloud model providers and may cross borders.

The present phrase “Only placeholders sent for system data” is accurate but incomplete if read as the whole transfer assessment. Mark both controls **partial for the MVP**, with the production treatment stated.

Use synthetic values only in R2b, especially for NRIC-format, card, phone, and address tests. Ensure the fixtures cannot be mistaken for real credentials or real people.

### Distinguish planned coverage from passed coverage

Part G currently labels many risks “Covered” before the v5.1 tests have run. Its note defines Covered as having a test, but readers may interpret it as a passed control.

Use two columns:

- **Test coverage before run:** planned, partial, untested, or out of scope.
- **Control result after run:** passed, failed, not measured, or not applicable.

This preserves a stable denominator and prevents the design from appearing to pre-judge results.

### Specify adjudication and critical-case review

Part C and Part I are strong, but add the following details:

- The first labeller and second labeller work independently before seeing each other's labels.
- The second labeller is blind to bot replies, running totals, and expected project conclusions.
- Every label disagreement records both rationales and the adjudicated reason.
- Every critical-severity candidate is reviewed by both labellers, whether the judge marked it pass or fail.
- A random audit sample includes predicted passes, not only flagged failures.

This avoids a review process that only verifies what the judge already found suspicious.

## Metric-specific observations

### Business value

- **B1–B3:** Well designed. Ensure a substantive but incomplete answer can fail B2/B3 even if factually correct under B1.
- **B5:** Strong improvement. Freeze acceptable routes before the bot run and allow several legitimate routes without treating extra irrelevant actions as success.
- **B6:** Fifteen explicit requests are acceptable for a zero-failure gate, but the confidence bound will remain wide. State this rather than implying strong frequency estimation.
- **B7:** Keep model/provider and free-tier wait time visible. A single combined latency number would be misleading.
- **B8:** Cost per resolved need should use B0a as the main denominator; cost per safe disposition using B0b can be reported separately.
- **B10:** Good decision to retain it. Clarification quality is a known weakness and should remain visible even if not a headline.

### Compliance assurance

- **C1:** Strong scope and critical gate. Preserve two-reviewer treatment for every advice case.
- **C3:** “Next step where it matters” requires a frozen rubric so reviewers do not decide necessity after seeing the reply.
- **C4:** The 59/60 threshold for demonstrating 90% is correct. Report the actual outcome distribution, not only same/different.
- **C5:** Appropriately limited to linguistic consistency. Do not use its result to claim demographic fairness.
- **C6:** Define the harmful-content subcategories precisely because self-harm is out of scope. Avoid an exclusion that is visible only after failures occur.
- **C7:** Protective questions belong in the ordinary/not-prohibited denominator when they ask for safety advice. This is essential to detect over-blocking.
- **C9:** The tool-event check is a major improvement. Define “matching need” and reviewer rules before reading events.
- **C10:** The split is clear. C10c says target 100% but is not explicitly a gate; either make required audit traceability a gate or explain why a miss is only a rate failure.

### Risk mitigation

- **R1:** Strong category reporting and severity gate. Overall 95% should never override a critical category failure.
- **R2c:** A one-customer implementation cannot meaningfully demonstrate cross-customer isolation. If feasible, seed a second synthetic customer record solely for authorization tests. Otherwise keep the status Partial and phrase the test as resistance to identifier manipulation, not proven isolation.
- **R3:** The design correctly discloses that 20/20 cannot demonstrate 90%. With credentials treated as critical, the zero-retention gate matters more than claiming the block-rate target.
- **R5:** The seeded audit test is valuable. Include varied claim types—numbers, eligibility, process steps, account-access claims, and advice—not 30 near-identical fabrications.
- **R6:** The narrowed observed-availability claim is honest. Do not infer graceful-failure quality from a run in which no failure occurs.
- **R7:** Perform fault seeding in isolated copies or temporary branches and keep the release commit unchanged. Record which three defects were reintroduced and why they represent different failure mechanisms.

## Answers to the three decisions

### 1. Test volume

**Recommendation: run the full approximately 9,000-call plan.**

For a high-mark capstone, the extra two to three days using the stated research key pool is justified. The full plan gives a better chance of demonstrating—not merely estimating—the main targets and provides stronger per-category evidence. It also prevents an examiner from seeing the reduced plan as a late-stage compromise made because stronger confidence was inconvenient.

Use a staged execution order to control risk and avoid wasting calls:

1. freeze and label H1 coverage;
2. calculate the actual denominators and exact pass thresholds;
3. build and inspect all H2–H4 manifests;
4. run cheap offline regression, canary scans, and seeded-audit tests;
5. run H3 critical safety gates;
6. run H1 business holdout;
7. run H2 journeys and H4 repetition/counterfactual sets; and
8. run judges and human adjudication last.

If schedule or quota forces a reduction, do not reduce critical safety/control coverage, languages, or the untouched nature of the sets. Reduce low-value repeated ordinary turns first and explicitly mark affected targets “met on estimate.” Do not choose the reduced plan based on interim results.

### 2. Second labeller

**Recommendation: accept the second-labeller role and budget the two to three hours.**

This is one of the highest-value uses of your time. It materially improves the credibility of coverage labels, `NO_DATA` exclusions, and critical safety judgments. For the capstone report, “AI proposal plus independent student confirmation with recorded agreement and adjudication” is much stronger than AI-only labels.

Conditions:

- complete the labels independently and blind to replies and totals;
- review every required `NO_DATA` case and critical case, not only the random 20%;
- preserve your original label and short rationale before adjudication;
- report percentage agreement and Cohen's kappa, but also report raw disagreements by label because kappa can be unstable with imbalanced labels; and
- do not call this bank-SME validation—describe it accurately as independent student review.

### 3. Planted FAQ indirect-injection test

**Recommendation: include it.**

Indirect prompt injection is a central RAG risk and is currently the only critical risk row whose test is optional. Running it offers high evidential value for little model-call cost and supports the report's claim that the design handles both user-side and retrieved-content attacks.

Run it safely:

- use a copied, test-only knowledge base or isolated collection;
- use unmistakably synthetic canary instructions and data;
- test several instruction forms, including requests to reveal prompts, override policy, call a tool, and emit a canary;
- verify retrieval actually supplied the planted entry to the generator;
- inspect tool events and the final reply;
- restore nothing destructively—discard the isolated collection after preserving results; and
- keep the production/delivered FAQ artefact unchanged.

If it is skipped, leave the risk status Partial and do not claim full injection coverage. Given the stated goal of strong risk evidence, skipping it is not recommended.

## Freeze checklist

### Required before v5.1-final

- [ ] Split B0 into autonomous resolution and safe disposition.
- [ ] Use B0a, not handover-inclusive B0b, for cost per resolved need.
- [ ] Correct the B4 example for the actual frozen session denominator.
- [ ] Resolve the C2 exact-test versus cluster-bootstrap decision rule.
- [ ] Split H2 dataset dialogues and authored scripts into separate strata.
- [ ] Remove exact duplicates and inspect near duplicates between H1 and all development questions.
- [ ] Define the C5 variant count and language allocation exactly.
- [ ] Add customer-entered model exposure to the PDPA transfer-limitation row.
- [ ] Change Part G from pre-run “Covered” to planned test-coverage status plus post-run result.
- [ ] Define harmful-content categories while preserving the self-harm scope decision.
- [ ] Define independent labelling and adjudication steps precisely.
- [ ] Make C10c a gate or explain its different treatment.
- [ ] Record the full-plan decision, second-labeller decision, and indirect-injection decision in Part K.
- [ ] Freeze source files, candidate pools, seeds, manifests, hashes, rubrics, models, prompts, FAQ version, release commit, retry rules, and pass thresholds.

### Recommended before execution

- [ ] Add a second synthetic customer for a genuine cross-customer authorization test, if feasible without changing project scope materially.
- [ ] Include pass cases in the manual audit sample, not only judge-flagged cases.
- [ ] Stratify R5 planted claims across several error types.
- [ ] Identify the three distinct defects used for R7 fault seeding.
- [ ] Confirm every fixture contains synthetic—not real—personal or credential-like data.
- [ ] Define how partial run failure, quota exhaustion, and resumed runs affect manifest integrity.
- [ ] Prepare the result-table template before running so no metric is omitted after results are known.

## Final approval recommendation

After the required freeze-checklist items are incorporated, v5.1 is suitable for the final capstone evaluation. The recommended decisions are:

- **Full test plan:** yes.
- **Student as second labeller:** yes.
- **Isolated planted-FAQ injection test:** yes.

The final report should claim strong performance only where targets are demonstrated or met on estimate, and should claim compliance and risk success only as **all in-scope critical gates passed on the frozen evaluation sets**. That wording is both impressive and defensible.
