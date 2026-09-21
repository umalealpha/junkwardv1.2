"""Tests for the RealPay → subrogation collections import."""
import io
import tempfile
from decimal import Decimal

from django.contrib.auth.models import User
from django.core.management import call_command
from django.test import TestCase

from claims.models import Subrogation, SubrogationReceipt
from core.models import Company

# Real RealPay successful-collections header (verified against the sample file).
HEADER = ('Installment Date,Merchant,ClientNumber,ClientName,ContractNumber,'
          'ContractSequence,InstSeq,InstallmentAmount,TotalAmount,Collected Amount,'
          'CurrentCycleHits,HitsAllowed(CurrentTracking),Tracking,Report Status,'
          'Current Status,Result,Client Bank')


def _csv(lines):
    f = tempfile.NamedTemporaryFile('w', suffix='.csv', delete=False, encoding='utf-8', newline='')
    f.write(HEADER + '\n')
    for ln in lines:
        f.write(ln + '\n')
    f.close()
    return f.name


class RealPayImportTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create(username='sys', is_superuser=True)
        cls.co = Company.objects.create(code='ADIC', name='Alpha Direct')
        cls.sub = Subrogation.objects.create(
            claim_reference='20180657', company=cls.co, third_party_name='Abel Kgowe',
            claim_paid_amount=Decimal('0'), expected_recovery=Decimal('5000'),
            created_by=cls.user)
        cls.subG = Subrogation.objects.create(
            claim_reference='G2025003787', company=cls.co, third_party_name='Emmanuel K',
            claim_paid_amount=Decimal('0'), expected_recovery=Decimal('3000'),
            created_by=cls.user)

    def _run(self, lines, commit=True):
        out = io.StringIO()
        f = _csv(lines)
        call_command('import_subrogation_realpay', f, *(['--commit'] if commit else []), stdout=out, stderr=out)
        return out.getvalue()

    def _row(self, merchant, client, collected, status='SUCCESSFUL', cseq='1', instseq='1', contract='C1'):
        return (f'4/18/2026 6:00,{merchant},{client},Third Party,{contract},{cseq},{instseq},'
                f'99,99,{collected},0,0,No Tracking,{status},{status},0,FNB Botswana')

    def test_matched_successful_collection_is_posted(self):
        self._run([self._row('Alpha Direct Third Parties', '20180657', '1500.00')])
        self.assertEqual(self.sub.receipts.count(), 1)
        r = self.sub.receipts.first()
        self.assertEqual(r.amount, Decimal('1500.00'))
        self.assertEqual(r.method, SubrogationReceipt.Method.REALPAY)

    def test_graphite_style_client_number_matches(self):
        self._run([self._row('Alpha Direct Third Parties', 'G2025003787', '800.00')])
        self.assertEqual(self.subG.receipts.count(), 1)

    def test_other_merchant_is_never_posted(self):
        """The sample export also carried Genric Insurance — those must not post."""
        self._run([self._row('Genric Insurance', '20180657', '9999.00')])
        self.assertEqual(SubrogationReceipt.objects.count(), 0)

    def test_unsuccessful_line_is_skipped(self):
        self._run([self._row('Alpha Direct Third Parties', '20180657', '0', status='FAILED')])
        self.assertEqual(SubrogationReceipt.objects.count(), 0)

    def test_unmatched_client_number_is_reported_not_guessed(self):
        out = self._run([self._row('Alpha Direct Third Parties', '99999999', '500.00')])
        self.assertEqual(SubrogationReceipt.objects.count(), 0)
        self.assertIn('UNMATCHED', out)

    def test_duplicate_instalment_line_rejected_on_rerun(self):
        line = self._row('Alpha Direct Third Parties', '20180657', '1500.00', cseq='7', instseq='3')
        self._run([line])
        self._run([line])   # same file again
        self.assertEqual(self.sub.receipts.count(), 1)

    def test_dry_run_writes_nothing(self):
        out = self._run([self._row('Alpha Direct Third Parties', '20180657', '1500.00')], commit=False)
        self.assertIn('Dry run', out)
        self.assertEqual(SubrogationReceipt.objects.count(), 0)

    def test_amount_recovered_reflects_realpay_receipt(self):
        self._run([self._row('Alpha Direct Third Parties', '20180657', '1500.00')])
        self.sub.refresh_from_db()
        self.assertEqual(self.sub.amount_recovered, Decimal('1500.00'))
        self.assertEqual(self.sub.outstanding_balance, Decimal('3500.00'))   # 5000 - 1500
