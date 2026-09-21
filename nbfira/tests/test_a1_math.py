"""Statement A.1 arithmetic, checked against the RETURNS ACTUALLY FILED.

CFO decision 2026-08-18: adopt the filed workbook's method. These tests are the
proof it was adopted correctly — every input below was read out of the four
FY2026 workbooks Oprah Mogomotsi uploaded, and every expected figure is the
answer that workbook produced.

Q1, Q2 and Q4 must reproduce to the cent. Q3 must NOT: its allocation cells were
hand-typed over the template's formulas, which is a defect in that filed return,
not in this arithmetic. `test_q3_does_not_reconcile_because_it_was_hand_edited`
pins that so nobody later "fixes" the formula to chase Q3 and breaks the other
three.
"""
from decimal import Decimal

from django.test import SimpleTestCase

from nbfira.a1_math import allocate, class_charge, compute_a1
from nbfira.constants import DEFAULT_MRC, INSURANCE_CLASSES

BUCKETS = [key for key, _lbl, _f in DEFAULT_MRC]
MRC_FACTORS = {key: Decimal(str(f)) for key, _lbl, f in DEFAULT_MRC}
# The workbook carries the same class loadings Omni already holds.
IRC_FACTORS = {
    'property': '0.15', 'transportation': '0.30', 'motor': '0.15',
    'accident': '0.50', 'health': '0.50', 'guarantee': '0.30',
    'liability': '0.70', 'engineering': '0.30', 'miscellaneous': '0.50',
}
MCR = Decimal('5000')

# ── inputs lifted from the filed workbooks (P'000) ────────────────────────
FILED = {
    '2026Q1': {
        'anwp': {'property': 5631, 'transportation': 1008, 'motor': 17352,
                 'accident': 3422, 'health': 473, 'guarantee': 255,
                 'liability': 3398, 'engineering': None, 'miscellaneous': 1284},
        'net_assets':  {'cash': '1006.7649399999991', 'other_assets': 33603},
        'alloc_mrctr': {'cash': '1006.7649399999991', 'other_assets': '16172.23506'},
        'expect': {'irc_total': '10404.3625', 'mrc_total': '5660.28227',
                   'irc_adj': '7698.5461', 'mrc_adj': '4188.2378',
                   'g_ins': '0.825', 'g_mkt': '0.65', 'pct': '11562.4482'},
    },
    '2026Q2': {
        'anwp': {'property': 5682, 'transportation': 1192, 'motor': 5649,
                 'accident': 5796, 'health': 1062, 'guarantee': 522,
                 'liability': 5937, 'engineering': 787, 'miscellaneous': 1860},
        'net_assets':  {'cash': '537.0255700000134', 'other_assets': 47474},
        'alloc_mrctr': {'cash': '537.0255700000134', 'other_assets': '24173.54344999999'},
        'expect': {'irc_total': '9862.9625', 'mrc_total': '8460.74021',
                   'irc_adj': '7003.2386', 'mrc_adj': '6007.5847',
                   'g_ins': '0.825', 'g_mkt': '0.65', 'pct': '12725.2600'},
    },
    '2026Q4': {
        'anwp': {'property': 6712, 'transportation': 1487, 'motor': 7492,
                 'accident': 6325, 'health': 1121, 'guarantee': 722,
                 'liability': 6219, 'engineering': 926, 'miscellaneous': 2156},
        'net_assets':  {'cash': '-7653.4400000000005', 'other_assets': 46965},
        'alloc_mrctr': {'cash': '-7653.4400000000005', 'other_assets': '22906.67'},
        'expect': {'irc_total': '11346.35', 'mrc_total': '8017.3345',
                   'irc_adj': '8158.5711', 'mrc_adj': '5764.8489',
                   'g_ins': '0.825', 'g_mkt': '0.65', 'pct': '13479.4216'},
    },
}

# Q3 — the hand-edited one. Same shape, kept separate so it can never be
# mistaken for a reconciling period.
Q3 = {
    'anwp': FILED['2026Q4']['anwp'],          # identical ANWP to Q4 in the file
    'net_assets':  {'cash': 3613, 'other_assets': 47511},
    'alloc_mrctr': {'cash': 3613, 'other_assets': 20000,
                    'unlisted_equities': '-4191.430979999997'},
    'filed_pct': '12262.4095',
    'filed_g_mkt': '0.6634933617104913',
}


def run(case):
    return compute_a1(
        mcr=MCR,
        classes=INSURANCE_CLASSES,
        irc_factors={k: Decimal(v) for k, v in IRC_FACTORS.items()},
        anwp=case['anwp'],
        mer_total=Decimal('300'),
        buckets=BUCKETS,
        mrc_factors=MRC_FACTORS,
        net_assets=case['net_assets'],
        alloc_mrctr=case['alloc_mrctr'],
    )


def close(a, b, tol='0.01'):
    return abs(Decimal(str(a)) - Decimal(str(b))) <= Decimal(tol)


class ClassChargeTest(SimpleTestCase):

    def test_charge_is_a_quarter_of_premium_uplifted_by_the_loading(self):
        """Property Q4: 25% × 6,712 × 1.15 = 1,929.70. Omni used to compute
        0.15 × 6,712 = 1,006.80 — the wrong figure AND the wrong shape."""
        self.assertTrue(close(class_charge(6712, '0.15'), '1929.70'))

    def test_the_old_flat_multiplication_is_not_what_this_does(self):
        self.assertFalse(close(class_charge(6712, '0.15'), '1006.80'))

    def test_a_blank_premium_cell_is_zero_not_a_crash(self):
        """Q1 left Engineering empty."""
        self.assertEqual(class_charge(None, '0.30'), Decimal('0'))


class AllocateTest(SimpleTestCase):

    def test_first_need_is_filled_before_the_second(self):
        first, second = allocate([Decimal('100')], Decimal('30'), Decimal('50'))
        self.assertEqual(first, [Decimal('30')])
        self.assertEqual(second, [Decimal('50')])

    def test_a_bucket_only_gives_what_it_has(self):
        first, second = allocate([Decimal('10'), Decimal('90')],
                                 Decimal('30'), Decimal('0'))
        self.assertEqual(first, [Decimal('10'), Decimal('20')])

    def test_order_decides_which_bucket_funds_which_need(self):
        """This is why the g-factors depend on the asset MIX, not just totals."""
        first, _ = allocate([Decimal('0'), Decimal('50')],
                            Decimal('50'), Decimal('0'))
        self.assertEqual(first, [Decimal('0'), Decimal('50')])


class FiledReturnsReconcileTest(SimpleTestCase):
    """The whole point: our arithmetic must land on the filed answer."""

    def test_every_intact_filed_quarter_reproduces_exactly(self):
        for period, case in FILED.items():
            with self.subTest(period=period):
                got, exp = run(case), case['expect']
                self.assertTrue(close(got['irc_total'], exp['irc_total']),
                                f"{period} IRC total {got['irc_total']}")
                self.assertTrue(close(got['mrc_total'], exp['mrc_total']),
                                f"{period} MRC total {got['mrc_total']}")
                self.assertTrue(close(got['irc_adj'], exp['irc_adj']),
                                f"{period} IRC adj {got['irc_adj']}")
                self.assertTrue(close(got['mrc_adj'], exp['mrc_adj']),
                                f"{period} MRC adj {got['mrc_adj']}")
                self.assertTrue(close(got['g_insurance'], exp['g_ins'], '0.0001'),
                                f"{period} g_ins {got['g_insurance']}")
                self.assertTrue(close(got['g_market'], exp['g_mkt'], '0.0001'),
                                f"{period} g_mkt {got['g_market']}")
                self.assertTrue(close(got['pct'], exp['pct']),
                                f"{period} PCT {got['pct']} != {exp['pct']}")

    def test_q4_target_to_the_cent(self):
        """Named separately because it is the figure Finance quoted."""
        self.assertTrue(close(run(FILED['2026Q4'])['pct'], '13479.4216'))

    def test_the_g_factors_are_derived_and_do_move(self):
        """Not stored constants. Change the asset mix, g changes."""
        base = run(FILED['2026Q4'])
        shifted = run({**FILED['2026Q4'],
                       'net_assets': {'cash': '-7653.4400000000005',
                                      'other_assets': 30000,
                                      'unlisted_equities': 16965},
                       'alloc_mrctr': {'cash': '-7653.4400000000005',
                                       'other_assets': '22906.67'}})
        self.assertNotEqual(base['g_insurance'], shifted['g_insurance'])

    def test_q3_does_not_reconcile_because_it_was_hand_edited(self):
        """Q3's filed workbook has LITERALS where the template has formulas in
        the asset-allocation block (B59/C59/D59/E59), which drives one row to a
        NEGATIVE allocation of −1,084.07 and shifts g_market to 0.6635.

        Our answer is the template's answer. The ~P53k gap is that override.
        Do NOT bend the formula to match Q3 — it would break Q1, Q2 and Q4.
        """
        got = run(Q3)
        self.assertFalse(close(got['pct'], Q3['filed_pct']),
                         'Q3 reconciling would mean the formula chased an override')
        self.assertTrue(close(got['g_market'], '0.65', '0.0001'))
        self.assertFalse(close(got['g_market'], Q3['filed_g_mkt'], '0.0001'))
        # Still the right order of magnitude — this is an override, not chaos.
        self.assertLess(abs(got['pct'] - Decimal(Q3['filed_pct'])), Decimal('100'))


class EmptyInputsTest(SimpleTestCase):

    def test_no_inputs_floors_at_the_statutory_minimum_never_zero(self):
        """Omni's risk inputs are still unfilled. The sheet would return 0 here;
        a Prescribed Capital Target of 0.00 reads as 'none required', so we floor
        at the MCR instead."""
        got = compute_a1(
            mcr=MCR, classes=INSURANCE_CLASSES,
            irc_factors={k: Decimal(v) for k, v in IRC_FACTORS.items()},
            anwp={}, mer_total=Decimal('0'), buckets=BUCKETS,
            mrc_factors=MRC_FACTORS, net_assets={}, alloc_mrctr={},
        )
        self.assertEqual(got['pct'], MCR)
        self.assertNotEqual(got['pct'], Decimal('0'))
        self.assertTrue(any('minimum capital requirement' in n.lower()
                            for n in got['notes']), got['notes'])
