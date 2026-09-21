"""taskboard/test_payment_premium_exception.py — PAY-PREM-01, the premium Gate 0
committee trigger (Kago Tshutlhedi memo v2, 6-Sep-2026, GC 3.B).

A motor claim whose premium for the period of loss was NOT received (red: lapsed,
or the loss falls in an unpaid period) does not proceed straight through — it is
entered as an EXCEPTION the payment committee can release, and the committee
cannot release it without the bank-error proof attached. Green / amber /
cannot-assess do not block.

Each test fails without its fix:
  - red premium claim              -> EXCEPTION, PAY-PREM-01;
  - committee approve with no proof -> refused (attach the bank-error proof);
  - proof attached, three approve   -> released to pending_finance;
  - green premium claim             -> normal path (no premium exception);
  - loss past the data horizon      -> not blocked (cannot assess != lapsed).

Run: manage.py test taskboard.test_payment_premium_exception
"""
from datetime import date
from decimal import Decimal

from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse
from rest_framework.test import APITestCase

from integrations.models import GraphiteClaim
from realpay.models import RealPayTransaction
from taskboard.models import PaymentRequest, PaymentRequestAttachment
from taskboard.test_helpers import seed_adic


def _member(username, email):
    return User.objects.create_user(username, email=email, password='x')


class PremiumExceptionTests(APITestCase):
    def setUp(self):
        seed_adic()
        self.raiser = _member('lmakwapa', 'lmakwapa@alphadirect.co.bw')
        self.pako = _member('pako', 'pkago@alphadirect.co.bw')
        self.kago = _member('kago', 'ktshutlhedi@alphadirect.co.bw')
        self.oprah = _member('oprah', 'omogomotsi@alphadirect.co.bw')
        self.list_url = reverse('v1-payment-requests')
        # The RealPay mirror reaches 31 Aug — losses on/before then are assessable.
        RealPayTransaction.objects.create(
            source=RealPayTransaction.Source.TRANSACTION, txn_date=date(2026, 8, 31),
            client_number='OTHERPOL', current_status='SUCCESSFUL',
            installment_amount=Decimal('100.00'))

    def _claim(self, claim_number, policy_number, loss):
        return GraphiteClaim.objects.create(
            graphite_id=int(claim_number[-6:]), claim_number=claim_number,
            customer_name='M M', is_company=False, policy_number=policy_number,
            product_name='Motor', status='open', date_of_loss=loss,
            total_reserve=Decimal('1999.00'), total_payment=Decimal('0.00'),
            balance=Decimal('1999.00'))

    def _debit(self, client_number, when, status):
        RealPayTransaction.objects.create(
            source=RealPayTransaction.Source.TRANSACTION, txn_date=when,
            client_number=client_number, current_status=status,
            installment_amount=Decimal('500.00'))

    def _raise(self, claim_number, amount='1999.00'):
        self.client.force_authenticate(self.raiser)
        return self.client.post(self.list_url, {
            'subject': 'Motor claim payment',
            'category': PaymentRequest.Category.CLAIM,
            'entity': 'Alpha Direct Insurance Company',
            'claim_payee_type': PaymentRequest.ClaimPayeeType.CLIENT,
            'payee': 'A CLAIMANT',
            'account_name': 'A CLAIMANT',
            'bank_name': 'FNB Botswana',
            # No digits, so no bank control has anything to judge — this suite is
            # about the premium leg only.
            'account_number': 'TEST-ACCT-NOT-REAL',
            'line_items': [{'description': f'A CLAIMANT {claim_number}',
                            'amount': amount, 'claim_number': claim_number}],
        }, format='json')

    def _lapsed_claim(self, claim_number):
        # Cover bought 1 Jun ends 1 Jul; a loss on 1 Aug is 31 days overdue -> red.
        self._claim(claim_number, 'POLRED', date(2026, 8, 1))
        self._debit('POLRED', date(2026, 6, 1), 'SUCCESSFUL')

    def _sign(self, user, pk, **over):
        self.client.force_authenticate(user)
        body = {'decision': 'approve'}
        body.update(over)
        return self.client.post(reverse('v1-payment-exception-signoff', args=[pk]),
                                body, format='json')

    # ── routing ──────────────────────────────────────────────────────────────
    def test_red_premium_claim_goes_to_the_committee(self):
        self._lapsed_claim('G0000001')
        r = self._raise('G0000001')
        self.assertEqual(r.status_code, 201, r.content)
        body = r.json()
        self.assertEqual(body['status'], PaymentRequest.Status.EXCEPTION)
        self.assertEqual(body['exception']['control'], 'PAY-PREM-01')
        self.assertIn('proof', body['exception']['message'].lower())
        pr = PaymentRequest.objects.get(pk=body['id'])
        self.assertEqual(pr.exception_control, 'PAY-PREM-01')
        self.assertIn('[PAY-PREM-01]', pr.exception_reason)

    def test_green_premium_claim_stays_on_the_normal_path(self):
        self._claim('G0000004', 'POLGRN', date(2026, 8, 1))
        self._debit('POLGRN', date(2026, 7, 20), 'SUCCESSFUL')   # cover to 19 Aug — covered
        r = self._raise('G0000004')
        self.assertEqual(r.status_code, 201, r.content)
        self.assertEqual(r.json()['status'], PaymentRequest.Status.PENDING_FINANCE)
        self.assertEqual(PaymentRequest.objects.get(pk=r.json()['id']).exception_control, '')

    def test_loss_past_the_data_horizon_is_not_blocked(self):
        # Mirror ends 31 Aug (setUp); a 15 Sep loss cannot be assessed — never a
        # false lapse, so it stays on the normal path.
        self._claim('G0000005', 'POLNA', date(2026, 9, 15))
        self._debit('POLNA', date(2026, 6, 1), 'SUCCESSFUL')
        r = self._raise('G0000005')
        self.assertEqual(r.status_code, 201, r.content)
        self.assertEqual(r.json()['status'], PaymentRequest.Status.PENDING_FINANCE)

    # ── the block: committee cannot release without the bank-error proof ──────
    def test_committee_cannot_release_without_designating_the_proof(self):
        self._lapsed_claim('G0000002')
        pk = self._raise('G0000002').json()['id']
        pr = PaymentRequest.objects.get(pk=pk)
        # An unrelated file (the invoice the raiser added at raise) must NOT
        # satisfy the gate — the member has to designate the specific bank-error
        # proof. Approving with no proof_attachment_id is refused.
        PaymentRequestAttachment.objects.create(
            request=pr, file=SimpleUploadedFile('invoice.pdf', b'inv'),
            original_name='invoice.pdf', uploaded_by=self.raiser)
        r = self._sign(self.pako, pk)   # no proof_attachment_id
        self.assertEqual(r.status_code, 400, r.content)
        self.assertIn('proof', r.json()['detail'].lower())
        self.assertEqual(PaymentRequest.objects.get(pk=pk).status,
                         PaymentRequest.Status.EXCEPTION)   # still blocked

    def test_with_the_designated_proof_three_approvals_release_it(self):
        self._lapsed_claim('G0000003')
        pk = self._raise('G0000003').json()['id']
        pr = PaymentRequest.objects.get(pk=pk)
        proof = PaymentRequestAttachment.objects.create(
            request=pr, file=SimpleUploadedFile('bankerror.pdf', b'proof'),
            original_name='bankerror.pdf', uploaded_by=self.raiser)
        pid = str(proof.id)
        self.assertEqual(self._sign(self.pako, pk, proof_attachment_id=pid).status_code, 200)
        self.assertEqual(self._sign(self.kago, pk, proof_attachment_id=pid).status_code, 200)
        last = self._sign(self.oprah, pk, proof_attachment_id=pid)
        self.assertEqual(last.status_code, 200, last.content)
        self.assertTrue(last.json()['decided'])
        pr.refresh_from_db()
        self.assertEqual(pr.status, PaymentRequest.Status.PENDING_FINANCE)
        self.assertEqual(pr.exception_decision, 'approve')
        # the proof's name is written onto the release record
        from taskboard.models import PaymentReleaseSignoff
        self.assertTrue(any('bankerror.pdf' in (s.note or '')
                            for s in PaymentReleaseSignoff.objects.filter(request=pr)))
