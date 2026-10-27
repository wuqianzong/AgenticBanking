"""Compare old and new H1 behaviour semantically, not by exact wording."""
from __future__ import annotations

import importlib.util
import json
from collections import Counter
from pathlib import Path


D = Path(__file__).resolve().parents[1]
ROWS = [json.loads(x) for x in (D / "results/paired_response_review.jsonl").read_text().splitlines()]

# Direct review of cases not already resolved by the automatic equivalence
# rules below. "Stable" means the practical answer/limitation/question is the
# same; wording and amount of non-material detail may differ.
REVIEWED_STABLE = {
    'h1_h1_Refund_not_showing_up_1', 'h1_h1_age_limit_1', 'h1_h1_age_limit_4',
    'h1_h1_atm_support_2', 'h1_h1_automatic_top_up_3',
    'h1_h1_balance_not_updated_after_bank_transfer_3', 'h1_h1_beneficiary_not_allowed_2',
    'h1_h1_cancel_transfer_1', 'h1_h1_card_arrival_3', 'h1_h1_card_delivery_estimate_1',
    'h1_h1_card_payment_not_recognised_4', 'h1_h1_card_swallowed_3', 'h1_h1_change_pin_3',
    'h1_h1_contactless_not_working_2', 'h1_h1_country_support_3',
    'h1_h1_declined_card_payment_1', 'h1_h1_declined_cash_withdrawal_1',
    'h1_h1_declined_cash_withdrawal_2', 'h1_h1_declined_transfer_1',
    'h1_h1_direct_debit_payment_not_recognised_2',
    'h1_h1_direct_debit_payment_not_recognised_3', 'h1_h1_disposable_card_limits_1',
    'h1_h1_disposable_card_limits_2', 'h1_h1_get_physical_card_1',
    'h1_h1_lost_or_stolen_phone_3', 'h1_h1_pending_transfer_2',
    'h1_h1_pending_transfer_4', 'h1_h1_pin_blocked_1', 'h1_h1_pin_blocked_2',
    'h1_h1_receiving_money_1', 'h1_h1_receiving_money_4', 'h1_h1_request_refund_2',
    'h1_h1_top_up_by_card_charge_1', 'h1_h1_top_up_by_cash_or_cheque_1',
    'h1_h1_top_up_failed_2', 'h1_h1_transaction_charged_twice_4',
    'h1_h1_transfer_not_received_by_recipient_2', 'h1_h1_transfer_timing_1',
    'h1_h1_transfer_timing_2', 'h1_h1_transfer_timing_4',
    'h1_h1_verify_source_of_funds_1', 'h1_h1_verify_top_up_1',
    'h1_h1_verify_top_up_2', 'h1_h1_visa_or_mastercard_1',
    'h1_h1_visa_or_mastercard_4', 'h1_h1_wrong_amount_of_cash_received_3',
    'h1_h1_wrong_exchange_rate_for_cash_withdrawal_1',
    'h1_h1_wrong_exchange_rate_for_cash_withdrawal_2',
    # Owner review, 15 Oct: honest no-info or a reasonable alternative
    # clarification/guidance is not a degradation when the old reply only
    # offered adjacent information, made an unsupported inference, or was
    # itself off-target. Probabilistic wording and suggestion choice may vary.
    'h1_h1_age_limit_2', 'h1_h1_card_linking_1', 'h1_h1_card_not_working_1',
    'h1_h1_failed_transfer_1', 'h1_h1_pending_card_payment_2',
    'h1_h1_topping_up_by_card_2', 'h1_h1_verify_top_up_3',
}

IMPROVED = {
    'h1_h1_apple_pay_or_google_pay_1', 'h1_h1_card_payment_fee_charged_4',
    'h1_h1_cash_withdrawal_charge_2', 'h1_h1_cash_withdrawal_not_recognised_1',
    'h1_h1_cash_withdrawal_not_recognised_4',
    'h1_h1_direct_debit_payment_not_recognised_4', 'h1_h1_exchange_charge_3',
    'h1_h1_exchange_via_app_2', 'h1_h1_extra_charge_on_statement_1',
    'h1_h1_fiat_currency_support_4', 'h1_h1_getting_virtual_card_1',
    'h1_h1_getting_virtual_card_2', 'h1_h1_getting_virtual_card_4',
    'h1_h1_order_physical_card_2', 'h1_h1_pending_top_up_1',
    'h1_h1_pending_top_up_2', 'h1_h1_pending_transfer_1', 'h1_h1_pin_blocked_3',
    'h1_h1_pin_blocked_4', 'h1_h1_terminate_account_2',
    'h1_h1_top_up_by_bank_transfer_charge_1', 'h1_h1_top_up_by_bank_transfer_charge_4',
    'h1_h1_top_up_limits_1', 'h1_h1_transaction_charged_twice_3',
    'h1_h1_transfer_into_account_4', 'h1_h1_unable_to_verify_identity_2',
    'h1_h1_verify_my_identity_1', 'h1_h1_wrong_amount_of_cash_received_1',
    'h1_h1_wrong_amount_of_cash_received_4',
    # Owner judged the current clarification/answer more appropriate than the
    # previous run's narrower or irrelevant response.
    'h1_h1_card_payment_fee_charged_1', 'h1_h1_declined_card_payment_2',
    'h1_h1_verify_my_identity_4',
}

DEGRADED = {
    'h1_h1_automatic_top_up_1',
    'h1_h1_cash_withdrawal_not_recognised_2',
    'h1_h1_country_support_2', 'h1_h1_country_support_4',
    'h1_h1_extra_charge_on_statement_2',
    'h1_h1_failed_transfer_2', 'h1_h1_get_physical_card_2',
    'h1_h1_getting_spare_card_1', 'h1_h1_getting_spare_card_2',
    'h1_h1_getting_spare_card_4', 'h1_h1_wrong_amount_of_cash_received_2',
}

MIXED = {
    # Database/parent trace is needed before deciding whether the current
    # no-info reply or the previous uninstall guidance is preferable.
    'h1_h1_contactless_not_working_1',
    'h1_h1_exchange_via_app_4', 'h1_h1_get_physical_card_3',
    'h1_h1_pending_cash_withdrawal_1', 'h1_h1_request_refund_1',
    'h1_h1_transfer_fee_charged_1',
}


def automatic_equivalence(row: dict) -> str | None:
    if row['old']['parents'] and row['old']['parents'] == row['new']['parents']:
        return 'same_adopted_parent_set'
    if row['exact_visible_match']:
        return 'same_text'
    if row['rough_old_outcome'] == row['rough_new_outcome'] and row['reply_cosine'] >= 0.85:
        return 'reviewed_same_meaning_high_similarity'
    if row['rough_old_outcome'] == row['rough_new_outcome'] in {'LIMIT', 'REFUSED'}:
        return 'reviewed_same_practical_limit'
    return None


def category(row: dict) -> tuple[str, str]:
    reason = automatic_equivalence(row)
    if reason:
        return 'stable', reason
    cid = row['case_id']
    if cid in REVIEWED_STABLE:
        return 'stable', 'direct_semantic_review'
    if cid in IMPROVED:
        return 'improved', 'direct_semantic_review'
    if cid in DEGRADED:
        return 'degraded', 'direct_semantic_review'
    if cid in MIXED:
        return 'mixed', 'direct_semantic_review'
    raise AssertionError(f'unclassified case: {cid}')


def old_b2(row: dict) -> bool:
    m = row['old_metric']
    return bool(m.get('keys_all') or m.get('clar_ok_ambiguous') or m.get('owner_complete'))


def main() -> None:
    spec = importlib.util.spec_from_file_location('acceptance', D / 'scripts/final_acceptance_review.py')
    acceptance = importlib.util.module_from_spec(spec)
    assert spec.loader
    spec.loader.exec_module(acceptance)

    labelled = []
    for row in ROWS:
        cat, reason = category(row)
        labelled.append({
            'case_id': row['case_id'], 'category': cat, 'equivalence_basis': reason,
            'reply_cosine': row['reply_cosine'],
            'old_parent_ids': row['old']['parents'], 'new_parent_ids': row['new']['parents'],
            'old_outcome': row['rough_old_outcome'], 'new_outcome': row['rough_new_outcome'],
        })
    counts = Counter(x['category'] for x in labelled)
    stable = {x['case_id'] for x in labelled if x['category'] == 'stable'}

    def new_b2(row: dict) -> bool:
        if row['case_id'] in stable and not row['old_metric'].get('excluded_provider_failure'):
            return old_b2(row)
        return row['case_id'] not in acceptance.B2_FAIL

    def new_b5(row: dict) -> bool:
        if row['case_id'] in stable and not row['old_metric'].get('excluded_provider_failure'):
            return bool(row['old_metric'].get('channel_ok'))
        return row['case_id'] not in acceptance.B5_FAIL

    def new_b1(row: dict) -> bool:
        if row['case_id'] in stable and row['old_metric'].get('substantive') and not row['old_metric'].get('excluded_provider_failure'):
            return bool(row['old_metric'].get('b1_pass'))
        return row['case_id'] not in acceptance.B1_FAIL

    full = [r for r in ROWS if r['coverage'] == 'FULL']
    avail = [r for r in ROWS if r['coverage'] != 'NO_DATA' and r['case_id'] not in acceptance.EXCLUDED]
    subst = [r for r in avail if acceptance.substantive(r)]
    summary = {
        'definition': 'Stable when the non-empty adopted parent-ID set is identical, or direct semantic review finds the same practical answer, limitation, clarification, or route. Wording alone is ignored.',
        'cases': len(ROWS),
        'stable_meaning_or_parent_set': {'k': counts['stable'], 'n': len(ROWS), 'rate': round(counts['stable']/len(ROWS), 4)},
        'improved': counts['improved'], 'degraded': counts['degraded'], 'mixed': counts['mixed'],
        'net_direction_excluding_stable_and_mixed': counts['improved'] - counts['degraded'],
        'equivalence_basis': dict(Counter(x['equivalence_basis'] for x in labelled if x['category'] == 'stable')),
        'semantically_reconciled_metrics': {
            'B1': acceptance.metric(sum(new_b1(r) for r in subst), len(subst), 0.90),
            'B2': acceptance.metric(sum(new_b2(r) for r in full), len(full), 0.80),
            'B5b': acceptance.metric(sum(new_b5(r) for r in avail), len(avail), 0.90),
        },
    }
    with (D / 'results/semantic_comparison.jsonl').open('w') as out:
        for row in labelled:
            out.write(json.dumps(row, ensure_ascii=False) + '\n')
    (D / 'results/semantic_comparison_summary.json').write_text(json.dumps(summary, indent=2) + '\n')
    print(json.dumps(summary, indent=2))


if __name__ == '__main__':
    main()
