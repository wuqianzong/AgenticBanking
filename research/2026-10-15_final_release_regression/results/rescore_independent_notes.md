# Independent release-reply rescore

Reviewer: GPT-6.1 Sol  
Scoring date: 2026-10-29

## Scope and source restrictions

Scored all 234 saved `new` release-run replies, including the provider-error case, with one verdict row per input case in original order. No case was omitted. Only the brief, the permitted paired-response file, the read-only knowledge-base database, and METRICS_DESIGN_v5.1.md were consulted. The chatbot was not run, no external model was called, and no replies were regenerated. No final performance metrics or comparison with previous verdicts were calculated.

The per-case review used the question, frozen coverage and ambiguity labels, answer key, reference FAQ, saved reply, buttons, forms, preflight outcome and adopted FAQ IDs. Adopted FAQ text and FAQ placeholders were checked against the two specified database tables. Previous replies, previous verdicts, review flags and rough outcome labels were not presented to the reviewer or used for judgments. Only `old_metric.phase` was extracted for the required output field.

This is a fresh case-by-case review, not a claim that the reviewer had no prior context: the preceding conversation already discussed aggregate results and limitations of the earlier evaluation. No previous per-case verdict comparison was performed after scoring either.

## Rule clarification and operational interpretations

- The brief and metrics-design B2 wording differ: the design refers to every frozen key item, while the brief requires the facts actually requested. Before finalising the scores, the user explicitly answered “follow the brief.” Accordingly, B2 requires question-relevant facts, not an exhaustive recital of every answer-key or FAQ detail. Frozen coverage and ambiguity labels were retained even when a question could reasonably be read as ambiguous in ordinary conversation.
- Honest limits on PARTIAL and NO_DATA questions count as correctly handled when they do not introduce an error or unsupported claim. For FULL questions, an own-case limit may pass B2 where the evidence provides only general or contact-based information and cannot establish the customer's actual status, rate, reversal or diagnosis. A limit fails where the FAQ directly supplies the requested policy, restriction, procedure or timeframe. These exceptions do not make a bare limitation a successful B5b channel: without a useful answer, matching self-service path, relevant clarification or person route, B5b remains false.
- A matching app button or form counts where it actually serves the frozen need. A generic card-management button does not establish supplementary-card issuance, and an outgoing Send Money shortcut does not perform incoming funding. Security Settings counts for a Key/PIN reset route, but does not substitute for the login-screen Forgot Passcode procedure when app access is forgotten.
- B1 and C2 are evaluated only for FULL/PARTIAL replies giving banking facts from nonempty adopted FAQ evidence. Pure limits, questions, acknowledgements and button-only replies are not substantive. NO_DATA replies remain outside B1/C2 even if they contain supported general context.
- B1 distinguishes a relevant but incomplete answer from an answer to a different question. C2 checks all factual additions against adopted evidence, rather than treating a fact in a reference-only FAQ as adopted evidence. Unconditional statements that erase a material FAQ qualification, unsupported account access and instructions misapplied to another operation fail grounding.
- B2 and B5b are separate from C2: a reply can answer the requested fact or supply a suitable route yet contain an unsupported additional claim. The FULL handled-correctly field follows B2 exactly, as the brief explicitly prescribes; PARTIAL handling fails when a substantive answer fails B1 or C2.
- An ordinary question with a failed prompt-attack preflight is marked B5b false as explicitly required, even if a relevant button or clarification survives in the saved final reply. Generic operational-error messages are not treated as honest knowledge limits.
- `person_offered` includes an explicit bank support, Customer Care, hotline, call/chat or human-review route, as well as a true saved soft-escalation flag. It records an offer, not completed contact. A police-report recommendation alone is not treated as an offer of bank human support.
- The provider-error case `h1_h1_unable_to_verify_identity_1` was scored on its saved error response and marked `excluded: true`. Its B5b verdict is recorded as false rather than null; exclusion is represented by the dedicated flag. All other exclusion flags are false.

## Evidence limitations and judgment calls

All cases received the required binary judgments within the stated scope. These are reviewer judgments from saved evidence, not new observations of runtime behaviour.

The reply for `h1_h1_transfer_into_account_3` includes `{{data-855a728e}}` as an account-balance placeholder. The permitted saved fields do not include its resolved customer value or an own-data tool-result trace. Its actual displayed balance and provenance could therefore not be verified. The funding answer can be assessed, but C2 is conservatively false for the additional balance assertion lacking verifiable grounding in the permitted record; this is not evidence that the resolved balance itself was numerically wrong.

Some FAQ material is internally inconsistent or scoped differently across entries, notably general card-replacement fees versus metal-card replacement fees, and ATM network descriptions. Grounding was assessed against the adopted entries with their stated scope; reference material and the frozen key were used to assess completeness. The output reasons identify consequential omissions, contradictions and scope errors rather than assuming that every inconsistency originated with the chatbot.

The judgments do not establish completed handover, successful execution of a button/form, account access, or any behaviour beyond the saved reply and listed evidence. No source outside the permitted files was used to infer those outcomes.

## Output validation

The JSONL was checked for exactly 234 unique input case IDs in input order, the specified fields and types, copied coverage and phase, required null applicability, the single specified exclusion, nonempty reasons, and consistency of FULL handling with B2. The two requested output files are the only repository files written. No rates, bounds, aggregate metric results or previous-verdict changes are included.
