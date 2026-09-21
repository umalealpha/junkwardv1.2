import datetime as dt
from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from banking.models import BankAccount, BankStatement, BankStatementLine, BankMatchMemory
from core.models import Company, Currency
from ledger.models import Account
from payments.models import Payment
from billing.models import Contact
from . import match_memory


class MatchMemoryNormalizationTests(TestCase):
    def test_normalise_counterparty(self):
        desc = "PAYMENT TO Bob's Burgers REF: 12345-BB FROM FNB POS"
        expected = "BOB S BURGERS"
        self.assertEqual(match_memory.normalise_counterparty(desc), expected)
        desc_short = "EFT TO ACME"
        self.assertEqual(match_memory.normalise_counterparty(desc_short), "ACME")
        desc_empty = "PAYMENT"
        self.assertEqual(match_memory.normalise_counterparty(desc_empty), "")

    def test_normalise_payee(self):
        name = "ACME Corp (Pty) Ltd."
        expected = "ACME CORP PTY LTD"
        self.assertEqual(match_memory.normalise_payee(name), expected)


class MatchMemoryAndSuggestionTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        Currency.objects.get_or_create(
            code='BWP', defaults={'name': 'Botswana Pula', 'symbol': 'P'})
        cls.company = Company.objects.create(name='Test Co', code='TC')
        cls.other_company = Company.objects.create(name='Other Co', code='OC')
        cls.user = User.objects.create_superuser('mem_tester', 'mem@example.com', 'x')

        gl = Account.objects.create(
            code='1122', name='Memory test bank', account_type='asset',
            sub_type='test', is_active=True, is_bank_account=True,
            owner_company=cls.company)
        cls.bank_account = BankAccount.objects.create(
            gl_account=gl, bank_name='FNB', account_name='Mem test',
            account_number='000000999', currency_code_id='BWP')

        stmt = BankStatement.objects.create(
            bank_account=cls.bank_account,
            statement_date=dt.date(2026, 9, 18),
            opening_balance=Decimal('0.00'), closing_balance=Decimal('1000.00'),
        )
        cls.line = BankStatementLine.objects.create(
            statement=stmt, line_number=1,
            transaction_date=dt.date(2026, 9, 18),
            description='PAYMENT TO SPAR G-WEST', reference='SPAR',
            amount=Decimal('-250.00'))

        cls.contact1 = Contact.objects.create(name='Spar G-West', company=cls.company)
        cls.contact2 = Contact.objects.create(name='Shell', company=cls.company)
        
        # Payment with good deterministic match
        cls.payment_good = Payment.objects.create(
            payment_number='PAY-M001', payment_type=Payment.PaymentType.RECEIVED, bank_account=cls.bank_account.gl_account, currency_code_id='BWP', contact=cls.contact1, company=cls.company,
            payment_date=dt.date(2026, 9, 18), amount=Decimal('250.00'),
            status=Payment.Status.CONFIRMED, created_by=cls.user)
        
        # Payment with weaker deterministic match
        cls.payment_weak = Payment.objects.create(
            payment_number='PAY-M002', payment_type=Payment.PaymentType.RECEIVED, bank_account=cls.bank_account.gl_account, currency_code_id='BWP', contact=cls.contact2, company=cls.company,
            payment_date=dt.date(2026, 9, 10), amount=Decimal('250.00'),
            status=Payment.Status.CONFIRMED, created_by=cls.user)
            

    def setUp(self):
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)

    def test_remember_creates_and_increments_memory(self):
        self.assertEqual(BankMatchMemory.objects.count(), 0)
        match_memory.remember(self.line, self.payment_good, self.user)
        self.assertEqual(BankMatchMemory.objects.count(), 1)
        
        mem = BankMatchMemory.objects.first()
        self.assertEqual(mem.times_confirmed, 1)
        self.assertEqual(mem.counterparty_key, 'SPAR G WEST')
        self.assertEqual(mem.payee_key, 'SPAR G WEST')
        self.assertIsNotNone(mem.last_confirmed_at)
        self.assertEqual(mem.last_confirmed_by, self.user)

        # Confirming again should increment
        match_memory.remember(self.line, self.payment_good, self.user)
        mem.refresh_from_db()
        self.assertEqual(mem.times_confirmed, 2)

    def test_real_match_writes_memory(self):
        self.assertEqual(BankMatchMemory.objects.count(), 0)
        url = f'/api/v1/bank-statement-lines/{self.line.id}/match/'
        response = self.client.post(url, {'payment_id': self.payment_good.id}, format='json')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(BankMatchMemory.objects.count(), 1)
        self.assertEqual(BankMatchMemory.objects.first().times_confirmed, 1)

    def test_learning_failure_never_undoes_the_match(self):
        from unittest import mock
        url = f'/api/v1/bank-statement-lines/{self.line.id}/match/'
        with mock.patch('banking.match_memory.remember', side_effect=RuntimeError('boom')):
            response = self.client.post(url, {'payment_id': self.payment_good.id}, format='json')
        self.assertEqual(response.status_code, 200)
        self.line.refresh_from_db()
        self.assertEqual(self.line.matched_payment_id, self.payment_good.id)

    def test_long_names_are_cut_to_the_column_sizes(self):
        from banking.match_memory import normalise_counterparty, normalise_payee
        self.payment_good.contact.name = 'X' * 300
        match_memory.remember(self.line, self.payment_good, self.user)
        mem = BankMatchMemory.objects.get()
        self.assertLessEqual(len(mem.payee_key), 200)
        self.assertLessEqual(len(mem.counterparty_key), 120)

    def test_dry_run_does_not_write_memory(self):
        self.assertEqual(BankMatchMemory.objects.count(), 0)
        url = f'/api/v1/bank-statement-lines/{self.line.id}/match/'
        response = self.client.post(url, {'payment_id': self.payment_good.id, 'dry_run': True}, format='json')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(BankMatchMemory.objects.count(), 0)

    def test_suggestions_boosted_by_memory(self):
        # Good payment: same day, amount equal -> 90. Weak payment: 8 days apart -> 70.
        # After remembering good payment, its score should be higher.
        suggestions = match_memory.suggest(self.line, [self.payment_good, self.payment_weak])['suggestions']
        good_sugg = next(s for s in suggestions if s['payment_id'] == str(self.payment_good.id))
        self.assertEqual(good_sugg['deterministic_confidence'], 90)
        self.assertEqual(good_sugg['score'], 90)

        # Now, remember the good match
        match_memory.remember(self.line, self.payment_good, self.user)
        
        suggestions2 = match_memory.suggest(self.line, [self.payment_good, self.payment_weak])['suggestions']
        good_sugg2 = next(s for s in suggestions2 if s['payment_id'] == str(self.payment_good.id))
        weak_sugg2 = next(s for s in suggestions2 if s['payment_id'] == str(self.payment_weak.id))

        self.assertEqual(good_sugg2['score'], 94)  # 90 + 1*4
        self.assertEqual(good_sugg2['deterministic_confidence'], 90)  # shown side by side, unchanged
        self.assertEqual(weak_sugg2['score'], 70) # Unchanged
        self.assertTrue(suggestions2[0]['payment_id'] == str(self.payment_good.id))

    def test_disabled_memory_is_ignored(self):
        match_memory.remember(self.line, self.payment_good, self.user)
        mem = BankMatchMemory.objects.first()
        mem.disabled = True
        mem.save()

        suggestions = match_memory.suggest(self.line, [self.payment_good])['suggestions']
        self.assertEqual(suggestions[0]['score'], suggestions[0]['deterministic_confidence'])
        self.assertEqual(suggestions[0]['memory_hits'], 0)

    def test_cross_company_payment_not_suggested(self):
        other_contact = Contact.objects.create(name='Other Co Supplier', company=self.other_company)
        other_payment = Payment.objects.create(
            payment_number='PAY-M003', payment_type=Payment.PaymentType.RECEIVED, bank_account=self.bank_account.gl_account, currency_code_id='BWP', contact=other_contact, company=self.other_company,
            payment_date=dt.date(2026, 9, 18), amount=Decimal('250.00'),
            status=Payment.Status.CONFIRMED, created_by=self.user)
        
        suggestions = match_memory.suggest(self.line, [other_payment])['suggestions']
        self.assertEqual(len(suggestions), 0)

    def test_already_matched_payment_excluded_from_suggestions_endpoint(self):
        # Match the good payment
        self.line.matched_payment = self.payment_good
        self.line.save()

        # The candidate query for the endpoint should exclude it
        url = f'/api/v1/bank-statement-lines/{self.line.id}/suggestions/'
        line2 = BankStatementLine.objects.create(
            statement=self.line.statement, line_number=2,
            transaction_date=dt.date(2026, 9, 18),
            description='PAYMENT TO SPAR G-WEST 2', reference='SPAR2',
            amount=Decimal('-250.00'))
        
        url2 = f'/api/v1/bank-statement-lines/{line2.id}/suggestions/'
        response = self.client.get(url2)
        self.assertEqual(response.status_code, 200)
        
        payment_ids = [s['payment_id'] for s in response.data['suggestions']]
        self.assertNotIn(str(self.payment_good.id), payment_ids)

    def test_suggestions_endpoint_is_read_only_and_ok(self):
        url = f'/api/v1/bank-statement-lines/{self.line.id}/suggestions/'
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertIn('suggestions', response.data)
        self.assertIn('agrees_with_deterministic', response.data)
        self.assertEqual(BankMatchMemory.objects.count(), 0)
