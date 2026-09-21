"""
python manage.py sweep_task_reminders [--dry-run]

Daily reminder sweep for the taskboard (run by cron on the EC2, e.g. 06:30
Africa/Gaborone — same mechanism as helpdesk_pending_reminder / email_morning_report;
this repo has no Celery worker).

Creates due-today / overdue Notification rows for open OmniTasks so the omni
front-end can raise the force-action modal. Idempotent — a standing unacknowledged
reminder is never duplicated — so running it several times a day is safe.

The gentle assign-day toast is created inline when a task is assigned
(services.notify_on_assign), not here.
"""
from __future__ import annotations

from django.core.management.base import BaseCommand
from django.db import transaction

from taskboard import services


class Command(BaseCommand):
    help = "Create due-today / overdue task reminders (run daily via cron)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Report what would be created without writing anything.",
        )
        parser.add_argument(
            "--no-email",
            action="store_true",
            help="Only create the in-app reminders; skip the email nudge.",
        )
        parser.add_argument(
            "--force",
            action="store_true",
            help="Run even on a public holiday (bypass the holiday pause).",
        )

    def handle(self, *args, **options):
        # Public-holiday quiet time (CFO 2026-07-19): on a Botswana non-working
        # public holiday, stay silent — no new task reminders, no approval
        # nudges. The next working day's run creates them, so nothing is lost —
        # people just get one catch-up instead of holiday spam.
        from hris.workforce_brief import is_holiday_off
        if is_holiday_off() and not options.get("force"):
            self.stdout.write(self.style.WARNING(
                "Public holiday (Botswana) — task reminders & approval nudges "
                "paused; they resume and catch up on the next working day."))
            return
        if options["dry_run"]:
            with transaction.atomic():
                created = services.sweep_due_and_overdue()
                transaction.set_rollback(True)
            self.stdout.write(
                self.style.WARNING(
                    f"[dry-run] would create due_day={created['due_day']} "
                    f"overdue={created['overdue']} (rolled back; no email)"
                )
            )
            return
        created = services.sweep_due_and_overdue()
        self.stdout.write(
            self.style.SUCCESS(
                f"taskboard reminders created: due_day={created['due_day']} "
                f"overdue={created['overdue']}"
            )
        )
        if not options["no_email"]:
            # On the first working day after a public-holiday break the per-item
            # task nudge is REPLACED by the single 'welcome back' digest
            # (send_welcome_back_digest) so nobody is double-mailed (CFO 2026-07-19).
            # In-app reminders above still run; approval chases below still run.
            from hris.workforce_brief import is_first_working_day_after_holiday
            if is_first_working_day_after_holiday():
                self.stdout.write(self.style.WARNING(
                    "First working day back — task-reminder emails handled by the "
                    "welcome-back digest today; skipping the per-item nudge."))
            else:
                sent = services.email_open_reminders()
                self.stdout.write(
                    self.style.SUCCESS(
                        f"reminder emails: people={sent['people']} emailed={sent['emailed']}"
                    )
                )
            # Approval nudges are now folded into the ONE Daily Brief (the
            # brief renders each person's pending approvals) instead of a
            # separate "approvals waiting on you" email — the CFO was getting 3
            # overlapping emails a morning (CFO 2026-07-22). Suppressed here.
            # (Re-enable by restoring email_pending_approvals() if the brief is
            # ever disabled.)
