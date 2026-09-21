"""
taskboard/payment_amend.py

Amend or cancel a payment BEFORE finance sign-off (Kelvin Kimani spec
2026-09-08).

A Payment Authorisation Request had no clean way to correct or pull a payment
once it was raised — the inputter was stuck with what they typed, so a wrong
amount or an invoice that should not be paid meant abandoning the whole request
and re-raising it. This gives the inputter their own window to fix it.

Two levels, because "a payment" means both things on this request:
  a single LINE     — one invoice within the request;
  the whole REQUEST — every payment on it.

The rules this module exists to enforce, all of them from the spec:

  THE WINDOW      Only while the request is pre-sign-off. The moment finance
                  signs off it locks; changing a signed-off request is
                  recall/reject by finance and is a different process.
  RECALCULATE     Every amend re-derives TOTAL PAYABLE (and therefore the
                  Section B liquidity position, which is computed from it) "so
                  the figures never drift from the lines behind them".
  RE-CHECK        Every amend invalidates a completed cross-check, so a request
                  cannot be checked, quietly changed, then signed off on a stale
                  confirmation.
  LOG             Field, before, after, who, when — on every amend.
  REASON          Required on a cancel only. The before/after log is the record
                  of what changed; the "why" is demanded where a payment is
                  pulled entirely, because that is what an auditor asks about.
  NEVER DELETE    A cancelled line or request is FLAGGED and kept in full. No
                  row and no line is ever erased.
  ATTRIBUTION     "Amended by / Cancelled by [Name] · [Department]" comes from
                  the logged-in user, never typed, with a timestamp.

OMNI MOVES NO MONEY. Amending or cancelling here changes a workflow record;
money leaves only through FNB / First Capital with the CFO's phone 2-factor.
"""
from __future__ import annotations

import logging
from decimal import Decimal

from django.db import transaction
from django.utils import timezone

from taskboard.models import PaymentRequest, PaymentRequestChange, money_dec

log = logging.getLogger(__name__)

#: Line fields an inputter may correct, exactly the spec's list: "amount,
#: invoice number, invoice date, due date, GL code, description". Anything not
#: named here is refused rather than quietly ignored — a field that looks
#: editable and silently is not is worse than one that says no.
LINE_FIELDS = ('amount', 'invoice_number', 'invoice_date', 'due_date',
               'gl_code', 'description')

#: Request-level fields: "the payment date and the processing method", plus the
#: subject (it titles the POP) and the payee. The payee is called out in the
#: spec as the higher-risk amend and is flagged distinctly below.
REQUEST_FIELDS = ('payment_date', 'processing_method', 'subject', 'payee')

#: Editing the beneficiary is the classic payment-redirection risk, so it is
#: never an ordinary field edit.
HIGH_RISK_FIELDS = ('payee',)

#: The money fields, compared as Decimal so 1250 and "1250.00" are not logged as
#: a change. money_dec is the ONE money parser for this app.
MONEY_FIELDS = ('amount',)


class AmendError(Exception):
    """A refusal with a message meant for the person reading the screen."""

    def __init__(self, detail: str, *, control: str = 'PAY-AMEND-01', status: int = 400):
        super().__init__(detail)
        self.detail = detail
        self.control = control
        self.status = status


def actor_bits(user) -> tuple:
    """(name, department) for the attribution line — read, never typed."""
    name = (getattr(user, 'get_full_name', lambda: '')()
            or getattr(user, 'username', '') or '')
    dept = ''
    try:
        dept = (getattr(getattr(user, 'profile', None), 'department', '') or '')
    except Exception:                                            # noqa: BLE001
        # A missing profile must never stop a correction being recorded; the
        # name alone is still a usable trail.
        log.warning('amend: no profile for user %s', getattr(user, 'pk', '?'))
    return name[:160], dept[:100]


def may_amend(user, pr: PaymentRequest) -> bool:
    """Who may amend or cancel in the pre-sign-off state.

    ⚠️ The spec leaves this to OMNI's access model rather than deciding it:
    "Who may amend or cancel is an access-control question, not decided here …
    The intent to carry into that model: a request should not be quietly altered
    by a user unrelated to it."

    Built to that intent, and no wider: the person who raised it, a finance
    approver, and the CFO. Flagged for the CFO to confirm.
    """
    from taskboard.payment_views import _is_cfo, _is_first_approver
    if pr.created_by_id and user.id == pr.created_by_id:
        return True
    return bool(_is_cfo(user) or _is_first_approver(user))


def _normalise(field: str, value) -> str:
    """The stored form of a value, so a no-op edit is not logged as a change."""
    if field in MONEY_FIELDS:
        return f'{money_dec(value):.2f}'
    return str('' if value is None else value).strip()


def _invalidate_cross_check(pr: PaymentRequest) -> list:
    """Force a fresh cross-check before sign-off, and say what was cleared.

    "Invalidates any completed cross-check … so a request can't be checked,
    quietly changed, then signed off on a stale confirmation."

    Two kinds of confirmation exist on a request today, and they behave
    differently:

      The sign-off acknowledgements (PAY-BANK-ACK / PAY-BANK-DOC / PAY-POP-ACK)
      are NOT stored as "done" — every attempt at sign-off re-derives them from
      the request's current values, so an amend invalidates them for free. That
      is by design and is the reason there is nothing to clear here.

      A committee decision IS durable state saying "this was checked". If the
      pack it was taken on has since changed, that decision no longer describes
      what would be paid, so it is cleared and the committee decides again.
    """
    cleared = []
    if pr.exception_decision:
        pr.exception_decision = ''
        pr.exception_decided_at = None
        cleared.append('the committee decision')
    return cleared


def _log(pr, *, action, user, line=None, field='', before='', after='', reason=''):
    name, dept = actor_bits(user)
    return PaymentRequestChange.objects.create(
        request=pr, action=action, line=line, field=field,
        value_before=str(before)[:2000], value_after=str(after)[:2000],
        reason=reason[:2000], actor=user, actor_name=name, actor_department=dept)


def _reject_locked(pr):
    reason = pr.amend_lock_reason()
    if reason:
        raise AmendError(reason, control='PAY-AMEND-LOCK', status=409)


def _line_at(pr, line_no: int) -> dict:
    lines = pr.line_items or []
    if not isinstance(line_no, int) or line_no < 1 or line_no > len(lines):
        raise AmendError(f'There is no line {line_no} on this request.')
    ln = lines[line_no - 1]
    if not isinstance(ln, dict):
        raise AmendError(f'Line {line_no} cannot be edited.')
    return ln


@transaction.atomic
def amend(pr: PaymentRequest, user, *, line_changes=None, request_changes=None) -> dict:
    """Apply an amendment and return what it did.

    line_changes: [{'line': 1, 'field': 'amount', 'value': '1500.00'}, ...]
    request_changes: {'payment_date': '2026-09-10', ...}

    Returns {'changes': [...], 'total': Decimal, 'cross_check_cleared': [...],
             'high_risk': [...]}.
    """
    _reject_locked(pr)
    if not may_amend(user, pr):
        raise AmendError('You cannot change this payment request. Ask the person '
                         'who raised it, or a finance approver.',
                         control='PAY-AMEND-PERM', status=403)

    logged, high_risk = [], []
    lines = list(pr.line_items or [])

    for ch in (line_changes or []):
        if not isinstance(ch, dict):
            raise AmendError('Each line change needs a line, a field and a value.')
        field = str(ch.get('field') or '').strip()
        if field not in LINE_FIELDS:
            raise AmendError(
                f'"{field}" is not a field that can be corrected on a line. '
                f'Editable: {", ".join(LINE_FIELDS)}.')
        try:
            line_no = int(ch.get('line'))
        except (TypeError, ValueError):
            raise AmendError('Say which line number to change.')
        ln = dict(_line_at(pr, line_no))
        if ln.get('cancelled'):
            raise AmendError(f'Line {line_no} is cancelled. A cancelled line is '
                             'kept as the record and is not edited.')
        before = _normalise(field, ln.get(field))
        after = _normalise(field, ch.get('value'))
        if field == 'amount' and money_dec(ch.get('value')) <= 0:
            raise AmendError(f'Line {line_no}: an amount must be more than zero. '
                             'To drop the invoice, cancel the line instead — that '
                             'keeps it on the record.')
        if before == after:
            continue                       # a no-op is not a change; do not log it
        ln[field] = after
        lines[line_no - 1] = ln
        logged.append(_log(pr, action=PaymentRequestChange.Action.AMEND, user=user,
                           line=line_no, field=field, before=before, after=after))

    for field, value in (request_changes or {}).items():
        field = str(field).strip()
        if field not in REQUEST_FIELDS:
            raise AmendError(
                f'"{field}" is not a field that can be corrected on the request. '
                f'Editable: {", ".join(REQUEST_FIELDS)}.')
        if field == 'processing_method' and value not in dict(
                PaymentRequest.ProcessingMethod.choices):
            raise AmendError('The processing method is either bulk or individual.')
        if field == 'subject' and not str(value or '').strip():
            raise AmendError('The subject titles the proof of payment, so it '
                             'cannot be blanked.')
        before = _normalise(field, getattr(pr, field))
        after = _normalise(field, value)
        if before == after:
            continue
        if field == 'payment_date':
            from taskboard.payment_views import _parse_iso
            parsed = _parse_iso(after)
            if after and parsed is None:
                raise AmendError('The payment date must be a date, YYYY-MM-DD.')
            setattr(pr, field, parsed)
        else:
            setattr(pr, field, after)
        if field in HIGH_RISK_FIELDS:
            high_risk.append(field)
        logged.append(_log(pr, action=PaymentRequestChange.Action.AMEND, user=user,
                           field=field, before=before, after=after))

    if not logged:
        raise AmendError('Nothing changed.')

    pr.line_items = lines
    pr.recalculate_total()
    cleared = _invalidate_cross_check(pr)
    _resave(pr)
    return {'changes': logged, 'total': pr.total, 'cross_check_cleared': cleared,
            'high_risk': high_risk}


@transaction.atomic
def cancel_line(pr: PaymentRequest, user, *, line_no: int, reason: str) -> dict:
    """Pull ONE invoice off the request. Flagged and kept, never deleted."""
    _reject_locked(pr)
    if not may_amend(user, pr):
        raise AmendError('You cannot cancel a line on this payment request.',
                         control='PAY-AMEND-PERM', status=403)
    reason = (reason or '').strip()
    if len(reason) < 5:
        raise AmendError('Say briefly why this payment is being pulled. A '
                         'cancelled payment is exactly what an auditor asks '
                         '"why" about, so the reason goes on the record.',
                         control='PAY-CANCEL-REASON')

    ln = dict(_line_at(pr, line_no))
    if ln.get('cancelled'):
        raise AmendError(f'Line {line_no} is already cancelled.')

    name, dept = actor_bits(user)
    now = timezone.now()
    lines = list(pr.line_items or [])
    # FLAGGED, not removed. The description, amount, invoice number and dates
    # all stay exactly as they were — the record of what was nearly paid.
    lines[line_no - 1] = {**ln, 'cancelled': True, 'cancelled_reason': reason[:2000],
                          'cancelled_by': name, 'cancelled_department': dept,
                          'cancelled_at': now.isoformat()}
    pr.line_items = lines
    pr.recalculate_total()
    cleared = _invalidate_cross_check(pr)

    row = _log(pr, action=PaymentRequestChange.Action.CANCEL_LINE, user=user,
               line=line_no, before=_normalise('amount', ln.get('amount')),
               after='cancelled', field='amount', reason=reason)

    # "If cancelling a line empties the request (nothing left to pay), the
    # request itself can't proceed to sign-off and should fall to a cancelled
    # state." Its lines are all still there — nothing was erased.
    emptied = not PaymentRequest.payable_lines(pr.line_items)
    if emptied:
        pr.status = PaymentRequest.Status.CANCELLED
        pr.cancelled_reason = (
            f'Every line was cancelled; nothing is left to pay. Last reason: {reason}'
        )[:2000]
        pr.cancelled_by = user
        pr.cancelled_at = now
        _log(pr, action=PaymentRequestChange.Action.CANCEL_REQUEST, user=user,
             reason=pr.cancelled_reason)
    _resave(pr, cancelled=True)
    return {'change': row, 'total': pr.total, 'emptied': emptied,
            'cross_check_cleared': cleared, 'status': pr.status}


@transaction.atomic
def cancel_request(pr: PaymentRequest, user, *, reason: str) -> dict:
    """Void the whole request. Every line is kept, flagged cancelled."""
    _reject_locked(pr)
    if not may_amend(user, pr):
        raise AmendError('You cannot cancel this payment request.',
                         control='PAY-AMEND-PERM', status=403)
    reason = (reason or '').strip()
    if len(reason) < 5:
        raise AmendError('Say briefly why this request is being pulled.',
                         control='PAY-CANCEL-REASON')

    name, dept = actor_bits(user)
    now = timezone.now()
    pr.line_items = [
        ({**ln, 'cancelled': True, 'cancelled_reason': reason[:2000],
          'cancelled_by': name, 'cancelled_department': dept,
          'cancelled_at': now.isoformat()}
         if isinstance(ln, dict) and not ln.get('cancelled') else ln)
        for ln in (pr.line_items or [])
    ]
    # The total falls to zero because nothing on it is payable any more — but
    # every line, amount and invoice number is still on the record.
    pr.recalculate_total()
    pr.status = PaymentRequest.Status.CANCELLED
    pr.cancelled_reason = reason[:2000]
    pr.cancelled_by = user
    pr.cancelled_at = now
    _invalidate_cross_check(pr)
    row = _log(pr, action=PaymentRequestChange.Action.CANCEL_REQUEST, user=user,
               reason=reason)
    _resave(pr, cancelled=True)

    # The authorisation task goes with it — an approver should not be left
    # holding a task for a payment that no longer exists.
    task = pr.task
    if task is not None:
        from core.models import OmniTask
        if task.status != OmniTask.Status.CANCELLED:
            task.status = OmniTask.Status.CANCELLED
            task.completed_at = now
            task.save(update_fields=['status', 'completed_at', 'updated_at'])
    return {'change': row, 'total': pr.total, 'status': pr.status}


def _resave(pr, *, cancelled: bool = False) -> None:
    """Persist the amended request and re-render the pack from the new figures.

    The pack is re-rendered here, not left stale: the approver reads
    formatted_html, so a request amended from 16,556.09 to 14,000.00 that still
    printed the old total would be the worst possible outcome of this feature.
    """
    fields = ['line_items', 'total', 'payment_date', 'processing_method',
              'subject', 'payee', 'exception_decision', 'exception_decided_at',
              'formatted_html', 'status', 'updated_at']
    if cancelled:
        fields += ['cancelled_reason', 'cancelled_by', 'cancelled_at']
    try:
        from taskboard.payment_views import _pr_public_dict, _render_html
        pr.formatted_html = _render_html(_pr_public_dict(pr))
    except Exception:                                            # noqa: BLE001
        # A render failure must not lose the correction — the figures are the
        # record; the pack is a view of them and is rebuilt on next read.
        log.warning('amend: could not re-render the pack for %s', pr.ref,
                    exc_info=True)
    pr.save(update_fields=sorted(set(fields)))


def change_rows(pr: PaymentRequest) -> list:
    """The readable history for the screen: what changed, by whom, when, why."""
    return [{
        'id': str(c.id),
        'action': c.action,
        'action_label': c.get_action_display(),
        'line': c.line,
        'field': c.field,
        'value_before': c.value_before,
        'value_after': c.value_after,
        'reason': c.reason,
        'attribution': c.attribution,
        'at': c.created_at.isoformat(),
    } for c in pr.changes.all()]
