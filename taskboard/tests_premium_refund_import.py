"""Approved Graphite premium refunds become Omni payment requests, once each.

CFO 2026-08-11: accountants were typing every premium refund twice — into the
Graphite refund module and again into Omni by hand — and because nothing arrived in
Omni he was not authorising the payments at all.

The rule that matters most here is ONCE EACH. On 2026-08-09 eleven payment groups
went out twice because nothing tied a payment back to its source. So `graphite_ref`
carries a UNIQUE constraint and these tests prove a second run cannot double-pay.
"""
from decimal import Decimal
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from django.contrib.auth.models import User
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import IntegrityError, transaction
from django.test import TestCase

from taskboard.models import OmniTask, PaymentRequest

KEETILE = 'kmokhendo@alphadirect.co.bw'
TLAMELO = 'tchimidza@alphadirect.co.bw'

REFUNDS = [
    {'graphite_ref': 'RFND-000010', 'policy_number': 'DOMG2025153455',
     'customer_name': 'A Policyholder', 'refund_amount': Decimal('1629.37'),
     'currency': 'BWP', 'reason': 'Debited after cancellation', 'status': 'approved'},
    {'graphite_ref': 'RFND-000006', 'policy_number': 'MIS2024079662',
     'customer_name': 'Another Policyholder', 'refund_amount': Decimal('474.00'),
     'currency': 'BWP', 'reason': 'Multiple deductions', 'status': 'approved'},
]


class PremiumRefundImportTests(TestCase):
    def setUp(self):
        for em in (KEETILE, TLAMELO):
            User.objects.create_user(em.split('@')[0], email=em, password='x')
        # The importer now refuses to raise anything without an authoriser, because
        # a request with no CFO task is a request the CFO cannot pay.
        User.objects.create_superuser(
            'pganesharajah', email='pganesharajah@alphadirect.co.bw', password='x')

    def _run(self, rows=None, **kw):
        """Drive the real command with Graphite stubbed at the bridge boundary."""
        rows = REFUNDS if rows is None else rows
        with patch('integrations.graphite_ro.is_configured', return_value=True), \
             patch('integrations.graphite_ro.columns_of',
                   return_value=['graphite_ref', 'policy_number', 'customer_name',
                                 'refund_amount', 'currency', 'reason', 'status',
                                 'approved_at', 'deleted_at']), \
             patch('integrations.graphite_ro.query', return_value=rows):
            call_command('import_graphite_refunds', **kw)

    # ── the core behaviour ───────────────────────────────────────────────────
    def test_a_dry_run_writes_nothing(self):
        self._run()
        self.assertEqual(PaymentRequest.objects.count(), 0,
                         'the default must never write')

    def test_commit_raises_one_request_per_refund(self):
        self._run(commit=True)
        self.assertEqual(PaymentRequest.objects.count(), 2)
        prs = {p.graphite_ref: p for p in PaymentRequest.objects.all()}
        self.assertEqual(set(prs), {'RFND-000010', 'RFND-000006'})
        self.assertEqual(prs['RFND-000010'].total, Decimal('1629.37'))

    def test_it_is_entered_by_keetile_and_awaits_the_cfo(self):
        self._run(commit=True)
        p = PaymentRequest.objects.get(graphite_ref='RFND-000006')
        self.assertEqual(p.created_by.email, KEETILE)
        self.assertEqual(p.status, PaymentRequest.Status.PENDING_CFO)

    def test_it_lands_on_the_premium_refund_category(self):
        self._run(commit=True)
        for p in PaymentRequest.objects.all():
            self.assertEqual(p.category, PaymentRequest.Category.PREMIUM_REFUND)

    def test_the_refund_carries_the_wording_finance_asked_for(self):
        """Finance, 2026-08-20: "Client Refunds: ALPHA DIRECT REFUND + policy
        number", and for our own reference "REFUND + policy number + initials".

        The create screen refuses this category, so this importer is the ONLY
        place a client refund is ever raised — if the format is not set here it
        can never appear on a statement anywhere.
        """
        self._run(commit=True)
        p = PaymentRequest.objects.get(graphite_ref='RFND-000010')
        self.assertEqual(p.bank_narration, 'ALPHA DIRECT REFUND DOMG2025153455')
        self.assertEqual(p.bank_our_reference, 'REFUND DOMG2025153455 AP')
        self.assertEqual(p.bank_payment_type, 'client_refund')

    def test_the_omni_number_is_not_what_the_bank_is_told(self):
        """Their third constraint: "The Omni payment number should come off the
        bank narration… It tells me nothing when I am matching a bank line back
        to a claim or an invoice."
        """
        self._run(commit=True)
        for p in PaymentRequest.objects.all():
            self.assertNotIn(p.ref, p.bank_narration)
            self.assertNotIn(p.ref, p.bank_our_reference)

    # ── the rule that matters: once each ────────────────────────────────────
    def test_running_twice_does_not_double_pay(self):
        self._run(commit=True)
        self._run(commit=True)
        self.assertEqual(PaymentRequest.objects.count(), 2,
                         'a second run must not raise the same refunds again')

    def test_the_database_itself_refuses_a_duplicate(self):
        """Not just the query — the constraint. A concurrent run must lose."""
        self._run(commit=True)
        existing = PaymentRequest.objects.get(graphite_ref='RFND-000006')
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                PaymentRequest.objects.create(
                    ref='PAY/ADIC/2026/08/11/9999', entity='ADIC',
                    category=PaymentRequest.Category.PREMIUM_REFUND,
                    subject='duplicate attempt', opening_balance=Decimal('0.00'),
                    status=PaymentRequest.Status.PENDING_CFO,
                    graphite_ref=existing.graphite_ref)

    def test_hand_raised_requests_do_not_collide_on_the_blank_default(self):
        """The unique constraint is partial, so every ordinary request still saves."""
        for i in range(3):
            PaymentRequest.objects.create(
                ref=f'PAY/ADIC/2026/08/11/000{i}', entity='ADIC',
                category=PaymentRequest.Category.SUPPLIER,
                subject=f'ordinary {i}', opening_balance=Decimal('0.00'),
                status=PaymentRequest.Status.PENDING_FINANCE)
        self.assertEqual(PaymentRequest.objects.filter(graphite_ref='').count(), 3)

    # ── only finished refunds ───────────────────────────────────────────────
    def test_only_approved_refunds_are_asked_for(self):
        """The status filter belongs in the SQL, so unapproved refunds never arrive."""
        captured = {}

        def fake_query(sql, params=None, **kw):
            captured['sql'] = sql
            captured['params'] = params
            return []

        with patch('integrations.graphite_ro.is_configured', return_value=True), \
             patch('integrations.graphite_ro.columns_of',
                   return_value=['graphite_ref', 'status', 'refund_amount']), \
             patch('integrations.graphite_ro.query', side_effect=fake_query):
            call_command('import_graphite_refunds')
        self.assertIn('status', captured['sql'])
        self.assertIn('approved', captured['params'])

    # ── the FNB reminder ────────────────────────────────────────────────────
    def test_it_leaves_an_fnb_task_for_keetile_and_tlamelo(self):
        self._run(commit=True)
        tasks = OmniTask.objects.filter(source='premium_refund_import')
        self.assertEqual(tasks.count(), 2, 'one each — a task has a single assignee')
        self.assertEqual({t.assignee.email for t in tasks}, {KEETILE, TLAMELO})
        body = tasks.first().body
        self.assertIn('RFND-000010', body)
        self.assertIn('FNB', body)

    def test_no_fnb_task_when_nothing_was_raised(self):
        self._run(rows=[], commit=True)
        self.assertEqual(OmniTask.objects.filter(
            source='premium_refund_import').count(), 0,
            'a reminder to pay nothing is noise')

    def test_the_second_run_does_not_re_task_them(self):
        self._run(commit=True)
        self._run(commit=True)
        self.assertEqual(OmniTask.objects.filter(
            source='premium_refund_import').count(), 2,
            'the same batch must not be chased twice')


class HandKeyedRefundIsRefusedTests(TestCase):
    """Only the importer may raise a premium refund.

    Two open routes to the same refund is exactly how a payment goes out twice —
    once from the feed, once from whoever did not know the feed existed.
    """

    def test_the_create_endpoint_refuses_the_category(self):
        from rest_framework.test import APIRequestFactory, force_authenticate

        from taskboard.payment_views import payment_requests
        u = User.objects.create_superuser('pr-hand', 'hand@example.invalid', 'x')
        req = APIRequestFactory().post('/api/v1/payment-requests/', {
            'entity': 'ADIC', 'category': 'premium_refund',
            'currency': 'BWP', 'payee': 'Someone',
            'line_items': [{'description': 'x', 'amount': '10.00'}],
        }, format='json')
        force_authenticate(req, user=u)
        resp = payment_requests(req)
        self.assertEqual(resp.status_code, 400, resp.data)
        self.assertEqual(resp.data.get('control'), 'PAY-REFUND-01')
        self.assertIn('automatically', resp.data['detail'])
        self.assertEqual(PaymentRequest.objects.count(), 0)

    def test_an_ordinary_category_still_passes_this_check(self):
        """The block must be the refund category only, not every create."""
        from taskboard.models import PaymentRequest as P
        self.assertNotIn('supplier', P.IMPORTER_ONLY_CATEGORIES)
        self.assertIn('premium_refund', P.IMPORTER_ONLY_CATEGORIES)


class ImportedRefundIsPayableTests(TestCase):
    """Fable review 2026-08-11 — the eight fixes, each with its own failing test.

    The worst finding was not a crash: the requests were being raised correctly and
    were simply unpayable. The only route to PAID in Omni is completing the payment
    request's linked task, so a request with no task sat at PENDING_CFO for ever and
    the only button the CFO had was "clear". A feature whose headline is "I will be
    approving it" has to be tested through to the approval, not to the insert.
    """

    def setUp(self):
        for em in (KEETILE, TLAMELO):
            User.objects.create_user(em.split('@')[0], email=em, password='x')
        self.cfo = User.objects.create_superuser(
            'pganesharajah', email='pganesharajah@alphadirect.co.bw', password='x')

    def _run(self, rows=None, **kw):
        rows = REFUNDS if rows is None else rows
        with patch('integrations.graphite_ro.is_configured', return_value=True), \
             patch('integrations.graphite_ro.columns_of',
                   return_value=['graphite_ref', 'policy_number', 'customer_name',
                                 'refund_amount', 'currency', 'reason', 'status',
                                 'approved_at', 'deleted_at']), \
             patch('integrations.graphite_ro.query', return_value=rows):
            call_command('import_graphite_refunds', **kw)

    def test_every_imported_request_carries_the_cfo_authorisation_task(self):
        self._run(commit=True)
        for p in PaymentRequest.objects.all():
            self.assertIsNotNone(
                p.task,
                'no task means no pay button — the CFO cannot authorise it at all')
            self.assertEqual(p.task.assignee, self.cfo)
            self.assertEqual(p.task.source, 'payment_request',
                             'the duplicate-payment check only fires on this source')

    def test_a_refund_with_no_reference_is_refused(self):
        """A blank reference defeats the dedupe: partial-unique does not cover it."""
        rows = [dict(REFUNDS[0], graphite_ref='')]
        self._run(rows=rows, commit=True)
        self.assertEqual(PaymentRequest.objects.count(), 0)

    def test_a_refund_with_no_reference_would_otherwise_repeat_nightly(self):
        rows = [dict(REFUNDS[0], graphite_ref='')]
        self._run(rows=rows, commit=True)
        self._run(rows=rows, commit=True)
        self.assertEqual(PaymentRequest.objects.count(), 0,
                         'two nights, two payment requests, one refund')

    def test_an_unreadable_amount_is_refused_not_paid_as_zero(self):
        rows = [dict(REFUNDS[0], refund_amount='not a number')]
        self._run(rows=rows, commit=True)
        self.assertEqual(PaymentRequest.objects.count(), 0,
                         'a parse failure must not become a real 0.00 payment')

    def test_a_refused_amount_still_imports_once_graphite_is_corrected(self):
        """The refund must not lose its one-per-reference slot to a bad figure."""
        ref = REFUNDS[0]['graphite_ref']
        self._run(rows=[dict(REFUNDS[0], refund_amount='')], commit=True)
        self._run(rows=[dict(REFUNDS[0], refund_amount='1629.37')], commit=True)
        p = PaymentRequest.objects.get(graphite_ref=ref)
        self.assertEqual(p.total, Decimal('1629.37'))

    def test_already_imported_refunds_are_excluded_before_the_limit(self):
        """Otherwise the feed goes permanently silent once history passes the limit.

        With the limit applied at the source and an ascending sort, every fetch
        returns the same oldest rows for ever and each new refund falls outside the
        window — while the log reports "Nothing to do".
        """
        self._run(commit=True)
        captured = {}

        def fake_query(sql, params=None, **kw):
            captured['sql'] = sql
            captured['params'] = list(params or [])
            return []

        with patch('integrations.graphite_ro.is_configured', return_value=True), \
             patch('integrations.graphite_ro.columns_of',
                   return_value=['graphite_ref', 'refund_amount', 'status',
                                 'approved_at']), \
             patch('integrations.graphite_ro.query', side_effect=fake_query):
            call_command('import_graphite_refunds')

        self.assertIn('NOT IN', captured['sql'].upper())
        self.assertIn('RFND-000006', captured['params'])
        self.assertIn('RFND-000010', captured['params'])

    def test_the_log_never_carries_a_policy_number(self):
        """C5. This output is appended to a file on the server by the cron."""
        buf = StringIO()
        rows = REFUNDS
        with patch('integrations.graphite_ro.is_configured', return_value=True), \
             patch('integrations.graphite_ro.columns_of',
                   return_value=['graphite_ref', 'policy_number', 'refund_amount',
                                 'currency', 'status', 'approved_at']), \
             patch('integrations.graphite_ro.query', return_value=rows):
            call_command('import_graphite_refunds', stdout=buf)
        out = buf.getvalue()
        for r in rows:
            self.assertNotIn(str(r['policy_number']), out,
                             'a policy number in a log file breaches AD-POL-AI-GOV-001')
        self.assertIn('RFND-000010', out, 'the reference must still be there to act on')

    def test_the_cron_is_registered_with_the_installer(self):
        """A cron file the installer does not name is a job that never runs."""
        root = Path(__file__).resolve().parent.parent
        script = (root / 'infra' / 'install-crons.sh').read_text()
        enabled = script.split('ENABLED=(', 1)[1].split(')', 1)[0]
        self.assertIn('premium-refund-import', enabled)
        self.assertTrue((root / 'infra' / 'cron' / 'premium-refund-import.cron').exists())


class NoHandKeyTaskOnceTheDirectRouteLoadedItTests(TestCase):
    """End to end: a refund already in FNB gets its request, and NO task.

    The helper test in customer_refunds/test_keetile_automation.py stays green
    if the filter line calling it is removed, so it does not prove the wiring.
    This drives the real command and asserts on OmniTask, which is what would
    actually send Keetile to key a payment already sitting in the bank.
    """

    def setUp(self):
        User.objects.create_user('keetile', 'kmokhendo@alphadirect.co.bw', 'x')
        User.objects.create_superuser('pganesharajah', 'cfo@alphadirect.co.bw', 'x')
        User.objects.create_user('tlamelo', 'tchimidza@alphadirect.co.bw', 'x')

    def _run(self, rows, **kw):
        with patch('integrations.graphite_ro.is_configured', return_value=True),              patch('integrations.graphite_ro.columns_of',
                   return_value=['graphite_ref', 'policy_number', 'customer_name',
                                 'refund_amount', 'currency', 'reason', 'status',
                                 'approved_at', 'deleted_at']),              patch('integrations.graphite_ro.query', return_value=rows):
            call_command('import_graphite_refunds', stdout=StringIO(), **kw)

    def _already_in_fnb(self, graphite_ref):
        """A CustomerRefund carrying a real FNB batch — the direct route ran."""
        from banking.models import BankAccount
        from core.models import Company, Currency
        from customer_refunds.models import CustomerRefund
        from fnb.models import FNBBatchSubmission
        from ledger.models import Account

        Currency.objects.get_or_create(code='BWP', defaults={'name': 'Pula'})
        company, _ = Company.objects.get_or_create(
            code='ADIC', defaults={'name': 'Alpha Direct Insurance Company',
                                   'base_currency_id': 'BWP'})
        gl, _ = Account.objects.get_or_create(
            code='1100', defaults={'name': 'FNB Current Account',
                                   'owner_company': company,
                                   'is_bank_account': True})
        source, _ = BankAccount.objects.get_or_create(
            account_number='62403392335',
            defaults={'gl_account': gl, 'bank_name': 'FNB',
                      'account_name': 'Alpha Direct Current Account',
                      'currency_code_id': 'BWP'})
        batch = FNBBatchSubmission.objects.create(
            idempotency_key=f'BATCH-{graphite_ref}', source_account=source)
        CustomerRefund.objects.create(
            graphite_ref=graphite_ref, policy_number='MIS2026009999',
            refund_amount=Decimal('1629.37'), currency='BWP', fnb_batch=batch)

    def test_a_refund_already_in_fnb_raises_the_request_but_no_task(self):
        self._already_in_fnb('RFND-000010')
        self._run([REFUNDS[0]], commit=True)

        self.assertEqual(
            PaymentRequest.objects.filter(graphite_ref='RFND-000010').count(), 1,
            'it must still appear on the Refunds tab')
        self.assertEqual(
            OmniTask.objects.filter(source='premium_refund_import').count(), 0,
            'nobody may be told to key a payment that is already in FNB')

    def test_a_refund_not_yet_in_fnb_still_gets_its_task(self):
        """The guard must not silence the ordinary case."""
        self._run([REFUNDS[0]], commit=True)
        self.assertTrue(
            OmniTask.objects.filter(source='premium_refund_import').exists(),
            'a refund the direct route has not loaded still needs hand-keying')


class GraphiteWillNotShowUsTheTableTests(TestCase):
    """A table we cannot SEE must stop the run, not become broken SQL.

    Live on 2026-09-18: Graphite's read-only user has no SELECT grant on
    `refund_requests`, so information_schema — which is filtered by privilege —
    returned ZERO columns. The importer built `SELECT  FROM refund_requests`
    with an empty select list, and MySQL answered 1064 SYNTAX ERROR. Every
    night since August the log blamed our SQL for a missing GRANT while
    Keetile's approved refunds silently never arrived.
    """

    def setUp(self):
        for em in (KEETILE, TLAMELO):
            User.objects.create_user(em.split('@')[0], email=em, password='x')
        User.objects.create_superuser(
            'pganesharajah', email='pganesharajah@alphadirect.co.bw', password='x')

    def _run_with_columns(self, columns):
        with patch('integrations.graphite_ro.is_configured', return_value=True), \
             patch('integrations.graphite_ro.columns_of', return_value=columns), \
             patch('integrations.graphite_ro.query') as q:
            with self.assertRaises(CommandError) as caught:
                call_command('import_graphite_refunds')
            return caught.exception, q

    def test_no_columns_stops_the_run_and_names_the_grant(self):
        exc, q = self._run_with_columns([])
        self.assertIn('refund_requests', str(exc))
        self.assertIn('GRANT', str(exc).upper())
        q.assert_not_called()          # and it never reaches the database

    def test_a_reshaped_table_stops_the_run_and_shows_what_is_there(self):
        exc, q = self._run_with_columns(['id', 'created', 'amount_cents'])
        self.assertIn('amount_cents', str(exc))
        q.assert_not_called()

    def test_a_readable_table_still_runs(self):
        """The guard must not fire on the normal case."""
        with patch('integrations.graphite_ro.is_configured', return_value=True), \
             patch('integrations.graphite_ro.columns_of',
                   return_value=['graphite_ref', 'policy_number', 'customer_name',
                                 'refund_amount', 'currency', 'reason', 'status',
                                 'approved_at', 'deleted_at']), \
             patch('integrations.graphite_ro.query', return_value=REFUNDS):
            call_command('import_graphite_refunds')     # no raise

    def test_a_blocked_run_tells_a_person_not_just_the_log(self):
        """The alert must actually go. Its import sits at module level so a
        renamed notifier fails loudly at load, not silently inside the guard."""
        with patch('taskboard.management.commands.import_graphite_refunds'
                   '.send_html_with_cfo_cc') as send, \
             patch('integrations.graphite_ro.is_configured', return_value=True), \
             patch('integrations.graphite_ro.columns_of', return_value=[]), \
             patch('integrations.graphite_ro.query'):
            with self.assertRaises(CommandError):
                call_command('import_graphite_refunds', commit=True)
        self.assertTrue(send.called, 'the blocked import told nobody')
        self.assertIn('BLOCKED', send.call_args.kwargs['subject'])

    def test_a_manual_dry_run_does_not_email_finance(self):
        with patch('taskboard.management.commands.import_graphite_refunds'
                   '.send_html_with_cfo_cc') as send:
            self._run_with_columns([])          # default is a dry run
        send.assert_not_called()
