"""
bonu/test_ledger_import.py — reading the firm and the reference out of the books.

The two things that must not go wrong: a member's name must never end up stored anywhere,
and the same firm under two spellings must not become two firms (that would split the league
table and hide the very duplicates this is for).
"""
import datetime as dt
from decimal import Decimal

from django.test import SimpleTestCase, TestCase

from bonu.ledger_import import _firm_key, _reference, collect

D = Decimal
# A realistic entry description: Odoo bill ref, invoice number, and — the problem — the
# member's name. The name here is invented for the test.
DESC = 'Odoo BILL/2025/07/0005 — INVOICE NO: 5131 - / ALPHA DIRECT INSURANCE COMPANY (PTY) LTD / TEST MEMBER NAME'


class Entry:
    def __init__(self, notes, description, date, number='JE-1'):
        self.notes, self.description, self.entry_date, self.entry_number = (
            notes, description, date, number)


class Line:
    def __init__(self, entry, debit=0, credit=0):
        self.journal_entry, self.debit_bwp, self.credit_bwp = entry, D(str(debit)), D(str(credit))


class ReferenceTests(SimpleTestCase):

    def test_it_reads_the_invoice_number_and_the_bill_reference(self):
        inv, bill = _reference(DESC)
        self.assertEqual(inv, '5131')
        self.assertEqual(bill, 'BILL/2025/07/0005')

    def test_it_falls_back_to_the_bill_reference_when_there_is_no_invoice_number(self):
        inv, bill = _reference('Odoo BILL/2025/07/0001 — / ALPHA DIRECT / SOMEBODY')
        self.assertEqual(inv, '')
        self.assertEqual(bill, 'BILL/2025/07/0001')

    def test_nothing_readable_returns_nothing_invented(self):
        self.assertEqual(_reference('opening balance'), ('', ''))


class FirmMatchingTests(SimpleTestCase):

    def test_the_same_firm_in_two_spellings_is_one_firm(self):
        self.assertEqual(_firm_key('JEREMIAH TLADI & COMPANY'),
                         _firm_key('Jeremiah Tladi and Company'))

    def test_suffixes_do_not_split_a_firm(self):
        self.assertEqual(_firm_key('Kubanga Attorneys'), _firm_key('KUBANGA ATTORNEYS (PTY) LTD'))

    def test_two_genuinely_different_firms_stay_apart(self):
        self.assertNotEqual(_firm_key('Jere Attorneys'), _firm_key('Jeremiah Tladi & Company'))


class CollectTests(SimpleTestCase):

    def test_lines_on_one_reference_roll_into_one_invoice(self):
        e = Entry('KUBANGA ATTORNEYS', DESC, dt.date(2026, 3, 1))
        buckets, stats = collect([Line(e, 4500), Line(e, 1500)])
        self.assertEqual(len(buckets), 1)
        b = list(buckets.values())[0]
        self.assertEqual(b['amount'], D('6000'))
        self.assertEqual(b['ledger_lines'], 2)

    def test_a_credit_reduces_the_invoice_rather_than_being_ignored(self):
        e = Entry('KUBANGA ATTORNEYS', DESC, dt.date(2026, 3, 1))
        buckets, _ = collect([Line(e, 4500), Line(e, credit=500)])
        self.assertEqual(list(buckets.values())[0]['amount'], D('4000'))

    def test_an_entry_with_no_firm_is_counted_not_guessed(self):
        e = Entry('', DESC, dt.date(2026, 3, 1))
        buckets, stats = collect([Line(e, 1000)])
        self.assertEqual(len(buckets), 0)
        self.assertEqual(stats['no_firm'], 1)

    def test_an_entry_with_no_reference_is_counted_not_guessed(self):
        e = Entry('KUBANGA ATTORNEYS', 'BONU Clamis', dt.date(2026, 3, 1))
        buckets, stats = collect([Line(e, 1000)])
        self.assertEqual(len(buckets), 0)
        self.assertEqual(stats['no_reference'], 1)

    def test_one_reference_on_two_dates_is_flagged_for_a_question(self):
        d1 = Entry('KUBANGA ATTORNEYS', DESC, dt.date(2026, 3, 1))
        d2 = Entry('KUBANGA ATTORNEYS', DESC, dt.date(2026, 4, 9))
        buckets, _ = collect([Line(d1, 4500), Line(d2, 4500)])
        self.assertTrue(list(buckets.values())[0]['dates_differ'])

    def test_two_firms_with_the_same_invoice_number_stay_separate(self):
        a = Entry('KUBANGA ATTORNEYS', DESC, dt.date(2026, 3, 1))
        b = Entry('Jere Attorneys', DESC, dt.date(2026, 3, 1))
        buckets, _ = collect([Line(a, 1000), Line(b, 2000)])
        self.assertEqual(len(buckets), 2)


class LoadTests(TestCase):
    """The write path, including the one thing that must never happen."""

    def _entry(self, acc, number, date, description, notes, amount):
        """A posted journal entry. This model requires a creator, a company and a currency
        (same fixture shape the supplier-recon tests use) — the importer itself only reads."""
        from ledger.models import JournalEntry, JournalEntryLine
        e = JournalEntry.objects.create(
            entry_number=number, entry_date=date, description=description, notes=notes,
            status='posted', created_by=self.user, company=self.company,
            currency_code=self.bwp)
        JournalEntryLine.objects.create(journal_entry=e, account=acc,
                                        debit_bwp=D(str(amount)), credit_bwp=D('0'),
                                        description='BONU Clamis')
        return e

    def _ledger(self):
        from django.contrib.auth import get_user_model

        from core.models import Company
        from ledger.models import Account, Currency
        # Migrations seed currencies and may seed companies — never assume an empty database.
        self.bwp, _ = Currency.objects.get_or_create(
            code='BWP', defaults={'name': 'Pula', 'symbol': 'P'})
        self.company, _ = Company.objects.get_or_create(
            code='BONUT', defaults={'name': 'BONU Import Test Entity',
                                    'base_currency': self.bwp})
        self.user = get_user_model().objects.create_user('bonu-import-test', password='x')
        acc, _ = Account.objects.get_or_create(
            code='103014', defaults={'name': 'BONU Claims', 'account_type': 'expense',
                                     'currency_code': self.bwp})
        self._entry(acc, 'JE-TEST-1', dt.date(2026, 3, 1), DESC, 'KUBANGA ATTORNEYS', 4500)
        return acc

    def test_a_dry_run_writes_nothing(self):
        from bonu.ledger_import import load
        from bonu.models import BonuInvoice, LawFirm
        self._ledger()
        r = load(dry_run=True)
        self.assertTrue(r['ok'])
        self.assertEqual(r['invoices_seen'], 1)
        self.assertEqual(LawFirm.objects.count(), 0)
        self.assertEqual(BonuInvoice.objects.count(), 0)

    def test_a_dry_run_never_reads_a_bonu_table_either(self):
        # It has to work on a system where the BONU tables do not exist yet — that is
        # precisely when you want to see what a load would do. Proven by making any query
        # against LawFirm explode and checking the dry run still completes.
        from unittest.mock import patch

        from bonu.ledger_import import load
        self._ledger()
        with patch('bonu.models.LawFirm.objects.all',
                   side_effect=AssertionError('a dry run must not touch bonu tables')):
            r = load(dry_run=True)
        self.assertEqual(r['invoices_seen'], 1)

    def test_commit_creates_the_firm_and_the_invoice(self):
        from bonu.ledger_import import load
        from bonu.models import BonuInvoice, BonuInvoiceLine, LawFirm
        self._ledger()
        r = load(dry_run=False)
        self.assertEqual(r['invoices_created'], 1)
        firm = LawFirm.objects.get()
        self.assertEqual(firm.name, 'KUBANGA ATTORNEYS')
        self.assertIsNone(firm.agreed_hourly_rate)      # never invent a tariff
        inv = BonuInvoice.objects.get()
        self.assertEqual(inv.invoice_number, '5131')
        self.assertEqual(inv.total, D('4500'))
        self.assertEqual(BonuInvoiceLine.objects.count(), 1)

    def test_the_members_name_is_never_stored(self):
        from bonu.ledger_import import load
        from bonu.models import BonuInvoice, BonuInvoiceLine
        self._ledger()
        load(dry_run=False)
        line = BonuInvoiceLine.objects.get()
        inv = BonuInvoice.objects.get()
        for field in (line.member_ref, line.matter_description, line.matter_ref,
                      inv.review_note, inv.source_file):
            self.assertNotIn('TEST MEMBER NAME', field or '')

    def test_the_case_type_is_marked_as_never_classified(self):
        from bonu.ledger_import import load
        from bonu.models import BonuInvoiceLine
        self._ledger()
        load(dry_run=False)
        line = BonuInvoiceLine.objects.get()
        # The ledger has no case type. Saying "other, never classified" is the honest answer,
        # and the panel screen reports how much spend rests on it.
        self.assertEqual(line.matter_type, 'other')
        self.assertEqual(line.matter_type_source, 'default')

    def test_running_it_twice_does_not_duplicate_the_invoice(self):
        from bonu.ledger_import import load
        from bonu.models import BonuInvoice, LawFirm
        self._ledger()
        load(dry_run=False)
        r2 = load(dry_run=False)
        self.assertEqual(BonuInvoice.objects.count(), 1)
        self.assertEqual(LawFirm.objects.count(), 1)
        self.assertEqual(r2['invoices_skipped_existing'], 1)
        self.assertEqual(r2['invoices_created'], 0)

    def test_a_second_spelling_of_the_firm_does_not_create_a_second_firm(self):
        from bonu.ledger_import import load
        from bonu.models import LawFirm
        acc = self._ledger()
        self._entry(acc, 'JE-TEST-2', dt.date(2026, 4, 1),
                    'Odoo BILL/2025/08/0009 — INVOICE NO: 5140 - / X',
                    'Kubanga Attorneys (Pty) Ltd', 900)
        load(dry_run=False)
        self.assertEqual(LawFirm.objects.count(), 1)
