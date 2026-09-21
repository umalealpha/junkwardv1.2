"""
banking/test_line_references.py — CR-001: a bank statement line carries the
claim and the invoice it settled.

Two halves:
  * the parser (pure, no DB) — banking/line_references.py
  * the model wiring (needs a DB) — the fields are filled ON INSERT, from both
    import paths, and are NEVER rewritten by a later save.

That second half is the one worth having. The whole safety argument for CR-001
is "a deploy does not rewrite live financial rows" — and that argument is only
true if save() really does leave existing rows alone.

Run:  python manage.py test banking.test_line_references
"""
from __future__ import annotations

import unittest
from datetime import date
from decimal import Decimal

from django.test import TestCase

from banking.line_references import (
    PaymentBasis,
    extract_line_references,
)


class Parser(unittest.TestCase):
    """No database — the parser is pure."""

    def test_claim_number_is_read_out_of_the_narration(self):
        refs = extract_line_references('ALPHA DIRECT G2026004512 AOL', '')
        self.assertEqual(refs.claim_reference, 'G2026004512')

    def test_our_own_reference_is_read_as_well_as_the_description(self):
        refs = extract_line_references('', 'G2026004512 CIL')
        self.assertEqual(refs.claim_reference, 'G2026004512')
        self.assertEqual(refs.payment_basis, PaymentBasis.CIL)

    def test_claim_with_invoice_suffix_yields_both(self):
        refs = extract_line_references('ALPHA DIRECT G2026004567-4546', '')
        self.assertEqual(refs.claim_reference, 'G2026004567')
        self.assertEqual(refs.invoice_reference, '4546')
        self.assertEqual(refs.payment_basis, PaymentBasis.INVOICE)
        self.assertTrue(refs.settled_against_invoice)

    def test_supplier_invoice_without_a_claim(self):
        refs = extract_line_references('MOTOVAC INV45678', '')
        self.assertEqual(refs.claim_reference, '')
        self.assertEqual(refs.invoice_reference, '45678')
        self.assertEqual(refs.payment_basis, PaymentBasis.INVOICE)

    def test_the_claim_numbers_own_digits_are_not_an_invoice_number(self):
        refs = extract_line_references('ALPHA DIRECT G2026004512', '')
        self.assertEqual(refs.claim_reference, 'G2026004512')
        self.assertEqual(refs.invoice_reference, '')
        self.assertEqual(refs.payment_basis, PaymentBasis.UNKNOWN)

    # ── The narrations our OWN code writes ──────────────────────────────────
    # Fable 5.1, 13-Sep-2026: this parser claimed to read "the house format"
    # and was only ever tested on hand-typed samples. The module that WRITES
    # the narration is taskboard/narration_templates.py, and its repair format
    # — the commonest claims payment Omni raises — parsed to UNKNOWN with no
    # invoice number, which left it gross AND on the individual-claimant side
    # of the split. A parser of house-generated text must be fed the
    # generator's real output, not the author's idea of it.

    def test_the_house_repair_narration_is_read(self):
        """taskboard/narration_templates.py:194 writes exactly this pair."""
        refs = extract_line_references('ALPHA DIRECT G2026004782 3522',
                                       'G2026004782 REPAIR')
        self.assertEqual(refs.claim_reference, 'G2026004782')
        self.assertEqual(refs.invoice_reference, '3522')
        self.assertEqual(refs.payment_basis, PaymentBasis.REPAIR)
        self.assertTrue(refs.settled_against_invoice)

    def test_a_repair_is_not_degrossed_without_finance_saying_so(self):
        """A repair settles an invoice, so it is on the supplier side of the
        split — but the instruction de-grosses INVOICE and only INVOICE, and
        doubt resolves to leaving the money gross."""
        self.assertNotIn(PaymentBasis.REPAIR, PaymentBasis.DEGROSSED)

    def test_the_other_house_claim_codes_are_read(self):
        for text, basis in (
            ('ALPHA DIRECT G2026004512 THIRD PARTY', PaymentBasis.THIRD_PARTY),
            ('ALPHA DIRECT G2026004512 EX-GRATIA', PaymentBasis.EX_GRATIA),
        ):
            with self.subTest(text=text):
                refs = extract_line_references(text, '')
                self.assertEqual(refs.payment_basis, basis)
                self.assertEqual(refs.invoice_reference, '')
                self.assertNotIn(basis, PaymentBasis.DEGROSSED)

    def test_a_bank_date_stamp_is_not_an_invoice_number(self):
        """The one that would have re-created the 11% over-claim. FNB puts its
        own numeric references and date stamps in the description; reading one
        as an invoice number makes the line INVOICE basis and de-grosses it."""
        refs = extract_line_references('ALPHA DIRECT G2026004512 20260715', '')
        self.assertEqual(refs.invoice_reference, '')
        self.assertEqual(refs.payment_basis, PaymentBasis.UNKNOWN)
        self.assertNotIn(refs.payment_basis, PaymentBasis.DEGROSSED)

    def test_a_long_bank_reference_is_not_an_invoice_number(self):
        refs = extract_line_references('ALPHA DIRECT G2026004512 REF 1234567890', '')
        self.assertEqual(refs.invoice_reference, '')
        self.assertEqual(refs.payment_basis, PaymentBasis.UNKNOWN)

    def test_the_word_invoice_is_not_itself_an_invoice_number(self):
        """'INVOICE 45678' used to yield the invoice number 'OICE'."""
        refs = extract_line_references('INVOICE 45678 MOTOVAC', '')
        self.assertEqual(refs.invoice_reference, '45678')

    def test_lowercase_prose_for_does_not_eat_a_real_invoice(self):
        """Finance writes the codes in capitals. Matching case-insensitively
        turned 'for windscreen' into basis FOR, which both mislabelled the
        payment and threw away the invoice number sitting next to it."""
        refs = extract_line_references(
            'Payment G2026004512 for windscreen INV45678', '')
        self.assertEqual(refs.payment_basis, PaymentBasis.INVOICE)
        self.assertEqual(refs.invoice_reference, '45678')

    def test_blank_text_is_unknown_not_a_crash(self):
        for a, b in ((None, None), ('', ''), ('   ', None)):
            with self.subTest(a=a, b=b):
                refs = extract_line_references(a, b)
                self.assertEqual(refs.payment_basis, PaymentBasis.UNKNOWN)
                self.assertEqual(refs.claim_reference, '')

    def test_only_invoice_is_in_the_degross_set(self):
        """The VAT rule's whole surface area, asserted directly."""
        self.assertEqual(PaymentBasis.DEGROSSED, frozenset({PaymentBasis.INVOICE}))
        for basis in (PaymentBasis.AOL, PaymentBasis.FOR, PaymentBasis.CIL,
                      PaymentBasis.UNKNOWN):
            self.assertNotIn(basis, PaymentBasis.DEGROSSED)


class ModelWiring(TestCase):
    """The fields fill themselves on insert, and never on update."""

    def setUp(self):
        from banking.models import BankAccount, BankStatement
        from ledger.models import Account

        self.gl = Account.objects.create(
            code='100100', name='FNB Current', account_type='asset',
            is_bank_account=True,
        )
        self.acct = BankAccount.objects.create(
            gl_account=self.gl, bank_name='FNB', account_name='ADIC',
            account_number='62000000001',
        )
        self.stmt = BankStatement.objects.create(
            bank_account=self.acct, statement_date=date(2026, 7, 31),
            opening_balance=Decimal('0.00'), closing_balance=Decimal('0.00'),
            file_name='july.csv',
        )

    def _line(self, n, description, reference=''):
        from banking.models import BankStatementLine
        return BankStatementLine.objects.create(
            statement=self.stmt, line_number=n,
            transaction_date=date(2026, 7, 15),
            description=description, reference=reference,
            amount=Decimal('-1140.00'),
        )

    def test_a_new_line_fills_its_own_references(self):
        line = self._line(1, 'ALPHA DIRECT G2026004512 AOL')
        self.assertEqual(line.claim_reference, 'G2026004512')
        self.assertEqual(line.payment_basis, PaymentBasis.AOL)
        self.assertEqual(line.invoice_reference, '')

    def test_an_existing_line_is_never_rewritten_by_a_later_save(self):
        """The safety promise of CR-001, tested rather than asserted.

        A line whose references were corrected by hand — or deliberately left
        blank — must survive any later save of that row. If save() re-derived
        every time, a deploy touching these rows for any other reason would
        quietly rewrite financial references, which is exactly what the
        backfill command exists to keep as a human decision.
        """
        line = self._line(2, 'ALPHA DIRECT G2026004512 AOL')
        line.claim_reference = 'G9999999999'
        line.payment_basis = PaymentBasis.CIL
        line.save()
        line.refresh_from_db()
        self.assertEqual(line.claim_reference, 'G9999999999')
        self.assertEqual(line.payment_basis, PaymentBasis.CIL)

        # And a blank stays blank.
        line.claim_reference = ''
        line.invoice_reference = ''
        line.payment_basis = PaymentBasis.UNKNOWN
        line.save()
        line.refresh_from_db()
        self.assertEqual(line.claim_reference, '')
        self.assertEqual(line.payment_basis, PaymentBasis.UNKNOWN)

    def test_an_explicit_value_on_create_wins_over_the_parser(self):
        from banking.models import BankStatementLine
        line = BankStatementLine.objects.create(
            statement=self.stmt, line_number=3,
            transaction_date=date(2026, 7, 15),
            description='ALPHA DIRECT G2026004512 AOL', reference='',
            amount=Decimal('-1140.00'),
            claim_reference='G2026000001',
        )
        self.assertEqual(line.claim_reference, 'G2026000001')

    def test_the_dedupe_key_is_unchanged_by_the_new_fields(self):
        """The key fingerprints what the BANK sent. Folding a derived field
        into it would change every future key the moment the parser improves,
        breaking the no-double-import guard."""
        from banking.models import compute_line_dedupe_key
        line = self._line(4, 'ALPHA DIRECT G2026004512 AOL', 'G2026004512 AOL')
        self.assertEqual(line.dedupe_key, compute_line_dedupe_key(
            self.acct.id, date(2026, 7, 15), Decimal('-1140.00'),
            'ALPHA DIRECT G2026004512 AOL', 'G2026004512 AOL',
        ))


class BackfillCommand(TestCase):
    def setUp(self):
        from banking.models import BankAccount, BankStatement, BankStatementLine
        from ledger.models import Account

        gl = Account.objects.create(
            code='100200', name='FNB Two', account_type='asset',
            is_bank_account=True,
        )
        acct = BankAccount.objects.create(
            gl_account=gl, bank_name='FNB', account_name='ADIC',
            account_number='62000000002',
        )
        stmt = BankStatement.objects.create(
            bank_account=acct, statement_date=date(2026, 7, 31),
            opening_balance=Decimal('0.00'), closing_balance=Decimal('0.00'),
            file_name='july.csv',
        )
        self.line = BankStatementLine.objects.create(
            statement=stmt, line_number=1,
            transaction_date=date(2026, 7, 15),
            description='ALPHA DIRECT G2026004512 AOL', reference='',
            amount=Decimal('-1140.00'),
        )
        # Simulate a row imported BEFORE CR-001 existed: columns present,
        # nothing in them. Written with .update() so save() is bypassed
        # exactly as a schema migration's default would leave it.
        BankStatementLine.objects.filter(pk=self.line.pk).update(
            claim_reference='', invoice_reference='',
            payment_basis=PaymentBasis.UNKNOWN,
        )

    def _run(self, *args):
        from io import StringIO

        from django.core.management import call_command
        out = StringIO()
        call_command('backfill_bank_line_references', *args, stdout=out)
        return out.getvalue()

    def test_dry_run_writes_nothing(self):
        out = self._run()
        self.assertIn('DRY RUN', out)
        self.line.refresh_from_db()
        self.assertEqual(self.line.claim_reference, '')

    def test_commit_fills_the_row(self):
        self._run('--commit')
        self.line.refresh_from_db()
        self.assertEqual(self.line.claim_reference, 'G2026004512')
        self.assertEqual(self.line.payment_basis, PaymentBasis.AOL)

    def test_it_is_re_runnable(self):
        self._run('--commit')
        out = self._run('--commit')
        self.assertIn('Lines that differ: 0', out)
