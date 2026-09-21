"""Seed the CFO-directed PIP for Legakwa Ntabeni — delayed commission-payment
processing (CFO directive 2026-07-15).

Facts (from the Charmaine Bamusi ↔ CFO thread, 15 Jul 2026): completed
commission/incentive payment requests were submitted on time — Liberty on
5 June 2026, Market SA on 9 June 2026 — under the agreed monthly catch-up
arrangement, but were only paid on 13 July 2026 (~5 weeks late) at the
payment-processing stage.

Idempotent: re-running updates the same directed PIP rather than creating a
duplicate. Visible to the subject (Legakwa) plus Kago Tshutlhedi, Bharath
Balasubramanian and Unami Butale.
"""
from __future__ import annotations

import datetime as dt

from django.contrib.auth.models import User
from django.core.management.base import BaseCommand

from payroll.models import Employee
from hris.models import HRISProfile
from hris.performance_feedback_models import PerformanceImprovementPlan

SUBJECT_EMAIL = 'lntabeni@alphadirect.co.bw'
SUBJECT_NAME = 'Legakwa Ntabeni'
CFO_EMAIL = 'pganesharajah@alphadirect.co.bw'
VIEWERS = [
    'ktshutlhedi@alphadirect.co.bw',   # Kago Tshutlhedi
    'bbalasubramanian@alphadirect.co.bw',  # Bharath Balasubramanian
    'ubutale@alphadirect.co.bw',       # Unami Butale
]
TITLE = 'Delay in Processing Completed Commission Payments'

REASON = (
    "Completed commission / incentive payment requests were submitted on time by "
    "Charmaine Bamusi under the agreed monthly catch-up arrangement — Liberty on "
    "5 June 2026 and Market SA on 9 June 2026 — but the payments were only processed "
    "on 13 July 2026, a delay of approximately five weeks. The requests were sent on "
    "time; the delay occurred at the payment-processing stage, which is a core "
    "responsibility of the role. Late payment of due commissions exposes the company "
    "to reputational and relationship risk with its partners."
)

OBJECTIVES = (
    "1. Process approved commission / incentive payments within 3 working days of "
    "receiving a complete, approved request.\n"
    "2. Do not hold any approved payment without recording a same-day reason and "
    "escalating the blocker to the Financial Controller / CFO.\n"
    "3. Provide a weekly status of all pending payment requests (amount, partner, "
    "date received, expected pay date).\n"
    "4. Zero unexplained payment delays over the review period."
)

SUPPORT_PLAN = (
    "Support provided: a clear payment-processing turnaround standard, direct access "
    "to the payment queue, and an escalation path to the Financial Controller and CFO "
    "for any blocker (funding, approval, or data). Weekly check-in with the Financial "
    "Controller during the review period."
)


class Command(BaseCommand):
    help = "Create/update the CFO-directed PIP for Legakwa Ntabeni (commission-payment delay)."

    def handle(self, *args, **opts):
        emp = (Employee.objects.filter(email__iexact=SUBJECT_EMAIL).first()
               or Employee.objects.filter(full_name__iexact=SUBJECT_NAME).first())
        if emp is None:
            self.stderr.write(f"Employee not found for {SUBJECT_EMAIL} / {SUBJECT_NAME}.")
            return
        profile, _ = HRISProfile.objects.get_or_create(employee=emp)
        opened_by = User.objects.filter(email__iexact=CFO_EMAIL).first()

        start = dt.date(2026, 7, 15)
        review = dt.date(2026, 8, 15)

        pip = (PerformanceImprovementPlan.objects
               .filter(profile=profile, directed=True, title=TITLE).first())
        created = False
        if pip is None:
            pip = PerformanceImprovementPlan(profile=profile, directed=True, title=TITLE)
            created = True

        pip.directed = True
        pip.title = TITLE
        pip.reason = REASON
        pip.objectives = OBJECTIVES
        pip.support_plan = SUPPORT_PLAN
        pip.start_date = start
        pip.review_date = review
        pip.viewer_emails = VIEWERS
        if opened_by:
            pip.opened_by = opened_by
        if pip.status not in (PerformanceImprovementPlan.Status.MET,
                              PerformanceImprovementPlan.Status.NOT_MET,
                              PerformanceImprovementPlan.Status.CLOSED):
            # Keep IN_PROGRESS if the employee already explained; else OPEN.
            pip.status = (PerformanceImprovementPlan.Status.IN_PROGRESS
                          if pip.employee_explanation
                          else PerformanceImprovementPlan.Status.OPEN)
        pip.save(audit_user=opened_by)

        self.stdout.write(self.style.SUCCESS(
            f"{'Created' if created else 'Updated'} directed PIP {pip.id} for "
            f"{emp.full_name} (viewers: {', '.join(VIEWERS)})."))
