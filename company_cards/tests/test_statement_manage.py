"""Manage a loaded card statement, and never import the same charge twice.

Reported by Laone Thebe 2026-09-11 (Company Cards): a statement filed against
the wrong month could not be moved or deleted, and nothing stopped the same
transactions being imported again.

The rules proved here:
  * A statement can be moved to another month, and to delete one you must be
    Finance.  The FILE and its lines move with it — nothing is re-imported, so
    a move can never duplicate a transaction.
  * Only one statement per card per month exists, so a move onto an occupied
    month is refused with a reason, never a silent overwrite.
  * Deleting a statement deletes its lines and NOTHING else.  A cardholder's
    receipt is evidence in its own right and survives.
  * A duplicate transaction is date + description + amount, ALL THREE, on the
    same card across EVERY statement.  Two out of three is a different charge
    and must still come in.
"""
import datetime as dt
from decimal import Decimal

from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from rest_framework.test import APITestCase

from company_cards.models import (CardSpend, CardStatement, CardStatementLine,
                                  CompanyCard)
from core.models import AuditLog, Company, UserProfile

UPLOAD = '/api/v1/company-cards/statements/upload/'
HEADER = 'Date,Description,Debit,Credit'


def _csv(rows: str, name='stmt.csv') -> SimpleUploadedFile:
    return SimpleUploadedFile(name, (HEADER + '\n' + rows).encode(),
                              content_type='text/csv')


class StatementManageTest(APITestCase):
    @classmethod
    def setUpTestData(cls):
        cls.co = Company.objects.create(code='CCSM', name='ADIC (stmt manage)')
        cls.fin = User.objects.create_user(
            'sm_cfo', 'pganesharajah@alphadirect.co.bw', 'x')
        UserProfile.objects.update_or_create(
            user=cls.fin,
            defaults={'title': UserProfile.Title.CFO, 'is_active': True})
        cls.other = User.objects.create_user('sm_clerk', 'clerk@alphadirect.co.bw', 'x')
        UserProfile.objects.update_or_create(
            user=cls.other,
            defaults={'title': UserProfile.Title.OPERATIONS, 'is_active': True})
        cls.card = CompanyCard.objects.create(
            label='CFO Card', last4='4821', holder=cls.fin, company=cls.co)

    def _upload(self, rows, month=7, year=2026, name='stmt.csv', **extra):
        self.client.force_authenticate(self.fin)
        return self.client.post(UPLOAD, {
            'card': str(self.card.id), 'year': year, 'month': month,
            'file': _csv(rows, name), **extra}, format='multipart')

    def _stmt(self, month=7, year=2026):
        return CardStatement.objects.get(card=self.card, period_year=year,
                                         period_month=month)

    # ── moving a statement to the right month ───────────────────────────────

    def test_a_statement_can_be_moved_to_the_month_it_belongs_to(self):
        self._upload('2026-08-05,ABC SUPERMARKET,450.00,\n', month=7)
        stmt = self._stmt(7)
        self.client.force_authenticate(self.fin)
        r = self.client.post(
            f'/api/v1/company-cards/statements/{stmt.id}/reallocate/',
            {'year': 2026, 'month': 8}, format='json')
        self.assertEqual(r.status_code, 200, r.data)
        stmt.refresh_from_db()
        self.assertEqual((stmt.period_year, stmt.period_month), (2026, 8))
        # The file and its lines came with it — nothing was re-read.
        self.assertEqual(stmt.lines.count(), 1)

    def test_moving_a_statement_imports_nothing_so_cannot_duplicate(self):
        self._upload('2026-08-05,ABC SUPERMARKET,450.00,\n'
                     '2026-08-06,FUEL STATION,300.00,\n', month=7)
        stmt = self._stmt(7)
        before = CardStatementLine.objects.filter(statement__card=self.card).count()
        self.client.force_authenticate(self.fin)
        self.client.post(f'/api/v1/company-cards/statements/{stmt.id}/reallocate/',
                         {'year': 2026, 'month': 8}, format='json')
        self.assertEqual(
            CardStatementLine.objects.filter(statement__card=self.card).count(),
            before)

    def test_it_refuses_to_move_onto_a_month_that_already_has_a_statement(self):
        self._upload('2026-07-05,ABC SUPERMARKET,450.00,\n', month=7)
        self._upload('2026-08-05,FUEL STATION,300.00,\n', month=8, name='aug.csv')
        july = self._stmt(7)
        self.client.force_authenticate(self.fin)
        r = self.client.post(
            f'/api/v1/company-cards/statements/{july.id}/reallocate/',
            {'year': 2026, 'month': 8}, format='json')
        self.assertEqual(r.status_code, 409, r.data)
        self.assertIn('already has a statement', r.data['detail'])
        july.refresh_from_db()
        self.assertEqual(july.period_month, 7)      # untouched, not overwritten

    def test_only_finance_may_move_a_statement(self):
        self._upload('2026-07-05,ABC SUPERMARKET,450.00,\n', month=7)
        stmt = self._stmt(7)
        self.client.force_authenticate(self.other)
        r = self.client.post(
            f'/api/v1/company-cards/statements/{stmt.id}/reallocate/',
            {'year': 2026, 'month': 8}, format='json')
        self.assertEqual(r.status_code, 403)
        stmt.refresh_from_db()
        self.assertEqual(stmt.period_month, 7)

    # ── deleting a mis-loaded statement ─────────────────────────────────────

    def test_deleting_a_statement_removes_its_lines_and_keeps_the_receipts(self):
        spend = CardSpend.objects.create(
            card=self.card, uploaded_by=self.fin, spent_on=dt.date(2026, 7, 3),
            amount=Decimal('549.99'), currency='BWP',
            what_for='Lunch with the broker while we went through the renewal')
        self._upload('2026-07-05,SANITAS,549.99,\n', month=7)
        stmt = self._stmt(7)
        self.assertTrue(stmt.lines.filter(matched_spend=spend).exists())

        self.client.force_authenticate(self.fin)
        r = self.client.delete(
            f'/api/v1/company-cards/statements/{stmt.id}/delete/')
        self.assertEqual(r.status_code, 200, r.data)
        self.assertFalse(CardStatement.objects.filter(pk=stmt.id).exists())
        self.assertEqual(CardStatementLine.objects.filter(statement=stmt).count(), 0)
        # The receipt is evidence in its own right — it survives.
        self.assertTrue(CardSpend.objects.filter(pk=spend.id).exists())
        self.assertEqual(r.data['receipts_kept'], 1)

    def test_only_finance_may_delete_a_statement(self):
        self._upload('2026-07-05,ABC SUPERMARKET,450.00,\n', month=7)
        stmt = self._stmt(7)
        self.client.force_authenticate(self.other)
        r = self.client.delete(
            f'/api/v1/company-cards/statements/{stmt.id}/delete/')
        self.assertEqual(r.status_code, 403)
        self.assertTrue(CardStatement.objects.filter(pk=stmt.id).exists())

    def test_both_actions_leave_an_audit_row(self):
        self._upload('2026-07-05,ABC SUPERMARKET,450.00,\n', month=7)
        stmt = self._stmt(7)
        self.client.force_authenticate(self.fin)
        self.client.post(f'/api/v1/company-cards/statements/{stmt.id}/reallocate/',
                         {'year': 2026, 'month': 8}, format='json')
        moved = AuditLog.objects.filter(
            table_name='company_cards_cardstatement', record_id=str(stmt.id),
            action=AuditLog.Action.UPDATE).first()
        self.assertIsNotNone(moved)
        self.assertEqual(moved.user, self.fin)
        self.assertEqual(moved.old_values['period'], '2026-07')
        self.assertEqual(moved.new_values['period'], '2026-08')

        self.client.delete(f'/api/v1/company-cards/statements/{stmt.id}/delete/')
        gone = AuditLog.objects.filter(
            table_name='company_cards_cardstatement', record_id=str(stmt.id),
            action=AuditLog.Action.DELETE).first()
        self.assertIsNotNone(gone)
        self.assertEqual(gone.user, self.fin)


class DuplicateTransactionTest(APITestCase):
    @classmethod
    def setUpTestData(cls):
        cls.co = Company.objects.create(code='CCDP', name='ADIC (dupes)')
        cls.fin = User.objects.create_user(
            'dp_cfo', 'pganesharajah@alphadirect.co.bw', 'x')
        UserProfile.objects.update_or_create(
            user=cls.fin,
            defaults={'title': UserProfile.Title.CFO, 'is_active': True})
        cls.card = CompanyCard.objects.create(
            label='CFO Card', last4='4821', holder=cls.fin, company=cls.co)

    def _upload(self, rows, month=7, name='stmt.csv', **extra):
        self.client.force_authenticate(self.fin)
        return self.client.post(UPLOAD, {
            'card': str(self.card.id), 'year': 2026, 'month': month,
            'file': _csv(rows, name), **extra}, format='multipart')

    def test_the_same_charge_in_another_month_is_not_imported_twice(self):
        self._upload('2026-09-05,ABC SUPERMARKET,450.00,\n', month=7)
        r = self._upload('2026-09-05,ABC SUPERMARKET,450.00,\n'
                         '2026-09-06,FUEL STATION,300.00,\n',
                         month=8, name='aug.csv')
        self.assertEqual(r.status_code, 201, r.data)
        self.assertEqual(r.data['total_transactions'], 2)
        self.assertEqual(r.data['new_transactions'], 1)
        self.assertEqual(r.data['duplicate_transactions'], 1)
        self.assertEqual(
            CardStatementLine.objects.filter(
                statement__card=self.card,
                description='ABC SUPERMARKET').count(), 1)

    def test_a_different_description_is_a_different_transaction(self):
        self._upload('2026-09-05,ABC SUPERMARKET,450.00,\n', month=7)
        r = self._upload('2026-09-05,FUEL STATION,450.00,\n',
                         month=8, name='aug.csv')
        self.assertEqual(r.data['new_transactions'], 1)
        self.assertEqual(r.data['duplicate_transactions'], 0)

    def test_a_different_date_is_a_different_transaction(self):
        self._upload('2026-09-05,ABC SUPERMARKET,450.00,\n', month=7)
        r = self._upload('2026-09-06,ABC SUPERMARKET,450.00,\n',
                         month=8, name='aug.csv')
        self.assertEqual(r.data['new_transactions'], 1)
        self.assertEqual(r.data['duplicate_transactions'], 0)

    def test_a_different_amount_is_a_different_transaction(self):
        self._upload('2026-09-05,ABC SUPERMARKET,450.00,\n', month=7)
        r = self._upload('2026-09-05,ABC SUPERMARKET,451.00,\n',
                         month=8, name='aug.csv')
        self.assertEqual(r.data['new_transactions'], 1)
        self.assertEqual(r.data['duplicate_transactions'], 0)

    def test_case_and_spacing_do_not_make_a_new_transaction(self):
        self._upload('2026-09-05,ABC SUPERMARKET,450.00,\n', month=7)
        r = self._upload('2026-09-05,  abc   supermarket ,450.00,\n',
                         month=8, name='aug.csv')
        self.assertEqual(r.data['duplicate_transactions'], 1)
        self.assertEqual(r.data['new_transactions'], 0)

    def test_450_and_450_point_00_are_the_same_amount(self):
        self._upload('2026-09-05,ABC SUPERMARKET,450,\n', month=7)
        r = self._upload('2026-09-05,ABC SUPERMARKET,450.00,\n',
                         month=8, name='aug.csv')
        self.assertEqual(r.data['duplicate_transactions'], 1)

    def test_the_same_file_uploaded_again_is_queried_not_processed(self):
        self._upload('2026-09-05,ABC SUPERMARKET,450.00,\n', month=7)
        r = self._upload('2026-09-05,ABC SUPERMARKET,450.00,\n',
                         month=8, name='stmt.csv')
        self.assertEqual(r.status_code, 409, r.data)
        self.assertIn('already been loaded', r.data['detail'])
        self.assertFalse(
            CardStatement.objects.filter(card=self.card, period_month=8).exists())

    def test_the_same_file_can_be_forced_through_and_still_skips_the_dupes(self):
        self._upload('2026-09-05,ABC SUPERMARKET,450.00,\n', month=7)
        r = self._upload('2026-09-05,ABC SUPERMARKET,450.00,\n',
                         month=8, name='stmt.csv', force='1')
        self.assertEqual(r.status_code, 201, r.data)
        self.assertEqual(r.data['duplicate_transactions'], 1)
        self.assertEqual(r.data['new_transactions'], 0)

    def test_re_uploading_the_same_month_does_not_double_its_lines(self):
        self._upload('2026-09-05,ABC SUPERMARKET,450.00,\n'
                     '2026-09-06,FUEL STATION,300.00,\n', month=7)
        self._upload('2026-09-05,ABC SUPERMARKET,450.00,\n'
                     '2026-09-06,FUEL STATION,300.00,\n', month=7, name='again.csv')
        self.assertEqual(
            CardStatementLine.objects.filter(statement__card=self.card).count(), 2)

    def test_the_summary_names_the_duplicates_it_skipped(self):
        # force=1 because an IDENTICAL file is stopped one step earlier by the
        # same-file check; this test is about the line-level summary.
        self._upload('2026-09-05,ABC SUPERMARKET,450.00,\n', month=7)
        r = self._upload('2026-09-05,ABC SUPERMARKET,450.00,\n',
                         month=8, name='aug.csv', force='1')
        self.assertEqual(len(r.data['duplicates']), 1)
        self.assertEqual(r.data['duplicates'][0]['description'], 'ABC SUPERMARKET')
        self.assertEqual(r.data['duplicates'][0]['posted_on'], '2026-09-05')

    def test_a_charge_on_another_card_is_not_a_duplicate(self):
        """Two people can genuinely buy the same thing on the same day."""
        other_card = CompanyCard.objects.create(
            label='COO Card', last4='9911', holder=self.fin, company=self.co)
        self._upload('2026-09-05,ABC SUPERMARKET,450.00,\n', month=7)
        self.client.force_authenticate(self.fin)
        r = self.client.post(UPLOAD, {
            'card': str(other_card.id), 'year': 2026, 'month': 7,
            'file': _csv('2026-09-05,ABC SUPERMARKET,450.00,\n', 'coo.csv')},
            format='multipart')
        self.assertEqual(r.status_code, 201, r.data)
        self.assertEqual(r.data['new_transactions'], 1)
        self.assertEqual(r.data['duplicate_transactions'], 0)
