"""Nightly: fetch Alpha Brain's PII-free compliance summary, let our local AI
read it, store the result for the Compliance/AML dashboard. Idempotent; safe to
run when Alpha Brain isn't activated yet (records an 'awaiting feed' row)."""
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = 'Fetch + AI-analyse the Alpha Brain compliance summary (nightly).'

    def handle(self, *args, **opts):
        from core.compliance_brain import run_nightly
        row = run_nightly()
        self.stdout.write(self.style.SUCCESS(
            f'compliance-brain: as_of={row.as_of} fetched_ok={row.fetched_ok} '
            f'engine={row.ai_engine or "-"} note={row.note or "-"}'))
