from datetime import date

from django.core.management.base import BaseCommand
from django.utils import timezone

from iso_compliance.models import DPIA, DPIACondition


DESCRIPTION = (
    "Staff wellness rewards app processing blood-pressure (health), "
    "physical activity, driving/movement data, and wellness-scan facial "
    "readings. P1 is mandatory because health/special-category data is processed."
)
DATA_TYPES = (
    "Blood pressure (health/special category), physical activity, "
    "driving/movement telematics, wellness-scan facial reading"
)
PILOT_CONDITIONS = [
    "Implement MFA and confirm encryption of the health data",
    "Verify the raw wellness-scan reading is discarded after scoring (data minimisation)",
    "Confirm lawful basis and close Tester Consent Form gaps "
    "(in-app health disclosure; right to complain to the Commissioner)",
]
ROLLOUT_CONDITIONS = [
    "Confirm cross-border safeguards for the Google and Apple transfers and the "
    "wellness-scan processor (DPA s.74) — share terms/contract",
    "Re-assess any additional data types or processing added ahead of customer rollout",
]


class Command(BaseCommand):
    help = "Seed the Alpha Nexus (Alpha Rewards) DPIA and its pilot/rollout conditions (idempotent, non-destructive)."

    def handle(self, *args, **options):
        project = "Alpha Nexus (Alpha Rewards)"

        # Create once. On re-run we intentionally do NOT touch the existing
        # record — that preserves any edits made in the app and never forges a
        # fresh CFO signature timestamp.
        dpia, created = DPIA.objects.get_or_create(
            project=project,
            defaults={
                "description": DESCRIPTION,
                "data_types": DATA_TYPES,
                "special_category": True,
                "risk_rating": DPIA.RATING_P1,
                "residual_risk": DPIA.RESIDUAL_MEDIUM,
                "status": DPIA.STATUS_CONDITIONS_OPEN,
                "dpo_reviewer": "Oratile Tlhomelang",
                "compliance_officer": "Kakale Bontana",
                "cfo_signed": True,
                "cfo_signed_at": timezone.now(),
                "deadline": date(2026, 8, 30),
            },
        )

        # Conditions keyed on (dpia, phase, text) so a re-run is a no-op and
        # never collides with conditions added later in the app.
        added = 0
        for order, text in enumerate(PILOT_CONDITIONS):
            _, made = DPIACondition.objects.get_or_create(
                dpia=dpia, phase=DPIACondition.PHASE_PILOT, text=text,
                defaults={"owner": "CFO", "order": order},
            )
            added += int(made)
        for order, text in enumerate(ROLLOUT_CONDITIONS):
            _, made = DPIACondition.objects.get_or_create(
                dpia=dpia, phase=DPIACondition.PHASE_ROLLOUT, text=text,
                defaults={"owner": "CFO", "order": order},
            )
            added += int(made)

        verb = "Created" if created else "Already exists (left unchanged)"
        self.stdout.write(self.style.SUCCESS(
            f"{verb}: DPIA '{project}'. Conditions added this run: {added}."
        ))
