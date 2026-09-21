"""seed_elra_leave — upsert the ELRA-2025 statutory leave types (Botswana).

Populates hris.LeaveType with the Employment & Labour Relations Act No. 27 of
2025 values (HRIS blueprint, ref ADI/HC/HRIS/2026) so the leave engine reads
them from the DB (get_leave_rules overlay) — a statutory change becomes an
admin edit, not a code deploy. Idempotent: update_or_create by code, safe to
re-run. Company entitlement (default_annual_days) may exceed the statutory
floor (statutory_min_days); the floor is recorded so it is never under-provided.

    manage.py seed_elra_leave            # upsert BW ELRA types
    manage.py seed_elra_leave --dry-run  # show what would change, write nothing
"""
from django.core.management.base import BaseCommand
from django.db import transaction

from hris.models import LeaveType

# code -> field values. accrual_method: 'monthly' accrues 1/12 per month
# (annual s.219); 'frontload' = full entitlement immediately (sick s.220 etc.).
ELRA_BW_LEAVE = {
    "annual": dict(
        name="Annual Leave", default_annual_days=21, paid_pct=100,
        accrual_method="monthly", statutory_min_days=15, min_mandatory_take_days=8,
        carry_over_cap_days=42, max_carry_over_years=3, requires_medical_cert=False,
        statutory_ref="ELRA s.219", country_code="BW",
    ),
    "sick": dict(
        name="Sick Leave", default_annual_days=20, paid_pct=100,
        accrual_method="frontload", statutory_min_days=20, requires_medical_cert=True,
        proof_type="medical_certificate", statutory_ref="ELRA s.220", country_code="BW",
    ),
    "hospitalisation": dict(
        name="Hospitalisation Leave", default_annual_days=20, paid_pct=100,
        accrual_method="frontload", statutory_min_days=20, requires_medical_cert=True,
        proof_type="medical_certificate", statutory_ref="ELRA s.220", country_code="BW",
    ),
    "maternity": dict(
        name="Maternity Leave", default_annual_days=98, paid_pct=70,
        accrual_method="frontload", requires_medical_cert=True, blocks_termination=True,
        statutory_ref="ELRA s.222", country_code="BW",
    ),
    "paternity": dict(
        name="Paternity Leave", default_annual_days=5, paid_pct=100,
        accrual_method="frontload", leave_window_weeks=14, proof_type="birth_certificate",
        statutory_ref="ELRA s.227", country_code="BW",
    ),
    "compassionate": dict(
        name="Compassionate / Family Responsibility Leave", default_annual_days=5, paid_pct=100,
        accrual_method="frontload", statutory_min_days=3,
        statutory_ref="ELRA s.221", country_code="BW",
    ),
    "study": dict(
        name="Study Leave", default_annual_days=10, paid_pct=100,
        accrual_method="frontload", statutory_ref="CoS §7.12.3", country_code="BW",
    ),
    "special": dict(
        name="Special Leave", default_annual_days=10, paid_pct=100,
        accrual_method="frontload", statutory_ref="CoS §7.11", country_code="BW",
    ),
}


class Command(BaseCommand):
    help = "Upsert the ELRA-2025 statutory leave types (Botswana) into hris.LeaveType."

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true",
                            help="Show what would change without writing.")

    def handle(self, *args, **opts):
        dry = opts["dry_run"]
        created = updated = 0
        for code, fields in ELRA_BW_LEAVE.items():
            existing = LeaveType.objects.filter(code=code).first()
            if dry:
                if existing is None:
                    self.stdout.write(f"CREATE {code}: {fields}")
                    created += 1
                else:
                    diff = {k: (getattr(existing, k, None), v) for k, v in fields.items()
                            if getattr(existing, k, None) != v}
                    if diff:
                        self.stdout.write(f"UPDATE {code}: {diff}")
                        updated += 1
                    else:
                        self.stdout.write(f"OK     {code}: unchanged")
                continue
            with transaction.atomic():
                obj, was_created = LeaveType.objects.update_or_create(
                    code=code, defaults={**fields, "is_active": True})
            created += int(was_created)
            updated += int(not was_created)
            self.stdout.write(f"{'CREATED' if was_created else 'UPDATED'} {code} "
                              f"({fields['default_annual_days']}d @ {fields['paid_pct']}% · "
                              f"{fields['statutory_ref']})")
        self.stdout.write(self.style.SUCCESS(
            f"{'DRY-RUN ' if dry else ''}done — {created} created, {updated} updated, "
            f"{len(ELRA_BW_LEAVE)} ELRA leave types."))
