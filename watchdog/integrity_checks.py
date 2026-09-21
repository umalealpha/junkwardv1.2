"""watchdog/integrity_checks.py — "do the modules still talk to each other?"

Read-only cross-module checks. These encode the CFO's 2026-08-21 "known gaps"
(Claims / Leases / Commissions never reach the GL; Payments send no signal to
supplier recon) plus the trial-balance invariant from
.claude/steering/erp-relationships.md (rule 6: debits == credits).

Every finance/GL finding is tagged domains=("finance","gl"), so watchdog.severity
marks it DANGER — reported for a human, never auto-fixed.
"""
from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

from django.db.models import Sum
from django.utils import timezone

from watchdog.checks import CheckResult, register

_CENT = Decimal("0.01")


@register(key="trial_balance", label="Trial balance (debits == credits)",
          module="ledger", category="integrity")
def trial_balance():
    """Rule 6: posted debits must equal posted credits, per company (BWP)."""
    from core.models import Company
    from ledger.models import JournalEntryLine

    results = []
    for co in Company.objects.filter(is_active=True):
        agg = (JournalEntryLine.objects
               .filter(journal_entry__company_id=co.id,
                       journal_entry__status="posted")
               .aggregate(d=Sum("debit_bwp"), c=Sum("credit_bwp")))
        d, c = agg["d"] or Decimal("0"), agg["c"] or Decimal("0")
        if d == 0 and c == 0:
            continue                        # no posted activity — nothing to assert
        diff = (d - c).quantize(_CENT)
        name = co.code or co.name
        if abs(diff) > _CENT:
            results.append(CheckResult(
                ok=False,
                title=f"Trial balance out of balance — {name}",
                detail=(f"Posted debits {d:,.2f} vs credits {c:,.2f} "
                        f"(difference {diff:,.2f} BWP)."),
                domains=("finance", "gl", "posting"), severity="danger"))
        else:
            results.append(CheckResult(ok=True, title=f"TB balanced — {name}"))
    if not results:
        results.append(CheckResult(ok=True, title="No posted ledger activity"))
    return results


@register(key="claims_gl_link", label="Claims settlements reach the GL",
          module="claims", category="integrity")
def claims_gl_link():
    """Known gap: claim settlements (in Graphite) never post a claims-expense JE
    into Omni — there is no link in the schema."""
    from integrations.models import GraphiteClaim

    settled = GraphiteClaim.objects.filter(total_payment__gt=0)
    n = settled.count()
    if not n:
        return [CheckResult(ok=True, title="No settled claims to post")]
    recent = settled.filter(
        created_at__gte=timezone.now() - timedelta(days=30)).count()
    return [CheckResult(
        ok=False,
        title="Claims settlements do not post to the General Ledger",
        detail=(f"{n:,} settled claim(s) mirrored from Graphite ({recent:,} in the "
                f"last 30 days) — no journal entry links a claim settlement to the "
                f"books. Standing architectural gap."),
        domains=("finance", "gl"), severity="danger")]


@register(key="leases_gl_link", label="Lease schedules reach the GL",
          module="leases", category="integrity")
def leases_gl_link():
    """Known gap: the IFRS-16 engine computes ROU/liability schedules but never
    writes a journal entry (schedules are derived, never persisted)."""
    from leases.models import Lease

    n = Lease.objects.count()
    if not n:
        return [CheckResult(ok=True, title="No leases to post")]
    return [CheckResult(
        ok=False,
        title="IFRS-16 lease postings never reach the GL",
        detail=(f"{n} lease(s) produce ROU/liability schedules in the engine that "
                f"are never written as journal entries. Standing architectural gap."),
        domains=("finance", "gl"), severity="danger")]


@register(key="commissions_gl_link", label="Commissions reach the GL",
          module="commissions", category="integrity")
def commissions_gl_link():
    """Known gap: approved/paid commissions have no journal-entry link — GL
    posting is manual."""
    from commissions.models import CommissionSubmission

    qs = CommissionSubmission.objects.filter(status__in=["approved", "paid"])
    n = qs.count()
    if not n:
        return [CheckResult(ok=True, title="No approved commissions pending GL")]
    gross = qs.aggregate(s=Sum("gross_commission"))["s"] or Decimal("0")
    return [CheckResult(
        ok=False,
        title="Commissions post to the GL by hand",
        detail=(f"{n} approved/paid commission submission(s) (gross {gross:,.2f} "
                f"BWP) carry no journal-entry link — GL posting is manual."),
        domains=("finance", "gl"), severity="danger")]


@register(key="payment_recon_signal", label="Payments signal supplier recon",
          module="payments", category="integrity")
def payment_recon_signal():
    """Known gap: a confirmed payment sends no signal — supplier recon is a
    scheduled snapshot, so it can lag new confirmed payments. Safe to re-run."""
    from payments.models import Payment
    from supplier_recon.models import SupplierReconRun

    newest = (Payment.objects.filter(status="confirmed")
              .order_by("-created_at").first())
    if newest is None:
        return [CheckResult(ok=True, title="No confirmed payments")]
    last_run = SupplierReconRun.objects.order_by("-created_at").first()
    if last_run is None:
        return [CheckResult(
            ok=False,
            title="Supplier reconciliation has never run",
            detail="Confirmed payments exist but no supplier-recon snapshot exists.",
            severity="warn", safe_to_autofix=True,
            autofix_note="run the supplier-recon snapshot")]
    if newest.created_at > last_run.created_at:
        return [CheckResult(
            ok=False,
            title="Supplier recon is behind confirmed payments",
            detail=(f"A payment was confirmed after the last supplier-recon snapshot "
                    f"({last_run.created_at:%d %b %H:%M}). Recon recomputes on a "
                    f"schedule; it is not signalled when a payment is confirmed."),
            severity="warn", safe_to_autofix=True,
            autofix_note="re-run the supplier-recon snapshot")]
    return [CheckResult(ok=True, title="Supplier recon current")]
