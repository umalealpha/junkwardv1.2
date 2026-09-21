"""Development Dialogue reminders (CFO directive 2026-07-18).

Nudges the people who still have to act on an OPEN (unlocked, current) review:
  * employees who have not completed their self-assessment (no employee sign-off)
  * managers who still have team reviews to complete / sign off

Safe by design: DRY-RUN unless --send is passed. Only emails people who
actually have something pending. CFO is CC'd for oversight.

    python manage.py dd_reminders            # dry run — prints who would be nudged
    python manage.py dd_reminders --send     # actually email (weekly cron)
"""
from __future__ import annotations

from collections import defaultdict

from django.core.management.base import BaseCommand

from hris.models import DevelopmentDialogue, HRISProfile
from payroll.models import Employee

BASE = 'https://omni.alphadirect.co.bw'


class Command(BaseCommand):
    help = "Nudge staff/managers with open Development Dialogues to complete/sign."

    def add_arguments(self, parser):
        parser.add_argument('--send', action='store_true', help='Actually send emails.')

    def handle(self, *args, **opts):
        send = opts['send']
        current = list(DevelopmentDialogue.objects.filter(is_current=True, locked=False))

        # 1) employees who have not self-assessed / signed
        emp_nudges = []
        for d in current:
            so = (d.payload or {}).get('signoff') or {}
            if not so.get('employee') and d.email:
                emp_nudges.append((d.name, d.email))

        # 2) managers with team reviews lacking a manager sign-off
        # map dialogue-email -> employee -> manager email
        by_manager = defaultdict(list)
        email_to_emp = {(e.email or '').lower(): e for e in Employee.objects.all() if e.email}
        for d in current:
            so = (d.payload or {}).get('signoff') or {}
            if so.get('manager'):
                continue
            emp = email_to_emp.get((d.email or '').lower())
            prof = getattr(emp, 'hris_profile', None) if emp else None
            mgr = prof.manager if (prof and prof.manager) else None
            if mgr and mgr.email:
                by_manager[mgr.email].append(d.name)

        self.stdout.write(f"Employees to nudge (self-assessment): {len(emp_nudges)}")
        for nm, em in emp_nudges:
            self.stdout.write(f"  emp: {nm} <{em}>")
        self.stdout.write(f"Managers to nudge (sign-off pending): {len(by_manager)}")
        for em, names in by_manager.items():
            self.stdout.write(f"  mgr: {em} -> {len(names)} review(s): {', '.join(names)}")

        if not send:
            self.stdout.write(self.style.WARNING("DRY RUN — no emails sent. Pass --send to email."))
            return

        from core.notifications import send_with_cfo_cc
        sent = 0
        for nm, em in emp_nudges:
            body = (f"{nm.split(' ')[0]},\n\n"
                    f"Your Development Dialogue for the current period is open. "
                    f"Please complete your self-assessment in omni:\n{BASE}/hris/my-dialogue\n\n"
                    f"Regards,\nHR — Alpha Direct")
            sent += send_with_cfo_cc(subject="Action: complete your Development Dialogue",
                                     body=body, to=[em]) or 0
        for em, names in by_manager.items():
            body = (f"You have {len(names)} team Development Dialogue(s) still to complete/sign "
                    f"for the current period:\n- " + "\n- ".join(names) +
                    f"\n\nReview and sign them off in omni:\n{BASE}/hris/talent-cockpit\n\n"
                    f"Regards,\nHR — Alpha Direct")
            sent += send_with_cfo_cc(subject="Action: Development Dialogues awaiting your sign-off",
                                     body=body, to=[em]) or 0
        self.stdout.write(self.style.SUCCESS(f"Sent {sent} reminder email(s)."))
