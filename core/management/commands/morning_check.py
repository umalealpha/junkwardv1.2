"""
morning_check — invoked by .github/workflows/morning-check.yml each 05:00 SAST.

Runs the full prod-side health probe. Emits Markdown to stdout so the
Action can append it to ops/morning-checks/YYYY-MM-DD.md verbatim.

Sections:
  - git head + container status (shell-only — Action does these)
  - Django check
  - pending migrations
  - TB / BS balanced
  - login + user counts
  - related-party-transactions smoke (regression guard)
  - test_write_ops dry-run summary
  - orphan-account scan + auto-backfill if non-zero
  - last 24h ERROR/CRITICAL log lines (count only)

Each section prints `### Title — ✅` or `### Title — ❌`. Exit code is 0
unless an internal raise — the Action interprets ❌ via grep, not exit code.
"""

from datetime import date, timedelta
from decimal import Decimal

from django.contrib.auth.models import User
from django.core.management.base import BaseCommand
from django.core.management import call_command
from django.utils import timezone


def _ok(title):
    print(f"\n### {title} — ✅")


def _fail(title, err=None):
    print(f"\n### {title} — ❌")
    if err:
        print("```")
        print(str(err)[:2000])
        print("```")


def _kv(label, value):
    print(f"- **{label}:** {value}")


class Command(BaseCommand):
    help = 'Daily prod-side morning check. See .github/workflows/morning-check.yml.'

    def add_arguments(self, parser):
        parser.add_argument('--auto-fix', action='store_true',
                            help='If non-zero issues found, attempt safe auto-fixes (orphan backfill).')

    def handle(self, *args, **opts):
        print("## Prod deep checks")
        print()
        print(f"Run: {timezone.now().isoformat()}")

        self._django_check()
        self._migrations()
        self._tb_balanced()
        self._bs_balanced()
        self._login_counts()
        self._related_party_smoke()
        self._orphans(auto_fix=opts['auto_fix'])
        self._test_write_ops()
        self._error_log_summary()

    # ── individual checks ──────────────────────────────────────────────────

    def _django_check(self):
        try:
            call_command('check')
            _ok('Django check')
        except Exception as exc:
            _fail('Django check', exc)

    def _migrations(self):
        try:
            from django.db.migrations.loader import MigrationLoader
            from django.db import connection
            loader = MigrationLoader(connection)
            unapplied = [
                f"{app}.{name}" for app, name in loader.graph.leaf_nodes()
                if (app, name) not in loader.applied_migrations
            ]
            if unapplied:
                _fail('Migrations pending', '\n'.join(unapplied[:20]))
            else:
                _ok('Migrations')
                _kv('count', f'{len(loader.applied_migrations)} applied')
        except Exception as exc:
            _fail('Migrations', exc)

    def _tb_balanced(self):
        try:
            from reporting.reports import build_trial_balance
            r = build_trial_balance(timezone.localdate())
            balanced = r.get('totals', {}).get('balanced')
            if balanced:
                _ok('Trial Balance balanced')
                _kv('total_debits',  r['totals'].get('total_debits'))
                _kv('total_credits', r['totals'].get('total_credits'))
                _kv('rows', len(r.get('rows', [])))
            else:
                _fail('Trial Balance NOT balanced',
                      f"debits={r['totals'].get('total_debits')} credits={r['totals'].get('total_credits')}")
        except Exception as exc:
            _fail('Trial Balance', exc)

    def _bs_balanced(self):
        try:
            from reporting.reports import build_balance_sheet
            r = build_balance_sheet(timezone.localdate())
            balanced = r['totals'].get('balanced')
            if balanced:
                _ok('Balance Sheet balanced')
                _kv('total_assets',   r['totals']['total_assets'])
                _kv('liab_and_equity', r['totals']['liabilities_and_equity'])
            else:
                _fail('Balance Sheet NOT balanced',
                      f"assets={r['totals']['total_assets']} L&E={r['totals']['liabilities_and_equity']}")
        except Exception as exc:
            _fail('Balance Sheet', exc)

    def _login_counts(self):
        try:
            from payroll.models import Employee
            users_total = User.objects.count()
            users_active = User.objects.filter(is_active=True).count()
            emp_total = Employee.objects.count()
            emp_linked = Employee.objects.filter(user__isnull=False).count()
            if emp_linked < emp_total:
                _fail('Login coverage',
                      f'{emp_total - emp_linked} Employees still missing a User FK')
            else:
                _ok('Login coverage')
            _kv('users',     f'{users_active} active / {users_total} total')
            _kv('employees', f'{emp_linked} linked / {emp_total} total')
        except Exception as exc:
            _fail('Login coverage', exc)

    def _related_party_smoke(self):
        try:
            from reporting.reports import build_related_party_transactions
            r = build_related_party_transactions(
                timezone.localdate() - timedelta(days=30), timezone.localdate(),
            )
            _ok('Related-party regression guard')
            _kv('window_count',      r['summary']['window_count'])
            _kv('fiscal_year_count', r['summary']['fiscal_year_count'])
        except Exception as exc:
            _fail('Related-party regression guard', exc)

    def _orphans(self, *, auto_fix):
        try:
            from ledger.models import Account
            n = Account.objects.filter(owner_company__isnull=True).count()
            if n == 0:
                _ok('Orphan accounts')
                _kv('count', 0)
                return
            _fail('Orphan accounts detected', f'{n} accounts with NULL owner_company')
            if auto_fix:
                try:
                    call_command('backfill_account_owner_company')
                    after = Account.objects.filter(owner_company__isnull=True).count()
                    _kv('after backfill', after)
                except Exception as exc:
                    _kv('auto-fix failed', exc)
        except Exception as exc:
            _fail('Orphan accounts', exc)

    def _test_write_ops(self):
        # Skip on the morning run — test_write_ops creates a real PO + posts
        # depreciation JEs. Run manually only.
        _ok('test_write_ops — skipped (manual)')

    def _error_log_summary(self):
        # The Action grabs logs separately via `docker logs --since 24h`.
        # We just emit a marker here so the report section stays consistent.
        _ok('Error log summary — see Action step below')
