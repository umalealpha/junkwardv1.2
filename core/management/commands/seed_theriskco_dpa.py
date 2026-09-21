"""
seed_theriskco_dpa — create (idempotently) the Data Processing Agreement with
ADRisk / The Risk Co for the Alpha Brain data extracts, and print its signing
link. CFO 2026-07-29 (DPA-audit finding H-6).

    python manage.py seed_theriskco_dpa
"""
from django.core.management.base import BaseCommand

REFERENCE = 'DPA-2026-0001'
DEFAULTS = dict(
    processor_name='ADRisk Global Solutions Private Limited (The Risk Co)',
    processor_email='pbisen@theriskco.com',
    country='India',
    adequate=False,   # India is not on Botswana's adequacy list
    purpose=("to develop, support, operate and analyse the Graphite and Alpha "
             "Brain systems on Alpha Direct's documented instructions"),
    data_categories=("policyholder name, policy number, and premium / "
                     "outstanding-balance amounts contained in Alpha Brain "
                     "operational worklists and data extracts"),
)


class Command(BaseCommand):
    help = "Create/update the ADRisk (The Risk Co) processor DPA and show its signing link."

    def handle(self, *args, **opts):
        from core.models import ProcessorDPA
        from core.processor_dpa import render_dpa_document, sign_url

        dpa, created = ProcessorDPA.objects.get_or_create(
            reference=REFERENCE, defaults=DEFAULTS)

        if not created:
            # keep the record's terms current, but NEVER silently re-freeze a
            # document that has already been signed.
            for k, v in DEFAULTS.items():
                setattr(dpa, k, v)

        if dpa.status != ProcessorDPA.Status.SIGNED:
            dpa.document_html = render_dpa_document(dpa)
            if dpa.status == ProcessorDPA.Status.DRAFT:
                dpa.status = ProcessorDPA.Status.SENT
                from django.utils import timezone
                dpa.sent_at = timezone.now()
        dpa.save()

        self.stdout.write(self.style.SUCCESS(
            f"{'Created' if created else 'Updated'} {dpa.reference} (id={dpa.id}, "
            f"status={dpa.status})"))
        self.stdout.write(f"SIGN_URL={sign_url(dpa)}")
