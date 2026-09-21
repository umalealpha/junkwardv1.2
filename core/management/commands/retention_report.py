"""python manage.py retention_report — show how many records are past their
keep-date, per data class (DPA H-4). Report only; deletes nothing. CFO 2026-07-19."""
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Report records past their retention period (read-only)."

    def handle(self, *args, **opts):
        from core.retention import retention_report
        r = retention_report()
        for c in r['classes']:
            if 'error' in c:
                self.stdout.write(f"  {c['model']}: skipped ({c['error']})")
            else:
                self.stdout.write(f"  {c['model']} (keep {c['years']}y): {c['past_count']} past retention")
        self.stdout.write(self.style.SUCCESS(f"TOTAL past retention: {r['total_past_retention']} (report only)"))
