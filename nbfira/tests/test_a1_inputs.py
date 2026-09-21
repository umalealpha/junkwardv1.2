"""Entering the A.1 assumptions the ledger cannot supply.

CFO 2026-08-18: Omni now computes A.1 the workbook's way, but three of its
inputs are entered figures — assumed annual NWP per class (forward twelve
months), the treaty event retention, and the per-bucket asset split. Without
somewhere to enter them the target correctly sits on the statutory MCR floor,
so the feature is only finished once it can be filled in.

The load-bearing tests here:
  * saving the FILED Q4 assumptions makes Omni's A1_PCT come out at the filed
    13,479.4216 — end to end, through the real endpoint;
  * a save with no stated source is refused, because a regulatory assumption
    with no provenance cannot be evidenced later;
  * the Finance Manager (the reviewer's role) can do it, and an ordinary staff login
    cannot.
"""
import datetime as dt
from decimal import Decimal

from django.contrib.auth.models import User
from rest_framework.test import APIClient, APITestCase

from core.models import Company, UserProfile
from nbfira.models import NBFIRAA1Input, NBFIRAReturn, NBFIRAReturnLine

URL = '/api/v1/nbfira/returns'

# Straight off the filed 2026Q4 workbook (P'000).
FILED_Q4 = {
    'anwp': {'property': '6712', 'transportation': '1487', 'motor': '7492',
             'accident': '6325', 'health': '1121', 'guarantee': '722',
             'liability': '6219', 'engineering': '926', 'miscellaneous': '2156'},
    'mer_total': '300',
    'net_assets':  {'cash': '-7653.44', 'other_assets': '46965'},
    'alloc_mrctr': {'cash': '-7653.44', 'other_assets': '22906.67'},
    'source_note': 'As per the filed Q4 June 2026 workbook.',
}


class A1InputsTest(APITestCase):

    @classmethod
    def setUpTestData(cls):
        cls.company = Company.objects.create(code='NBIN', name='NBFIRA Inputs Co.')
        # finance_manager — the role the reviewer signs in with.
        cls.fm = User.objects.create_user('fm', 'fm@test.example', 'x')
        UserProfile.objects.update_or_create(
            user=cls.fm, defaults={'title': 'finance_manager', 'is_active': True})

    def setUp(self):
        self.client = APIClient()
        self.client.force_authenticate(self.fm)
        self.ret = NBFIRAReturn.objects.create(
            type='quarterly', period_label='2026Q4', company=self.company,
            period_start=dt.date(2026, 4, 1), period_end=dt.date(2026, 6, 30),
        )

    def url(self, ret=None):
        return f'{URL}/{(ret or self.ret).id}/a1-inputs/'

    def pct(self):
        line = NBFIRAReturnLine.objects.filter(
            return_obj=self.ret, schedule='A.1', line_code='A1_PCT').first()
        return line.value if line else None

    # ── the one that matters ────────────────────────────────────────────
    def test_saving_the_filed_assumptions_reproduces_the_filed_target(self):
        r = self.client.put(self.url(), FILED_Q4, format='json')
        self.assertEqual(r.status_code, 200, r.data)
        self.assertAlmostEqual(self.pct(), Decimal('13479.4216'), places=3)

    def test_before_any_entry_the_target_sits_on_the_statutory_floor(self):
        """Not zero — a target of 0.00 would read as 'none required'."""
        self.client.post(f'{URL}/{self.ret.id}/regenerate/')
        self.assertEqual(self.pct(), Decimal('5000.0000'))

    def test_changing_an_assumption_moves_the_target(self):
        self.client.put(self.url(), FILED_Q4, format='json')
        first = self.pct()
        halved = {**FILED_Q4,
                  'anwp': {k: str(Decimal(v) / 2) for k, v in FILED_Q4['anwp'].items()},
                  'source_note': 'Halved premium assumption, testing sensitivity.'}
        self.client.put(self.url(), halved, format='json')
        self.assertLess(self.pct(), first)

    # ── provenance ──────────────────────────────────────────────────────
    def test_a_save_with_no_stated_source_is_refused(self):
        r = self.client.put(self.url(), {**FILED_Q4, 'source_note': '  '},
                            format='json')
        self.assertEqual(r.status_code, 400)
        self.assertFalse(NBFIRAA1Input.objects.filter(return_obj=self.ret).exists())

    def test_who_entered_it_is_recorded(self):
        self.client.put(self.url(), FILED_Q4, format='json')
        row = NBFIRAA1Input.objects.get(return_obj=self.ret)
        self.assertEqual(row.entered_by, self.fm)
        self.assertIn('filed Q4', row.source_note)

    def test_the_entry_is_written_to_the_audit_log(self):
        self.client.put(self.url(), FILED_Q4, format='json')
        self.assertTrue(self.ret.audit_logs.filter(action='edit').exists())

    # ── guards ──────────────────────────────────────────────────────────
    def test_rubbish_numbers_are_refused_with_the_offending_value(self):
        bad = {**FILED_Q4, 'anwp': {**FILED_Q4['anwp'], 'motor': 'lots'}}
        r = self.client.put(self.url(), bad, format='json')
        self.assertEqual(r.status_code, 400)
        self.assertIn('lots', r.data['detail'])

    def test_unknown_keys_are_dropped_not_stored(self):
        odd = {**FILED_Q4, 'anwp': {**FILED_Q4['anwp'], 'spaceships': '99'}}
        self.client.put(self.url(), odd, format='json')
        self.assertNotIn('spaceships',
                         NBFIRAA1Input.objects.get(return_obj=self.ret).anwp)

    def test_a_locked_return_refuses_the_change(self):
        self.ret.status = NBFIRAReturn.Status.LOCKED
        self.ret.save()
        r = self.client.put(self.url(), FILED_Q4, format='json')
        self.assertEqual(r.status_code, 409)

    def test_saving_replaces_the_lines_rather_than_duplicating_them(self):
        self.client.put(self.url(), FILED_Q4, format='json')
        after_one = NBFIRAReturnLine.objects.filter(return_obj=self.ret).count()
        self.client.put(self.url(), FILED_Q4, format='json')
        self.assertEqual(
            NBFIRAReturnLine.objects.filter(return_obj=self.ret).count(),
            after_one)

    # ── who may do it ───────────────────────────────────────────────────
    def test_the_finance_manager_may_read_and_save(self):
        self.assertEqual(self.client.get(self.url()).status_code, 200)
        self.assertEqual(
            self.client.put(self.url(), FILED_Q4, format='json').status_code, 200)

    def test_an_ordinary_staff_login_cannot(self):
        clerk = User.objects.create_user('clerk2', 'clerk2@test.example', 'x')
        UserProfile.objects.update_or_create(
            user=clerk, defaults={'title': 'accountant', 'is_active': True})
        c = APIClient()
        c.force_authenticate(clerk)
        self.assertIn(c.get(self.url()).status_code, (403, 404))
        self.assertIn(c.put(self.url(), FILED_Q4, format='json').status_code,
                      (403, 404))

    def test_signed_out_cannot(self):
        self.assertIn(APIClient().get(self.url()).status_code, (401, 403))


class FormulaTraceLengthTest(APITestCase):
    """NBFIRAReturnLine.formula is varchar(200). The A.1 trace carries the
    derived g-factors and a floor note, and once ran past that — which is a 500
    on Regenerate, not a cosmetic problem. Caught by the empty-inputs case,
    which is exactly the state every period is in before anyone enters anything.
    """

    @classmethod
    def setUpTestData(cls):
        cls.company = Company.objects.create(code='NBFL', name='NBFIRA Len Co.')
        cls.user = User.objects.create_user('fml', 'fml@test.example', 'x')
        UserProfile.objects.update_or_create(
            user=cls.user, defaults={'title': 'finance_manager', 'is_active': True})

    def test_no_a1_line_trace_exceeds_the_column(self):
        from nbfira.builders import build_schedule_a1
        for label, inputs in (('empty', None), ('filled', {
                'anwp': {'motor': Decimal('7492')}, 'mer_total': Decimal('300'),
                'net_assets': {'other_assets': Decimal('46965')},
                'alloc_mrctr': {'other_assets': Decimal('22906.67')}})):
            with self.subTest(inputs=label):
                for line in build_schedule_a1(dt.date(2026, 4, 1),
                                              dt.date(2026, 6, 30), inputs=inputs):
                    self.assertLessEqual(
                        len(line['formula']), 200,
                        f"{line['line_code']} trace is {len(line['formula'])} chars")

    def test_regenerate_actually_saves_with_no_assumptions_entered(self):
        """The end-to-end version: this 500'd before the cap."""
        ret = NBFIRAReturn.objects.create(
            type='quarterly', period_label='2026Q2', company=self.company,
            period_start=dt.date(2025, 10, 1), period_end=dt.date(2025, 12, 31),
        )
        c = APIClient()
        c.force_authenticate(self.user)
        r = c.post(f'{URL}/{ret.id}/regenerate/')
        self.assertEqual(r.status_code, 200, r.data)
        self.assertTrue(NBFIRAReturnLine.objects.filter(
            return_obj=ret, line_code='A1_PCT').exists())


class EffectiveFromTest(A1InputsTest):
    """The field the reviewer actually fills in, which no test covered.

    Bug report 12 Aug 2026: "Save and recompute A.1" returned HTTP 500 on all four
    FY2026 quarters. Her step 4 was *Set "Effective from" date* — and every test in
    this file omitted that key, so the whole suite was green against a payload no
    user sends.

    What made it worse than an ordinary 500: the write had already COMMITTED. The
    atomic block closes before the response is built, so `update_or_create` saved,
    the lines were rebuilt, and only then did serialising the reply raise
    AttributeError — `update_or_create` leaves `effective_from` as the *string* it
    was handed, and `_a1_input()` calls `.isoformat()` on it. She was told it failed
    four times while her figures were landing correctly every time.
    """

    def test_saving_with_an_effective_from_date_succeeds(self):
        r = self.client.put(self.url(), {**FILED_Q4, 'effective_from': '2026-06-30'},
                            format='json')
        self.assertEqual(r.status_code, 200, r.data)
        self.assertEqual(r.data['saved']['effective_from'], '2026-06-30')

    def test_the_date_is_stored_as_a_date_not_a_string(self):
        self.client.put(self.url(), {**FILED_Q4, 'effective_from': '2026-06-30'},
                        format='json')
        row = NBFIRAA1Input.objects.get(return_obj=self.ret)
        self.assertEqual(row.effective_from, dt.date(2026, 6, 30))

    def test_the_target_still_comes_out_at_the_filed_figure(self):
        """The date must not disturb the number the whole feature exists for."""
        r = self.client.put(self.url(), {**FILED_Q4, 'effective_from': '2026-06-30'},
                            format='json')
        self.assertEqual(r.status_code, 200, r.data)
        pct = NBFIRAReturnLine.objects.get(return_obj=self.ret, line_code='A1_PCT')
        self.assertEqual(Decimal(str(pct.value)).quantize(Decimal('0.0001')),
                         Decimal('13479.4216'))

    def test_saving_without_the_date_still_works(self):
        """It is optional — the earlier tests' payload must keep passing."""
        r = self.client.put(self.url(), FILED_Q4, format='json')
        self.assertEqual(r.status_code, 200, r.data)
        self.assertIsNone(r.data['saved']['effective_from'])

    def test_a_date_that_is_not_a_date_is_refused_with_a_readable_reason(self):
        r = self.client.put(self.url(), {**FILED_Q4, 'effective_from': '30/06/2026'},
                            format='json')
        self.assertEqual(r.status_code, 400, r.data)
        self.assertIn('YYYY-MM-DD', r.data['detail'])

    def test_a_refused_date_saves_nothing(self):
        """A 400 must not be the 500's cousin — no half-commit behind the error."""
        self.client.put(self.url(), {**FILED_Q4, 'effective_from': 'tomorrow'},
                        format='json')
        self.assertFalse(NBFIRAA1Input.objects.filter(return_obj=self.ret).exists())

    def test_the_saved_date_survives_a_reload(self):
        self.client.put(self.url(), {**FILED_Q4, 'effective_from': '2026-06-30'},
                        format='json')
        g = self.client.get(self.url())
        self.assertEqual(g.status_code, 200, g.data)
        self.assertEqual(g.data['saved']['effective_from'], '2026-06-30')
