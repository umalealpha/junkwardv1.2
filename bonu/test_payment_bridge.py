"""Phase 2 — raise a lawyer payment through the vault, ready to load to FNB.

The bridge must reuse the safe rails: a firm is paid only through an APPROVED
vaulted bank account, the raise needs a maker title, and a bill can't be raised
twice. The approval quorum + the FNB submit are existing, tested code — these
tests isolate the bridge's own logic (submit_for_approval is patched so the test
doesn't depend on the approval-policy fixtures).
"""
import datetime
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth.models import User
from django.test import TestCase
from rest_framework.test import APIRequestFactory, force_authenticate

from bonu.models import BonuInvoice, LawFirm
from bonu.payment_bridge import bonu_payables, link_firm_vendor, raise_bonu_payment
from core.models import Company, Currency, UserProfile


class PaymentBridgeTests(TestCase):
    def setUp(self):
        self.company = (Company.objects.filter(code='ADIC').first()
                        or Company.objects.create(code='ADIC', name='ADIC'))
        Currency.objects.get_or_create(code='BWP', defaults={'name': 'Pula', 'symbol': 'P'})
        self.firm = LawFirm.objects.create(name='Vault & Co', is_active=True)
        self.rf = APIRequestFactory()
        self.creator = self._user('setup.creator@x.bw', UserProfile.Title.ACCOUNTANT)

    def _user(self, email, title):
        u = User.objects.create_user(email.split('@')[0], email=email, password='x')
        UserProfile.objects.update_or_create(user=u, defaults={'title': title, 'is_active': True})
        return User.objects.get(pk=u.pk)

    def _bank(self):
        from ledger.models import Account
        return Account.objects.create(
            code='BONU-BANK-TEST', name='Test Bank', account_type='asset',
            sub_type='bank', currency_code_id='BWP', is_bank_account=True, is_active=True)

    def _contact(self):
        from billing.models import Contact
        return Contact.objects.create(company=self.company, contact_type='vendor',
                                      name='Vault & Co', currency_code_id='BWP')

    def _vba(self, contact, status='active'):
        from procurement.models import VendorBankAccount
        return VendorBankAccount.objects.create(
            contact=contact, bank_name='FNB', account_holder_name='Vault & Co',
            account_number='620000000', status=status, created_by=self.creator)

    def _invoice(self, firm=None, total='1000.00', number='INV-1'):
        return BonuInvoice.objects.create(
            firm=firm or self.firm, invoice_number=number,
            invoice_date=datetime.date(2026, 8, 1), total=Decimal(total),
            status=BonuInvoice.Status.APPROVED)

    def _post(self, view, body, user, **kw):
        req = self.rf.post('/x', body, format='json')
        force_authenticate(req, user=user)
        return view(req, **kw)

    def _link_ready_firm(self):
        c = self._contact()
        self.firm.vendor_contact = c
        self.firm.save(update_fields=['vendor_contact'])
        return c

    # ---- guards -------------------------------------------------------------
    def test_raise_needs_a_vault_link(self):
        acct = self._user('a@x.bw', UserProfile.Title.ACCOUNTANT)
        inv = self._invoice()
        r = self._post(raise_bonu_payment, {'bank_account_id': 'x'}, acct, invoice_id=str(inv.pk))
        self.assertEqual(r.status_code, 400, r.data)
        self.assertIn('not linked', r.data['detail'])

    def test_raise_needs_an_approved_bank(self):
        acct = self._user('a@x.bw', UserProfile.Title.ACCOUNTANT)
        c = self._link_ready_firm()
        self._vba(c, status='draft')       # present but NOT approved
        inv = self._invoice()
        r = self._post(raise_bonu_payment, {'bank_account_id': 'x'}, acct, invoice_id=str(inv.pk))
        self.assertEqual(r.status_code, 400, r.data)
        self.assertIn('APPROVED bank', r.data['detail'])

    def test_only_an_approved_bill_can_be_raised(self):
        acct = self._user('a@x.bw', UserProfile.Title.ACCOUNTANT)
        c = self._link_ready_firm()
        self._vba(c, status='active')
        bank = self._bank()
        inv = self._invoice(total='500.00')
        inv.status = BonuInvoice.Status.QUERIED   # under query — must NOT be payable
        inv.save(update_fields=['status'])
        r = self._post(raise_bonu_payment, {'bank_account_id': str(bank.pk)}, acct, invoice_id=str(inv.pk))
        self.assertEqual(r.status_code, 400, r.data)
        self.assertIn('APPROVED bill', r.data['detail'])

    def test_a_finance_non_maker_is_refused(self):
        auditor = self._user('aud@x.bw', UserProfile.Title.AUDITOR)  # can view, cannot originate
        inv = self._invoice()
        r = self._post(raise_bonu_payment, {}, auditor, invoice_id=str(inv.pk))
        self.assertEqual(r.status_code, 403, getattr(r, 'data', None))

    # ---- happy path + dedupe ------------------------------------------------
    def test_happy_path_builds_a_sent_payment_via_the_vault(self):
        from payments.models import Payment
        acct = self._user('a@x.bw', UserProfile.Title.ACCOUNTANT)
        c = self._link_ready_firm()
        vba = self._vba(c, status='active')
        bank = self._bank()
        inv = self._invoice(total='1500.00')
        with patch.object(Payment, 'submit_for_approval') as sub:
            r = self._post(raise_bonu_payment,
                           {'bank_account_id': str(bank.pk), 'payment_date': '2026-08-20'},
                           acct, invoice_id=str(inv.pk))
        self.assertEqual(r.status_code, 201, r.data)
        p = Payment.objects.get(pk=r.data['payment_id'])
        self.assertEqual(p.payment_type, Payment.PaymentType.SENT)
        self.assertEqual(p.vendor_bank_account_id, vba.pk)   # paid via the vault, not once-off
        self.assertEqual(p.contact_id, c.pk)
        self.assertEqual(p.amount, Decimal('1500.00'))
        sub.assert_called_once()                              # went into the approval queue

    def test_a_bill_cannot_be_raised_twice(self):
        from payments.models import Payment
        acct = self._user('a@x.bw', UserProfile.Title.ACCOUNTANT)
        c = self._link_ready_firm()
        self._vba(c, status='active')
        bank = self._bank()
        inv = self._invoice()
        with patch.object(Payment, 'submit_for_approval'):
            first = self._post(raise_bonu_payment, {'bank_account_id': str(bank.pk)}, acct, invoice_id=str(inv.pk))
            self.assertEqual(first.status_code, 201, first.data)
            second = self._post(raise_bonu_payment, {'bank_account_id': str(bank.pk)}, acct, invoice_id=str(inv.pk))
        self.assertEqual(second.status_code, 400)
        self.assertIn('already in the queue', second.data['detail'])

    # ---- link + list --------------------------------------------------------
    def test_link_firm_to_a_vendor_contact(self):
        acct = self._user('a@x.bw', UserProfile.Title.ACCOUNTANT)
        c = self._contact()
        r = self._post(link_firm_vendor, {'contact_id': str(c.pk)}, acct, firm_id=str(self.firm.pk))
        self.assertEqual(r.status_code, 200, r.data)
        self.firm.refresh_from_db()
        self.assertEqual(self.firm.vendor_contact_id, c.pk)

    def test_payables_shows_bills_and_readiness(self):
        acct = self._user('a@x.bw', UserProfile.Title.ACCOUNTANT)
        self._invoice()
        req = self.rf.get('/x')
        force_authenticate(req, user=acct)
        r = bonu_payables(req)
        self.assertEqual(r.status_code, 200)
        row = next(x for x in r.data['invoices'] if x['invoice_number'] == 'INV-1')
        self.assertFalse(row['linked'])       # not vaulted yet
        self.assertFalse(row['bank_ready'])
        self.assertIsNone(row['payment'])
