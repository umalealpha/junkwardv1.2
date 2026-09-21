"""core/request_tracker.py — unified "My Requests" tracker (CFO directive 2026-08-04).

Aggregates every approval-workflow request a staff member has SUBMITTED — leave,
leave pay (encashment), staff loan, petty cash, payment request — into one
normalised list so they can see the status, whose desk it is on right now, and
what is next.

This is the requester-side twin of core/approvals_views.pending_approvals_for
(the approver side). READ ONLY — it never changes a request. Each module is read
in its own try/except so one broken module can never blank the whole page. The
per-module "mine" filters mirror those verified in each module's own list view.
"""
from __future__ import annotations

import logging
from dataclasses import asdict, dataclass, field
from typing import Optional

from django.utils import timezone

from core.request_tracker_ai import plain_status_line

log = logging.getLogger(__name__)

# Buckets that still sit on someone's desk (aging applies).
_IN_FLIGHT = {'pending', 'approved'}
# How many days on one desk before we flag it as aging/stuck.
_AGING_DAYS = 5
# Hard cap on external AI-polish calls per request (so ?ai=1 can never fan a
# single page load out into an unbounded number of metered LLM calls).
_AI_POLISH_CAP = 10


@dataclass
class TrackedRequest:
    kind: str
    kind_label: str
    id: str
    title: str
    amount: Optional[float]
    currency: str
    bucket: str                   # pending|approved|rejected|paid|cancelled|draft
    status_label: str
    holder_label: str             # whose desk now ('' when finished)
    next_label: str               # what is next ('' when none)
    submitted_at: Optional[str]
    updated_at: Optional[str]
    days_waiting: Optional[int]
    href: str
    timeline: list = field(default_factory=list)
    stuck: bool = False
    status_line: str = ''
    # A7 — the exact six words the CFO asked for, derived from bucket+holder.
    holder_standard: str = ''
    # A3 — None means receipts do not apply to this kind of request.
    has_receipt: Optional[bool] = None

    def to_dict(self) -> dict:
        return asdict(self)


def _iso(dt):
    return dt.isoformat() if dt else None


def _days_since(dt):
    if not dt:
        return None
    return max(0, (timezone.now() - dt).days)


def _step(label, done, at=None):
    return {'label': label, 'done': bool(done), 'at': _iso(at)}


# ── A7 — standard approval-progress wording ──────────────────────────────────
# The CFO asked for these phrases across Leave, Expense, Petty Cash and
# Reimbursement. Originally six; 'Waiting with HR' was added 15-Sep-2026 after
# leave encashment turned out to sit on an HR desk that none of the six fitted.
# Each adapter keeps its own precise desk name in
# `holder_label` (losing "a second finance signatory" would cost the requester
# real information); `holder_standard` is the standard phrase shown first.
WAITING_MANAGER = 'Waiting with Manager'
WAITING_CFO     = 'Waiting with CFO'
WAITING_FINANCE = 'Waiting with Finance'
# Seventh phrase, added on the CFO's instruction 15-Sep-2026. The original spec
# said exactly six, but leave encashment genuinely sits with HR and none of the
# six fitted; folding it into Finance would have sent people to the wrong desk.
WAITING_HR      = 'Waiting with HR'
WAITING_ME      = 'Waiting with Me'
COMPLETED       = 'Completed'
REJECTED        = 'Rejected'

# Matched on the desk name each adapter already sets. Allow-list, never a
# substring range: an unrecognised desk falls through to '' and is shown as the
# adapter's own words rather than guessed into the wrong queue.
_HOLDER_STANDARD = {
    'your approver':                 WAITING_MANAGER,
    'the CFO':                       WAITING_CFO,
    'Finance':                       WAITING_FINANCE,
    # _leave_pay's own desk names — missed on the first pass, which is exactly
    # the string-coupling failure this allow-list is meant to make visible.
    'Finance (FC/FM)':               WAITING_FINANCE,
    'Finance (for payment)':         WAITING_FINANCE,
    'Finance (to pay)':              WAITING_FINANCE,
    'Finance (to release)':          WAITING_FINANCE,
    'Finance (to reimburse)':        WAITING_FINANCE,
    'a finance signatory':           WAITING_FINANCE,
    'a second finance signatory':    WAITING_FINANCE,
    'HR':                            WAITING_HR,
    'HR (to disburse)':              WAITING_HR,
    'you (to sign)':                 WAITING_ME,
}


def _standard_holder(bucket: str, holder_label: str) -> str:
    """The A7 phrase for a row. Terminal buckets win over any stale desk name."""
    if bucket == 'rejected':
        return REJECTED
    if bucket in ('paid', 'cancelled'):
        return COMPLETED
    if bucket == 'draft':
        return WAITING_ME
    return _HOLDER_STANDARD.get((holder_label or '').strip(), '')


# ── Per-module adapters — each returns list[TrackedRequest] for THIS user ──────

def _leave_pay(user):
    from hris.leave_encash_models import LeaveEncashment as LE
    buckets = {'pending_cfo': 'pending', 'pending_hr': 'pending',
               'pending_finance': 'pending', 'approved': 'approved',
               'rejected': 'rejected', 'paid': 'paid'}
    holders = {'pending_cfo': 'the CFO', 'pending_hr': 'HR',
               'pending_finance': 'Finance (FC/FM)', 'approved': 'Finance (for payment)'}
    nexts = {'pending_cfo': 'HR', 'pending_hr': 'Finance',
             'pending_finance': 'payment', 'approved': 'payment'}
    out = []
    for e in LE.objects.filter(applicant=user).select_related('employee'):
        bucket = buckets.get(e.status, 'pending')
        out.append(TrackedRequest(
            kind='leave_pay', kind_label='Leave Pay', id=str(e.pk),
            title=f'{e.days} days', amount=float(e.amount or 0), currency='BWP',
            bucket=bucket, status_label=e.get_status_display(),
            holder_label=holders.get(e.status, ''), next_label=nexts.get(e.status, ''),
            submitted_at=_iso(e.created_at), updated_at=_iso(e.updated_at),
            days_waiting=_days_since(e.updated_at) if bucket in _IN_FLIGHT else None,
            href='/hris/leave-encashment',
            timeline=[
                _step('Submitted', True, e.created_at),
                _step('CFO', e.cfo_approved_at, e.cfo_approved_at),
                _step('HR', e.hr_approved_at, e.hr_approved_at),
                _step('Finance', e.finance_approved_at, e.finance_approved_at),
                _step('Paid', e.payroll_processed_at, e.payroll_processed_at),
            ],
        ))
    return out


def _leave(user):
    from hris.models import LeaveRequest
    try:
        from hris.feature_views import _profile_for
        profile = _profile_for(user)
    except Exception:                       # noqa: BLE001
        profile = None
    if profile is None:
        return []
    # Leave has no payment leg, so an APPROVED leave request is finished — map it
    # to a terminal bucket ('paid' renders as "Done") so it is not counted "in
    # progress" forever.
    buckets = {'draft': 'draft', 'pending': 'pending', 'approved': 'paid',
               'refused': 'rejected', 'cancelled': 'cancelled'}
    out = []
    for lr in LeaveRequest.objects.filter(profile=profile).select_related('leave_type'):
        bucket = buckets.get(lr.status, 'pending')
        out.append(TrackedRequest(
            kind='leave', kind_label='Leave', id=str(lr.pk),
            title=f'{lr.leave_type} · {lr.days} day(s)', amount=None, currency='',
            bucket=bucket, status_label=lr.get_status_display(),
            holder_label='your approver' if lr.status == 'pending' else '',
            next_label='',
            submitted_at=_iso(lr.created_at), updated_at=_iso(lr.updated_at),
            days_waiting=_days_since(lr.updated_at) if bucket == 'pending' else None,
            href='/hris/leave',
            timeline=[
                _step('Submitted', lr.status != 'draft', lr.created_at),
                _step('Decision', lr.decided_at, lr.decided_at),
            ],
        ))
    return out


def _loan(user):
    from staff_loans.models import StaffLoanApplication as SLA
    buckets = {'draft': 'draft', 'pending_cfo': 'pending', 'approved': 'approved',
               'signed': 'approved', 'active': 'paid', 'declined': 'rejected',
               'cancelled': 'cancelled'}
    holders = {'pending_cfo': 'the CFO', 'approved': 'you (to sign)',
               'signed': 'Finance (to release)'}
    nexts = {'pending_cfo': 'your signature', 'approved': 'Finance to release',
             'signed': 'payout'}
    out = []
    for l in SLA.objects.filter(employee__user=user).select_related('employee'):
        bucket = buckets.get(l.status, 'pending')
        try:
            title = f'{l.get_loan_type_display()} loan'
        except Exception:                   # noqa: BLE001
            title = 'Staff loan'
        out.append(TrackedRequest(
            kind='loan', kind_label='Loan', id=str(l.pk),
            title=title,
            amount=float(l.approved_amount or l.amount_requested or 0), currency='BWP',
            bucket=bucket, status_label=l.get_status_display(),
            holder_label=holders.get(l.status, ''), next_label=nexts.get(l.status, ''),
            submitted_at=_iso(l.submitted_at or l.created_at), updated_at=_iso(l.updated_at),
            days_waiting=_days_since(l.updated_at) if bucket in _IN_FLIGHT else None,
            href='/hris/staff-loans',
            timeline=[
                _step('Submitted', l.status != 'draft', l.submitted_at or l.created_at),
                _step('CFO approval', l.cfo_decided_at, l.cfo_decided_at),
                _step('Signed', l.signed_at, l.signed_at),
                _step('Disbursed', l.disbursed_at, l.disbursed_at),
            ],
        ))
    return out


def _petty(user):
    from petty_cash.models import PettyCashVoucher as PCV
    buckets = {'draft': 'draft', 'pending_approval': 'pending',
               'one_signature': 'pending', 'posted': 'approved',
               'reimbursed': 'paid', 'rejected': 'rejected'}
    holders = {'pending_approval': 'a finance signatory',
               'one_signature': 'a second finance signatory',
               'posted': 'Finance (to reimburse)'}
    nexts = {'pending_approval': 'first signature', 'one_signature': 'second signature',
             'posted': 'reimbursement'}
    out = []
    for v in PCV.objects.filter(created_by=user):
        bucket = buckets.get(v.status, 'pending')
        out.append(TrackedRequest(
            kind='petty_cash', kind_label='Petty Cash', id=str(v.pk),
            title=(v.description or v.payee or v.voucher_number or 'Petty cash')[:80],
            amount=float(v.amount or 0), currency='BWP',
            bucket=bucket, status_label=v.get_status_display(),
            holder_label=holders.get(v.status, ''), next_label=nexts.get(v.status, ''),
            submitted_at=_iso(v.submitted_at or v.created_at), updated_at=_iso(v.updated_at),
            days_waiting=_days_since(v.updated_at) if bucket in _IN_FLIGHT else None,
            href='/petty-cash',
            timeline=[
                _step('Submitted', v.status != 'draft', v.submitted_at or v.created_at),
                _step('First signature', v.first_approved_at, v.first_approved_at),
                _step('Second signature', v.approved_at, v.approved_at),
                _step('Reimbursed', v.status == 'reimbursed', None),
            ],
        ))
    return out


def _payment(user):
    from taskboard.models import PaymentRequest as PR
    buckets = {'pending_finance': 'pending', 'pending_cfo': 'pending',
               'rejected': 'rejected', 'paid': 'paid', 'cancelled': 'cancelled'}
    holders = {'pending_finance': 'Finance', 'pending_cfo': 'the CFO'}
    nexts = {'pending_finance': 'CFO authorisation', 'pending_cfo': 'payment'}
    out = []
    for p in PR.objects.filter(created_by=user):
        bucket = buckets.get(p.status, 'pending')
        out.append(TrackedRequest(
            kind='payment', kind_label='Payment', id=str(p.pk),
            title=(p.subject or p.payee or p.ref or 'Payment request')[:80],
            amount=float(p.total or 0), currency=(getattr(p, 'currency', '') or 'BWP'),
            bucket=bucket, status_label=p.get_status_display(),
            holder_label=holders.get(p.status, ''), next_label=nexts.get(p.status, ''),
            submitted_at=_iso(p.created_at), updated_at=_iso(p.updated_at),
            days_waiting=_days_since(p.updated_at) if bucket == 'pending' else None,
            href='/payment-requests',
            timeline=[
                _step('Submitted', True, p.created_at),
                _step('Finance sign-off', p.first_approved_at, p.first_approved_at),
                _step('CFO authorised / Paid', p.status == 'paid', None),
            ],
        ))
    return out


def _expense(user):
    """A3 + A5 — out-of-pocket refunds. Requester-scoped by profile__user, the
    same filter MyExpenseClaimsView already uses."""
    from hris.expense_claim_models import ExpenseClaim as EC
    buckets = {'draft': 'draft', 'submitted': 'pending', 'pending_cfo': 'pending',
               'approved': 'approved', 'rejected': 'rejected', 'paid': 'paid'}
    holders = {'submitted': 'Finance', 'pending_cfo': 'the CFO',
               'approved': 'Finance (to pay)'}
    nexts = {'submitted': 'accountant to load the payment',
             'pending_cfo': 'CFO approval', 'approved': 'payment'}
    out = []
    for c in (EC.objects.filter(profile__user=user)
              .select_related('profile').prefetch_related('invoices')):
        bucket = buckets.get(c.status, 'pending')
        # A3: a returned claim must say what to correct, not just "rejected".
        nxt = nexts.get(c.status, '')
        if c.status == 'rejected':
            nxt = (c.reject_reason or '').strip() or 'correct and resubmit'
        # A3/A5: paid claims show the evidence reference where we have one.
        title = (c.category or 'Refund')[:80]
        if c.status == 'paid' and (c.paid_reference or '').strip():
            title = f'{title} · ref {c.paid_reference.strip()[:24]}'
        out.append(TrackedRequest(
            kind='expense', kind_label='Refund', id=str(c.pk),
            title=title,
            amount=float(c.amount or 0), currency=(c.currency or 'BWP'),
            bucket=bucket, status_label=c.get_status_display(),
            holder_label=holders.get(c.status, ''), next_label=nxt,
            submitted_at=_iso(c.submitted_at or c.created_at),
            updated_at=_iso(c.updated_at),
            days_waiting=_days_since(c.updated_at) if bucket in _IN_FLIGHT else None,
            href='/hris/expense-claims',
            timeline=[
                _step('Submitted', c.status != 'draft', c.submitted_at or c.created_at),
                _step('Accountant', c.status not in ('draft', 'submitted'), None),
                _step('CFO approval', c.approved_at, c.approved_at),
                _step('Paid', c.status == 'paid', c.paid_at),
            ],
            # A3: the receipt state, so "missing receipt" is visible here and
            # can drive the Needs-your-action list without a second query.
            has_receipt=bool(c.attachment) or bool(c.invoices.all()),
        ))
    return out


_ADAPTERS = (_leave_pay, _leave, _loan, _petty, _payment, _expense)
_BUCKET_ORDER = {'pending': 0, 'approved': 1, 'draft': 2,
                 'rejected': 3, 'paid': 4, 'cancelled': 5}


def my_requests(user, *, ai_polish: bool = False) -> list[dict]:
    """Every request THIS user submitted, across all five workflows, newest and
    most-active first. `ai_polish=True` runs each in-flight line through DeepSeek
    (PII-free) for friendlier phrasing; default off keeps the list instant."""
    if not user or not getattr(user, 'is_authenticated', False):
        return []
    items: list[TrackedRequest] = []
    for fn in _ADAPTERS:
        try:
            items.extend(fn(user))
        except Exception:                   # noqa: BLE001 — one module must not blank the page
            log.exception('request_tracker: %s failed', getattr(fn, '__name__', fn))

    items.sort(key=lambda r: r.updated_at or '', reverse=True)
    items.sort(key=lambda r: _BUCKET_ORDER.get(r.bucket, 9))

    polished = 0
    for r in items:
        r.holder_standard = _standard_holder(r.bucket, r.holder_label)
        r.stuck = bool(r.days_waiting is not None and r.days_waiting >= _AGING_DAYS)
        done = (r.bucket == 'paid')
        # Only polish in-flight rows, and never more than the cap (fabe L7).
        want_ai = ai_polish and r.bucket in _IN_FLIGHT and polished < _AI_POLISH_CAP
        r.status_line = plain_status_line(
            kind_label=r.kind_label, status_label=r.status_label,
            holder_label=r.holder_label, days_waiting=r.days_waiting,
            next_label=r.next_label, done=done, rejected=(r.bucket == 'rejected'),
            use_ai=want_ai,
        )
        if want_ai:
            polished += 1
    return [r.to_dict() for r in items]


# ── A6 — "Needs your action" ─────────────────────────────────────────────────
# Only things the REQUESTER can act on. Anything sitting on someone else's desk
# is progress, not an action, and listing it would train people to ignore this.
# An adapter that raises is reported as unknown rather than silently dropped —
# a fake all-clear is the one outcome the CFO called out by name.

def my_pending_actions(user) -> dict:
    """{'actions': [...], 'unavailable': ['leave', ...]} — never a false all-clear."""
    if not user or not getattr(user, 'is_authenticated', False):
        return {'actions': [], 'unavailable': []}

    actions: list[dict] = []
    unavailable: list[str] = []

    def _add(kind, label, instruction, href):
        actions.append({'kind': kind, 'label': label,
                        'instruction': instruction, 'href': href})

    # Refunds returned for correction, and submitted refunds with no receipt.
    try:
        from hris.expense_claim_models import ExpenseClaim as EC
        for c in (EC.objects.filter(profile__user=user)
                  .exclude(status__in=['paid', 'approved'])
                  .prefetch_related('invoices')):
            if c.status == 'rejected':
                why = (c.reject_reason or '').strip() or 'It needs a correction.'
                _add('expense', f'Refund returned · {c.category or "refund"}',
                     f'{why} Fix it and send it again.', '/hris/expense-claims')
            elif c.status == 'submitted' and not c.attachment and not c.invoices.all():
                _add('expense', f'Receipt missing · {c.category or "refund"}',
                     'Attach the receipt so Finance can pay this.',
                     '/hris/expense-claims')
    except Exception:                       # noqa: BLE001
        log.exception('pending_actions: expense failed')
        unavailable.append('refunds')

    # Leave that needs a certificate, and drafts never sent.
    try:
        from hris.feature_views import _profile_for
        from hris.models import LeaveRequest
        profile = _profile_for(user)
        if profile is not None:
            for lr in (LeaveRequest.objects.filter(profile=profile,
                                                   status__in=['draft', 'pending'])
                       .select_related('leave_type')):
                if lr.status == 'draft':
                    _add('leave', f'Leave not sent · {lr.leave_type}',
                         'This leave request is still a draft. Send it for approval.',
                         '/hris/leave')
                elif ((getattr(lr.leave_type, 'proof_type', '') or '').strip()
                      and not lr.medical_certificate):
                    _add('leave', f'Certificate missing · {lr.leave_type}',
                         'Upload the certificate or this leave cannot be approved.',
                         '/hris/leave')
    except Exception:                       # noqa: BLE001
        log.exception('pending_actions: leave failed')
        unavailable.append('leave')

    # A leave reversal the employee must respond to.
    try:
        from hris.feature_views import _profile_for
        from hris.leave_reversal_models import LeaveReversal as LR
        rev_profile = _profile_for(user)
        for r in (LR.objects.filter(profile=rev_profile,
                                    status__in=LR.OPEN_STATUSES)
                  if rev_profile is not None else []):
            _add('leave_reversal', 'Leave reversal needs your response',
                 'Your manager has asked to reverse leave. Please respond.',
                 '/hris/leave')
    except Exception:                       # noqa: BLE001
        log.exception('pending_actions: leave reversal failed')
        unavailable.append('leave reversals')

    # Petty-cash vouchers still sitting in draft.
    try:
        from petty_cash.models import PettyCashVoucher as PCV
        for v in PCV.objects.filter(created_by=user, status='draft'):
            _add('petty_cash',
                 f'Petty cash not sent · {(v.description or v.voucher_number or "")[:40]}',
                 'This voucher is still a draft. Send it for signature.',
                 '/petty-cash')
    except Exception:                       # noqa: BLE001
        log.exception('pending_actions: petty cash failed')
        unavailable.append('petty cash')

    return {'actions': actions, 'unavailable': unavailable}
