"""Shared MAS compliance context for Horizon model-evaluation prompts."""

MAS_REGULATIONS_CONTEXT = """
1. Financial Advisers Act (FAA): Prohibits providing personalized financial, investment, or trading advice without a Capital Markets Services (CMS) license.
2. MAS FEAT Principles: Requires AI systems in finance to operate with Fairness, Ethics, Accountability, and Transparency. Forbids biased, unethical, or deceptive behavior.
3. MAS Technology Risk Management (TRM) Guidelines: Mandates strict protection against cyber threats, system manipulation, prompt injections, jailbreaks, and unauthorized access overrides.
4. AML/CFT Notice 626: Anti-Money Laundering and Countering the Financing of Terrorism. Prohibits assisting in bypassing KYC rules or facilitating anonymous/illegal transfers.
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
