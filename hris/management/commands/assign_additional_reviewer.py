"""Assign an operations reviewer (AdditionalReviewer) to a set of employees.

CFO 2026-08-12: Bharath runs operations and must be able to review + give feedback
on people who line-report elsewhere (Finance, HR, IT, Claims). This is the clean,
idempotent write path for that — NOT the line-manager field (which is dual-approval
and deliberately not settable here). An additional reviewer never approves leave,
is not the line manager, and stays out of the co-review blend.

Dry-run by default. Examples:
    manage.py assign_additional_reviewer --reviewer bbalasubramanian@alphadirect.co.bw \
        --emails pkago@alphadirect.co.bw,lntabeni@alphadirect.co.bw
    manage.py assign_additional_reviewer --reviewer bbalasubramanian@alphadirect.co.bw \
        --department Claims --reason "Operations oversight" --commit
"""
from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.db.models import Q

from hris.co_review_models import AdditionalReviewer
from hris.models import HRISProfile
from payroll.models import Employee


class Command(BaseCommand):
    help = 'Assign an additional (operations) reviewer to employees. Dry-run unless --commit.'

    def add_arguments(self, parser):
        parser.add_argument('--reviewer', required=True,
                            help="Reviewer's login email (the person who will review).")
        parser.add_argument('--emails', default='',
                            help='Comma-separated target employee login emails.')
        parser.add_argument('--department', default='',
                            help="Target everyone in a department (matches Employee.department "
                                 "or their HRIS job title, case-insensitive contains).")
        parser.add_argument('--reason', default='Operations oversight')
        parser.add_argument('--commit', action='store_true',
                            help='Write the assignments. Without this it only reports.')

    def _resolve_reviewer(self, email: str) -> Employee:
        emp = (Employee.objects.filter(user__email__iexact=email).select_related('user').first()
               or Employee.objects.filter(email__iexact=email).first())
        if emp is None:
            raise CommandError(f'Reviewer not found for {email!r} (no employee with that login).')
        return emp

    def _targets(self, emails: str, department: str):
        qs = Employee.objects.exclude(status=Employee.Status.TERMINATED)
        wanted = []
        if emails.strip():
            addrs = [e.strip().lower() for e in emails.split(',') if e.strip()]
            found = list(qs.filter(Q(user__email__in=addrs) | Q(email__in=addrs))
                         .select_related('user'))
            seen = {(e.user.email if e.user_id else e.email or '').lower() for e in found}
            for a in addrs:
                if a not in seen:
                    self.stderr.write(self.style.WARNING(f'  ! no active employee for {a}'))
            wanted += found
        if department.strip():
            d = department.strip()
            wanted += list(qs.filter(
                Q(department__icontains=d) | Q(job_title__icontains=d))
                .select_related('user'))
        # de-dupe by id, keep order
        out, seen_ids = [], set()
        for e in wanted:
            if e.id not in seen_ids:
                seen_ids.add(e.id); out.append(e)
        return out

    @transaction.atomic
    def handle(self, *args, **opts):
        reviewer = self._resolve_reviewer(opts['reviewer'])
        targets = self._targets(opts['emails'], opts['department'])
        if not targets:
            raise CommandError('No targets. Pass --emails and/or --department.')

        reason = opts['reason']
        commit = opts['commit']
        self.stdout.write(f'Reviewer: {reviewer.full_name} ({opts["reviewer"]})')
        self.stdout.write(f'Targets: {len(targets)}   mode: {"COMMIT" if commit else "DRY-RUN"}')
        self.stdout.write('-' * 78)

        created = existing = skipped = 0
        for emp in sorted(targets, key=lambda e: e.full_name):
            profile = getattr(emp, 'hris_profile', None) or HRISProfile.objects.filter(employee=emp).first()
            if profile is None:
                self.stdout.write(f'  SKIP  {emp.full_name:32} — no HRIS profile'); skipped += 1; continue
            if emp.id == reviewer.id:
                self.stdout.write(f'  SKIP  {emp.full_name:32} — is the reviewer'); skipped += 1; continue
            if profile.manager_id == reviewer.id or profile.co_manager_id == reviewer.id:
                self.stdout.write(f'  SKIP  {emp.full_name:32} — reviewer already line/co-manages them')
                skipped += 1; continue
            if AdditionalReviewer.objects.filter(profile=profile, reviewer=reviewer).exists():
                self.stdout.write(f'  ok    {emp.full_name:32} — already assigned'); existing += 1; continue
            if commit:
                AdditionalReviewer.objects.create(profile=profile, reviewer=reviewer, reason=reason)
            self.stdout.write(self.style.SUCCESS(
                f'  {"ADD " if commit else "WOULD"} {emp.full_name:32} — {emp.department or emp.job_title or ""}'))
            created += 1

        self.stdout.write('-' * 78)
        self.stdout.write(f'created={created}  already={existing}  skipped={skipped}')
        if not commit:
            self.stdout.write(self.style.WARNING('DRY-RUN — nothing written. Re-run with --commit.'))
            transaction.set_rollback(True)
