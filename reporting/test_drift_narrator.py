"""Tests for the frozen-drift narrator (feature #4)."""
from datetime import date, datetime, timezone
from decimal import Decimal
from unittest import mock

from django.test import TestCase

from reporting import drift_narrator


class NarrateTests(TestCase):
    """_narrate is pure (no DB); AI-optional with a deterministic fallback."""

    def test_no_entries_gives_report_mapping_hint(self):
        drift = {'frozen': '125.00', 'actual': '130.00', 'diff_pct': '4.00'}
        out = drift_narrator._narrate('GWP', drift, [], use_ai=True)
        self.assertIn('GWP', out)
        self.assertIn('no posted journal entries', out.lower())

    def test_use_ai_false_returns_deterministic_sentence_not_blank(self):
        drift = {'frozen': '100.00', 'actual': '150.00', 'diff_pct': '50.00'}
        top = [{'entry_number': 'JE-9', 'entry_date': '2026-07-14',
                'account_code': '4100', 'amount_bwp': '50.00'}]
        out = drift_narrator._narrate('PAT', drift, top, use_ai=False)
        self.assertTrue(out.strip())
        self.assertIn('JE-9', out)

    def test_ai_failure_falls_back_to_deterministic_sentence(self):
        drift = {'frozen': '100.00', 'actual': '150.00', 'diff_pct': '50.00'}
        top = [{'entry_number': 'JE-2026-004512', 'entry_date': '2026-07-14',
                'account_code': '4100', 'amount_bwp': '50.00'}]
        with mock.patch('core.ai_assist.reasoning_complete', side_effect=RuntimeError('down')):
            out = drift_narrator._narrate('PAT', drift, top, use_ai=True)
        self.assertIn('JE-2026-004512', out)
        self.assertIn('PAT', out)

    def test_ai_success_returns_model_string(self):
        # reasoning_complete returns a STRING (not a dict) — regression guard for C2.
        drift = {'frozen': '100.00', 'actual': '150.00', 'diff_pct': '50.00'}
        top = [{'entry_number': 'JE-1', 'entry_date': '2026-07-14',
                'account_code': '4100', 'amount_bwp': '50.00'}]
        with mock.patch('core.ai_assist.reasoning_complete',
                        return_value='GWP rose because JE-1 booked new premium.'):
            out = drift_narrator._narrate('GWP', drift, top, use_ai=True)
        self.assertEqual(out, 'GWP rose because JE-1 booked new premium.')


class AccountsForLabelTests(TestCase):
    def test_unknown_label_returns_empty(self):
        self.assertEqual(drift_narrator._accounts_for_label('Nonsense').count(), 0)

    def test_known_labels_do_not_raise(self):
        for lbl in ('GWP', 'PAT', 'Total Assets', 'Cash & Bank'):
            self.assertGreaterEqual(drift_narrator._accounts_for_label(lbl).count(), 0)


class TopEntriesTests(TestCase):
    """DB-backed — guards C1 (lowercase 'posted' status) and H2 (company scoping)."""

    def setUp(self):
        from django.contrib.auth import get_user_model
        from core.models import Company
        from ledger.models import Account, Currency, JournalEntry, JournalEntryLine

        self.user = get_user_model().objects.create(username='drift-test-user')
        self.bwp, _ = Currency.objects.get_or_create(code='BWP', defaults={'name': 'Pula'})
        self.adic = Company.objects.create(code='ADIC', name='ADIC Test')
        self.other = Company.objects.create(code='OTHER', name='Other Co Test')
        self.rev = Account.objects.create(
            code='4100-TST', name='Premium Revenue', account_type=Account.AccountType.REVENUE,
        )
        # Dates must sit OUTSIDE ledger.locks.LOCKED_PERIODS (FY25 Jul-2024→Jun-2025
        # and FY26-9M Jul-2025→Mar-2026). The company code is 'ADIC', so a JE dated
        # inside those windows is refused by the historical-financials hard lock
        # (added in #107) before it is ever saved — which is why this test errored
        # from the day it was written. Apr-2026 onward is unlocked and the narrator
        # only cares that the entries fall in [from_date, to_date].
        self.frm, self.to = date(2026, 4, 1), date(2026, 6, 30)
        posted_at = datetime(2026, 5, 1, tzinfo=timezone.utc)

        def _je(num, company):
            je = JournalEntry.objects.create(
                entry_number=num, entry_date=date(2026, 5, 1),
                posted_date=posted_at, description='test',
                status=JournalEntry.Status.POSTED, company=company,
                created_by=self.user, currency_code=self.bwp,
            )
            JournalEntryLine.objects.create(
                journal_entry=je, account=self.rev,
                debit_bwp=Decimal('0'), credit_bwp=Decimal('9000000'),
            )
            return je

        self.adic_je = _je('JE-ADIC-1', self.adic)
        self.other_je = _je('JE-OTHER-1', self.other)
        # a DRAFT ADIC entry that must NOT be picked up (guards C1)
        draft = JournalEntry.objects.create(
            entry_number='JE-DRAFT-1', entry_date=date(2026, 5, 1),
            description='draft', status=JournalEntry.Status.DRAFT, company=self.adic,
            created_by=self.user, currency_code=self.bwp,
        )
        JournalEntryLine.objects.create(
            journal_entry=draft, account=self.rev,
            debit_bwp=Decimal('0'), credit_bwp=Decimal('99000000'),
        )

    def test_finds_posted_adic_entry_only(self):
        rows = drift_narrator._top_entries_for_drift(
            'GWP', self.frm, self.to, since=None, company_id=self.adic.id,
        )
        nums = {r['entry_number'] for r in rows}
        self.assertIn('JE-ADIC-1', nums)          # C1: lowercase 'posted' matches
        self.assertNotIn('JE-OTHER-1', nums)      # H2: other company excluded
        self.assertNotIn('JE-DRAFT-1', nums)      # draft excluded
        self.assertEqual(rows[0]['amount_bwp'], '9000000.00')


class BuildNarrativesTests(TestCase):
    def test_unknown_period_is_graceful(self):
        out = drift_narrator.build_drift_narratives('BOGUS_PERIOD', use_ai=False)
        self.assertTrue(out['all_ok'])
        self.assertEqual(out['narratives'], [])
