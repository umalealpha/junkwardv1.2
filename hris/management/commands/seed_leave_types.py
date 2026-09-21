"""
Seed LeaveType rows from Alpha Direct Conditions of Service v2023 §7.

Source: 'Alpha Direct Insurance Co Conditions of Service-2023 Version.docx'
        attached by Unami Butale (HR) 2026-06-03, parsed paragraphs 370-452.

Idempotent — uses LeaveType.code as natural key, updates name + default
days + paid flag on every run, never deletes existing rows.

Annual / vacation leave is grade-banded per §7.1 — the bands are encoded
as separate VAC_* rows because LeaveType has no per-grade rate field:

    VAC_DRIVER     18 days  (Drivers, Intern, Industry Attaché, Inside Sales)
    VAC_JR_ASSOC   19 days  (Junior Associate)
    VAC_ASSOC      20 days  (Associate)
    VAC_SR_ASSOC   21 days  (Senior Associate)
    VAC_ASST_MGR   22 days  (Assistant Manager)
    VAC_MGR        25 days  (Manager and above)

Carry-over cap = 2× annual entitlement, max 3 years accumulation
(§7.2-7.3). These rules are NOT stored on LeaveType yet — the model
only carries default_annual_days. Track this in a follow-up migration
that adds:  carry_over_cap_days, probation_months, requires_medical_cert,
half_pay_after_days, paid_pct_after_days.

Run:
    python manage.py seed_leave_types
"""
from django.core.management.base import BaseCommand
from hris.models import LeaveType


SEED = [
    # code, name, default_annual_days, is_paid,
    # carry_over_cap, max_carry_yrs, probation_months, requires_med, paid_pct
    ('VAC_DRIVER',    'Annual Leave — Drivers / Intern / Attaché / Inside Sales', 18, True,
         36, 3, 0, False, 100),
    ('VAC_JR_ASSOC',  'Annual Leave — Junior Associate',                          19, True,
         38, 3, 0, False, 100),
    ('VAC_ASSOC',     'Annual Leave — Associate',                                 20, True,
         40, 3, 0, False, 100),
    ('VAC_SR_ASSOC',  'Annual Leave — Senior Associate',                          21, True,
         42, 3, 0, False, 100),
    ('VAC_ASST_MGR',  'Annual Leave — Assistant Manager',                         22, True,
         44, 3, 0, False, 100),
    ('VAC_MGR',       'Annual Leave — Manager and above',                         25, True,
         50, 3, 0, False, 100),
    ('SICK',          'Sick Leave (20 days, no accrual)',                         20, True,
          0, 0, 0, True, 100),
    ('HOSP_FULL',     'Hospitalisation — full pay (up to 130 working days)',     130, True,
          0, 0, 0, True, 100),
    ('HOSP_HALF',     'Hospitalisation — half pay (next 6 months)',              130, True,
          0, 0, 0, True, 50),
    ('MATERNITY',     'Maternity Leave (84 calendar days @ 50% pay)',             84, True,
          0, 0, 0, True, 50),
    ('PATERNITY',     'Paternity Leave (5 non-continuous working days)',           5, True,
          0, 0, 0, True, 100),
    ('LWOP',          'Leave Without Pay (case-by-case, earned leave first)',     0, False,
          0, 0, 0, False, 0),
    ('SPECIAL',       'Special Leave (10 days extenuating / 5 days sport)',      10, True,
          0, 0, 0, False, 100),
    ('STUDY_SPONS',   'Study Leave — sponsored (yr1 100%, yr2 75%, yr3 50%)',   365, True,
          0, 0, 0, False, 100),
    ('STUDY_SELF',    'Study Leave — self-study (10 days/yr, 5 per semester)',  10, True,
          0, 0, 0, False, 100),
    ('COMPASSION',    'Compassionate Leave (5 days, next-of-kin death)',          5, True,
          0, 0, 0, False, 100),
    # Time Doctor deduction (HR request, 2026-08-25; CFO chose employee
    # self-service). Staff whose tracked hours fall short apply via Omni stating
    # this reason, so the shortfall is recorded rather than silent. A normal leave
    # type in the same apply flow as annual/sick. UNPAID (is_paid=False,
    # paid_pct=0), zero entitlement, no cert, no probation.
    #
    # 🔴 The code MUST be lowercase 'td_deduct' to match feature_views.apply_leave,
    # which lowercases the type and get_or_creates a LeaveType. An UPPERCASE row
    # would not match, so applying would auto-create a duplicate with the model
    # default paid_pct=100 — silently making the deduction PAID.
    ('td_deduct',     'Time Doctor Deduction (unpaid - tracked-hours shortfall)',   0, False,
          0, 0, 0, False, 0),
]


class Command(BaseCommand):
    help = 'Seed Alpha Direct LeaveType rows from CoS-2023 §7. Idempotent.'

    def handle(self, *args, **opts):
        created = updated = 0
        for row in SEED:
            code, name, days, paid, carry_cap, carry_yrs, prob_m, req_med, paid_pct = row
            obj, made = LeaveType.objects.update_or_create(
                code=code,
                defaults={
                    'name':                  name,
                    'default_annual_days':   days,
                    'is_paid':               paid,
                    'is_active':             True,
                    'carry_over_cap_days':   carry_cap,
                    'max_carry_over_years':  carry_yrs,
                    'probation_months':      prob_m,
                    'requires_medical_cert': req_med,
                    'paid_pct':              paid_pct,
                },
            )
            if made:
                created += 1
                self.stdout.write(self.style.SUCCESS(f'  CREATED  {code:14} {days:>3}d  {name[:60]}'))
            else:
                updated += 1
                self.stdout.write(f'  updated  {code:14} {days:>3}d  {name[:60]}')

        self.stdout.write(self.style.SUCCESS(
            f'\nDone. {created} created, {updated} updated. Total now: {LeaveType.objects.count()}'
        ))
