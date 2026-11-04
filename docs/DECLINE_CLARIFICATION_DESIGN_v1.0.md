# Decline, Clarification, and No-Info Resolution v1.0

**Status:** Implemented and live-verified end-to-end. This is a living
document — update it in place as anything changes.

## 1. What this covers

How a turn that can't be answered from real evidence — because the topic
is out of scope (DECLINE), the request is ambiguous (CLARIFY), or a search
was genuinely attempted and found nothing (NO_INFO) — is resolved,
generated, and audited. The design went through several rounds since the
first version of this document: an initial fix to base-product
identification and honest DECLINE wording, then a fully separate
LLM-phrased-and-audited path for CLARIFY/DECLINE text, and finally the
current design below, which unifies all of this with ordinary answer
generation and removes free-form LLM phrasing from the evidence itself
entirely.

## 2. Base-product identification (Rule 2, PRODUCT) — unchanged

Human intuition strips a qualifier before checking whether a product
exists: "business credit card" is still fundamentally a credit card. The
prompt teaches this as a two-step, generalizable move instead of a
case-by-case list of qualifiers:

1. Strip any descriptive qualifier, tier name, or marketing brand from the
   wording (e.g. "Flexible Loan" → base family "loan") and match the base
   family, not the qualified name, to a Matrix noun.
2. Classify what kind of qualifier it was:
   - **Tier/brand/plan name** (e.g. "Flexible Loan", "Gold Card") — the
     matched base Matrix noun becomes PRODUCT immediately; the qualifier
     doesn't need separate clarification. Whether that exact named variant
     is actually covered is left to the sufficiency auditor downstream,
     which has real retrieved evidence to check it against.
   - **Non-individual customer segment** (business, corporate,
     institutional) — this does *not* become PRODUCT for SEARCH purposes.
     This FAQ corpus was confirmed, by direct inspection of the live vector
     store (2,118 chunks; zero contain "business credit card" or "business
     account"; the entire `product_tag` taxonomy is exactly the Matrix's
     individual-customer product set), to be scoped exclusively to
     individual/personal banking. A business-segment qualifier is treated
     as a scope boundary, not an ambiguity to search around.

`customer_component` — the wording judged by the evidence audit — is
explicitly preserved unchanged throughout; only the internal
`target_commercial_product` extraction is affected by base-product
stripping.

## 3. Three evidence categories, all deterministic — never LLM-phrased

Three helpers in `nodes.py`, none of which ever call an LLM:

- `_decline_evidence_text(category)` — looks up a fixed sentence in
  `_DECLINE_CATEGORY_EVIDENCE` keyed by `category`:
  - `"out_of_scope_product"` — the base product family has no Matrix entry
    at all (margin trading, crypto). *"This is not a product or service
    Horizon offers."*
  - `"individual_customers_only"` — the base product exists, but the
    component names a non-individual segment. *"This assistant's
    information covers individual, personal banking products only;
    business or corporate banking questions aren't covered here."* This
    never asserts whether Horizon actually does or doesn't serve that
    segment in reality — only that this assistant's information doesn't.
  An invalid or missing category safely defaults to `out_of_scope_product`
  (triage's own separate decline vocabulary — `non_compliant`/
  `non_financial` — is a different enum entirely and never accepted here).
- `_clarification_evidence_text(unresolved_request)` — always
  `f"Could you clarify what you mean by \"{unresolved_request}\"?"`. Since
  `unresolved_request` is already a trusted, extracted term (not free
  prose), this is exactly as safe as a fixed template while still naming
  the specific ambiguity. **Only for `node_02e`'s intent-level
  clarifications** -- `unresolved_request` there genuinely is a term the
  customer used, so "what you mean by X" is an accurate framing.
- `_evidence_gap_clarification_text(unresolved_term)` — always
  `f"Could you tell us more about {unresolved_term}?"`. Deliberately a
  **different** template for `node_03c`'s evidence-authored clarifications
  (§4a): `unresolved_term` there is the sufficiency auditor's own
  characterization of a gap in the evidence (e.g. "documents for online
  submission"), not necessarily anything the customer said. Reusing
  `_clarification_evidence_text`'s "what you mean by X" framing for this
  case was live-confirmed as a real bug -- it produced sentences like
  *"Could you clarify what you mean by 'documents for online
  submission'?"* when the customer had only asked "what's the required
  docs?" and never used that phrase at all, reading as the assistant
  misquoting them (the same class of bug §1's original fix addressed for
  triage, now recurring one layer downstream). This template instead asks
  for the missing detail directly, which is accurate regardless of whether
  the customer's own wording resembles `unresolved_term`.
- `_no_info_evidence_text(resolved_subquery)` — `f"I don't have
  information available regarding: {resolved_subquery}."`, or a generic
  fallback sentence when `resolved_subquery` is empty. Used both for a RAG
  task whose retries exhausted with zero usable evidence
  (`resolution_status == "CLARIFICATION_REQUIRED"`, see
  `TOOL_CALLING_v3.0.md`), and for a fresh SEARCH resolution whose
  product/intent pairing the matrix itself rejects (see §6 below). **One
  exception, see §4a**: a `CLARIFICATION_REQUIRED` task whose own
  `unresolved_term` is non-empty (the auditor judged the *request itself*
  under-specified, not merely undocumented) skips this fixed no-info
  template entirely -- it's instead fully represented by its own
  auditor-authored `clarification_tasks` entry, deterministic in exactly
  the same way as this one, just naming the ambiguous term instead of a
  genuine coverage gap.

**Why deterministic, not LLM-phrased (this document's biggest change from
v1's design):** an earlier version had `node_02e` phrase
`customer_facing_question`/`customer_facing_message` itself, audited by a
dedicated combined-audit call (`_audit_clarification_decline_blocks`,
removed — see §7). That closed the "who decides, who phrases" reliability
gap it targeted, but left a narrower one: nothing verified the *evidence
text itself*, only the final customer-facing response against it. Making
these three fields fixed templates over already-trusted, already-extracted
data removes that gap at the source — there's no longer a hallucination
surface for the evidence to fail, because there's no generation step
producing it. `clarification_guesses` (the tappable chip guesses, see §4)
remain LLM-generated, since they're offered guesses a customer can dismiss,
not asserted facts — a different risk profile that doesn't need this same
treatment. §4a extends this same LLM-generated-guesses treatment to a
second case beyond Intent Clarification: node_04b's own optional
terminal-marker clarification, once §4a made a genuinely ambiguous
zero-evidence task eligible for it too.

## 4. Clarification is generic, not product-specific

Any node that determines a request is genuinely ambiguous can trigger
CLARIFY — not only the intent formulator (§4a extends this principle to
`node_03c`, catching ambiguity RAG's own total failure reveals after the
fact). Every clarification-raising node shares one standard contract (see
§4a for the full field list), built around a single standing rule: **who
raises it, who provides the evidence and reason** — scoped strictly to
that node's own job. `node_02e`'s `IntentClarificationTask` carries
`unresolved_request`, `clarification_reason`, and `clarification_guesses`
(`List[ClarificationGuess]`, 0–3 entries, most-likely-first). Each
`ClarificationGuess` is a reasonable, grounded PREDICTION of what the
customer likely means — never a report of anything the customer has
actually said — and has:

- `label` — a short chip caption (e.g. "Personal Loan").
- `message` — a complete, natural sentence sent to the assistant if
  tapped (e.g. "I'm asking about a personal loan.") — never a bare word,
  since it becomes the next turn's actual user input.
- `reason` — internal-only justification naming the specific Matrix fact
  or conversation detail that makes the guess plausible.

Guesses are populated only when genuinely inferable from context (e.g. a
term with exactly one plausible Matrix match); left empty when there's no
real basis to guess. They attach to whichever response block ends up
citing that clarification's `subquery_id` in `sourceTaskIds` — never a
separate mechanism from the block itself.

## 4a. A second author for clarification: node_03c, not node_04b

**This section describes the current design. An earlier version (kept
below in git history) gave `node_04b` an optional judgment call --
"would a clarifying question help here" -- with the auditor only
supplying a boolean (`likely_ambiguous_request`). That version was
short-lived: it put the actual reasoning about *what's ambiguous and what
the customer probably means* in the one node that never reviewed the
evidence at all, working from a thin `{resolved_subquery, status}` marker
instead of the retrieved parents and conversation the auditor already
had in front of it. It also broke the "who raises it, who authors it"
symmetry every other clarification type in this document already
follows (§3, §4): only this one path let the *relaying* node also be the
one deciding whether and what to ask.**

The intent formulator (`node_02e`) only gets one look at a request, before
any retrieval happens. If it judges something ambiguous enough, it becomes
an `IntentClarificationTask` per §4 above and never reaches RAG at all. But
if it doesn't catch the ambiguity and RAG proceeds, two more situations can
still reveal that a clarifying question would genuinely help, and the node
best positioned to recognize either one is `node_03c_sufficiency_auditor`
-- the only node that has actually reviewed the retrieved parents against
the request:

1. **`PARTIALLY_SUFFICIENT`**: some evidence was found, but a meaningful
   part of the request has none, and an additional customer detail could
   plausibly unlock a different, more complete answer for that part.
2. **`INSUFFICIENT` + genuine ambiguity**: nothing was found at all, and
   the reason is that the request itself is under-specified (e.g. "the
   fee" without saying which fee), not that the topic is simply
   undocumented.

`TaskSufficiencyItem` (see `TOOL_CALLING_v3.0.md`) now carries this
authoring responsibility directly, on the same per-round call that already
judges `status`/`reason`/`likely_ambiguous_request` -- no extra LLM call:

- `unresolved_term` (empty by default): the auditor sets this, naming the
  specific ambiguous/unresolved part, in exactly the two cases above. Left
  empty whenever asking wouldn't genuinely help (e.g. a `PARTIALLY_
  SUFFICIENT` gap that's simply undocumented, not disambiguatable by any
  customer detail) -- a clarifying question is not owed just because a
  task didn't fully resolve.
- `clarification_reason`: distinct from `reason` above (which explains the
  evidence verdict generally, for every status) -- specific to why THIS
  part needs clarification, naming a real detail in Original User Message
  or Trusted Conversation Context.
- `clarification_guesses` (`List[ClarificationGuess]`, same shape §4
  already uses): 0-3 guesses, populated only when `unresolved_term` is
  non-empty and genuinely inferable -- grounded in the specific candidate
  parents just reviewed or Trusted Conversation Context, explicitly
  **never** the Product/Intent Matrix directly (that's node_02e's domain,
  not an evidence auditor's).

When a task terminates (via the same retry-ladder finalization §2 of
`RAG_RETRY_EFFORT_v1.0.md` describes) with a non-empty `unresolved_term`,
`node_03c` builds a full clarification entry itself --
`_evidence_gap_clarification_text(unresolved_term)` (§3 -- NOT
`_clarification_evidence_text`, a deliberately different template) for the
deterministic question text, plus the validated `clarification_guesses` --
and appends it to the single unified `clarification_tasks` state list (see
§4b) alongside whatever `node_02e` already put there this turn.

**`node_04b` is now a pure relay for every clarification, with no
judgment call of its own anywhere in this design.** Its own `clarification`
field narrows back down to exactly what §5 describes below: a plain,
optional-only-in-the-sense-of-whether-a-gap-exists neutral availability
notice for a `PARTIALLY_SUFFICIENT` task node_03c did *not* think was
worth a clarifying question. It never generates a question or guesses of
its own, and a `CLARIFICATION_REQUIRED`-but-ambiguous task is fully
represented by its `clarification_tasks` entry -- it no longer appears as
a terminal marker or a no-info entry at all, so there is nothing left in
that path for `node_04b` to compose.

A guard mirrors `_rag_gap_still_matters` (§6): a `CLARIFICATION_REQUIRED`
task another tool already resolved this round is excluded from getting a
`clarification_tasks` entry at all, the same reasoning that already
applies to a no-info statement.

`_generate_evidence_only_response`'s narrower dispatch path benefits from
this too: since `clarification_tasks` entries are now what's checked (not
the old `terminal_context`, which never influenced that dispatch), a turn
whose only content is one ambiguous, zero-evidence task now correctly
qualifies for the cheaper evidence-only sub-prompt instead of always
falling through to the full
flexible prompt.

## 4b. One standard clarification contract, keyed by subquery_id

Originally `node_02e`'s and `node_03c`'s clarifications lived in two
separate state lists (`intent_clarification_tasks` and
`evidence_clarification_tasks`), each with its own synthetic
`clarification_id` string (`intent_clarification_N` /
`evidence_clarification_{task_id}`) as the only way to trace an entry back
to what it was about. Every downstream consumer merged the two lists
before doing anything with them regardless (`_build_clarification_context`,
node_06's audit, node_09's escalation summary, node_11's fallback), which
was the signal that the split was authoring-time residue, not a real
business distinction -- and the string-based id was itself never a stored
reference back to the originating subquery, just a parsed-out substring.

Both were collapsed into one state list, `clarification_tasks`, with one
standard contract regardless of which node raised the entry:

- `subquery_id` -- the sole link back to `financial_subqueries`. Mutual
  exclusivity already guarantees this is unique across the whole list: a
  subquery resolves to exactly one of SEARCH/CLARIFY/DECLINE in `node_02e`
  (so it can appear in `clarification_tasks` XOR ever reach a `node_03c`
  task at all), and a SEARCH task terminates with at most one clarification
  in `node_03c`. No separate clarification id is needed.
- `originator` -- `"intent_formulator"` or `"sufficiency_auditor"`.
- `scope` -- a fixed, one-line statement of the originator's own job
  (`_CLARIFICATION_SCOPE_BY_ORIGINATOR`), e.g. "Identifying the correct
  product and intent for this request, before any evidence retrieval was
  attempted" vs. "Reviewing whether retrieved evidence fully answers this
  request." This makes the "who raises it, who provides evidence and
  reason -- scoped to that node's own job" principle (§4) something
  node_06's audit can actually check field-by-field, rather than only a
  convention each prompt follows on trust.
- `clarification_reason` -- internal-only, must name a real detail from
  the customer's own input/history, and must stay inside that entry's own
  `scope` (an `intent_formulator` reason can't be an evidence-sufficiency
  judgment, and vice versa).
- `clarification_evidence` -- the deterministic, ready-to-relay question
  (§3's two distinct templates, chosen by `originator`).
- `clarification_guesses` -- 0-3 `ClarificationGuess` entries (renamed
  from `ClarificationReply`/`candidate_replies`: the old name read as
  "replies that exist," risking the model treating its own invented
  guesses as observed data; `clarification_guesses` states plainly that
  each is a PREDICTION offered to unblock the task if correct, never a
  report of anything the customer actually said).

`_build_clarification_context` now just returns `clarification_tasks`
directly -- no merge logic left, since there's only one list. `node_03c`'s
own accumulation still deduplicates before appending (a task finalized
with a clarification in an earlier retry round must not be regenerated
every time a *different* task in the same turn keeps retrying), now keyed
on `subquery_id` instead of the old `clarification_id` string -- the more
robust key, since it's the field the whole contract is already built
around rather than a separately-maintained identifier.

## 4c. No stored clarification_evidence: compose, don't relay

§4b's `clarification_evidence` (a fixed template, `"Could you clarify what
you mean by X?"`) was audited identically to `decline_evidence`/
`no_info_evidence`: generation could only paraphrase it "preserving its
exact meaning." For a decline or a no-info statement that strictness is
correct -- both assert a boundary or an absence of information, and
loosening the wording risks misstating it. A clarifying question asserts
nothing, so it carries none of that factual risk; the strictness only
constrained *phrasing*, which is why every clarification read identically
regardless of the customer's own language, tone, or the style-
personalization every normal answer already gets.

The fix: demote `clarification_evidence` out of the persisted contract
entirely. `unresolved_request` comes back as its own field (previously
folded into the template and discarded); generation composes its own
question from `unresolved_request` + `scope` + `clarification_guesses` +
the ordinary conversation context already in its prompt, the same way it
composes a real answer from RAG evidence rather than relaying a fixed
string. The two templates (`_clarification_evidence_text`,
`_evidence_gap_clarification_text`) still exist, but only as
`_clarification_fallback_text`'s ingredients -- computed on demand, never
stored, used only by node_11's final fallback and the deterministic
completeness nets when generation produces nothing usable for an entry.

`clarification_reason` moves further inward too: it was designed as a
forcing function on the *authoring* node ("if you cannot state a concrete
reason, don't offer this at all" -- node_02e/node_03c's own prompts still
require this), not as something a second party verifies. It is no longer
shown to generation (it never told generation how to phrase anything
useful) or to node_06's audit (auditing the customer-facing text against
information generation never had access to risks a false veto: the
generator can't be faulted for failing to reflect a fact it was never
given). `originator` is excluded from both for the same reason -- `scope`'s
own descriptive text already carries whatever distinction the audit needs
(e.g. whether Product/Intent Matrix grounding is appropriate for a guess).

A new helper, `_clarification_prompt_view(entries)`, is the single
projection used for *both* the generator's prompt and node_06's audit
context (`subquery_id`, `scope`, `unresolved_request`,
`clarification_guesses` only) -- guaranteeing by construction that the
audit can never see more than the generator did.

## 4d. Four originators, not two: triage and the task dispatcher

The "who raises it, who provides evidence and reason -- scoped to that
node's own job" principle (§4) generalizes to every node in the pipeline
that makes a judgment call about what the customer wants, not just the two
that happened to need it first. The complete, closed inventory:

1. **`node_02d` (triage)** -- ambiguity in its own per-component
   classification. **Updated**: triage now always decomposes the whole
   turn into components (`TOOL_ELIGIBLE`/`NON_FINANCIAL`/`CLARIFICATION`,
   see `TOOL_CALLING_v3.0.md`'s "Triage decomposes per-component, not
   per-turn" section) rather than picking one whole-turn `triage_status`
   for everything -- `NON_COMPLIANT`/`ESCALATION` remain whole-turn vetoes,
   but a component tagged `CLARIFICATION` is scoped narrowly to "is THIS
   component itself a genuine banking need or a non-financial aside,"
   never a content ambiguity (a vague-but-real ask is `TOOL_ELIGIBLE`
   instead, letting `node_02e`/`node_03a`/`node_03c` resolve it downstream
   exactly as this section's other three originators already do). Since
   triage decomposes before tagging, a `CLARIFICATION` component always
   has its own real `subquery_id` -- no synthetic id is needed anymore.
   A turn with zero `TOOL_ELIGIBLE` components (all `NON_FINANCIAL`/
   `CLARIFICATION`) routes straight to `n19` (skipping `n14` tool planning
   entirely, since there's nothing to plan tools for), reusing the exact
   same proven `n19 -> n8` path node_02e's own clarifications already took.
2. **`node_02e`** -- product/intent (existing, §4).
3. **`node_03a` (task dispatcher)** -- new: which registered tool
   capability actually addresses an otherwise well-formed subquery, when
   two or more are each independently plausible with materially different
   outcomes (e.g. a self-service deeplink vs. a formal service-request
   submission) and nothing in the subquery's own wording lets the planner
   prefer one. Deliberately narrow -- an imperfect or non-existent match
   still means "return no task," not a clarification; this is reserved for
   a genuine tie between real options, never invoked merely because
   relevance-matching came up empty.
4. **`node_03c` (sufficiency auditor)** -- evidence sufficiency (existing,
   §4a).

Every other node is deliberately excluded, and this is a design choice, not
an oversight: `node_04b`/`node_11` relay only (§4a already settled this);
`node_06` audits someone else's already-generated output, so it never has
its own view of customer intent to be ambiguous about; `node_04a` and
`node_08`/`node_09` make no judgment call about what the customer wants --
`node_04a`'s retry/stop decision is a deterministic status rollup, and
`node_08`'s escalation offer and `node_09`'s hard-escalation handoff both
only ever fire on signals that are already unambiguous by the time they
run.

**A subquery already flagged for clarification is now excluded from
further tool planning**, in both `node_03a` and `node_04a` -- fixing a real
bug found while wiring the task dispatcher: `node_02e`'s own `CLARIFY`
verdict for a subquery never prevented `node_03a`'s *later* planning round
from independently (and, live-confirmed, wrongly) matching that same
subquery to an unrelated tool with no real grounding, because a
CLARIFY'd subquery has no covering tool_attempt and so reads as
perpetually `OPEN`. `node_03a` now excludes any `subquery_id` already
present in `clarification_tasks` from its own `open_subquery_ids`
computation (regardless of which node raised the entry), and `node_04a`
makes the same exclusion in its own `needs_tools` decision, so a turn
whose only open subquery just got clarified terminates immediately instead
of burning a wasted extra planning round-trip.

## 4e. ClarificationGuess: a principle-based definition, not a grounding checklist

The original `ClarificationGuess.reason` definition required "concrete
grounding" (a Product/Intent Matrix fact or a real conversation detail) but
said nothing about *recency* -- every other field in the contract
(`unresolved_request`, `clarification_reason`) is explicitly anchored to
the *latest* user input first, but this one field was defined as an
unqualified search across the entire conversation history, with an example
("the customer named a credit card two turns ago") that actively encouraged
reaching arbitrarily far back. Live-confirmed failure mode: across three
turns where a customer was offered "Resubmit KYC" / "Change Address," then
explicitly said "neither," the model re-offered the exact same two
rejected options again, justified only by "the user was presented with
this as the first/second option" -- a real, true fact, just no longer one
that reflects what the customer currently wants.

The fix was **not** to bolt a currency/non-contradiction clause onto the
grounding requirement (the first draft of this fix did exactly that, and
was itself judged over-specified). The actual repair changes what a guess
*is*, not what proof accompanies it: a `ClarificationGuess` is defined as
"a possible customer intention, based on the current context, that could
resolve this ambiguity if correct" -- a prediction of *current* intent, not
a citation of a historical fact. Once the primary definition already means
"given everything currently known," the currency requirement isn't a
separate rule that needs stating; a prediction that ignores the customer's
own most recent statement was never a prediction of their current intent
in the first place. `node_06`'s audit rule for guesses was simplified the
same way: one test ("does this fit the current context"), not a grounding
check plus a separate corrections/retractions clause.

## 4f. Two prompt pitfalls specific to this design, worth remembering

Both found live, both fixed by clarifying which field is customer-facing
prose and which is structural metadata or customer-voiced template text --
neither is specific to clarification, but both surfaced through it:

- **An id can leak into customer-facing text even when it has its own
  JSON field.** `_generate_evidence_only_response`'s rule said "address
  every statement, citing its task_id" -- ambiguous enough that the model
  wrote the literal string `[subquery_1]` into the customer-facing `text`
  field itself, reading "citing" as "mention in the text" rather than
  "populate the separate `task_id` field." Fixed in both generation
  prompts by stating explicitly that an id belongs only in its own
  structural field and must never appear as text, a bracketed token, or
  the literal value inside customer-facing prose.
- **A guess's `message` is written in the customer's voice; the
  generator's own text is the bot's voice.** When told to "mention what
  the options actually are" using a guess's `label`/`message`, the model
  sometimes copied the guess's sentence structure nearly verbatim into its
  own outbound question -- e.g. a guess message "Can you list the other
  banking services **you** can help **me** with?" became the bot's own
  question with the same pronouns left unreversed, reading as though the
  bot were asking the customer to help *it*. Fixed by stating explicitly
  that a guess's `label`/`message` supplies the *topic* to mention, never
  the *sentence structure* to reuse -- pronouns must always be re-derived
  from the bot's own voice, with a concrete before/after example in both
  generation prompts.

## 4g. The completeness net for uncited entries: detection only, insertion bypassed

The response generator composes freely — it may blend a clarification, a
decline, or a subquery's real evidence into one coherent element rather
than one block per source, which is intentional (§5): coherence is the
generator's job, not the underlying schema's shape. A deterministic
completeness net used to sit after generation, inserting a fallback block
verbatim for any clarification/decline/no-info entry whose `task_id`
didn't appear in any generated element's `source_task_ids` — the same
"don't trust two unreliable LLM calls to both remember" reasoning already
used elsewhere in this module (§3).

Live tracing surfaced a real false-positive cost: an element that
correctly blended two sources into one coherent answer, while only citing
one of their ids, looked to the net exactly like a dropped entry — the net
then inserted a second, redundant clarification block, producing a visibly
confusing double-clarification in the same response. The underlying
citation gap (a blended element should list every contributing id, not
just one) was real and separately fixed via a prompt instruction, but the
net's insertion behavior was a bigger, more visible failure mode than the
omission it was guarding against.

Decision: bypass insertion, keep detection. An entry the generator
genuinely dropped now only gets logged (`response.flexible.entry_omitted`,
`fallback_inserted: False`) — nothing is appended to the response.
Reasoning: most turns carry a single subquery, so there is rarely more
than one entry to lose track of in the first place; a genuine omission is
low-severity (the customer can just ask again), while the net's own
false-positive was actively degrading otherwise-good, coherent responses.
Detection stays on specifically so real frequency data — how often this
actually fires in practice — can inform whether a more targeted fix (e.g.
enforcing complete multi-id citation on blended elements, rather than
reintroducing a blunt fallback) is worth building later, without that call
being made blind.

**Extended to `_generate_evidence_only_response`**, the sibling generator
used when a turn has no real evidence at all (§4a) — it had inherited the
same one-block-per-entry structure this section describes for the main
path, never updated when the main path was fixed. Its schema changed from
a single `task_id` per element to a `source_task_ids` list (matching the
main path's field name), so it can now compose two related statements
(most commonly two clarifications about the same ambiguity) into one
coherent element instead of two separate ones. Its own completeness net
follows the identical detection-only decision above, with one addition
specific to this function: since *every* entry it handles is of the kind
the main path bypasses (there is no separate evidence-task net standing
behind it the way there is in the main path), insertion is still used, but
only as a genuine defensive fallback — when the model call fails or parses
to nothing at all, not for an individually-uncited entry in an otherwise
coherent response. Chip attachment was updated the same way: a block
citing more than one clarification's `subquery_id` now combines and
dedupes guesses from all of them, not just the first.

## 4h. Audit-feedback scoping: fixing what's flagged must not narrow the whole job

A failed outbound audit produces `rewrite_instruction` (§6) alongside a
recovery strategy — free-form guidance on what to change. Live tracing
found this feedback silently re-scoping the *entire* next generation
attempt: a redraft asked only to remove one unsupported claim would also
drop an already-correctly-authored clarification for a *different*
subquery in the same turn, as if fixing the flagged problem had become the
model's whole job rather than one instruction among many still-standing
ones. The generator's first attempt had no trouble handling clarification
+ evidence together; only the *redraft* lost that scope, which pointed at
the feedback-handling instruction biasing attention toward the fix alone.

Fixed with three coordinated, narrowly-targeted changes rather than a
single broad one:
1. `_generate_response_blocks`'s "how to use feedback" instruction now
   states explicitly that removing the flagged content is not permission
   to narrow the job — every standing duty (citing every Clarification/
   Decline/No-info entry, composing evidence the same way, etc.) still
   applies in full, exactly as on a first attempt.
2. `node_06`'s own prompt now constrains what `rewrite_instruction` itself
   is allowed to say: name only the specific flagged claim and how to fix
   it, never restate or imply the response's overall scope or
   completeness — that reassertion is not this field's job.
3. `NARROW_TO_EVIDENCE_SCOPE`'s existing "don't ask a further question"
   rule gained an explicit carve-out: it constrains only the *answer's*
   own content, never whether an already-authored Clarification Input for
   a different entry still gets relayed, which remains required exactly
   as on any other turn.

Verified live: a turn whose blended element cited both a clarification's
and a decline's ids survived a real audit-triggered redraft with both
still present and correctly cited.

**A second bug surfaced during the same verification, unrelated to the
above but in the same code path**: once citation correctly covered both
ids on one blended element, the auto-attached official
`clarification_guesses` chips (§4b) stacked on top of options the model
had already written into that same element itself, producing duplicate
chips. Fixed by deduplicating by `(label, message)` before combining the
model's own options with the auto-attached guesses, in the chip-attachment
loop in `_generate_response_blocks`.

## 5. Unified response generation: one generator role, two sub-prompts

`node_04b_response_generator` always calls `_generate_response_blocks`.
That function checks whether any real evidence exists this turn
(`evidence_by_task`, `available_shortcuts`, `unavailable_service_requests`,
or `verified_api_context`):

- **Real evidence exists** (the common case, including a mixed turn with
  some real evidence and some CLARIFY/DECLINE/NO_INFO entries alongside
  it): the full `FLEXIBLE RESPONSE GENERATION` prompt runs, with
  clarification/decline/no-info entries described as evidence to relay
  faithfully (never raw material to reason from, unlike real retrieval
  evidence — see the prompt's own "Approved evidence... vs. these three"
  framing) alongside everything else. Style/personalization applies to
  every element uniformly.
- **Zero real evidence, only CLARIFY/DECLINE/NO_INFO entries** (a turn that
  resolves to nothing but a question, a boundary, or an admission of no
  information): `_generate_evidence_only_response` runs instead — a
  narrower `PRE-DECIDED STATEMENT COMPOSITION` sub-prompt whose only job is
  composing these already-decided statements into one coherent message.
  Its output schema has no alternative field to substitute a different
  answer into, which is what removes the reliability risk a fully
  open-ended prompt had here: earlier, node_04b's single open-ended prompt
  reliably gave a bland "I don't have information" answer instead of
  reliably asking a pending clarification, since that instruction had to
  compete with everything else in the prompt. With no alternative field
  available, there's no easier answer left to dodge into.

Both paths share **one** outbound audit (`node_06`'s existing compliance +
groundedness gate) — there is no separate audit mechanism for this content
anymore (see §7). `_normal_outbound_grounding_context` builds the same
"Intent Clarification Inputs" / "Decline Inputs" / "No-Info Inputs"
evidence lists the generator sees, so the audit checks exactly what the
generator was given.

Both generator paths are instructed to *rephrase* each entry's evidence
text naturally, in the customer's own style, rather than copy it
verbatim — verbatim is a last resort only, never the default. Both also
run a **deterministic completeness net**: after generation, any entry
whose `task_id` never got cited in the model's output gets its own
evidence text appended verbatim as a fallback block. This exists because
the model has been observed dropping an entry even when its prompt
explicitly forbids skipping any of them, and the audit meant to catch that
omission has also been observed rationalizing it away ("acceptable since
not yet resolved by the customer") instead of failing the draft —
completeness no longer depends on either model behaving.

## 6. Cross-tool contradiction: a subquery resolved by one tool must never also claim "no information"

A subquery is commonly planned with more than one tool in the same round —
e.g. `service_request` (register a real, working form) *and* `rag` (tagged
`"complementary"`, in case the FAQ corpus also has something useful to add).
Two situations can make RAG's own side of that turn assert "no information
available" for the very thing the other tool just resolved:

1. **RAG's formulation is scope-rejected.** `node_02e` proposes a fresh
   `SEARCH` task whose product/intent pairing `_normalize_search_task_scope`
   rejects. This never becomes a live `search_task`, so without a
   compensating entry it would vanish silently, reaching generation with
   zero evidence of any kind for that subquery.
2. **RAG's retries genuinely exhaust with nothing found**
   (`node_03c_sufficiency_auditor`, `resolution_status ==
   "CLARIFICATION_REQUIRED"`).

Both are rendered as a **NO_INFO entry, never a DECLINE** — a matrix
rejecting a RAG formulation, or RAG's own retries coming up empty, only
proves RAG couldn't answer it from FAQ text; neither proves the underlying
service doesn't exist. (An earlier version of fix #1 used `decline_tasks`
here, which both asserted the stronger, false claim that Horizon doesn't
offer the service, and routed the whole turn to `node_10`'s hard-rejection
path whenever it was the only thing produced that round — the wrong node
for "temporarily couldn't answer this from FAQ text," not "we don't do
this.") Fix #1's entries accumulate in a new state field,
`unresolvable_search_tasks` (`state.py`), following the same manual,
non-reduced accumulation pattern as `decline_tasks` so they survive a mixed
turn where `node_03c` also writes `task_resolution_instructions` for a
sibling task the same round. Both fix #1 and fix #2's entries feed into the
shared `_split_terminal_markers` helper, so the generator and the audit
always compute identical `no_info_evidence` wording for the same task.

But an unconditional NO_INFO entry is still wrong if the *same subquery*
already has a different, successful tool attempt this round: it would tell
the customer no information exists for something a working shortcut was
just registered for. Confirmed live — "How to update KYC information"
produced both a completed "Resubmit KYC Document" service request *and* a
no-info entry for the identical wording, and the generator surfaced the
false "no information" claim instead of the real shortcut. Both fix
sites now check `_resolved_by_other_tool(owning_subquery)` before creating
their entry, which looks only at that subquery's **non-`rag`** attempts for
full coverage (`_fully_covers`, the same binary-coverage helper the
`resolution_status` rollup uses). This is deliberately narrower than the
general `_compute_subquery_status` rollup: both check sites run from
*inside* RAG's own execution for that exact subquery (`node_02e`
formulating it, or `node_03c` auditing it, before `_finalize_rag_attempts`
marks it terminal later in the same node) — at that moment RAG's own
attempt is still `"pending"`, which would make the general rollup report
`PENDING` and never reach `RESOLVED`, regardless of what else already
covers it. This was confirmed live as the reason the first version of
this exact fix silently did nothing: it checked
`_compute_subquery_status(...) == "RESOLVED"`, which could never be true
at that point in the round.

**Two more sites needed the identical guard, found via live tracing after
this fix shipped.** The pattern generalizes to *any* place that manufactures
a failure-shaped entry for a subquery without first checking whether some
other tool already resolved it this round -- not just RAG's own no-info
paths:

- **The formulator's own `DECLINE` resolution.** A component the intent
  formulator judges out-of-scope can be the same subquery a different tool
  (`deeplink`, `service_request`, `api`) already fully resolved this same
  round -- the formulator's `SEARCH`/`CLARIFY`/`DECLINE` partition never
  sees other tools' results. `node_02e`'s `DECLINE` branch now checks
  `_resolved_by_other_tool(owning_subquery)` before appending to
  `decline_tasks`, skipping the decline (and setting the same
  `any_subquery_resolved_by_other_tool` flag used below) when it fires.
- **The generic "no valid retrieval task could be formed" fallback.** When
  `node_02e` produces zero `search_tasks` and zero real
  `clarification_tasks`/`unresolvable_search_tasks`, it manufactures a
  catch-all clarification entry asking the customer to clarify their whole
  request -- correct when nothing at all resolved the
  turn, but wrong when a scope-rejected RAG formulation's subquery was
  already fully answered by a deeplink: confirmed live, "how can I manage
  my profile info" produced a working `profile-personal-details` deeplink
  *and* a fabricated "please clarify" task for the same subquery, and the
  response generator surfaced both -- a customer-visible self-contradiction
  (an answer plus a request to clarify the thing just answered) that then
  failed groundedness three times in a row before falling through to the
  exhausted-retry fallback, for a turn that was never actually stuck.
  `node_02e` now tracks `any_subquery_resolved_by_other_tool` across the
  scope-rejection loop and skips manufacturing the fallback clarification
  whenever it's set, falling through to the same `"continue_plan"` status
  already used for "nothing new to search, but not stuck" turns instead.

## 7. What this replaced

- **`_audit_clarification_decline_blocks`** (a bespoke combined
  compliance+groundedness call over every LLM-phrased CLARIFY/DECLINE
  block, tagged by `blockId`) — deleted. `node_06`'s existing audit now
  covers this content via `_normal_outbound_grounding_context`'s enriched
  evidence lists; no separate audit call is needed once the content isn't
  independently LLM-generated outside the normal generation step.
- **`customer_facing_question` / `customer_facing_message`** LLM output
  fields on `IntentClarificationTask`/`DeclineTask` — removed from the
  schema entirely, replaced by the deterministic `*_evidence` fields (§3).
  Renamed away from the `customer_facing_*` naming specifically because
  that name risked the response generator and the groundedness auditor not
  treating them as evidence to check against.
- **`route_n19`'s special-cased `n25` branch** (routing a pending
  intent-clarification with no `search_tasks` straight to
  `node_11_deterministic_clarification_fallback`) — reverted.
  `_generate_evidence_only_response` (§5) now handles that exact shape of
  turn within `n8` itself, sharing the normal audit; `node_11` is reachable
  only via `route_n20`/`route_n21`'s own three-failed-retries condition
  now (see §10 — that condition's *destination* changed again since this
  was first written), and its own rendering was simplified to relay
  `clarification_evidence`/`decline_evidence`/`no_info_evidence` directly
  with no generation or audit call of its own (see `TOOL_CALLING_v3.0.md`
  for the retry-budget mechanics that lead here).

## 10. Hard escalation reserved for explicit requests only

`route_n20`/`route_n21` originally sent an exhausted-retry turn
(`safety_retry_count >= 3`) to `node_09_hard_escalation_broker` ("n23")
unless a separately-computed `clarification_fallback_required` flag was
set, in which case it went to `node_11` ("n25") instead. This meant a
turn's own repeated inability to pass its own audit — nothing to do with
the customer asking for a human — could still end a conversation in a
hard-escalation handoff screen. Confirmed live as an unnecessary outcome:
a turn whose RAG ladder had already produced a real, deterministic
`CLARIFICATION_REQUIRED` no-info entry still routed to `n23`, because
`clarification_fallback_required` (`preserve_answer` in
`node_05_outbound_deterministic_gate`) also requires the *answer* portion
to have separately passed compliance/groundedness — under the audit's
fail-closed default (see `GROUNDEDNESS_v2.0.md`), a transient error (e.g.
an upstream rate limit) makes that condition false too, even though `n25`
already had a perfectly good deterministic no-info entry ready to render.

Both routers now send *every* exhausted-retry turn to `n25`, unconditionally:

```
route_n20 / route_n21: return "n8" if count < 3 else "n25"
```

`n23` is reachable only via a customer's own explicit request now — triage's
`ESCALATION` status (`route_n2d`) or an accepted transfer confirmation
(`route_n3`) — never via the generation/audit retry loop. `node_11`
absorbs the difference: it already rendered real
`clarification_evidence`/`decline_evidence`/`no_info_evidence` directly
when any existed; for the one case that has none at all — a real
generated answer that simply never passed compliance/groundedness, with
no clarification, decline, or no-info entry to fall back on — it now
apologizes plainly instead ("I'm sorry, I'm not able to answer that right
now. Could you try again, or ask in a different way?") rather than
escalating a case that may just be a phrasing or evidence gap, not
something only a human agent can resolve.

**Every turn routed to `n25` is scored 0.0 for the rolling confidence
signal**, regardless of which of the two deterministic contents it ends
up showing. This closes a related gap found via the same live tracing:
`n23`/`n25` both reach `END` directly, skipping
`node_08_soft_escalation_analyzer` — the only node that normally appends
to `turn_escalation_history` — so a turn this badly failed previously
contributed *no* data point to the rolling-confidence window instead of
the 0.0 it deserved, quietly inflating the average by shrinking the
sample rather than dragging it down. `_append_failed_turn_escalation_history`
(`nodes.py`) force-scores every financial subquery 0.0 (never read from
`_score_subquery`, since internal resolution state is exactly what the
customer did not end up receiving) and appends the entry itself. See
`SOFT_ESCALATION_v1.0.md` §2 for the scoring model this feeds.

## 11. Next-action suggestions: grounded pointers to unfeatured evidence, not guesses

`_generate_response_blocks` also returns 0-3 `next_actions` — short,
customer-voiced next-question suggestions rendered above the input box on
the frontend (stacked vertically, closest-to-input = most relevant),
generated by the same call as the response itself, no extra LLM round
trip.

The design constraint that matters most here came directly from a real
failure mode: an earlier, unconstrained attempt at this feature let the
model suggest topics it merely guessed the customer *might* want, with no
guarantee the KB actually covered them — tapping one could dead-end into
"I don't have that," which is worse than offering nothing. The fix is a
narrow evidence requirement, not a prompt-tone fix: every suggestion must
trace back to a specific fact or sub-topic already present in *this
turn's own approved evidence* that the composed answer simply didn't
feature (e.g. evidence covers both application steps and eligibility, the
response only used the steps — suggest asking about eligibility).
Retrieval for a next action is not literally deterministic, but close to
it in practice: this codebase's verified-session-parent RRF matching
(`Search Strategy v2.8`) preferentially re-surfaces a parent already
approved earlier in the same session, so a follow-up tap very reliably
finds the same evidence again.

This is a categorically different kind of "guess" from `ClarificationGuess`
(§4e): a clarification guess predicts customer *intent* to resolve a
present ambiguity; a next-action suggestion predicts customer *interest*
in content that is already known, with certainty, to exist. `node_06`'s
audit holds the two to different standards accordingly — a next-action is
rejected only if it names a topic with no basis anywhere in the approved
evidence, not against the same factual-assertion bar the answer text
itself is held to.

Optional by design: an empty list is the expected common case whenever
nothing materially relevant was left unfeatured, not a fallback failure.
Capped at 3, deduplicated, and — like every other customer-facing field in
this design — never carries a raw id/subquery_id/bracketed token (§4f
applies here too).

Frontend: replaces an earlier static/mock suggestion list entirely (fixed
per-language defaults, or reusing the last message's own chip options) —
deleted outright rather than kept as a fallback, since a mock suggestion
carries exactly the dead-end risk this feature exists to avoid.

## 8. Files touched

`backend/app/core/llm_service.py` (`ClarificationGuess`, renamed from
`ClarificationReply` -- required `reason` field, moved earlier in the file
so `TaskSufficiencyItem` can reference it --
`IntentClarificationTask.clarification_guesses`/`clarification_reason`
(`clarification_id` and `unresolved_request`-only `reason` retired --
`subquery_id` is injected by `node_02e` from `component_id` instead),
`DeclineTask.category`, `TaskSufficiencyItem.likely_ambiguous_request`/
`unresolved_term`/`clarification_reason`/`clarification_guesses`),
`backend/app/orchestration/state.py` (`clarification_tasks`, replacing the
former `intent_clarification_tasks` + `evidence_clarification_tasks`
pair -- see §4b),
`backend/app/orchestration/nodes.py` (`_DECLINE_CATEGORY_EVIDENCE`,
`_decline_evidence_text`/`_clarification_evidence_text`/
`_no_info_evidence_text`/`_evidence_gap_clarification_text` (new, §3),
`_normalize_clarification_guesses` (new, shared by
`node_02e` and `node_03c`), `_CLARIFICATION_SCOPE_BY_ORIGINATOR` (new,
§4b), `_build_clarification_context` (now a direct passthrough of the
single `clarification_tasks` list, no merge logic left), `_generate_response_blocks`
(clarification field reverted to a plain neutral-notice string, no
judgment call), `_generate_evidence_only_response`, `_resolved_by_other_
tool`, `node_02e`'s scope-rejection, `DECLINE` handling, and Rule 2's
INTENT-ambiguity guidance, `node_03c`'s `_rag_gap_still_matters`, its
sufficiency-audit prompt/schema, and its new evidence-clarification
authoring + finalization logic (§4a/§4b, deduplicated by `subquery_id`),
`node_06`'s audit rules (checks `scope`-consistency on
`clarification_reason`, not just its content), `node_09`'s escalation
summary and simplified scope (explicit-escalation only), `node_11`'s
generalized rendering plus its plain-apology fallback,
`_append_failed_turn_escalation_history`),
`backend/app/orchestration/state.py` (`unresolvable_search_tasks`),
`backend/app/orchestration/graph.py` (`route_n19` reverted; `route_n20`/
`route_n21` no longer route to `n23` on retry exhaustion — see §10).

**Second round (§4c-4f):** `backend/app/core/llm_service.py`
(`ClarificationGuess` redefined around current-intent prediction rather
than a grounding checklist, `TriageEvaluation.clarification` (new field,
`IntentClarificationTask` reused), `IntentClarificationTask` moved earlier
in the file so `TriageEvaluation` can reference it),
`backend/app/orchestration/state.py` (`clarification_tasks` entries no
longer carry `clarification_evidence`; comment updated for
`unresolved_request`/originator set of four),
`backend/app/orchestration/nodes.py` (`_clarification_fallback_text` (new,
on-demand-only template selection), `_clarification_prompt_view` (new, the
shared generator/audit projection), `_CLARIFICATION_SCOPE_BY_ORIGINATOR`
extended with `"triage"`/`"task_dispatcher"`, `node_02d`'s new Rule 5
(`CLARIFICATION_NEEDED`) and `clarification_tasks` construction,
`node_03a`'s `_tool_calling_plan_schema` (new `clarifications` field),
its own tool-selection-clarification prompt rule and parsing, and its
`already_clarified_subquery_ids` exclusion from `open_subquery_ids`,
`node_04a`'s matching exclusion from its own `needs_tools` decision,
`_generate_response_blocks`/`_generate_evidence_only_response` (both
prompts: clarification composed from `unresolved_request`/`scope`/
`clarification_guesses` rather than relaying `clarification_evidence`;
explicit id-never-in-text-field instruction; explicit guess-is-topic-not-
sentence-structure instruction), `node_06`'s Clarification Rules (dropped
the `clarification_reason` check entirely -- no longer shown to the audit
-- and simplified the guess check to a single current-context test),
`node_09`'s escalation summary (reads `unresolved_request` directly)),
`backend/app/orchestration/graph.py` (`route_n2d` gains a
`CLARIFICATION_NEEDED -> n19` branch, reusing `node_02e`'s own proven
clarification-only path).

**Third round (§4g/§4h/§11):** `backend/app/orchestration/nodes.py`
(`_generate_response_blocks`'s completeness net for clarification/decline/
no-info entries changed from inserting a fallback block to detection-only
logging (`fallback_inserted: False`); its "failed outbound-audit feedback"
instruction rewritten to preserve full standing duties on a redraft;
`next_actions` field added to its output schema, prompt rule, parsing
(cap 3, dedupe), and `ui_action_payload`; its chip-attachment loop
deduplicated by `(label, message)`; `node_06`'s audit-feedback rules gained
a `rewrite_instruction`-scoping constraint and a next-actions evidence
check; `NARROW_TO_EVIDENCE_SCOPE`'s definition gained the Clarification-
Input carve-out), `backend/app/api_gateway.py` (`nextActions` on the
response payload), `frontend/src/App.jsx` (deleted the static/mock
`suggestedNextActions` fallback; new `nextActions`/`isInputFocused` state;
next-action suggestions redesigned from a horizontal chip row to a
vertical stacked list stemming from the input box, shown on hover or
focus).

## 9. Verification

Live-traced: "business credit card eligibility" and "annual fee for my
business credit card" (`DECLINE`, `individual_customers_only`, honest
wording); "margin trading account" (`DECLINE`, `out_of_scope_product`);
"personal credit card" (unaffected, normal `SEARCH`); a pure ambiguous
"tell me about the fee" turn resolving to `_generate_evidence_only_response`
with candidate-reply chips attached; a mixed "fee + loan application +
routing number" turn producing real evidence for two subqueries and a
`no_info` entry for the third within one `_generate_response_blocks` call,
sharing one audit; "can I change my address?" producing a `service_request`
+ `deeplink` pair correctly distinguished by kind in the response text; "can
I change my KYC material and also apply for a credit card" confirmed, after
the `_resolved_by_other_tool` fix, no longer producing a false "no
information available" claim about KYC alongside the real, already-
registered KYC resubmission service request.
