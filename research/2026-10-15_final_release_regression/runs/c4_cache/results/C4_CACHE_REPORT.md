# C4 cache-parent recall v1.1 result

- Run valid: **True**
- Scorable questions: **10/20**
- Configured similarity threshold: **0.65**
- Parent-ID recall: **44/44 (100.0%)**
- Complete parent-set recall: **20/20 (100.0%)**
- Target: **100% parent-ID recall**
- Decision: **PASSED**
- Unscorable questions: h1_Refund_not_showing_up_2, h1_age_limit_2, h1_automatic_top_up_2, h1_exchange_rate_2, h1_get_physical_card_1, h1_order_physical_card_2, h1_pending_cash_withdrawal_1, h1_pin_blocked_2, h1_supported_cards_and_currencies_2, h1_unable_to_verify_identity_1
- Structural errors: none

This final release run measured the implemented 0.65 threshold live. It scores
only whether run-1 approved parent IDs surfaced before the downstream
sufficiency audit; downstream selection and answer wording do not affect C4.

See `c4_cache_summary.json` for per-question details.
