"""Escalate manager silence (CFO 2026-07-25).

Any Manager Accountability Note whose deadline has passed with no response is
escalated — once — to Human Resources, with the manager's name on it. HR, not
the C-suite: this is a people matter and the note says so (CFO 2026-08-03). Run shortly after the daily deadline (17:00).

  python manage.py escalate_manager_accountability
  python manage.py escalate_manager_accountability --dry-run
"""
from __future__ import annotations

from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.core.management.base import BaseCommand
from django.utils import timezone

from hris import manager_accountability as ma


class Command(BaseCommand):
    help = 'Escalate manager-accountability notes that got no response by the deadline.'

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true')

    def handle(self, *args, **opts):
        from hris.models import ManagerAccountabilityNote

        now = timezone.now()
        due = (ManagerAccountabilityNote.objects
               .filter(deadline__lt=now, responded_at__isnull=True, escalated_at__isnull=True)
               .select_related('manager'))
        if not due:
            self.stdout.write(self.style.SUCCESS('No silent notes past deadline.'))
            return

        from_email = (getattr(settings, 'OMNI_FROM_EMAIL', '')
                      or getattr(settings, 'DEFAULT_FROM_EMAIL', 'Omni ERP <omni@alphadirect.co.bw>'))
        # Only name a manager to anyone when the absence really is unexplained.
        # With a thin leave register it is not (CFO 2026-07-28) — hold it as a
        # quiet system-health note until leave is captured in Omni. That quiet
        # path is a DATA problem, not a people matter, so it stays with the CFO.
        reliable = ma.leave_register_reliable()
        recipients = ma.ESCALATION_EMAILS if reliable else ma.QUIET_ESCALATION_EMAILS
        if not reliable:
            self.stdout.write(self.style.WARNING(
                'Leave register too thin to prove "no notice" — holding it as a quiet note to the CFO only.'))
        exempt = ma.exempt_names()
        n = skipped = 0
        for note in due:
            mgr_name = (getattr(note.manager, 'full_name', '') or '').strip() or 'A manager'
            reports = [r for r in (note.reports or [])
                       if (r.get('name') or '').strip().lower() not in exempt]
            if not reports:                             # every dark report is exempt
                note.escalated_at = now
                note.save(update_fields=['escalated_at', 'updated_at'])
                skipped += 1
                continue
            deadline_str = timezone.localtime(note.deadline).strftime('%H:%M, %a %d %b')
            html = ma.build_escalation_email(
                manager_name=mgr_name, reports=reports,
                sent_on=note.for_date, deadline_str=deadline_str)
            if opts.get('dry_run'):
                self.stdout.write(f'[dry] escalate {mgr_name} ({len(reports)} dark) → {recipients}')
                n += 1
                continue
            msg = EmailMultiAlternatives(
                subject=f'Escalation — {mgr_name} did not answer on team downtime',
                body=f'{mgr_name} did not respond to the accountability note by the deadline.',
                from_email=from_email, to=recipients)
            msg.attach_alternative(html, 'text/html')
            msg.send(fail_silently=False)
            note.escalated_at = now
            note.save(update_fields=['escalated_at', 'updated_at'])
            n += 1

        self.stdout.write(self.style.SUCCESS(
            f'{"[dry] " if opts.get("dry_run") else ""}Escalated {n} silent note(s)'
            f'{f", {skipped} closed as exempt-only" if skipped else ""}.'))
