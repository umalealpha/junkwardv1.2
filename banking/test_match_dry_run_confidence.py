"""CFO M6 (2026-09-18): searchable match picker with dry-run, confidence, and audit.

Tests for:
  * dry_run returns explanation without persisting
  * confidence calculation: 100/90/70 based on amount, date gap, reference match
  * entity scope: payment company must match statement's bank account company
  * audit logging: AuditLog row created on non-dry-run match
"""
from __future__ import annotations

import datetime as dt
from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase
from rest_framework.test import APIClient

from banking.api_views import compute_match_confidence
from banking.models import BankAccount, BankStatement, BankStatementLine
from core.models import AuditLog
from payments.models import Payment


class ConfidenceCalculationTests(TestCase):
    """Pure function testing for compute_match_confidence."""

    def test_100_percent_confidence_perfect_match(self):
        """100% when amounts equal + date gap ≤ 3 days + reference match."""
        confidence, explanation = compute_match_confidence(
            statement_amount=Decimal('1000.00'),
            candidate_amount=Decimal('1000.00'),
            date_gap_days=2,
            reference_match=True,
        )
        self.assertEqual(confidence, 100)
        self.assertTrue(explanation['amount_equal'])
        self.assertEqual(explanation['date_gap_days'], 2)
        self.assertTrue(explanation['reference_match'])

    def test_90_percent_confidence_amount_and_date_ok(self):
        """90% when amounts equal + date gap ≤ 7 days (ref doesn't matter)."""
        confidence, explanation = compute_match_confidence(
            statement_amount=Decimal('500.00'),
            candidate_amount=Decimal('500.00'),
            date_gap_days=5,
            reference_match=False,
        )
        self.assertEqual(confidence, 90)
        self.assertTrue(explanation['amount_equal'])
        self.assertEqual(explanation['date_gap_days'], 5)
        self.assertFalse(explanation['reference_match'])

    def test_70_percent_confidence_amount_only(self):
        """70% when amounts equal but date gap > 7 days."""
        confidence, explanation = compute_match_confidence(
            statement_amount=Decimal('250.00'),
            candidate_amount=Decimal('250.00'),
            date_gap_days=15,
            reference_match=False,
        )
        self.assertEqual(confidence, 70)
        self.assertTrue(explanation['amount_equal'])
        self.assertEqual(explanation['date_gap_days'], 15)

    def test_zero_confidence_amount_mismatch(self):
        """0% when amounts don't match."""
        confidence, explanation = compute_match_confidence(
            statement_amount=Decimal('1000.00'),
            candidate_amount=Decimal('999.00'),
            date_gap_days=1,
            reference_match=True,
        )
        self.assertEqual(confidence, 0)
        self.assertFalse(explanation['amount_equal'])

    def test_handles_negative_amounts(self):
        """Confidence uses abs() on amounts."""
        confidence, explanation = compute_match_confidence(
            statement_amount=Decimal('-500.00'),
            candidate_amount=Decimal('500.00'),
            date_gap_days=1,
            reference_match=True,
        )
        # abs(-500) == abs(500), so amount_equal should be true
        self.assertTrue(explanation['amount_equal'])


class MatchDryRunTests(TestCase):
    """Dry-run returns explanation without persisting."""

    @classmethod
    def setUpTestData(cls):
        from core.models import Company, Currency
        from ledger.models import Account
        from payments.models import Payment
        from billing.models import Contact

        Currency.objects.get_or_create(
            code='BWP', defaults={'name': 'Botswana Pula', 'symbol': 'P'})
        cls.company = Company.objects.create(name='Test Co', code='TC')
        cls.user = User.objects.create_superuser(
            'match_tester', 'match@example.com', 'x')

        # GL account + bank account
        gl = Account.objects.create(
            code='1121', name='Match test bank', account_type='asset',
            sub_type='test', is_active=True, is_bank_account=True,
            owner_company=cls.company)
        cls.bank_account = BankAccount.objects.create(
            gl_account=gl, bank_name='FNB', account_name='Match test',
            account_number='000000888', currency_code_id='BWP')

        # Statement with a line
        stmt = BankStatement.objects.create(
            bank_account=cls.bank_account,
            statement_date=dt.date(2026, 9, 15),
            opening_balance=Decimal('0.00'),
            closing_balance=Decimal('1000.00'),
        )
        cls.line = BankStatementLine.objects.create(
            statement=stmt, line_number=1,
            transaction_date=dt.date(2026, 9, 15),
            description='Customer refund', reference='REF-001',
            amount=Decimal('500.00'))

        # Confirmed payment
        contact = Contact.objects.create(
            name='Test Customer', contact_type='customer',
            company=cls.company)
        cls.payment = Payment.objects.create(
            payment_number='PAY-001', payment_type=Payment.PaymentType.RECEIVED,
            contact=contact, company=cls.company,
            bank_account=cls.bank_account.gl_account,
            payment_date=dt.date(2026, 9, 15),
            amount=Decimal('500.00'),
            currency_code_id='BWP',
            reference='REF-001',
            status=Payment.Status.CONFIRMED,
            created_by=cls.user)

    def setUp(self):
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)

    def test_dry_run_returns_explanation(self):
        """dry_run=true returns explanation dict."""
        resp = self.client.post(
            f'/api/v1/bank-statement-lines/{self.line.id}/match/',
            {'payment_id': str(self.payment.id), 'dry_run': True},
            format='json')
        self.assertEqual(resp.status_code, 200, resp.content)
        body = resp.json()
        self.assertIn('dry_run', body)
        self.assertTrue(body['dry_run'])
        self.assertIn('explanation', body)
        self.assertIn('confidence', body['explanation'])

    def test_dry_run_does_not_persist(self):
        """dry_run=true does not change the line in the database."""
        self.assertIsNone(self.line.matched_payment_id)
        self.assertEqual(self.line.match_status, BankStatementLine.MatchStatus.UNMATCHED)

        self.client.post(
            f'/api/v1/bank-statement-lines/{self.line.id}/match/',
            {'payment_id': str(self.payment.id), 'dry_run': True},
            format='json')

        # Verify nothing changed in DB
        self.line.refresh_from_db()
        self.assertIsNone(self.line.matched_payment_id)
        self.assertEqual(self.line.match_status, BankStatementLine.MatchStatus.UNMATCHED)

    def test_dry_run_no_audit_log(self):
        """dry_run=true does not create ANY AuditLog row."""
        audit_count_before = AuditLog.objects.count()

        self.client.post(
            f'/api/v1/bank-statement-lines/{self.line.id}/match/',
            {'payment_id': str(self.payment.id), 'dry_run': True},
            format='json')

        audit_count_after = AuditLog.objects.count()
        self.assertEqual(audit_count_before, audit_count_after)

    def test_dry_run_validates_amount_mismatch(self):
        """dry_run still validates (no mock save of bad data)."""
        # Create a payment with a different amount
        from billing.models import Contact
        contact = Contact.objects.create(
            name='Mismatch Test', contact_type='customer',
            company=self.company)
        mismatch_payment = Payment.objects.create(
            payment_number='PAY-002', payment_type=Payment.PaymentType.RECEIVED,
            contact=contact, company=self.company,
            bank_account=self.bank_account.gl_account,
            payment_date=dt.date(2026, 9, 15),
            amount=Decimal('999.00'),  # Different!
            currency_code_id='BWP',
            status=Payment.Status.CONFIRMED,
            created_by=self.user)

        resp = self.client.post(
            f'/api/v1/bank-statement-lines/{self.line.id}/match/',
            {'payment_id': str(mismatch_payment.id), 'dry_run': True},
            format='json')
        self.assertEqual(resp.status_code, 400, resp.content)
        self.assertIn('Amount mismatch', resp.json()['error'])


class MatchConfidencePersistenceTests(TestCase):
    """Real match stores computed confidence in line.match_confidence."""

    @classmethod
    def setUpTestData(cls):
        from core.models import Company, Currency
        from ledger.models import Account
        from payments.models import Payment
        from billing.models import Contact

        Currency.objects.get_or_create(code='BWP', defaults={'name': 'Botswana Pula', 'symbol': 'P'})
        cls.company = Company.objects.create(name='Conf Test', code='CT')
        cls.user = User.objects.create_superuser('conf_tester', 'conf@example.com', 'x')

        gl = Account.objects.create(
            code='1122', name='Conf test bank', account_type='asset',
            sub_type='test', is_active=True, is_bank_account=True,
            owner_company=cls.company)
        cls.bank_account = BankAccount.objects.create(
            gl_account=gl, bank_name='FNB', account_name='Conf test',
            account_number='000000889', currency_code_id='BWP')

        stmt = BankStatement.objects.create(
            bank_account=cls.bank_account,
            statement_date=dt.date(2026, 9, 15),
            opening_balance=Decimal('0.00'),
            closing_balance=Decimal('1000.00'),
        )
        cls.line = BankStatementLine.objects.create(
            statement=stmt, line_number=1,
            transaction_date=dt.date(2026, 9, 15),
            description='Test', reference='REF-X',
            amount=Decimal('500.00'))

        contact = Contact.objects.create(
            name='Test', contact_type='customer', company=cls.company)
        cls.payment = Payment.objects.create(
            payment_number='PAY-X', payment_type=Payment.PaymentType.RECEIVED,
            contact=contact, company=cls.company,
            bank_account=cls.bank_account.gl_account,
            payment_date=dt.date(2026, 9, 15),
            amount=Decimal('500.00'),
            currency_code_id='BWP',
            reference='REF-X',
            status=Payment.Status.CONFIRMED,
            created_by=cls.user)

    def setUp(self):
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)

    def test_real_match_with_wrong_amount_is_refused_and_unchanged(self):
        wrong = Payment.objects.create(
            payment_number='PAY-W', payment_type=Payment.PaymentType.RECEIVED,
            contact=self.payment.contact, company=self.company,
            bank_account=self.bank_account.gl_account,
            payment_date=dt.date(2026, 9, 15), amount=Decimal('501.00'),
            currency_code_id='BWP', status=Payment.Status.CONFIRMED,
            created_by=self.user)
        resp = self.client.post(
            f'/api/v1/bank-statement-lines/{self.line.id}/match/',
            {'payment_id': str(wrong.id)}, format='json')
        self.assertEqual(resp.status_code, 400, resp.content)
        self.line.refresh_from_db()
        self.assertIsNone(self.line.matched_payment_id)

    def test_a_reference_match_scores_above_the_old_default_path(self):
        """Same amount, same day, but NO reference overlap scores 90 — so the
        100 in the next test is earned by the reference, not hard-coded."""
        other = Payment.objects.create(
            payment_number='PAY-N', payment_type=Payment.PaymentType.RECEIVED,
            contact=self.payment.contact, company=self.company,
            bank_account=self.bank_account.gl_account,
            payment_date=dt.date(2026, 9, 15), amount=Decimal('500.00'),
            reference='UNRELATED', currency_code_id='BWP',
            status=Payment.Status.CONFIRMED, created_by=self.user)
        self.client.post(f'/api/v1/bank-statement-lines/{self.line.id}/match/',
                         {'payment_id': str(other.id)}, format='json')
        self.line.refresh_from_db()
        self.assertEqual(self.line.match_confidence, 90)

    def test_real_match_stores_confidence(self):
        """Non-dry-run match stores computed confidence."""
        resp = self.client.post(
            f'/api/v1/bank-statement-lines/{self.line.id}/match/',
            {'payment_id': str(self.payment.id)},
            format='json')
        self.assertEqual(resp.status_code, 200, resp.content)

        self.line.refresh_from_db()
        # Should have computed 100 (amount=, date gap=0, ref match)
        self.assertEqual(self.line.match_confidence, 100)
        self.assertEqual(self.line.matched_payment_id, self.payment.id)
        self.assertEqual(self.line.match_status, BankStatementLine.MatchStatus.MANUALLY_MATCHED)


class EntityScopeTests(TestCase):
    """Payment/JE company must match statement's bank account company."""

    @classmethod
    def setUpTestData(cls):
        from core.models import Company, Currency
        from ledger.models import Account
        from payments.models import Payment
        from billing.models import Contact

        Currency.objects.get_or_create(code='BWP', defaults={'name': 'Botswana Pula', 'symbol': 'P'})
        cls.company1 = Company.objects.create(name='Company A', code='CA')
        cls.company2 = Company.objects.create(name='Company B', code='CB')
        cls.user = User.objects.create_superuser('entity_tester', 'entity@example.com', 'x')

        # Bank account for company1
        gl1 = Account.objects.create(
            code='1123', name='Entity test bank 1', account_type='asset',
            sub_type='test', is_active=True, is_bank_account=True,
            owner_company=cls.company1)
        cls.bank_account1 = BankAccount.objects.create(
            gl_account=gl1, bank_name='FNB', account_name='Entity test 1',
            account_number='000000890', currency_code_id='BWP')

        stmt = BankStatement.objects.create(
            bank_account=cls.bank_account1,
            statement_date=dt.date(2026, 9, 15),
            opening_balance=Decimal('0.00'),
            closing_balance=Decimal('1000.00'),
        )
        cls.line = BankStatementLine.objects.create(
            statement=stmt, line_number=1,
            transaction_date=dt.date(2026, 9, 15),
            description='Test', reference='REF-E',
            amount=Decimal('500.00'))

        # Payment for company2 (cross-company!)
        contact = Contact.objects.create(
            name='Cross-Co', contact_type='customer', company=cls.company2)
        cls.cross_company_payment = Payment.objects.create(
            payment_number='PAY-CC', payment_type=Payment.PaymentType.RECEIVED,
            contact=contact, company=cls.company2,
            bank_account=cls.bank_account1.gl_account,
            payment_date=dt.date(2026, 9, 15),
            amount=Decimal('500.00'),
            currency_code_id='BWP',
            status=Payment.Status.CONFIRMED,
            created_by=cls.user)

        # Payment for company1 (correct)
        contact1 = Contact.objects.create(
            name='Same-Co', contact_type='customer', company=cls.company1)
        cls.same_company_payment = Payment.objects.create(
            payment_number='PAY-SC', payment_type=Payment.PaymentType.RECEIVED,
            contact=contact1, company=cls.company1,
            bank_account=cls.bank_account1.gl_account,
            payment_date=dt.date(2026, 9, 15),
            amount=Decimal('500.00'),
            currency_code_id='BWP',
            status=Payment.Status.CONFIRMED,
            created_by=cls.user)

    def setUp(self):
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)

    def test_cross_company_payment_rejected(self):
        """Payment from different company rejected with 400."""
        resp = self.client.post(
            f'/api/v1/bank-statement-lines/{self.line.id}/match/',
            {'payment_id': str(self.cross_company_payment.id)},
            format='json')
        self.assertEqual(resp.status_code, 400, resp.content)
        self.assertIn('Entity mismatch', resp.json()['error'])

    def test_same_company_payment_accepted(self):
        """Payment from same company accepted."""
        resp = self.client.post(
            f'/api/v1/bank-statement-lines/{self.line.id}/match/',
            {'payment_id': str(self.same_company_payment.id)},
            format='json')
        self.assertEqual(resp.status_code, 200, resp.content)


class AuditLoggingTests(TestCase):
    """Real match logs to AuditLog."""

    @classmethod
    def setUpTestData(cls):
        from core.models import Company, Currency
        from ledger.models import Account
        from payments.models import Payment
        from billing.models import Contact

        Currency.objects.get_or_create(code='BWP', defaults={'name': 'Botswana Pula', 'symbol': 'P'})
        cls.company = Company.objects.create(name='Audit Test', code='AT')
        cls.user = User.objects.create_superuser('audit_tester', 'audit@example.com', 'x')

        gl = Account.objects.create(
            code='1124', name='Audit test bank', account_type='asset',
            sub_type='test', is_active=True, is_bank_account=True,
            owner_company=cls.company)
        cls.bank_account = BankAccount.objects.create(
            gl_account=gl, bank_name='FNB', account_name='Audit test',
            account_number='000000891', currency_code_id='BWP')

        stmt = BankStatement.objects.create(
            bank_account=cls.bank_account,
            statement_date=dt.date(2026, 9, 15),
            opening_balance=Decimal('0.00'),
            closing_balance=Decimal('1000.00'),
        )
        cls.line = BankStatementLine.objects.create(
            statement=stmt, line_number=1,
            transaction_date=dt.date(2026, 9, 15),
            description='Test', reference='REF-A',
            amount=Decimal('500.00'))

        contact = Contact.objects.create(
            name='Test', contact_type='customer', company=cls.company)
        cls.payment = Payment.objects.create(
            payment_number='PAY-A', payment_type=Payment.PaymentType.RECEIVED,
            contact=contact, company=cls.company,
            bank_account=cls.bank_account.gl_account,
            payment_date=dt.date(2026, 9, 15),
            amount=Decimal('500.00'),
            currency_code_id='BWP',
            reference='REF-A',
            status=Payment.Status.CONFIRMED,
            created_by=cls.user)

    def setUp(self):
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)

    def test_audit_log_created_on_real_match(self):
        """Real match writes AuditLog row."""
        mine = AuditLog.objects.filter(table_name='banking.BankStatementLine',
                                       description__startswith='Manually matched')
        audit_count_before = mine.count()

        resp = self.client.post(
            f'/api/v1/bank-statement-lines/{self.line.id}/match/',
            {'payment_id': str(self.payment.id)},
            format='json')
        self.assertEqual(resp.status_code, 200, resp.content)

        audit_count_after = mine.count()
        self.assertEqual(audit_count_after, audit_count_before + 1)

        # Check the audit log content
        log = mine.latest('created_at')
        self.assertEqual(log.table_name, 'banking.BankStatementLine')
        self.assertEqual(log.action, AuditLog.Action.UPDATE)
        self.assertEqual(log.user, self.user)
        self.assertIn('confidence', log.new_values)
        self.assertIn('Manually matched', log.description)
