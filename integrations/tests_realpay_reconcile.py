"""
Tests for the RealPay-vs-ledger reconciliation.

The figures below are the REAL ones measured on the live replica on 8-Sep-2026, the
day the gap was found. That makes this a regression test against a specific incident:
if the comparison, the tolerance, the settling rule or the negative-shortfall rule
ever drift, one of these numbers moves and the test fails.

No customer data — months and counts only.
"""
from __future__ import annotations

import datetime
from unittest.mock import patch

from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from integrations import realpay_ledger_reconcile as recon

URL = '/api/v1/integrations/realpay-ledger-reconciliation/'

# The live figures, measured contract by contract on 8-Sep-2026, EXCEPT September's
# shortfall: live September reconciles (2 missing) and is deliberately modelled here as
# a large 2,079 loss instead, because that is the only way to prove the settling-month
# rule is load-bearing rather than incidental. The month totals are real; the
# per-contract split is modelled to reproduce the measured shortfall exactly, because
# the point of these tests is the ARITHMETIC of the match, not the row detail.
#
#   month     feed    ledger   subtraction said   matched truth   ledger-only
#   2026-04  19,927   23,344       -3,417 "ok"      41  ( 0.2%)         3,458
#   2026-06  18,635   17,257        1,378 (7.4%)  3,474  (18.6%)         2,096
#   2026-07  18,090    3,742       14,348 (79%)  15,680  (86.7%)         1,332
#   2026-08  19,268   11,123        8,145 (42%)   8,954  (46.5%)           809
MONTHS = {
    # month: (feed_total, ledger_total, missing, ledger_only)
    '2026-04': (19927, 23344, 41, 3458),
    '2026-05': (19124, 22906, 60, 3800),
    '2026-06': (18635, 17257, 3474, 2096),
    '2026-07': (18090, 3742, 15680, 1332),
    '2026-08': (19268, 11123, 8954, 809),
    '2026-09': (2279, 2522, 2079, 300),
}


def _split(feed_total, ledger_total, missing, ledger_only):
    """Per-contract counts that add up to the measured month.

    Three groups of contracts: ones the feed has and the ledger does not (the loss),
    ones both have (matched), and ones only the ledger has (the offset that used to
    hide the loss).
    """
    both_feed = feed_total - missing
    both_ledger = ledger_total - ledger_only
    feed = {}
    ledger = {}
    if missing:
        feed['LOST'] = missing
    if both_feed or both_ledger:
        # Matched contracts: give the ledger at least as many as the feed so they
        # contribute nothing to the shortfall.
        feed['BOTH'] = both_feed
        ledger['BOTH'] = (max(both_ledger, both_feed), max(both_ledger, both_feed) * 69.0)
    if ledger_only:
        ledger['LEDGER_ONLY'] = (ledger_only, ledger_only * 69.0)
    return feed, ledger


#: Every (sql, params) pair the calculation sent, so the tests can assert on what was
#: actually asked of the database and not only on what came back.
CALLS: list = []


class _Cur:
    """Answers the per-contract feed query, then the per-contract ledger query."""

    def __init__(self, months):
        self._months, self._rows = months, []

    def __enter__(self): return self
    def __exit__(self, *a): return False

    def execute(self, sql, params=None):
        params = list(params or [])
        CALLS.append((sql, params))
        month = str(params[0])[:7] if params else ''
        # Months the fixture does not model are treated as small and clean, not as
        # empty — an empty feed is now an outage, so a long window would otherwise
        # trip the H25 fence rather than testing what it means to.
        totals = self._months.get(month, (1000, 1000, 0, 0))
        feed, ledger = _split(*totals)
        if 'realpay_contract_installments' in sql:
            self._rows = [{'k': k, 'n': n} for k, n in feed.items()]
        else:
            self._rows = [{'k': k, 'n': n, 'amt': a} for k, (n, a) in ledger.items()]

    def fetchall(self): return self._rows


class _Cx:
    def __init__(self, months): self._m = months
    def __enter__(self): return self
    def __exit__(self, *a): return False
    def cursor(self): return _Cur(self._m)


def _fake(months=None):
    from contextlib import contextmanager

    @contextmanager
    def cm():
        yield _Cx(months if months is not None else MONTHS)
    return cm


class ReconcileTests(TestCase):

    def setUp(self):
        CALLS.clear()

    def run_compare(self, **kw):
        with patch.object(recon.graphite_ro, 'connection', _fake(kw.pop('months_data', None))):
            return recon.compare(months=kw.pop('months', 6),
                                 asof=kw.pop('asof', datetime.date(2026, 9, 8)))

    # ---- the incident, matched contract by contract ---------------------

    def test_it_catches_the_july_and_august_gap(self):
        r = self.run_compare()
        self.assertTrue(r['available'])
        self.assertEqual(r['verdict'], 'break')
        m = {x['month']: x for x in r['rows']}
        self.assertEqual(m['2026-07']['shortfall'], 15680)
        self.assertEqual(m['2026-07']['shortfall_pct'], 86.7)
        self.assertEqual(m['2026-08']['shortfall'], 8954)
        self.assertEqual(m['2026-08']['shortfall_pct'], 46.5)
        self.assertEqual(r['worst']['month'], '2026-07')

    def test_a_clean_month_is_clean_on_the_matched_measure_too(self):
        """April subtracts to -3,417 and matches to 41. Both say fine; only the
        matched figure says fine for the right reason."""
        r = self.run_compare()
        apr = [x for x in r['rows'] if x['month'] == '2026-04'][0]
        self.assertEqual(apr['shortfall'], 41)
        self.assertEqual(apr['shortfall_pct'], 0.2)
        self.assertFalse(apr['breach'])

    def test_the_ledger_only_receipts_can_no_longer_hide_a_loss(self):
        """THE bug this rewrite fixes. April holds 3,458 receipts the feed lacks.
        Subtracting totals let those cancel real losses one for one, so a month
        losing ~16% read as "reconciles". They are now reported, never netted."""
        r = self.run_compare()
        apr = [x for x in r['rows'] if x['month'] == '2026-04'][0]
        self.assertEqual(apr['ledger_only'], 3458)
        # The old measure: 19,927 - 23,344 = -3,417, i.e. "we are ahead".
        self.assertLess(apr['feed'] - apr['ledger'], 0)
        # The honest measure is positive and small, and is what drives the alarm.
        self.assertEqual(apr['shortfall'], 41)

    def test_june_was_two_and_a_half_times_worse_than_the_old_measure_said(self):
        """Subtraction said 1,378 (7.4%). Matched says 3,474 (18.6%)."""
        r = self.run_compare()
        jun = [x for x in r['rows'] if x['month'] == '2026-06'][0]
        self.assertEqual(jun['feed'] - jun['ledger'], 1378)   # what it used to report
        self.assertEqual(jun['shortfall'], 3474)              # what is actually missing
        self.assertEqual(jun['shortfall_pct'], 18.6)
        self.assertTrue(jun['breach'])

    def test_the_summary_names_the_worst_month(self):
        line = recon.summary_line(self.run_compare())
        self.assertIn('28,108', line)          # 3,474 + 15,680 + 8,954
        self.assertIn('3 month(s)', line)
        self.assertIn('2026-07', line)
        self.assertIn('86.7', line)

    def test_it_estimates_the_value_from_receipts_that_did_land(self):
        r = self.run_compare()
        jul = [x for x in r['rows'] if x['month'] == '2026-07'][0]
        self.assertGreater(jul['estimated_value'], 0)
        self.assertAlmostEqual(jul['estimated_value'],
                               round(jul['shortfall'] * 69.0, 2), places=2)

    # ---- the rules that stop it crying wolf, and the one that stops a false clean

    def test_the_newest_month_is_reported_but_never_alarmed(self):
        """September is 2,079 short at 91% and must STILL not alarm — mid-month."""
        r = self.run_compare()
        sep = [x for x in r['rows'] if x['month'] == '2026-09'][0]
        self.assertTrue(sep['settling'])
        self.assertEqual(sep['shortfall'], 2079)
        self.assertGreater(sep['shortfall_pct'], 5.0)
        self.assertFalse(sep['breach'])
        self.assertNotIn('2026-09', [b['month'] for b in r['breaches']])

    def test_an_empty_feed_is_an_outage_not_a_reconciliation(self):
        """H25. The feed importer can die exactly as the ledger importer did. A month
        with no feed rows has a shortfall of zero, which would read as perfect."""
        data = dict(MONTHS)
        data['2026-07'] = (0, 3742, 0, 3742)
        r = self.run_compare(months_data=data)
        self.assertFalse(r['available'])
        self.assertIn('no rows for 2026-07', r['reason'])

    def test_an_empty_feed_early_in_the_current_month_is_not_an_outage(self):
        data = dict(MONTHS)
        data['2026-09'] = (0, 0, 0, 0)
        r = self.run_compare(months_data=data, asof=datetime.date(2026, 9, 2))
        self.assertTrue(r['available'])

    def test_an_empty_feed_later_in_the_current_month_is_an_outage(self):
        data = dict(MONTHS)
        data['2026-09'] = (0, 0, 0, 0)
        r = self.run_compare(months_data=data, asof=datetime.date(2026, 9, 20))
        self.assertFalse(r['available'])

    def test_below_either_threshold_is_not_a_break(self):
        data = {'2026-07': (10000, 9700, 300, 0), '2026-08': (10000, 9999, 1, 0)}
        r = self.run_compare(months_data=data, months=2,
                             asof=datetime.date(2026, 8, 31))
        self.assertEqual(r['verdict'], 'ok')

    @override_settings(REALPAY_RECONCILE_MIN_ROWS=100, REALPAY_RECONCILE_MIN_PCT=1.0)
    def test_the_thresholds_are_settings_not_hard_coded(self):
        data = {'2026-07': (10000, 9700, 300, 0), '2026-08': (10000, 9999, 1, 0)}
        r = self.run_compare(months_data=data, months=2,
                             asof=datetime.date(2026, 8, 31))
        self.assertEqual(r['thresholds'], {'min_rows': 100, 'min_pct': 1.0})
        self.assertEqual(r['verdict'], 'break')

    def test_months_is_clamped_so_a_silly_window_cannot_read_ok(self):
        r = self.run_compare(months=0, asof=datetime.date(2026, 8, 31))
        self.assertGreaterEqual(len(r['rows']), 2)

    # ---- what was actually asked of the database ------------------------

    def test_both_sides_are_matched_on_every_spelling_of_success(self):
        self.run_compare()
        feed_call = [c for c in CALLS if 'realpay_contract_installments' in c[0]][0]
        ledger_call = [c for c in CALLS if 'payment_transactions' in c[0]][0]
        for spelling in ('SUCCESS', 'SUCCESSFUL', 'PAID'):
            self.assertIn(spelling, ledger_call[1], f'ledger lost {spelling}')
        for code in ('S', 'SUCCESS', 'SUCCESSFUL', 'PAID'):
            self.assertIn(code, feed_call[1], f'feed lost {code}')
        self.assertIn('UPPER(TRIM(pt.status))', ledger_call[0])
        self.assertIn('UPPER(TRIM(InstalmentStatus))', feed_call[0])

    def test_it_groups_by_contract_rather_than_totalling_the_month(self):
        """The whole fix. A count-vs-count query would have no GROUP BY key."""
        self.run_compare()
        feed_call = [c for c in CALLS if 'realpay_contract_installments' in c[0]][0]
        ledger_call = [c for c in CALLS if 'payment_transactions' in c[0]][0]
        self.assertIn('clientNumber', feed_call[0])
        self.assertIn('GROUP BY k', feed_call[0])
        self.assertIn('policyNumber', ledger_call[0])
        self.assertIn('GROUP BY k', ledger_call[0])
        self.assertIn('JOIN policies', ledger_call[0])

    def test_both_windows_are_bounded_at_the_top(self):
        """payment_transactions holds scheduled rows to 2058 and the feed's date is a
        VARCHAR carrying rows to 2124. An open upper end would sum the future."""
        self.run_compare()
        for sql, params in CALLS:
            self.assertIn('< %s', sql, 'a window was left open at the top')

    # ---- degradation ---------------------------------------------------

    def test_an_unreachable_replica_is_an_outage_not_a_clean_pass(self):
        from contextlib import contextmanager

        @contextmanager
        def boom():
            raise RuntimeError('replica down')
            yield  # pragma: no cover

        with patch.object(recon.graphite_ro, 'connection', boom):
            r = recon.compare()
        self.assertFalse(r['available'])
        self.assertEqual(r['reason'], 'RuntimeError')
        self.assertIn('could not run', recon.summary_line(r))

    def test_a_clean_month_reads_ok(self):
        data = {'2026-07': (1000, 1000, 0, 0), '2026-08': (1000, 1000, 0, 0)}
        r = self.run_compare(months_data=data, months=2,
                             asof=datetime.date(2026, 8, 31))
        self.assertEqual(r['verdict'], 'ok')
        self.assertIn('reconcile', recon.summary_line(r))


class ReconcileEndpointTests(TestCase):
    """The endpoint is gated on CanViewFinancials — the same permission the broker
    loss-ratio panel beside it uses. This is a collections figure, so a general
    operational login must not read it."""

    @classmethod
    def setUpTestData(cls):
        # can_view_financials is a derived property of the profile title, not a
        # settable field, so the allowed case uses a superuser — the same pattern the
        # broker loss-ratio panel's own tests use for the identical gate.
        cls.finance = User.objects.create_superuser('fin', 'fin@example.test', 'x' * 24)
        cls.operations = User.objects.create_user('ops', 'ops@example.test', 'x')

    def setUp(self):
        self.client = APIClient()
        self.client.force_authenticate(self.finance)

    def test_a_login_without_finance_access_is_refused(self):
        """The whole point of the gate. Remove it and this goes green wrongly."""
        client = APIClient()
        client.force_authenticate(self.operations)
        self.assertEqual(client.get(URL).status_code, 403)

    def test_the_endpoint_serves_the_same_calculation(self):
        with patch.object(recon.graphite_ro, 'connection', _fake()):
            r = self.client.get(URL, {'months': 6, 'asof': '2026-09-08'})
        self.assertEqual(r.status_code, 200, r.content[:300])
        body = r.json()
        self.assertEqual(body['verdict'], 'break')
        self.assertEqual(body['worst']['month'], '2026-07')
        self.assertIn('summary', body)

    def test_the_screen_serves_this_mornings_saved_answer_without_scanning(self):
        """The whole point: nobody waits 23 seconds. With a snapshot present the view
        must not call compare() at all."""
        from django.core.cache import cache
        from integrations.models import RealpayReconSnapshot
        cache.clear()
        with patch.object(recon.graphite_ro, 'connection', _fake()):
            computed = recon.compare(months=6, asof=datetime.date(2026, 9, 8))
        RealpayReconSnapshot.objects.create(
            asof=datetime.date(2026, 9, 8), window_months=6,
            verdict=computed['verdict'], total_shortfall=28108, months_breached=3,
            payload=computed)

        with patch.object(recon, 'compare') as never:
            body = self.client.get(URL, {'months': 6}).json()
        never.assert_not_called()
        self.assertEqual(body['served_from'], 'the daily check')
        self.assertFalse(body['live'])
        self.assertIsNotNone(body['computed_at'])
        self.assertEqual(body['verdict'], 'break')
        self.assertEqual(body['worst']['month'], '2026-07')

    def test_asking_for_live_bypasses_the_saved_answer(self):
        from django.core.cache import cache
        from integrations.models import RealpayReconSnapshot
        cache.clear()
        RealpayReconSnapshot.objects.create(
            asof=datetime.date(2026, 9, 8), window_months=6, verdict='ok',
            payload={'available': True, 'verdict': 'ok', 'rows': [], 'breaches': []})
        with patch.object(recon.graphite_ro, 'connection', _fake()):
            body = self.client.get(URL, {'months': 6, 'live': '1'}).json()
        self.assertEqual(body['served_from'], 'a live scan')
        self.assertTrue(body['live'])
        self.assertEqual(body['verdict'], 'break')      # the real figures, not the stub

    def test_with_no_snapshot_yet_it_computes_rather_than_showing_nothing(self):
        from django.core.cache import cache
        cache.clear()
        with patch.object(recon.graphite_ro, 'connection', _fake()):
            body = self.client.get(URL, {'months': 6}).json()
        self.assertEqual(body['served_from'], 'a live scan')
        self.assertEqual(body['verdict'], 'break')

    def test_the_daily_command_saves_what_the_screen_will_serve(self):
        from django.core.management import call_command
        from integrations.models import RealpayReconSnapshot
        import io

        with patch.object(recon.graphite_ro, 'connection', _fake()):
            call_command('realpay_ledger_reconcile', months=6, asof='2026-09-08',
                         stdout=io.StringIO())
        snap = RealpayReconSnapshot.objects.get()
        self.assertEqual(snap.verdict, 'break')
        self.assertEqual(snap.total_shortfall, 28108)
        self.assertEqual(snap.months_breached, 3)
        self.assertIn('2026-07', snap.summary)
        self.assertEqual(snap.payload['worst']['month'], '2026-07')

    def test_a_correction_is_a_new_row_never_an_overwrite(self):
        from django.core.management import call_command
        from integrations.models import RealpayReconSnapshot
        import io

        with patch.object(recon.graphite_ro, 'connection', _fake()):
            call_command('realpay_ledger_reconcile', months=6, asof='2026-09-08',
                         stdout=io.StringIO())
            call_command('realpay_ledger_reconcile', months=6, asof='2026-09-08',
                         stdout=io.StringIO())
        self.assertEqual(RealpayReconSnapshot.objects.count(), 2)

    def test_the_screen_is_cached_but_the_daily_alarm_is_not(self):
        """23 seconds is not a screen anyone waits for, so the view caches. The cron
        must NOT: the one job whose purpose is to notice a change cannot answer from
        a stale copy."""
        from django.core.cache import cache
        cache.clear()
        with patch.object(recon, 'compare', wraps=recon.compare) as spy:
            with patch.object(recon.graphite_ro, 'connection', _fake()):
                first = self.client.get(URL, {'months': 6, 'asof': '2026-09-08'}).json()
                second = self.client.get(URL, {'months': 6, 'asof': '2026-09-08'}).json()
        self.assertFalse(first['cached'])
        self.assertTrue(second['cached'])
        self.assertEqual(spy.call_count, 1, 'the second read should not recompute')
        self.assertEqual(first['verdict'], second['verdict'])

    def test_an_outage_is_never_cached(self):
        """A replica blip would otherwise read as the state of the world for 15 min."""
        from contextlib import contextmanager
        from django.core.cache import cache
        cache.clear()

        @contextmanager
        def boom():
            raise RuntimeError('replica down')
            yield  # pragma: no cover

        with patch.object(recon.graphite_ro, 'connection', boom):
            self.assertFalse(self.client.get(URL, {'asof': '2026-09-08'}).json()['available'])
        with patch.object(recon.graphite_ro, 'connection', _fake()):
            body = self.client.get(URL, {'asof': '2026-09-08'}).json()
        self.assertTrue(body['available'], 'the outage was cached and masked a good read')

    def test_it_is_read_only(self):
        self.assertEqual(self.client.post(URL).status_code, 405)

    def test_sign_in_is_required(self):
        self.assertIn(APIClient().get(URL).status_code, (401, 403))

    def test_it_never_returns_customer_information(self):
        with patch.object(recon.graphite_ro, 'connection', _fake()):
            body = self.client.get(URL, {'asof': '2026-09-08'}).content.decode().lower()
        for banned in ('omang', 'policynumber', 'policy_number', 'firstname', 'surname',
                       'accountnumber', 'cellphone', 'id_number', 'clientnumber'):
            self.assertNotIn(banned, body, banned)

    def test_a_silly_month_count_is_clamped_not_an_error(self):
        with patch.object(recon.graphite_ro, 'connection', _fake()):
            self.assertEqual(self.client.get(URL, {'months': 999}).json()['window_months'], 24)
            self.assertEqual(self.client.get(URL, {'months': 'abc'}).json()['window_months'], 6)


class ReconcileCommandTests(TestCase):
    """The command's exit code is load-bearing: cron reads it."""

    def test_an_outage_exits_non_zero_rather_than_looking_like_a_clean_night(self):
        from contextlib import contextmanager
        from django.core.management import call_command
        from django.core.management.base import CommandError

        @contextmanager
        def boom():
            raise RuntimeError('replica down')
            yield  # pragma: no cover

        with patch.object(recon.graphite_ro, 'connection', boom):
            with self.assertRaises(CommandError) as caught:
                call_command('realpay_ledger_reconcile')
        self.assertIn('outage to report', str(caught.exception))

    def test_an_outage_tells_somebody_before_it_exits(self):
        """There is no MAILTO in any cron file, so raising alone writes to a log
        nobody reads — the two-month silence, rebuilt. Remove the email and this
        goes red."""
        from contextlib import contextmanager
        from django.core.management import call_command
        from django.core.management.base import CommandError
        from django.core import mail
        import io

        @contextmanager
        def boom():
            raise RuntimeError('replica down')
            yield  # pragma: no cover

        with patch.object(recon.graphite_ro, 'connection', boom):
            with self.assertRaises(CommandError):
                call_command('realpay_ledger_reconcile', email=True, stdout=io.StringIO())
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn('could not run', mail.outbox[0].subject)

    def test_a_silent_feed_also_tells_somebody(self):
        """The feed importer can die exactly as the ledger importer did."""
        from django.core.management import call_command
        from django.core.management.base import CommandError
        from django.core import mail
        import io

        data = dict(MONTHS)
        data['2026-07'] = (0, 3742, 0, 3742)
        with patch.object(recon.graphite_ro, 'connection', _fake(data)):
            with self.assertRaises(CommandError) as caught:
                call_command('realpay_ledger_reconcile', months=6, asof='2026-09-08',
                             email=True, stdout=io.StringIO())
        self.assertIn('no rows for 2026-07', str(caught.exception))
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn('RealPay feed', mail.outbox[0].body)

    def test_a_mistyped_date_exits_non_zero(self):
        from django.core.management import call_command
        from django.core.management.base import CommandError

        with self.assertRaises(CommandError):
            call_command('realpay_ledger_reconcile', asof='not-a-date')

    def test_it_reports_the_break_and_can_email_it(self):
        from django.core.management import call_command
        from django.core import mail
        import io

        out = io.StringIO()
        with patch.object(recon.graphite_ro, 'connection', _fake()):
            call_command('realpay_ledger_reconcile', months=6, asof='2026-09-08',
                         email=True, stdout=out)
        printed = out.getvalue()
        self.assertIn('2026-07', printed)
        self.assertIn('BREAK', printed)
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn('not reaching the payment ledger', mail.outbox[0].subject)
        self.assertIn('28,108', mail.outbox[0].body)

    def test_a_clean_run_sends_nothing_unless_asked(self):
        from django.core.management import call_command
        from django.core import mail
        import io

        clean = {'2026-07': (1000, 1000, 0, 0), '2026-08': (1000, 1000, 0, 0)}
        with patch.object(recon.graphite_ro, 'connection', _fake(clean)):
            call_command('realpay_ledger_reconcile', months=2, asof='2026-08-31',
                         email=True, stdout=io.StringIO())
        self.assertEqual(len(mail.outbox), 0)

        with patch.object(recon.graphite_ro, 'connection', _fake(clean)):
            call_command('realpay_ledger_reconcile', months=2, asof='2026-08-31',
                         email=True, always=True, stdout=io.StringIO())
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn('reconcile', mail.outbox[0].subject)
