"""banking/test_bank_balances.py — Morning Bank Balances.

Six behaviours are pinned here, and each one exists because the live system
got it wrong on 20-Sep-2026:

1.  A bank payload with NO balance block must record ``closing is None``.
    Today ``fnb.statements._persist_statement`` starts at ``Decimal('0.00')``
    and only overwrites on an OPBD/CLBD block, so the Claims account
    (62493282265) reports a confident ``P0.00`` while the bank actually holds
    roughly P256,000. "Unknown" and "genuinely empty" must not share a value.
2.  A REAL zero still reads ``0.00`` with outcome ``ok`` — the fix above must
    not turn an empty account into an unknown one.
3.  A never-read account still appears, with ``balance: null`` and a note.
    The CFO asked explicitly to see what is not rendering.
4.  The credit card 4901344312871000 is NOT watched. It is a card, not a bank
    account; it is in today's daily pull list by name match and fails with
    HTTP 400 every morning.
5.  A 27-hour-old reading is ``stale``, never ``ok`` — an old figure shown as
    current is the failure mode this whole feature exists to prevent.
6.  A failed latest read with a good earlier one returns the OLDER figure
    under status ``failed`` — visible, and visibly not current.
"""
from __future__ import annotations

import datetime as dt
from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIRequestFactory, force_authenticate

from banking.models import BankAccount, BankStatement

# banking.balances and banking.models.BankBalanceSnapshot are imported inside
# the tests, not at module level: before they exist, a module-level import
# would stop the whole file from loading and hide the one red that CAN be
# seen against today's code — the false P0.00 in BalanceBlockParsingTests.


CARD_NUMBER = '4901344312871000'

# The six the contract names, in the contract's order.
SIX = [
    ('62403392335', 'ADIC', 'FNBB 62403392335 CHEQ A/C'),
    ('62493282265', 'ADIC', 'FNBB (Claims A/C)-62493282265'),
    ('62407809485', 'ADIC', 'FNBB (Call A/C) - 62407809485'),
    ('62477843132', 'VCM',  'Veritas Capital Mgmt'),
    ('62477854999', 'RSA',  'Risk Software Africa'),
    ('62842621725', 'UNI',  'Unicoin - Current AC'),
]


class _BalancesBase(TestCase):
    """Builds the six watched accounts plus the unwatched credit card."""

    @classmethod
    def setUpTestData(cls):
        from core.models import Company, Currency
        from ledger.models import Account

        Currency.objects.get_or_create(
            code='BWP', defaults={'name': 'Botswana Pula', 'symbol': 'P'})
        cls.user = User.objects.create_superuser(
            'balances_tester', 'balances@example.com', 'x')

        cls.companies = {}
        for code, name in (('ADIC', 'Alpha Direct Insurance'),
                           ('VCM',  'Veritas Capital'),
                           ('RSA',  'Risk Software Africa'),
                           ('UNI',  'Unicoin')):
            cls.companies[code] = Company.objects.get_or_create(
                code=code, defaults={'name': name})[0]

        cls.accounts = {}
        for i, (number, company_code, account_name) in enumerate(SIX):
            gl = Account.objects.create(
                code=f'11{i:02d}-BAL', name=f'Bank clearing {number}',
                account_type='asset', sub_type='test',
                is_active=True, is_bank_account=True,
                owner_company=cls.companies[company_code],
            )
            cls.accounts[number] = BankAccount.objects.create(
                gl_account=gl, bank_name='First National Bank Botswana',
                account_name=account_name, account_number=number,
                fnb_balance_watch=True, fnb_statement_pull=True,
            )

        # The credit card: present, active, name matches "FNB" — and must be
        # left out of every watched set.
        card_gl = Account.objects.create(
            code='1199-BAL', name='Card control', account_type='asset',
            sub_type='test', is_active=True, is_bank_account=True,
            owner_company=cls.companies['ADIC'],
        )
        cls.card = BankAccount.objects.create(
            gl_account=card_gl, bank_name='(unset — edit in /bank-accounts)',
            account_name=f'FNBB Credit Card Control A/C - {CARD_NUMBER}',
            account_number=CARD_NUMBER,
        )

    # ── helpers ─────────────────────────────────────────────────────────────
    def snapshot(self, number, *, closing, hours_ago=1.0, outcome='ok',
                 opening=None, error_text='', source='scheduled'):
        from banking.models import BankBalanceSnapshot
        taken = timezone.now() - dt.timedelta(hours=hours_ago)
        return BankBalanceSnapshot.objects.create(
            bank_account=self.accounts[number],
            taken_at=taken,
            as_of_date=timezone.localdate(),
            opening=opening,
            closing=closing,
            outcome=outcome,
            error_text=error_text,
            source=source,
        )

    @staticmethod
    def payload():
        from banking.balances import build_balances_payload
        return build_balances_payload()

    def account_row(self, payload, number):
        masked = '…' + number[-4:]
        for group in payload['groups']:
            for row in group['accounts']:
                if row['account_masked'] == masked:
                    return row
        raise AssertionError(f'account {masked} missing from the payload')


# ---------------------------------------------------------------------------
# 1 + 2 — a missing balance is unknown; a real zero is a real zero
# ---------------------------------------------------------------------------

class BalanceBlockParsingTests(TestCase):
    """fnb.statements.extract_balances / _persist_statement."""

    NO_BALANCE_PAYLOAD = {
        'statement': {
            'account': {'accountNumber': '62493282265', 'currency': 'BWP'},
            # No `balance` key at all — exactly what the Claims account returns.
            'entry': [],
        }
    }

    ZERO_BALANCE_PAYLOAD = {
        'statement': {
            'account': {'accountNumber': '62493282265', 'currency': 'BWP'},
            'balance': [
                {'typeCode': 'OPBD', 'amountValue': 0,
                 'creditDebitIndicator': 'Credit'},
                {'typeCode': 'CLBD', 'amountValue': 0,
                 'creditDebitIndicator': 'Credit'},
            ],
            'entry': [],
        }
    }

    REAL_BALANCE_PAYLOAD = {
        'statement': {
            'account': {'accountNumber': '62493282265', 'currency': 'BWP'},
            'balance': [
                {'typeCode': 'OPBD', 'amountValue': 255000.00,
                 'creditDebitIndicator': 'Credit'},
                {'typeCode': 'CLBD', 'amountValue': 256000.00,
                 'creditDebitIndicator': 'Credit'},
            ],
            'entry': [],
        }
    }

    @classmethod
    def setUpTestData(cls):
        from core.models import Currency
        from ledger.models import Account
        Currency.objects.get_or_create(
            code='BWP', defaults={'name': 'Botswana Pula', 'symbol': 'P'})
        gl = Account.objects.create(
            code='1150-BAL', name='Claims bank clearing', account_type='asset',
            sub_type='test', is_active=True, is_bank_account=True)
        cls.account = BankAccount.objects.create(
            gl_account=gl, bank_name='First National Bank Botswana',
            account_name='FNBB (Claims A/C)-62493282265',
            account_number='62493282265', fnb_balance_watch=True)

    def test_no_balance_block_reads_as_unknown_not_zero(self):
        """THE bug: no balance block must NOT become a confident P0.00."""
        from fnb.statements import extract_balances
        opening, closing = extract_balances(self.NO_BALANCE_PAYLOAD)
        self.assertIsNone(closing)
        self.assertIsNone(opening)

    def test_a_real_zero_is_a_real_zero(self):
        from fnb.statements import extract_balances
        opening, closing = extract_balances(self.ZERO_BALANCE_PAYLOAD)
        self.assertEqual(closing, Decimal('0.00'))
        self.assertEqual(opening, Decimal('0.00'))

    def test_a_real_figure_survives(self):
        from fnb.statements import extract_balances
        opening, closing = extract_balances(self.REAL_BALANCE_PAYLOAD)
        self.assertEqual(closing, Decimal('256000.00'))
        self.assertEqual(opening, Decimal('255000.00'))

    def test_persisted_statement_records_unknown_as_null(self):
        """The real save path, not just the parser."""
        from fnb.statements import _persist_statement
        today = timezone.localdate()
        stmt = _persist_statement(
            self.account, self.NO_BALANCE_PAYLOAD, today, today)
        stmt.refresh_from_db()
        self.assertIsNone(stmt.closing_balance)
        self.assertIsNone(stmt.opening_balance)

    def test_persisted_statement_keeps_a_real_zero(self):
        from fnb.statements import _persist_statement
        today = timezone.localdate()
        stmt = _persist_statement(
            self.account, self.ZERO_BALANCE_PAYLOAD, today, today)
        stmt.refresh_from_db()
        self.assertEqual(stmt.closing_balance, Decimal('0.00'))

    def test_reconciliation_report_survives_an_unknown_closing_balance(self):
        """Every reader of closing_balance has to cope with the null."""
        from banking.services import get_reconciliation_report
        stmt = BankStatement.objects.create(
            bank_account=self.account, statement_date=timezone.localdate(),
            opening_balance=None, closing_balance=None,
            file_name='unknown-balance-test')
        rep = get_reconciliation_report(stmt)
        self.assertIsNone(rep['bank_closing_balance'])
        self.assertIsNone(rep['difference'])
        self.assertFalse(rep['is_reconciled'])


# ---------------------------------------------------------------------------
# 3 + 4 — who is watched, and who still shows up
# ---------------------------------------------------------------------------

class WatchListTests(_BalancesBase):

    def test_the_credit_card_is_not_watched(self):
        from banking.balances import watched_accounts
        numbers = [a.account_number for a in watched_accounts()]
        self.assertNotIn(CARD_NUMBER, numbers)
        self.assertEqual(sorted(numbers), sorted(n for n, _, _ in SIX))

    def test_the_credit_card_is_absent_from_the_payload(self):
        payload = self.payload()
        masked = [row['account_masked']
                  for g in payload['groups'] for row in g['accounts']]
        self.assertNotIn('…' + CARD_NUMBER[-4:], masked)
        self.assertEqual(len(masked), 6)

    def test_pull_fnb_statements_all_selects_on_the_flag(self):
        """--all must not go back to matching the name."""
        from fnb.management.commands.pull_fnb_statements import (
            watched_statement_accounts,
        )
        numbers = [a.account_number for a in watched_statement_accounts()]
        self.assertNotIn(CARD_NUMBER, numbers)
        self.assertEqual(sorted(numbers), sorted(n for n, _, _ in SIX))

    def test_an_account_pulled_but_not_shown_keeps_being_pulled(self):
        """The CFO's ruling, pinned: "Keep pulling them, just don't show them."

        Investment Income, Choppies Kiosk, Alpha Health and the USD account are
        reached by today's name match and are NOT among his six. The first
        version of this work used ONE flag for both jobs, which would have
        silently stopped collecting their statements - data Finance still uses
        for reconciliation and month-end. Reading an account and displaying it
        are different decisions; this test fails if they are ever merged again.

        Flips an existing account rather than building one, so it cannot trip
        over the unique constraints on Account.code and BankAccount.gl_account.
        """
        from banking.balances import watched_accounts
        from fnb.management.commands.pull_fnb_statements import (
            watched_statement_accounts,
        )

        number = SIX[0][0]
        BankAccount.objects.filter(account_number=number).update(
            fnb_statement_pull=True,      # still read from the bank
            fnb_balance_watch=False,      # but not on the CFO's screen
        )

        pulled = [a.account_number for a in watched_statement_accounts()]
        shown = [a.account_number for a in watched_accounts()]

        self.assertIn(number, pulled)
        self.assertNotIn(number, shown)

    def test_a_never_read_account_still_appears_with_a_null_balance(self):
        # Only three of the six have ever been read — the live picture.
        self.snapshot('62403392335', closing=Decimal('995103.12'))
        self.snapshot('62407809485', closing=Decimal('933199.49'))
        self.snapshot('62493282265', closing=Decimal('0.00'))

        payload = self.payload()
        for never in ('62477843132', '62477854999', '62842621725'):
            row = self.account_row(payload, never)
            self.assertIsNone(row['balance'])
            self.assertEqual(row['balance_status'], 'never_read')
            self.assertIsNone(row['taken_at'])
            self.assertIsNone(row['age_hours'])
            self.assertIsNone(row['projected_floor'])
            self.assertTrue(row['note'], 'a never-read account needs a note')
        self.assertEqual(payload['headline']['accounts_total'], 6)
        self.assertEqual(payload['headline']['accounts_read'], 3)
        self.assertEqual(payload['headline']['worst_status'], 'never_read')

    def test_the_full_account_number_never_leaves_the_server(self):
        self.snapshot('62403392335', closing=Decimal('995103.12'))
        import json
        blob = json.dumps(self.payload())
        for number, _, _ in SIX:
            self.assertNotIn(number, blob)


# ---------------------------------------------------------------------------
# 5 + 6 — staleness, and a failed read over a good one
# ---------------------------------------------------------------------------

class BalanceStatusTests(_BalancesBase):

    def test_a_fresh_reading_is_ok(self):
        self.snapshot('62403392335', closing=Decimal('995103.12'), hours_ago=3.2)
        row = self.account_row(self.payload(), '62403392335')
        self.assertEqual(row['balance_status'], 'ok')
        self.assertEqual(row['balance'], '995103.12')
        self.assertAlmostEqual(row['age_hours'], 3.2, places=1)
        self.assertEqual(row['note'], '')

    def test_a_real_zero_reads_ok_and_shows_the_zero(self):
        self.snapshot('62493282265', closing=Decimal('0.00'), hours_ago=1.0)
        row = self.account_row(self.payload(), '62493282265')
        self.assertEqual(row['balance_status'], 'ok')
        self.assertEqual(row['balance'], '0.00')

    def test_a_twenty_seven_hour_old_snapshot_is_stale(self):
        self.snapshot('62403392335', closing=Decimal('995103.12'), hours_ago=27)
        row = self.account_row(self.payload(), '62403392335')
        self.assertEqual(row['balance_status'], 'stale')
        self.assertEqual(row['balance'], '995103.12')
        self.assertTrue(row['note'])

    def test_the_eight_hour_boundary(self):
        self.snapshot('62403392335', closing=Decimal('1.00'), hours_ago=7.9)
        self.assertEqual(
            self.account_row(self.payload(), '62403392335')
            ['balance_status'], 'ok')
        from banking.models import BankBalanceSnapshot  # noqa: F401
        BankBalanceSnapshot.objects.all().delete()
        self.snapshot('62403392335', closing=Decimal('1.00'), hours_ago=8.1)
        self.assertEqual(
            self.account_row(self.payload(), '62403392335')
            ['balance_status'], 'stale')

    def test_a_failed_latest_read_returns_the_older_figure(self):
        self.snapshot('62403392335', closing=Decimal('995103.12'), hours_ago=9)
        self.snapshot('62403392335', closing=None, hours_ago=1,
                      outcome='failed',
                      error_text='HTTP 400 accountId 62403392335 rejected')
        row = self.account_row(self.payload(), '62403392335')
        self.assertEqual(row['balance_status'], 'failed')
        self.assertEqual(row['balance'], '995103.12')
        # The timestamp must be the OLD reading's — never the failed attempt's.
        self.assertAlmostEqual(row['age_hours'], 9.0, places=1)
        self.assertTrue(row['note'])
        # And the bank's error text must never carry the number out.
        self.assertNotIn('62403392335', row['note'])

    def test_no_balance_returned_shows_no_number_at_all(self):
        self.snapshot('62403392335', closing=Decimal('995103.12'), hours_ago=9)
        self.snapshot('62403392335', closing=None, hours_ago=1,
                      outcome='no_balance_returned')
        row = self.account_row(self.payload(), '62403392335')
        self.assertEqual(row['balance_status'], 'no_balance')
        self.assertIsNone(row['balance'])


# ---------------------------------------------------------------------------
# The headline, the groups and the money
# ---------------------------------------------------------------------------

class BalancesPayloadShapeTests(_BalancesBase):

    def setUp(self):
        self.snapshot('62403392335', closing=Decimal('995103.12'), hours_ago=3.2)
        self.snapshot('62407809485', closing=Decimal('933199.49'), hours_ago=3.2)
        self.snapshot('62493282265', closing=Decimal('256000.00'), hours_ago=3.2)

    def test_headline_totals_only_what_was_read(self):
        head = self.payload()['headline']
        self.assertEqual(head['accounts_read'], 3)
        self.assertEqual(head['accounts_total'], 6)
        self.assertEqual(head['total_balance'], '2184302.61')
        self.assertIn('2,184,302.61', head['sentence'])
        self.assertIn('3 of 6 accounts up to date', head['sentence'])

    def test_headline_is_null_when_nothing_could_be_read(self):
        from banking.models import BankBalanceSnapshot  # noqa: F401
        BankBalanceSnapshot.objects.all().delete()
        head = self.payload()['headline']
        self.assertIsNone(head['total_balance'])
        self.assertIsNone(head['taken_at'])
        self.assertEqual(head['accounts_read'], 0)
        self.assertEqual(head['worst_status'], 'never_read')

    def test_a_group_subtotal_is_null_when_any_account_is_unknown(self):
        payload = self.payload()
        groups = {g['company']: g for g in payload['groups']}
        self.assertEqual(groups['Alpha Direct']['subtotal_balance'],
                         '2184302.61')
        for unread in ('Veritas', 'Risk Software', 'Unicoin'):
            self.assertIsNone(groups[unread]['subtotal_balance'])

    def test_every_account_carries_the_contract_keys(self):
        required = {
            'id', 'label', 'account_masked', 'balance', 'balance_status',
            'taken_at', 'age_hours', 'outgoing_omni', 'outgoing_at_bank',
            'outgoing_unconfirmed_count', 'projected_floor', 'note',
        }
        for group in self.payload()['groups']:
            self.assertEqual(set(group), {'company', 'subtotal_balance',
                                          'accounts'})
            for row in group['accounts']:
                self.assertEqual(set(row), required)

    def test_labels_are_plain_words_never_the_raw_account_name(self):
        labels = [row['label'] for g in self.payload()['groups']
                  for row in g['accounts']]
        self.assertIn('Alpha Direct — Current', labels)
        self.assertIn('Alpha Direct — Claims', labels)
        self.assertIn('Alpha Direct — Call', labels)
        self.assertIn('Veritas — Current', labels)
        self.assertIn('Risk Software — Current', labels)
        self.assertIn('Unicoin — Current', labels)
        for label in labels:
            self.assertNotIn('FNBB', label)

    def test_outgoing_at_bank_ignores_settled_batches(self):
        from fnb.models import FNBBatchSubmission
        current = self.accounts['62403392335']
        FNBBatchSubmission.objects.create(
            idempotency_key='bal-open-1', source_account=current,
            payment_count=2, total_amount_bwp=Decimal('1110266.82'),
            status=FNBBatchSubmission.Status.SUBMITTED)
        FNBBatchSubmission.objects.create(
            idempotency_key='bal-settled-1', source_account=current,
            payment_count=1, total_amount_bwp=Decimal('900000.00'),
            status=FNBBatchSubmission.Status.SETTLED)
        row = self.account_row(self.payload(), '62403392335')
        self.assertEqual(row['outgoing_at_bank'], '1110266.82')
        self.assertEqual(row['outgoing_unconfirmed_count'], 1)

    def test_projected_floor_is_balance_less_both_outgoings(self):
        from fnb.models import FNBBatchSubmission
        from taskboard.models import PaymentRequest
        current = self.accounts['62403392335']
        FNBBatchSubmission.objects.create(
            idempotency_key='bal-open-2', source_account=current,
            payment_count=1, total_amount_bwp=Decimal('500000.00'),
            status=FNBBatchSubmission.Status.SUBMITTED)
        PaymentRequest.objects.create(
            ref='PR-BAL-1', entity='Alpha Direct Insurance',
            subject='balances test', total=Decimal('47487.50'),
            status=PaymentRequest.Status.PENDING_CFO)
        row = self.account_row(self.payload(), '62403392335')
        self.assertEqual(row['outgoing_omni'], '47487.50')
        self.assertEqual(row['outgoing_at_bank'], '500000.00')
        self.assertEqual(row['projected_floor'], '447615.62')

    def test_projected_floor_is_null_when_the_balance_is_unknown(self):
        row = self.account_row(self.payload(), '62477843132')
        self.assertIsNone(row['projected_floor'])


# ---------------------------------------------------------------------------
# The endpoint
# ---------------------------------------------------------------------------

class BalancesEndpointTests(_BalancesBase):

    def _get(self, user=None, query=''):
        from banking.balance_views import BankBalancesView
        req = APIRequestFactory().get('/api/v1/banking/balances/' + query)
        if user is not None:
            force_authenticate(req, user=user)
        return BankBalancesView.as_view()(req)

    def test_the_company_query_parameter_is_ignored(self):
        """The frontend's apiFetch appends ?company=<uuid> to every GET.

        This screen is group-wide on purpose — six accounts across four
        companies — so honouring that parameter would turn the headline into
        one company's cash while still calling it the total.
        """
        self.snapshot('62403392335', closing=Decimal('995103.12'))
        self.snapshot('62477843132', closing=Decimal('11.00'))
        def without_ages(payload):
            # age_hours is measured against "now" and so differs by
            # microseconds between two calls; everything else must match.
            return [{**row, 'age_hours': None}
                    for group in payload['groups'] for row in group['accounts']]

        plain = self._get(self.user).data
        scoped = self._get(
            self.user, query=f'?company={self.companies["ADIC"].id}').data
        self.assertEqual(without_ages(scoped), without_ages(plain))
        self.assertEqual(scoped['headline']['accounts_read'],
                         plain['headline']['accounts_read'])
        self.assertEqual(scoped['headline']['accounts_total'], 6)
        self.assertEqual(scoped['headline']['total_balance'], '995114.12')
        self.assertEqual(len(scoped['groups']), 4)

    def test_get_returns_the_contract_shape(self):
        self.snapshot('62403392335', closing=Decimal('995103.12'))
        resp = self._get(self.user)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(set(resp.data), {'headline', 'groups'})

    def test_anonymous_is_refused(self):
        self.assertIn(self._get().status_code, (401, 403))

    def test_the_endpoint_has_no_write_path(self):
        from banking.balance_views import BankBalancesView
        allowed = {m.lower() for m in BankBalancesView().allowed_methods}
        self.assertFalse(allowed & {'post', 'put', 'patch', 'delete'},
                         'the balances endpoint must never gain a write path')

    def test_it_is_routed_at_the_contract_url(self):
        from django.urls import resolve
        match = resolve('/api/v1/banking/balances/')
        self.assertEqual(match.url_name, 'v1-banking-balances')


# ---------------------------------------------------------------------------
# The command
# ---------------------------------------------------------------------------

class PullBalancesCommandTests(_BalancesBase):

    def _run(self, **opts):
        from io import StringIO
        from django.core.management import call_command
        out = StringIO()
        call_command('pull_fnb_balances', stdout=out, stderr=out, **opts)
        return out.getvalue()

    def test_a_dry_run_writes_no_snapshot(self):
        from unittest import mock
        with mock.patch('fnb.statements.fetch_balances',
                        return_value=(Decimal('1.00'), Decimal('2.00'))):
            self._run(dry_run=True)
        from banking.models import BankBalanceSnapshot
        self.assertEqual(BankBalanceSnapshot.objects.count(), 0)

    def test_every_watched_account_gets_a_snapshot_including_failures(self):
        from unittest import mock
        from fnb.client import FNBAPIError

        def answer(bank_account, **kw):
            if bank_account.account_number == '62403392335':
                return (Decimal('631972.51'), Decimal('995103.12'))
            if bank_account.account_number == '62493282265':
                return (None, None)
            raise FNBAPIError(400, 'Bad Request')

        with mock.patch('fnb.statements.fetch_balances', side_effect=answer):
            self._run()

        from banking.models import BankBalanceSnapshot
        self.assertEqual(BankBalanceSnapshot.objects.count(), 6)
        by_number = {s.bank_account.account_number: s
                     for s in BankBalanceSnapshot.objects.all()}
        self.assertEqual(by_number['62403392335'].outcome, 'ok')
        self.assertEqual(by_number['62403392335'].closing,
                         Decimal('995103.12'))
        self.assertEqual(by_number['62493282265'].outcome,
                         'no_balance_returned')
        self.assertIsNone(by_number['62493282265'].closing)
        self.assertEqual(by_number['62477843132'].outcome, 'failed')
        self.assertIn('400', by_number['62477843132'].error_text)
        # No statement row anywhere — a one-day window must import nothing.
        self.assertEqual(BankStatement.objects.count(), 0)

    def test_it_asks_the_bank_for_a_one_day_window(self):
        from unittest import mock
        seen = {}

        def answer(bank_account, *, on_date=None, **kw):
            seen[bank_account.account_number] = on_date
            return (None, Decimal('1.00'))

        with mock.patch('fnb.statements.fetch_balances', side_effect=answer):
            self._run(account='62403392335')
        self.assertEqual(seen['62403392335'], timezone.localdate())
        from banking.models import BankBalanceSnapshot
        snap = BankBalanceSnapshot.objects.get()
        self.assertEqual(snap.as_of_date, timezone.localdate())

    def test_account_option_runs_one_account_only(self):
        from unittest import mock
        with mock.patch('fnb.statements.fetch_balances',
                        return_value=(None, Decimal('1.00'))):
            self._run(account='62403392335')
        from banking.models import BankBalanceSnapshot
        self.assertEqual(BankBalanceSnapshot.objects.count(), 1)

    def test_the_source_is_recorded(self):
        from unittest import mock
        with mock.patch('fnb.statements.fetch_balances',
                        return_value=(None, Decimal('1.00'))):
            self._run(account='62403392335', source='manual')
        from banking.models import BankBalanceSnapshot
        self.assertEqual(BankBalanceSnapshot.objects.get().source, 'manual')

    def test_balances_never_land_on_the_statement_table(self):
        """Three pulls a day must not multiply BankStatement rows."""
        from unittest import mock
        with mock.patch('fnb.statements.fetch_balances',
                        return_value=(Decimal('1.00'), Decimal('2.00'))):
            self._run()
            self._run()
            self._run()
        self.assertEqual(BankStatement.objects.count(), 0)
        from banking.models import BankBalanceSnapshot
        self.assertEqual(BankBalanceSnapshot.objects.count(), 18)


class EntityAttributionTests(_BalancesBase):
    """Whose pending payments land against WHICH account.

    `PaymentRequest` carries no account link — only free-text `entity` and a
    `category`. The first version resolved the entity with a hand-rolled EXACT
    match on Company code/name/legal_name (three ways wrong, all three putting
    a confidently mis-attributed number on the CFO's screen), and then hung the
    company's whole total on its FIRST account — so claims came off the Current
    account and the Claims account showed nothing going out.
    """

    # The six accounts as the screen groups them: the company's main account
    # first, exactly as build_balances_payload orders them.
    SIX_BY_COMPANY = {
        'ADIC': ['62403392335', '62493282265', '62407809485'],
        'VCM':  ['62477843132'],
        'RSA':  ['62477854999'],
        'UNI':  ['62842621725'],
    }

    def _raise(self, entity, amount, category=''):
        from taskboard.models import PaymentRequest
        return PaymentRequest.objects.create(
            entity=entity, total=Decimal(amount), status='pending_cfo',
            subject='test', payee='Test Payee', currency='BWP',
            category=category, created_by=self.user,
        )

    def _totals(self):
        from banking.balances import _outgoing_omni_by_account
        return _outgoing_omni_by_account(self.SIX_BY_COMPANY)

    def test_the_default_entity_string_is_attributed_to_alpha_direct(self):
        """PaymentRequest.entity DEFAULTS to 'Alpha Direct Insurance Company'
        while Company(code='ADIC').name is 'Alpha Direct Insurance'. An exact
        match therefore missed the COMMONEST value in the whole table and fell
        through to whatever company happened to carry is_default."""
        self._raise('Alpha Direct Insurance Company', '25000.00')
        totals = self._totals()

        self.assertEqual(totals['62403392335'], Decimal('25000.00'))
        self.assertEqual(totals['62842621725'], Decimal('0.00'))

    def test_a_unicoin_request_is_not_charged_to_alpha_direct(self):
        """The one that matters: Unicoin's pending payments were being
        subtracted from Alpha Direct's Current account."""
        self._raise('Unicoin', '9000.00')
        totals = self._totals()

        self.assertEqual(totals['62842621725'], Decimal('9000.00'))
        self.assertEqual(totals['62403392335'], Decimal('0.00'))

    def test_money_is_never_silently_dropped(self):
        """The old fallback was Company(is_default=True); with no default the
        unresolved total vanished, outgoing_omni read 0.00 and the floor was
        overstated. Nothing may disappear."""
        from core.models import Company

        Company.objects.update(is_default=False)
        self._raise('Something Nobody Configured', '4321.00')
        totals = self._totals()

        self.assertEqual(sum(totals.values()), Decimal('4321.00'))

    @override_settings(PAYMENT_REQUEST_FNB_SOURCE_ACCOUNTS={
        'claim': '62493282265', 'default': '62403392335'})
    def test_a_claim_lands_on_the_claims_account_not_the_current_account(self):
        """CFO 2026-08-21: "claims payments will go through alpha Direct claims
        accounts", and Omni already pays them that way. Before this, P419,199.21
        of pending claims was subtracted from the Current account instead —
        Current understated its floor by that much and Claims overstated its."""
        self._raise('Alpha Direct Insurance Company', '419199.21', 'claim')
        totals = self._totals()

        self.assertEqual(totals['62493282265'], Decimal('419199.21'))
        self.assertEqual(totals['62403392335'], Decimal('0.00'))

    @override_settings(PAYMENT_REQUEST_FNB_SOURCE_ACCOUNTS={
        'claim': '62493282265', 'default': '62403392335'})
    def test_an_ordinary_payment_lands_on_the_current_account(self):
        """The other half of the same rule: everything that is not a claim
        leaves the operating account. A test that only pinned claims would pass
        just as happily if EVERY request were routed to the Claims account."""
        self._raise('Alpha Direct Insurance Company', '11601.44', 'vendor')
        totals = self._totals()

        self.assertEqual(totals['62403392335'], Decimal('11601.44'))
        self.assertEqual(totals['62493282265'], Decimal('0.00'))

    @override_settings(PAYMENT_REQUEST_FNB_SOURCE_ACCOUNTS={
        'claim': '62493282265', 'default': '62403392335'})
    def test_another_company_never_pays_from_alpha_directs_account(self):
        """The category map answers '62403392335' (Alpha Direct's Current) for
        every ordinary category, whoever raised the request. Honouring it
        blindly would subtract Veritas's bills from Alpha Direct's balance, so
        the map is only obeyed when the account it names belongs to the
        requesting company — the same scoping the real payment path applies."""
        self._raise('Veritas Capital', '7500.00', 'vendor')
        totals = self._totals()

        self.assertEqual(totals['62477843132'], Decimal('7500.00'))
        self.assertEqual(totals['62403392335'], Decimal('0.00'))

    @override_settings(PAYMENT_REQUEST_FNB_SOURCE_ACCOUNTS={
        'claim': '62493282265', 'default': '62403392335'})
    def test_a_category_nobody_mapped_still_shows_up(self):
        """An unmapped category resolves to the company's main account rather
        than vanishing. Being imprecise about WHICH account is recoverable;
        showing the CFO less money going out than there is, is not."""
        self._raise('Alpha Direct Insurance Company', '888.00', 'brand_new')
        totals = self._totals()

        self.assertEqual(totals['62403392335'], Decimal('888.00'))
        self.assertEqual(sum(totals.values()), Decimal('888.00'))


class DoubleCountTests(_BalancesBase):
    """The same payment must not be subtracted twice.

    Omni loads a request into FNB at FINANCE sign-off, not at CFO
    authorisation. So a request waiting for the CFO normally already has a live
    bank batch: it is at the bank, waiting for his phone, not sitting in Omni.

    Found 2026-09-20 from the CFO's own two screenshots — his Omni queue showed
    BHUMI INVESTMENT 111,720.00 waiting for him, while his FNB queue showed the
    same payment as "Authorisation Requested". The screen subtracted it from
    the Claims account twice. 10 of 15 pending requests were in that state,
    P486,835.61 double-counted, and the Claims floor read -827,727.02 when the
    honest figure was -408,527.81.
    """

    SIX_BY_COMPANY = EntityAttributionTests.SIX_BY_COMPANY

    def _raise(self, amount, category='claim'):
        from taskboard.models import PaymentRequest
        # `ref` is unique and is not auto-filled on a bare create, so two
        # requests in one test collide on the empty string.
        n = PaymentRequest.objects.count() + 1
        return PaymentRequest.objects.create(
            ref=f'PAY/TEST/{n:04d}',
            entity='Alpha Direct Insurance Company', total=Decimal(amount),
            status='pending_cfo', subject='test', payee='Test Payee',
            currency='BWP', category=category, created_by=self.user,
        )

    def _load_to_bank(self, request, account_number, status='submitted'):
        """What finance sign-off does: put the request in the bank's queue."""
        from fnb.models import FNBBatchSubmission
        batch = FNBBatchSubmission.objects.create(
            source_account=self.accounts[account_number],
            payment_count=1, total_amount_bwp=request.total,
            currency_code='BWP', status=status,
            idempotency_key=f'TEST {request.id}',
        )
        request.fnb_batch = batch
        request.save(update_fields=['fnb_batch'])
        return batch

    def _totals(self):
        from banking.balances import _outgoing_omni_by_account
        return _outgoing_omni_by_account(self.SIX_BY_COMPANY)

    @override_settings(PAYMENT_REQUEST_FNB_SOURCE_ACCOUNTS={
        'claim': '62493282265', 'default': '62403392335'})
    def test_a_request_already_at_the_bank_is_not_also_counted_in_omni(self):
        """The whole bug, at its smallest: one claim, loaded to the bank,
        still awaiting the CFO. `_outgoing_at_bank` already counts it, so this
        side must not."""
        pr = self._raise('111720.00', 'claim')
        self._load_to_bank(pr, '62493282265')

        self.assertEqual(self._totals()['62493282265'], Decimal('0.00'))

    @override_settings(PAYMENT_REQUEST_FNB_SOURCE_ACCOUNTS={
        'claim': '62493282265', 'default': '62403392335'})
    def test_a_request_not_yet_at_the_bank_is_still_counted(self):
        """The other half. A test that only pinned the exclusion would pass
        just as happily if this side counted NOTHING at all."""
        self._raise('111720.00', 'claim')

        self.assertEqual(self._totals()['62493282265'], Decimal('111720.00'))

    @override_settings(PAYMENT_REQUEST_FNB_SOURCE_ACCOUNTS={
        'claim': '62493282265', 'default': '62403392335'})
    def test_a_settled_batch_puts_the_request_back_in_the_omni_bucket(self):
        """`settled` means the money has actually gone, so `_outgoing_at_bank`
        stops counting it. If this side stayed silent too, a paid-but-still-open
        request would vanish from "going out" entirely and overstate the floor.
        The exclusion must track the bank bucket exactly, not just 'has a
        batch'."""
        pr = self._raise('111720.00', 'claim')
        self._load_to_bank(pr, '62493282265', status='settled')

        self.assertEqual(self._totals()['62493282265'], Decimal('111720.00'))

    @override_settings(PAYMENT_REQUEST_FNB_SOURCE_ACCOUNTS={
        'claim': '62493282265', 'default': '62403392335'})
    def test_the_two_buckets_add_up_to_every_pending_request_once(self):
        """The CFO's arithmetic check: what his app calls "ready to authorise"
        must equal Omni-waiting plus at-the-bank, with nothing counted twice
        and nothing lost. Modelled on the real 20-Sep split — P486,835.61 at
        the bank, P48,947.53 only in Omni, P535,783.14 in his queue."""
        from banking.balances import _outgoing_at_bank

        at_bank_pr = self._raise('486835.61', 'claim')
        self._load_to_bank(at_bank_pr, '62493282265')
        self._raise('48947.53', 'vendor')

        omni = sum(self._totals().values())
        bank = sum(t for t, _ in _outgoing_at_bank(
            [a.id for a in self.accounts.values()]).values())

        self.assertEqual(omni, Decimal('48947.53'))
        self.assertEqual(bank, Decimal('486835.61'))
        self.assertEqual(omni + bank, Decimal('535783.14'))


class LiveBatchLinkTests(_BalancesBase):
    """Which link proves a request is already at the bank.

    There are TWO: `PaymentRequest.fnb_batch` (the forward FK, written only on
    the success path) and `FNBBatchSubmission.payment_request` (reverse
    `fnb_batches`, the newer lineage link). Neither is complete — on prod
    2026-09-20 the forward one covered 312 of 321 batches and the reverse 15.

    Keying on either alone leaves the same money subtracted twice. A code
    review recommended the reverse link on its own; measured against live data
    that would have matched 1 request where the forward one matched 10, quietly
    undoing the double-count fix it was reviewing.
    """

    SIX_BY_COMPANY = EntityAttributionTests.SIX_BY_COMPANY

    def _raise(self, amount, category='claim'):
        from taskboard.models import PaymentRequest
        n = PaymentRequest.objects.count() + 1
        return PaymentRequest.objects.create(
            ref=f'PAY/LINK/{n:04d}',
            entity='Alpha Direct Insurance Company', total=Decimal(amount),
            status='pending_cfo', subject='test', payee='Test Payee',
            currency='BWP', category=category, created_by=self.user,
        )

    def _batch(self, account_number, amount, status='submitted', request=None):
        from fnb.models import FNBBatchSubmission
        n = FNBBatchSubmission.objects.count() + 1
        return FNBBatchSubmission.objects.create(
            source_account=self.accounts[account_number], payment_count=1,
            total_amount_bwp=Decimal(amount), currency_code='BWP',
            status=status, idempotency_key=f'TEST LINK {n}',
            payment_request=request,
        )

    def _totals(self):
        from banking.balances import _outgoing_omni_by_account
        return _outgoing_omni_by_account(self.SIX_BY_COMPANY)

    @override_settings(PAYMENT_REQUEST_FNB_SOURCE_ACCOUNTS={
        'claim': '62493282265', 'default': '62403392335'})
    def test_the_forward_link_excludes(self):
        """The common case: 312 of 321 batches on prod are linked this way."""
        pr = self._raise('111720.00')
        pr.fnb_batch = self._batch('62493282265', '111720.00')
        pr.save(update_fields=['fnb_batch'])

        self.assertEqual(self._totals()['62493282265'], Decimal('0.00'))

    @override_settings(PAYMENT_REQUEST_FNB_SOURCE_ACCOUNTS={
        'claim': '62493282265', 'default': '62403392335'})
    def test_the_reverse_link_alone_also_excludes(self):
        """The CONSUMER half: given a live batch reachable only by the reverse
        link, this side must not count the money again.

        That the reverse link is actually WRITTEN in that situation is a
        separate promise, made by `submit_eft_batch` and proved against the
        real function in
        `fnb.tests.IndeterminateSubmitKeepsItsLineageTests` — because the
        first version of this test hand-built an 'unknown' batch with
        `payment_request` already set, which no production path then produced.
        It passed, and certified a trap as closed while it was open. A
        consumer test may assume the state; it may not be the only thing
        asserting the state can occur.
        """
        pr = self._raise('111720.00')
        self._batch('62493282265', '111720.00', status='unknown', request=pr)
        self.assertIsNone(pr.fnb_batch_id)

        self.assertEqual(self._totals()['62493282265'], Decimal('0.00'))

    @override_settings(PAYMENT_REQUEST_FNB_SOURCE_ACCOUNTS={
        'claim': '62493282265', 'default': '62403392335'})
    def test_several_batches_do_not_multiply_the_request(self):
        """An Individual request submits one batch PER LINE. Excluding via an
        OR across the reverse join fans out one row per batch, and Sum('total')
        over a fanned-out join multiplies the request's total by its batch
        count. Here that would read 4 x 111,720.00 instead of nothing."""
        pr = self._raise('111720.00')
        for _ in range(4):
            self._batch('62493282265', '27930.00', request=pr)

        totals = self._totals()
        self.assertEqual(totals['62493282265'], Decimal('0.00'))
        self.assertEqual(sum(totals.values()), Decimal('0.00'))


class FreshnessTests(_BalancesBase):
    """A figure is not the same as a figure read TODAY.

    `resolve_account_state` deliberately falls back to an older good reading
    when the latest read failed — visible, and visibly not current. But the
    payload counted that account in `accounts_read`, and the home card's "all
    six read" test was `accounts_read == accounts_total`. So the first morning
    the three FNB-400 accounts succeed once and then fail again, the card would
    go "complete", drop its amber line, and present days-old cash as the
    morning position.
    """

    def setUp(self):
        # super() first: _BalancesBase builds its fixtures in setUpTestData,
        # but an override that silently skips a parent setUp is the kind of
        # thing that leaves a test green and hollow (off-subscription panel,
        # OpenAI, K4).
        super().setUp()
        from banking.balances import build_balances_payload
        self.build = build_balances_payload

    def _snap(self, number, closing, *, hours_ago, outcome):
        from banking.models import BankBalanceSnapshot
        taken = timezone.now() - dt.timedelta(hours=hours_ago)
        return BankBalanceSnapshot.objects.create(
            bank_account=self.accounts[number], closing=closing,
            outcome=outcome, taken_at=taken,
            # localtime(), never .date() on a UTC datetime — Botswana is CAT
            # and the bare form has broken this suite between 22:00 and
            # midnight twice. Nothing here asserts on as_of_date; it is set
            # only because the column is NOT NULL.
            as_of_date=timezone.localtime(taken).date(),
        )

    def test_a_stale_fallback_counts_as_read_but_not_as_fresh(self):
        from banking.models import BankBalanceSnapshot
        OK = BankBalanceSnapshot.Outcome.OK
        FAILED = BankBalanceSnapshot.Outcome.FAILED

        # Read cleanly yesterday, failed this morning: a figure, but not today's.
        self._snap('62493282265', Decimal('300000.00'), hours_ago=30, outcome=OK)
        self._snap('62493282265', None, hours_ago=1, outcome=FAILED)
        # Read cleanly this morning.
        self._snap('62403392335', Decimal('100000.00'), hours_ago=1, outcome=OK)

        h = self.build()['headline']
        self.assertEqual(h['accounts_read'], 2)
        self.assertEqual(h['accounts_fresh'], 1)

    def test_a_clean_read_is_both(self):
        """The other direction. A test that only pinned the stale case would
        pass just as happily if accounts_fresh were hard-wired to zero."""
        from banking.models import BankBalanceSnapshot
        self._snap('62403392335', Decimal('100000.00'), hours_ago=1,
                   outcome=BankBalanceSnapshot.Outcome.OK)

        h = self.build()['headline']
        self.assertEqual(h['accounts_read'], 1)
        self.assertEqual(h['accounts_fresh'], 1)


class PermissionGateTests(_BalancesBase):
    """The gate itself, pinned.

    Every other endpoint test authenticates as a superuser, and the anonymous
    test accepts 401 OR 403 — so `CanViewFinancials` could be deleted from the
    view and the whole suite stayed green. It is the only thing between all
    staff and the group cash position plus every pending outgoing.
    """

    def test_a_staff_user_without_financial_access_is_refused(self):
        from rest_framework.test import APIClient

        plain = User.objects.create_user(
            'balances_no_finance', 'nofin@example.com', 'x')
        profile = getattr(plain, 'profile', None)
        if profile is not None and hasattr(profile, 'title'):
            profile.title = ''
            profile.save(update_fields=['title'])

        client = APIClient()
        client.force_authenticate(user=plain)
        resp = client.get('/api/v1/banking/balances/')

        self.assertEqual(resp.status_code, 403, resp.content[:200])


class BalanceWindowTests(_BalancesBase):
    """The window a balance read asks for. Measured against live FNB.

    The first version asked for ONE day (fromDate == toDate). A day the bank
    has not opened yet answers OPBD 0.00 / CLBD 0.00 in a shape
    indistinguishable from a real zero, so on Sunday 2026-09-20 the CFO's
    screen would have shown P0.00 against an account holding BWP 1,341,798.13.
    """

    def test_the_balance_read_never_asks_for_a_single_day(self):
        import datetime as _dt
        from unittest import mock
        from fnb.statements import fetch_balances, BALANCE_WINDOW_DAYS

        self.assertGreaterEqual(BALANCE_WINDOW_DAYS, 1)

        sent = {}

        class _Resp:
            json = {'statement': {'balance': [
                {'typeCode': 'OPBD', 'amountValue': 1.0},
                {'typeCode': 'CLBD', 'amountValue': 2.0},
            ]}}

        def _post(self_inner, *a, **kw):
            sent.update(kw['json_body'])
            return _Resp()

        on = _dt.date(2026, 9, 20)
        with mock.patch('fnb.client.FNBClient.post', _post):
            fetch_balances(self.accounts[SIX[0][0]], on_date=on)

        self.assertEqual(sent['toDate'], '2026-09-20')
        self.assertNotEqual(sent['fromDate'], sent['toDate'],
                            'a one-day window answers 0.00 on a day the bank '
                            'has not opened — that is a false zero')
        span = (_dt.date.fromisoformat(sent['toDate'])
                - _dt.date.fromisoformat(sent['fromDate'])).days
        self.assertGreaterEqual(span, 2, 'the window must span a business day')


class ZeroEntriesMeansUnknownTests(_BalancesBase):
    """FNB answers balance 0.00 for any window it finds NO ENTRIES in.

    Caught on 2026-09-20 by the CFO sending a screenshot of the FNB app:
    Omni's brand-new screen read "Alpha Direct Claims — 0.00, confirmed by the
    bank" while the app showed the same account holding P300,474.31. Measured
    against live FNB for …2265 at that moment:

        1, 2, 7, 14 days  entries=0    CLBD 0.00
        30 days           entries=357  CLBD 256,229.74

    Widening the window does not fix this, it only moves the boundary. A
    dormant account returns no entries for ANY window, and for that account a
    zero is unknowable from this endpoint. No entries, no figure.
    """

    def _post_returning(self, entries, opbd, clbd):
        class _Resp:
            json = {'statement': {
                'entry': [{}] * entries,
                'balance': [
                    {'typeCode': 'OPBD', 'amountValue': opbd},
                    {'typeCode': 'CLBD', 'amountValue': clbd},
                ],
            }}
        return lambda self_inner, *a, **kw: _Resp()

    def test_a_zero_balance_with_no_entries_is_unknown_not_zero(self):
        from unittest import mock
        from fnb.statements import fetch_balances

        with mock.patch('fnb.client.FNBClient.post',
                        self._post_returning(0, 0.0, 0.0)):
            opening, closing = fetch_balances(self.accounts[SIX[0][0]])

        self.assertIsNone(closing, 'a 0.00 block with no entries is FNB saying '
                                   'nothing, not an empty account')
        self.assertIsNone(opening)

    def test_a_real_balance_with_entries_is_kept(self):
        from decimal import Decimal
        from unittest import mock
        from fnb.statements import fetch_balances

        with mock.patch('fnb.client.FNBClient.post',
                        self._post_returning(357, 1182023.36, 256229.74)):
            opening, closing = fetch_balances(self.accounts[SIX[0][0]])

        self.assertEqual(closing, Decimal('256229.74'))
        self.assertEqual(opening, Decimal('1182023.36'))

    def test_a_genuine_zero_with_entries_is_still_a_real_zero(self):
        """An account emptied to exactly nothing HAS transactions explaining
        it, so this must not be swept up with the unknowns."""
        from decimal import Decimal
        from unittest import mock
        from fnb.statements import fetch_balances

        with mock.patch('fnb.client.FNBClient.post',
                        self._post_returning(4, 500.00, 0.0)):
            _, closing = fetch_balances(self.accounts[SIX[0][0]])

        self.assertEqual(closing, Decimal('0.00'))

    def test_the_window_is_wide_enough_for_a_quiet_account(self):
        """…2265 returns nothing at 14 days and 357 entries at 30."""
        from fnb.statements import BALANCE_WINDOW_DAYS
        self.assertGreaterEqual(BALANCE_WINDOW_DAYS, 30)

    def test_the_window_never_exceeds_what_fnb_allows(self):
        """🔴 FNB refuses any window over 30 days with a bare HTTP 400.

        A 35-day window was deployed for eight minutes and ALL SIX accounts
        failed — the CFO's screen had no figures at all. Measured on …2335 and
        …2265: 28d ok, 29d ok, 30d ok, 31d 400, 32d 400, 34d 400. There is no
        margin: 30 is both the maximum FNB allows and the minimum the quiet
        account needs.
        """
        from fnb.statements import BALANCE_WINDOW_DAYS, FNB_MAX_WINDOW_DAYS
        self.assertLessEqual(BALANCE_WINDOW_DAYS, FNB_MAX_WINDOW_DAYS)
        self.assertEqual(FNB_MAX_WINDOW_DAYS, 30)

    def test_an_illegal_window_fails_loudly_not_at_the_bank(self):
        from unittest import mock
        from fnb.statements import _assert_window_is_legal
        with self.assertRaises(ValueError) as cm:
            _assert_window_is_legal(31)
        self.assertIn('30', str(cm.exception))

        with mock.patch('fnb.statements.BALANCE_WINDOW_DAYS', 45), \
             mock.patch('fnb.client.FNBClient.post') as post:
            from fnb.statements import fetch_balances
            with self.assertRaises(ValueError):
                fetch_balances(self.accounts[SIX[0][0]])
            post.assert_not_called()


class HeadlineArithmeticTests(_BalancesBase):
    """balances − pending payments = theoretical balance.

    The CFO's own words, 20-Sep-2026, asking where the figures were on the
    phone: "the complete info, balances - pending payments = theoretical
    balance". The first payload carried only the balance total, so the phone
    could not do the sum without re-adding every row itself.
    """

    def test_the_headline_carries_the_whole_sum(self):
        p = self.payload()['headline']
        for key in ('total_balance', 'total_outgoing_omni',
                    'total_outgoing_at_bank', 'total_outgoing',
                    'total_projected_floor', 'outgoing_unconfirmed_count'):
            self.assertIn(key, p, key)

    def test_going_out_is_the_two_buckets_added_up(self):
        from decimal import Decimal
        p = self.payload()['headline']
        self.assertEqual(
            Decimal(p['total_outgoing']),
            Decimal(p['total_outgoing_omni']) + Decimal(p['total_outgoing_at_bank']))

    def test_the_floor_is_the_balance_less_everything_going_out(self):
        from decimal import Decimal
        p = self.payload()['headline']
        if p['total_balance'] is None:
            self.skipTest('nothing was read, so there is no floor to check')
        self.assertEqual(
            Decimal(p['total_projected_floor']),
            Decimal(p['total_balance']) - Decimal(p['total_outgoing']))

    def test_an_unreadable_balance_leaves_the_floor_unknown(self):
        """You cannot subtract from a figure you do not have. The floor must be
        null rather than quietly equal to minus the outgoings."""
        from banking.models import BankBalanceSnapshot
        BankBalanceSnapshot.objects.all().delete()
        p = self.payload()['headline']
        self.assertIsNone(p['total_balance'])
        self.assertIsNone(p['total_projected_floor'])

    def test_outgoings_are_counted_even_on_an_account_we_could_not_read(self):
        """The money is committed whether or not the balance came back.
        Dropping it would understate what is going out, which is the wrong
        direction to be wrong in."""
        from decimal import Decimal
        from banking.models import BankBalanceSnapshot
        BankBalanceSnapshot.objects.all().delete()
        p = self.payload()['headline']
        self.assertIsNotNone(p['total_outgoing'])
        self.assertGreaterEqual(Decimal(p['total_outgoing']), Decimal('0.00'))
