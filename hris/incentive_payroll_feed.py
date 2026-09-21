"""
hris/incentive_payroll_feed.py — approved incentives flow into payroll by
themselves (CFO 2026-08-28: "let anything be pushed to payroll to make it easy;
I don't want my people to work hard in that area").

What it does, and the guardrails that keep it safe:

* When an incentive request is fully approved, every line is pushed into a
  single PENDING payroll amendment batch for that month + entity. Finance never
  re-keys it. The batch is created PARSED, **never applied** — the monthly
  payroll close still reviews and applies it, and dual sign-off still pays it.
  Omni never moves money; it only prepares the batch.
* **Real employee only.** A line is pushed using its resolved `employee`
  foreign key — never a name match — so it can never invent or mis-target a
  person (the name-mismatch scar). Lines with no employee, or an employee in a
  different entity, are skipped and reported, not guessed.
* **Idempotent + no double-count.** Each line links to the amendment it fed
  (`IncentiveLine.payroll_amendment`); a line already linked is never pushed
  again. Per employee there is exactly ONE amendment row per month whose amount
  is the SUM of that person's approved incentive lines — so two incentives for
  the same person in one month add up instead of overwriting each other (the
  apply engine does update_or_create per component).
* **Race-safe idempotency (CFO 16-Sep-2026).** The row is matched and written on
  `PayrollAmendment.feed_key`, which the DATABASE holds unique. A Python
  "does it already exist?" check is not a control — two approvals landing at the
  same moment, or a retry inside a race, both pass it. The unique index cannot
  be raced: the second write is refused by Postgres itself.
* **Current month only, and only while it is OPEN.** `payroll.period_guard`
  refuses a back-dated month (his words: "it only works for the month of
  september, no going backward") and refuses any month Finance has locked,
  approved, posted or paid. The June–August backlog is released by Finance on
  the payroll reconciliation screen, never by this feed.
"""
from __future__ import annotations

from decimal import Decimal
from typing import Any

from django.db import transaction

from payroll import feed_common, period_guard

INCENTIVE_COMPONENT_CODE = 'INCENTIVE'
AUTO_BATCH_MARKER = 'AUTO-INCENTIVE'


def _incentive_component():
    """The taxable earning line incentives land on. Created once, reused."""
    from payroll.models import PayslipComponent
    comp, _ = PayslipComponent.objects.get_or_create(
        code=INCENTIVE_COMPONENT_CODE,
        defaults={
            'name': 'Incentive',
            'kind': PayslipComponent.Kind.EARNING,   # taxable earning
            'is_taxable': True,
            'sort_order': 60,
        },
    )
    return comp


def _target_period(period_label: str):
    from payroll.models import PayrollPeriod
    return PayrollPeriod.objects.filter(period_name=period_label).first()


def _baseline_for(target):
    """The month whose payslips are the starting point — the latest period
    strictly before the target. None on the very first period."""
    from payroll.models import PayrollPeriod
    return (PayrollPeriod.objects.filter(start_date__lt=target.start_date)
            .order_by('-start_date', '-created_at').first())


BATCH_NOTES = ('Auto-generated from approved incentives (CFO 2026-08-28). '
               'Pending — reviewed and applied at the monthly payroll close.')


def feed_period_company(*, period_label: str, company, user=None) -> dict[str, Any]:
    """Push all APPROVED incentive lines for one month + entity into the pending
    payroll batch. Idempotent: safe to re-run; only adds/updates, never doubles.
    Returns a plain-English status dict — never raises into the approval path."""
    from payroll.models import Employee, PayrollAmendment
    from hris.incentive_models import IncentiveLine, IncentiveRequest

    if company is None:
        return {'status': 'no_company', 'detail': 'The request has no entity set.',
                'pushed': 0, 'skipped': 0}
    target = _target_period(period_label)
    if target is None:
        return {'status': 'no_period', 'detail': f'No payroll period {period_label} yet.',
                'pushed': 0, 'skipped': 0}
    # Ask the period guard BEFORE anything else about the month. Checking the
    # baseline first would report a back-dated June feed as "no baseline" —
    # true, but not the reason it was stopped, and the wrong sentence is what
    # sends someone hunting in the wrong place.
    closed = period_guard.refusal(target, source=AUTO_BATCH_MARKER,
                                  current_month_only=True)
    if closed:
        period_guard.record_refusal(target, source=AUTO_BATCH_MARKER,
                                    reason=closed, user=user)
        return {'status': 'period_closed', 'detail': closed,
                'pushed': 0, 'skipped': 0, 'period': period_label}
    baseline = _baseline_for(target)
    if baseline is None:
        return {'status': 'no_baseline', 'detail': f'No period before {period_label}.',
                'pushed': 0, 'skipped': 0}

    # Pull approved lines for the month + entity. Exclude requests Finance
    # already MANUALLY keyed into payroll (payroll_processed AND never auto-fed)
    # — the double-pay guard — but KEEP a line that this feed itself already
    # pushed (payroll_amendment set), even if its request was later marked
    # processed, so recomputing the per-employee sum never drops it (Fable r2).
    from django.db.models import Q
    lines = (IncentiveLine.objects
             .select_related('employee', 'request')
             .filter(Q(request__payroll_processed=False) | Q(payroll_amendment__isnull=False),
                     request__status=IncentiveRequest.Status.APPROVED,
                     request__company=company,
                     request__period=period_label))

    # Group by resolved, same-entity employee. Skip (report) everything else.
    per_emp: dict[Any, dict[str, Any]] = {}
    skipped: list[dict] = []
    for ln in lines:
        emp = ln.employee
        if emp is None:
            skipped.append({'line_id': str(ln.pk), 'name': ln.name,
                            'reason': 'no employee linked'})
            continue
        if emp.company_id != company.pk:
            skipped.append({'line_id': str(ln.pk), 'name': ln.name,
                            'reason': 'employee belongs to a different entity'})
            continue
        slot = per_emp.setdefault(emp.pk, {'employee': emp, 'total': Decimal('0.00'),
                                           'lines': []})
        slot['total'] += ln.amount
        slot['lines'].append(ln)

    if not per_emp:
        return {'status': 'nothing_to_push', 'pushed': 0, 'skipped': len(skipped),
                'skipped_detail': skipped}

    comp = _incentive_component()
    pushed_emps = 0
    try:
        with transaction.atomic():
            batch = feed_common.canonical_batch(
                target=target, baseline=baseline, company=company, user=user,
                marker=AUTO_BATCH_MARKER, notes=BATCH_NOTES,
                current_month_only=True)
            for slot in per_emp.values():
                emp = slot['employee']
                amd, _created = feed_common.upsert_amendment(
                    batch=batch, employee=emp,
                    kind=PayrollAmendment.Kind.ALLOWANCE_ADD, component=comp,
                    amount=slot['total'],
                    reason=f'Approved incentives {period_label} (auto)',
                    approver='Auto (approved incentive)',
                    marker=AUTO_BATCH_MARKER, period_label=period_label,
                )
                # Link every contributing line to this amendment (idempotency guard).
                for ln in slot['lines']:
                    if ln.payroll_amendment_id != amd.pk:
                        ln.payroll_amendment = amd
                        ln.save(update_fields=['payroll_amendment', 'updated_at'])
                pushed_emps += 1
            # Never-silent: surface every skipped line as a zero-amount row carrying
            # a resolution_error, so an approved incentive that could not be pushed
            # is visible on the batch at the close instead of vanishing.
            for sk in skipped:
                feed_common.record_skip(
                    batch=batch, name=sk['name'],
                    reason=f'Incentive not pushed: {sk["reason"]}',
                    marker=AUTO_BATCH_MARKER, period_label=period_label,
                    discriminator=sk['line_id'])
            feed_common.close_batch(batch)
    except period_guard.PeriodClosed as closed:
        # The month is shut, or this is a back-dated backlog line. Never raise
        # into the approval path — report it so the approval still succeeds and
        # Finance picks the line up on the reconciliation screen.
        #
        # The audit row is written HERE, after the atomic block has rolled back.
        # Written inside the guard it would be rolled back with the batch and
        # the refusal would leave no trace at all.
        period_guard.record_refusal(target, source=AUTO_BATCH_MARKER,
                                    reason=closed.reason, user=user)
        return {'status': 'period_closed', 'detail': closed.reason,
                'pushed': 0, 'skipped': len(skipped), 'skipped_detail': skipped,
                'period': period_label}

    return {'status': 'pushed', 'pushed': pushed_emps, 'skipped': len(skipped),
            'skipped_detail': skipped, 'batch_id': str(batch.pk),
            'period': period_label, 'company': company.code}


def feed_for_request(req, user=None) -> dict[str, Any]:
    """Convenience: push everything for the request's month + entity (recomputes
    the whole month so the just-approved request is included). Called on approval."""
    return feed_period_company(period_label=req.period, company=req.company, user=user)
