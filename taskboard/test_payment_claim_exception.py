"""taskboard/test_payment_claim_exception.py — PAY-CLAIM-01, the Graphite
claim signal (CFO 2026-09-04, then the standing rule of CFO 2026-09-09).

Leano Makwapa raised repairer/supplier claim payments and Omni kept "rejecting"
them: it picked up the reserve/payoff off Graphite and treated the claim as
already processed, so nearly every claim parked in the exceptions queue while
"only a few pass through". The reason it is a FALSE signal: Graphite posts the
loss payment the moment Claims RAISES it, before FNB has paid anyone.

The CFO's standing ruling (2026-09-09), hard-coded in payment_views.py:
  a Graphite-derived claim signal — closed / settled / repudiated status, the
  reserve, the payoff, the balance — NEVER parks, holds, blocks or rejects a
  payment, and never turns it into an exception on its own. It is attached to
  the pack as a NOTE the finance approver / committee reads; the payment flows
  the NORMAL sign-off path and a human makes the final decision. Omni never
  moves money regardless — FNB + the CFO's phone 2-factor is the real gate.

Genuine committee controls are unrelated to Graphite and keep their own suites:
a changed bank account (PAY-BANK-01), an Omni-record duplicate (PAY-DUP-01),
premium not received (PAY-PREM-01) — see test_payment_exception_committee.py,
test_payment_duplicates.py, test_payment_premium_exception.py.

Rules pinned here (each fails without its fix):
  - closed in Graphite + nothing in Omni  -> ALLOWED on the normal path
    (pending_finance, no exception), with the Graphite note on the pack and a
    plain "you are not blocked" note back to the raiser;
  - open in Graphite with a payment posted -> normal path, no note needed;
  - unknown claim                          -> normal path, no note.

Run: manage.py test taskboard.test_payment_claim_exception
"""
from decimal import Decimal
from uuid import uuid4

from django.contrib.auth.models import User
from django.urls import reverse
from rest_framework.test import APITestCase

from integrations.models import GraphiteClaim
from taskboard.models import PaymentRequest
from taskboard.test_helpers import seed_adic


def _member(username, email):
    return User.objects.create_user(username, email=email, password='x')


class ClaimGraphiteNoteTests(APITestCase):
    def setUp(self):
        seed_adic()
        self.raiser = _member('lmakwapa', 'lmakwapa@alphadirect.co.bw')
        self.pako = _member('pako', 'pkago@alphadirect.co.bw')
        self.list_url = reverse('v1-payment-requests')

    def _mirror(self, claim_number, **over):
        base = dict(graphite_id=int(claim_number[-6:]), claim_number=claim_number,
                    customer_name='M M', status='Closed',
                    total_reserve=Decimal('1999.00'), total_payment=Decimal('1999.00'),
                    balance=Decimal('0.00'))
        base.update(over)
        return GraphiteClaim.objects.create(**base)

    def _raise(self, claim_number, amount='1999.00'):
        self.client.force_authenticate(self.raiser)
        return self.client.post(self.list_url, {
            'subject': 'Shielders Botswana — claim payment',
            'category': PaymentRequest.Category.CLAIM,
            'entity': 'Alpha Direct Insurance Company',
            'claim_payee_type': PaymentRequest.ClaimPayeeType.CLIENT,
            'payee': 'SHIELDERS BOTSWANA',
            'account_name': 'SHIELDERS BOTSWANA',
            'bank_name': 'FNB Botswana',
            # No digits, so neither PAY-BANK-01 nor PAY-BANK-03 has anything to
            # judge — this suite is about the claim leg only.
            'account_number': 'TEST-ACCT-NOT-REAL',
            'line_items': [{'description': f'SHIELDERS BOTSWANA {claim_number}',
                            'amount': amount, 'claim_number': claim_number}],
        }, format='json')

    # ── the standing rule: a Graphite signal never blocks the payment ────────
    def test_closed_claim_is_allowed_on_the_normal_path_with_a_note(self):
        self._mirror('G2026004646')
        r = self._raise('G2026004646')
        self.assertEqual(r.status_code, 201, r.content)
        body = r.json()
        # NOT parked as an exception — it flows the normal sign-off path.
        self.assertEqual(body['status'], PaymentRequest.Status.PENDING_FINANCE)
        self.assertNotIn('exception', body)
        # The raiser is reassured in plain English that they are not blocked.
        self.assertIn('not blocked', body.get('note', ''))
        pr = PaymentRequest.objects.get(ref=body['ref'])
        self.assertEqual(pr.exception_control, '')
        self.assertIsNone(pr.exception_raised_at)
        # The Graphite observation rides the finance pack for the approver.
        self.assertIn('G2026004646', pr.task.body)
        self.assertIn('GRAPHITE NOTE', pr.task.body)
        self.assertIn('sign-off', pr.task.title.lower())

    def test_open_claim_with_a_posting_stays_on_the_normal_path(self):
        # Leano's Randy Taukobong case: Pending in Graphite, 378,477.01 posted,
        # Finance raising 376,477.01. Normal sequence — no note, no exception.
        self._mirror('G2026004552', status='Pending',
                     total_reserve=Decimal('378477.01'), total_payment=Decimal('378477.01'))
        r = self._raise('G2026004552', amount='376477.01')
        self.assertEqual(r.status_code, 201, r.content)
        self.assertEqual(r.json()['status'], PaymentRequest.Status.PENDING_FINANCE)
        self.assertNotIn('exception', r.json())
        self.assertNotIn('note', r.json())
        self.assertEqual(PaymentRequest.objects.get().exception_control, '')

    def test_closed_claim_omni_already_holds_is_not_flagged_here(self):
        # PAY-DUP-01 owns the real Omni-record duplicate; this leg adds no note.
        self._mirror('G2026004646')
        other = _member('other', 'other@alphadirect.co.bw')
        PaymentRequest.objects.create(
            ref=f'PAY/TEST/{uuid4().hex[:8]}', entity='Alpha Direct Insurance Company',
            subject='earlier', payee='SHIELDERS BOTSWANA', currency='BWP', total='500.00',
            line_items=[{'description': 'SHIELDERS G2026004646 part 1', 'amount': '500.00',
                         'claim_number': 'G2026004646'}],
            status=PaymentRequest.Status.PAID, created_by=other)
        r = self._raise('G2026004646')       # different amount: PAY-DUP-01 lets it through
        self.assertEqual(r.status_code, 201, r.content)
        self.assertEqual(r.json()['status'], PaymentRequest.Status.PENDING_FINANCE)
        self.assertNotIn('note', r.json())

    def test_unknown_claim_is_not_flagged(self):
        r = self._raise('G2026009999')
        self.assertEqual(r.status_code, 201, r.content)
        self.assertEqual(r.json()['status'], PaymentRequest.Status.PENDING_FINANCE)
        self.assertNotIn('note', r.json())
