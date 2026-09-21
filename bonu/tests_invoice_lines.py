"""The transactions behind a consolidated billed figure must reconcile to it.

Kutlo Keitumele asked for this on 11 August 2026: the BONU screens showed totals
only — P4.8m billed, P1.1m for one firm — with nothing underneath, so Finance could
not verify a pula of it or explain a difference to the union.

The contract these tests hold is narrow and important: the detail is built from the
SAME query as the consolidated figure, so a difference between them is a real
difference in the data and never two queries disagreeing with each other.
"""
from decimal import Decimal as D

from django.contrib.auth.models import User
from django.test import TestCase
from rest_framework.test import APIRequestFactory, force_authenticate

from bonu.models import BonuInvoice, BonuInvoiceLine, LawFirm
from bonu.views import forensics, invoice_lines
from core.models import Company, UserProfile


class BonuLineDetailTests(TestCase):
    def setUp(self):
        self.company = (Company.objects.filter(code='ADIC').first()
                        or Company.objects.create(code='ADIC', name='ADIC'))
        self.user = User.objects.create_user('bonu-fin', email='fin@example.invalid',
                                             password='x')
        UserProfile.objects.update_or_create(
            user=self.user, defaults={'title': UserProfile.Title.CFO, 'is_active': True})
        self.user = User.objects.get(pk=self.user.pk)

        self.firm_a = LawFirm.objects.create(name='Alpha Attorneys')
        self.firm_b = LawFirm.objects.create(name='Beta & Partners')
        self._line(self.firm_a, '1000.00', matter_type='divorce', earner='M Tladi')
        self._line(self.firm_a, '2500.50', matter_type='divorce', earner='M Tladi')
        self._line(self.firm_a, '400.00', matter_type='debt', earner='K Jere')
        self._line(self.firm_b, '750.25', matter_type='divorce', earner='L Ndlovu')

    _n = 0

    def _line(self, firm, amount, matter_type='other', earner='', source='firm'):
        import datetime as dt
        BonuLineDetailTests._n += 1
        inv = BonuInvoice.objects.create(
            firm=firm, invoice_number=f'INV-{BonuLineDetailTests._n:04d}',
            invoice_date=dt.date(2026, 7, 1))
        return BonuInvoiceLine.objects.create(
            invoice=inv, line_no=1, amount=D(amount), matter_type=matter_type,
            fee_earner=earner, matter_type_source=source,
            member_ref=f'BONU-{BonuLineDetailTests._n:04d}',
            service_date=dt.date(2026, 7, 2))

    def _get(self, **params):
        req = APIRequestFactory().get('/api/v1/bonu/lines/', params)
        force_authenticate(req, user=self.user)
        return invoice_lines(req)

    def _forensics(self, **params):
        req = APIRequestFactory().get('/api/v1/bonu/forensics/', params)
        force_authenticate(req, user=self.user)
        return forensics(req)

    # ── the reconciliation contract ─────────────────────────────────────────
    def test_the_detail_total_equals_the_consolidated_total(self):
        r = self._get()
        self.assertEqual(r.status_code, 200, r.data)
        self.assertEqual(D(r.data['reconciliation']['total_billed']), D('4650.75'))
        self.assertEqual(r.data['reconciliation']['total_lines'], 4)

    def test_it_reconciles_to_what_the_forensics_screen_shows_for_one_firm(self):
        """The whole point: the breakdown must add up to the figure it sits under."""
        f = self._forensics(firm='Alpha')
        consolidated = next(row['billed'] for row in f.data['by_firm']
                            if row['firm'] == 'Alpha Attorneys')
        d = self._get(firm='Alpha')
        self.assertEqual(D(str(consolidated)),
                         D(d.data['reconciliation']['total_billed']))
        self.assertTrue(d.data['reconciliation']['balances'])

    def test_it_reconciles_by_case_type_too(self):
        f = self._forensics(matter_type='divorce')
        consolidated = next(row['billed'] for row in f.data['by_matter_type']
                            if row['type'] == 'divorce')
        d = self._get(matter_type='divorce')
        self.assertEqual(D(str(consolidated)),
                         D(d.data['reconciliation']['total_billed']))

    def test_every_line_behind_the_figure_is_listed(self):
        d = self._get(firm='Alpha')
        self.assertEqual(len(d.data['lines']), 3)
        self.assertEqual(sum(D(l['amount']) for l in d.data['lines']), D('3900.50'))

    def test_a_lawyer_filter_narrows_the_same_way(self):
        d = self._get(lawyer='Tladi')
        self.assertEqual(d.data['reconciliation']['total_lines'], 2)
        self.assertEqual(D(d.data['reconciliation']['total_billed']), D('3500.50'))

    # ── a reconciliation screen may never drop rows quietly ─────────────────
    def test_truncation_is_declared_not_hidden(self):
        d = self._get(limit=2)
        rec = d.data['reconciliation']
        self.assertEqual(len(d.data['lines']), 2)
        self.assertTrue(rec['truncated'], 'a page that drops rows must say so')
        self.assertFalse(rec['balances'],
                         'and must not claim to balance while it is short')
        self.assertEqual(rec['total_lines'], 4,
                         'the total is over every matching row, not just this page')

    def test_the_second_page_continues_where_the_first_stopped(self):
        first = self._get(limit=2)
        second = self._get(limit=2, offset=2)
        ids = {l['id'] for l in first.data['lines']} | {l['id'] for l in second.data['lines']}
        self.assertEqual(len(ids), 4)
        self.assertFalse(second.data['reconciliation']['truncated'])

    # ── an AI guess must not read as a fact ────────────────────────────────
    def test_unconfirmed_classification_is_quantified(self):
        self._line(self.firm_a, '99.00', matter_type='divorce', source='ai')
        d = self._get(firm='Alpha')
        self.assertEqual(D(d.data['reconciliation']['unconfirmed_classification_billed']),
                         D('99.00'))
        guessed = [l for l in d.data['lines'] if not l['classification_confirmed']]
        self.assertEqual(len(guessed), 1)
        self.assertEqual(guessed[0]['matter_type_source'], 'ai')

    # ── the standing BONU rule ─────────────────────────────────────────────
    def test_only_the_scheme_reference_is_exposed_never_a_member_name(self):
        d = self._get()
        for l in d.data['lines']:
            self.assertTrue(l['member_ref'].startswith('BONU-'))
            self.assertNotIn('member_name', l)
            self.assertNotIn('omang', l)

    def test_somebody_with_no_bonu_access_is_refused(self):
        outsider = User.objects.create_user('outsider', email='out@example.invalid',
                                            password='x')
        UserProfile.objects.update_or_create(
            user=outsider, defaults={'title': UserProfile.Title.OPERATIONS,
                                     'is_active': True})
        req = APIRequestFactory().get('/api/v1/bonu/lines/')
        force_authenticate(req, user=User.objects.get(pk=outsider.pk))
        r = invoice_lines(req)
        self.assertIn(r.status_code, (403, 404),
                      'the detail must be gated exactly like the rest of BONU')


class FableFixesTests(BonuLineDetailTests):
    """Fable review 2026-08-11 — the two defects my first ten tests walked past."""

    def test_hours_come_through_for_an_hourly_line(self):
        """The model field is `units`. A getattr('hours') default shipped a column
        that was blank for every line, while the overview's by-lawyer table read
        `units` and showed real figures — two screens, same data, different answers.
        """
        import datetime as dt
        from bonu.models import BonuInvoice, BonuInvoiceLine
        inv = BonuInvoice.objects.create(firm=self.firm_a, invoice_number='INV-HRS',
                                         invoice_date=dt.date(2026, 7, 3))
        BonuInvoiceLine.objects.create(
            invoice=inv, line_no=1, amount=D('3000.00'), matter_type='civil',
            basis='hourly', units=D('2.50'), rate=D('1200.00'),
            fee_earner='H Ourly', matter_type_source='firm')
        d = self._get(lawyer='Ourly')
        self.assertEqual(len(d.data['lines']), 1)
        self.assertEqual(D(d.data['lines'][0]['hours']), D('2.50'),
                         'the hours column must carry the billed units')
        self.assertEqual(D(d.data['lines'][0]['rate']), D('1200.00'))

    def test_the_unconfirmed_figure_does_not_shrink_as_you_page(self):
        """It sits beside whole-set totals, so it must have whole-set scope.

        Summed over the current page it understated the moment anyone paged — a
        silent partial on the one screen built to forbid silent partials.
        """
        for _ in range(3):
            self._line(self.firm_a, '100.00', matter_type='divorce', source='ai')
        full = self._get(firm='Alpha')
        paged = self._get(firm='Alpha', limit=1)
        self.assertEqual(
            D(full.data['reconciliation']['unconfirmed_classification_billed']),
            D('300.00'))
        self.assertEqual(
            D(paged.data['reconciliation']['unconfirmed_classification_billed']),
            D('300.00'),
            'the unconfirmed total must not depend on the page size')

    def test_the_case_types_come_from_the_model_not_a_copy(self):
        d = self._get()
        values = {t['value'] for t in d.data['matter_types']}
        from bonu.models import BonuInvoiceLine as L
        self.assertEqual(values, {v for v, _ in L.MatterType.choices})

    def test_the_forensics_screen_declares_when_its_own_read_is_capped(self):
        f = self._forensics()
        self.assertIn('lines_truncated', f.data)
        self.assertIn('lines_total', f.data)
        self.assertFalse(f.data['lines_truncated'],
                         'well under the cap with this fixture')
        self.assertEqual(f.data['lines_total'], 4)
