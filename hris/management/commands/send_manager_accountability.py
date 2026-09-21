"""Send the Manager Accountability Note (CFO 2026-07-25).

For each line manager who has reports dark 3+ consecutive days with no leave /
client visit logged, send ONE firm interactive note and record it. Silence past
the deadline is swept by `escalate_manager_accountability`.

  python manage.py send_manager_accountability                 # yesterday
  python manage.py send_manager_accountability --date 2026-07-24
  python manage.py send_manager_accountability --dry-run       # compute, send nothing
  python manage.py send_manager_accountability --preview-file /tmp/m.html
"""
from __future__ import annotations

import datetime

from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.core.management.base import BaseCommand
from django.utils import timezone

from hris import manager_accountability as ma


class Command(BaseCommand):
    help = 'Email each manager whose team has an unexplained 3+ day Time Doctor gap.'

    def add_arguments(self, parser):
        parser.add_argument('--date', dest='date')
        parser.add_argument('--dry-run', action='store_true')
        parser.add_argument('--preview-file', dest='preview_file')

    def handle(self, *args, **opts):
        # Consolidated 'morning to-dos' (CONSOLIDATED_EMAILS_ENABLED, CFO
        # 2026-08-05): when ON, the 06:30 taskboard sweep folds each manager's
        # accountability note into their single morning email
        # (taskboard.services.email_open_reminders via
        # hris.manager_accountability.managers_on_the_hook), so this standalone
        # sender self-skips its own send — otherwise the manager gets two emails.
        # Flag OFF ⇒ byte-for-byte unchanged. --dry-run and --preview-file still
        # run so the note stays testable by hand even with the flag on. Only the
        # real SEND self-skips. The escalate_manager_accountability command is
        # untouched and still escalates silence off the notes the sweep persists.
        if (getattr(settings, 'CONSOLIDATED_EMAILS_ENABLED', False)
                and not opts.get('dry_run') and not opts.get('preview_file')):
            self.stdout.write(self.style.WARNING(
                'CONSOLIDATED_EMAILS_ENABLED on — the 06:30 task sweep now delivers '
                'manager accountability in the one morning email; skipping own send.'))
            return

        from integrations.timedoctor import TimeDoctorClient, TimeDoctorError
        from hris.models import ManagerAccountabilityNote

        client = TimeDoctorClient.from_settings()
        if not client.configured:
            self.stdout.write(self.style.WARNING('SKIPPED: TIMEDOCTOR_TOKEN not set.'))
            return

        if opts.get('date'):
            label_day = datetime.date.fromisoformat(opts['date'])
        else:
            label_day = timezone.localtime().date() - datetime.timedelta(days=1)

        try:
            by_mgr = ma.dark_reports_by_manager(client, label_day)
        except TimeDoctorError as exc:
            self.stderr.write(self.style.ERROR(f'Time Doctor pull failed: {exc}'))
            raise SystemExit(1)

        if not by_mgr:
            self.stdout.write(self.style.SUCCESS(f'No unexplained 3+ day gaps for {label_day} — no notes.'))
            return

        # Deadline: 17:00 local the next day.
        base_dt = timezone.make_aware(datetime.datetime.combine(
            label_day + datetime.timedelta(days=1), datetime.time(17, 0)))
        deadline_str = timezone.localtime(base_dt).strftime('%H:%M, %a %d %b')
        from_email = (getattr(settings, 'OMNI_FROM_EMAIL', '')
                      or getattr(settings, 'DEFAULT_FROM_EMAIL', 'Omni ERP <omni@alphadirect.co.bw>'))
        base_url = getattr(settings, 'OMNI_BASE_URL', 'https://omni.alphadirect.co.bw')
        # The proxy only forwards /hris/api/* to Django — a bare /hris/... link is
        # served by the frontend and 404s (bug d3caf386).
        answer_path = '/hris/api/manager-accountability/answer/'

        sent = 0
        for mgr, reports in by_mgr.items():
            mgr_name = (getattr(mgr, 'full_name', '') or '').strip()
            mgr_email = (getattr(mgr, 'email', '') or '').strip()
            reports = sorted(reports, key=lambda r: r['days_dark'], reverse=True)

            if opts.get('preview_file'):
                html = ma.build_manager_email(
                    manager_name=mgr_name, reports=reports,
                    answer_url=f'{base_url}{answer_path}?t=PREVIEW',
                    deadline_str=deadline_str)
                with open(opts['preview_file'], 'w', encoding='utf-8') as fh:
                    fh.write(html)
                self.stdout.write(f'Wrote preview for {mgr_name} ({len(reports)} reports) → {opts["preview_file"]}')
                return

            note, _ = ManagerAccountabilityNote.objects.update_or_create(
                manager=mgr, for_date=label_day,
                defaults={'reports': reports, 'deadline': base_dt,
                          'response': '', 'responded_at': None, 'escalated_at': None})

            if opts.get('dry_run'):
                self.stdout.write(f'[dry] {mgr_name} <{mgr_email}> — {len(reports)} dark: '
                                  f'{", ".join(r["name"] for r in reports)}')
                sent += 1
                continue

            token = ma.make_token(note.id)
            html = ma.build_manager_email(
                manager_name=mgr_name, reports=reports,
                answer_url=f'{base_url}{answer_path}?t={token}',
                deadline_str=deadline_str)
            msg = EmailMultiAlternatives(
                subject="Your team's tracking — action needed",
                body='Open in an HTML-capable client. Your team has an unexplained Time Doctor gap.',
                from_email=from_email, to=[mgr_email],
                # HR, not the CFO (CFO 2026-08-03) — the note tells the manager
                # this is a Human Resources matter, so Human Resources is who
                # is copied on it.
                cc=list(ma.ESCALATION_EMAILS))
            msg.attach_alternative(html, 'text/html')
            msg.send(fail_silently=False)
            sent += 1

        self.stdout.write(self.style.SUCCESS(
            f'{"[dry] " if opts.get("dry_run") else ""}Manager accountability: {sent} manager(s) for {label_day}.'))
