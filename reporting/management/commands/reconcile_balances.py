"""
reporting/management/commands/reconcile_balances.py

CFO directive 2026-05-24: any disagreement between two builders reading
the same posted JEs is an exception. This command runs every check in
`reporting.reconciliation_checks.CHECKS` and upserts a Reconciliation
row per A-vs-B pair. New exceptions optionally get a DeepSeek
explanation.

Usage:
    python manage.py reconcile_balances --company ADIC
    python manage.py reconcile_balances --company ADIC --ai     # fire DeepSeek
    python manage.py reconcile_balances --all                   # every company
"""
from __future__ import annotations

import json
from datetime import datetime
from decimal import Decimal
from typing import Iterable

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from core.models import Company
from reporting.models import Reconciliation
from reporting.reconciliation_checks import run_all


THRESHOLD_BWP = Decimal('1')        # ignore sub-Pula rounding noise
SEV_HIGH_PCT  = Decimal('5')        # |%| >= 5
SEV_MED_PCT   = Decimal('0.5')      # 0.5 <= |%| < 5


def _severity(pct: Decimal) -> str:
    a = abs(pct)
    if a >= SEV_HIGH_PCT:
        return 'high'
    if a >= SEV_MED_PCT:
        return 'medium'
    return 'low'


class Command(BaseCommand):
    help = 'Reconcile every "two builders, one number" check; upsert exceptions.'

    def add_arguments(self, parser):
        parser.add_argument('--company', type=str, default='',
                            help='Company.code (e.g. ADIC). Omit for --all.')
        parser.add_argument('--all', action='store_true',
                            help='Run for every active company.')
        parser.add_argument('--ai',  action='store_true',
                            help='Fire DeepSeek to explain new/changed exceptions.')
        parser.add_argument('--clear-resolved', action='store_true',
                            help='Delete previously-open rows that now agree.')

    def handle(self, *args, **opts):
        companies = self._target_companies(opts)
        if not companies:
            raise CommandError('No companies to reconcile. Pass --company or --all.')

        rows_in = rows_out_open = rows_changed = rows_cleared = 0
        for c in companies:
            self.stdout.write(self.style.MIGRATE_HEADING(
                f'\nReconciling {c.code} ({c.id})'
            ))
            results = list(run_all(str(c.id)))
            rows_in += len(results)
            seen_keys = set()
            for r in results:
                seen_keys.add(self._key(c, r))
                created, changed = self._upsert(c, r)
                if created or changed:
                    rows_changed += 1
                if Reconciliation.objects.filter(
                    company=c,
                    period_label=r['period_label'],
                    metric=r['metric'],
                    source_a_name=r['source_a_name'],
                    source_b_name=r['source_b_name'],
                    status='open',
                ).exists():
                    rows_out_open += 1

            if opts['clear_resolved']:
                rows_cleared += self._sweep_resolved(c, seen_keys)

        if opts['ai']:
            self._run_deepseek()

        self.stdout.write(self.style.SUCCESS(
            f'\nDone. checks_run={rows_in}  changed={rows_changed}  '
            f'open_after={rows_out_open}  resolved_swept={rows_cleared}'
        ))

    # ──────────────────────────────────────────────────────────────
    def _target_companies(self, opts) -> list[Company]:
        if opts['all']:
            return list(Company.objects.filter(is_active=True).order_by('code'))
        code = (opts['company'] or '').strip().upper()
        if not code:
            return []
        c = Company.objects.filter(code__iexact=code).first()
        return [c] if c else []

    def _key(self, c: Company, r: dict) -> tuple:
        return (c.id, r['period_label'], r['metric'],
                r['source_a_name'], r['source_b_name'])

    def _upsert(self, c: Company, r: dict) -> tuple[bool, bool]:
        a = Decimal(r['source_a_value'])
        b = Decimal(r['source_b_value'])
        delta = a - b
        # divide-by-zero guard
        base = abs(a) if abs(a) > abs(b) else abs(b)
        pct = (delta / base * Decimal('100')) if base else Decimal('0')

        obj, created = Reconciliation.objects.get_or_create(
            company=c,
            period_label=r['period_label'],
            metric=r['metric'],
            source_a_name=r['source_a_name'],
            source_b_name=r['source_b_name'],
            defaults={
                'period_start':   r['period_start'],
                'period_end':     r['period_end'],
                'source_a_value': a,
                'source_b_value': b,
                'delta_bwp':      delta,
                'delta_pct':      pct,
                'severity':       _severity(pct),
                'status':         'resolved' if abs(delta) < THRESHOLD_BWP else 'open',
            },
        )

        changed = False
        if not created:
            if (obj.source_a_value != a) or (obj.source_b_value != b):
                obj.source_a_value = a
                obj.source_b_value = b
                obj.delta_bwp      = delta
                obj.delta_pct      = pct
                obj.severity       = _severity(pct)
                # auto-resolve if delta closed; reopen if it returned
                if abs(delta) < THRESHOLD_BWP:
                    if obj.status != 'resolved':
                        obj.status      = 'resolved'
                        obj.resolved_at = timezone.now()
                else:
                    # someone marked explained but delta is still real;
                    # leave their explanation alone but bump status back
                    if obj.status == 'resolved':
                        obj.status      = 'open'
                        obj.resolved_at = None
                # invalidate AI explanation so the next --ai pass refreshes
                obj.ai_reviewed_at = None
                obj.save()
                changed = True

        self.stdout.write(
            f'  {r["metric"]:<32} {r["period_label"]:<8} '
            f'{r["source_a_name"][:22]:<22} {a:>15,.2f}  '
            f'{r["source_b_name"][:22]:<22} {b:>15,.2f}  '
            f'delta={delta:>+13,.2f}  pct={pct:>+7.2f}%  '
            f'{obj.status} ({obj.severity})'
        )
        return created, changed

    def _sweep_resolved(self, c: Company, seen: set) -> int:
        """Delete pre-existing open rows that didn't appear in this run
        AND are now in 'resolved' state. Keeps the table tidy."""
        stale = Reconciliation.objects.filter(
            company=c, status='resolved',
        )
        n = 0
        for row in stale:
            key = (c.id, row.period_label, row.metric,
                   row.source_a_name, row.source_b_name)
            if key not in seen:
                row.delete()
                n += 1
        return n

    def _run_deepseek(self):
        """Fire one DeepSeek call per OPEN exception missing ai_reviewed_at."""
        try:
            from core.ai_assist import explain_reconciliation
        except ImportError:
            self.stdout.write(self.style.WARNING(
                'core.ai_assist.explain_reconciliation not found; skipping AI pass.'
            ))
            return

        pending = Reconciliation.objects.filter(
            status='open',
            ai_reviewed_at__isnull=True,
        ).order_by('-severity', '-detected_at')[:50]

        if not pending:
            self.stdout.write('No exceptions awaiting AI explanation.')
            return

        for row in pending:
            try:
                review = explain_reconciliation(row)
                row.ai_cause      = (review.get('cause') or '')[:8000]
                row.ai_fix        = (review.get('suggested_fix') or '')[:8000]
                row.ai_confidence = Decimal(str(review.get('confidence', 0)))
                row.ai_reviewed_at = timezone.now()
                row.save(update_fields=[
                    'ai_cause', 'ai_fix', 'ai_confidence', 'ai_reviewed_at',
                ])
                self.stdout.write(f'  AI: {row.metric}/{row.period_label} '
                                  f'-> {review.get("cause","")[:80]}')
            except Exception as exc:  # noqa: BLE001
                self.stdout.write(self.style.WARNING(
                    f'  AI failed for {row.id}: {exc}'
                ))
