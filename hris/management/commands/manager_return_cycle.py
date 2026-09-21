"""
Monthly Manager Return cycle (CFO 2026-07-26).

Run on the 1st of each month, for the month that just ended. Three jobs:
  1. Create the draft return for every people-manager (this is also what decides
     their role-specific question set, so it happens once, up front).
  2. Put ONE task on each manager's omni dashboard, due the 5th.
  3. On the 6th onwards, chase the ones who have not filed; from the 11th, chase
     the reviewers who have not cleared.

Idempotent — a manager already tasked for the period is skipped, so it is safe to
re-run. Dry-run by default; pass --commit to write.
"""
from __future__ import annotations

import datetime as dt

from django.contrib.auth.models import User
from django.core.management.base import BaseCommand
from django.utils import timezone

BASE = 'https://omni.alphadirect.co.bw'


def _system_assigner():
    for email in ('ubutale@alphadirect.co.bw', 'pganesharajah@alphadirect.co.bw'):
        u = User.objects.filter(email__iexact=email).first()
        if u:
            return u
    return User.objects.filter(is_superuser=True).order_by('id').first()


def _user_for(emp):
    u = getattr(emp, 'user', None)
    if u:
        return u
    return (User.objects.filter(email__iexact=emp.email).first()
            if getattr(emp, 'email', '') else None)


class Command(BaseCommand):
    help = "Create + chase the monthly manager accountability returns."

    def add_arguments(self, parser):
        parser.add_argument('--commit', action='store_true', help='Write (else dry-run).')
        parser.add_argument('--year', type=int)
        parser.add_argument('--month', type=int)
        parser.add_argument('--no-ai', action='store_true',
                            help='Skip Aria question generation (curated sets only).')

    def handle(self, *args, **opts):
        from django.conf import settings
        if not getattr(settings, 'ELRA_PERF_ENABLED', False):
            self.stdout.write('Performance module is off — nothing to do.')
            return

        from core.models import OmniTask
        from hris.models import HRISProfile
        from hris.manager_return_models import ManagerMonthlyReturn, ReturnStatus
        from hris.manager_return_service import (
            get_or_create_draft, prev_period, team_profiles,
        )
        from payroll.models import Employee

        commit = opts['commit']
        today = timezone.localdate()
        if opts.get('year') and opts.get('month'):
            year, month = int(opts['year']), int(opts['month'])
        else:
            year, month = prev_period(today)
        tag = f'manager_return:{year}-{month:02d}'
        period_label = dt.date(year, month, 1).strftime('%B %Y')
        self.stdout.write(f'Monthly manager return for {period_label} (commit={commit})')

        assigner = _system_assigner()
        mgr_ids = (HRISProfile.objects.exclude(manager=None)
                   .values_list('manager', flat=True).distinct())
        managers = (Employee.objects.filter(pk__in=list(mgr_ids))
                    .exclude(status=Employee.Status.TERMINATED)
                    .order_by('full_name'))

        created = tasked = skipped = no_login = 0
        for mgr in managers:
            n_reports = team_profiles(mgr).count()
            if not n_reports:
                continue
            mgr_user = _user_for(mgr)
            if mgr_user is None:
                no_login += 1
                self.stdout.write(f'  ! {mgr.full_name}: no login, cannot task')
                continue

            ret = None
            if commit:
                ret = get_or_create_draft(mgr, year, month)
                created += 1
            if OmniTask.objects.filter(assignee=mgr_user, source=tag).exists():
                skipped += 1
                continue

            due = ret.submit_due if ret else dt.date(
                year + (1 if month == 12 else 0), 1 if month == 12 else month + 1, 5)
            spec = (ret.question_spec or {}) if ret else {}
            asks = []
            if spec.get('asks_sales'):
                asks.append('sales')
            if spec.get('asks_sla'):
                asks.append('service levels')
            if spec.get('department_matched'):
                asks.append(spec['department_matched'])
            extra = f" It also asks about {', '.join(asks)}." if asks else ''

            title = f'Monthly manager return — {period_label} ({n_reports} in your team)'
            body = (
                f'Your monthly return for {period_label} is ready.\n\n'
                f'Your team\'s hours, leave and task record are already filled in — '
                f'you do not type any of that. You answer a short set of questions '
                f'about how you managed the team.{extra}\n\n'
                f'Open it here: {BASE}/hris/monthly-return\n\n'
                f'Due to your manager by the {due.strftime("%d")}th. '
                f'They are expected to clear it by the 10th.\n\n'
                f'It is short — about five minutes if you know your team.'
            )
            if commit:
                OmniTask.objects.create(
                    assigner=assigner or mgr_user, assignee=mgr_user,
                    title=title[:200], body=body,
                    priority=OmniTask.Priority.HIGH,
                    status=OmniTask.Status.PENDING,
                    due_at=due, source=tag)
            tasked += 1
            self.stdout.write(f'  -> {mgr.full_name}: {n_reports} report(s), due {due}')

        self.stdout.write(self.style.SUCCESS(
            f'drafts={created} tasked={tasked} already_tasked={skipped} no_login={no_login}'
            + ('' if commit else '   [DRY RUN — pass --commit to write]')))

        # ── chasing ─────────────────────────────────────────────────────────
        if today.day > 5:
            late = ManagerMonthlyReturn.objects.filter(
                period_year=year, period_month=month,
                status__in=[ReturnStatus.DRAFT, ReturnStatus.RETURNED])
            self.stdout.write(f'  late filers ({today.day} > 5): {late.count()}')
            for r in late.select_related('manager'):
                self.stdout.write(f'    ! {r.manager.full_name} has not filed')
        if today.day > 10:
            uncleared = ManagerMonthlyReturn.objects.filter(
                period_year=year, period_month=month, status=ReturnStatus.SUBMITTED)
            self.stdout.write(f'  uncleared past the 10th: {uncleared.count()}')
            for r in uncleared.select_related('manager', 'submitted_to'):
                who = r.submitted_to.full_name if r.submitted_to else 'no manager on record'
                self.stdout.write(f'    ! {r.manager.full_name} waiting on {who}')
