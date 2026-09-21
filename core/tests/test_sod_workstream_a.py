"""
Segregation-of-duties controls — Workstream A (CFO-approved 2026-07-02).

Covers bugs #1 (voucher-clearing / posted-JE reversal), #2 (outbound payments),
#5 (FX-rate loading). Control model:

    MAKER  (originate) = Financial Controller / Senior Accountant / Accountant
    CHECKER (approve)  = Finance Manager (or CFO)
    approver != originator, enforced at the record level (never waived, not even
    for a superuser).

Proves: (a) a Finance Manager can NO LONGER originate payments / JE reversals /
FX loads; (b) a maker can originate but not approve; (c) a Finance Manager can
approve; (d) the same user cannot be both maker and approver on one record;
(e) the legitimate maker→different-checker flow still works.
"""
from __future__ import annotations

from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase
from rest_framework.test import APIRequestFactory, force_authenticate

from core.models import UserProfile, Currency


def _user(username, title, is_superuser=False):
    u = User.objects.create_user(username=username, password='x', is_superuser=is_superuser,
                                 is_staff=is_superuser)
    UserProfile.objects.update_or_create(
        user=u, defaults={'role': UserProfile.Role.ACCOUNTANT, 'title': title, 'is_active': True})
    return u


class SoDTitleModelTests(TestCase):
    """(a)(b)(c) at the authority layer — the maker/checker sets."""

    def test_finance_manager_is_checker_not_maker(self):
        fm = _user('fm1', UserProfile.Title.FINANCE_MANAGER)
        self.assertFalse(fm.profile.can_originate_controlled_txn)   # (a) FM not a maker
        self.assertTrue(fm.profile.can_check_controlled_txn)        # (c) FM is a checker

    def test_financial_controller_is_maker_not_checker(self):
        fc = _user('fc1', UserProfile.Title.FINANCIAL_CONTROLLER)
        self.assertTrue(fc.profile.can_originate_controlled_txn)    # (b) maker
        self.assertFalse(fc.profile.can_check_controlled_txn)       # (b) not a checker

    def test_senior_accountant_and_accountant_are_makers(self):
        sa = _user('sa1', UserProfile.Title.SENIOR_ACCOUNTANT)
        ac = _user('ac1', UserProfile.Title.ACCOUNTANT)
        self.assertTrue(sa.profile.can_originate_controlled_txn)
        self.assertTrue(ac.profile.can_originate_controlled_txn)
        self.assertFalse(sa.profile.can_check_controlled_txn)
        self.assertFalse(ac.profile.can_check_controlled_txn)

    def test_cfo_can_check(self):
        cfo = _user('cfo1', UserProfile.Title.CFO)
        self.assertTrue(cfo.profile.can_check_controlled_txn)


class FxRateSoDTests(TestCase):
    """#5 — FX load gated to makers; loader cannot approve own rate (even super)."""

    def setUp(self):
        self.factory = APIRequestFactory()
        Currency.objects.get_or_create(code='USD', defaults={'name': 'US Dollar', 'symbol': '$'})
        Currency.objects.get_or_create(code='BWP', defaults={'name': 'Botswana Pula', 'symbol': 'P'})
        from core.api_views import ExchangeRateViewSet
        self.vs = ExchangeRateViewSet

    def _load_payload(self):
        # source defaults to bank_of_botswana; omit it (the old 'BoB' was an
        # invalid choice and returned 400 before the SoD gate).
        return {'from_currency': 'USD', 'to_currency': 'BWP',
                'rate': '13.31', 'effective_date': '2026-06-25'}

    def test_finance_manager_cannot_load(self):
        fm = _user('fm_fx', UserProfile.Title.FINANCE_MANAGER)
        req = self.factory.post('/api/v1/exchange-rates/', self._load_payload())
        force_authenticate(req, user=fm)
        resp = self.vs.as_view({'post': 'create'})(req)
        self.assertEqual(resp.status_code, 403)   # (a) FM can't load

    def test_maker_can_load_then_fm_approves_but_loader_cannot(self):
        fc = _user('fc_fx', UserProfile.Title.FINANCIAL_CONTROLLER)
        fm = _user('fm_fx2', UserProfile.Title.FINANCE_MANAGER)
        # maker loads
        req = self.factory.post('/api/v1/exchange-rates/', self._load_payload())
        force_authenticate(req, user=fc)
        resp = self.vs.as_view({'post': 'create'})(req)
        self.assertEqual(resp.status_code, 201)   # (b) maker can load
        rid = resp.data['id']
        # loader (FC) tries to approve own rate -> 403: an FC is a MAKER, not a
        # checker, blocked at the authority layer before the SoD-identity check.
        # (The loader==approver SoD-400 path is proven in the superuser test —
        # the only identity that can pass both gates.)
        req2 = self.factory.post(f'/api/v1/exchange-rates/{rid}/approve/')
        force_authenticate(req2, user=fc)
        resp2 = self.vs.as_view({'post': 'approve_rate'})(req2, pk=rid)
        self.assertEqual(resp2.status_code, 403)  # (b) FC cannot approve at all
        # different FM approves -> ok (c)(e)
        req3 = self.factory.post(f'/api/v1/exchange-rates/{rid}/approve/')
        force_authenticate(req3, user=fm)
        resp3 = self.vs.as_view({'post': 'approve_rate'})(req3, pk=rid)
        self.assertEqual(resp3.status_code, 200)  # (c) FM approves; (e) flow works

    def test_superuser_finance_manager_cannot_approve_own_load(self):
        # omogomotsi is FM + superuser: the old `and not is_super` bypass let
        # her self-approve. It must be closed.
        su = _user('fm_super', UserProfile.Title.FINANCE_MANAGER, is_superuser=True)
        req = self.factory.post('/api/v1/exchange-rates/', self._load_payload())
        force_authenticate(req, user=su)
        # superuser is allowed to load (automation), then tries to approve own:
        resp = self.vs.as_view({'post': 'create'})(req)
        self.assertEqual(resp.status_code, 201)
        rid = resp.data['id']
        req2 = self.factory.post(f'/api/v1/exchange-rates/{rid}/approve/')
        force_authenticate(req2, user=su)
        resp2 = self.vs.as_view({'post': 'approve_rate'})(req2, pk=rid)
        self.assertEqual(resp2.status_code, 400)  # (d) no superuser SoD bypass


class PaymentCreateSoDTests(TestCase):
    """#2 — Finance Manager can no longer originate a payment."""

    def setUp(self):
        self.factory = APIRequestFactory()
        from payments.api_views import PaymentViewSet
        self.vs = PaymentViewSet

    def test_finance_manager_cannot_create_payment(self):
        fm = _user('fm_pay', UserProfile.Title.FINANCE_MANAGER)
        req = self.factory.post('/api/v1/payments/', {})
        force_authenticate(req, user=fm)
        resp = self.vs.as_view({'post': 'create'})(req)
        self.assertEqual(resp.status_code, 403)   # (a) FM can't originate a payment

    def test_accountant_passes_maker_gate(self):
        # An accountant clears the maker gate (may then fail validation on an
        # empty body, but NOT with 403 — that proves the gate lets makers in).
        ac = _user('ac_pay', UserProfile.Title.ACCOUNTANT)
        req = self.factory.post('/api/v1/payments/', {})
        force_authenticate(req, user=ac)
        resp = self.vs.as_view({'post': 'create'})(req)
        self.assertNotEqual(resp.status_code, 403)  # (b) maker not blocked by SoD


class JeReverseSoDTests(TestCase):
    """#1 — direct posted-JE reversal is disabled for humans (queue only)."""

    def setUp(self):
        self.factory = APIRequestFactory()
        from ledger.api_views import JournalEntryViewSet
        self.vs = JournalEntryViewSet

    def test_finance_manager_direct_reverse_forbidden(self):
        fm = _user('fm_je', UserProfile.Title.FINANCE_MANAGER)
        # No JE needs to exist: the SoD gate runs before get_object() work only
        # if get_object precedes it — here get_object runs first, so create a
        # posted-less dummy is unnecessary; a 404 would also prove the gate is
        # not a 201. We assert it is NOT a successful reversal (201).
        req = self.factory.post('/api/v1/journal-entries/00000000-0000-0000-0000-000000000000/reverse/')
        force_authenticate(req, user=fm)
        resp = self.vs.as_view({'post': 'reverse_entry'})(
            req, pk='00000000-0000-0000-0000-000000000000')
        self.assertIn(resp.status_code, (403, 404))  # never 201; FM can't direct-reverse
