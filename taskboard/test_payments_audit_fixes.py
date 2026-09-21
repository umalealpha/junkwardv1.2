"""taskboard/test_payments_audit_fixes.py — the Fable 5.1 audit fixes (CFO 2026-09-02).

Each test fails without its fix. Omni moves no money: everything here is a
workflow record; the CFO's own FNB two-factor stays the only real gate.

Run: manage.py test taskboard.test_payments_audit_fixes
"""
from datetime import timedelta
from decimal import Decimal
from unittest import mock
from uuid import uuid4

from django.contrib.auth.models import User
from django.core import mail
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APITestCase

from taskboard.models import PaymentRequest

ENTITY = 'Alpha Direct Insurance Company'


def _u(username, email):
    return User.objects.create_user(username, email=email, password='x')


class _Base(APITestCase):
    def setUp(self):
        self.cfo = User.objects.create_superuser(
            'cfo', email='pganesharajah@alphadirect.co.bw', password='x')
        self.pako = _u('pkago', 'pkago@alphadirect.co.bw')
        self.kago = _u('ktshutlhedi', 'ktshutlhedi@alphadirect.co.bw')
        self.lega = _u('lntabeni', 'lntabeni@alphadirect.co.bw')
        self.oprah = _u('omogomotsi', 'omogomotsi@alphadirect.co.bw')
        self.clerk = _u('clerk', 'clerk@alphadirect.co.bw')
        self.list_url = reverse('v1-payment-requests')

    def _post(self, user, **over):
        self.client.force_authenticate(user)
        body = {
            'subject': 'Repair invoices', 'category': PaymentRequest.Category.OTHER,
            'payee': 'Carfil Services',
            'line_items': [{'description': 'Panel work', 'amount': '1000.00'}],
            'account_name': 'Carfil Services', 'bank_name': 'FNB',
            'account_number': '62010000000', 'new_payee_confirmed': True,
        }
        body.update(over)
        return self.client.post(self.list_url, body, format='json')

    def _history(self, payee='Carfil Services', account='62010000000'):
        """A PAID request — the account we last paid this payee into."""
        return PaymentRequest.objects.create(
            ref=f'PAY/ADIC/2026/08/01/{uuid4().hex[:4]}', entity=ENTITY,
            status=PaymentRequest.Status.PAID, subject='prior', payee=payee,
            account_name=payee, account_number=account, bank_name='FNB',
            currency='BWP', total=Decimal('500.00'),
            line_items=[{'description': 'older', 'amount': '500.00'}],
            created_by=self.clerk)

    def _exception(self):
        return PaymentRequest.objects.create(
            ref=f'PAY/TEST/{uuid4().hex[:8]}', entity=ENTITY,
            subject='Changed bank account', payee='ABC Traders',
            currency='BWP', total=Decimal('4500.00'),
            line_items=[{'description': 'x', 'amount': '4500.00'}],
            status=PaymentRequest.Status.EXCEPTION,
            exception_control='PAY-BANK-01', exception_reason='The account changed.',
            created_by=self.clerk)

    def _sign(self, user, pk, decision='approve'):
        self.client.force_authenticate(user)
        return self.client.post(
            reverse('v1-payment-exception-signoff', args=[pk]),
            {'decision': decision, 'called_who': 'Mr Traders',
             'called_number': '+267 300 0000'}, format='json')


class ReferenceAllocationTests(_Base):
    """C1 — one Drop Box use dead-ended every hand-typed payment for the day."""

    def test_a_discarded_draft_does_not_break_todays_numbering(self):
        from taskboard.payment_views import _next_ref
        pre = f'PAY/ADIC/{timezone.localdate():%Y/%m/%d}/'
        for n in (1, 2):
            PaymentRequest.objects.create(
                ref=f'{pre}{n:04d}', entity=ENTITY, status=PaymentRequest.Status.DRAFT,
                subject='draft', currency='BWP', total=Decimal('1.00'),
                line_items=[], created_by=self.clerk)
        PaymentRequest.objects.filter(ref=f'{pre}0001').delete()        # the gap
        self.assertEqual(_next_ref(ENTITY), f'{pre}0003')
        r = self._post(self.clerk)
        self.assertEqual(r.status_code, 201, r.content)
        self.assertEqual(r.json()['ref'], f'{pre}0003')


class CommitteeDecisionTests(_Base):
    def test_one_reject_ends_it_at_once(self):
        """H2 — any reject rejects the payment immediately."""
        pr = self._exception()
        r = self._sign(self.pako, pr.id, decision='reject')
        self.assertEqual(r.status_code, 200, r.content)
        self.assertTrue(r.json()['decided'])
        pr.refresh_from_db()
        self.assertEqual(pr.status, PaymentRequest.Status.REJECTED)
        self.assertEqual(pr.exception_decision, 'reject')

    def test_three_approvals_still_release_it(self):
        pr = self._exception()
        self._sign(self.pako, pr.id)
        self._sign(self.kago, pr.id)
        r = self._sign(self.oprah, pr.id)
        self.assertTrue(r.json()['decided'])
        pr.refresh_from_db()
        self.assertEqual(pr.status, PaymentRequest.Status.PENDING_FINANCE)

    def test_the_cfo_can_close_an_undecided_exception(self):
        """H2 — an exception with one signature had no exit at all."""
        pr = self._exception()
        self._sign(self.pako, pr.id)
        url = reverse('v1-payment-exception-clear', args=[pr.id])
        self.client.force_authenticate(self.cfo)
        self.assertEqual(self.client.post(url, {}, format='json').status_code, 409)
        r = self.client.post(url, {'close': True,
                                   'reason': 'Supplier could not be reached; cancelled.'},
                             format='json')
        self.assertEqual(r.status_code, 200, r.content)
        pr.refresh_from_db()
        self.assertEqual(pr.status, PaymentRequest.Status.REJECTED)
        self.assertIsNotNone(pr.exception_cleared_at)
        self.assertIn('Closed by the CFO', pr.decision_notes)

    def test_a_committee_member_cannot_close_it(self):
        pr = self._exception()
        self.client.force_authenticate(self.pako)
        r = self.client.post(reverse('v1-payment-exception-clear', args=[pr.id]),
                             {'close': True, 'reason': 'trying it on'}, format='json')
        self.assertEqual(r.status_code, 403)

    def test_the_raiser_is_emailed_when_the_committee_decides(self):
        """M1 — the pop promises it; now it happens."""
        pr = self._exception()
        mail.outbox = []
        self._sign(self.pako, pr.id, decision='reject')
        recipients = [a for m in mail.outbox for a in (m.to + m.cc)]
        self.assertIn('clerk@alphadirect.co.bw', recipients)

    def test_an_approved_exception_reaches_the_finance_approver(self):
        """M1 — raised through the real create path so the finance task exists."""
        self._history()
        r = self._post(self.clerk, account_number='62019999999')   # changed account
        self.assertEqual(r.status_code, 201, r.content)
        self.assertEqual(r.json()['status'], 'exception')
        pr = PaymentRequest.objects.get(id=r.json()['id'])
        mail.outbox = []
        self._sign(self.pako, pr.id)
        self._sign(self.kago, pr.id)
        self._sign(self.oprah, pr.id)
        pr.refresh_from_db()
        self.assertEqual(pr.status, PaymentRequest.Status.PENDING_FINANCE)
        self.assertNotIn('committee decision needed', pr.task.title)
        recipients = [a for m in mail.outbox for a in m.to]
        self.assertIn(pr.task.assignee.email, recipients)


class CfoResearchAlertTests(_Base):
    """CFO 2026-09-09: every payment exception raises an amber alert to the CFO —
    an Omni task on his account AND an email — 'research before paying'."""

    def test_an_exception_raises_a_cfo_research_task_and_email(self):
        from django.core import mail
        from taskboard.models import OmniTask
        self._history('Carfil Services', '62010000000')
        mail.outbox = []
        r = self._post(self.clerk, account_number='62019999999')   # changed account -> exception
        self.assertEqual(r.json().get('status'), 'exception', r.content)
        # A 'research before paying' task is created on the CFO's own account.
        cfo_tasks = OmniTask.objects.filter(
            assignee=self.cfo, title__icontains='Research before paying')
        self.assertEqual(cfo_tasks.count(), 1)
        self.assertIn('research', cfo_tasks.first().body.lower())
        # The amber alert email reached the EXCO board.
        recipients = [a for m in mail.outbox for a in m.to]
        self.assertIn('excoboard@alphadirect.co.bw', recipients)

    def test_a_clean_payment_raises_no_cfo_research_task(self):
        from taskboard.models import OmniTask
        r = self._post(self.clerk)   # clean -> pending_finance, no exception
        self.assertEqual(r.json().get('status'), 'pending_finance', r.content)
        self.assertFalse(OmniTask.objects.filter(
            assignee=self.cfo, title__icontains='Research before paying').exists())


class RespelledPayeeTests(_Base):
    """H1 — one changed letter turned a committee decision into a self-tick."""

    def test_one_changed_letter_still_goes_to_the_committee(self):
        self._history('Carfil Services', '62010000000')
        r = self._post(self.clerk, payee='Carfil Service', account_name='Carfil Service',
                       account_number='62019999999')
        self.assertEqual(r.status_code, 201, r.content)
        self.assertEqual(r.json()['status'], 'exception')

    def test_a_suffix_is_the_same_supplier_too(self):
        self._history('Carfil Services', '62010000000')
        r = self._post(self.clerk, payee='Carfil Services BW',
                       account_name='Carfil Services BW', account_number='62019999999')
        self.assertEqual(r.json()['status'], 'exception')

    def test_the_same_account_is_never_an_exception(self):
        self._history('Carfil Services', '62010000000')
        r = self._post(self.clerk, payee='Carfil Service', account_name='Carfil Service')
        self.assertEqual(r.status_code, 201, r.content)
        self.assertEqual(r.json()['status'], 'pending_finance')

    def test_a_genuinely_different_name_is_a_first_payment(self):
        # CFO 2026-09-09 "no blocker": a first-ever payee with no confirmation
        # tick no longer dead-ends with a 409 — it goes to the committee, which
        # confirms the account. The raiser is not blocked.
        self._history('Carfil Services', '62010000000')
        r = self._post(self.clerk, payee='Botswana Power Corporation',
                       account_name='Botswana Power Corporation',
                       account_number='62019999999', new_payee_confirmed=False)
        self.assertEqual(r.status_code, 201, r.content)
        self.assertEqual(r.json()['exception']['control'], 'PAY-BANK-03')


class PayeeLookupTests(_Base):
    """H4 — the supplier bank book was readable by typing payee names."""

    def _lookup(self, user):
        self.client.force_authenticate(user)
        return self.client.get(reverse('v1-payee-bank-lookup') + '?payee=Carfil%20Services')

    def test_ordinary_users_see_only_the_last_four_digits(self):
        self._history()
        r = self._lookup(self.clerk)
        self.assertEqual(r.status_code, 200, r.content)
        self.assertTrue(r.json()['account_masked'])
        self.assertIn('*', r.json()['account_number'])
        self.assertTrue(r.json()['account_number'].endswith('0000'))

    def test_a_finance_approver_sees_the_full_number(self):
        self._history()
        r = self._lookup(self.pako)
        self.assertFalse(r.json()['account_masked'])
        self.assertEqual(r.json()['account_number'], '62010000000')

    def test_the_form_can_still_use_the_known_account_without_seeing_it(self):
        self._history()
        r = self._post(self.clerk, account_number='*******0000', use_known_account=True)
        self.assertEqual(r.status_code, 201, r.content)
        pr = PaymentRequest.objects.get(id=r.json()['id'])
        self.assertEqual(pr.account_number, '62010000000')
        self.assertEqual(pr.status, PaymentRequest.Status.PENDING_FINANCE)

    def test_autofill_never_attaches_a_fuzzy_neighbours_account(self):
        """Reviewer MEDIUM: use_known_account must fill only on an EXACT payee
        match, or a new supplier ('Gaborone Motels') would silently get a real
        neighbour's account ('Gaborone Motors') with the bank controls suppressed."""
        self._history(payee='Gaborone Motors', account='62055550000')
        r = self._post(self.clerk, payee='Gaborone Motels', account_name='Gaborone Motels',
                       account_number='*******0000', use_known_account=True)
        # Motors' account is NEVER stored on a Motels payment — the security
        # property that matters. The masked placeholder no longer dead-ends the
        # raiser (CFO 2026-09-09): it is blanked and goes to the committee to
        # confirm the real digits.
        self.assertEqual(r.status_code, 201, r.content)
        self.assertEqual(r.json()['exception']['control'], 'PAY-BANK-02')
        self.assertFalse(PaymentRequest.objects.filter(
            account_number='62055550000', payee='Gaborone Motels').exists())
        # The stored account is blank, never the fuzzy neighbour's digits.
        self.assertEqual(PaymentRequest.objects.get(payee='Gaborone Motels').account_number, '')

    def test_exact_match_lookup_does_not_offer_a_neighbours_account(self):
        from taskboard.payee_bank_history import last_known_bank
        self._history(payee='Gaborone Motors', account='62055550000')
        self.assertIsNone(last_known_bank('Gaborone Motels', exact=True))
        self.assertIsNotNone(last_known_bank('Gaborone Motors', exact=True))


class LateRejectTests(_Base):
    def test_the_signer_cannot_reject_what_they_signed_off(self):
        """M3 — the own-signoff bar that clear() already had."""
        pr_id = self._post(self.clerk).json()['id']
        url = reverse('v1-payment-request-decide', args=[pr_id])
        self.client.force_authenticate(self.kago)
        self.assertEqual(self.client.post(url, {'decision': 'approve'}, format='json').status_code, 200)
        r = self.client.post(url, {'decision': 'reject', 'notes': 'changed my mind'}, format='json')
        self.assertEqual(r.status_code, 403, r.content)
        self.client.force_authenticate(self.pako)
        r = self.client.post(url, {'decision': 'reject', 'notes': 'duplicate of last week'}, format='json')
        self.assertEqual(r.status_code, 200, r.content)


class PettyCashCapTests(_Base):
    """M4 — BWP 5,000 cap (CFO 2026-09-02)."""

    def _petty(self, amount):
        return self._post(self.clerk, category=PaymentRequest.Category.PETTY_CASH,
                          line_items=[{'description': 'float top-up', 'amount': amount}],
                          account_name='', bank_name='', account_number='')

    def test_petty_cash_over_the_cap_bank_details_go_to_committee(self):
        # CFO 2026-09-09 "no blocker": missing bank details no longer refuse the
        # pack — it goes to the committee (which confirms the details).
        r = self._petty('6000.00')
        self.assertEqual(r.status_code, 201, r.content)
        self.assertEqual(r.json()['exception']['control'], 'PAY-BANK-02')

    def test_petty_cash_under_the_cap_is_still_exempt(self):
        r = self._petty('4000.00')
        self.assertEqual(r.status_code, 201, r.content)


class AttachmentsTests(_Base):
    """M11 — approvers and the committee could read a pack but not attach to it."""

    def test_a_committee_member_can_see_and_add_documents(self):
        pr = self._exception()
        self.client.force_authenticate(self.oprah)
        url = reverse('v1-payment-request-attachments', args=[pr.id])
        self.assertEqual(self.client.get(url).status_code, 200)
        f = SimpleUploadedFile('callback-note.pdf', b'%PDF-1.4 note',
                               content_type='application/pdf')
        r = self.client.post(url, {'file': f}, format='multipart')
        self.assertEqual(r.status_code, 201, r.content)

    def test_an_outsider_still_cannot(self):
        pr = self._exception()
        self.client.force_authenticate(_u('outsider', 'nobody@alphadirect.co.bw'))
        r = self.client.get(reverse('v1-payment-request-attachments', args=[pr.id]))
        self.assertEqual(r.status_code, 403)


class RoundingTests(_Base):
    def test_line_amounts_round_half_up(self):
        """M10 — the house rule for money."""
        from taskboard.payment_views import _dec
        self.assertEqual(_dec('10.005'), Decimal('10.01'))
        self.assertEqual(_dec('0.125'), Decimal('0.13'))
        self.assertEqual(_dec('2.675'), Decimal('2.68'))


class DuplicateOverrideRemovedTests(_Base):
    """H3 / CFO 2026-09-02 + 2026-09-04 — the raiser's written override is gone.
    A possible duplicate is still ENTERED (201) and routed to the exception
    committee (status exception, control PAY-DUP-01); it is never blocked with a
    400, and no reason or category typed on the form can wave it straight
    through to finance sign-off."""

    def test_a_written_reason_and_category_no_longer_clear_a_duplicate(self):
        lines = [{'description': 'G2026004287 CARFIL', 'amount': '5307.32'}]
        self.assertEqual(self._post(self.clerk, line_items=lines).status_code, 201)
        r = self._post(self.clerk, line_items=lines,
                       duplicate_override_reason=('FNB rejected the first attempt with RJCT '
                                                  'on 30 July, confirmed on the statement.'),
                       duplicate_override_category='bank_rejected')
        self.assertEqual(r.status_code, 201, r.content)
        body = r.json()
        self.assertEqual(body['status'], PaymentRequest.Status.EXCEPTION)
        self.assertEqual(body['exception']['control'], 'PAY-DUP-01')
        pr = PaymentRequest.objects.get(id=body['id'])
        self.assertEqual(pr.status, PaymentRequest.Status.EXCEPTION)
        self.assertEqual(pr.exception_control, 'PAY-DUP-01')
        # Both requests exist; only the FIRST is with finance. The override text
        # did not move the second one past the committee.
        self.assertEqual(PaymentRequest.objects.count(), 2)
        self.assertEqual(PaymentRequest.objects.filter(
            status=PaymentRequest.Status.PENDING_FINANCE).count(), 1)

    def test_the_countersign_route_is_gone(self):
        from django.urls import NoReverseMatch
        with self.assertRaises(NoReverseMatch):
            reverse('v1-payment-countersign-override', args=[uuid4()])


class ExceptionVisibilityTests(_Base):
    """M2 — exceptions were invisible everywhere but the board."""

    def test_detail_carries_the_exception_fields(self):
        pr = self._exception()
        self._sign(self.pako, pr.id)
        self.client.force_authenticate(self.oprah)
        r = self.client.get(reverse('v1-payment-request-detail', args=[pr.id]))
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(r.json()['exception_control'], 'PAY-BANK-01')
        self.assertEqual(len(r.json()['exception_signoffs']), 1)

    def test_the_digest_counts_exceptions(self):
        from taskboard import payment_digest
        self._exception()
        d = payment_digest.collect()
        self.assertEqual(len(d['waiting_committee']), 1)
        self.assertEqual(d['open_count'], 1)

    def test_the_summary_panel_counts_exceptions(self):
        self._exception()
        self.client.force_authenticate(self.pako)
        with mock.patch('taskboard.payment_digest.narrative', return_value=('x', 'fallback')):
            r = self.client.get(reverse('v1-payment-summary'))
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(r.json()['waiting_committee_count'], 1)


class DropBoxSubmitTests(_Base):
    """H6 — the draft path had a silent success and a dead-end of its own."""

    def _draft(self, **over):
        d = dict(ref=f'PAY/ADIC/2026/08/02/{uuid4().hex[:4]}', entity=ENTITY,
                 status=PaymentRequest.Status.DRAFT, subject='Invoice INV-1',
                 payee='Gaborone Panel Beaters', category=PaymentRequest.Category.OTHER,
                 currency='BWP', total=Decimal('1500.00'),
                 account_name='Gaborone Panel Beaters', account_number='62001234567',
                 bank_name='FNB Botswana', branch_code='282267',
                 line_items=[{'description': 'Invoice INV-1', 'amount': '1500.00',
                              'invoice_number': 'INV-1'}],
                 created_by=self.clerk)
        d.update(over)
        return PaymentRequest.objects.create(**d)

    def _submit(self, draft, **body):
        self.client.force_authenticate(self.clerk)
        return self.client.post(reverse('v1-payment-request-draft-submit', args=[draft.id]),
                                body, format='json')

    def test_a_changed_account_from_drop_box_is_told_to_the_raiser(self):
        self._history('Gaborone Panel Beaters', '62009999999')
        r = self._submit(self._draft())
        self.assertEqual(r.status_code, 201, r.content)
        self.assertEqual(r.json()['status'], 'exception')
        self.assertIn('committee', (r.json().get('exception') or {}).get('message', '').lower())

    def test_a_supplier_invoice_not_yet_due_can_be_submitted_with_a_payment_date(self):
        today = timezone.localdate()
        statement = (today.replace(day=1) + timedelta(days=32)).replace(day=1) - timedelta(days=1)
        due = statement + timedelta(days=30)
        lines = [{'description': 'Parts', 'amount': '1500.00', 'invoice_number': 'KA-1',
                  'invoice_date': today.isoformat(), 'terms_basis': 'statement',
                  'terms_days': '30', 'due_date': due.isoformat()}]
        # Without a payment date / discount check the terms gate fires — but it
        # no longer refuses (CFO 2026-09-09): it enters as a committee exception,
        # the raiser is not blocked. (A submit consumes the draft, so each leg
        # gets its own draft.)
        r = self._submit(self._draft(category=PaymentRequest.Category.SUPPLIER,
                                     line_items=lines), new_payee_confirmed=True)
        self.assertEqual(r.status_code, 201, r.content)
        self.assertEqual(r.json().get('status'), 'exception')
        self.assertEqual(r.json()['exception']['control'], 'PAY-SUP-01')
        # With a payment date on/after due + the discount check, it sails through
        # the normal finance path. Distinct invoice + no prior request, so the
        # duplicate gate (PAY-DUP-01) has nothing to match against.
        PaymentRequest.objects.all().delete()
        lines2 = [{**lines[0], 'invoice_number': 'KA-2'}]
        r2 = self._submit(self._draft(category=PaymentRequest.Category.SUPPLIER,
                                      line_items=lines2),
                          new_payee_confirmed=True, payment_date=due.isoformat(),
                          discount_checked=True)
        self.assertEqual(r2.status_code, 201, r2.content)
        self.assertEqual(r2.json().get('status'), 'pending_finance')
