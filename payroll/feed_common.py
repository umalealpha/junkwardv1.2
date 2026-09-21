"""
payroll/feed_common.py — the ONE mechanism the automatic payroll feeds share.

This is not a new pattern. It is the pattern already proven by
`commissions/payroll_feed.py` (marker AUTO-COMMISSION) and
`hris/incentive_payroll_feed.py` (marker AUTO-INCENTIVE), lifted into one
place so the three feeds added for the Build Spec (B10 leave pay, B11
severance, B12 salary advance) are TWINS of those two rather than three more
half-copies that drift apart.

What it gives a feed:

* `target_period` / `baseline_for` — the same period lookup both originals use.
* `canonical_batch` — ONE `PayrollAmendmentBatch` per (target period, company),
  created `PARSED` and **never applied**. The monthly close reviews and applies;
  dual sign-off still pays. Omni never moves money — a payroll amendment is a
  record, nothing more.
* `make_feed_key` — a deterministic fingerprint written to the new
  `PayrollAmendment.feed_key` column, which is **UNIQUE AT THE DATABASE LEVEL**
  (the `banking.BankStatementLine.dedupe_key` pattern). A Python `if` that
  checks before writing is not a control: two workers, two requests or a
  re-run inside a race can both pass it. The unique index cannot be raced —
  the second write is refused by Postgres itself.
* `record_skip` — never-silent: something that could not be fed shows up on the
  batch as a zero-amount row carrying the reason, instead of vanishing.

Hand-keyed amendments keep `feed_key = NULL`, and NULLs stay distinct under a
unique index, so nothing already in the table is affected.
"""
from __future__ import annotations

import hashlib
from decimal import Decimal
from typing import Any

ZERO = Decimal('0.00')


def make_feed_key(marker: str, period_label: str, company_id, employee_id,
                  component_code: str = '', discriminator: str = '') -> str:
    """Deterministic fingerprint for ONE auto-fed payroll amendment.

    Identity = which feed (`marker`), which month, which entity, which person,
    which payslip line — plus an optional discriminator for a feed that can
    legitimately raise more than one row per person per month. Re-running the
    feed recomputes the identical key, so the row is UPDATED; a second,
    independent attempt to create the same row is refused by the unique index.
    """
    parts = [marker, period_label or '', str(company_id or ''),
             str(employee_id or ''), (component_code or '').upper(),
             discriminator or '']
    return hashlib.sha256('|'.join(parts).encode('utf-8')).hexdigest()


def target_period(period_label: str):
    from payroll.models import PayrollPeriod
    return PayrollPeriod.objects.filter(period_name=period_label).first()


def baseline_for(target):
    """The month whose payslips are the starting point — the latest period
    strictly before the target. None on the very first period."""
    from payroll.models import PayrollPeriod
    return (PayrollPeriod.objects.filter(start_date__lt=target.start_date)
            .order_by('-start_date', '-created_at').first())


def canonical_batch(*, target, baseline, company, user, marker: str, notes: str,
                    current_month_only: bool = False):
    """One PARSED auto-batch per (target period, entity) per feed marker.

    The closed-period backstop lives HERE and not in each feed: every automatic
    amendment hangs off a batch, and every batch is built through this call, so
    a service, an API, a retry and a management command all meet the same
    refusal. Raises `period_guard.PeriodClosed` — feeds catch it and return the
    sentence rather than letting it escape into an approval path.
    """
    from payroll import period_guard
    from payroll.models import PayrollAmendmentBatch

    period_guard.ensure_writable(target, source=marker, user=user,
                                 current_month_only=current_month_only)
    batch = (PayrollAmendmentBatch.objects.select_for_update()
             .filter(target_period=target, company=company, file_name=marker,
                     status=PayrollAmendmentBatch.Status.PARSED)
             .first())
    if batch is None:
        batch = PayrollAmendmentBatch.objects.create(
            target_period=target, baseline_period=baseline, company=company,
            file_name=marker, status=PayrollAmendmentBatch.Status.PARSED,
            notes=notes, uploaded_by=user)
    return batch


def upsert_amendment(*, batch, employee, kind, component, amount: Decimal,
                     reason: str, approver: str, marker: str, period_label: str,
                     discriminator: str = '') -> Any:
    """Create or refresh the one auto-fed row for this person + line + month.

    Matched on `feed_key` — the same column the database holds unique — so the
    lookup and the guard are the SAME fact. (Matching on the field tuple while
    guarding on a different column is how the two drift apart.)
    """
    from payroll.models import PayrollAmendment
    key = make_feed_key(marker, period_label, batch.company_id, employee.pk,
                        getattr(component, 'code', ''), discriminator)
    amd, created = PayrollAmendment.objects.update_or_create(
        feed_key=key,
        defaults={'batch': batch, 'employee': employee, 'kind': kind,
                  'component': component, 'amount': amount,
                  'employee_ref': employee.employee_number or employee.full_name,
                  'reason': reason, 'approver': approver,
                  'resolution_error': ''},
    )
    return amd, created


def record_skip(*, batch, name: str, reason: str, marker: str, period_label: str,
                discriminator: str):
    """Never-silent: a zero-amount row on the batch naming what was not fed."""
    from payroll.models import PayrollAmendment
    key = make_feed_key(marker + ':SKIP', period_label, batch.company_id, None,
                        '', discriminator)
    PayrollAmendment.objects.update_or_create(
        feed_key=key,
        defaults={'batch': batch, 'employee': None,
                  'kind': PayrollAmendment.Kind.OTHER, 'component': None,
                  'employee_ref': (name or '')[:120], 'amount': ZERO,
                  'reason': reason[:300], 'resolution_error': reason[:300]},
    )


def close_batch(batch):
    batch.row_count = batch.amendments.count()
    batch.save(update_fields=['row_count', 'updated_at'])
