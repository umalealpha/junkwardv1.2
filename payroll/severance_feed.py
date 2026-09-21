"""
payroll/severance_feed.py — Severance Connect (Build Spec B11, requested by
Unopa Male). Marker `AUTO-SEVERANCE`.

The same batch mechanism as B10 and the two originals — one
`PayrollAmendmentBatch` per (target period, company), created PARSED, never
applied — with its own marker.

What it connects. `payroll.archive_service.terminate_employee` already sets
BOTH `status` and `termination_date`; this feed does not write a second
termination path and never terminates anybody. It reads the terminations that
already happened and puts them in front of the payroll close as amendment rows,
so a leaver is dealt with deliberately instead of rolling forward by accident.

**The rule that matters (CFO decision 12 September 2026, already enforced in
`payroll/eligibility.py`): a leaver terminated on DAY 1 of a period gets NO
automatic full month.** This feed honours that rather than re-deciding it — it
asks `is_active_for_period`, and:

  * terminated STRICTLY AFTER the period starts → they are still on this
    month's payroll, so a note-only row is raised naming the leaving date, and
    the close handles their final payslip. It is deliberately NOT a TERMINATE
    row: the apply engine cancels the target payslip for those, which would
    cancel the final pay this feed exists to protect;
  * terminated ON day 1 (or earlier) → NO amendment that could pay them. A
    zero-amount row is written instead, naming the person and saying in plain
    words that Finance must raise a settlement amendment for what is actually
    owed. Never silent, and never an automatic month.

No money is computed here. A severance/terminal-benefit figure is a Conditions
of Service calculation with its own tax treatment; inventing a formula in a
feed would be worse than leaving Finance to raise it. This item connects the
termination to payroll — it does not price it.

No-double is enforced by the UNIQUE `PayrollAmendment.feed_key` column.
"""
from __future__ import annotations

from decimal import Decimal
from typing import Any

from django.db import transaction

from payroll import feed_common

AUTO_BATCH_MARKER = 'AUTO-SEVERANCE'

BATCH_NOTES = ('Auto-generated from terminations dated in this period (Build '
               'Spec B11). Pending — reviewed and applied at the monthly '
               'payroll close. No severance amount is computed here; Omni '
               'never moves money.')

DAY_ONE_NOTE = ('terminated on day 1 of this period — NO automatic full month '
                '(CFO 12-Sep-2026). Finance must raise a settlement amendment '
                'for what is actually owed.')


def feed_period(*, period_label: str, user=None, company=None) -> dict[str, Any]:
    """Put every termination dated inside `period_label` in front of the close."""
    from payroll.models import Employee, PayrollAmendment
    from payroll.eligibility import is_active_for_period

    target = feed_common.target_period(period_label)
    if target is None:
        return {'status': 'no_period', 'pushed': 0, 'skipped': 0}
    baseline = feed_common.baseline_for(target)
    if baseline is None:
        return {'status': 'no_baseline', 'pushed': 0, 'skipped': 0}

    leavers = (Employee.objects
               .select_related('company')
               .filter(termination_date__gte=target.start_date,
                       termination_date__lte=target.end_date,
                       is_test_record=False)
               .exclude(company__isnull=True)
               .order_by('full_name'))
    if company is not None:
        leavers = leavers.filter(company=company)

    final_month: list = []
    day_one: list = []
    for emp in leavers:
        (final_month if is_active_for_period(emp, target) else day_one).append(emp)

    if not final_month and not day_one:
        return {'status': 'nothing_to_push', 'pushed': 0, 'skipped': 0,
                'period': period_label}

    pushed = 0
    batches: dict[Any, Any] = {}
    with transaction.atomic():
        def _batch_for(emp):
            b = batches.get(emp.company_id)
            if b is None:
                b = feed_common.canonical_batch(
                    target=target, baseline=baseline, company=emp.company,
                    user=user, marker=AUTO_BATCH_MARKER, notes=BATCH_NOTES)
                batches[emp.company_id] = b
            return b

        for emp in final_month:
            batch = _batch_for(emp)
            feed_common.upsert_amendment(
                batch=batch, employee=emp,
                # NOT Kind.TERMINATE. The apply engine's TERMINATE handler
                # CANCELS the target payslip — so a TERMINATE row here would
                # cancel the final pay of the very person this feed exists to
                # put in front of the close. Kind.OTHER with component=None is
                # a note-only row: it names the leaver on the batch and changes
                # no figure. (Fable, 13-Sep-2026.)
                kind=PayrollAmendment.Kind.OTHER, component=None,
                amount=Decimal('0.00'),
                reason=(f'Leaver — terminated {emp.termination_date}, final '
                        f'period {period_label}. Settlement amount is raised by '
                        f'Finance, not computed here.'),
                approver='Auto (termination on file)',
                marker=AUTO_BATCH_MARKER, period_label=period_label)
            pushed += 1

        for emp in day_one:
            batch = _batch_for(emp)
            # Deliberately NOT an employee-bearing amendment: nothing that could
            # be applied and pay a full month. A named, zero-amount notice.
            feed_common.record_skip(
                batch=batch, name=emp.full_name,
                reason=f'{emp.full_name} {DAY_ONE_NOTE}',
                marker=AUTO_BATCH_MARKER, period_label=period_label,
                discriminator=str(emp.pk))

        for batch in batches.values():
            feed_common.close_batch(batch)

    return {'status': 'pushed', 'pushed': pushed, 'skipped': len(day_one),
            'day_one_excluded': [e.full_name for e in day_one],
            'period': period_label,
            'batch_ids': [str(b.pk) for b in batches.values()]}
