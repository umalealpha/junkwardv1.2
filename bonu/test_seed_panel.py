"""
bonu/test_seed_panel.py — the seed command, tested because it writes to the live database.

Anything that will be run by hand against production gets a test first. Three things must
hold: it records what it was told, running it twice does not duplicate the agreement, and it
creates NO invoice, case or event — a control measuring invented work is worse than none.
"""
from decimal import Decimal

from django.core.management import CommandError, call_command
from django.test import TestCase

from bonu.models import (BonuInvoice, CaseEvent, LawFirm, LegalCase, RetainerAgreement)


class SeedPanelTests(TestCase):

    def seed(self, **kw):
        args = {'firm': 'Jeremiah & Taldi', 'fee': '85000', 'cases': 40, 'start': '2026-01-01'}
        args.update(kw)
        call_command('bonu_seed_panel', **args)

    def test_it_records_the_firm_and_the_retainer(self):
        self.seed()
        firm = LawFirm.objects.get(name='Jeremiah & Taldi')
        r = RetainerAgreement.objects.get(firm=firm)
        self.assertEqual(r.monthly_fee, Decimal('85000'))
        self.assertEqual(r.committed_cases, 40)
        # 85,000 / 40 = 2,125 — the price per case the CFO is actually paying.
        self.assertEqual(r.fee_per_committed_case, Decimal('2125.00'))

    def test_the_committed_count_carries_where_it_came_from(self):
        self.seed()
        r = RetainerAgreement.objects.get()
        self.assertIn('CFO instruction', r.scope_note)
        # Until the signed terms are on file the shortfall is a measurement, not a claim.
        self.assertIn('measurement, not a claim', r.scope_note)

    def test_running_it_twice_updates_rather_than_duplicates(self):
        self.seed()
        self.seed(cases=36)
        self.assertEqual(LawFirm.objects.count(), 1)
        self.assertEqual(RetainerAgreement.objects.count(), 1)
        self.assertEqual(RetainerAgreement.objects.get().committed_cases, 36)

    def test_it_invents_no_work(self):
        self.seed()
        self.assertEqual(BonuInvoice.objects.count(), 0)
        self.assertEqual(LegalCase.objects.count(), 0)
        self.assertEqual(CaseEvent.objects.count(), 0)

    def test_no_agreed_rate_is_left_blank_not_guessed(self):
        self.seed()
        self.assertIsNone(LawFirm.objects.get().agreed_hourly_rate)

    def test_an_agreed_rate_is_stored_when_given(self):
        self.seed(rate='1200')
        self.assertEqual(LawFirm.objects.get().agreed_hourly_rate, Decimal('1200'))

    def test_a_bad_date_is_refused(self):
        with self.assertRaises(CommandError):
            self.seed(start='next Monday')

    def test_a_bad_fee_is_refused(self):
        with self.assertRaises(CommandError):
            self.seed(fee='eighty five thousand')

    def test_zero_committed_cases_is_refused(self):
        # Dividing the fee by zero cases is how a scorecard starts printing nonsense.
        with self.assertRaises(CommandError):
            self.seed(cases=0)


class SeveralAgreementsWithOneFirmTests(TestCase):
    """One firm, two SLAs. Without a label the second run UPDATED the first agreement and
    silently lost a fee — which is what happened on prod on 2026-08-03."""

    def test_two_labelled_agreements_live_side_by_side(self):
        call_command('bonu_seed_panel', firm='JEREMIAH TLADI & COMPANY', fee='40000', cases=40,
                     start='2025-07-01', label='South')
        call_command('bonu_seed_panel', firm='JEREMIAH TLADI & COMPANY', fee='85000', cases=40,
                     start='2025-07-01', label='North')
        self.assertEqual(LawFirm.objects.count(), 1)
        self.assertEqual(RetainerAgreement.objects.count(), 2)
        self.assertEqual(sorted(r.monthly_fee for r in RetainerAgreement.objects.all()),
                         [Decimal('40000'), Decimal('85000')])

    def test_the_label_is_visible_in_the_agreement_name(self):
        call_command('bonu_seed_panel', firm='JEREMIAH TLADI & COMPANY', fee='40000', cases=40,
                     start='2025-07-01', label='South')
        self.assertIn('(South)', RetainerAgreement.objects.get().name)

    def test_re_running_the_same_label_still_updates_rather_than_duplicates(self):
        for fee in ('40000', '42000'):
            call_command('bonu_seed_panel', firm='JEREMIAH TLADI & COMPANY', fee=fee, cases=40,
                         start='2025-07-01', label='South')
        self.assertEqual(RetainerAgreement.objects.count(), 1)
        self.assertEqual(RetainerAgreement.objects.get().monthly_fee, Decimal('42000'))
