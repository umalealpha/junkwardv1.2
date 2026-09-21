"""
integrations/tests.py

Tests for the Graphite V2 Finance payment-transaction feed. The live API needs
a Sanctum token, so every test mocks the HTTP layer — these pass with no token
set and no network.

Run:  python manage.py test integrations
"""

from __future__ import annotations

from datetime import date
from unittest import mock

from django.test import TestCase, override_settings

from integrations import graphite_finance as gf
from integrations.graphite_finance import (
    GraphiteFinanceAPIError,
    GraphiteFinanceClient,
    GraphiteFinanceConfig,
    GraphiteFinanceNotConfigured,
    ingest_row,
    iter_windows,
    row_to_defaults,
)
from integrations.models import GraphitePaymentSyncRun, GraphitePaymentTransaction
from reporting.graphite_payments import build_graphite_payments


CONFIGURED = dict(
    GRAPHITE_FINANCE_API_BASE='https://graphite-v2-prod-be.example/',
    GRAPHITE_FINANCE_API_TOKEN='tok_test',
    GRAPHITE_FINANCE_TIMEOUT_SECONDS=5,
)


def _row(graphite_id=1, **over):
    row = {
        'id': graphite_id,
        'policy_number': 'POL-001',
        'reference_number': 'REF-001',
        'amount': 199.95,
        'payment_method': 'DPO',
        'status': 'Success',
        'is_refund': False,
        'is_reverse': False,
        'paid_at': '2026-05-10 09:30:00',
        'paid_on': '2026-05-10',
        'recorded_at': '2026-05-10 09:30:05',
        'updated_at': '2026-05-10 09:31:00',
        'note': '',
        'payment_frequency': 'RECURRING',
        'policy': {'id': 10, 'product_id': 2, 'plan_id': 3,
                   'product_name': 'Motor', 'plan_name': 'Comprehensive'},
        'customer_name': 'Test Customer',
        'agent_name': 'Test Agent',
        'dpo': {'trans_id': 'T123', 'company_ref': 'C9', 'token': 'tok'},
    }
    row.update(over)
    return row


class _FakeResp:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code
        self.text = str(payload)

    def json(self):
        return self._payload


# ---------------------------------------------------------------------------
# Window chunking
# ---------------------------------------------------------------------------

class IterWindowsTests(TestCase):
    def test_single_window_when_short(self):
        wins = list(iter_windows(date(2026, 5, 1), date(2026, 5, 15)))
        self.assertEqual(wins, [(date(2026, 5, 1), date(2026, 5, 15))])

    def test_chunks_at_30_days(self):
        wins = list(iter_windows(date(2026, 1, 1), date(2026, 3, 31)))
        # Every chunk must be < 31 days so the server never 422s.
        for a, b in wins:
            self.assertLess((b - a).days, gf.MAX_WINDOW_DAYS)
        # Contiguous, no gaps, full coverage.
        self.assertEqual(wins[0][0], date(2026, 1, 1))
        self.assertEqual(wins[-1][1], date(2026, 3, 31))

    def test_rejects_reversed_range(self):
        with self.assertRaises(ValueError):
            list(iter_windows(date(2026, 5, 2), date(2026, 5, 1)))


# ---------------------------------------------------------------------------
# Config / client
# ---------------------------------------------------------------------------

class ConfigTests(TestCase):
    @override_settings(**CONFIGURED)
    def test_is_configured_true(self):
        self.assertTrue(GraphiteFinanceConfig.from_settings().is_configured)

    @override_settings(GRAPHITE_FINANCE_API_BASE='', GRAPHITE_FINANCE_API_TOKEN='')
    def test_is_configured_false(self):
        self.assertFalse(GraphiteFinanceConfig.from_settings().is_configured)


class ClientTests(TestCase):
    @override_settings(GRAPHITE_FINANCE_API_BASE='', GRAPHITE_FINANCE_API_TOKEN='')
    def test_unconfigured_raises(self):
        with self.assertRaises(GraphiteFinanceNotConfigured):
            GraphiteFinanceClient().list_payments(date(2026, 5, 1), date(2026, 5, 2))

    @override_settings(**CONFIGURED)
    def test_window_cap_rejected_client_side(self):
        with self.assertRaises(ValueError):
            GraphiteFinanceClient().list_payments(date(2026, 1, 1), date(2026, 3, 1))

    @override_settings(**CONFIGURED)
    @mock.patch('integrations.graphite_finance.requests.get')
    def test_list_payments_params_and_auth(self, mget):
        mget.return_value = _FakeResp({'data': [_row()], 'meta': {'has_more': False}})
        GraphiteFinanceClient().list_payments(
            date(2026, 5, 1), date(2026, 5, 31),
            partner='DPO', status='success', is_refund=False, limit=999,
        )
        _, kwargs = mget.call_args
        self.assertEqual(kwargs['headers']['Authorization'], 'Bearer tok_test')
        self.assertEqual(kwargs['params']['date_from'], '2026-05-01')
        self.assertEqual(kwargs['params']['date_to'], '2026-05-31')
        self.assertEqual(kwargs['params']['partner'], 'DPO')
        self.assertEqual(kwargs['params']['status'], 'success')
        self.assertEqual(kwargs['params']['is_refund'], 0)
        self.assertEqual(kwargs['params']['limit'], 999)

    @override_settings(**CONFIGURED)
    @mock.patch('integrations.graphite_finance.requests.get')
    def test_limit_capped_at_max(self, mget):
        mget.return_value = _FakeResp({'data': [], 'meta': {'has_more': False}})
        GraphiteFinanceClient().list_payments(date(2026, 5, 1), date(2026, 5, 2), limit=99999)
        self.assertEqual(mget.call_args.kwargs['params']['limit'], gf.MAX_LIMIT)

    @override_settings(**CONFIGURED)
    @mock.patch('integrations.graphite_finance.requests.get')
    def test_non_2xx_raises(self, mget):
        mget.return_value = _FakeResp({'error': 'nope'}, status_code=422)
        with self.assertRaises(GraphiteFinanceAPIError):
            GraphiteFinanceClient().list_payments(date(2026, 5, 1), date(2026, 5, 2))

    @override_settings(**CONFIGURED)
    @mock.patch('integrations.graphite_finance.requests.get')
    def test_iter_window_follows_cursor(self, mget):
        mget.side_effect = [
            _FakeResp({'data': [_row(1), _row(2)],
                       'meta': {'has_more': True, 'next_cursor': 'CUR2'}}),
            _FakeResp({'data': [_row(3)],
                       'meta': {'has_more': False, 'next_cursor': None}}),
        ]
        rows = list(GraphiteFinanceClient().iter_window(date(2026, 5, 1), date(2026, 5, 31)))
        self.assertEqual([r['id'] for r in rows], [1, 2, 3])
        self.assertEqual(mget.call_args_list[1].kwargs['params']['cursor'], 'CUR2')


# ---------------------------------------------------------------------------
# Ingest / upsert
# ---------------------------------------------------------------------------

class IngestTests(TestCase):
    def test_row_to_defaults_flattens_nested(self):
        d = row_to_defaults(_row(payment_method='RealPay', status='SUCCESS'))
        self.assertEqual(d['product_name'], 'Motor')
        self.assertEqual(d['plan_id'], 3)
        self.assertEqual(d['dpo_trans_id'], 'T123')
        self.assertEqual(d['status_norm'], 'success')   # normalised casing
        self.assertIsNotNone(d['source_recorded_at'])

    def test_ingest_is_idempotent(self):
        ingest_row(_row(1, status='Success'))
        self.assertEqual(GraphitePaymentTransaction.objects.count(), 1)
        # Re-pull same id with a status change → update in place, not duplicate.
        obj, created = ingest_row(_row(1, status='Reversed'))
        self.assertFalse(created)
        self.assertEqual(GraphitePaymentTransaction.objects.count(), 1)
        obj.refresh_from_db()
        self.assertEqual(obj.status, 'Reversed')
        self.assertEqual(obj.status_norm, 'reversed')

    def test_ingest_requires_id(self):
        with self.assertRaises(ValueError):
            ingest_row(_row(1, id=None))


# ---------------------------------------------------------------------------
# Report builder
# ---------------------------------------------------------------------------

class ReportTests(TestCase):
    def setUp(self):
        ingest_row(_row(1, payment_method='DPO', status='Success', amount=100))
        ingest_row(_row(2, payment_method='DPO', status='Failed', amount=50))
        ingest_row(_row(3, payment_method='RealPay', status='Success', amount=25))

    def test_summary_groups_and_totals(self):
        rep = build_graphite_payments()
        self.assertEqual(rep['summary']['total_count'], 3)
        self.assertEqual(rep['summary']['total_amount'], 175.0)
        partners = {r['partner']: r for r in rep['summary']['by_partner']}
        self.assertEqual(partners['DPO']['count'], 2)
        self.assertEqual(partners['DPO']['amount'], 150.0)
        statuses = {r['status']: r for r in rep['summary']['by_status']}
        self.assertIn('success', statuses)
        self.assertIn('failed', statuses)

    def test_partner_filter(self):
        rep = build_graphite_payments(partner='RealPay')
        self.assertEqual(rep['summary']['total_count'], 1)
        self.assertEqual(rep['rows'][0]['payment_method'], 'RealPay')


# ---------------------------------------------------------------------------
# Management command
# ---------------------------------------------------------------------------

class CommandTests(TestCase):
    @override_settings(GRAPHITE_FINANCE_API_BASE='', GRAPHITE_FINANCE_API_TOKEN='')
    def test_command_skips_when_unconfigured(self):
        from django.core.management import call_command
        from django.core.management.base import CommandError

        with self.assertRaises(CommandError):
            call_command('pull_graphite_payments', '--days', '1')
        run = GraphitePaymentSyncRun.objects.latest('created_at')
        self.assertEqual(run.status, GraphitePaymentSyncRun.Status.SKIPPED)

    @override_settings(**CONFIGURED)
    @mock.patch('integrations.graphite_finance.requests.get')
    def test_command_ingests_and_records_run(self, mget):
        from django.core.management import call_command

        mget.return_value = _FakeResp({'data': [_row(1), _row(2)],
                                       'meta': {'has_more': False}})
        call_command('pull_graphite_payments', '--days', '1')
        self.assertEqual(GraphitePaymentTransaction.objects.count(), 2)
        run = GraphitePaymentSyncRun.objects.latest('created_at')
        self.assertEqual(run.status, GraphitePaymentSyncRun.Status.SUCCESS)
        self.assertEqual(run.rows_created, 2)

    @override_settings(**CONFIGURED)
    @mock.patch('integrations.graphite_finance.requests.get')
    def test_command_dry_run_writes_nothing(self, mget):
        from django.core.management import call_command

        mget.return_value = _FakeResp({'data': [_row(1)], 'meta': {'has_more': False}})
        call_command('pull_graphite_payments', '--days', '1', '--dry-run')
        self.assertEqual(GraphitePaymentTransaction.objects.count(), 0)
        run = GraphitePaymentSyncRun.objects.latest('created_at')
        self.assertTrue(run.dry_run)
        self.assertEqual(run.rows_seen, 1)


# ---------------------------------------------------------------------------
# Time Doctor token-expiry decoder (renewal reminder — CFO 2026-07-14)
# ---------------------------------------------------------------------------

import base64 as _b64
import json as _json
from datetime import datetime as _dt, timezone as _tz

from integrations.timedoctor import jwt_expiry


def _make_jwt(exp=None):
    """Build a signature-less JWT with an optional exp claim, for jwt_expiry()."""
    def part(obj):
        raw = _json.dumps(obj).encode()
        return _b64.urlsafe_b64encode(raw).rstrip(b'=').decode()
    payload = {'sub': 'svc'}
    if exp is not None:
        payload['exp'] = exp
    return f'{part({"alg": "HS256", "typ": "JWT"})}.{part(payload)}.sig'


class TimeDoctorJwtExpiryTests(TestCase):
    def test_reads_exp_as_utc(self):
        ts = int(_dt(2026, 12, 25, 8, 0, tzinfo=_tz.utc).timestamp())
        got = jwt_expiry(_make_jwt(exp=ts))
        self.assertIsNotNone(got)
        self.assertEqual(got, _dt(2026, 12, 25, 8, 0, tzinfo=_tz.utc))

    def test_none_when_no_exp_claim(self):
        self.assertIsNone(jwt_expiry(_make_jwt(exp=None)))

    def test_none_on_garbage(self):
        for bad in ('', 'not-a-jwt', 'only.two', None):
            self.assertIsNone(jwt_expiry(bad))
