"""claims_rules/tests.py — unit tests for the five claims automation rule sets.

Plain `unittest.TestCase`, no Django, no database, no fixtures. The engine is
pure, so its tests must be runnable with nothing but a Python interpreter:

    python3 -m unittest discover -s claims_rules -t .

Every branch and every boundary has a test here, and the message strings are
asserted in full — the exact wording IS the requirement (Lindani Mababa spec
2026-09-08), so a test that only checks a prefix would not catch the day
somebody softens "Do not proceed".

No customer PII in any fixture: names are 'Test Client A', policies 'POL-0001'.
"""
import unittest
from decimal import Decimal

from claims_rules.engine import (
    DEFAULT_MAX_ALLOWED_FAILURES,
    AolResult,
    Outcome,
    aol_breakdown_lines,
    calculate_aol,
    evaluate_aol,
    evaluate_premium_confirmation,
    evaluate_realpay_collection_cycle,
    evaluate_realpay_transaction,
    evaluate_recovery,
    evaluate_write_off,
    prorata_premium_adjustment,
)

CLIENT = 'Test Client A'
POLICY = 'POL-0001'
VEHICLE = 'B 123 ABC'


def kinds(outcomes):
    return [o.kind for o in outcomes]


def audiences(outcomes):
    return [o.audience for o in outcomes]


def messages(outcomes):
    return [o.message for o in outcomes]


# =============================================================== RULE SET 1 ==

class WriteOffTests(unittest.TestCase):

    def test_single_vehicle_gives_critical_notice_and_cancellation_task(self):
        out = evaluate_write_off(CLIENT, VEHICLE, active_item_count=1)
        self.assertEqual(kinds(out), ['notify', 'notify', 'task'])
        self.assertEqual(audiences(out), ['operations', 'claims', 'finance'])
        self.assertEqual(
            out[0].message,
            f'CRITICAL: Client {CLIENT} has only one vehicle on the system, which has '
            f'been marked as a write-off.',
        )
        self.assertEqual(out[0].message, out[1].message)
        self.assertEqual(out[0].severity, 'critical')
        self.assertEqual(out[1].severity, 'critical')
        self.assertEqual(
            out[2].message,
            f'POLICY CANCELLATION: Please completely cancel the policy and stop all '
            f'future debit orders on the Real Pay system immediately for Client {CLIENT}.',
        )
        self.assertEqual(out[2].severity, 'urgent')

    def test_single_vehicle_never_mentions_a_premium_adjustment(self):
        # Nothing is left to insure, so there is no amended premium to send to
        # Real Pay — only a full stop.
        out = evaluate_write_off(CLIENT, VEHICLE, active_item_count=1,
                                 amended_premium=Decimal('500.00'))
        self.assertNotIn('PREMIUM ADJUSTMENT', ' '.join(messages(out)))

    def test_multi_item_removes_vehicle_and_amends_premium(self):
        out = evaluate_write_off(CLIENT, VEHICLE, active_item_count=2,
                                 amended_premium=Decimal('1234'))
        self.assertEqual(kinds(out), ['task', 'notify'])
        self.assertEqual(audiences(out), ['underwriting', 'finance'])
        self.assertEqual(
            out[0].message,
            f"ACTION REQUIRED: Please remove Vehicle {VEHICLE} from Client {CLIENT}'s "
            f"active policy due to a write-off.",
        )
        self.assertEqual(
            out[1].message,
            'PREMIUM ADJUSTMENT: Vehicle removed. Please update Real Pay system with the '
            'newly amended premium of 1,234.00 for the next debit cycle.',
        )

    def test_multi_item_boundary_three_items_behaves_like_two(self):
        out = evaluate_write_off(CLIENT, VEHICLE, active_item_count=3,
                                 amended_premium=Decimal('99.5'))
        self.assertEqual(kinds(out), ['task', 'notify'])
        self.assertIn('99.50', out[1].message)

    def test_amended_premium_formats_with_thousands_comma_and_two_decimals(self):
        out = evaluate_write_off(CLIENT, VEHICLE, active_item_count=2,
                                 amended_premium=Decimal('1234567.5'))
        self.assertIn('1,234,567.50', out[1].message)

    def test_amended_premium_accepts_a_string_amount(self):
        out = evaluate_write_off(CLIENT, VEHICLE, active_item_count=2,
                                 amended_premium='2500')
        self.assertIn('2,500.00', out[1].message)

    def test_missing_amended_premium_warns_instead_of_printing_a_placeholder(self):
        out = evaluate_write_off(CLIENT, VEHICLE, active_item_count=2)
        self.assertEqual(kinds(out), ['task', 'notify'])
        self.assertEqual(audiences(out), ['underwriting', 'technical'])
        self.assertEqual(
            out[1].message,
            f'PREMIUM ADJUSTMENT PENDING: Vehicle {VEHICLE} removal for Client {CLIENT} '
            f'has no amended premium supplied, so the Real Pay system cannot be updated '
            f'yet.',
        )
        self.assertNotIn('PREMIUM ADJUSTMENT:', ' '.join(messages(out)))

    def test_zero_active_items_is_a_data_error_not_a_crash(self):
        out = evaluate_write_off(CLIENT, VEHICLE, active_item_count=0)
        self.assertEqual(len(out), 1)
        self.assertEqual((out[0].kind, out[0].audience, out[0].severity),
                         ('notify', 'technical', 'warning'))
        self.assertEqual(
            out[0].message,
            f'DATA ERROR: Client {CLIENT} has no active insured items on the system, so '
            f'the write-off of Vehicle {VEHICLE} cannot be actioned. Check the client '
            f'record before proceeding.',
        )

    def test_negative_active_items_is_also_a_data_error(self):
        out = evaluate_write_off(CLIENT, VEHICLE, active_item_count=-4)
        self.assertEqual(kinds(out), ['notify'])
        self.assertIn('DATA ERROR', out[0].message)

    def test_outcome_is_immutable(self):
        out = evaluate_write_off(CLIENT, VEHICLE, active_item_count=1)
        with self.assertRaises(Exception):
            out[0].message = 'tampered'


class ProrataTests(unittest.TestCase):

    def test_half_the_period_is_half_the_premium(self):
        self.assertEqual(
            prorata_premium_adjustment(Decimal('1000.00'), 15, 30),
            Decimal('500.00'),
        )

    def test_rounds_half_up_not_bankers(self):
        # 100.01 * 1/2 = 50.005 → 50.01 half up (bankers' rounding gives 50.00).
        self.assertEqual(
            prorata_premium_adjustment(Decimal('100.01'), 1, 2),
            Decimal('50.01'),
        )

    def test_rounds_half_up_on_an_odd_day_count(self):
        # 1000 * 10/365 = 27.397... → 27.40
        self.assertEqual(
            prorata_premium_adjustment(Decimal('1000.00'), 10, 365),
            Decimal('27.40'),
        )

    def test_zero_days_remaining_is_zero(self):
        self.assertEqual(prorata_premium_adjustment(Decimal('1000.00'), 0, 30),
                         Decimal('0.00'))

    def test_negative_days_remaining_is_zero(self):
        self.assertEqual(prorata_premium_adjustment(Decimal('1000.00'), -5, 30),
                         Decimal('0.00'))

    def test_full_period_remaining_is_the_full_premium(self):
        self.assertEqual(prorata_premium_adjustment(Decimal('1000.00'), 30, 30),
                         Decimal('1000.00'))

    def test_days_remaining_beyond_the_period_is_clamped(self):
        self.assertEqual(prorata_premium_adjustment(Decimal('1000.00'), 45, 30),
                         Decimal('1000.00'))

    def test_zero_length_period_raises(self):
        with self.assertRaises(ValueError):
            prorata_premium_adjustment(Decimal('1000.00'), 5, 0)

    def test_result_is_a_decimal_never_a_float(self):
        self.assertIsInstance(prorata_premium_adjustment(Decimal('1000.00'), 15, 30),
                              Decimal)


# =============================================================== RULE SET 2 ==

class PremiumConfirmationTests(unittest.TestCase):

    def test_branch_a_grants_authorization_and_changes_nothing_else(self):
        out = evaluate_premium_confirmation(POLICY, CLIENT, 'monthly', 1, 1, 0)
        self.assertEqual(kinds(out), ['release'])
        self.assertEqual(audiences(out), ['claims'])
        self.assertEqual(
            out[0].message,
            f'AUTHORIZATION GRANTED: Go ahead and proceed with the claim for Policy '
            f'{POLICY}. All invoices are up to date and premiums are paid.',
        )
        self.assertEqual(out[0].severity, 'info')

    def test_branch_a_at_the_boundary_generated_equals_expected(self):
        out = evaluate_premium_confirmation(POLICY, CLIENT, 'quarterly', 4, 4, 0)
        self.assertEqual(kinds(out), ['release'])

    def test_more_generated_than_expected_is_still_green(self):
        out = evaluate_premium_confirmation(POLICY, CLIENT, 'annual', 1, 2, 0)
        self.assertEqual(kinds(out), ['release'])

    def test_branch_b_missing_invoice_holds_and_calls_finance(self):
        out = evaluate_premium_confirmation(POLICY, CLIENT, 'monthly', 3, 2, 0)
        self.assertEqual(kinds(out), ['hold', 'notify'])
        self.assertEqual(audiences(out), ['claims', 'finance'])
        self.assertEqual(
            out[0].message,
            'CLAIM ON HOLD: Do not proceed. System Invoicing Error detected. The required '
            'billing invoice was not generated by the system.',
        )
        self.assertEqual(
            out[1].message,
            f'URGENT FINANCE CHECK: System Invoicing Error on Policy {POLICY} for Client '
            f'{CLIENT}. The expected invoice was not generated. Please investigate and '
            f'rectify the premium status.',
        )
        self.assertEqual([o.severity for o in out], ['urgent', 'urgent'])

    def test_branch_c_unpaid_premium_holds_and_calls_finance(self):
        out = evaluate_premium_confirmation(POLICY, CLIENT, 'monthly', 3, 3, 1)
        self.assertEqual(kinds(out), ['hold', 'notify'])
        self.assertEqual(
            out[0].message,
            'CLAIM ON HOLD: Do not proceed. Premium payment is missing or outstanding for '
            'the current billing cycle.',
        )
        self.assertEqual(
            out[1].message,
            f'FINANCE CHECK REQUIRED: Claim is on hold for Policy {POLICY} (Client: '
            f'{CLIENT}) due to outstanding premiums. Please check the premium status and '
            f'payment records.',
        )

    def test_both_b_and_c_returns_all_four_with_b_first(self):
        out = evaluate_premium_confirmation(POLICY, CLIENT, 'monthly', 3, 1, 2)
        self.assertEqual(len(out), 4)
        self.assertIn('System Invoicing Error', out[0].message)
        self.assertIn('System Invoicing Error', out[1].message)
        self.assertIn('Premium payment is missing', out[2].message)
        self.assertIn('outstanding premiums', out[3].message)
        self.assertEqual(kinds(out), ['hold', 'notify', 'hold', 'notify'])

    def test_nothing_reads_as_a_decline_or_repudiation(self):
        # CFO rule 2026-08-11: red never auto-declines. A hold is a hold.
        for expected, generated, unpaid in ((3, 1, 2), (3, 2, 0), (3, 3, 1), (1, 1, 0)):
            blob = ' '.join(
                messages(evaluate_premium_confirmation(POLICY, CLIENT, 'monthly',
                                                       expected, generated, unpaid))
            ).lower()
            for banned in ('decline', 'repudiat', 'reject', 'refuse'):
                self.assertNotIn(banned, blob)

    def test_unknown_billing_frequency_raises(self):
        with self.assertRaises(ValueError):
            evaluate_premium_confirmation(POLICY, CLIENT, 'weekly', 1, 1, 0)

    def test_billing_frequency_is_case_insensitive(self):
        out = evaluate_premium_confirmation(POLICY, CLIENT, 'Monthly', 1, 1, 0)
        self.assertEqual(kinds(out), ['release'])

    def test_expected_invoice_count_below_one_raises(self):
        with self.assertRaises(ValueError):
            evaluate_premium_confirmation(POLICY, CLIENT, 'monthly', 0, 0, 0)

    def test_negative_counts_raise(self):
        with self.assertRaises(ValueError):
            evaluate_premium_confirmation(POLICY, CLIENT, 'monthly', 1, -1, 0)
        with self.assertRaises(ValueError):
            evaluate_premium_confirmation(POLICY, CLIENT, 'monthly', 1, 1, -1)


# =============================================================== RULE SET 3 ==

class AolCalculationTests(unittest.TestCase):

    def test_monthly_multiplies_by_months(self):
        r = calculate_aol(Decimal('300.00'), 'monthly', 4)
        self.assertEqual(r.total, Decimal('1200.00'))
        self.assertEqual(r.periods_owed, 4)

    def test_gap_of_zero_months_is_zero(self):
        r = calculate_aol(Decimal('300.00'), 'monthly', 0)
        self.assertEqual(r.total, Decimal('0.00'))
        self.assertEqual(r.periods_owed, 0)

    def test_negative_gap_is_zero(self):
        r = calculate_aol(Decimal('300.00'), 'quarterly', -2)
        self.assertEqual(r.total, Decimal('0.00'))
        self.assertEqual(r.periods_owed, 0)

    def test_quarterly_one_month_gap_owes_a_full_quarter(self):
        r = calculate_aol(Decimal('900.00'), 'quarterly', 1)
        self.assertEqual(r.periods_owed, 1)
        self.assertEqual(r.total, Decimal('900.00'))

    def test_quarterly_three_month_gap_is_exactly_one_quarter(self):
        r = calculate_aol(Decimal('900.00'), 'quarterly', 3)
        self.assertEqual(r.periods_owed, 1)
        self.assertEqual(r.total, Decimal('900.00'))

    def test_quarterly_four_month_gap_ceils_to_two_quarters(self):
        r = calculate_aol(Decimal('900.00'), 'quarterly', 4)
        self.assertEqual(r.periods_owed, 2)
        self.assertEqual(r.total, Decimal('1800.00'))

    def test_quarterly_six_month_gap_is_two_quarters(self):
        self.assertEqual(calculate_aol(Decimal('900.00'), 'quarterly', 6).periods_owed, 2)

    def test_quarterly_seven_month_gap_is_three_quarters(self):
        self.assertEqual(calculate_aol(Decimal('900.00'), 'quarterly', 7).periods_owed, 3)

    def test_rounds_half_up_on_a_half_thebe_premium(self):
        # 0.005 * 1 month → 0.01 half up (0.00 under bankers' rounding).
        self.assertEqual(calculate_aol(Decimal('0.005'), 'monthly', 1).total,
                         Decimal('0.01'))

    def test_rounds_half_up_on_a_larger_half_thebe_case(self):
        # 100.005 → 100.01, then × 2 = 200.02
        self.assertEqual(calculate_aol(Decimal('100.005'), 'monthly', 2).total,
                         Decimal('200.02'))

    def test_total_is_a_decimal_never_a_float(self):
        self.assertIsInstance(calculate_aol(Decimal('300.00'), 'monthly', 2).total,
                              Decimal)

    def test_requires_user_confirmation_defaults_true(self):
        # The spec's manual "Confirm Calculation" step — the engine never marks
        # a deduction as applied.
        self.assertTrue(calculate_aol(Decimal('300.00'), 'monthly', 2)
                        .requires_user_confirmation)
        self.assertTrue(calculate_aol(Decimal('300.00'), 'monthly', 0)
                        .requires_user_confirmation)
        self.assertTrue(AolResult(Decimal('0.00'), 'monthly', 0, 0)
                        .requires_user_confirmation)

    def test_annual_is_not_a_valid_aol_frequency(self):
        with self.assertRaises(ValueError):
            calculate_aol(Decimal('300.00'), 'annual', 2)

    def test_unknown_frequency_raises(self):
        with self.assertRaises(ValueError):
            calculate_aol(Decimal('300.00'), 'fortnightly', 2)


class AolBreakdownTests(unittest.TestCase):

    def test_quarterly_block_matches_the_spec_layout(self):
        self.assertEqual(
            aol_breakdown_lines(Decimal('1500.00'), 'quarterly', 4),
            [
                'AOL CALCULATION FOR REVIEW:',
                '- Billing Frequency: Quarterly',
                '- Item Premium: 1,500.00',
                '- Unpaid Period: 4 Months (From Last Payment to Renewal Month)',
                '- Calculated Quarters/Months Owed: 2 quarters',
                '- SYSTEM CALCULATED AOL TO DEDUCT: 3,000.00',
            ],
        )

    def test_monthly_block_matches_the_spec_layout(self):
        self.assertEqual(
            aol_breakdown_lines(Decimal('450.50'), 'monthly', 3),
            [
                'AOL CALCULATION FOR REVIEW:',
                '- Billing Frequency: Monthly',
                '- Item Premium: 450.50',
                '- Unpaid Period: 3 Months (From Last Payment to Renewal Month)',
                '- Calculated Quarters/Months Owed: 3 months',
                '- SYSTEM CALCULATED AOL TO DEDUCT: 1,351.50',
            ],
        )

    def test_single_period_reads_singular(self):
        self.assertIn('- Calculated Quarters/Months Owed: 1 quarter',
                      aol_breakdown_lines(Decimal('900.00'), 'quarterly', 2))
        self.assertIn('- Calculated Quarters/Months Owed: 1 month',
                      aol_breakdown_lines(Decimal('900.00'), 'monthly', 1))

    def test_zero_gap_block_shows_zero(self):
        lines = aol_breakdown_lines(Decimal('900.00'), 'monthly', 0)
        self.assertIn('- Unpaid Period: 0 Months (From Last Payment to Renewal Month)',
                      lines)
        self.assertIn('- Calculated Quarters/Months Owed: 0 months', lines)
        self.assertIn('- SYSTEM CALCULATED AOL TO DEDUCT: 0.00', lines)

    def test_large_amounts_carry_thousands_commas(self):
        lines = aol_breakdown_lines(Decimal('12500'), 'quarterly', 7)
        self.assertIn('- Item Premium: 12,500.00', lines)
        self.assertIn('- SYSTEM CALCULATED AOL TO DEDUCT: 37,500.00', lines)


class AolOutcomeTests(unittest.TestCase):

    def test_zero_aol_passes_the_check(self):
        out = evaluate_aol(Decimal('300.00'), 'monthly', 0)
        self.assertEqual(kinds(out), ['notify'])
        self.assertEqual(audiences(out), ['claims'])
        self.assertEqual(
            out[0].message,
            'AOL CHECK PASSED: Outstanding amount is 0.00. Proceed with claim.',
        )
        self.assertEqual(out[0].severity, 'info')

    def test_outstanding_aol_holds_and_tells_finance_and_claims(self):
        out = evaluate_aol(Decimal('1500.00'), 'quarterly', 4)
        self.assertEqual(kinds(out), ['hold', 'notify', 'notify'])
        self.assertEqual(audiences(out), ['claims', 'finance', 'claims'])
        expected = (
            'AOL DEDUCTION REQUIRED: Full outstanding amount of 3,000.00 detected. Hold '
            'claim execution until calculation is verified by user and processed for '
            'deduction.'
        )
        self.assertEqual(set(messages(out)), {expected})
        self.assertEqual({o.severity for o in out}, {'urgent'})

    def test_aol_messages_never_read_as_a_decline(self):
        blob = ' '.join(messages(evaluate_aol(Decimal('1500.00'), 'monthly', 5))).lower()
        for banned in ('decline', 'repudiat', 'reject', 'refuse'):
            self.assertNotIn(banned, blob)

    def test_never_reports_a_deduction_as_applied(self):
        blob = ' '.join(messages(evaluate_aol(Decimal('1500.00'), 'monthly', 5))).lower()
        self.assertNotIn('deducted', blob)
        self.assertNotIn('applied', blob)


# =============================================================== RULE SET 4 ==

class RecoveryTests(unittest.TestCase):

    NOD_PROMPT = (
        'POSSIBLE RECOVERY DETECTED: This claim has been flagged for potential recovery '
        'options. ACTION REQUIRED: Please attach the Notice of Demand (NOD) immediately '
        'to proceed.'
    )

    def test_third_party_at_fault_without_nod_prompts_uploads_and_holds(self):
        out = evaluate_recovery(third_party_at_fault=True,
                                other_recovery_grounds=False,
                                notice_of_demand_attached=False)
        self.assertEqual(kinds(out), ['prompt', 'upload_required', 'hold'])
        self.assertEqual(audiences(out), ['claims', 'claims', 'claims'])
        self.assertEqual(out[0].message, self.NOD_PROMPT)
        self.assertEqual(out[1].message, 'Upload Notice of Demand Here')
        self.assertEqual(out[2].message, self.NOD_PROMPT)
        self.assertEqual({o.severity for o in out}, {'urgent'})

    def test_other_recovery_grounds_alone_also_triggers_the_nod_gate(self):
        out = evaluate_recovery(third_party_at_fault=False,
                                other_recovery_grounds=True,
                                notice_of_demand_attached=False)
        self.assertEqual(kinds(out), ['prompt', 'upload_required', 'hold'])

    def test_both_grounds_without_nod_still_gates_once(self):
        out = evaluate_recovery(third_party_at_fault=True,
                                other_recovery_grounds=True,
                                notice_of_demand_attached=False)
        self.assertEqual(kinds(out), ['prompt', 'upload_required', 'hold'])

    def test_nod_attached_means_no_hold(self):
        out = evaluate_recovery(third_party_at_fault=True,
                                other_recovery_grounds=False,
                                notice_of_demand_attached=True)
        self.assertNotIn('hold', kinds(out))
        self.assertNotIn('upload_required', kinds(out))
        self.assertEqual(kinds(out), ['notify'])
        self.assertEqual(
            out[0].message,
            'NOTICE OF DEMAND ON FILE: Recovery grounds flagged and the Notice of Demand '
            '(NOD) is attached. No hold on claim progress.',
        )
        self.assertEqual(out[0].severity, 'info')

    def test_no_recovery_ground_bypasses_the_nod_check_entirely(self):
        self.assertEqual(
            evaluate_recovery(third_party_at_fault=False,
                              other_recovery_grounds=False,
                              notice_of_demand_attached=False),
            [],
        )

    def test_no_recovery_ground_with_a_nod_on_file_is_still_a_bypass(self):
        self.assertEqual(
            evaluate_recovery(third_party_at_fault=False,
                              other_recovery_grounds=False,
                              notice_of_demand_attached=True),
            [],
        )


# =============================================================== RULE SET 5 ==

class RealpayTransactionTests(unittest.TestCase):

    def test_processed_but_not_posted_raises_an_integration_error(self):
        out = evaluate_realpay_transaction(POLICY, processed_on_realpay=True,
                                           posted_to_graphite=False)
        self.assertEqual(kinds(out), ['notify'])
        self.assertEqual(audiences(out), ['technical'])
        self.assertEqual(out[0].severity, 'urgent')
        self.assertEqual(
            out[0].message,
            f'GRAPHITE INTEGRATION ERROR: A payment transaction has been processed on '
            f'Real Pay for Policy {POLICY} but is NOT REGISTERED TO GRAPHITE. Immediate '
            f'technical reconciliation required.',
        )

    def test_processed_and_posted_is_silent(self):
        self.assertEqual(
            evaluate_realpay_transaction(POLICY, processed_on_realpay=True,
                                         posted_to_graphite=True),
            [],
        )

    def test_not_processed_is_silent_even_when_graphite_has_nothing(self):
        self.assertEqual(
            evaluate_realpay_transaction(POLICY, processed_on_realpay=False,
                                         posted_to_graphite=False),
            [],
        )

    def test_not_processed_but_posted_is_silent_here(self):
        # Out of scope for this watch — this rule only chases Real Pay money
        # that never reached Graphite.
        self.assertEqual(
            evaluate_realpay_transaction(POLICY, processed_on_realpay=False,
                                         posted_to_graphite=True),
            [],
        )


class RealpayCollectionCycleTests(unittest.TestCase):

    LAPSE = (
        f'URGENT NOTICE: Policy contract for {CLIENT} (Policy {POLICY}) is ABOUT TO LAPSE '
        f'due to failed Real Pay collections and missing updates in Graphite. Immediate '
        f'retention intervention required.'
    )
    FAIL_ALERT = (
        f'ALERT: Weekly premium payment via Real Pay has failed for Policy {POLICY} '
        f'(Client: {CLIENT}). Check payment alignment.'
    )

    def test_clean_cycle_is_silent(self):
        self.assertEqual(
            evaluate_realpay_collection_cycle(
                POLICY, CLIENT, weekly_debit_failed=False, cumulative_failures=0,
                graphite_payment_found_this_cycle=True),
            [],
        )

    def test_one_failed_debit_alerts_operations_only(self):
        out = evaluate_realpay_collection_cycle(
            POLICY, CLIENT, weekly_debit_failed=True, cumulative_failures=1,
            graphite_payment_found_this_cycle=True)
        self.assertEqual(kinds(out), ['notify'])
        self.assertEqual(audiences(out), ['operations'])
        self.assertEqual(out[0].message, self.FAIL_ALERT)
        self.assertEqual(out[0].severity, 'urgent')

    def test_default_threshold_is_three(self):
        self.assertEqual(DEFAULT_MAX_ALLOWED_FAILURES, 3)

    def test_two_failures_is_below_the_default_threshold(self):
        out = evaluate_realpay_collection_cycle(
            POLICY, CLIENT, weekly_debit_failed=False, cumulative_failures=2,
            graphite_payment_found_this_cycle=True)
        self.assertEqual(out, [])

    def test_three_failures_escalates_to_operations_and_retention(self):
        out = evaluate_realpay_collection_cycle(
            POLICY, CLIENT, weekly_debit_failed=False, cumulative_failures=3,
            graphite_payment_found_this_cycle=True)
        self.assertEqual(kinds(out), ['notify', 'notify'])
        self.assertEqual(audiences(out), ['operations', 'retention'])
        self.assertEqual(set(messages(out)), {self.LAPSE})
        self.assertEqual({o.severity for o in out}, {'critical'})

    def test_four_failures_still_escalates(self):
        out = evaluate_realpay_collection_cycle(
            POLICY, CLIENT, weekly_debit_failed=False, cumulative_failures=4,
            graphite_payment_found_this_cycle=True)
        self.assertEqual(audiences(out), ['operations', 'retention'])

    def test_no_graphite_payment_this_cycle_escalates_on_its_own(self):
        out = evaluate_realpay_collection_cycle(
            POLICY, CLIENT, weekly_debit_failed=False, cumulative_failures=0,
            graphite_payment_found_this_cycle=False)
        self.assertEqual(audiences(out), ['operations', 'retention'])
        self.assertEqual(set(messages(out)), {self.LAPSE})

    def test_failed_debit_and_threshold_breach_return_both_alert_and_lapse(self):
        out = evaluate_realpay_collection_cycle(
            POLICY, CLIENT, weekly_debit_failed=True, cumulative_failures=3,
            graphite_payment_found_this_cycle=False)
        self.assertEqual(kinds(out), ['notify', 'notify', 'notify'])
        self.assertEqual(audiences(out), ['operations', 'operations', 'retention'])
        self.assertEqual(out[0].message, self.FAIL_ALERT)
        self.assertEqual(out[1].message, self.LAPSE)
        self.assertEqual(out[2].message, self.LAPSE)

    def test_threshold_is_overridable(self):
        out = evaluate_realpay_collection_cycle(
            POLICY, CLIENT, weekly_debit_failed=False, cumulative_failures=2,
            graphite_payment_found_this_cycle=True, max_allowed_failures=2)
        self.assertEqual(audiences(out), ['operations', 'retention'])

    def test_lapse_warning_never_reads_as_a_repudiation(self):
        # Three unpaid premiums qualifies for repudiation under the policy
        # wording — the engine still only warns. Management decides.
        blob = self.LAPSE.lower()
        for banned in ('repudiat', 'decline', 'reject', 'cancelled', 'has lapsed'):
            self.assertNotIn(banned, blob)


class OutcomeContractTests(unittest.TestCase):
    """The four fields are the whole contract with the callers — guard them."""

    ALLOWED_KINDS = {'notify', 'task', 'hold', 'release', 'prompt', 'upload_required'}
    ALLOWED_AUDIENCES = {'claims', 'finance', 'underwriting', 'operations', 'retention',
                         'technical'}
    ALLOWED_SEVERITIES = {'info', 'warning', 'urgent', 'critical'}

    def all_outcomes(self):
        out = []
        out += evaluate_write_off(CLIENT, VEHICLE, 1)
        out += evaluate_write_off(CLIENT, VEHICLE, 2, Decimal('100'))
        out += evaluate_write_off(CLIENT, VEHICLE, 2)
        out += evaluate_write_off(CLIENT, VEHICLE, 0)
        out += evaluate_premium_confirmation(POLICY, CLIENT, 'monthly', 1, 1, 0)
        out += evaluate_premium_confirmation(POLICY, CLIENT, 'monthly', 3, 1, 2)
        out += evaluate_aol(Decimal('300'), 'monthly', 0)
        out += evaluate_aol(Decimal('300'), 'quarterly', 4)
        out += evaluate_recovery(True, False, False)
        out += evaluate_recovery(True, False, True)
        out += evaluate_realpay_transaction(POLICY, True, False)
        out += evaluate_realpay_collection_cycle(POLICY, CLIENT, True, 3, False)
        return out

    def test_every_outcome_uses_a_known_vocabulary(self):
        for o in self.all_outcomes():
            self.assertIsInstance(o, Outcome)
            self.assertIn(o.kind, self.ALLOWED_KINDS, o.message)
            self.assertIn(o.audience, self.ALLOWED_AUDIENCES, o.message)
            self.assertIn(o.severity, self.ALLOWED_SEVERITIES, o.message)
            self.assertTrue(o.message.strip())

    def test_no_outcome_anywhere_reads_as_a_decline(self):
        # CFO design rule 2026-08-11, asserted across all five rule sets at once.
        for o in self.all_outcomes():
            low = o.message.lower()
            for banned in ('decline', 'repudiat', 'reject', 'refuse'):
                self.assertNotIn(banned, low, o.message)
            self.assertNotEqual(o.kind, 'decline')


if __name__ == '__main__':
    unittest.main()
