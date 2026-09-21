"""taskboard/test_pop_recipient.py — the POP Recipient column, per line.

Finance spec 2026-09-08: "add a new column: POP Recipient. Populate its dropdown
from contacts already linked to that claim (claimant, supplier, broker) … with a
free-text fallback for name + email. Make it required per line … If the value
entered isn't one of the existing linked contacts, require a second approver to
clear it."

These are the rules that must never regress:
  - every stored line carries a POP recipient — a proof of payment never has
    nowhere to go. A blank falls back to Accounts, the standing default the POP
    email field has carried since CFO 2026-08-22, so a Drop Box draft read off
    an invoice is never dead-ended;
  - the recipient is PER LINE: one claim can pay a panel beater, a parts
    supplier and the claimant, and each proof belongs to whoever was paid;
  - a recipient on the list is stamped with WHERE it came from;
  - a recipient we do NOT hold is not refused — it is stamped off-list, and the
    finance approver (who can never be the raiser) must tick to clear it. No
    tick, no sign-off;
  - the source is derived on the server, never taken from the browser. A request
    that could label itself "linked" would walk past the gate;
  - a malformed email address is refused rather than silently stored;
  - it applies identically to a claims pack and an operations pack.

No real payee or staff names — fake suppliers and fake addresses only.

Run: manage.py test taskboard.test_pop_recipient
"""
from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from taskboard.models import PaymentRequest
from taskboard.pop_recipients import (
    SOURCE_ACCOUNTS, SOURCE_CONTACT, SOURCE_OFF_LIST,
    accounts_default, classify, linked_recipients, off_list_lines,
)
from taskboard.test_helpers import seed_adic

ACCT = '62077665544'
ON_FILE = 'ap@nonesuch-couriers.example'
OFF_LIST = 'someone.else@not-on-file.example'


def _contact(name, email, kind='vendor'):
    from billing.models import Contact
    from core.models import Currency
    Currency.objects.get_or_create(code='BWP', defaults={'name': 'Pula'})
    return Contact.objects.create(contact_type=kind, name=name, email=email,
                                  currency_code_id='BWP')


class PopRecipientLookupTests(TestCase):
    """The deterministic lookup behind the dropdown."""

    def setUp(self):
        seed_adic()

    def test_accounts_is_always_offered_as_the_standing_default(self):
        opts = linked_recipients()
        self.assertIn(accounts_default()['email'], [o['email'] for o in opts])

    def test_a_contact_matching_the_payee_is_offered(self):
        _contact('Nonesuch Couriers (Pty) Ltd', ON_FILE)
        opts = linked_recipients(payee='nonesuch couriers')
        self.assertIn(ON_FILE, [o['email'] for o in opts])

    def test_the_payee_is_matched_on_the_squashed_key_not_the_spelling(self):
        """'ABC Traders (Pty) Ltd.' and 'abc traders pty ltd' are one payee —
        the same normaliser the bank-history control uses, not a second one."""
        _contact('ABC Traders (Pty) Ltd.', 'ap@abc.example')
        opts = linked_recipients(payee='abc traders pty ltd')
        self.assertIn('ap@abc.example', [o['email'] for o in opts])

    def test_the_claimant_named_on_the_claim_is_offered_when_we_hold_an_address(self):
        from integrations.models import GraphiteClaim
        GraphiteClaim.objects.create(graphite_id=90001, claim_number='G2026009001',
                                     customer_name='Nonesuch Claimant')
        _contact('Nonesuch Claimant', 'claimant@nonesuch.example', kind='customer')
        opts = linked_recipients(claim_number='G2026009001')
        row = next(o for o in opts if o['email'] == 'claimant@nonesuch.example')
        self.assertEqual(row['source_label'], 'named on this claim')

    def test_the_same_address_reached_two_ways_is_offered_once(self):
        """One recipient listed three times reads as three choices."""
        from integrations.models import GraphiteClaim
        GraphiteClaim.objects.create(graphite_id=90002, claim_number='G2026009002',
                                     customer_name='Nonesuch Couriers')
        _contact('Nonesuch Couriers', ON_FILE)
        opts = linked_recipients(claim_number='G2026009002', payee='Nonesuch Couriers')
        self.assertEqual([o['email'] for o in opts].count(ON_FILE), 1)

    def test_a_contact_with_no_address_is_not_offered(self):
        _contact('Nonesuch Silent Ltd', '')
        opts = linked_recipients(payee='Nonesuch Silent Ltd')
        self.assertEqual([o['name'] for o in opts if o['name'] == 'Nonesuch Silent Ltd'], [])

    # ── classify ─────────────────────────────────────────────────────────────
    def test_an_address_on_the_list_classifies_to_its_source(self):
        _contact('Nonesuch Couriers', ON_FILE)
        opts = linked_recipients(payee='Nonesuch Couriers')
        self.assertIn(classify('Nonesuch Couriers', ON_FILE, opts),
                      (SOURCE_CONTACT, 'vendor_account'))

    def test_an_address_we_do_not_hold_classifies_off_list(self):
        opts = linked_recipients(payee='Nonesuch Couriers')
        self.assertEqual(classify('Whoever', OFF_LIST, opts), SOURCE_OFF_LIST)

    def test_matching_ignores_case_and_surrounding_space(self):
        _contact('Nonesuch Couriers', ON_FILE)
        opts = linked_recipients(payee='Nonesuch Couriers')
        self.assertNotEqual(classify('', f'  {ON_FILE.upper()} ', opts), SOURCE_OFF_LIST)

    def test_a_blank_address_is_off_list(self):
        self.assertEqual(classify('Somebody', '', linked_recipients()), SOURCE_OFF_LIST)

    def test_the_accounts_default_is_never_off_list(self):
        opts = linked_recipients()
        self.assertEqual(classify('Accounts', accounts_default()['email'], opts),
                         SOURCE_ACCOUNTS)


class PopRecipientCaptureTests(TestCase):
    """What the create endpoint stores against each line."""

    def setUp(self):
        self.me = User.objects.create_user('raiser', password='x')
        self.approver = User.objects.create_user(
            'kago', email='ktshutlhedi@alphadirect.co.bw', password='x',
            first_name='Kago')
        self.client.force_login(self.me)
        self.url = reverse('v1-payment-requests')
        seed_adic()
        _contact('Nonesuch Couriers', ON_FILE)

    def post(self, **over):
        body = {
            'subject': 'Nonesuch Couriers — July',
            'category': PaymentRequest.Category.OTHER,
            'line_items': [{'description': 'Courier run', 'amount': '1250.00',
                            'pop_recipient_name': 'Nonesuch Couriers',
                            'pop_recipient_email': ON_FILE}],
            'payee': 'Nonesuch Couriers',
            'account_name': 'Nonesuch Couriers',
            'bank_name': 'FNB', 'account_number': ACCT,
            'new_payee_confirmed': True,
        }
        body.update(over)
        return self.client.post(self.url, body, content_type='application/json')

    def test_the_recipient_is_stored_on_the_line(self):
        self.assertEqual(self.post().status_code, 201)
        ln = PaymentRequest.objects.get().line_items[0]
        self.assertEqual(ln['pop_recipient_email'], ON_FILE)
        self.assertEqual(ln['pop_recipient_name'], 'Nonesuch Couriers')

    def test_a_recipient_on_file_is_not_stamped_off_list(self):
        self.assertEqual(self.post().status_code, 201)
        self.assertNotEqual(
            PaymentRequest.objects.get().line_items[0]['pop_recipient_source'],
            SOURCE_OFF_LIST)

    def test_every_line_ends_up_with_a_recipient(self):
        """A proof of payment is never left with nowhere to go — a blank falls
        back to Accounts, the standing default (CFO 2026-08-22)."""
        r = self.post(line_items=[{'description': 'No recipient typed',
                                   'amount': '900.00'}])
        self.assertEqual(r.status_code, 201, r.content)
        ln = PaymentRequest.objects.get().line_items[0]
        self.assertEqual(ln['pop_recipient_email'], accounts_default()['email'])
        self.assertEqual(ln['pop_recipient_source'], SOURCE_ACCOUNTS)

    def test_the_recipient_is_per_line_not_per_request(self):
        """One claim can pay a panel beater and the claimant."""
        r = self.post(line_items=[
            {'description': 'Panel beating', 'amount': '500.00',
             'pop_recipient_name': 'Nonesuch Couriers', 'pop_recipient_email': ON_FILE},
            {'description': 'Assessment', 'amount': '250.00',
             'pop_recipient_name': 'Accounts',
             'pop_recipient_email': accounts_default()['email']},
        ])
        self.assertEqual(r.status_code, 201, r.content)
        got = [ln['pop_recipient_email'] for ln in PaymentRequest.objects.get().line_items]
        self.assertEqual(got, [ON_FILE, accounts_default()['email']])

    def test_a_malformed_address_defaults_to_accounts_not_silently_stored(self):
        """No blocker (CFO 2026-09-09): a malformed address is never refused —
        it falls back to Accounts, same as a blank one, and is flagged for the
        committee rather than silently kept as typed."""
        r = self.post(line_items=[{'description': 'Courier run', 'amount': '1250.00',
                                   'pop_recipient_email': 'not-an-address'}])
        self.assertEqual(r.status_code, 201, r.content)
        self.assertEqual(r.json()['status'], PaymentRequest.Status.EXCEPTION)
        self.assertEqual(r.json()['exception']['control'], 'PAY-POP-01')
        ln = PaymentRequest.objects.get().line_items[0]
        self.assertEqual(ln['pop_recipient_email'], accounts_default()['email'])
        self.assertEqual(ln['pop_recipient_source'], SOURCE_ACCOUNTS)

    def test_an_off_list_address_is_accepted_and_stamped_off_list(self):
        """Never dead-end the raiser — flag it and let a second person clear it."""
        r = self.post(line_items=[{'description': 'Courier run', 'amount': '1250.00',
                                   'pop_recipient_name': 'Whoever',
                                   'pop_recipient_email': OFF_LIST}])
        self.assertEqual(r.status_code, 201, r.content)
        self.assertEqual(
            PaymentRequest.objects.get().line_items[0]['pop_recipient_source'],
            SOURCE_OFF_LIST)

    def test_clean_lines_drops_a_browser_supplied_source(self):
        """The guard is the field whitelist in _clean_lines: the source is not
        one of the keys read off the request, so it cannot arrive at all."""
        from taskboard.payment_views import _clean_lines
        cleaned = _clean_lines([{'description': 'x', 'amount': '1.00',
                                 'pop_recipient_source': SOURCE_CONTACT}])
        self.assertNotIn('pop_recipient_source', cleaned[0])

    def test_the_browser_cannot_label_its_own_recipient_as_linked(self):
        """The source is derived on the server. A request that could stamp
        itself 'contact on file' would walk straight past the gate."""
        r = self.post(line_items=[{'description': 'Courier run', 'amount': '1250.00',
                                   'pop_recipient_name': 'Whoever',
                                   'pop_recipient_email': OFF_LIST,
                                   'pop_recipient_source': SOURCE_CONTACT}])
        self.assertEqual(r.status_code, 201, r.content)
        self.assertEqual(
            PaymentRequest.objects.get().line_items[0]['pop_recipient_source'],
            SOURCE_OFF_LIST)

    def test_it_applies_to_a_claims_pack_identically(self):
        r = self.post(category=PaymentRequest.Category.CLAIM,
                      claim_payee_type='client',
                      line_items=[{'description': 'Settlement', 'amount': '1250.00',
                                   'claim_number': 'G2026009009',
                                   'pop_recipient_name': 'Whoever',
                                   'pop_recipient_email': OFF_LIST}])
        self.assertEqual(r.status_code, 201, r.content)
        self.assertEqual(
            PaymentRequest.objects.get().line_items[0]['pop_recipient_source'],
            SOURCE_OFF_LIST)

    def test_the_pack_names_the_recipient_and_flags_an_off_list_one(self):
        self.assertEqual(self.post(line_items=[
            {'description': 'Courier run', 'amount': '1250.00',
             'pop_recipient_name': 'Whoever', 'pop_recipient_email': OFF_LIST}]).status_code, 201)
        body = PaymentRequest.objects.get().task.body
        self.assertIn(OFF_LIST, body)
        self.assertIn('NOT ON FILE', body)

    def test_the_dropdown_endpoint_serves_the_options(self):
        r = self.client.get(reverse('v1-pop-recipients'), {'payee': 'Nonesuch Couriers'})
        self.assertEqual(r.status_code, 200, r.content)
        d = r.json()
        self.assertIn(ON_FILE, [o['email'] for o in d['options']])
        self.assertEqual(d['default']['email'], accounts_default()['email'])

    def test_the_dropdown_endpoint_needs_a_login(self):
        self.client.logout()
        self.assertIn(self.client.get(reverse('v1-pop-recipients')).status_code,
                      (401, 403))


class OffListNeedsASecondApproverTests(TestCase):
    """"If the value entered isn't one of the existing linked contacts, require
    a second approver to clear it." The finance approver is that person — they
    may never sign off a request they raised."""

    def setUp(self):
        self.me = User.objects.create_user('raiser', password='x')
        self.approver = User.objects.create_user(
            'kago', email='ktshutlhedi@alphadirect.co.bw', password='x',
            first_name='Kago')
        # An approved sign-off hands the request on to the CFO, so his account
        # must exist or decide() returns 500 before the gate under test.
        User.objects.create_user('pganesharajah',
                                 email='pganesharajah@alphadirect.co.bw')
        seed_adic()
        _contact('Nonesuch Couriers', ON_FILE)
        self.client.force_login(self.me)
        r = self.client.post(reverse('v1-payment-requests'), {
            'subject': 'Nonesuch Couriers — July',
            'category': PaymentRequest.Category.OTHER,
            'line_items': [{'description': 'Courier run', 'amount': '1250.00',
                            'pop_recipient_name': 'Whoever',
                            'pop_recipient_email': OFF_LIST}],
            'payee': 'Nonesuch Couriers', 'account_name': 'Nonesuch Couriers',
            'bank_name': 'FNB', 'account_number': ACCT,
            'new_payee_confirmed': True,
        }, content_type='application/json')
        self.assertEqual(r.status_code, 201, r.content)
        self.pr = PaymentRequest.objects.get()
        self.decide_url = reverse('v1-payment-request-decide', args=[self.pr.id])

    def test_the_helper_names_which_line_is_off_list(self):
        rows = off_list_lines(self.pr.line_items, payee=self.pr.payee)
        self.assertEqual(rows, [{'line': 1, 'name': 'Whoever', 'email': OFF_LIST}])

    def test_sign_off_is_blocked_until_the_approver_clears_it(self):
        self.client.force_login(self.approver)
        r = self.client.post(self.decide_url, {'decision': 'approve'},
                             content_type='application/json')
        self.assertEqual(r.status_code, 409, r.content)
        self.assertEqual(r.json()['control'], 'PAY-POP-ACK')
        self.assertIn(OFF_LIST, r.json()['detail'])
        self.pr.refresh_from_db()
        self.assertEqual(self.pr.status, PaymentRequest.Status.PENDING_FINANCE)

    def test_sign_off_goes_through_once_it_is_cleared(self):
        self.client.force_login(self.approver)
        r = self.client.post(self.decide_url,
                             {'decision': 'approve', 'pop_ack': True},
                             content_type='application/json')
        self.assertEqual(r.status_code, 200, r.content)
        self.pr.refresh_from_db()
        self.assertEqual(self.pr.status, PaymentRequest.Status.PENDING_CFO)

    def test_the_clearance_is_written_on_the_record(self):
        """A tick that leaves no trace is not a control."""
        self.client.force_login(self.approver)
        self.client.post(self.decide_url, {'decision': 'approve', 'pop_ack': True},
                         content_type='application/json')
        self.pr.refresh_from_db()
        self.assertIn('PAY-POP-ACK', self.pr.decision_notes)
        self.assertIn(OFF_LIST, self.pr.decision_notes)

    def test_the_string_false_does_not_clear_it(self):
        """bool("false") is True in Python — a naive check disables the gate."""
        self.client.force_login(self.approver)
        r = self.client.post(self.decide_url,
                             {'decision': 'approve', 'pop_ack': 'false'},
                             content_type='application/json')
        self.assertEqual(r.status_code, 409, r.content)
        self.assertEqual(r.json()['control'], 'PAY-POP-ACK')

    def test_the_raiser_cannot_clear_their_own_off_list_recipient(self):
        """Segregation of duties: the second approver is a second PERSON."""
        first = User.objects.create_user('legakwa',
                                         email='lntabeni@alphadirect.co.bw',
                                         password='x')
        self.client.force_login(first)
        pr2 = PaymentRequest.objects.create(
            ref='PAY/ADIC/2026/09/08/9500', subject='Own request',
            created_by=first, total='100.00', payee='Nonesuch Couriers',
            status=PaymentRequest.Status.PENDING_FINANCE,
            line_items=[{'description': 'x', 'amount': '100.00',
                         'pop_recipient_email': OFF_LIST}])
        r = self.client.post(
            reverse('v1-payment-request-decide', args=[pr2.id]),
            {'decision': 'approve', 'pop_ack': True}, content_type='application/json')
        self.assertEqual(r.status_code, 403, r.content)
        self.assertIn('segregation of duties', r.json()['detail'])

    def test_a_request_whose_recipients_are_all_on_file_needs_no_tick(self):
        self.client.force_login(self.me)
        r = self.client.post(reverse('v1-payment-requests'), {
            'subject': 'Nonesuch Couriers — August',
            'category': PaymentRequest.Category.OTHER,
            'line_items': [{'description': 'Courier run August', 'amount': '2250.00',
                            'pop_recipient_name': 'Nonesuch Couriers',
                            'pop_recipient_email': ON_FILE}],
            'payee': 'Nonesuch Couriers', 'account_name': 'Nonesuch Couriers',
            'bank_name': 'FNB', 'account_number': ACCT,
        }, content_type='application/json')
        self.assertEqual(r.status_code, 201, r.content)
        clean = PaymentRequest.objects.exclude(pk=self.pr.pk).get()
        self.client.force_login(self.approver)
        r = self.client.post(reverse('v1-payment-request-decide', args=[clean.id]),
                             {'decision': 'approve'}, content_type='application/json')
        self.assertEqual(r.status_code, 200, r.content)

    def test_a_cancelled_line_raises_nothing(self):
        """A pulled line is not a payment, so it has no proof to send."""
        items = [{**self.pr.line_items[0], 'cancelled': True}]
        self.assertEqual(off_list_lines(items, payee=self.pr.payee), [])

    def test_the_detail_endpoint_tells_the_screen_which_lines_are_off_list(self):
        self.client.force_login(self.approver)
        d = self.client.get(reverse('v1-payment-request-detail',
                                    args=[self.pr.id])).json()
        self.assertEqual([o['email'] for o in d['pop_off_list']], [OFF_LIST])
