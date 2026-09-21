"""
python manage.py generate_hris_alerts

Idempotent.  Scans contracts / leave / reviews and writes/updates
HRISAlert rows.  Per Unami's 2026-06-02 wishlist:

  * Contract expiry within 60 days
  * Leave balance over the legal cap (default 60 days, configurable per leave-type)
  * Performance review due (review_date within 30 days, status='draft')
  * Quarterly review due (the next quarter end within 30 days)

Run nightly via cron / launchd.
"""
from __future__ import annotations

from datetime import date, timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from payroll.models import EmploymentContract, Employee
from hris.models import HRISAlert, LeaveRequest, PerformanceReview, HRISProfile
from hris.feature_views import get_leave_rules


DEFAULT_CONTRACT_LOOKAHEAD_DAYS = 60
DEFAULT_LEAVE_CAP_DAYS = 60        # generic legal-cap; can be overridden per leave-type later
DEFAULT_REVIEW_LOOKAHEAD_DAYS = 30


class Command(BaseCommand):
    help = 'Generate HRIS alerts (contract expiry, leave excess, review due).'

    def add_arguments(self, parser):
        parser.add_argument('--contract-days', type=int, default=DEFAULT_CONTRACT_LOOKAHEAD_DAYS)
        parser.add_argument('--leave-cap', type=int, default=DEFAULT_LEAVE_CAP_DAYS)
        parser.add_argument('--review-days', type=int, default=DEFAULT_REVIEW_LOOKAHEAD_DAYS)

    def _upsert(self, kind, target_id, **fields):
        """Idempotent: keyed on (kind, target_id)."""
        obj, _ = HRISAlert.objects.update_or_create(
            kind=kind, target_id=str(target_id),
            defaults=fields,
        )
        return obj

    def handle(self, *args, **opts):
        today = timezone.localdate()
        counts = dict(contract=0, leave=0, mandatory_take=0, review=0, quarterly=0)

        # ── Contracts expiring soon ───────────────────────────────────
        cutoff = today + timedelta(days=opts['contract_days'])
        qs = (EmploymentContract.objects
              .filter(end_date__isnull=False,
                      end_date__gte=today, end_date__lte=cutoff,
                      status='active')
              .select_related('employee'))
        for c in qs:
            days_left = (c.end_date - today).days
            sev = HRISAlert.SEV_HIGH if days_left <= 30 else HRISAlert.SEV_MEDIUM
            self._upsert(
                kind=HRISAlert.KIND_CONTRACT_EXPIRY,
                target_id=c.id,
                target_kind='employment_contract',
                employee=c.employee,
                profile=getattr(c.employee, 'hris_profile', None),
                severity=sev,
                state=HRISAlert.STATE_OPEN,
                title=f'Contract expires in {days_left} days — {c.employee.full_name}',
                detail=(f'Employment contract {c.id} for {c.employee.full_name} '
                        f'({c.employee.department}) ends {c.end_date}. '
                        f'Initiate renewal or off-boarding.'),
                due_date=c.end_date,
            )
            counts['contract'] += 1

        # ── Leave-balance excess (approved future LR aggregate above cap) ──
        # Simple heuristic: sum of approved leave days per profile in current year
        from django.db.models import Sum
        year_start = date(today.year, 1, 1)
        agg = (LeaveRequest.objects
               .filter(status='approved',
                       start_date__gte=year_start,
                       start_date__lte=today + timedelta(days=365))
               .values('profile_id')
               .annotate(total=Sum('days')))
        cap = opts['leave_cap']
        for row in agg:
            if (row['total'] or 0) <= cap:
                continue
            try:
                profile = HRISProfile.objects.select_related('employee').get(id=row['profile_id'])
            except HRISProfile.DoesNotExist:
                continue
            self._upsert(
                kind=HRISAlert.KIND_LEAVE_EXCESS,
                target_id=profile.id,
                target_kind='hris_profile',
                employee=profile.employee,
                profile=profile,
                severity=HRISAlert.SEV_MEDIUM,
                state=HRISAlert.STATE_OPEN,
                title=f'Leave balance excess — {profile.employee.full_name}',
                detail=(f'{profile.employee.full_name} has {row["total"]} approved '
                        f'leave days this year, above the {cap}-day legal cap. '
                        f'Action: encourage uptake or carry-over approval.'),
            )
            counts['leave'] += 1

        # ── Mandatory annual-leave take (ELRA s.219) ──────────────────
        # 8 annual days must be TAKEN within 6 months of cycle end. Cycle =
        # calendar year; flag from H2 (month >= 7) any active profile that has
        # taken fewer than the mandated minimum, escalating in Q4. min_take is
        # read from the editable leave rules (get_leave_rules), not hard-coded.
        min_take = int(get_leave_rules().get('annual', {}).get('min_take', 8) or 0)
        if min_take and today.month >= 7:
            annual_taken = dict(
                LeaveRequest.objects
                .filter(status='approved', start_date__gte=year_start,
                        leave_type__code__iexact='annual')
                .values_list('profile_id')
                .annotate(total=Sum('days'))
                .values_list('profile_id', 'total'))
            for profile in HRISProfile.objects.select_related('employee'):
                if not profile.employee_id:
                    continue
                taken = float(annual_taken.get(profile.id) or 0)
                if taken >= min_take:
                    continue
                sev = HRISAlert.SEV_HIGH if today.month >= 10 else HRISAlert.SEV_MEDIUM
                self._upsert(
                    kind=HRISAlert.KIND_LEAVE_MANDATORY_TAKE,
                    target_id=str(profile.id),   # UUID is 36 chars; target_id is varchar(40)
                    target_kind='hris_profile',
                    employee=profile.employee,
                    profile=profile,
                    severity=sev,
                    state=HRISAlert.STATE_OPEN,
                    title=f'Mandatory leave not taken ({today.year}) — {profile.employee.full_name}',
                    detail=(f'{profile.employee.full_name} has taken {taken:.0f} of the '
                            f'{min_take} annual-leave days that must be used within 6 months '
                            f'of cycle end (ELRA s.219). Action: schedule the balance before year-end.'),
                    due_date=date(today.year, 12, 31),
                )
                counts['mandatory_take'] += 1

        # ── Performance reviews due ───────────────────────────────────
        review_cutoff = today + timedelta(days=opts['review_days'])
        qs = (PerformanceReview.objects
              .filter(review_date__isnull=False,
                      review_date__gte=today, review_date__lte=review_cutoff,
                      status='draft')
              .select_related('profile__employee'))
        for r in qs:
            self._upsert(
                kind=HRISAlert.KIND_REVIEW_DUE,
                target_id=r.id,
                target_kind='performance_review',
                employee=r.profile.employee,
                profile=r.profile,
                severity=HRISAlert.SEV_MEDIUM,
                state=HRISAlert.STATE_OPEN,
                title=f'Review due — {r.profile.employee.full_name} ({r.period})',
                detail=f'Performance review {r.id} draft pending; scheduled {r.review_date}.',
                due_date=r.review_date,
            )
            counts['review'] += 1

        # ── Quarterly check-in due ────────────────────────────────────
        # The next financial-quarter end (FY = Jul→Jun): Sep 30, Dec 31, Mar 31, Jun 30
        q_ends = [date(today.year, 9, 30), date(today.year, 12, 31),
                  date(today.year + 1, 3, 31), date(today.year + 1, 6, 30)]
        next_q = next((q for q in q_ends if q >= today), None)
        if next_q and (next_q - today).days <= 30:
            for prof in HRISProfile.objects.select_related('employee'):
                self._upsert(
                    kind=HRISAlert.KIND_QUARTERLY_REVIEW,
                    # UUID(36)+':Q3' fits varchar(40); the full ISO date overflowed.
                    target_id=f'{prof.id}:Q{(next_q.month - 1) // 3 + 1}',
                    target_kind='quarterly_review_window',
                    employee=prof.employee,
                    profile=prof,
                    severity=HRISAlert.SEV_LOW,
                    state=HRISAlert.STATE_OPEN,
                    title=f'Quarterly review due {next_q} — {prof.employee.full_name}',
                    detail='Quarterly performance check-in window opens.',
                    due_date=next_q,
                )
                counts['quarterly'] += 1

        self.stdout.write(self.style.SUCCESS(
            f'HRIS alerts: contract={counts["contract"]} leave={counts["leave"]} '
            f'mandatory_take={counts["mandatory_take"]} '
            f'review={counts["review"]} quarterly={counts["quarterly"]}'
        ))
