"""python manage.py dpo_checklist_sweep

Create the current month's DPO checklist run + mark any unsubmitted past-due run
OVERDUE. Drives the Data Protection dashboard's overdue action card (CFO / C-suite
can then convert it to a disciplinary action). Cron: daily. CFO directive 2026-07-19.
"""
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Create the current DPO checklist run + flag overdue ones."

    def handle(self, *args, **opts):
        from core.dpa_checklist import sweep
        r = sweep()
        self.stdout.write(self.style.SUCCESS(
            f"DPO checklist: current={r['current']} newly_overdue={r['newly_overdue']}"))
