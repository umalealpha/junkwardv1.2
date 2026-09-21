"""
banking/test_integrity_watch.py

The 2026-09 duplicate-import defect, pinned.

Between June and 11-Sep-2026 the daily pull re-imported the same seven-day
window into a NEW statement every morning; 20,779 of 25,871 live bank lines
(80.4%) turned out to be copies and reconciliation could never match them.
These tests fail if the watcher stops noticing that shape.
"""
from __future__ import annotations

import datetime
from decimal import Decimal
from unittest import mock

from django.test import TestCase
from django.utils import timezone

from banking.integrity_watch import (
    check_duplicate_lines,
    check_balance_moved_without_transactions,
    compose_email,
    run,
)
from banking.models import BankAccount, BankStatement, BankStatementLine
from ledger.models import Account


class _Base(TestCase):
    def setUp(self):
        self.gl = Account.objects.create(
            code='1000', name='Bank', account_type='asset', is_bank_account=True)
        self.ba = BankAccount.objects.create(
            gl_account=self.gl, account_name='FNBB TEST CHEQ',
            account_number='62403392335', is_active=True)
        # Relative to TODAY, never a fixed calendar date: the checks look
        # back _TIE_DAYS from today, so a hard-coded date silently falls out
        # of the window and the test quietly stops testing anything.
        self.day = timezone.localdate() - datetime.timedelta(days=2)
        self.day2 = timezone.localdate() - datetime.timedelta(days=3)

    def _statement(self, statement_date, *, closing=Decimal('100.00'), lines=0):
        return BankStatement.objects.create(
            bank_account=self.ba, statement_date=statement_date,
            opening_balance=Decimal('0.00'), closing_balance=closing,
            file_name=f'test-{statement_date}', line_count=lines)

    def _line(self, statement, *, amount=Decimal('-410.73'),
              description='FNB OB PMT', reference='REF1', date=None):
        # An explicit dedupe_key is what the live duplicates actually have: the
        # key formula changed during 2026, so the same transaction re-imported
        # on two mornings ended up under two DIFFERENT keys and slid past the
        # unique constraint. Letting save() compute it here would instead
        # recreate today's (fixed) behaviour and the test could never model the
        # data this watcher exists to find.
        self._seq = getattr(self, '_seq', 0) + 1
        line_no = statement.lines.count() + 1   # (statement, line_number) is unique
        return BankStatementLine.objects.create(
            statement=statement, line_number=line_no,
            transaction_date=date or self.day, amount=amount,
            description=description, reference=reference, raw_data={},
            dedupe_key=f'test-key-{self._seq:032d}')


class DuplicateLineDetectionTests(_Base):

    def test_the_same_transaction_under_two_statements_is_reported(self):
        """The exact production defect: one day pulled twice on two mornings."""
        for d in (self.day, self.day2):
            self._line(self._statement(d))

        findings = check_duplicate_lines()

        self.assertEqual(len(findings), 1, findings)
        self.assertIn('…2335', findings[0])
        self.assertIn('more than', findings[0])

    def test_a_genuine_same_day_repeat_inside_one_statement_is_not_reported(self):
        """Two identical debit orders on one day are real. Flagging them would
        train everyone to ignore this alert, which is how the last watcher died."""
        stmt = self._statement(self.day)
        self._line(stmt)
        self._line(stmt)

        self.assertEqual(check_duplicate_lines(), [])

    def test_clean_data_reports_nothing(self):
        self._line(self._statement(self.day))
        self._line(self._statement(self.day2),
                   amount=Decimal('-99.00'), reference='REF2')

        self.assertEqual(check_duplicate_lines(), [])

    def test_the_account_number_is_masked_to_the_last_four_digits(self):
        """Full account numbers must never reach the AI or the email."""
        for d in (self.day, self.day2):
            self._line(self._statement(d))

        findings = check_duplicate_lines()

        self.assertNotIn('62403392335', findings[0])
        self.assertIn('…2335', findings[0])


class BalanceMovedWithoutTransactionsTests(_Base):

    def test_a_balance_that_moved_with_no_lines_is_reported(self):
        """Money went in or out and Omni holds nothing to explain it."""
        today = timezone.localdate()
        self._statement(today - datetime.timedelta(days=2),
                        closing=Decimal('100000.00'), lines=5)
        self._statement(today - datetime.timedelta(days=1),
                        closing=Decimal('95000.00'), lines=0)

        findings = check_balance_moved_without_transactions()

        self.assertEqual(len(findings), 1, findings)
        self.assertIn('…2335', findings[0])
        # The amount must NOT be in the finding: findings go verbatim to the
        # AI, and the module promises it never sees an amount.
        self.assertNotIn('5,000', findings[0])
        self.assertNotIn('BWP', findings[0])

    def test_a_quiet_sunday_is_not_reported(self):
        """The first live run of this check flagged 13-Sep and 20-Sep-2026 —
        both Sundays. An alert that fires every weekend is the alert everyone
        filters, and that is how the previous watcher was switched off."""
        today = timezone.localdate()
        self._statement(today - datetime.timedelta(days=2),
                        closing=Decimal('100000.00'), lines=5)
        self._statement(today - datetime.timedelta(days=1),
                        closing=Decimal('100000.00'), lines=0)

        self.assertEqual(check_balance_moved_without_transactions(), [])

    def test_a_day_with_lines_is_never_reported(self):
        today = timezone.localdate()
        self._statement(today - datetime.timedelta(days=2),
                        closing=Decimal('100000.00'), lines=5)
        self._statement(today - datetime.timedelta(days=1),
                        closing=Decimal('95000.00'), lines=3)

        self.assertEqual(check_balance_moved_without_transactions(), [])


class EmailTests(_Base):

    def test_no_findings_means_no_email(self):
        """A daily all-clear becomes an Outlook filter rule within a fortnight."""
        with mock.patch('banking.integrity_watch.send_with_cfo_cc') as send:
            result = run(skip_bank_tie=True)

        send.assert_not_called()
        self.assertEqual(result['findings'], 0)
        self.assertFalse(result['emailed'])

    def test_findings_email_kago_pako_and_keetile(self):
        for d in (self.day, self.day2):
            self._line(self._statement(d))

        with mock.patch('banking.integrity_watch.send_with_cfo_cc') as send, \
             mock.patch('banking.integrity_watch.reasoning_complete',
                        return_value='Rewritten by the AI.'):
            result = run(skip_bank_tie=True)

        send.assert_called_once()
        self.assertEqual(
            sorted(send.call_args.kwargs['to']),
            ['kmokhendo@alphadirect.co.bw',
             'ktshutlhedi@alphadirect.co.bw',
             'pkago@alphadirect.co.bw'])
        self.assertTrue(result['emailed'])

    def test_every_ai_engine_down_still_sends_the_findings(self):
        """The AI rewrites findings, it never produces them. An outage across
        DeepSeek, Gemini and the rest must not silence the alert.

        This asserts the email is actually SENT. The first version called
        compose_email directly and asserted on its return value, so it proved
        the fallback string existed and nothing about whether anyone was told —
        and it only covered DeepSeekUnavailable, the one exception already
        caught. Any other error out of the AI layer took the whole run down and
        lost the findings.
        """
        for d in (self.day, self.day2):
            self._line(self._statement(d))

        with mock.patch('banking.integrity_watch.send_with_cfo_cc',
                        return_value=1) as send, \
             mock.patch('banking.integrity_watch.reasoning_complete',
                        side_effect=RuntimeError('every engine is down')):
            result = run(skip_bank_tie=True)

        send.assert_called_once()
        self.assertTrue(result['emailed'])
        self.assertIn('…2335', send.call_args.kwargs['body'])

    def test_a_silently_unsent_email_is_not_recorded_as_sent(self):
        """send_with_cfo_cc returns 0 on a silent failure — it does not raise."""
        for d in (self.day, self.day2):
            self._line(self._statement(d))

        with mock.patch('banking.integrity_watch.send_with_cfo_cc',
                        return_value=0), \
             mock.patch('banking.integrity_watch.reasoning_complete',
                        return_value='x'):
            result = run(skip_bank_tie=True)

        self.assertFalse(result['emailed'])
        self.assertIn('failed', result['action'])

    def test_a_check_that_throws_is_reported_not_swallowed(self):
        """A silent watcher is worse than no watcher — that is this whole file."""
        def _boom():
            raise RuntimeError('boom')

        with mock.patch('banking.integrity_watch.CHECKS',
                        (('Duplicate bank lines', _boom),)), \
             mock.patch('banking.integrity_watch.send_with_cfo_cc'), \
             mock.patch('banking.integrity_watch.reasoning_complete',
                        return_value='x'):
            result = run(skip_bank_tie=True)

        self.assertEqual(result['findings'], 1)
        flat = [f for _, fs in result['results'] for f in fs]
        self.assertTrue(any('could not run' in f for f in flat), flat)


class HeadlineHonestyTests(_Base):
    """The opening line must describe what actually failed.

    On its first live run the summary opened "Omni's copy of the bank does not
    agree with the bank" above a finding that was purely about unconfirmed
    payment batches — the bank figures themselves were fine — and the AI
    faithfully repeated the false headline. One wrong headline and the finance
    team stops reading the rest.
    """

    def test_a_real_disagreement_does_say_so(self):
        from banking.integrity_watch import _plain_summary

        body = _plain_summary([
            ('Duplicate bank lines', ['Account …2335: 12 transactions.']),
        ])

        self.assertIn('does not agree', body)


class BankTieTests(_Base):
    """The check that does the actual comparison with the bank.

    It had NO tests at all in the first version — the one check that would
    have caught the duplicate-import defect on day one, and the only one that
    talks to FNB, was the only one nobody pinned.
    """

    def _client(self, per_day):
        """Fake FNBClient. per_day maps a date -> entry count, or an Exception."""
        class _Resp:
            def __init__(self, n): self.json = {'statement': {'entry': [{}] * n}}

        def _post(self_inner, *a, **kw):
            d = kw['json_body']['fromDate']
            outcome = per_day[d]
            if isinstance(outcome, BaseException):
                raise outcome
            return _Resp(outcome)
        return _post

    def setUp(self):
        super().setUp()
        # check_bank_tie only considers accounts that already have statements
        # (`statements__isnull=False`) — an account Omni has never pulled has
        # nothing to compare. _Base creates the account but no statement.
        self._statement(self.day)

    def _days(self, n=7):
        today = timezone.localdate()
        return [today - datetime.timedelta(days=b) for b in range(1, n + 1)]

    def test_a_day_where_the_bank_holds_more_than_omni_is_reported(self):
        from banking.integrity_watch import check_bank_tie
        days = self._days()
        per_day = {d.isoformat(): 0 for d in days}
        per_day[days[0].isoformat()] = 12          # bank says 12, Omni holds 0

        with mock.patch('fnb.client.FNBClient.post', self._client(per_day)):
            findings = check_bank_tie()

        self.assertEqual(len(findings), 1, findings)
        self.assertIn('does not agree', findings[0])
        self.assertIn('bank 12, Omni 0', findings[0])
        self.assertIn('…2335', findings[0])
        self.assertNotIn('62403392335', findings[0])

    def test_agreement_on_every_day_reports_nothing(self):
        from banking.integrity_watch import check_bank_tie
        per_day = {d.isoformat(): 0 for d in self._days()}

        with mock.patch('fnb.client.FNBClient.post', self._client(per_day)):
            self.assertEqual(check_bank_tie(), [])

    def test_days_the_bank_would_not_answer_are_never_reported_as_agreement(self):
        """Six silent days out of seven used to print "ok" and send no email —
        agreement the check had never established."""
        from banking.integrity_watch import check_bank_tie
        from fnb.client import FNBAPIError
        days = self._days()
        per_day = {d.isoformat(): FNBAPIError(503, 'no answer') for d in days}
        per_day[days[0].isoformat()] = 0           # exactly one day answered

        with mock.patch('fnb.client.FNBClient.post', self._client(per_day)):
            findings = check_bank_tie()

        self.assertEqual(len(findings), 1, findings)
        self.assertIn('only 1 of the last 7 days', findings[0])
        self.assertIn('6 could not be checked', findings[0])
        # and it must NOT flip the email headline: nothing was compared
        from banking.integrity_watch import COULD_NOT_RUN, _plain_summary
        self.assertTrue(findings[0].startswith(COULD_NOT_RUN), findings[0])
        self.assertNotIn('does not agree',
                         _plain_summary([('Omni vs the bank', findings)]))

    def test_no_watched_account_is_reported_not_silently_passed(self):
        from banking.integrity_watch import check_bank_tie
        BankAccount.objects.filter(pk=self.ba.pk).update(is_active=False)

        findings = check_bank_tie()

        self.assertEqual(len(findings), 1, findings)
        self.assertIn('nothing was compared', findings[0])
        from banking.integrity_watch import COULD_NOT_RUN
        self.assertTrue(findings[0].startswith(COULD_NOT_RUN), findings[0])


class ErroredCheckTests(_Base):
    """A check that ERRORED must never assert a disagreement nobody measured."""

    def test_an_errored_check_does_not_claim_the_bank_figures_disagree(self):
        from banking.integrity_watch import _plain_summary, COULD_NOT_RUN

        body = _plain_summary([
            ('Duplicate bank lines', [f'{COULD_NOT_RUN} OperationalError. Treat as unchecked.']),
        ])

        self.assertNotIn('does not agree', body)
        self.assertIn('needs a person', body)
        self.assertIn('OperationalError', body)


class OversubscribedAccountTests(TestCase):
    """An account with more queued against it than it holds.

    CFO /recc 20-Sep-2026: "tell me in the morning email", so he can move money
    from Call BEFORE he starts authorising. On the day he asked, Claims held
    BWP 256,229.74 against BWP 419,199.21 waiting on his phone.

    The danger in this check is not missing a shortfall — it is INVENTING one.
    A failed or stale read still hands back a real number (the last good
    figure), so a naive "is the floor negative" test would subtract today's
    commitments from last week's balance and email four people every morning
    about an account that is perfectly funded. Three of these six accounts are
    refused by FNB daily, so that is the normal case, not the edge case.
    """

    def _payload(self, *, status, balance, floor):
        return {'groups': [{'accounts': [{
            'label': 'Alpha Direct — Claims',
            'balance_status': status,
            'balance': balance,
            'projected_floor': floor,
        }]}]}

    def _run(self, payload):
        # The check imports build_balances_payload INSIDE the function, so the
        # patch has to land on the source module, not on integrity_watch.
        from banking.integrity_watch import check_claims_account_is_oversubscribed
        with mock.patch('banking.balances.build_balances_payload',
                        return_value=payload):
            return check_claims_account_is_oversubscribed()

    def test_a_real_shortfall_on_a_current_reading_is_reported(self):
        findings = self._run(self._payload(
            status='ok', balance='256229.74', floor='-162969.47'))
        self.assertEqual(len(findings), 1)
        self.assertIn('Claims', findings[0])

    def test_a_funded_account_is_not_reported(self):
        """The opposite direction — otherwise the check could be wired to fire
        on every account and this suite would still be green."""
        self.assertEqual(self._run(self._payload(
            status='ok', balance='500000.00', floor='80800.79')), [])

    def test_a_failed_read_never_invents_a_shortfall(self):
        """THE one that matters. resolve_account_state falls back to the last
        GOOD figure when today's read fails, so the floor is populated from a
        balance that is not today's. The Claims account is refused by FNB
        regularly; without this gate the alarm fires every morning off a figure
        getting older each day."""
        self.assertEqual(self._run(self._payload(
            status='failed', balance='120000.00', floor='-299199.21')), [])

    def test_a_stale_read_never_invents_a_shortfall(self):
        """Same shape, the other non-current status."""
        self.assertEqual(self._run(self._payload(
            status='stale', balance='120000.00', floor='-299199.21')), [])

    def test_an_unread_account_is_skipped(self):
        self.assertEqual(self._run(self._payload(
            status='never_read', balance=None, floor=None)), [])

    def test_no_amount_ever_reaches_the_finding(self):
        """Findings go VERBATIM to an external model to be rewritten, so a
        figure here leaves the company. A distinctive sentinel, not a round
        number that could coincide with something else."""
        import re
        findings = self._run(self._payload(
            status='ok', balance='987654.32', floor='-987654.32'))
        text = ' '.join(findings)
        self.assertTrue(findings)
        self.assertNotIn('987654', text)
        self.assertNotIn('987,654', text)
        self.assertIsNone(re.search(r'\d[\d,]*\.\d{2}', text),
                          f'a money-shaped figure reached the finding: {text!r}')


class HeadlineScopeTests(TestCase):
    """The opening line must describe what was actually MEASURED.

    This module has now had the same regression twice: a finding that was not
    about bank data flipped the headline to "Omni's copy of the bank does not
    agree with the bank", and the AI faithfully repeated it. One false headline
    and the finance team stops reading the rest.
    """

    def test_an_oversubscribed_account_does_not_claim_the_bank_disagrees(self):
        from banking.integrity_watch import _plain_summary
        summary = _plain_summary([
            ('Duplicate bank lines', []),
            ('Balance moved with no transactions', []),
            ('Omni vs the bank', []),
            ('An account cannot cover what is queued',
             ['Alpha Direct — Claims: more payments are approved than it holds.']),
        ])
        self.assertNotIn('does not agree', summary)
        self.assertIn('Claims', summary)

    def test_a_real_bank_disagreement_still_says_so(self):
        """The twin. A test that only pinned the negative would pass just as
        happily if the headline were deleted outright."""
        from banking.integrity_watch import _plain_summary
        summary = _plain_summary([
            ('Duplicate bank lines', ['Account ...2335: 12 duplicate lines.']),
        ])
        self.assertIn('does not agree', summary)
