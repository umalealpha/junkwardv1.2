"""
payroll/backfill_service.py — how Finance releases a month the automatic feed
is no longer allowed to touch.

CFO, payroll.docx §5 (16-Sep-2026): *"production has 30 approved incentive lines
totalling BWP 71,000.00 and 56 approved commissions totalling BWP 609,141.08
across June–September. Reconcile these against amounts already paid before any
backfill. Never automatically feed the backlog… Provide a Finance-controlled
reconciliation/backfill process with preview, duplicate detection, period
selection, approval and audit trail."*

`payroll.period_guard` is the half that STOPS it: the feeds may only write into
the current month, so those June–August rows can no longer reach payroll by
themselves. This file is the half that lets a person release them on purpose.

WHERE THE MONEY GOES, and why it is not the original month. June's payroll has
been calculated, approved, posted and paid. You cannot pay June again. So a
released line is raised against the CURRENT month, on its normal payslip line,
carrying a reason that names the month it came from — which is also what the
payslip needs to say, because that is the month the person will ask about.

DUPLICATE DETECTION is the whole point of the preview, and it is read off the
source record rather than inferred from amounts:

  * **already fed** — the incentive line or commission submission already
    points at a payroll amendment. It has been through payroll once.
  * **already settled by hand** — Finance keyed it themselves
    (`IncentiveRequest.payroll_processed`, or a commission at status PAID).

A row carrying either flag is shown, greyed, with the reason — and `release()`
REFUSES it even if it is asked for. A preview that only warns is a preview
somebody clicks past at 17:00 on the 20th.

Omni never moves money. A release raises a payroll AMENDMENT in a PARSED batch;
Finance still applies the batch, sign-off still approves it, and the CFO still
authorises the payment in the bank.
"""
from __future__ import annotations

from decimal import Decimal
from typing import Any

from django.db import transaction
from django.db.models import Q, Sum

from payroll import feed_common, period_guard

ZERO = Decimal('0.00')

INCENTIVE = 'incentive'
COMMISSION = 'commission'
KINDS = (INCENTIVE, COMMISSION)

#: Marker for the batch a RELEASED backlog line lands in. Deliberately its own
#: batch, not the automatic AUTO-INCENTIVE / AUTO-COMMISSION one: Finance must
#: be able to see at the close exactly which lines came out of the backlog and
#: which are this month's ordinary work.
BATCH_MARKER = 'BACKLOG-RELEASE'
BATCH_NOTES = ('Backlog released by Finance from an earlier month '
               '(CFO payroll.docx §5, 16-Sep-2026). Pending — reviewed and '
               'applied at the monthly payroll close.')


class ReleaseRefused(Exception):
    """The release may not go ahead. Carries a sentence a person can act on."""


# ---------------------------------------------------------------------------
# Preview
# ---------------------------------------------------------------------------

def _incentive_rows(source_period: str) -> list[dict[str, Any]]:
    from hris.incentive_models import IncentiveLine, IncentiveRequest

    lines = (IncentiveLine.objects
             .select_related('employee', 'employee__company', 'request')
             .filter(request__status=IncentiveRequest.Status.APPROVED,
                     request__period=source_period))
    per: dict[Any, dict[str, Any]] = {}
    for ln in lines:
        emp = ln.employee
        if emp is None:
            continue
        slot = per.setdefault(emp.pk, {
            'employee_id': str(emp.pk),
            '_employee_pk': emp.pk,
            'employee_name': emp.full_name,
            'employee_number': emp.employee_number or '',
            'company': getattr(emp.company, 'code', '') or '',
            'amount': ZERO,
            'already_fed': False,
            'already_settled': False,
            'source_count': 0,
        })
        slot['amount'] += ln.amount or ZERO
        slot['source_count'] += 1
        if ln.payroll_amendment_id:
            slot['already_fed'] = True
        if ln.request.payroll_processed:
            slot['already_settled'] = True
    return list(per.values())


def _commission_rows(source_period: str) -> list[dict[str, Any]]:
    from commissions.models import CommissionGroup, CommissionSubmission

    # APPROVED, already paid, or already fed. NOT `.exclude(REJECTED)`, which
    # let DRAFT / SUBMITTED / SECOND_REVIEW / FINAL_REVIEW through and offered
    # a commission nobody had approved yet as "still owed" (Fable, F3). The
    # feed itself only ever takes APPROVED; the release must not be looser than
    # the automatic path it stands in for.
    subs = (CommissionSubmission.objects
            .select_related('agent', 'group')
            .filter(Q(status=CommissionSubmission.Status.APPROVED)
                    | Q(status=CommissionSubmission.Status.PAID)
                    | Q(payroll_amendment__isnull=False),
                    period_label=source_period,
                    group__pays_via=CommissionGroup.PaysVia.PAYROLL))

    from commissions.payroll_feed import _employee_for_agent

    per: dict[Any, dict[str, Any]] = {}
    for sub in subs:
        emp = _employee_for_agent(sub.agent)
        if emp is None:
            continue
        slot = per.setdefault(emp.pk, {
            'employee_id': str(emp.pk),
            '_employee_pk': emp.pk,
            'employee_name': emp.full_name,
            'employee_number': emp.employee_number or '',
            'company': getattr(emp.company, 'code', '') or '',
            'amount': ZERO,
            'already_fed': False,
            'already_settled': False,
            'source_count': 0,
        })
        slot['amount'] += sub.net_payable or ZERO
        slot['source_count'] += 1
        if sub.payroll_amendment_id:
            slot['already_fed'] = True
        if sub.status == CommissionSubmission.Status.PAID:
            slot['already_settled'] = True
    return list(per.values())


#: The payslip line each kind lands on in the ORIGINAL month. Used to read what
#: the source month actually paid, which is the CFO's own wording: "Reconcile
#: these against amounts already paid before any backfill." A link flag only
#: knows about money this system fed; a line Finance keyed into the amendment
#: spreadsheet by hand carries no flag at all, and without this check it would
#: read as "still owed" and be paid a second time.
PAID_COMPONENT = {INCENTIVE: 'INCENTIVE', COMMISSION: 'COMMISSION'}


def _paid_in_source_month(source_period: str, kind: str) -> dict:
    """{employee_id: amount} already on a payslip for that month and line."""
    from payroll.models import PayslipLine

    rows = (PayslipLine.objects
            .filter(payslip__period__period_name=source_period,
                    component__code=PAID_COMPONENT[kind])
            .values_list('payslip__employee_id', 'amount'))
    out: dict[Any, Decimal] = {}
    for emp_id, amount in rows:
        out[emp_id] = out.get(emp_id, ZERO) + (amount or ZERO)
    return out


def _duplicate_reason(row: dict[str, Any]) -> str:
    if row['already_fed'] and row['already_settled']:
        return ('already went through payroll AND is marked settled by hand — '
                'releasing it again would pay it a third time')
    if row['already_fed']:
        return 'already went through payroll once — releasing it would pay it twice'
    if row['already_settled']:
        return 'Finance already settled this by hand outside the feed'
    paid = row.get('paid_in_source_month') or ZERO
    if paid > ZERO:
        return (f'BWP {paid} was already paid on the {row["source_period"]} '
                'payslip for this line — releasing it would pay it twice')
    return ''


def preview(*, source_period: str, kind: str) -> dict[str, Any]:
    """Everything approved in `source_period` that Finance could release.

    Read-only. Writes nothing, so it is safe to open as often as anyone likes.
    """
    if kind not in KINDS:
        raise ReleaseRefused(f'Unknown kind {kind!r} — expected one of {KINDS}.')

    rows = (_incentive_rows if kind == INCENTIVE else _commission_rows)(source_period)
    paid = _paid_in_source_month(source_period, kind)
    for row in rows:
        row['source_period'] = source_period
        row['paid_in_source_month'] = paid.get(row['_employee_pk'], ZERO)
        row['duplicate_reason'] = _duplicate_reason(row)
        row['releasable'] = not row['duplicate_reason']
        row['amount'] = str(row['amount'])
        row['paid_in_source_month'] = str(row['paid_in_source_month'])
        row.pop('_employee_pk', None)

    target_label = period_guard.current_period_label()
    target = feed_common.target_period(target_label)
    blocked = ''
    if target is None:
        blocked = (f'There is no payroll period for {target_label} yet, so there '
                   'is nowhere to release these into. Open the month first.')
    else:
        blocked = period_guard.refusal(target, source='Backlog release')

    releasable = [r for r in rows if r['releasable']]
    return {
        'source_period': source_period,
        'kind': kind,
        'target_period': target_label,
        'rows': sorted(rows, key=lambda r: r['employee_name']),
        'releasable_count': len(releasable),
        'releasable_total': str(sum((Decimal(r['amount']) for r in releasable), ZERO)),
        'held_count': len(rows) - len(releasable),
        'blocked': blocked,
    }


# ---------------------------------------------------------------------------
# Release
# ---------------------------------------------------------------------------

#: A released line lands on its OWN payslip component, not the one this month's
#: ordinary feed uses.
#:
#: WHY, and it is the sharpest thing in this file (Fable, F2): applying a batch
#: is `PayslipLine.update_or_create(payslip, component, defaults={'amount'})` —
#: LAST WINS per component. Alice with September's own incentive of 100 on
#: INCENTIVE, and June released as 250 on INCENTIVE, would get 100 OR 250 on her
#: payslip depending on which batch Finance applied last. Never 350. Silently.
#: Its own component means both survive, and the payslip also READS correctly:
#: "Incentive 100, Incentive arrears 250" is what the person will ask about.
ARREARS_COMPONENT = {
    INCENTIVE:  ('INCENTIVE_ARREARS',  'Incentive arrears',  62),
    COMMISSION: ('COMMISSION_ARREARS', 'Commission arrears', 63),
}


def _component(kind: str):
    from payroll.models import PayslipComponent
    code, name, order = ARREARS_COMPONENT[kind]
    comp, _ = PayslipComponent.objects.get_or_create(
        code=code,
        defaults={'name': name, 'kind': PayslipComponent.Kind.EARNING,
                  'is_taxable': True, 'sort_order': order},
    )
    return comp


def _link_and_total(*, kind: str, amendment, employee, source_period: str):
    """Mark the released source rows as fed, then total EVERYTHING fed by it.

    Two jobs, and both are corrections Fable caught (F1, F2):

    * The release used to raise the amendment and stop, never writing
      `payroll_amendment` back on the source row — the very flag the preview
      reads for "already fed". June would still have shown as "still owed"
      afterwards, and in October it would have been releasable again under a
      new fingerprint. Paid twice.
    * There is ONE release amendment per person per target month, so releasing
      June and then July has to SUM. The amount is therefore recomputed from
      every source row linked to this amendment, exactly the way the ordinary
      feeds recompute a whole month, rather than being overwritten by whichever
      release ran last.
    """
    if kind == INCENTIVE:
        from hris.incentive_models import IncentiveLine, IncentiveRequest
        (IncentiveLine.objects
         .filter(request__period=source_period,
                 request__status=IncentiveRequest.Status.APPROVED,
                 employee=employee, payroll_amendment__isnull=True)
         .update(payroll_amendment=amendment))
        total = (IncentiveLine.objects.filter(payroll_amendment=amendment)
                 .aggregate(t=Sum('amount'))['t'])
    else:
        from commissions.models import CommissionSubmission
        # Matched to the person the same way the commission feed does it —
        # agent email, exact, never by name (the name-match scar). No email
        # means no safe match, so nothing is linked rather than guessed.
        email = (employee.email or '').strip()
        if email:
            (CommissionSubmission.objects
             .filter(period_label=source_period,
                     status=CommissionSubmission.Status.APPROVED,
                     payroll_amendment__isnull=True,
                     agent__email__iexact=email)
             .update(payroll_amendment=amendment))
        total = (CommissionSubmission.objects.filter(payroll_amendment=amendment)
                 .aggregate(t=Sum('net_payable'))['t'])
    return total or ZERO


def release(*, source_period: str, kind: str, employee_ids: list[str],
            user) -> dict[str, Any]:
    """Raise this month's payroll amendments for the chosen people.

    `employee_ids` is an explicit list — there is no "release everything"
    shortcut, because the whole control is that a person looked at each line.
    """
    from core.models import AuditLog
    from payroll.models import Employee, PayrollAmendment

    if kind not in KINDS:
        raise ReleaseRefused(f'Unknown kind {kind!r} — expected one of {KINDS}.')
    wanted = {str(e) for e in (employee_ids or [])}
    if not wanted:
        raise ReleaseRefused('Nobody was selected, so there is nothing to release.')

    shot = preview(source_period=source_period, kind=kind)
    if shot['blocked']:
        raise ReleaseRefused(shot['blocked'])

    by_id = {r['employee_id']: r for r in shot['rows']}
    unknown = sorted(wanted - set(by_id))
    if unknown:
        raise ReleaseRefused(
            f'{len(unknown)} of the people selected have nothing approved in '
            f'{source_period}. Reload the preview and try again.')
    held = sorted(by_id[i]['employee_name'] for i in wanted
                  if not by_id[i]['releasable'])
    if held:
        raise ReleaseRefused(
            'Refused — these have already been through payroll or were settled '
            'by hand, so releasing them would pay twice: ' + ', '.join(held))

    target_label = shot['target_period']
    target = feed_common.target_period(target_label)
    baseline = feed_common.baseline_for(target)
    component = _component(kind)
    marker = f'{BATCH_MARKER}:{kind.upper()}'

    released: list[dict[str, Any]] = []
    total = ZERO
    with transaction.atomic():
        batches: dict[Any, Any] = {}
        for emp_id in sorted(wanted):
            row = by_id[emp_id]
            employee = Employee.objects.select_related('company').get(pk=emp_id)
            company = employee.company
            if company is None:
                raise ReleaseRefused(
                    f'{employee.full_name} is not attached to an entity, so the '
                    'release has no company to raise the amendment against.')
            batch = batches.get(company.pk)
            if batch is None:
                # The current month, and only while it is OPEN — the same guard
                # the automatic feeds meet. `current_month_only` is off because
                # the TARGET is already this month; what is back-dated here is
                # the SOURCE, which is the whole point of the screen.
                batch = feed_common.canonical_batch(
                    target=target, baseline=baseline, company=company, user=user,
                    marker=marker, notes=BATCH_NOTES)
                batches[company.pk] = batch
            # ONE amendment per person per target month — discriminator '',
            # not the source month. Two released months on one component in one
            # batch would silently overwrite each other at apply time; they have
            # to be a single summed row, the same way the ordinary feeds sum a
            # whole month into one line.
            amd, _created = feed_common.upsert_amendment(
                batch=batch, employee=employee,
                kind=PayrollAmendment.Kind.ALLOWANCE_ADD, component=component,
                amount=Decimal(row['amount']),
                reason=(f'{kind.title()} approved in {source_period}, released '
                        f'into {target_label} by Finance'),
                approver=(getattr(user, 'get_full_name', lambda: '')()
                          or getattr(user, 'username', '') or 'Finance'),
                marker=marker, period_label=target_label)
            # Write the "fed" flag back on the source rows, then set the amount
            # from everything this amendment now carries.
            amount = _link_and_total(kind=kind, amendment=amd,
                                     employee=employee,
                                     source_period=source_period)
            if amount != amd.amount:
                amd.amount = amount
                amd.save(update_fields=['amount', 'updated_at'])
            total += amount
            released.append({'employee': employee.full_name,
                             'amount': str(amount)})

        for batch in batches.values():
            feed_common.close_batch(batch)

        AuditLog.objects.create(
            table_name='payroll_payrollamendment',
            record_id=f'{marker}:{source_period}',
            action=AuditLog.Action.APPROVE,
            user=user if getattr(user, 'pk', None) else None,
            description=(f'Backlog released: {len(released)} {kind} line(s) from '
                         f'{source_period} into {target_label}, BWP {total}'),
            new_values={'source_period': source_period, 'kind': kind,
                        'target_period': target_label,
                        'released': released, 'total': str(total)},
        )

    return {'status': 'released', 'source_period': source_period, 'kind': kind,
            'target_period': target_label, 'released': released,
            'count': len(released), 'total': str(total)}
