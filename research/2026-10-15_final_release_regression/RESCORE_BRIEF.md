# Rescore brief: release-run replies (v5.1), version 2

**Version 2 (29 Oct 2026).** Version 1 of this brief left out several rules
the project owner set by hand during the October reviews (recorded in
`docs/report/evaluation/RESULTS_v5.1.md`, sections 3.7 to 3.9 and the 16 Oct
re-review), and it did not point to the corrected ambiguity labels. This
version adds them; nothing else changed. The version 1 output
(`results/rescore_independent.jsonl`) stays in the repo as a record. Write the
version 2 output to the new file names given under Output.

You are an independent reviewer. Score the saved replies of the 15 October
release run directly against the frozen criteria, and write one per-case
verdict table. Every reported metric will then be derived from that table
alone.

This replaces two weaknesses in the current scoring: 45 replies kept their
5 October verdicts because their evidence matched, and B0a and B2 use two
different completeness lists. Do not run the chatbot or call it. No new
model output is needed; score only what is saved.

## Scope

Score **all 234 cases** in the input file, so one reviewer judges the whole
set. Score the **`new`** reply only (the 15 October release run). The `old`
reply is the 5 October run; ignore it.

**Score blind.** Do not read `old_metric`, `old`, `review_flags` or
`rough_old_outcome` before you have written your verdict for a case. They
hold the previous verdicts.

## Inputs (all in the repo)

| File | What it holds |
|---|---|
| `research/2026-10-15_final_release_regression/results/paired_response_review.jsonl` | One row per case. Use: `case_id`, `question`, `coverage` (FULL / PARTIAL / NO_DATA), `ambiguous`, `answer_key` (frozen before the run), `acceptable_routes` (frozen), `reference_faq` (the FAQ text that answers the question), and `new` (`reply`, `nav` buttons, `forms`, `soft` = offer of a person, `preflight`, `parents` = FAQ entries the bot was allowed to use). `old_metric.phase` says whether the case is in the end-to-end sample (`end_to_end`) or the FULL top-up |
| `backend/data/knowledge_base.db` | SQLite. `knowledge_parents(parent_id, content)` holds the full text of every FAQ entry in `new.parents`. `Precision_Reference_Table(placeholder, exact_value)` resolves `{{faq_...}}` tokens in that text to the exact values the customer saw |
| `research/2026-10-04_v51_holdout/labels/ambiguity_review.jsonl` | **Corrected ambiguity labels.** Five questions were relabelled ambiguous in the owner-requested review of 5 Oct. The `ambiguous` field in the paired file still holds the old value for them, so use this file's `ambiguous` value for its five `item_id`s (match on the paired file's `item_id`) |
| `docs/report/evaluation/METRICS_DESIGN_v5.1.md` | The metric definitions (B0a, B0b, B1, B2, B5, C2, C3). This brief summarises them, together with the owner's review rules below; where the two differ, follow this brief |
| `docs/report/evaluation/RESULTS_v5.1.md` | Sections 3.7 to 3.9 and the 16 Oct "Owner re-review of the B5b failures" record where the owner's review rules came from. Read the rules there if a case is unclear. Do not use the per-case verdicts in the review files they cite |

## Rules

Judge each reply as a careful banking reviewer would. The customer sees the
reply text, any buttons (`nav`) and any forms (`forms`); all of them count.

**Honest-limit principle.** No chatbot can answer from content it does not
have. An honest "no information", or a relevant clarifying question, on a
question or the part of one that the FAQ cannot answer is **correct**. Only
an invented, wrong or wrongly refused answer fails.

**B2, complete answer.** Cases: `coverage = FULL` (165). These are the
owner's review rules (RESULTS_v5.1 sections 3.7 and 3.8).
- Pass when the reply gives what the customer asked, judged against
  `answer_key` and `reference_faq`. A correct app button, form or own-data
  result that serves the need also counts.
- **Answer what was asked.** The answer key may list more facts than the
  customer asked for. A key fact that answers a question the customer did not
  ask is optional, for example the ATM fee on "which ATMs can I use", the
  police report on "how do I cut off the card", or "PayNow is free" on "what
  does a transfer cost".
- **A clarifying question passes when the question is ambiguous** (use the
  corrected labels), **or when a needed detail is missing or a term is
  unclear**, even if the question is not labelled ambiguous. Examples the
  owner accepted: "Are you available in my country?" (which country),
  "top off transfer" (unclear term), and currencies asked without naming a
  product. The clarifying question must be relevant to the customer's need.
- **"No information" passes when answering would need an assumption**, for
  example concluding there is no children's account from a minimum-age rule,
  or no currency exchange from an SGD-only transfer rule.
- **Own-case questions.** When the customer asks about their own case (their
  transfer, charge, refund, withdrawal or card) and the FAQ offers only a
  contact route or general facts, "no information" passes.
- **A reasonable default reading counts** (cash withdrawal means the savings
  account at an ATM), and **a message that is not a question passes** when
  the reply responds sensibly.
- **"No information" fails when the FAQ plainly gives the answer.**
- A wrong refusal, a compliance block on an ordinary question, or an answer
  to a different question fails.

**B1, correct and relevant.** Cases: substantive replies only, meaning the
reply gives a banking answer drawn from FAQ evidence (`new.parents` is not
empty), on a question with `coverage` FULL or PARTIAL. A reply that only
states a limit, only asks a question, or is only a button is **not
substantive**: set `substantive = false` and leave B1 and C2 empty.
- Pass when the answer has no major error and addresses the question. A
  correct but incomplete answer passes B1 (it fails B2 instead).

**C2, grounded.** Same cases as B1.
- Pass when every factual claim is supported by the text of the FAQ entries
  in `new.parents` (resolve `{{faq_...}}` tokens with
  `Precision_Reference_Table`), or by a button or form the reply carries.
- A `{{data-...}}` token is the customer's own value (for example their
  balance) from a data lookup. By design the value goes only to the
  customer's screen and is not in the saved record. Treat the token as
  supported by that tool result; judge only the words around it.
- Fail if any claim is unsupported: a wrong number, an invented step,
  invented eligibility, implied account access or advice.

**B5b, suitable channel.** Cases: `coverage` FULL or PARTIAL, except
`h1_h1_unable_to_verify_identity_1` (219).
- Pass when the final reply sends the customer somewhere suitable: a correct
  answer, a self-service path (button or form), a relevant clarifying
  question, or a route to a person. `acceptable_routes` lists the routes
  frozen as acceptable for that question; more than one may be correct. An
  irrelevant extra button does not count as success.
- **A reply that passes B2 is a suitable channel.**
- **An honest "no information" is a suitable channel** (owner's rules,
  RESULTS_v5.1 section 3.9 and the 16 Oct re-review) when:
  - the question is about the customer's own case, which the bot cannot see;
  - the FAQ entries do not actually answer the question (this becomes a
    signal to add an FAQ entry); or
  - the FAQ offers only a contact route.
  It is **not** suitable when the FAQ plainly answers the question.
- **Attack-guard false blocks are excluded** from the quality metrics, by the
  owner's scope decision: the guard is a third-party model the project does
  not govern. If `new.preflight` shows the prompt-attack check blocked an
  ordinary question, set `guard_false_block = true` and `b5b_suitable = null`,
  and say so in the reason.

**Handled correctly (feeds B0a and B0b).** Every case.
- FULL: handled correctly when B2 passes.
- PARTIAL: handled correctly when the available part or an honest limit is
  given with nothing wrong (B1) or unsupported (C2).
- NO_DATA: handled correctly when the reply is an honest limit or a relevant
  clarifying question.
- `person_offered`: true when the reply offers or routes to a person
  (`new.soft` is true, or the text offers one).

**Exclusion.** `h1_h1_unable_to_verify_identity_1` failed because the model
provider was unavailable. Score it, but set `excluded = true`.

## Output

Write **`research/2026-10-15_final_release_regression/results/rescore_independent_v2.jsonl`**,
one JSON object per line, one line per case (234 lines):

```json
{
  "case_id": "h1_h1_card_arrival_1",
  "coverage": "FULL",
  "ambiguous": false,
  "phase": "end_to_end",
  "excluded": false,
  "guard_false_block": false,
  "substantive": true,
  "b1_pass": true,
  "c2_pass": true,
  "b2_complete": true,
  "b5b_suitable": true,
  "handled_correctly": true,
  "person_offered": false,
  "reason": "One or two sentences: what the reply did and why it passes or fails.",
  "reviewer": "GPT-6.1 Sol",
  "scored_at": "2026-10-29"
}
```

- Use `null` where a field does not apply: `b2_complete` for non-FULL cases,
  `b1_pass` and `c2_pass` when `substantive` is false, `b5b_suitable` for
  NO_DATA cases and for attack-guard false blocks.
- `reason` is required for every fail, and short for passes.
- Copy `phase` from `old_metric.phase`; reading that one field is fine.
- `ambiguous` is the label you used, after the corrections from
  `ambiguity_review.jsonl`.

Also write **`results/rescore_independent_v2_notes.md`** with:
- how many cases you scored and anything you could not judge, with the
  reason;
- any place where you read a rule differently from this brief;
- **only after scoring**, a comparison with the previous verdicts in
  `old_metric` (how many changed per metric and which cases), if you choose
  to report it.

## Do not

- Run or call the chatbot, or any model, to regenerate replies.
- Edit any other file in the repo.
- Compute or report the final metrics. They will be derived from your
  table by a separate script, so every number comes from one source.
