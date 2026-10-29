# Independent release-reply rescore — version 2

Reviewer: GPT-6.1 Sol  
Scored: 2026-10-29  
Authority: RESCORE_BRIEF.md, version 2 (29 October 2026)

## Scope and blindness

All 234 saved release-run replies were reviewed again, case by case, under version 2. Every case received a verdict; none was omitted or left unscored. The output preserves input order.

Only the brief and its permitted input files were consulted: paired_response_review.jsonl, the read-only knowledge_base.db, ambiguity_review.jsonl, METRICS_DESIGN_v5.1.md, and the specified owner-rule sections of RESULTS_v5.1.md. FAQ text was read from the adopted parent entries and numeric FAQ placeholders were resolved through Precision_Reference_Table. Reference FAQs and the frozen keys were used to distinguish a direct answer from related material requiring an assumption.

The review projections omitted old replies, old verdicts, review_flags and rough_old_outcome. Only old_metric.phase was extracted for output, after the judgments were drafted. Version 1 output files and cited per-case owner-review files were not opened or used to populate this table. No verdicts were carried forward by matching evidence or reply text.

The reviewer necessarily retains prior conversational context, including having performed version 1 in this same chat. The permitted RESULTS rule-provenance passages also contain historical outcomes alongside the rules. Thus “blind” means the earlier verdict fields and verdict files were withheld from this pass, not that prior contextual knowledge could be erased. Decisions were made from the saved new replies and version 2 rules, without a previous-verdict comparison or running performance totals.

No chatbot, model endpoint, judging script, regeneration, rerun or external source was invoked. This assistant performed the textual review directly. No final metrics, confidence intervals, metric pass totals or previous-verdict changes were computed.

## Authority and interpretations

Version 2 takes precedence over conflicting metric-design wording and earlier owner-review wording. In particular, the later own-case/contact-only honest-limit rule supersedes the earlier statement that every omitted contact route is a miss. No intentional departure from version 2 was made. The following judgment boundaries explain how its rules were applied:

- **Question-relevant completeness.** B2 requires what was asked, rather than every fact listed in a key. A valid usable ATM list need not enumerate every overseas alternative; a transfer-cost answer need not separately repeat that PayNow is free. A routine savings-account ATM withdrawal is a reasonable default. In cash_withdrawal_charge_2, that default makes the free savings-ATM answer complete even though contradictory additional effective-date text fails B1 and C2.
- **Corrected ambiguity.** The ambiguity-review values override the paired-file value for item_065, item_181, item_045, item_261 and item_074. All other labels are retained. A relevant clarification can pass B2 even without an ambiguous label when the country, product, meaning of a term or verification type is needed. An ambiguous label alone does not rescue an answer: a replacement-only form does not resolve replacement versus an additional card, and delivery timing does not resolve which card the customer wants to obtain.
- **Statements and requests.** A sensible response to a FULL item that is merely a statement can pass B2 without reciting an unsolicited checklist. Imperative requests such as “show me how” still require the requested help. The prescribed PARTIAL handling rule remains available information, an honest limit or relevant clarification without wrong or unsupported content; a bare acknowledgement in pending_transfer_1 supplies none of those and is not credited as handling.
- **Honest limits.** Limits on actual status, actual causes, actual rates or actions the chatbot cannot perform pass where the FAQ gives only general facts or contact guidance. A direct policy or procedure is different: claiming no guidance about automatic incoming funding, supplementary-card fees, overseas use or generic pending-payment timeframes misses an available answer. The distinction is between an unavailable fact and an available method, not merely whether the customer uses “my.”
- **Refund boundary.** Refund_not_showing_up_1 explicitly disclaims visibility of the actual refund status and passes. Refund_not_showing_up_2 broadly disclaims guidance on finding the refund despite a directly applicable FAQ explaining where refunds are credited; that is scored as a missed available method. Similarly, the combined cheque/cash balance question does not excuse omitting the explicit cheque non-acceptance rule.
- **Assumptions and related evidence.** A minimum-age rule does not establish that a children's-account product does not exist. SGD-only transfer rules do not establish a separate currency-exchange service policy, every unspecified funding method, or an exchange-rate formula. Account-application residency rules do not establish a complete list of countries of operation. Verification of a top-up funding card is not automatically activation of a new Horizon card. Honest limits on those unstated connections are accepted. Explicit cross-border transfer questions, by contrast, are directly addressed by the transfer restriction.
- **Substantive scope.** B1 and C2 apply only to banking facts in FULL/PARTIAL replies with adopted FAQ evidence. Pure limits, clarifications, acknowledgements and button-only replies are not substantive. NO_DATA replies remain outside B1/C2 even if they include supported general context.
- **Grounding and correctness.** C2 checks every added factual claim against adopted evidence, not all reference material. Supported words may become unsupported when moved into a different service or stripped of a qualification: a late-fee waiver is not a transfer-fee waiver, and a security lock that blocks access is not a remedy for an existing withdrawal restriction. A shortcut's existence supports opening its named screen, not an additional claim that the screen bypasses login recovery or performs incoming funding.
- **Metric separation.** A requested answer can be complete while an additional claim fails C2, or fails B1 through a contradiction. Likewise, a supported assistance route can remain suitable despite another incorrect claim. B5b is true whenever B2 is true, except for the explicit guard-block exclusion. FULL handled_correctly follows B2 exactly; PARTIAL handling fails when its substantive content fails B1 or C2.
- **Customer-data tokens.** The balance token in transfer_into_account_3 is accepted as a supported own-data result as version 2 explicitly directs. Its display-only value is neither requested nor reconstructed; only the surrounding words are judged.
- **Human contact.** person_offered includes explicit bank Customer Care, hotline, app call/chat or human-review routes and any saved soft offer. It records an offer, not a completed handover. A police-report recommendation alone is not counted as an offer of bank human support.

## Guard and provider exclusions

For these ordinary H1 questions, a saved preflight value of fail is treated as an attack-guard false block: guard_false_block is true and b5b_suitable is null. Other fields still describe the saved reply using their specified applicability; a surviving matching PIN-reset button can therefore pass B2 even on a guard-flagged row. A generic operational fallback is not reinterpreted as an honest knowledge limit.

The ordinary compliance refusal marked non_compliant, and product refusals marked declined, are assessed as refusals rather than silently excluded as attack-guard blocks.

The named provider-unavailable case h1_h1_unable_to_verify_identity_1 is scored on its saved error response and has excluded true. Its error_malicious value is treated as the provider error identified by the brief, not proof of an actual guard block. Its B5b verdict is recorded as false; the separate exclusion flag removes it from the stated metric scope. All other excluded flags are false. Guard exclusions are represented by guard_false_block, rather than changing excluded.

## Evidence limits and validation

All required judgments were possible under the brief's conventions. The permitted record does not independently establish actual customer balances, completed support contact, successful button/form execution, real account-status access or runtime behaviour beyond the saved response. No outside files were used to infer those outcomes. The own-data-token convention resolves the scoring question without claiming independent numeric verification.

The FAQ contains scope differences and apparent conflicts, including general replacement fees versus metal-card fees and ATM-network descriptions. These were assessed with their stated scope; conflicts were not silently resolved through outside banking knowledge.

Structural validation checks exactly 234 unique case IDs in input order, corrected ambiguity values, copied coverage and phase, the required fields and types, null applicability, the named provider exclusion, guard flags, reasons, FULL handling equal to B2, and the B2-to-B5b suitability implication subject to guard exclusion. Validation does not compute the final metrics.

Only rescore_independent_v2.jsonl and rescore_independent_v2_notes.md are written. The version 1 files are left unchanged.

## Correction: normal-pipeline preflight values (29 October 2026)

The earlier interpretation of preflight = "fail" as an attack-guard block was incorrect. The six turns below proceeded through the normal pipeline and produced replies; a true guard block would show preflight = "error_malicious". This correction supersedes the earlier preflight-to-guard mapping in these notes. The separately identified provider-unavailable case remains unchanged under the brief's explicit exclusion.

For each listed case, guard_false_block is now false and B5b is judged on the saved final reply under version 2:

| Case ID | b5b_suitable | Reason |
|---|---|---|
| h1_h1_declined_card_payment_2 | true | Asks a relevant clarification distinguishing AutoPay from a card purchase or bank transfer. |
| h1_h1_failed_transfer_2 | false | Generic retry/rephrase fallback supplies neither an honest knowledge limit nor a suitable answer, clarification or support route. |
| h1_h1_passcode_forgotten_2 | false | Security Settings alone does not supply the Forgot Passcode login-recovery procedure. |
| h1_h1_passcode_forgotten_4 | false | Security Settings alone does not supply forgotten-passcode recovery. |
| h1_h1_pin_blocked_2 | true | The Security Settings button matches the frozen acceptable Key/PIN-reset screen route and the unchanged B2 pass. |
| h1_h1_transfer_not_received_by_recipient_1 | false | Generic retry/rephrase fallback supplies no suitable answer, honest knowledge limit, relevant clarification or support route. |

Only guard_false_block and b5b_suitable changed in these six JSONL rows. Every other field, including the original reason text, was preserved as requested; statements about guard blocks or null B5b values in those original reasons are superseded by this correction table. All other rows and the version 1 files remain unchanged. No model or chatbot calls were made and no final metrics were computed.
