"""
reinsurance/cession_service.py

Auto-cession pass — for a given fiscal period and company, walk every
posted customer invoice and generate draft Cession rows against each
active treaty that covers the period.

This is a *backbone* implementation: it computes the cession amount
proportionally from `treaty.cession_share_percent`. Non-proportional
treaties (XL / Stop-Loss) are skipped because their structure isn't a
per-invoice % share — they need claim-level data.

Idempotency
-----------
The pass is safe to re-run. A unique constraint on Cession(invoice, treaty)
(see models.Cession.Meta.constraints) prevents duplicate rows. The service
also pre-checks existence and reports skipped counts.

Posting
-------
Cessions are created in `draft` status. The actual GL journal is posted by
the existing reinsurance/services.py::post_cession path (separate review +
approve step). Auto-cession only stages the draft data.

Usage
-----
    from reinsurance.cession_service import run_cession_pass
    result = run_cession_pass(period, company_id=adic.id)

Mgmt command: ``python manage.py run_cession_pass --company ADIC --period FY26_9M``
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal, ROUND_HALF_UP
from typing import Optional

from django.db import transaction

from billing.models import Invoice
from .models import Cession, ReinsuranceTreaty


ZERO = Decimal('0.00')
HUNDRED = Decimal('100')
TWO_PLACES = Decimal('0.01')


@dataclass
class CessionPassResult:
    period: str
    company_id: Optional[str]
    invoices_processed: int = 0
    cessions_created: int = 0
    cessions_skipped: int = 0
    treaties_considered: int = 0


def _period_bounds(period) -> tuple[date, date]:
    """
    Resolve `period` to (start_date, end_date).

    Accepts:
      - a ledger.FiscalPeriod instance (uses .start_date / .end_date)
      - a fiscal-period name string ("2026-04") — looked up in FiscalPeriod
      - a (start_date, end_date) tuple — used directly
      - a string for a multi-period range ("FY26_9M") — looked up by
        period_name in FiscalPeriod first; if no match the caller must
        pass an explicit tuple. The lookup mirrors how other services
        resolve periods, falling back cleanly when not found.
    """
    from ledger.models import FiscalPeriod

    if hasattr(period, 'start_date') and hasattr(period, 'end_date'):
        return period.start_date, period.end_date

    if isinstance(period, tuple) and len(period) == 2:
        return period[0], period[1]

    if isinstance(period, str):
        fp = FiscalPeriod.objects.filter(period_name=period).first()
        if fp is not None:
            return fp.start_date, fp.end_date
        # FY26_9M-style names: span the matching fiscal-year periods.
        # Looks for periods whose period_name starts with the prefix
        # (e.g. "2025-" for FY26 if your CoA names them that way) — keep
        # the resolver tolerant; the mgmt command provides explicit dates
        # when ambiguity matters.
        qs = FiscalPeriod.objects.filter(period_name__startswith=period[:4])
        if qs.exists():
            return (
                qs.order_by('start_date').first().start_date,
                qs.order_by('-end_date').first().end_date,
            )

    raise ValueError(f'Cannot resolve period: {period!r}')


def _round_money(value: Decimal) -> Decimal:
    """Round to 2 decimal places, BWP convention."""
    return value.quantize(TWO_PLACES, rounding=ROUND_HALF_UP)


@transaction.atomic
def run_cession_pass(period, company_id=None) -> CessionPassResult:
    """
    For `period` (FiscalPeriod, period name, or (start, end) tuple) and
    optional `company_id`, walk every posted customer invoice issued in
    the window and generate draft Cession rows for each active
    proportional treaty whose effective dates span the invoice issue date.

    Returns CessionPassResult with counts. Idempotent on re-run.
    """
    start_date, end_date = _period_bounds(period)

    period_label = (
        period if isinstance(period, str)
        else getattr(period, 'period_name', f'{start_date}..{end_date}')
    )
    result = CessionPassResult(period=period_label, company_id=str(company_id) if company_id else None)

    # ----- 1. Pull active proportional treaties covering this window --------
    # The model has BOTH a status field and inception/expiry dates. "Active"
    # for the auto-cession purpose means: status == ACTIVE AND treaty
    # effective dates intersect the period window.
    treaty_qs = ReinsuranceTreaty.objects.filter(
        status=ReinsuranceTreaty.Status.ACTIVE,
        inception_date__lte=end_date,
        expiry_date__gte=start_date,
        cession_share_percent__gt=ZERO,
    )
    treaties = list(treaty_qs)
    result.treaties_considered = len(treaties)
    if not treaties:
        return result

    # ----- 2. Pull posted customer invoices in the window -------------------
    # NOTE on contact_type: the spec said 'policyholder' "or similar field —
    # inspect model". billing.Contact.ContactType has CUSTOMER (not
    # POLICYHOLDER). For Alpha Direct, customer invoices ARE policy
    # premium invoices, so customer == policyholder here.
    invoice_qs = (
        Invoice.objects
        .filter(
            invoice_type=Invoice.InvoiceType.CUSTOMER_INVOICE,
            status__in=[
                Invoice.Status.POSTED,
                Invoice.Status.PARTIALLY_PAID,
                Invoice.Status.PAID,
                Invoice.Status.OVERDUE,
            ],
            issue_date__gte=start_date,
            issue_date__lte=end_date,
            contact__contact_type='customer',
        )
        .select_related('contact')
    )
    if company_id is not None:
        invoice_qs = invoice_qs.filter(company_id=company_id)

    # ----- 3. For each invoice × treaty, create a draft Cession (idempotent) -
    for invoice in invoice_qs.iterator():
        result.invoices_processed += 1
        gross = invoice.total_amount or ZERO
        if gross <= ZERO:
            continue

        for treaty in treaties:
            # Treaty currency / multi-currency: v1 assumes BWP equivalence.
            # The treaty share is applied to the invoice total as-presented;
            # FX translation is handled separately downstream when posting.

            # Idempotency: skip if a Cession already exists for this pair.
            already = Cession.objects.filter(
                invoice=invoice, treaty=treaty,
            ).exists()
            if already:
                result.cessions_skipped += 1
                continue

            share_pct = treaty.cession_share_percent or ZERO
            ceded = _round_money(gross * share_pct / HUNDRED)
            if ceded <= ZERO:
                # 0% treaty or rounding floor — nothing to cede, still
                # count as "considered, nothing to create" rather than
                # creating a zero row.
                continue

            Cession.objects.create(
                treaty=treaty,
                invoice=invoice,
                cession_date=invoice.issue_date or start_date,
                policy_reference=invoice.invoice_number or '',
                risk_description=(invoice.contact.name if invoice.contact else '')[:200],
                gross_premium=gross,
                ceded_premium=ceded,
                share_percent=share_pct,
                status=Cession.Status.DRAFT,
            )
            result.cessions_created += 1

    return result
