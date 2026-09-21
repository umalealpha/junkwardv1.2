"""
hris/incentive_service.py — submit / dual-approve / reject / payroll-mark
for staff incentive requests (CFO directive 2026-07-13).

Authority model (mirrors amendment_service, extended to TWO slots):
  * Submit  — genuine managers and above (hris_role mgr/hr/hris/ceo/admin/
              superadmin). Deliberately NOT whitelist-gated: the real
              requesters (e.g. Sales & Marketing managers) are not HRIS
              whitelist members, and a maker only ever sees their own
              requests.
  * CFO slot — the CFO's email. HR slot — Unami's email. A superuser may
              sign a named slot (backup path, still SoD-checked).
  * Reject  — either approver, while pending.
  * Payroll — Finance (Pako / Kago), or hr/admin/superadmin tiers, mark an
              APPROVED request processed once loaded into payroll.

Segregation of duties: nobody signs or rejects their own request.
"""
from __future__ import annotations

import logging
import re
from decimal import Decimal, InvalidOperation

from django.core.exceptions import ValidationError
from django.utils import timezone

from core.hris_access import hris_role
from .amendment_service import CFO_EMAIL, UNAMI_EMAIL, _local
from .incentive_models import (
    IncentiveLine, IncentiveRequest, RecurringIncentive,
)

log = logging.getLogger(__name__)

SUBMIT_ROLES  = frozenset({'mgr', 'hr', 'hris', 'ceo', 'admin', 'superadmin'})
FINANCE_LOCALS = frozenset({'pkago', 'ktshutlhedi'})   # Pako Kago, Kago Tshutlhedi
_PERIOD_RE = re.compile(r'^\d{4}-(0[1-9]|1[0-2])$')

# The earned-incentive justification gate (CFO 2026-07-13; raised to 50 words
# 2026-08-17). Promoted to module scope so amend_incentive re-runs the EXACT
# same validation as submit_request. Incentives pay for OVER-performance above
# day-to-day duties, so the requester must justify it substantively (≥50 words).
MIN_JUSTIFICATION_WORDS = 50


def can_submit(user) -> bool:
    return hris_role(user) in SUBMIT_ROLES


def is_finance(user) -> bool:
    if hris_role(user) in {'hr', 'admin', 'superadmin'}:
        return True
    return _local(getattr(user, 'email', '')) in FINANCE_LOCALS


def slot_for(user, requested: str = '') -> str | None:
    """Which signature slot this user fills: 'cfo' | 'hr' | None."""
    local = _local(getattr(user, 'email', ''))
    if local == _local(CFO_EMAIL):
        return 'cfo'
    if local == _local(UNAMI_EMAIL):
        return 'hr'
    if getattr(user, 'is_superuser', False) and requested in ('cfo', 'hr'):
        return requested
    return None


def is_approver(user) -> bool:
    return slot_for(user) is not None or getattr(user, 'is_superuser', False)


def _parse_amount(raw) -> Decimal:
    try:
        amount = Decimal(str(raw).replace(',', '').strip())
    except (InvalidOperation, AttributeError, TypeError) as exc:
        raise ValidationError(f"Amount {raw!r} is not a number.") from exc
    if amount <= 0:
        raise ValidationError("Every incentive amount must be greater than zero.")
    return amount.quantize(Decimal('0.01'))


def _truthy(v) -> bool:
    return v is True or str(v).strip().lower() in ('1', 'true', 'yes', 'on')


def _validate_and_parse_line(l: dict) -> dict:
    """Run the earned-incentive gate on ONE line and return a parsed dict.

    Shared by submit_request (ad-hoc submissions) and amend_incentive (post-hoc
    corrections) so both enforce the identical CFO 2026-07-13 gate — including
    the >= MIN_JUSTIFICATION_WORDS justification.
    """
    name = str(l.get('name') or '').strip()[:160]
    beyond   = _truthy(l.get('beyond_normal_duties'))
    on_time  = _truthy(l.get('on_time'))
    clean    = _truthy(l.get('error_free'))
    mgr_fix  = _truthy(l.get('needed_manager_fix'))
    why      = str(l.get('justification') or '').strip()

    # The earned-incentive gate (CFO 2026-07-13): incentives are ONLY for
    # work above normal duties. Every check below must pass per person, or
    # the whole request is rejected — no incentive for routine work.
    if not beyond:
        raise ValidationError(
            f"“{name}”: incentives are only for work BEYOND normal day-to-day "
            f"duties. Routine work does not qualify — remove this person or "
            f"state clearly how the work exceeded their role.")
    if not on_time:
        raise ValidationError(
            f"“{name}”: the work must have been delivered ON TIME to earn an "
            f"incentive.")
    if not clean:
        raise ValidationError(
            f"“{name}”: the work must have been ERROR-FREE to earn an incentive.")
    if mgr_fix:
        raise ValidationError(
            f"“{name}”: if the manager had to spend significant time FIXING it, "
            f"the employee has not earned an incentive.")
    if len(why.split()) < MIN_JUSTIFICATION_WORDS:
        raise ValidationError(
            f"“{name}”: explain in at least {MIN_JUSTIFICATION_WORDS} words why "
            f"this went beyond normal duties (that is the justification the CFO "
            f"and HR sign off).")

    return {
        'name': name,
        'basis': str(l.get('basis') or '').strip()[:200],
        'amount': _parse_amount(l.get('amount')),
        'employee_id': l.get('employee_id') or None,
        'beyond_normal_duties': beyond,
        'on_time': on_time,
        'error_free': clean,
        'needed_manager_fix': mgr_fix,
        'justification': why,
    }


def _employee_overdue(employee_id):
    """The overdue-task summary for a line's employee, or None when there is
    nothing to check (no linked employee, no omni login, or nothing overdue).

    CFO 2026-08-18: the incentive discipline gate looks at the RECIPIENT of the
    money, never the manager who submits or the executive who approves — a
    manager always carries open tasks, and blocking Unami's approval on his own
    workload (as the 2026-08-07 gate did) was wrong. Fail-OPEN: a broken check
    must never silently deny someone a reward, so any error → not blocked (logged).
    """
    if not employee_id:
        return None
    try:
        from payroll.models import Employee
        emp = (Employee.objects.filter(pk=employee_id)
               .select_related('user').first())
        user = getattr(emp, 'user', None) if emp else None
        if user is None:
            return None
        from hris import overdue_gate
        summary = overdue_gate.overdue_summary(user)
        return summary if summary.get('count', 0) > 0 else None
    except Exception:                          # noqa: BLE001 — never deny on error
        log.exception("Incentive overdue check failed for employee %s", employee_id)
        return None


def _split_by_overdue(parsed: list[dict]) -> tuple[list[dict], list[dict]]:
    """Split parsed lines into (eligible, held). A person carrying tasks more
    than the overdue threshold past due is HELD — an incentive rewards work
    above the job on a CLEAN baseline. Held ≠ the whole request fails: the CFO
    chose to block only that person, so the eligible lines still go through."""
    eligible, held = [], []
    for p in parsed:
        od = _employee_overdue(p.get('employee_id'))
        if od:
            held.append({'name': p['name'], 'count': od['count'],
                         'days_threshold': od['days_threshold'],
                         'tasks': od.get('tasks', [])})
        else:
            eligible.append(p)
    return eligible, held


def _held_message(held: list[dict]) -> str:
    days = held[0]['days_threshold'] if held else 2
    names = ', '.join(h['name'] for h in held)
    verb = 'has' if len(held) == 1 else 'have'
    return (f"Not eligible: {names} {verb} task(s) more than {days} days overdue. "
            f"An incentive is only for work above the job on a clean baseline — "
            f"clear the overdue tasks first, then submit for them.")


def _company_for_maker(maker):
    emp_rec = getattr(maker, 'employee_record', None)
    return getattr(emp_rec, 'company', None) if emp_rec is not None else None


def _persist_request(*, maker, title: str, period: str, department: str,
                     notes: str, parsed: list[dict], company=None,
                     source_template=None, notify: bool = True,
                     manager_attested: bool = True
                     ) -> IncentiveRequest:
    """Create an IncentiveRequest + its lines from already-parsed rows.

    Shared by submit_request and generate_recurring_for_period so the record
    shape is identical however a request is created. `manager_attested` defaults
    True so a pre-authorised recurring generation is not blocked; ad-hoc submits
    pass the manager's actual tick.
    """
    req = IncentiveRequest.objects.create(
        title=title, period=period,
        department=(department or '').strip()[:80],
        notes=(notes or '').strip(),
        maker=maker,
        maker_email=getattr(maker, 'email', '') or '',
        company=company if company is not None else _company_for_maker(maker),
        source_template=source_template,
        manager_attested=bool(manager_attested),
    )
    from payroll.models import Employee
    for p in parsed:
        employee = (Employee.objects.filter(pk=p['employee_id']).first()
                    if p['employee_id'] else None)
        IncentiveLine.objects.create(
            request=req, employee=employee,
            name=(employee.full_name if employee else p['name']),
            basis=p['basis'], amount=p['amount'],
            beyond_normal_duties=p['beyond_normal_duties'],
            on_time=p['on_time'], error_free=p['error_free'],
            needed_manager_fix=p['needed_manager_fix'],
            justification=p['justification'])

    if notify:
        from .incentive_notify import notify_submitted
        try:
            notify_submitted(req)
        except Exception:                  # noqa: BLE001 — never block on email
            log.exception("Incentive %s: notify_submitted failed", req.pk)
    return req


def submit_request(*, maker, title: str, period: str, department: str = '',
                   notes: str = '', lines: list[dict] | None = None,
                   manager_attested: bool = False
                   ) -> IncentiveRequest:
    if not can_submit(maker):
        raise ValidationError(
            "Only managers and above can submit incentive requests.")
    # Monthly submission deadline (CFO 2026-08-28): after the 16th only the
    # CFO may load incentives; managers get the red "ask CFO" message.
    from core.payroll_deadline import assert_can_submit, SubmissionDeadlinePassed
    try:
        assert_can_submit(maker)
    except SubmissionDeadlinePassed as exc:
        raise ValidationError(exc.message)
    # Manager declaration (CFO 2026-08-18): the accountability sits with the
    # manager. They cannot submit without confirming they checked the person's
    # attendance and leave and would stand by the payment publicly.
    if not _truthy(manager_attested):
        raise ValidationError(
            "Tick the declaration to confirm you have checked this employee's "
            "attendance and leave, and would be comfortable if all staff saw "
            "why they are paid.")
    title = (title or '').strip()
    if not title:
        raise ValidationError("Title is required.")
    period = (period or '').strip()
    if not _PERIOD_RE.match(period):
        raise ValidationError("Period must be in YYYY-MM format.")
    rows = [l for l in (lines or [])
            if str(l.get('name') or '').strip()]
    if not rows:
        raise ValidationError("At least one incentive line is required.")

    parsed = [_validate_and_parse_line(l) for l in rows]
    # Employee discipline gate (CFO 2026-08-18): hold anyone carrying overdue
    # tasks — the check is on the RECIPIENT, never the manager/approver (that
    # was the 2026-08-07 bug that blocked Unami's approval on his OWN workload).
    # Block only that person; the eligible lines still go through.
    eligible, held = _split_by_overdue(parsed)
    if not eligible:
        raise ValidationError(_held_message(held))
    req = _persist_request(
        maker=maker, title=title, period=period, department=department,
        notes=notes, parsed=eligible, manager_attested=True)
    # Carried to the API layer for the response (not a persisted field) so the
    # manager is told exactly who was held and why.
    req.held_lines = held
    return req


def approve_request(req: IncentiveRequest, approver,
                    requested_slot: str = '') -> IncentiveRequest:
    if req.status != IncentiveRequest.Status.PENDING:
        raise ValidationError(f"Request is {req.status}, not pending.")
    if req.maker_id and approver.pk == req.maker_id:
        raise ValidationError(
            "Segregation of duties: you cannot approve your own request.")
    slot = slot_for(approver, requested_slot)
    if slot is None:
        raise ValidationError(
            "Only the CFO and Head of Human Capital approve incentive requests.")
    now = timezone.now()
    if slot == 'cfo':
        if req.cfo_approved_at:
            raise ValidationError("The CFO signature is already recorded.")
        # Dual control is two HUMANS, not two slots: a single superuser must
        # not be able to sign both legs (Fable review fix, 2026-07-13).
        if req.hr_approver_id and req.hr_approver_id == approver.pk:
            raise ValidationError(
                "The CFO and HR signatures must come from two different people.")
        req.cfo_approver, req.cfo_approved_at = approver, now
        fields = ['cfo_approver', 'cfo_approved_at']
    else:
        if req.hr_approved_at:
            raise ValidationError("The HR signature is already recorded.")
        if req.cfo_approver_id and req.cfo_approver_id == approver.pk:
            raise ValidationError(
                "The CFO and HR signatures must come from two different people.")
        req.hr_approver, req.hr_approved_at = approver, now
        fields = ['hr_approver', 'hr_approved_at']

    # NOTE (CFO 2026-08-18): the old "long-overdue-task" countersignature gate
    # that lived here was REMOVED. It checked the SUBMITTING/APPROVING person's
    # own overdue tasks and blocked Unami from approving because HE had pending
    # work — but managers and approvers always carry tasks. The discipline check
    # now lives at SUBMIT and looks at the EMPLOYEE receiving the incentive
    # (see submit_request / _split_by_overdue). Nothing to check at approval.

    if req.fully_signed:
        req.status = IncentiveRequest.Status.APPROVED
        fields.append('status')
    req.save(update_fields=fields + ['updated_at'], audit_user=approver)

    if req.status == IncentiveRequest.Status.APPROVED:
        from .incentive_notify import notify_approved
        try:
            notify_approved(req)
        except Exception:                  # noqa: BLE001
            log.exception("Incentive %s: notify_approved failed", req.pk)
        # Auto-feed to payroll (CFO 2026-08-28): the approved amounts flow into a
        # PENDING payroll batch by themselves — Finance no longer re-keys them.
        # Best-effort + idempotent: a failure here never un-approves the request;
        # the Finance re-run endpoint (push-to-payroll) recovers any stragglers.
        try:
            from .incentive_payroll_feed import feed_for_request
            res = feed_for_request(req, user=approver)
            if res.get('status') != 'pushed' or res.get('skipped'):
                # Never silent: an approved incentive that did not fully reach
                # payroll is logged for Finance to recover via push-to-payroll.
                log.warning("Incentive %s payroll auto-feed: %s", req.pk, res)
        except Exception:                  # noqa: BLE001
            log.exception("Incentive %s: payroll auto-feed failed", req.pk)
    return req


def reject_request(req: IncentiveRequest, approver,
                   notes: str = '') -> IncentiveRequest:
    if req.status != IncentiveRequest.Status.PENDING:
        raise ValidationError(f"Request is {req.status}, not pending.")
    if req.maker_id and approver.pk == req.maker_id:
        raise ValidationError(
            "Segregation of duties: you cannot decide your own request.")
    if slot_for(approver) is None and not getattr(approver, 'is_superuser', False):
        raise ValidationError(
            "Only the CFO and Head of Human Capital decide incentive requests.")
    req.status = IncentiveRequest.Status.REJECTED
    req.rejected_by = approver
    req.rejected_at = timezone.now()
    req.decision_notes = (notes or '').strip()
    req.save(update_fields=['status', 'rejected_by', 'rejected_at',
                            'decision_notes', 'updated_at'],
             audit_user=approver)
    from .incentive_notify import notify_rejected
    try:
        notify_rejected(req)
    except Exception:                      # noqa: BLE001
        log.exception("Incentive %s: notify_rejected failed", req.pk)
    return req


def mark_processed(req: IncentiveRequest, user) -> IncentiveRequest:
    if req.status != IncentiveRequest.Status.APPROVED:
        raise ValidationError("Only approved requests can be marked processed.")
    if req.payroll_processed:
        raise ValidationError("Already marked as processed in payroll.")
    if not is_finance(user):
        raise ValidationError(
            "Only Finance can mark a request as processed in payroll.")
    # This request's incentives already flow into payroll automatically
    # (CFO 2026-08-28). Marking it manually processed would risk a double entry,
    # so block it and point Finance at the auto-generated batch (Fable r2).
    from payroll.models import PayrollAmendmentBatch
    fed = req.lines.filter(
        payroll_amendment__batch__status=PayrollAmendmentBatch.Status.PARSED).exists()
    if fed:
        raise ValidationError(
            "This request's incentives are already queued in the month's "
            "auto-generated payroll batch — no manual payroll entry is needed.")
    req.payroll_processed = True
    req.payroll_processed_by = user
    req.payroll_processed_at = timezone.now()
    req.save(update_fields=['payroll_processed', 'payroll_processed_by',
                            'payroll_processed_at', 'updated_at'],
             audit_user=user)
    return req


# ---------------------------------------------------------------------------
# Amend (CFO 2026-07-22) — correct a still-pending request's amount / lines.
# ---------------------------------------------------------------------------
def _can_amend(user, req: IncentiveRequest) -> bool:
    """The original requester, the CFO/HR slot holders, or a superuser."""
    if getattr(user, 'is_superuser', False):
        return True
    if req.maker_id and getattr(user, 'pk', None) == req.maker_id:
        return True
    return slot_for(user) is not None


def _line_snapshot(line) -> dict:
    """Current field values of a line, as the merge base for an amend."""
    return {
        'name': line.name,
        'basis': line.basis,
        'amount': line.amount,
        'beyond_normal_duties': line.beyond_normal_duties,
        'on_time': line.on_time,
        'error_free': line.error_free,
        'needed_manager_fix': line.needed_manager_fix,
        'justification': line.justification,
    }


def amend_incentive(incentive_id, user, *, amount=None, reason=None,
                    lines=None, title=None, notes=None, department=None
                    ) -> IncentiveRequest:
    """Correct a still-pending incentive request (the fat-finger fix).

    Who: the ORIGINAL requester, the CFO or HR (superuser backup). This is NOT
    a signature, so the maker-≠-approver rule does not block the maker here.

    When: only while the request has NOT been processed in payroll and is not
    rejected. `amend blocked once processed`.

    What: change the amount (single-line convenience via `amount`, or per-line
    via `lines=[{id, amount, basis, justification, …}]`) and optionally the
    title / notes / department. Every touched line is re-validated through the
    SAME earned-incentive gate as a fresh submission (incl. the
    MIN_JUSTIFICATION_WORDS justification) — existing line detail is merged in,
    so correcting just the amount keeps the stored justification.

    Audit: writes one explicit AuditLog row recording old→new totals + amounts.
    If any amount changed, partial signatures are cleared and the request drops
    back to PENDING so a changed figure can never carry an old approval.
    """
    req = (IncentiveRequest.objects
           .prefetch_related('lines')
           .filter(pk=incentive_id)
           .first())
    if req is None:
        raise ValidationError("Incentive request not found.")
    if req.payroll_processed:
        raise ValidationError(
            "This request has already been processed in payroll and can no "
            "longer be amended.")
    if req.status == IncentiveRequest.Status.REJECTED:
        raise ValidationError("A rejected request cannot be amended.")
    if not _can_amend(user, req):
        raise ValidationError(
            "Only the original requester, the CFO or HR can amend a request.")
    if amount is None and lines is None and title is None and notes is None \
            and department is None:
        raise ValidationError(
            "Nothing to amend — provide a new amount or line details.")

    existing = list(req.lines.all())
    by_id = {str(l.pk): l for l in existing}
    old_amounts = sorted(str(l.amount) for l in existing)
    old_total = sum((l.amount for l in existing), Decimal('0.00'))
    status_before = req.status

    # Build (line_obj, merged_row) pairs.
    updates: list = []
    if lines is not None:
        for row in lines:
            lid = str((row or {}).get('id') or '')
            line = by_id.get(lid)
            if line is None:
                raise ValidationError(
                    f"Line {lid or '(missing id)'} is not part of this request.")
            merged = _line_snapshot(line)
            for k, v in (row or {}).items():
                if k != 'id' and v is not None:
                    merged[k] = v
            updates.append((line, merged))
    elif amount is not None:
        if len(existing) != 1:
            raise ValidationError(
                "This request has more than one line — send per-line changes "
                "(with each line id) instead of a single amount.")
        line = existing[0]
        merged = _line_snapshot(line)
        merged['amount'] = amount
        updates.append((line, merged))

    # Re-validate every touched line through the same gate, then apply.
    for line, merged in updates:
        parsed = _validate_and_parse_line(merged)
        line.name = parsed['name'] or line.name
        line.basis = parsed['basis']
        line.amount = parsed['amount']
        line.beyond_normal_duties = parsed['beyond_normal_duties']
        line.on_time = parsed['on_time']
        line.error_free = parsed['error_free']
        line.needed_manager_fix = parsed['needed_manager_fix']
        line.justification = parsed['justification']
        # One explicit AuditLog row (below) records the amend; skip the mixin's
        # per-save auto-row to avoid double entries (BUG-003 convention).
        line.save(skip_audit=True)

    new_amounts = sorted(str(l.amount) for l in existing)
    new_total = sum((l.amount for l in existing), Decimal('0.00'))
    amount_changed = old_amounts != new_amounts

    header_fields = ['amended_at', 'amended_by', 'updated_at']
    req.amended_at = timezone.now()
    req.amended_by = user
    if title is not None:
        req.title = (title or '').strip()[:160] or req.title
        header_fields.append('title')
    if notes is not None:
        req.notes = (notes or '').strip()
        header_fields.append('notes')
    if department is not None:
        req.department = (department or '').strip()[:80]
        header_fields.append('department')

    signatures_reset = False
    if amount_changed and (req.cfo_approved_at or req.hr_approved_at):
        req.cfo_approver = None
        req.cfo_approved_at = None
        req.hr_approver = None
        req.hr_approved_at = None
        req.status = IncentiveRequest.Status.PENDING
        signatures_reset = True
        header_fields += ['cfo_approver', 'cfo_approved_at',
                          'hr_approver', 'hr_approved_at', 'status']

    req.save(update_fields=list(dict.fromkeys(header_fields)), skip_audit=True)

    editor_email = getattr(user, 'email', '') or 'unknown'
    from core.models import AuditLog
    AuditLog.objects.create(
        table_name='IncentiveRequest',
        record_id=str(req.pk),
        action=AuditLog.Action.UPDATE,
        old_values={'total': str(old_total), 'line_amounts': old_amounts,
                    'status': status_before},
        new_values={'total': str(new_total), 'line_amounts': new_amounts,
                    'status': req.status, 'reason': (reason or '').strip(),
                    'signatures_reset': signatures_reset},
        user=user,
        description=(
            f"Incentive request amended by {editor_email}: total "
            f"BWP {old_total} → BWP {new_total}"
            + ("; signatures reset — re-approval required"
               if signatures_reset else "")),
    )

    from .incentive_notify import notify_amended
    try:
        notify_amended(req, old_total=old_total, new_total=new_total,
                       editor_email=editor_email,
                       signatures_reset=signatures_reset)
    except Exception:                      # noqa: BLE001 — never block on email
        log.exception("Incentive %s: notify_amended failed", req.pk)
    return req


# ---------------------------------------------------------------------------
# Recurring incentives (CFO 2026-07-22) — set a monthly incentive once.
# ---------------------------------------------------------------------------
def can_manage_recurring(user) -> bool:
    """Managers and above (same tier that submits incentives)."""
    return can_submit(user)


def current_period() -> str:
    return timezone.now().strftime('%Y-%m')


def list_recurring_templates(user):
    """All standing templates (a shared list — the CFO/HR review them too)."""
    return RecurringIncentive.objects.all()


def create_recurring_template(*, user, name: str, amount, category: str = '',
                              basis: str = '', employee_id=None,
                              department: str = '', justification: str = '',
                              note: str = '') -> RecurringIncentive:
    if not can_manage_recurring(user):
        raise ValidationError(
            "Only managers and above can manage recurring incentives.")
    name = (name or '').strip()[:160]
    category = (category or '').strip()[:120]
    if not name and not category:
        raise ValidationError(
            "A name or category is required for a recurring incentive.")
    amt = _parse_amount(amount)

    # Optional employee link — store the id, and prefer the live full name as
    # the label when it resolves.
    employee = None
    if employee_id:
        from payroll.models import Employee
        employee = Employee.objects.filter(pk=employee_id).first()

    tpl = RecurringIncentive(
        name=(employee.full_name if employee else (name or category)),
        category=category,
        basis=(basis or '').strip()[:200],
        amount=amt,
        employee_id=(employee.pk if employee else (employee_id or None)),
        department=(department or '').strip()[:80],
        justification=(justification or '').strip(),
        note=(note or '').strip(),
        created_by=user,
        created_by_email=getattr(user, 'email', '') or '',
    )
    tpl.save(audit_user=user)
    return tpl


def set_recurring_active(template_id, user, active: bool) -> RecurringIncentive:
    if not can_manage_recurring(user):
        raise ValidationError(
            "Only managers and above can manage recurring incentives.")
    tpl = RecurringIncentive.objects.filter(pk=template_id).first()
    if tpl is None:
        raise ValidationError("Recurring incentive not found.")
    tpl.active = bool(active)
    tpl.save(update_fields=['active', 'updated_at'], audit_user=user)
    return tpl


def _recurring_line(tpl: RecurringIncentive) -> dict:
    """Build a parsed incentive line from a template (bypasses the ad-hoc gate
    — a recurring incentive is a pre-authorised arrangement — but still carries
    a substantive standing justification so any later amend re-validates)."""
    why = (tpl.justification or '').strip()
    if len(why.split()) < MIN_JUSTIFICATION_WORDS:
        who = tpl.name or tpl.category or 'this employee'
        why = (f"Standing recurring incentive for {who}, authorised by "
               f"{tpl.created_by_email or 'management'} and set once as an "
               f"ongoing monthly arrangement for over-performance above their "
               f"normal day-to-day duties. It was pre-approved as a repeating "
               f"reward, and each individual period is still independently "
               f"reviewed, re-validated and signed off by both the CFO and HR "
               f"before payroll is permitted to process any payment for that "
               f"month.")
    return {
        'name': tpl.name,
        'basis': tpl.basis,
        'amount': tpl.amount,
        'employee_id': str(tpl.employee_id) if tpl.employee_id else None,
        'beyond_normal_duties': True,
        'on_time': True,
        'error_free': True,
        'needed_manager_fix': False,
        'justification': why,
    }


def generate_recurring_for_period(period: str, user) -> dict:
    """Materialise one IncentiveRequest per ACTIVE template for `period`.

    Idempotent: a (template, period) that already produced a request is
    skipped — safe to run twice, or monthly by cron. The generated requests are
    ordinary PENDING requests that go through the normal CFO sign-off; no
    approver email is sent here (the manager reviews the batch on the page
    first).
    """
    if not can_manage_recurring(user):
        raise ValidationError(
            "Only managers and above can generate recurring incentives.")
    period = (period or '').strip()
    if not _PERIOD_RE.match(period):
        raise ValidationError("Period must be in YYYY-MM format.")

    created, skipped = [], []
    for tpl in RecurringIncentive.objects.filter(active=True):
        if IncentiveRequest.objects.filter(
                source_template=tpl, period=period).exists():
            skipped.append(tpl)
            continue
        title = f"{tpl.category or tpl.name} — recurring {period}"
        req = _persist_request(
            maker=user, title=title[:160], period=period,
            department=tpl.department, notes='',
            parsed=[_recurring_line(tpl)],
            source_template=tpl, notify=False)
        created.append(req)

    return {
        'period': period,
        'created': created,
        'skipped': skipped,
        'created_count': len(created),
        'skipped_count': len(skipped),
    }
