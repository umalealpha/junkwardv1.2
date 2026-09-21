"""Card statement upload + the missing-receipt report (CFO 2026-08-07).

This is the leg that makes the module worth building: without it nobody ever
learns which transactions were never accounted for.
"""
import datetime as dt
from decimal import Decimal

from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.utils import timezone
from rest_framework.test import APITestCase

from company_cards import services as svc
from company_cards.models import (CardSpend, CardStatement, CardStatementLine,
                                  CompanyCard)
from company_cards.statement_import import parse_card_statement
from core.models import Company, UserProfile

UPLOAD = '/api/v1/company-cards/statements/upload/'


def _csv(rows: str, header='Date,Description,Debit,Credit') -> SimpleUploadedFile:
    return SimpleUploadedFile('stmt.csv', (header + '\n' + rows).encode(),
                              content_type='text/csv')


class ParserTest(APITestCase):
    def test_it_reads_a_plain_bank_csv(self):
        f = _csv('2026-07-03,SANITAS GABORONE,549.99,\n'
                 '2026-07-11,ENGEN FUEL,1422.19,\n')
        rows = parse_card_statement(f)
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]['date'], dt.date(2026, 7, 3))
        self.assertEqual(rows[0]['amount'], Decimal('549.99'))
        self.assertIn('SANITAS', rows[0]['description'])

    def test_a_credit_is_not_a_spend(self):
        """A refund needs no receipt — only spend does."""
        f = _csv('2026-07-03,SANITAS,549.99,\n'
                 '2026-07-09,REFUND SANITAS,,549.99\n')
        rows = parse_card_statement(f)
        self.assertEqual(len(rows), 1)
        self.assertIn('SANITAS', rows[0]['description'])

    def test_a_preamble_above_the_header_is_skipped(self):
        f = SimpleUploadedFile('stmt.csv', (
            'FIRST NATIONAL BANK\nCard ending 4821\n\n'
            'Date,Description,Debit,Credit\n'
            '2026-07-03,SANITAS,549.99,\n').encode(), content_type='text/csv')
        rows = parse_card_statement(f)
        self.assertEqual(len(rows), 1)

    def test_an_unreadable_file_says_so_in_plain_english(self):
        bad = SimpleUploadedFile('x.csv', b'\xff\xfe\x00nonsense',
                                 content_type='text/csv')
        with self.assertRaises(ValueError) as ctx:
            parse_card_statement(bad)
        self.assertTrue(str(ctx.exception))


class MatchingTest(APITestCase):
    @classmethod
    def setUpTestData(cls):
        cls.co = Company.objects.create(code='CCMT', name='ADIC (match test)')
        cls.cfo = User.objects.create_user(
            'mt_cfo', 'pganesharajah@alphadirect.co.bw', 'x')
        UserProfile.objects.update_or_create(
            user=cls.cfo, defaults={'title': UserProfile.Title.CFO, 'is_active': True})
        cls.card = CompanyCard.objects.create(
            label='CFO Card', last4='4821', holder=cls.cfo, company=cls.co)

    def _spend(self, amount, when):
        return CardSpend.objects.create(
            card=self.card, uploaded_by=self.cfo, spent_on=when,
            amount=Decimal(amount), what_for='Test spend', currency='BWP')

    def _upload(self, rows):
        self.client.force_authenticate(self.cfo)
        return self.client.post(UPLOAD, {
            'card': str(self.card.id), 'year': 2026, 'month': 7,
            'file': _csv(rows)}, format='multipart')

    def test_a_receipt_is_matched_to_its_statement_line(self):
        self._spend('549.99', dt.date(2026, 7, 3))
        r = self._upload('2026-07-05,SANITAS,549.99,\n')
        self.assertEqual(r.status_code, 201, r.data)
        self.assertEqual(r.data['matched'], 1)
        self.assertEqual(r.data['unmatched'], 0)

    def test_a_transaction_with_no_receipt_is_named(self):
        r = self._upload('2026-07-05,SANITAS,549.99,\n'
                         '2026-07-06,MYSTERY SHOP,2000.00,\n')
        self.assertEqual(r.data['matched'], 0)
        self.assertEqual(r.data['unmatched'], 2)

        stmt = CardStatement.objects.get()
        self.client.force_authenticate(self.cfo)
        g = self.client.get(f'/api/v1/company-cards/statements/{stmt.id}/gaps/')
        self.assertEqual(g.status_code, 200, g.data)
        self.assertEqual(g.data['missing_count'], 2)
        self.assertIn('MYSTERY SHOP',
                      [m['description'] for m in g.data['missing']])

    def test_it_never_guesses_when_two_receipts_share_an_amount(self):
        """A wrong match marks a transaction as evidenced when nobody produced
        the receipt — the exact hole this module closes. Leave it to a human."""
        self._spend('100.00', dt.date(2026, 7, 3))
        self._spend('100.00', dt.date(2026, 7, 4))
        r = self._upload('2026-07-05,SHOP,100.00,\n')
        self.assertEqual(r.data['matched'], 0)
        self.assertEqual(r.data['ambiguous'], 1)
        self.assertEqual(r.data['unmatched'], 1)

    def test_a_spend_far_outside_the_window_is_not_matched(self):
        self._spend('549.99', dt.date(2026, 6, 1))
        r = self._upload('2026-07-05,SANITAS,549.99,\n')
        self.assertEqual(r.data['matched'], 0)

    def test_one_receipt_cannot_cover_two_statement_lines(self):
        self._spend('75.00', dt.date(2026, 7, 3))
        r = self._upload('2026-07-04,COFFEE,75.00,\n'
                         '2026-07-05,COFFEE,75.00,\n')
        self.assertEqual(r.data['matched'], 1)
        self.assertEqual(r.data['unmatched'], 1)

    def test_finance_can_waive_a_line_that_needs_no_receipt(self):
        self._upload('2026-07-31,CARD FEE,45.00,\n')
        line = CardStatementLine.objects.get()
        self.assertTrue(line.needs_receipt)
        self.client.force_authenticate(self.cfo)
        r = self.client.post(
            f'/api/v1/company-cards/statement-lines/{line.id}/waive/',
            {'note': 'Monthly card fee — no receipt exists.'}, format='json')
        self.assertEqual(r.status_code, 200, r.data)
        line.refresh_from_db()
        self.assertFalse(line.needs_receipt)

    def test_waiving_needs_a_reason(self):
        self._upload('2026-07-31,CARD FEE,45.00,\n')
        line = CardStatementLine.objects.get()
        self.client.force_authenticate(self.cfo)
        r = self.client.post(
            f'/api/v1/company-cards/statement-lines/{line.id}/waive/', {}, format='json')
        self.assertEqual(r.status_code, 400, r.data)

    def test_reloading_a_statement_keeps_the_matches_already_made(self):
        self._spend('549.99', dt.date(2026, 7, 3))
        self._upload('2026-07-05,SANITAS,549.99,\n')
        again = self._upload('2026-07-05,SANITAS,549.99,\n'
                             '2026-07-09,NEW LINE,300.00,\n')
        self.assertEqual(again.status_code, 201, again.data)
        self.assertEqual(CardStatement.objects.count(), 1)
        self.assertEqual(again.data['unmatched'], 1)

    def test_a_non_finance_user_cannot_load_a_statement(self):
        other = User.objects.create_user('mt_other', 'other@alphadirect.co.bw', 'x')
        self.client.force_authenticate(other)
        r = self.client.post(UPLOAD, {'card': str(self.card.id), 'year': 2026,
                                      'month': 7, 'file': _csv('2026-07-05,X,1.00,\n')},
                             format='multipart')
        self.assertEqual(r.status_code, 403, r.data)


class TheOverdueGateNeverAppliesHereTest(APITestCase):
    """CFO: uploading a receipt is a duty, not a request. Gating it would mean
    fewer receipts, which is the opposite of the point."""

    def test_uploading_raises_no_countersignature_even_with_overdue_work(self):
        from core.models import OmniTask
        from hris.exec_signoff_models import ExecSignoff
        co = Company.objects.create(code='CCNG', name='ADIC (no gate)')
        cfo = User.objects.create_user('ng_cfo', 'pganesharajah@alphadirect.co.bw', 'x')
        card = CompanyCard.objects.create(
            label='CFO Card', last4='4821', holder=cfo, company=co)
        OmniTask.objects.create(
            assigner=cfo, assignee=cfo, title='Very overdue',
            due_at=timezone.localdate() - dt.timedelta(days=30),
            status=OmniTask.Status.PENDING)

        self.client.force_authenticate(cfo)
        r = self.client.post('/api/v1/company-cards/spends/', {
            'card': str(card.id), 'amount': '100',
            'spent_on': timezone.localdate().isoformat(),
            'what_for': 'Fuel for the Gaborone trip'}, format='multipart')
        self.assertEqual(r.status_code, 201, r.data)
        self.assertEqual(ExecSignoff.objects.count(), 0)
        self.assertNotIn('needs_exec_signoff', r.data)


class FinanceScreensTest(APITestCase):
    """The two endpoints the Finance screen runs on (CFO 2026-08-07)."""

    @classmethod
    def setUpTestData(cls):
        cls.co = Company.objects.create(code='CCFS', name='ADIC (finance screens)')
        cls.cfo = User.objects.create_user('fs_cfo', 'pganesharajah@alphadirect.co.bw', 'x')
        UserProfile.objects.update_or_create(
            user=cls.cfo, defaults={'title': UserProfile.Title.CFO, 'is_active': True})
        cls.outsider = User.objects.create_user('fs_out', 'out@alphadirect.co.bw', 'x')
        cls.card = CompanyCard.objects.create(
            label='CFO Card', last4='0000', holder=cls.cfo, company=cls.co)

    def test_the_register_lists_the_cards(self):
        self.client.force_authenticate(self.cfo)
        r = self.client.get('/api/v1/company-cards/register/')
        self.assertEqual(r.status_code, 200, r.data)
        self.assertEqual(r.data['cards'][0]['last4'], '0000')

    def test_finance_can_set_the_last_four_digits(self):
        self.client.force_authenticate(self.cfo)
        r = self.client.patch('/api/v1/company-cards/register/',
                              {'id': str(self.card.id), 'last4': '4821'}, format='json')
        self.assertEqual(r.status_code, 200, r.data)
        self.card.refresh_from_db()
        self.assertEqual(self.card.last4, '4821')

    def test_a_full_card_number_is_refused(self):
        """Omni must never end up holding a PAN, even by accident."""
        self.client.force_authenticate(self.cfo)
        r = self.client.patch('/api/v1/company-cards/register/',
                              {'id': str(self.card.id), 'last4': '4111111111111111'},
                              format='json')
        self.assertEqual(r.status_code, 400, r.data)
        self.assertIn('LAST four', r.data['detail'])
        self.card.refresh_from_db()
        self.assertEqual(self.card.last4, '0000')

    def test_spaced_digits_are_accepted(self):
        self.client.force_authenticate(self.cfo)
        r = self.client.patch('/api/v1/company-cards/register/',
                              {'id': str(self.card.id), 'last4': '4 8 2 1'}, format='json')
        self.assertEqual(r.status_code, 200, r.data)
        self.card.refresh_from_db()
        self.assertEqual(self.card.last4, '4821')

    def test_an_outsider_cannot_read_or_change_the_register(self):
        self.client.force_authenticate(self.outsider)
        self.assertEqual(self.client.get('/api/v1/company-cards/register/').status_code, 403)
        self.assertEqual(self.client.patch(
            '/api/v1/company-cards/register/',
            {'id': str(self.card.id), 'last4': '9999'}, format='json').status_code, 403)

    def test_the_statement_list_counts_what_has_no_receipt(self):
        stmt = CardStatement.objects.create(
            card=self.card, period_year=2026, period_month=7, uploaded_by=self.cfo)
        CardStatementLine.objects.create(
            statement=stmt, posted_on=dt.date(2026, 7, 4), amount=Decimal('100'),
            description='NO RECEIPT')
        waived = CardStatementLine.objects.create(
            statement=stmt, posted_on=dt.date(2026, 7, 5), amount=Decimal('45'),
            description='CARD FEE')
        waived.waived = True
        waived.save(update_fields=['waived'])

        self.client.force_authenticate(self.cfo)
        r = self.client.get('/api/v1/company-cards/statements/')
        self.assertEqual(r.status_code, 200, r.data)
        row = r.data['statements'][0]
        self.assertEqual(row['total_lines'], 2)
        self.assertEqual(row['missing_count'], 1, 'a waived line must not count as missing')

    def test_an_outsider_cannot_see_the_statement_list(self):
        self.client.force_authenticate(self.outsider)
        self.assertEqual(self.client.get('/api/v1/company-cards/statements/').status_code, 403)
