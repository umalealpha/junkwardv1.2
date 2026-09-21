"""python manage.py seed_dpo_tasks

Assign the DPO (Oratile) a real, standing work-plan as OmniTasks (CFO directive
2026-07-20: the DPO's role is to PROTECT + SUPPORT the business, not just regulate).
Idempotent — get_or_create by (assignee, title), so re-runs never duplicate.
"""
from django.core.management.base import BaseCommand

# (title, body, due-in-days, priority)
DPO_TASKS = [
    ("Finalise the Records of Processing (ROPA)",
     "Map every processing activity (payroll, policyholder, claims, KYC, telematics, AI) to its "
     "lawful basis, recipients, retention and cross-border transfers. Foundation for everything else.", 21, 'high'),
    ("Drive the 4 DPIAs to sign-off",
     "Review + approve the drafted DPIAs on the Data Protection dashboard: KYC, Payroll, AI, Telematics. "
     "Add mitigations and record residual risk.", 30, 'high'),
    ("Register the DPO office with the IDPC",
     "Complete and file the DPO/controller registration with the Information & Data Protection Commission.", 21, 'high'),
    ("Document processor agreements (DPAs)",
     "Get signed data-processing agreements from WebFleet, Time Doctor, Microsoft 365 and RealPay; "
     "record them in the cross-border register.", 30, 'high'),
    ("Confirm + publish the privacy notices",
     "Review the Staff and Policyholder privacy notices on the dashboard, confirm the wording with legal, "
     "and make sure customers actually receive/see the policyholder notice.", 14, 'normal'),
    ("Run a 72-hour breach drill",
     "Log a test incident in the breach register, walk the 72-hour IDPC-notification steps, and write the "
     "breach response SOP so the team can act fast for real.", 21, 'normal'),
    ("Monitor data-subject requests within SLA",
     "Watch the data-subject request register daily; make sure every access/correct/delete request is "
     "actioned within the statutory 30 days.", 7, 'high'),
    ("Quarterly access review (Graphite + omni)",
     "Confirm no ex-staff / ex-contractors keep access and least-privilege holds. Support the business by "
     "keeping the right people in and the wrong people out.", 30, 'normal'),
    ("Roll out staff data-protection training",
     "Prepare and deliver short DPA training so every team knows how to handle personal data day-to-day.", 45, 'normal'),
    ("Complete the monthly DPA checklist — every month",
     "Submit the compulsory monthly checklist (Graphite + omni) on the dashboard before month-end. This is "
     "the recurring proof the controls are working.", 7, 'high'),
]


class Command(BaseCommand):
    help = "Assign the DPO a standing data-protection work-plan (idempotent)."

    def handle(self, *args, **opts):
        from core.models import OmniTask
        from django.contrib.auth import get_user_model
        from django.utils import timezone
        from datetime import timedelta
        U = get_user_model()
        dpo = U.objects.filter(email__istartswith='otlhomelang').first()
        if not dpo:
            self.stderr.write("DPO (otlhomelang) not found — no tasks created.")
            return
        cfo = U.objects.filter(email__istartswith='pganesharajah').first()
        created = 0
        for title, body, days, pri in DPO_TASKS:
            _, made = OmniTask.objects.get_or_create(
                assignee=dpo, title=title[:200],
                defaults={'assigner': cfo or dpo, 'body': body[:5000],
                          'due_at': timezone.localdate() + timedelta(days=days),
                          'priority': getattr(OmniTask.Priority, pri.upper()),
                          'status': OmniTask.Status.PENDING})
            created += 1 if made else 0
        self.stdout.write(self.style.SUCCESS(f"DPO work-plan: {created} new task(s) of {len(DPO_TASKS)} assigned to {dpo.username}."))
