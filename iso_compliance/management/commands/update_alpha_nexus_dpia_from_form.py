from datetime import date

from django.core.management.base import BaseCommand
from django.db import transaction

from iso_compliance.models import DPIA, DPIARisk


class Command(BaseCommand):
    help = "Update the Alpha Nexus DPIA record with data extracted from the Alpha DPIA Form Nexus."

    @transaction.atomic
    def handle(self, *args, **options):
        dpia = DPIA.objects.filter(project__startswith="Alpha Nexus").first()

        if dpia is None:
            self.stdout.write("No DPIA found with project starting 'Alpha Nexus'.")
            return

        # Header
        dpia.dpia_ref = "DPIA-2026-004"
        dpia.date_opened = date(2026, 8, 4)
        dpia.department = "Projects (Project Management Department)"
        dpia.process_owner = "Projects Department – Project Nexus (owner name to be confirmed)"
        dpia.system_used = (
            "Alpha Nexus mobile app (Android & iOS): Click & Drive telematics, "
            "fingertip Wellness scan, Earn & Climb rewards engine; supporting rewards "
            "back-end. Underlying telematics/wellness-scan providers to be confirmed with IT."
        )
        dpia.new_change_existing = "new"
        dpia.planned_go_live = "Phased customer rollout – date to be confirmed (staff version already live)"
        dpia.vendor_involved = True
        dpia.cross_border = True

        # Section 1
        dpia.purpose = (
            "Improve customer retention / reduce churn (target 5%), encourage timely "
            "premium payments, increase multi-product bundling (target +15%), preserve GWP, "
            "differentiate Alpha Direct via a locally relevant loyalty offering."
        )

        # Section 2
        dpia.data_subjects = (
            "Alpha Direct policyholders/customers who opt in (currently staff during pilot), "
            "incl. insured drivers whose telematics are scored."
        )
        dpia.data_categories = {
            "identity": True,
            "contact": True,
            "financial": True,
            "policy_claims": True,
            "location_online": True,
            "criminal": False,
        }
        dpia.data_other = "driving/telematics behaviour and app usage/analytics."
        dpia.special_category_detail = (
            "YES — wellness-scan biometrics (heart rate, stress, breathing) + self-logged "
            "fitness/health (steps, workouts, meals)."
        )
        dpia.vulnerable = True
        dpia.vulnerable_note = (
            "Children: No. Staff-pilot participants carry a power imbalance, so the "
            "vulnerable-data-subjects trigger applies (not applicable at customer rollout)."
        )
        dpia.volume = "<1k staff pilot; projected 1k–10k at customer rollout."

        # Section 3
        dpia.triggers = {
            "special_category": True,
            "large_scale": False,
            "profiling": True,
            "monitoring": True,
            "new_tech": True,
            "new_vendor": True,
            "cross_border": True,
            "vulnerable": True,
        }

        # Section 4
        dpia.lawful_basis = "consent"
        dpia.lawful_basis_note = (
            "Consent (opt-in) for pilot; Contract not applied at pilot — revisit with Legal "
            "(Ikanyeng Sechele and Arjun Iyer) at customer rollout."
        )
        dpia.special_category_basis = (
            "Explicit written consent, obtained separately/specifically for wellness-scan health "
            "data, with right to withdraw. Health data minimised, stored separately, processed "
            "only when feature in scope."
        )
        dpia.how_informed = (
            "Programme terms + privacy notice (Setswana & English); WhatsApp/SMS opt-in message; "
            "in-app permission screen for health data; agent scripts + FAQs."
        )
        dpia.how_rights = (
            "Via Alpha Direct data-subject request channel / DPO — access, correct, object, "
            "withdraw consent, contest automated tier decisions via human review. Published "
            "channel + timeline TBC with DPO."
        )

        # Section 5
        dpia.internal_sharing = (
            "available to projects, finance, sales, IT on formal written request with valid "
            "reasons (for health data)."
        )
        dpia.external_vendors = (
            "Google LLC (Google Play) & Apple Inc. (App Store) receive user account emails for "
            "app distribution — processed OUTSIDE Botswana, relies on explicit consent (DPA s.74 "
            "derogation). Reward partners (airtime, retail, Spin & Win game provider) receive "
            "ANONYMISED data only."
        )
        dpia.cross_border_detail = (
            "YES — Google/Apple process in the US for distribution; s.74 consent derogation. "
            "Confirm whether app hosting / wellness-scan processing also occurs outside Botswana "
            "and apply DPA safeguards."
        )

        # Section 6
        dpia.retention_period = (
            "Per Alpha Direct Data Retention & Erasure Policy. Financial/policy/claims/FIA-filed: "
            "20 years (Financial Intelligence Regs 2022 reg.18(3)(a); AML/CFT). "
            "Health/wellness (special-category): MINIMISED — raw scan reading NOT retained after "
            "scoring; only awarded points + date kept."
        )
        dpia.retention_reason = (
            "programme operation (points accrual, redemption, disputes); regulatory "
            "record-keeping; fraud prevention."
        )
        dpia.disposal_method = (
            "secure deletion / cryptographic erasure; anonymisation where retained for analytics; "
            "documented disposal log. TBC with IT."
        )

        # Section 7
        dpia.security_controls = {
            "access_control": True,
            "mfa": False,
            "encryption_transit": False,
            "encryption_rest": False,
            "logging": True,
            "backups": True,
            "staff_training": False,
            "vendor_assurance": True,
            "data_minimisation": True,
            "pseudonymisation": False,
        }
        dpia.security_note = (
            "Information Security Policy provides for encryption (transit + rest), staff "
            "training, pseudonymisation; MFA not currently provided — to confirm with IT/Security."
        )

        # Section 9
        dpia.decision = "proceed_conditions"
        dpia.dpo_review_note = "Oratile Tlhomelang / 04-08-26 / Proceed with conditions."
        dpia.statement_of_alignment = (
            "follows privacy-by-design principles (lawfulness, fairness, transparency, data "
            "minimisation, security, accountability)."
        )

        dpia.save()

        # Section 8: replace risks
        dpia.risks.all().delete()

        risk_specs = [
            {
                "order": 0,
                "title": "Exposure of health/wellness data",
                "what_could_go_wrong": (
                    "wellness-scan biometrics accessed by unauthorised staff or leaked via "
                    "app/vendor."
                ),
                "controls": (
                    "explicit consent; access on written request; data minimisation; vendor "
                    "NDA/DPA."
                ),
                "likelihood": 2,
                "impact": 5,
                "mitigation": (
                    "store health data separately; encrypt in transit + at rest; restrict "
                    "access; process only while feature in scope; no sharing with reward partners."
                ),
                "residual_score": 5,
                "residual_band": "Medium",
            },
            {
                "order": 1,
                "title": "Location/driving profiling",
                "what_could_go_wrong": (
                    "continuous driving/location reveals movement patterns, over-retained, or "
                    "wrong driver scored."
                ),
                "controls": (
                    "one-driver-per-vehicle mapping; purpose limitation; access control."
                ),
                "likelihood": 3,
                "impact": 3,
                "mitigation": (
                    "minimise/aggregate trip data; retain only for active rewards period; clear "
                    "opt-in; human review of automated scores."
                ),
                "residual_score": 4,
                "residual_band": "Low–Med",
            },
            {
                "order": 2,
                "title": "Over-sharing with reward partners",
                "what_could_go_wrong": (
                    "more personal data than needed shared with airtime/retail partners or game "
                    "provider."
                ),
                "controls": "share only voucher/redemption codes; contractual controls.",
                "likelihood": 2,
                "impact": 3,
                "mitigation": (
                    "data-minimised integration (codes only); vendor assurance/DPAs; no health "
                    "data shared."
                ),
                "residual_score": 3,
                "residual_band": "Low",
            },
        ]

        for risk_data in risk_specs:
            DPIARisk.objects.create(
                dpia=dpia,
                order=risk_data["order"],
                title=risk_data["title"],
                what_could_go_wrong=risk_data["what_could_go_wrong"],
                controls=risk_data["controls"],
                likelihood=risk_data["likelihood"],
                impact=risk_data["impact"],
                mitigation=risk_data["mitigation"],
                residual_score=risk_data["residual_score"],
                residual_band=risk_data["residual_band"],
            )

        self.stdout.write(
            f"SUCCESS: Updated DPIA {dpia.dpia_ref} with {len(risk_specs)} risks."
        )
