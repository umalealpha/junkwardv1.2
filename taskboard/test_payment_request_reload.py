"""taskboard/test_payment_request_reload.py — "reload previous payments".

Leano Makwapa, Omni feature request 2026-09-15 (61 words): *"I would like for
the payment request option to reload previous supplier payments which had been
previously loaded, have an option to edit amounts and documents attached for
every supplier a payment request has been previously uploaded before but not
erasing the previous payment, just adding to the list of payments made to that
supplier for also recon purposes."*

What he is asking for is, mechanically, the action behind the fifteen items paid
twice in August — Manus, 2026-08-10: *"it reads as one person re-loading the
previous day's batch rather than checking what had already cleared."* So the
feature is built the other way round: it copies forward what genuinely repeats
and refuses to carry the figures that do not, and the copy leaves a stamp that
gives the duplicate sweep a HARD fact where it previously had to infer a repeat
from a matching amount.

These are the rules that must never regress:
  - ?copy=1 is opt-in; the plain history screen carries no copy payload;
  - the copy NEVER carries the amount, the invoice number or either date — a
    supplier paid monthly is paid on a new invoice for a new figure;
  - it DOES carry the supplier narration, GL code, agreed terms, claim number
    and POP recipient — that is where the typing actually was;
  - a cancelled line is shown but is not copyable;
  - a reloaded line whose source line is already PAID or CLEARED is a HARD
    duplicate clash, by reading the source rather than guessing;
  - no stamp, no false clash — an ordinary line is never flagged by this rule.

No real payee or staff names — fake suppliers only.

Run: manage.py test taskboard.test_payment_request_reload
"""
from datetime import date
from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from django.urls import reverse

from taskboard.models import PaymentRequest
from taskboard.payment_views import _clean_lines, _lines_copied_from_settled


def _line(desc, amount, invoice='', **extra):
    row = {'description': desc, 'amount': amount, 'invoice_number': invoice,
           'claim_number': '', 'gl_code': '', 'ref': ''}
    row.update(extra)
    return row


@override_settings(PAYMENT_FIRST_APPROVER_EMAILS=['approver@example.test'])
class CopyForwardPayloadTests(TestCase):
    """What the form is handed when the raiser asks to reload a supplier."""

    def setUp(self):
        self.url = reverse('v1-payment-history')
        # A fake address. A real staff address in a fixture is what tripped
        # the PII tripwire and stopped the external review panel from running
        # at all (Fable 5.1, 2026-09-15) — and this file's own header already
        # said no real staff names.
        self.approver = User.objects.create_user(
            'approver', email='approver@example.test', password='x')
        self.paid = PaymentRequest.objects.create(
            ref='PAY/ADIC/2026/08/10/0001', subject='Reload fixture',
            entity='Alpha Direct Insurance Company', currency='BWP',
            category=PaymentRequest.Category.SUPPLIER,
            payee='Nonesuch Trading', total='6556.00',
            status=PaymentRequest.Status.PAID, payment_date=date(2026, 8, 10),
            created_by=self.approver,
            line_items=[_line('Courier run July', '6556.00', 'IN102985',
                              gl_code='510300', terms_days='30',
                              terms_basis='invoice',
                              pop_recipient_name='Nonesuch Accounts',
                              pop_recipient_email='ap@nonesuch.example')])
        self.cancelled = PaymentRequest.objects.create(
            ref='PAY/ADIC/2026/08/11/0002', subject='Pulled fixture',
            entity='Alpha Direct Insurance Company', currency='BWP',
            category=PaymentRequest.Category.SUPPLIER,
            payee='Nonesuch Trading', total='100.00',
            status=PaymentRequest.Status.PAID, payment_date=date(2026, 8, 11),
            created_by=self.approver,
            line_items=[_line('Pulled line', '100.00', 'IN999', cancelled=True)])

    def _rows(self, **params):
        self.client.force_login(self.approver)
        r = self.client.get(self.url, params)
        self.assertEqual(r.status_code, 200, r.content)
        return r.json()['rows']

    def test_plain_history_carries_no_copy_payload(self):
        """The history screen must not grow a payload it does not use."""
        for row in self._rows(**{'from': '2026-08-01', 'to': '2026-08-31'}):
            self.assertNotIn('copy', row)
            self.assertNotIn('copyable', row)

    def test_copy_mode_omits_amount_invoice_and_dates(self):
        """The paid-twice shape is exactly what must NOT come across."""
        row = next(r for r in self._rows(copy='1', **{'from': '2026-08-01',
                                                      'to': '2026-08-31'})
                   if r['invoice_number'] == 'IN102985')
        copy = row['copy']
        for forbidden in ('amount', 'invoice_number', 'invoice_date', 'due_date'):
            self.assertNotIn(forbidden, copy,
                             f'{forbidden} must never be copied forward')

    def test_copy_mode_carries_what_genuinely_repeats(self):
        row = next(r for r in self._rows(copy='1', **{'from': '2026-08-01',
                                                      'to': '2026-08-31'})
                   if r['invoice_number'] == 'IN102985')
        copy = row['copy']
        self.assertEqual(copy['description'], 'Courier run July')
        self.assertEqual(copy['gl_code'], '510300')
        self.assertEqual(copy['terms_days'], '30')
        self.assertEqual(copy['pop_recipient_email'], 'ap@nonesuch.example')
        # The stamp that makes the settled-source control possible at all.
        self.assertEqual(copy['copied_from_ref'], 'PAY/ADIC/2026/08/10/0001')

    def test_a_paid_line_is_still_copyable(self):
        """A supplier paid every month is PAID. Blocking that kills the feature."""
        row = next(r for r in self._rows(copy='1', **{'from': '2026-08-01',
                                                      'to': '2026-08-31'})
                   if r['invoice_number'] == 'IN102985')
        self.assertTrue(row['copyable'])

    def test_a_cancelled_line_is_shown_but_not_copyable(self):
        row = next(r for r in self._rows(copy='1', **{'from': '2026-08-01',
                                                      'to': '2026-08-31'})
                   if r['invoice_number'] == 'IN999')
        self.assertTrue(row['cancelled'])
        self.assertFalse(row['copyable'])


class ReloadedSettledLineIsAHardClashTests(TestCase):
    """The control the reload button pays for."""

    def setUp(self):
        self.user = User.objects.create_user('raiser', password='x')
        self.paid = PaymentRequest.objects.create(
            ref='PAY/ADIC/2026/08/10/0001', subject='Settled',
            entity='Alpha Direct Insurance Company', currency='BWP',
            category=PaymentRequest.Category.SUPPLIER,
            payee='Nonesuch Trading', total='6556.00',
            status=PaymentRequest.Status.PAID, created_by=self.user,
            line_items=[_line('Courier run July', '6556.00', 'IN102985')])
        self.pending = PaymentRequest.objects.create(
            ref='PAY/ADIC/2026/09/01/0003', subject='Not yet settled',
            entity='Alpha Direct Insurance Company', currency='BWP',
            category=PaymentRequest.Category.SUPPLIER,
            payee='Nonesuch Trading', total='250.00',
            status=PaymentRequest.Status.PENDING_FINANCE, created_by=self.user,
            line_items=[_line('Waiting line', '250.00', 'IN555')])

    def test_reloading_an_already_paid_invoice_is_a_hard_clash(self):
        lines = [_line('Courier run July', Decimal('6556.00'), 'IN102985',
                       copied_from_ref='PAY/ADIC/2026/08/10/0001')]
        clashes = _lines_copied_from_settled(lines, currency='BWP')
        self.assertEqual(len(clashes), 1, clashes)
        self.assertEqual(clashes[0]['clash_kind'], 'copied_from_settled')
        self.assertEqual(clashes[0]['clash_ref'], 'PAY/ADIC/2026/08/10/0001')
        self.assertIn('settled once', clashes[0]['detail'])

    def test_a_new_invoice_off_the_same_supplier_is_free_to_pay(self):
        """The whole point: next month's invoice must go through."""
        lines = [_line('Courier run August', Decimal('7000.00'), 'IN103512',
                       copied_from_ref='PAY/ADIC/2026/08/10/0001')]
        self.assertEqual(_lines_copied_from_settled(lines, currency='BWP'), [])

    def test_a_line_copied_off_a_pack_still_awaiting_signoff_is_not_flagged(self):
        """Not settled is not paid — this rule speaks only to settled lines."""
        lines = [_line('Waiting line', Decimal('250.00'), 'IN555',
                       copied_from_ref='PAY/ADIC/2026/09/01/0003')]
        self.assertEqual(_lines_copied_from_settled(lines, currency='BWP'), [])

    def test_a_settled_line_reloaded_with_the_invoice_box_left_empty_is_caught(self):
        """The amount fallback must cover a blank invoice on EITHER side.

        The first cut only fell back to the amount when the SOURCE carried no
        invoice, so a raiser who reloaded a settled line and cleared the invoice
        box walked past this rule (Fable 5.1, 2026-09-15).
        """
        lines = [_line('Courier run July', Decimal('6556.00'), '',
                       copied_from_ref='PAY/ADIC/2026/08/10/0001')]
        clashes = _lines_copied_from_settled(lines, currency='BWP')
        self.assertEqual(len(clashes), 1, clashes)
        self.assertEqual(clashes[0]['clash_ref'], 'PAY/ADIC/2026/08/10/0001')

    def test_a_new_invoice_passes_even_when_the_settled_source_had_none(self):
        """The prod shape. Most real lines carry NO invoice number.

        Caught by running the guard against live data on 2026-09-15: the source
        line on PAY/ADIC/2026/09/12/0009 has invoice_number '', so a fallback
        keyed on EITHER side being blank clashed with every next-month invoice at
        a repeated amount. The local fixtures all had an invoice number on the
        source and were blind to it. A hard clash never refuses a payment, but it
        does send it to the committee — a control that cries wolf gets ignored.
        """
        blank = PaymentRequest.objects.create(
            ref='PAY/ADIC/2026/08/20/0009', subject='Settled, no invoice number',
            entity='Alpha Direct Insurance Company', currency='BWP',
            category=PaymentRequest.Category.SUPPLIER,
            payee='Nonesuch Trading', total='1129.67',
            status=PaymentRequest.Status.PAID, created_by=self.user,
            line_items=[_line('Monthly retainer', '1129.67', '')])

        # The raiser typed a NEW invoice number: a different bill, must pass.
        fresh = [_line('Monthly retainer', Decimal('1129.67'), 'IN-NEW-0001',
                       copied_from_ref='PAY/ADIC/2026/08/20/0009')]
        self.assertEqual(_lines_copied_from_settled(fresh, currency='BWP'), [])

        # Left the invoice box empty on the same figure: that IS the mistake.
        empty = [_line('Monthly retainer', Decimal('1129.67'), '',
                       copied_from_ref='PAY/ADIC/2026/08/20/0009')]
        self.assertEqual(len(_lines_copied_from_settled(empty, currency='BWP')), 1)

    def test_an_ordinary_unstamped_line_is_never_flagged_by_this_rule(self):
        lines = [_line('Courier run July', Decimal('6556.00'), 'IN102985')]
        self.assertEqual(_lines_copied_from_settled(lines, currency='BWP'), [])

    def test_a_stamp_pointing_at_a_different_currency_does_not_match(self):
        lines = [_line('Courier run July', Decimal('6556.00'), 'IN102985',
                       copied_from_ref='PAY/ADIC/2026/08/10/0001')]
        self.assertEqual(_lines_copied_from_settled(lines, currency='ZAR'), [])

    def test_clean_lines_carries_the_stamp_through(self):
        out = _clean_lines([_line('Courier run July', '6556.00', 'IN102985',
                                  copied_from_ref='PAY/ADIC/2026/08/10/0001')])
        self.assertEqual(out[0]['copied_from_ref'], 'PAY/ADIC/2026/08/10/0001')

    def test_clean_lines_defaults_the_stamp_to_blank(self):
        out = _clean_lines([_line('Courier run July', '6556.00', 'IN102985')])
        self.assertEqual(out[0]['copied_from_ref'], '')
