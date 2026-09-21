"""
generate_task_incentive — the monthly task-performance incentive (CFO 2026-08-26).

Priority-WEIGHTED (v2, 2026-08-26): each MANAGER-CONFIRMED task earns its priority
weight in points (low 1 / normal 2 / high 3 / urgent 5), reward =
max(0, points − 60) × BWP 25, capped BWP 2,000. A normal task = 2 points = BWP 50
and 60 points = ~30 normal tasks, so the CFO's original "P50 above 30" holds for
ordinary work while hard work pays more. "Manager-confirmed" = done, completed in
the month, and a manager (a non-assignee) left feedback on it — so self-marked
trivia cannot farm it (the CFO's anti-gaming rule).

It raises ONE IncentiveRequest for the month through the EXISTING incentive rails
(incentive_service._persist_request → CFO slot + HR slot → Finance loads payroll),
so the record shape is identical to every other incentive. Omni NEVER moves
money: it only raises the request; payment leaves via the bank with the CFO's 2FA.

Also honours the CFO's 2026-08-18 overdue-discipline gate: a person with overdue
tasks is HELD from this month's incentive (fail-open — an error never wrongly
holds anyone). Dry-run by default; --commit creates. Idempotent per month.
    0 6 1 * *  …/manage.py generate_task_incentive --commit
"""
from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

from django.contrib.auth.models import User
from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from core.models import OmniTask
from taskboard.cfo_views import (REWARD_MONTHLY_CAP, REWARD_PER_POINT,
                                 REWARD_POINT_MIN, confirmed_counts, task_reward)


# The auto-generated monthly request needs a real maker, not None (Manus QC
# 2026-08-27): a None maker leaves the request with no accountable origin. This
# is a named, disabled-login service account — it can never sign in (unusable
# password) and is hidden from task/league views via
# core.api_views._TASK_SYSTEM_USERNAMES.
SERVICE_USERNAME = 'task-incentive-bot'


def _service_account() -> User:
    user, created = User.objects.get_or_create(
        username=SERVICE_USERNAME,
        defaults={'first_name': 'Task Incentive', 'last_name': 'Automation',
                  'email': '', 'is_active': True, 'is_staff': False,
                  'is_superuser': False},
    )
    if created:
        user.set_unusable_password()
        user.save(update_fields=['password'])
    return user


def _month_bounds(ym: str) -> tuple[date, date]:
    y, m = (int(x) for x in ym.split('-'))
    start = date(y, m, 1)
    end = date(y + 1, 1, 1) if m == 12 else date(y, m + 1, 1)
    return start, end


def _has_overdue(user) -> bool:
    """The CFO's overdue-discipline gate (2026-08-18). Fail-open: any error =>
    NOT held, so a glitch never wrongly denies someone their incentive."""
    try:
        from hris.overdue_gate import overdue_summary
        return (overdue_summary(user) or {}).get('count', 0) > 0
    except Exception:                       # noqa: BLE001
        return False


class Command(BaseCommand):
    help = ('Monthly task incentive: BWP 25 per priority-weighted point above 60 '
            '(a normal task = 2 pts = P50). Raises ONE IncentiveRequest for CFO + HR '
            'approval. Dry-run unless --commit.')

    def add_arguments(self, parser):
        parser.add_argument('--month', help='YYYY-MM (default: current month).')
        parser.add_argument('--previous-month', action='store_true',
                            help='Use the month that just ended (for a 1st-of-month cron).')
        parser.add_argument('--commit', action='store_true',
                            help='Create the IncentiveRequest. Default = dry-run.')

    def handle(self, *args, **opts):
        if opts.get('month'):
            ym = opts['month']
        elif opts.get('previous_month'):
            first = timezone.localdate().replace(day=1)
            prev = first - timedelta(days=1)            # last day of the previous month
            ym = prev.strftime('%Y-%m')
        else:
            ym = timezone.localdate().strftime('%Y-%m')
        start, end = _month_bounds(ym)

        eligible, held = [], []
        rows = sorted(confirmed_counts(OmniTask.objects.all(), start, end),
                      key=lambda r: -r['points'])
        for r in rows:
            reward = task_reward(r['points'])
            if reward <= 0:
                continue
            name = (f"{r['assignee__first_name']} {r['assignee__last_name']}".strip()
                    or r['assignee__username'])
            user = User.objects.filter(pk=r['assignee_id']).first()
            if user and _has_overdue(user):
                held.append(name)
                continue
            eligible.append({'name': name, 'confirmed': r['n'],
                             'points': r['points'], 'reward': reward})

        total = sum(e['reward'] for e in eligible)
        self.stdout.write(f'Task incentive {ym}: {len(eligible)} eligible '
                          f'(>{REWARD_POINT_MIN} weighted points, no overdue) · total BWP {total}')
        for e in eligible:
            above = e['points'] - REWARD_POINT_MIN
            cap = ' (capped)' if e['reward'] == REWARD_MONTHLY_CAP else ''
            self.stdout.write(f"  {e['name']}: {e['confirmed']} confirmed = "
                              f"{e['points']} pts → {above} × {REWARD_PER_POINT} = BWP {e['reward']}{cap}")
        if held:
            self.stdout.write(f'HELD (overdue tasks, CFO gate): {", ".join(held)}')

        if not opts['commit']:
            self.stdout.write('[dry-run] no request created. Re-run with --commit.')
            return
        if not eligible:
            self.stdout.write('Nobody qualified — no request created.')
            return

        from hris.incentive_models import IncentiveRequest
        from hris.incentive_service import _persist_request
        title = f'Task incentive — {ym}'
        if IncentiveRequest.objects.filter(title=title, period=ym).exists():
            self.stdout.write(f'Request "{title}" already exists — skipped (idempotent).')
            return

        def _basis(e):
            above = e['points'] - REWARD_POINT_MIN
            b = (f"{e['confirmed']} manager-confirmed tasks = {e['points']} priority "
                 f"points ({above} above the {REWARD_POINT_MIN}-point minimum × BWP {REWARD_PER_POINT})")
            # Never let the equation contradict the capped amount (H74).
            return b + (f" — capped at BWP {REWARD_MONTHLY_CAP}"
                        if e['reward'] == REWARD_MONTHLY_CAP else '')

        parsed = [{
            'employee_id': None,          # matched by name in payroll (3 same-name people exist — no risky auto-FK)
            'name': e['name'],
            'basis': _basis(e),
            'amount': Decimal(e['reward']),
            'beyond_normal_duties': True, 'on_time': False, 'error_free': False,
            'needed_manager_fix': False, 'justification': '',
        } for e in eligible]

        run_stamp = timezone.now().strftime('%Y-%m-%dT%H:%M:%SZ')
        with transaction.atomic():
            req = _persist_request(
                maker=_service_account(), title=title, period=ym, department='',
                notes=(f'Auto-generated from the task dashboard: BWP {REWARD_PER_POINT} per '
                       f'priority-weighted point above {REWARD_POINT_MIN} for the month '
                       f'(a normal task = 2 points = BWP {REWARD_PER_POINT * 2}). '
                       f'Needs CFO + HR approval; payment via the bank. '
                       f'[auto-run {run_stamp} · {SERVICE_USERNAME}]'),
                parsed=parsed, notify=True, manager_attested=True)
        self.stdout.write(self.style.SUCCESS(
            f'Created {title} (#{req.pk}): {len(eligible)} line(s), BWP {total} '
            f'— pending CFO + HR approval.'))
