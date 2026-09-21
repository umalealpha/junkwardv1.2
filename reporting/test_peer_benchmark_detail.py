"""
Guards on the line-level benchmark.

The detail layer exists to stop three specific mistakes, and each has a test:
reading a blended loss ratio as if it described the motor book, comparing only
below-the-line expenses against peers who report half their costs above the
line, and reading the reinsurance sliding scale backwards.
"""
from django.test import SimpleTestCase

from reporting.peer_benchmark_detail import (
    ADIC_CLASSES,
    EXPENSE_LINES,
    build_detail,
    class_analysis,
    cost_base_analysis,
    motor_commission_entitlement,
)


class ClassOfBusinessTests(SimpleTestCase):
    """A blended loss ratio hides the book that is actually losing money."""

    def setUp(self):
        self.c = class_analysis()

    def test_the_blend_hides_the_motor_book(self):
        """58.7% blended reads healthy. Motor underneath it is 85.8%."""
        self.assertAlmostEqual(self.c['blended_loss_ratio'], 58.7, delta=0.2)
        self.assertGreater(self.c['motor']['loss_ratio'], 80.0)
        self.assertLess(self.c['non_motor']['loss_ratio'], 45.0)
        gap = self.c['motor']['loss_ratio'] - self.c['blended_loss_ratio']
        self.assertGreater(gap, 25, 'motor must show as materially worse than the blend')

    def test_personal_lines_motor_is_losing_money_outright(self):
        pl = next(c for c in self.c['classes'] if c['name'] == 'Personal Lines Motor')
        self.assertGreater(pl['loss_ratio'], 100.0)
        self.assertLess(pl['margin'], 0)

    def test_instant_insurance_is_the_profitable_book(self):
        ii = next(c for c in self.c['classes'] if c['name'] == 'Instant Insurance')
        self.assertLess(ii['loss_ratio'], 10.0)
        self.assertGreater(ii['share_of_book'], 15.0)

    def test_class_premiums_reconcile_to_the_nine_month_total(self):
        self.assertAlmostEqual(self.c['total_premium'], 96.097, delta=0.02)

    def test_basis_is_labelled_as_nine_months(self):
        """These must never be read as a full year alongside the 12-month
        figures in peer_benchmark.py."""
        self.assertIn('Nine months', self.c['basis'])


class MotorTreatyTests(SimpleTestCase):
    """The sliding scale is easy to read backwards: a LOW loss ratio earns a
    HIGH commission. Reading it the wrong way turns a warning into a fictional
    receivable."""

    def test_a_bleeding_book_earns_the_floor_not_the_top_rate(self):
        e = motor_commission_entitlement(85.8)
        self.assertEqual(e['commission'], 24.0)
        self.assertTrue(e['at_floor'])
        self.assertTrue(e['above_cap'])

    def test_a_clean_book_earns_the_top_rate(self):
        self.assertEqual(motor_commission_entitlement(55.0)['commission'], 41.0)
        self.assertEqual(motor_commission_entitlement(56.0)['commission'], 41.0)

    def test_the_scale_is_monotonic_downward(self):
        """Commission must never rise as the loss ratio rises."""
        rates = [motor_commission_entitlement(lr)['commission']
                 for lr in range(50, 95, 2)]
        self.assertEqual(rates, sorted(rates, reverse=True))

    def test_the_documented_mid_points_match_the_signed_slip(self):
        self.assertEqual(motor_commission_entitlement(57.5)['commission'], 39.5)
        self.assertEqual(motor_commission_entitlement(75.0)['commission'], 24.0)


class CostBaseTests(SimpleTestCase):
    """Peers report expenses in two places. Comparing one place understates
    them — Insure Guard reads 19.2% below the line and 44.9% in truth."""

    def setUp(self):
        self.cb = cost_base_analysis()
        self.by = {r['name']: r for r in self.cb['rows']}

    def test_insure_guard_total_is_far_above_its_below_the_line_figure(self):
        ig = self.by['Insure Guard']
        self.assertAlmostEqual(ig['below_only_pct'], 19.2, delta=0.3)
        self.assertAlmostEqual(ig['total_pct'], 44.9, delta=0.3)
        self.assertGreater(ig['total_cost'], ig['below_line_only'] * 2)

    def test_our_staff_cost_is_in_line_with_the_market(self):
        """The payroll is not the problem; the retained base under it is.
        If this ever drifts, the story on the dashboard changes."""
        us = self.by['Alpha Direct']
        self.assertAlmostEqual(us['staff_pct'], 9.8, delta=0.2)
        self.assertLess(abs(us['staff_pct'] - self.cb['median_staff_pct']), 2.0)

    def test_missing_staff_note_gives_none_not_zero(self):
        sunshine = self.by['Sunshine']
        self.assertIsNone(sunshine['staff_total'])
        self.assertIsNone(sunshine['total_pct'])
        self.assertIsNone(sunshine['below_only_pct'])

    def test_distressed_peer_excluded_from_the_medians(self):
        self.assertEqual(self.cb['median_staff_pct'], 9.7)
        self.assertEqual(self.cb['median_total_pct'], 19.2)


class ExpenseLineTests(SimpleTestCase):

    def test_lines_reconcile_to_the_expense_workbook(self):
        fy25 = sum(a for _, _, a, _ in EXPENSE_LINES)
        fy26 = sum(b for _, _, _, b in EXPENSE_LINES)
        self.assertAlmostEqual(fy25, 29_865_863, delta=50)
        self.assertAlmostEqual(fy26, 31_572_193, delta=50)

    def test_people_is_the_largest_group(self):
        groups = build_detail()['expenses']['groups']
        self.assertEqual(groups[0]['group'], 'People')
        self.assertGreater(groups[0]['share'], 40)

    def test_every_line_has_a_group(self):
        for group, line, _, _ in EXPENSE_LINES:
            self.assertTrue(group, f'{line} has no group')


class ExpenseBaseTests(SimpleTestCase):
    """Two expense bases are in play and they are NOT interchangeable. Mixing
    them produced a document that quoted 24.8%, 25.7% and 29.9% for what
    read like the same thing."""

    def test_the_two_bases_differ_by_exactly_the_provisions(self):
        cb = cost_base_analysis()
        self.assertAlmostEqual(cb['running_cost_base'], 33.226, places=2)
        self.assertAlmostEqual(cb['underwriting_cost_base'], 38.710, places=2)
        self.assertAlmostEqual(
            cb['underwriting_cost_base'] - cb['running_cost_base'],
            cb['provisions'], places=2)

    def test_the_peer_comparison_uses_the_running_base(self):
        """Peers rarely disclose a credit-loss charge, so comparing our
        provisions against a blank would overstate our cost base."""
        cb = cost_base_analysis()
        us = next(r for r in cb['rows'] if r['is_us'])
        self.assertAlmostEqual(us['total_cost'], cb['running_cost_base'], places=2)
        self.assertNotAlmostEqual(us['total_cost'], cb['underwriting_cost_base'], places=2)


class ReinsurerEconomicsTests(SimpleTestCase):
    """The slip prints a 'reinsurer margin' column. That is a parameter of the
    scale at each rung, not what the reinsurer actually earns at our loss
    ratio — and the difference is the whole renewal argument."""

    def test_the_reinsurer_is_under_water_at_our_loss_ratio(self):
        e = motor_commission_entitlement(85.8)
        self.assertEqual(e['reinsurer_result'], -9.8)
        self.assertLess(e['reinsurer_result'], 0)

    def test_the_reinsurer_is_already_under_water_at_the_cap(self):
        e = motor_commission_entitlement(85.8)
        self.assertEqual(e['reinsurer_result_at_cap'], -4.0)

    def test_a_clean_book_leaves_the_reinsurer_whole(self):
        e = motor_commission_entitlement(56.0)
        self.assertGreater(e['reinsurer_result'], 0)


class PeriodConsistencyTests(SimpleTestCase):
    """Nine-month premium must not be paired with a twelve-month cost."""

    def test_instant_insurance_acquisition_is_the_nine_month_figure(self):
        c = class_analysis()
        self.assertAlmostEqual(c['instant_insurance_acquisition_9m'], 4.992, places=3)
        # The twelve-month figure is 5.736 — pairing it with nine-month
        # premium is the mistake this pins.
        self.assertNotAlmostEqual(c['instant_insurance_acquisition_9m'], 5.736, places=2)


class PeerProfileTests(SimpleTestCase):
    """The profiles are the record the whole narrative rests on. Each one must
    reconcile to its own statement, because a profile that does not is worse
    than no profile — it looks authoritative and is wrong."""

    def setUp(self):
        from reporting.peer_benchmark_detail import peer_profiles
        self.profiles = {p['key']: p for p in peer_profiles()}

    def test_all_eight_are_present_and_flagged(self):
        self.assertEqual(len(self.profiles), 8)
        self.assertEqual(self.profiles['Phoenix']['flag'], 'twin')
        self.assertEqual(self.profiles['WestSure']['flag'], 'distressed')

    def test_every_insurance_service_result_reconciles(self):
        """revenue less service expenses less net reinsurance = the result."""
        for key, p in self.profiles.items():
            derived = p['revenue'] - p['ise'] - p['net_ri']
            self.assertAlmostEqual(
                derived, p['isr'], delta=max(abs(p['isr']) * 0.001, 2),
                msg=f"{key}: {derived:,.0f} derived vs {p['isr']:,.0f} reported")

    def test_insure_guard_result_is_the_reported_one(self):
        """It was transcribed as 31,580,648 at first — a figure that ignored
        reinsurance entirely. The P&L says 28,216,584."""
        self.assertEqual(self.profiles['Insure Guard']['isr'], 28_216_584)

    def test_westsure_reinsurance_components_sum_to_the_net(self):
        ws = self.profiles['WestSure']
        self.assertAlmostEqual(sum(v for _, v in ws['ri_components']),
                               ws['net_ri'], places=0)

    def test_bic_reinsurance_reconciles_only_with_the_non_performance_line(self):
        bic = self.profiles['BIC']
        without = bic['ceded'] - bic['recovered'] - bic['ri_commission']
        self.assertNotAlmostEqual(without, bic['net_ri'], places=0)
        with_it = without + bic['ri_other'][0][1]
        self.assertAlmostEqual(with_it, bic['net_ri'], places=0)

    def test_a_missing_staff_note_stays_missing(self):
        self.assertIsNone(self.profiles['Sunshine']['staff_total'])

    def test_every_profile_carries_units_and_a_headline(self):
        for key, p in self.profiles.items():
            self.assertIn(p['units'], ("Pula", "P'000"), key)
            self.assertTrue(p['headline'], key)
            self.assertTrue(p['pct_note'], key)


class PayloadTests(SimpleTestCase):

    def test_payload_carries_every_section(self):
        d = build_detail()
        for key in ('cost_base', 'expenses', 'classes', 'bic_cession',
                    'hollard_cession', 'adic_treaties', 'motor_entitlement'):
            self.assertIn(key, d)

    def test_bic_cedes_its_property_and_keeps_its_motor(self):
        """The structural contrast the whole reinsurance section rests on."""
        by = {c['cls']: c for c in build_detail()['bic_cession']}
        self.assertLess(by['Motor']['pct'], 5)
        self.assertGreater(by['Property']['pct'], 70)
