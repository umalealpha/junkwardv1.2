"""commissions/service.py — commission submission logic.

Standalone: computes totals + withholding and produces a payout CSV. No GL,
no payment run, no state change on export — those stay manual finance controls.
"""
from __future__ import annotations

import csv
import io
import logging
from decimal import Decimal, ROUND_HALF_UP

from django.db import transaction
from django.utils import timezone

from .models import CommissionSubmission

log = logging.getLogger('commissions')
TWO = Decimal('0.01')


def _money(x) -> Decimal:
    return Decimal(x or 0).quantize(TWO, rounding=ROUND_HALF_UP)


def compute_totals(line_amounts, withholding_rate) -> dict:
    """Pure (no DB). Gross = sum of per-line commission; withholding = gross ×
    rate (rate is a fraction, 0.10 = 10%); net = gross − withholding. All 2dp."""
    gross = _money(sum((Decimal(a or 0) for a in line_amounts), Decimal('0')))
    rate = Decimal(withholding_rate or 0)
    withholding = _money(gross * rate)
    net = _money(gross - withholding)
    return {'gross': gross, 'withholding': withholding, 'net': net}


def computed_commission(amount_collected, commission_rate_pct) -> Decimal:
    """Soft cross-check only — never overwrites the entered commission_amount.
    amount_collected × commission_rate% (rate entered as a percentage)."""
    return _money(Decimal(amount_collected or 0) * Decimal(commission_rate_pct or 0) / Decimal('100'))


def effective_withholding_rate(agent, group) -> Decimal:
    """The rate that actually applies to this agent (CFO 2026-07-15):
    an independent agent working THROUGH a company has NO withholding; a direct
    independent agent has the group's 10%. Payroll groups already carry 0."""
    if getattr(agent, 'works_via_company', False):
        return Decimal('0.0000')
    return group.withholding_rate


def recompute_submission(sub: CommissionSubmission) -> CommissionSubmission:
    """Re-total a submission from its lines, applying the agent's effective rate."""
    amounts = list(sub.lines.values_list('commission_amount', flat=True))
    rate = effective_withholding_rate(sub.agent, sub.group)
    t = compute_totals(amounts, rate)
    sub.gross_commission = t['gross']
    sub.withholding_rate = rate
    sub.withholding_amount = t['withholding']
    sub.net_payable = t['net']
    sub.save()
    if sub.status == CommissionSubmission.Status.APPROVED:
        # Auto-feed payroll-group commissions into a PENDING payroll batch
        # (CFO 2026-08-28). Best-effort + idempotent; never un-approve on error.
        try:
            from .payroll_feed import feed_submission
            _res = feed_submission(sub, user=locals().get('user'))
            if _res.get('status') not in ('pushed', 'not_payroll_group', 'not_approved') or _res.get('skipped'):
                log.warning('Commission %s payroll auto-feed: %s', sub.id, _res)
        except Exception:  # noqa: BLE001
            log.exception('Commission %s payroll auto-feed failed', sub.id)
    return sub


@transaction.atomic
def submit(sub: CommissionSubmission, user) -> CommissionSubmission:
    """Agent submits: draft/rejected → submitted (1st review). Snapshots totals
    and clears any prior sign-off stamps (a re-submit starts the chain over)."""
    S = CommissionSubmission.Status
    if sub.status not in (S.DRAFT, S.REJECTED):
        raise ValueError(f'Cannot submit a submission that is {sub.get_status_display()}.')
    # b6ccaa38 (CFO 21-Sep-2026): a submission MUST record who submitted it. A
    # NULL submitter made the self-review guard blind, so the summary-upload path
    # (which submitted with None) sailed past approval. Fail closed, never NULL.
    if user is None or getattr(user, 'id', None) is None:
        raise ValueError('A submission must record who submitted it.')
    recompute_submission(sub)
    sub.status = S.SUBMITTED
    sub.submitted_by = user
    sub.submitted_at = timezone.now()
    sub.first_reviewed_by = sub.second_reviewed_by = sub.final_by = None
    sub.first_reviewed_at = sub.second_reviewed_at = sub.final_at = None
    sub.review_note = ''
    sub.save()
    _tell_the_next_reviewers(sub)
    if sub.status == CommissionSubmission.Status.APPROVED:
        # Auto-feed payroll-group commissions into a PENDING payroll batch
        # (CFO 2026-08-28). Best-effort + idempotent; never un-approve on error.
        try:
            from .payroll_feed import feed_submission
            _res = feed_submission(sub, user=locals().get('user'))
            if _res.get('status') not in ('pushed', 'not_payroll_group', 'not_approved') or _res.get('skipped'):
                log.warning('Commission %s payroll auto-feed: %s', sub.id, _res)
        except Exception:  # noqa: BLE001
            log.exception('Commission %s payroll auto-feed failed', sub.id)
    return sub


def _tell_the_next_reviewers(sub):
    """Email the reviewers for the stage this submission has just landed on.

    on_commit: the email carries a one-tap approve link, so it must never go out
    for a transition a later failure rolls back. Best-effort throughout - an
    email problem must not undo a review that really happened.
    """
    def _go(_id=sub.id):
        try:
            from .models import CommissionSubmission
            fresh = (CommissionSubmission.objects.select_related('agent')
                     .filter(id=_id).first())
            if fresh is None:
                return
            from .notify import notify_commission_awaiting_review
            notify_commission_awaiting_review(fresh)
        except Exception:   # noqa: BLE001
            log.exception('commission awaiting-review notify failed for %s', _id)

    transaction.on_commit(_go)


@transaction.atomic
def review(sub: CommissionSubmission, user, approve: bool, note: str = '') -> CommissionSubmission:
    """Advance the submission one stage (approve) or send it back (reject).

    3-stage chain: submitted →(1st: Bokani/Tlamelo) second_review →(2nd: Pako/
    Kago) final_review →(final: CFO) approved. Separation of duties: a reviewer
    may not be the submitter, the payee, or a person who signed an earlier stage.
    """
    from .access import agent_for_user, can_review_stage, stage_of, STAGE1, STAGE2
    S = CommissionSubmission.Status
    stage = stage_of(sub)
    if not stage:
        raise ValueError(f'This submission is {sub.get_status_display()} — not awaiting review.')
    if not can_review_stage(user, stage):
        raise ValueError('You are not a reviewer for this stage.')
    if approve:
        uid = getattr(user, 'id', None)
        # Fail closed (b6ccaa38): a submission with no recorded submitter is
        # REFUSED at approval, never waved through. The old guard was skipped
        # when submitted_by_id was NULL, which is how the blind control passed.
        if sub.submitted_by_id is None:
            raise ValueError('This submission has no recorded submitter and cannot be approved.')
        if uid is not None and sub.submitted_by_id == uid:
            raise ValueError('You cannot review your own submission.')
        if agent_for_user(user) == sub.agent:
            raise ValueError('You cannot review a submission that pays you.')
        if uid is not None and uid in {sub.first_reviewed_by_id, sub.second_reviewed_by_id}:
            raise ValueError('You already reviewed this at an earlier stage.')
        now = timezone.now()
        if stage == STAGE1:
            sub.status, sub.first_reviewed_by, sub.first_reviewed_at = S.SECOND_REVIEW, user, now
        elif stage == STAGE2:
            sub.status, sub.second_reviewed_by, sub.second_reviewed_at = S.FINAL_REVIEW, user, now
        else:
            sub.status, sub.final_by, sub.final_at = S.APPROVED, user, now
    else:
        sub.status = S.REJECTED
        sub.review_note = (note or '').strip()[:300]
    sub.save()
    if approve and sub.status != S.APPROVED:
        # Advanced a stage, not finished - the people who own the NEXT stage
        # need to hear about it. Fully approved needs no reviewer email, and
        # a rejection goes back to the agent through the existing route.
        _tell_the_next_reviewers(sub)
    if sub.status == CommissionSubmission.Status.APPROVED:
        # Auto-feed payroll-group commissions into a PENDING payroll batch
        # (CFO 2026-08-28). Best-effort + idempotent; never un-approve on error.
        try:
            from .payroll_feed import feed_submission
            _res = feed_submission(sub, user=locals().get('user'))
            if _res.get('status') not in ('pushed', 'not_payroll_group', 'not_approved') or _res.get('skipped'):
                log.warning('Commission %s payroll auto-feed: %s', sub.id, _res)
        except Exception:  # noqa: BLE001
            log.exception('Commission %s payroll auto-feed failed', sub.id)
    return sub


@transaction.atomic
def final_approve(sub: CommissionSubmission, user, approve: bool = True,
                  note: str = '') -> CommissionSubmission:
    """CFO short-circuit (CFO 2026-08-18): the final approver may approve — or
    send back — a commission from ANY awaiting-review stage, without waiting for
    the 1st and 2nd reviews. Segregation of duties still holds: the CFO cannot
    approve their own submission or one that pays them.
    """
    from .access import agent_for_user, can_review_stage, FINAL
    S = CommissionSubmission.Status
    awaiting = (S.SUBMITTED, S.SECOND_REVIEW, S.FINAL_REVIEW)
    if sub.status not in awaiting:
        raise ValueError(f'This submission is {sub.get_status_display()} — not awaiting review.')
    if not can_review_stage(user, FINAL):
        raise ValueError('Only the final approver can approve directly.')
    if approve:
        uid = getattr(user, 'id', None)
        # Fail closed (b6ccaa38): no recorded submitter → refuse the direct approve.
        if sub.submitted_by_id is None:
            raise ValueError('This submission has no recorded submitter and cannot be approved.')
        if uid is not None and sub.submitted_by_id == uid:
            raise ValueError('You cannot approve your own submission.')
        if agent_for_user(user) == sub.agent:
            raise ValueError('You cannot approve a submission that pays you.')
        now = timezone.now()
        sub.status, sub.final_by, sub.final_at = S.APPROVED, user, now
        sub.review_note = (note.strip() if note and note.strip()
                           else 'Approved directly by the CFO (1st & 2nd review bypassed).')[:300]
    else:
        sub.status = S.REJECTED
        sub.review_note = (note or '').strip()[:300]
    sub.save()
    if sub.status == CommissionSubmission.Status.APPROVED:
        # Auto-feed payroll-group commissions into a PENDING payroll batch
        # (CFO 2026-08-28). Best-effort + idempotent; never un-approve on error.
        try:
            from .payroll_feed import feed_submission
            _res = feed_submission(sub, user=locals().get('user'))
            if _res.get('status') not in ('pushed', 'not_payroll_group', 'not_approved') or _res.get('skipped'):
                log.warning('Commission %s payroll auto-feed: %s', sub.id, _res)
        except Exception:  # noqa: BLE001
            log.exception('Commission %s payroll auto-feed failed', sub.id)
    return sub


@transaction.atomic
def mark_processed(sub: CommissionSubmission, user) -> CommissionSubmission:
    """Payroll marks an approved submission processed → paid. Then notify the
    1st-stage reviewers (Bokani/Tlamelo), cc the 2nd (Pako/Kago). The notify is
    best-effort — an email failure never rolls back the processed status."""
    S = CommissionSubmission.Status
    if sub.status != S.APPROVED:
        raise ValueError(f'Only an approved submission can be marked processed (this is {sub.get_status_display()}).')
    from payroll.models import PayrollAmendmentBatch
    if (sub.payroll_amendment_id and sub.payroll_amendment
            and sub.payroll_amendment.batch.status == PayrollAmendmentBatch.Status.PARSED):
        raise ValueError('This commission already flows into the month\'s auto payroll batch — '
                         'no manual payroll entry is needed.')
    sub.status = S.PAID
    sub.paid_by = user
    sub.paid_at = timezone.now()
    sub.save()
    try:
        from .notify import notify_payroll_done
        if notify_payroll_done(sub).get('sent'):
            sub.notified_at = timezone.now()
            sub.save(update_fields=['notified_at'])
    except Exception:   # never block the payroll transition on an email problem
        log.exception('commission payroll-done notify failed for %s', sub.id)
    return sub


def _row(sub: CommissionSubmission, bank) -> dict:
    return {
        'agent': sub.agent.name,
        'agent_code': sub.agent.agent_code,
        'period': sub.period_label,
        'gross': str(sub.gross_commission),
        'withholding': str(sub.withholding_amount),
        'net': str(sub.net_payable),
        'bank_name': getattr(bank, 'bank_name', '') if bank else '',
        'account_name': getattr(bank, 'account_name', '') if bank else '',
        'account_number': getattr(bank, 'account_number', '') if bank else '',
        'branch_code': getattr(bank, 'branch_code', '') if bank else '',
        'hold_reason': '',
    }


def payout_rows(group, period_label):
    """Approved submissions for a group + month, split into `ready` (bank details
    captured, net > 0) and `held` (surfaced with a reason, never dropped)."""
    S = CommissionSubmission.Status
    qs = (CommissionSubmission.objects
          .filter(group=group, period_label=period_label, status=S.APPROVED)
          .select_related('agent', 'agent__bank', 'group')
          .order_by('agent__name'))
    ready, held = [], []
    for sub in qs:
        bank = getattr(sub.agent, 'bank', None)
        row = _row(sub, bank)
        if sub.net_payable <= 0:
            # A bank file cannot carry a zero/negative payment (e.g. a clawback).
            row['hold_reason'] = 'Net is zero or negative — not payable'
            held.append(row)
        elif bank and (bank.account_number or '').strip():
            ready.append(row)
        else:
            row['hold_reason'] = 'No bank details captured'
            held.append(row)
    return ready, held


def _csv_safe(v):
    """Neutralise spreadsheet formula injection: a cell starting =, +, -, @, tab
    or CR is executed as a formula by Excel/Sheets. Prefix it with a quote."""
    s = '' if v is None else str(v)
    return "'" + s if s[:1] in ('=', '+', '-', '@', '\t', '\r') else s


def export_payout_csv(group, period_label, user=None):
    """Build the payout CSV (ready rows only). Returns (csv_text, meta). Read-only
    — no status change; marking as paid stays a deliberate manual step."""
    ready, held = payout_rows(group, period_label)
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(['Agent', 'Agent code', 'Period', 'Bank', 'Account name',
                'Account number', 'Branch code', 'Gross', 'Withholding', 'Net'])
    for r in ready:
        w.writerow([_csv_safe(x) for x in
                    (r['agent'], r['agent_code'], r['period'], r['bank_name'],
                     r['account_name'], r['account_number'], r['branch_code'],
                     r['gross'], r['withholding'], r['net'])])
    meta = {'ready_count': len(ready), 'held_count': len(held),
            'total_net': str(_money(sum((Decimal(r['net']) for r in ready), Decimal('0'))))}
    return buf.getvalue(), meta


def submission_payout_csv(sub: CommissionSubmission) -> str:
    """One-row payout CSV for a single submission (any status) — used by the
    payroll-done notification. Formula-injection safe."""
    row = _row(sub, getattr(sub.agent, 'bank', None))
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(['Agent', 'Agent code', 'Period', 'Bank', 'Account name',
                'Account number', 'Branch code', 'Gross', 'Withholding', 'Net'])
    w.writerow([_csv_safe(x) for x in
                (row['agent'], row['agent_code'], row['period'], row['bank_name'],
                 row['account_name'], row['account_number'], row['branch_code'],
                 row['gross'], row['withholding'], row['net'])])
    return buf.getvalue()


def monthly_summary(group=None, period_label=None):
    """Per-agent rollup for the queue view."""
    from django.db.models import Count
    qs = (CommissionSubmission.objects
          .select_related('agent', 'group')
          .annotate(line_count=Count('lines')))   # one query, not N+1
    if group is not None:
        qs = qs.filter(group=group)
    if period_label:
        qs = qs.filter(period_label=period_label)
    out = []
    for sub in qs.order_by('-period_label', 'agent__name'):
        out.append({
            'id': str(sub.id),
            'agent': sub.agent.name,
            'group': sub.group.name,
            'period': sub.period_label,
            'status': sub.status,
            'gross': str(sub.gross_commission),
            'withholding': str(sub.withholding_amount),
            'net': str(sub.net_payable),
            'lines': sub.line_count,
        })
    return out


def email_agent_statements(group, period_label, commit=True):
    """Month-close: email each APPROVED/PAID agent in a group+month their own
    statement (CFO 2026-08-22). commit=False is a dry-run that returns who WOULD
    be emailed and who has no address — nothing is sent. Best-effort per agent:
    one failure never stops the rest. Never raises for a single bad send."""
    S = CommissionSubmission.Status
    subs = list(CommissionSubmission.objects
                .filter(group=group, period_label=period_label, status__in=[S.APPROVED, S.PAID])
                .select_related('agent', 'group').prefetch_related('lines'))
    would = [s.agent.name for s in subs if getattr(s.agent, 'email', '')]
    no_email = [s.agent.name for s in subs if not getattr(s.agent, 'email', '')]
    if not commit:
        return {'count': len(would), 'would_email': would, 'no_email': no_email}
    from .notify import email_agent_statement
    emailed, failed = [], []
    for s in subs:
        if not getattr(s.agent, 'email', ''):
            continue
        try:
            (emailed if email_agent_statement(s).get('sent') else failed).append(s.agent.name)
        except Exception:  # noqa: BLE001
            import logging
            logging.getLogger('commissions').exception('statement email failed for %s', s.agent.name)
            failed.append(s.agent.name)
    return {'emailed': len(emailed), 'emailed_names': emailed, 'no_email': no_email, 'failed': failed}
