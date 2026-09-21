"""
hris/leave_pay_feed.py — Leave Pay Feed (Build Spec B10, requested by Unopa
Male). Marker `AUTO-LEAVEPAY`.

A TWIN of `hris/incentive_payroll_feed.py` and `commissions/payroll_feed.py`,
not a second mechanism: one `PayrollAmendmentBatch` per (target period,
company), created PARSED and never applied, sharing `payroll.feed_common`.

What flows: a fully-approved `LeaveEncashment` — both an encashment by a
serving employee and a leaver's final leave pay (`kind=SETTLEMENT`) — becomes
ONE taxable LEAVE_PAY earning on that person's payslip for the month the
Finance leg was signed (Botswana time).

Two decisions worth stating plainly:

* **The GROSS goes to payroll, not the net.** The settled PAYE rule (CFO
  2026-08-17) is the employee's own marginal rate off the SAME bracket table
  payroll uses. Pushing the gross as a taxable earning lets that one engine
  withhold once; pushing the net would tax an already-taxed figure a second
  time. The snapshot PAYE is carried in the amendment's reason for audit, not
  re-applied. The encashment PAYE rules are settled — nothing here re-derives
  them.
* **`payroll.eligibility.is_active_for_period` decides who is in the run.**
  Someone no longer on the payroll for the target month is skipped and
  RECORDED, never silently paid through a month they do not belong to.

Two go-live controls, CFO 13 September 2026 — both about not paying somebody
twice:

* **The go-live cut-off.** Approved encashments were settled DIRECTLY by
  Finance before this feed existed; the money has already left the bank. Every
  encashment whose Finance approval fell on or before
  `payroll.config` key **`leave_pay.golive_date`** is therefore never fed, and
  the run SAYS how many it left out for that reason — a cut-off nobody can see
  working is indistinguishable from a feed that is quietly broken.
* **One payroll line per encashment, whoever raised it.** Held by
  `hris.LeaveEncashmentPayrollLine`, whose `encashment` column is UNIQUE at the
  database level. A hand-keyed amendment and a feed run cannot both carry the
  same encashment, and the second writer is refused by Postgres rather than by
  an `if`.

No-double-pay for the feed's own re-runs is enforced by the UNIQUE
`PayrollAmendment.feed_key` column — a database index, not a Python check.
"""
from __future__ import annotations

from decimal import Decimal
from typing import Any

from django.db import transaction
from django.utils import timezone

from payroll import config as payroll_config
from payroll import feed_common

LEAVE_PAY_COMPONENT_CODE = 'LEAVE_PAY'
AUTO_BATCH_MARKER = 'AUTO-LEAVEPAY'

#: The named, Finance-editable go-live cut-off (payroll.config / PayrollSetting).
GOLIVE_SETTING_KEY = 'leave_pay.golive_date'

BATCH_NOTES = ('Auto-generated from approved leave encashments / leaver final '
               'leave pay (Build Spec B10). Pending — reviewed and applied at '
               'the monthly payroll close. Omni never moves money.')


def _leave_pay_component():
    from payroll.models import PayslipComponent
    comp, _ = PayslipComponent.objects.get_or_create(
        code=LEAVE_PAY_COMPONENT_CODE,
        defaults={'name': 'Leave Pay', 'kind': PayslipComponent.Kind.EARNING,
                  'is_taxable': True, 'sort_order': 62},
    )
    return comp


def _local_date(dt):
    """Botswana local date of an aware timestamp — never a bare date.today()."""
    if dt is None:
        return None
    return timezone.localtime(dt).date() if timezone.is_aware(dt) else dt.date()


def _existing_line_key(app):
    """The feed_key of the payroll line that ALREADY carries this encashment,
    or None when nothing does.

    Reads the unique claim row first (`LeaveEncashmentPayrollLine`), falling
    back to the older `LeaveEncashment.payroll_amendment` link so an encashment
    fed before the claim table existed is still recognised. `''` (empty string)
    means "carried by a hand-keyed amendment", which is not None and therefore
    never matches this feed's key — exactly the refusal wanted.
    """
    claim = getattr(app, 'payroll_line_claim', None)
    amd = claim.amendment if claim is not None else None
    if amd is None and app.payroll_amendment_id:
        amd = app.payroll_amendment
    if amd is None:
        return None
    return amd.feed_key or ''


def claim_payroll_line(encashment, amendment, *, source='hand'):
    """Register that `amendment` carries `encashment` — the hand-keyed route.

    Raises `django.db.IntegrityError` when a line already exists for that
    encashment. The refusal is the database's: the column is unique.
    """
    from hris.leave_encash_models import LeaveEncashmentPayrollLine
    return LeaveEncashmentPayrollLine.objects.create(
        encashment=encashment, amendment=amendment, source=source)


def feed_period(*, period_label: str, user=None, company=None) -> dict[str, Any]:
    """Push approved leave pay for one month into pending payroll batches (one
    per entity). Idempotent. Never raises into an approval path."""
    from payroll.models import PayrollAmendment, PayrollAmendmentBatch
    from payroll.eligibility import is_active_for_period, exclusion_reason
    from hris.leave_encash_models import (LeaveEncashment,
                                          LeaveEncashmentPayrollLine)

    target = feed_common.target_period(period_label)
    if target is None:
        return {'status': 'no_period', 'pushed': 0, 'skipped': 0}
    baseline = feed_common.baseline_for(target)
    if baseline is None:
        return {'status': 'no_baseline', 'pushed': 0, 'skipped': 0}

    # Candidates: everything Finance signed off — APPROVED, and PAID whether
    # this feed paid it or Finance settled it directly. An encashment settled
    # OUTSIDE payroll is still never fed; that guard now sits in the loop
    # below (and says so) rather than in this filter.
    apps = (LeaveEncashment.objects
            .select_related('employee', 'company', 'payroll_amendment',
                            'payroll_line_claim__amendment')
            # Everything Finance signed off, INCLUDING the ones already marked
            # paid that no payroll amendment carries. Those are exactly the
            # population the go-live cut-off exists for — settled directly,
            # outside payroll — so they must REACH the loop to be counted.
            # Excluding them in SQL (as this filter used to) made the cut-off
            # report nothing held back on the very rows it was written to hold
            # back. They are still never fed: the guard that used to live in
            # this filter now sits below the cut-off, as a recorded skip.
            .filter(status__in=[LeaveEncashment.Status.APPROVED,
                                LeaveEncashment.Status.PAID])
            .exclude(payroll_amendment__batch__status=PayrollAmendmentBatch.Status.REJECTED))
    if company is not None:
        apps = apps.filter(employee__company=company)

    # The go-live cut-off, read from the named setting every run so Finance can
    # move it on screen without a deploy. None = no cut-off (see config.py).
    golive = payroll_config.get_date(GOLIVE_SETTING_KEY)

    per: dict[tuple, dict[str, Any]] = {}
    skipped: list[dict] = []
    pre_golive: list[dict] = []
    for app in apps:
        signed = _local_date(app.finance_approved_at)
        if signed is None or not (target.start_date <= signed <= target.end_date):
            continue                      # belongs to a different month
        emp = app.employee
        if golive is not None and signed <= golive:
            # Approved before the feed existed, so Finance settled it directly
            # and the person has already been paid. Never fed — and never
            # silently: it is counted, named on the batch, and returned.
            pre_golive.append({
                'id': str(app.pk),
                'name': getattr(emp, 'full_name', '?'),
                'reason': (f'approved {signed} — on or before the leave-pay '
                           f'go-live cut-off {golive}; settled directly by '
                           f'Finance, not payable again through payroll'),
            })
            continue
        if (app.status == LeaveEncashment.Status.PAID
                and app.payroll_amendment_id is None
                and getattr(app, 'payroll_line_claim', None) is None):
            # Marked paid and carried by no payroll line: Finance settled it
            # outside payroll. The same double-pay guard that has always been
            # here, now SAID out loud instead of filtered away in SQL.
            skipped.append({
                'id': str(app.pk), 'name': getattr(emp, 'full_name', '?'),
                'reason': ('already marked paid and settled outside payroll — '
                           'not payable again through a payslip')})
            continue
        if emp is None or emp.company_id is None:
            skipped.append({'id': str(app.pk), 'name': getattr(emp, 'full_name', '?'),
                            'reason': 'employee has no entity'})
            continue
        if app.amount is None or app.amount <= 0:
            skipped.append({'id': str(app.pk), 'name': emp.full_name,
                            'reason': 'nil or negative leave payout'})
            continue
        if not is_active_for_period(emp, target):
            skipped.append({'id': str(app.pk), 'name': emp.full_name,
                            'reason': (exclusion_reason(emp, target)
                                       or 'not on the payroll for this period')})
            continue
        # One payroll line per encashment, whoever raised it. An encashment
        # already carried by an amendment that is NOT this feed's own row for
        # this month was raised elsewhere — hand-keyed, or by an earlier run
        # against a different period — and must not be raised again.
        expected_key = feed_common.make_feed_key(
            AUTO_BATCH_MARKER, period_label, emp.company_id, emp.pk,
            LEAVE_PAY_COMPONENT_CODE)
        held_by = _existing_line_key(app)
        if held_by is not None and held_by != expected_key:
            skipped.append({'id': str(app.pk), 'name': emp.full_name,
                            'reason': 'a payroll line already exists for this '
                                      'encashment — not raised a second time'})
            continue
        key = (emp.company_id, emp.pk)
        slot = per.setdefault(key, {'employee': emp, 'total': Decimal('0.00'),
                                    'tax': Decimal('0.00'), 'apps': []})
        slot['total'] += app.amount
        slot['tax'] += (app.tax_amount or Decimal('0.00'))
        slot['apps'].append(app)

    if not per:
        return {'status': 'nothing_to_push', 'pushed': 0, 'skipped': len(skipped),
                'skipped_detail': skipped, 'period': period_label,
                'skipped_pre_golive': len(pre_golive),
                'pre_golive_detail': pre_golive,
                'golive_cutoff': golive.isoformat() if golive else None}

    comp = _leave_pay_component()
    pushed = 0
    batches: dict[Any, Any] = {}
    with transaction.atomic():
        for (company_id, _emp_id), slot in per.items():
            emp = slot['employee']
            batch = batches.get(company_id)
            if batch is None:
                batch = feed_common.canonical_batch(
                    target=target, baseline=baseline, company=emp.company,
                    user=user, marker=AUTO_BATCH_MARKER, notes=BATCH_NOTES)
                batches[company_id] = batch
            reason = (f'Approved leave pay {period_label} (auto) — gross; PAYE '
                      f'snapshot {slot["tax"]} is audit only, payroll withholds '
                      f'at the marginal rate')
            amd, _created = feed_common.upsert_amendment(
                batch=batch, employee=emp,
                kind=PayrollAmendment.Kind.ALLOWANCE_ADD, component=comp,
                amount=slot['total'], reason=reason,
                approver='Auto (approved leave encashment)',
                marker=AUTO_BATCH_MARKER, period_label=period_label)
            for app in slot['apps']:
                if app.payroll_amendment_id != amd.pk:
                    app.payroll_amendment = amd
                    app.save(update_fields=['payroll_amendment', 'updated_at'])
                # The unique claim row. update_or_create keyed on the UNIQUE
                # column, so a re-run refreshes the feed's own row and a second
                # writer is refused by the database, not by a check above.
                LeaveEncashmentPayrollLine.objects.update_or_create(
                    encashment=app,
                    defaults={'amendment': amd,
                              'source': LeaveEncashmentPayrollLine.Source.FEED})
            pushed += 1
        for batch in batches.values():
            for sk in skipped + pre_golive:
                feed_common.record_skip(
                    batch=batch, name=sk['name'],
                    reason=f'Leave pay not pushed: {sk["reason"]}',
                    marker=AUTO_BATCH_MARKER, period_label=period_label,
                    discriminator=sk['id'])
            feed_common.close_batch(batch)

    return {'status': 'pushed', 'pushed': pushed, 'skipped': len(skipped),
            'skipped_detail': skipped, 'period': period_label,
            'skipped_pre_golive': len(pre_golive),
            'pre_golive_detail': pre_golive,
            'golive_cutoff': golive.isoformat() if golive else None,
            'batch_ids': [str(b.pk) for b in batches.values()]}
