"""taskboard/test_new_payee_control.py — PAY-BANK-03, the new-payee control.

CFO directive 2026-09-01, alongside wiring invoice OCR into the payment-request
form. PAY-BANK-01 (2026-08-20) challenges a CHANGED supplier bank account, but
it can only fire when there is a previous account to differ from:

    known = last_known_bank(payee)
    if known is None:
        return None          # <-- a brand-new payee was never challenged

So a fabricated supplier with a fabricated account had nothing standing in its
way. Reading the account straight off the invoice widens that hole, because now
nobody is even forced to look at the digits. PAY-BANK-03 closes it: a payee we
have never paid needs an explicit confirmation that the raiser checked the
number against the invoice and verified the supplier independently.

These are the rules that must never regress:
  - a first-ever payee WITHOUT the confirmation is refused (409, PAY-BANK-03)
    and no request is created;
  - the same request WITH the confirmation is created;
  - a payee we have paid before does NOT trigger it (PAY-BANK-01 owns that);
  - the string "false" does NOT count as a confirmation (a plain bool() would
    have waved it through — every non-empty string is truthy in Python);
  - the confirmation is recorded on the request, so "who waved this through"
    is answerable later.

Run: manage.py test taskboard.test_new_payee_control
"""
from datetime import datetime
from unittest import mock
from zoneinfo import ZoneInfo

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from taskboard.models import PaymentRequest
from taskboard.payee_bank_history import first_payment_warning
from taskboard.test_helpers import seed_adic

NEW_ACCT = '62099887766'
GABS = ZoneInfo('Africa/Gaborone')


def inside_the_load_window():
    """Hold the clock inside the 08:00-09:15 loading window (PAY-WIN-02).

    Without this the create call is refused with 403 PAY-WIN-02 before it ever
    reaches the control under test, and these tests would pass or fail purely
    on what time of day CI happened to run. Same patch point the PAY-WIN-02
    suite itself uses.
    """
    return mock.patch('taskboard.payment_views.timezone.localtime',
                      return_value=datetime(2026, 9, 1, 8, 30, tzinfo=GABS))


class NewPayeeControlTests(TestCase):
    """A payee Omni has never paid must be confirmed before it can be raised."""

    def setUp(self):
        self.me = User.objects.create_user('btendani', password='x')
        # A finance approver must exist or the create path fails before the gate.
        User.objects.create_user('kago', email='ktshutlhedi@alphadirect.co.bw',
                                 password='x', first_name='Kago')
        self.client.force_login(self.me)
        self.url = reverse('v1-payment-requests')
        seed_adic()

    def post(self, **over):
        body = {
            'subject': 'Brand new supplier, first invoice',
            'category': PaymentRequest.Category.OTHER,
            'line_items': [{'description': 'Consulting', 'amount': '4500.00'}],
            'payee': 'Nonesuch Trading',
            'account_name': 'Nonesuch Trading',
            'bank_name': 'FNB',
            'account_number': NEW_ACCT,
        }
        body.update(over)
        with inside_the_load_window():
            return self.client.post(self.url, body,
                                    content_type='application/json')

    # ── the gate itself ──────────────────────────────────────────────────────
    def test_first_payment_to_a_new_payee_goes_to_the_committee_not_refused(self):
        """No blocker (CFO 2026-09-09): a first-ever payee is entered and
        routed to the committee as an exception — never refused at creation."""
        r = self.post()
        self.assertEqual(r.status_code, 201, r.content)
        self.assertEqual(r.json()['status'], PaymentRequest.Status.EXCEPTION)
        self.assertEqual(r.json()['exception']['control'], 'PAY-BANK-03')
        self.assertEqual(PaymentRequest.objects.get().status,
                         PaymentRequest.Status.EXCEPTION)

    def test_confirmed_first_payment_is_created(self):
        r = self.post(new_payee_confirmed=True)
        self.assertEqual(r.status_code, 201, r.content)
        self.assertEqual(PaymentRequest.objects.count(), 1)

    def test_confirmation_is_recorded_on_the_request(self):
        """Ticking a box that leaves no trace is not a control."""
        self.post(new_payee_confirmed=True)
        pr = PaymentRequest.objects.get()
        self.assertIn('PAY-BANK-03', pr.bank_change_reason)

    # ── the truthiness trap ──────────────────────────────────────────────────
    def test_the_string_false_is_not_treated_as_a_confirmation(self):
        """bool("false") is True in Python. A naive check would treat this as
        confirmed and skip the committee routing entirely."""
        r = self.post(new_payee_confirmed='false')
        self.assertEqual(r.status_code, 201, r.content)
        self.assertEqual(r.json()['status'], PaymentRequest.Status.EXCEPTION)
        self.assertEqual(r.json()['exception']['control'], 'PAY-BANK-03')

    def test_an_explicit_affirmative_string_is_accepted(self):
        r = self.post(new_payee_confirmed='true')
        self.assertEqual(r.status_code, 201, r.content)

    # ── it must not fire on payees we already know ───────────────────────────
    def test_a_known_payee_does_not_trigger_the_new_payee_control(self):
        """Paying the SAME payee into the SAME account a second time is
        ordinary business — PAY-BANK-01 owns any change from here."""
        self.assertEqual(self.post(new_payee_confirmed=True).status_code, 201)
        # A DIFFERENT invoice — same payee, same account. Repeating the exact
        # line would (correctly) be stopped by the duplicate control PAY-DUP-01
        # and would prove nothing about this one.
        r = self.post(subject='Second invoice, same supplier',
                      line_items=[{'description': 'Consulting - October',
                                   'amount': '7800.00', 'ref': 'INV-1002'}])
        self.assertEqual(r.status_code, 201, r.content)
        self.assertEqual(PaymentRequest.objects.count(), 2)

    # ── the helper's own edges ───────────────────────────────────────────────
    def test_no_account_number_means_no_new_payee_warning(self):
        """The control is about an unverified ACCOUNT. With no account typed
        there is nothing to confirm, and the mandatory-details gate
        (PAY-BANK-02) is the one that should speak."""
        self.assertIsNone(first_payment_warning('Nonesuch Trading', ''))

    def test_warning_is_returned_for_an_unknown_payee(self):
        w = first_payment_warning('Nobody Has Ever Paid This Name', NEW_ACCT)
        self.assertIsNotNone(w)
        self.assertEqual(w['control'], 'PAY-BANK-03')
        self.assertEqual(w['new_account_tail'], NEW_ACCT[-4:])
