"""
payroll/signoff_models.py — CFO sign-off on a company's monthly payroll.

CFO directive 2026-07-26. He asked why Veritas / ADIC / Risk payrolls were not
in his task dashboard to sign off. They never could be:

  * ``core.approvals_views.pending_approvals_for()`` carried FOUR streams —
    leave, spend requests, refunds, purchase orders. Payroll was not one.
  * ``PayrollPeriod.Status`` already models open -> locked ("awaiting
    approval") -> approved -> posted -> paid, but on prod every period from
    2026-03 to 2026-07 was still ``open`` and every payslip still ``draft``.
    Nobody ever moved a month forward, so even the payroll module did not
    believe anything was awaiting sign-off.

He chose option A: the CFO signs each month once payroll is calculated, and
the payroll team must submit it to him.

Granularity is period x company on purpose. ``PayrollPeriod`` is ONE row per
month group-wide, but payroll is prepared, checked and paid per entity — the
CFO signs "ADIC July 2026", not "July 2026".

Totals are SNAPSHOTTED at submission: the CFO signs a specific headcount and a
specific net. If the payslips move after submission the snapshot no longer
matches the live figures and approval is refused until payroll re-submits —
the same time-of-check/time-of-use hole Fable 5 found in leave encashment
(PR #409).
"""

from __future__ import annotations

from decimal import Decimal

from django.contrib.auth.models import User
from django.db import models
from django.db.models import Count, Sum

from core.models import AuditableMixin, BaseModel, Company

ZERO = Decimal('0.00')


def live_payroll_totals(period, company) -> dict:
    """Headcount + gross/PAYE/net for one company's payslips in one period.

    Counts EVERY payslip except cancelled ones — the sign-off is about what
    the payroll run currently says, and payslips are all still 'draft' in
    practice (see reference_payslip_status_draft: status is unreliable, so it
    is never used as a filter here beyond excluding cancellations).
    """
    from .models import Payslip

    row = (Payslip.objects
           .filter(period=period, company=company)
           .exclude(status=Payslip.Status.CANCELLED)
           .aggregate(n=Count('id'),
                      gross=Sum('gross_amount'),
                      paye=Sum('paye_amount'),
                      net=Sum('net_amount')))
    return {
        'headcount': row['n'] or 0,
        'gross':     row['gross'] or ZERO,
        'paye':      row['paye'] or ZERO,
        'net':       row['net'] or ZERO,
    }


class PayrollSignOff(AuditableMixin, BaseModel):
    """One company's payroll for one month, submitted for CFO sign-off."""

    class Status(models.TextChoices):
        SUBMITTED = 'submitted', 'Awaiting CFO sign-off'
        APPROVED  = 'approved',  'Signed off by CFO'
        REJECTED  = 'rejected',  'Sent back by CFO'

    period  = models.ForeignKey(
                  'payroll.PayrollPeriod', on_delete=models.PROTECT,
                  related_name='sign_offs',
              )
    company = models.ForeignKey(
                  Company, on_delete=models.PROTECT,
                  related_name='payroll_sign_offs',
              )
    status  = models.CharField(max_length=12, choices=Status.choices,
                               default=Status.SUBMITTED)

    # ── Snapshot taken at submission — what the CFO is actually signing ──────
    headcount   = models.PositiveIntegerField(default=0)
    gross_total = models.DecimalField(max_digits=18, decimal_places=2, default=ZERO)
    paye_total  = models.DecimalField(max_digits=18, decimal_places=2, default=ZERO)
    net_total   = models.DecimalField(max_digits=18, decimal_places=2, default=ZERO)

    submitted_by = models.ForeignKey(User, null=True, blank=True,
                                     on_delete=models.SET_NULL,
                                     related_name='payroll_sign_offs_submitted',
                                     help_text='Who first brought this month forward '
                                               '(the earlier of the two signers).')
    submitted_at = models.DateTimeField(null=True, blank=True)
    submit_note  = models.TextField(blank=True, default='')

    # ── Dual sign-off (CFO directive 2026-07-28) ────────────────────────────
    # A company-month is CLOSED (payslips release) when BOTH an HR signer
    # (Unami / Dorothy) AND a Finance signer (Kago / Pako) have signed the SAME
    # current figures. The CFO is out of the routine loop (can still back-stop).
    hr_signed_by  = models.ForeignKey(User, null=True, blank=True,
                                      on_delete=models.SET_NULL,
                                      related_name='payroll_sign_offs_hr')
    hr_signed_at  = models.DateTimeField(null=True, blank=True)
    fin_signed_by = models.ForeignKey(User, null=True, blank=True,
                                      on_delete=models.SET_NULL,
                                      related_name='payroll_sign_offs_fin')
    fin_signed_at = models.DateTimeField(null=True, blank=True)

    # Kept for record: stamped when the SECOND signature lands (= fully signed).
    approved_by = models.ForeignKey(User, null=True, blank=True,
                                    on_delete=models.SET_NULL,
                                    related_name='payroll_sign_offs_approved')
    approved_at = models.DateTimeField(null=True, blank=True)

    rejected_by      = models.ForeignKey(User, null=True, blank=True,
                                         on_delete=models.SET_NULL,
                                         related_name='payroll_sign_offs_rejected')
    rejected_at      = models.DateTimeField(null=True, blank=True)
    rejection_reason = models.TextField(blank=True, default='')

    class Meta(BaseModel.Meta):
        ordering        = ['-period__start_date', 'company__name']
        unique_together = [('period', 'company')]
        verbose_name        = 'Payroll Sign-off'
        verbose_name_plural = 'Payroll Sign-offs'

    def __str__(self):
        return (f'{self.company.name} {self.period.period_name} '
                f'— {self.get_status_display()}')

    @property
    def both_signed(self) -> bool:
        """Both the HR and the Finance leg carry a signature."""
        return bool(self.hr_signed_by_id and self.fin_signed_by_id)

    # ── Snapshot integrity ──────────────────────────────────────────────────
    def live_totals(self) -> dict:
        return live_payroll_totals(self.period, self.company)

    def drift(self) -> dict | None:
        """None when the live payroll still matches what was submitted,
        otherwise {'submitted': {...}, 'live': {...}} for the UI/error."""
        live = self.live_totals()
        submitted = {
            'headcount': self.headcount,
            'gross':     self.gross_total,
            'paye':      self.paye_total,
            'net':       self.net_total,
        }
        if (live['headcount'] == submitted['headcount']
                and live['gross'] == submitted['gross']
                and live['paye'] == submitted['paye']
                and live['net'] == submitted['net']):
            return None
        return {'submitted': submitted, 'live': live}
