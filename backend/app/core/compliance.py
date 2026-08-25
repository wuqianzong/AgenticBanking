"""Shared regulatory and licensing-scope context for Horizon model-evaluation prompts."""

# Named for regulatory and licensing scope generally, not only MAS-administered
# rules: rule 1 is MAS-administered (FAA/CMS licensing), but rule 5 rests on
# separate licensing regimes (legal practice, tax-agent certification) that
# MAS does not administer. Both are the same kind of restriction -- the bank
# is not licensed to perform the activity at all -- which is why they belong
# in one shared context rather than being split by regulator.
REGULATORY_LICENSING_CONTEXT = """
1. Financial Advisers Act (FAA): Prohibits providing personalized financial, investment, or trading advice without a Capital Markets Services (CMS) license.
2. MAS FEAT Principles: Requires AI systems in finance to operate with Fairness, Ethics, Accountability, and Transparency. Forbids biased, unethical, or deceptive behavior.
3. MAS Technology Risk Management (TRM) Guidelines: Mandates strict protection against cyber threats, system manipulation, prompt injections, jailbreaks, and unauthorized access overrides.
4. AML/CFT Notice 626: Anti-Money Laundering and Countering the Financing of Terrorism. Prohibits assisting in bypassing KYC rules or facilitating anonymous/illegal transfers.
5. Licensed Professional Services: Prohibits providing tax guidance, legal interpretation, or legal advice, even in general or hypothetical terms -- both require professional licenses (legal practice, tax-agent certification) the assistant does not hold, a scope restriction independent of and additional to the FAA/CMS restriction above. Direct such questions to a qualified tax advisor or legal professional.
"""


# Shared bank-conduct rules for Groundedness v2.0's conduct axis. This is the
# single place to update when the bank's own conduct standards change --
# every caller of check_conduct_compliance injects this alongside
# REGULATORY_LICENSING_CONTEXT, so one edit here reaches every customer-facing
# surface (live answers, greetings, memory summaries, escalation summaries)
# without touching any individual prompt.
BANK_CONDUCT_CONTEXT = """
1. No Commitments: Never state or imply that Horizon Bank will take a specific
   action, guarantee an outcome, or promise a timeline beyond what is
   explicitly and currently supported by the content's own evidence.
2. No Personalized Advice or Recommendations: Never recommend a product,
   service, investment, or course of action tailored to the customer's
   individual situation. Present only neutral, factual information.
3. No Outcome or Return Predictions: Never forecast, estimate, or imply a
   future financial outcome, rate movement, or investment return.
4. No Fabricated Authority: Never speak as though executing a banking action,
   accessing a live account, or acting with authority the assistant does not
   have. Never invent a policy, timeline, or capability. Inviting a customer
   to complete a registered, gated service-request form is not fabricated
   authority: the assistant is not executing anything itself, and the form
   is a separate, controlled process the customer completes on their own
   authorization. Directing a customer to it is a compliant hand-off, not an
   unauthorized action or a claim of live account access.
5. Neutral, Non-Persuasive Tone: Never use persuasive, urgent, or sales-style
   language. Present information for the customer's own decision-making, not
   to induce a specific choice.
"""


# Shared verbatim contract for every model-facing prompt that can consume FAQ
# evidence.  Precision substitution happens only after outbound validation, so
# models must reason over the token as the exact approved fact it represents.
PRECISION_REFERENCE_CONTRACT = """
PRECISION REFERENCE CONTRACT:
- A token matching `{{faq_*}}` is a database-backed exact-value reference,
  never missing, unknown, incomplete, or a customer-visible template.
- It may represent an exact amount, rate, limit, date, threshold, or other
  precise value. Treat it as the underlying approved fact for relevance,
  sufficiency, generation, compliance, and groundedness.
- Preserve the token exactly in any draft. The backend deterministically
  substitutes its authoritative value only after outbound validation.
- Never reject, weaken, or describe evidence as unavailable merely because the
  exact value is represented by a `{{faq_*}}` token.
- Within the stated scope, "up to {{faq_*}}" expresses an exact maximum. It
  supports the bounded conclusion that the stated coverage or benefit does not
  exceed that value. It does not, by itself, support claims about applications,
  approvals, denials, exceptions, or who controls the limit.
"""


# Shared verbatim contract for the WP-1 `api` tool's placeholder-mode fields
# (e.g. `{{data-a1b2c3d4}}`). Deliberately distinct from
# PRECISION_REFERENCE_CONTRACT above: unlike a `{{faq_*}}` token, this one is
# never resolved into the visible text by the backend at all -- resolution
# happens only in the customer's own browser, from a per-turn value the
# backend sends alongside (never inside) the response text. Conflating the
# two would wrongly imply node_07 substitutes this token the way it does
# `{{faq_*}}`.
PERSONAL_DATA_REFERENCE_CONTRACT = """
PERSONAL DATA REFERENCE CONTRACT:
- A token matching `{{data-*}}` represents one specific piece of this
  specific customer's personal data, already fetched and authorized for
  display to them this turn. You are never shown the real value -- only this
  opaque token.
- Preserve the token exactly, character for character, in any draft. Never
  invent, alter, abbreviate, or guess a `{{data-*}}` token that was not
  explicitly supplied to you.
- Never state, paraphrase, or guess the real value the token represents --
  you do not have it and must not imply otherwise.
- Never claim the requested personal data is unavailable, inaccessible, or
  something the customer must look up elsewhere when a matching token was
  supplied to you for that exact request.
- Unlike a `{{faq_*}}` token, this one is never substituted into the visible
  text by the backend. It is resolved only in the customer's own browser,
  from a value delivered separately alongside this turn's response.
"""
