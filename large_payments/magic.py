"""The CEO's four no-login buttons: approve all, refuse, ask, more detail.

These are the WHOLE interface for the CEO — "Arun won't use omni" (CFO
2026-09-12) — so each one has to be complete and honest on its own: say what it
will do before it does it, do exactly that, and tell him plainly what happened.

They plug into core/magic_action.py, which supplies the hard parts and whose
guarantees this module depends on rather than re-implementing:
  * the signed token IS the gate — no login, no session minted;
  * GET is INERT. It renders a confirm page and changes nothing, so Outlook's
    Safe Links prefetching the URL cannot approve a payment run. The action
    happens only on the POST from the Confirm button;
  * single-use: the token's jti is burned in the cache on first POST;
  * 72-hour expiry.

REGISTERED FROM AppConfig.ready(), NOT by editing core/magic_action.py. The
ACTIONS dict is that module's documented extension point, and registering from
here keeps the whole feature inside its own app — nothing preexisting is edited
to add it, and removing the app removes the actions with it.

EVERY HANDLER RE-CHECKS THAT THE CLICKER IS THE CEO. The token names a user, but
a handler that trusted the token alone would let any signed link for any user
drive a payment authorisation if a kind string were ever reused. The identity is
re-derived from the email on every call.
"""
from __future__ import annotations

import logging
from decimal import Decimal

from django.utils import timezone

log = logging.getLogger(__name__)


def _request(ctx):
    from .models import LargePaymentRequest
    rid = ctx.get('r')
    if not rid:
        return None
    return LargePaymentRequest.objects.filter(pk=rid).first()


def _is_ceo(user) -> bool:
    from .ceo_email import ceo_email_address
    return bool(user and (user.email or '').strip().lower()
                == ceo_email_address().strip().lower())


def _summary(req) -> str:
    lines = list(req.lines.all())
    total = sum((ln.amount for ln in lines), Decimal('0.00'))
    return f'{len(lines)} claim payment(s), BWP {total:,.2f}'


#: States in which the CEO can still act. Anything else means somebody already
#: dealt with it, and saying so is better than a second silent decision.
_OPEN = ('sent_to_ceo', 'question')


def _guard(user, ctx):
    """(request, error_message). Shared by all four handlers."""
    if not _is_ceo(user):
        return None, 'Only the Chief Executive Officer can act on this request.'
    req = _request(ctx)
    if req is None:
        return None, 'That authorisation request no longer exists.'
    return req, ''


# ── Approve all ──────────────────────────────────────────────────────────────

def _approve_describe(user, ctx):
    req, err = _guard(user, ctx)
    if err:
        return 'Cannot open this request', err, 'Close'
    detail = (f'{_summary(req)} on request {req.ref}. Approving records your '
              f'authorisation of the whole request and closes it in Omni. '
              f'These payments are already loaded at the bank, so this does not '
              f'itself release any money.')
    if req.status not in _OPEN:
        return ('Already dealt with', f'Request {req.ref} is now '
                f'"{req.get_status_display()}". Nothing further is needed.', 'Close')
    return f'Approve all payments on {req.ref}?', detail, 'Yes, I approve all'


def _approve_act(user, ctx):
    req, err = _guard(user, ctx)
    if err:
        return False, err
    if req.status == 'ceo_approved':
        return True, f'Already approved — thank you. {req.ref} is closed.'
    if req.status not in _OPEN:
        return False, (f'{req.ref} is "{req.get_status_display()}" and can no '
                       'longer be approved here.')

    from .models import LargePaymentRequest
    # Conditional UPDATE, not save(): two taps on a phone, or a reply plus a
    # click, must resolve to ONE approval. The database decides the winner.
    won = (LargePaymentRequest.objects
           .filter(pk=req.pk, status__in=_OPEN)
           .update(status=LargePaymentRequest.Status.CEO_APPROVED,
                   ceo_decided_at=timezone.now(),
                   updated_at=timezone.now()))
    if not won:
        req.refresh_from_db()
        return True, f'Already recorded — {req.ref} is {req.get_status_display()}.'

    req.refresh_from_db()
    _close_tasks(req)
    _tell_the_cfo(req, 'APPROVED by the CEO',
                  f'Mr Iyer approved {req.ref} in full — {_summary(req)}.')
    return True, (f'Approved. {req.ref} is authorised and closed, and Finance has '
                  'been told. Thank you.')


# ── Refuse ───────────────────────────────────────────────────────────────────

def _reject_describe(user, ctx):
    req, err = _guard(user, ctx)
    if err:
        return 'Cannot open this request', err, 'Close'
    if req.status not in _OPEN:
        return ('Already dealt with', f'Request {req.ref} is now '
                f'"{req.get_status_display()}".', 'Close')
    return (f'Refuse {req.ref}?',
            f'{_summary(req)}. Refusing returns the whole request to the CFO and '
            f'Finance. It cannot pull back money already at the bank — it records '
            f'that you did not authorise it, and frees these payments to be put to '
            f'you again on a corrected request.',
            'Yes, refuse it')


def _reject_act(user, ctx):
    req, err = _guard(user, ctx)
    if err:
        return False, err
    if req.status == 'ceo_rejected':
        return True, f'Already refused — {req.ref} has gone back to Finance.'
    if req.status not in _OPEN:
        return False, (f'{req.ref} is "{req.get_status_display()}" and can no '
                       'longer be refused here.')

    from .models import LargePaymentRequest
    won = (LargePaymentRequest.objects
           .filter(pk=req.pk, status__in=_OPEN)
           .update(status=LargePaymentRequest.Status.CEO_REJECTED,
                   ceo_decided_at=timezone.now(),
                   updated_at=timezone.now()))
    if not won:
        req.refresh_from_db()
        return True, f'Already recorded — {req.ref} is {req.get_status_display()}.'

    req.refresh_from_db()
    _close_tasks(req)
    _tell_the_cfo(req, 'REFUSED by the CEO',
                  f'Mr Iyer refused {req.ref} — {_summary(req)}. It is back with '
                  'you. Ask him what he wants changed, then raise a corrected '
                  'request.', urgent=True)
    return True, (f'Refused. {req.ref} has gone back to the CFO and Finance, and '
                  'these payments are free to be put to you again on a corrected '
                  'request.')


# ── Ask one person a question ────────────────────────────────────────────────

def _ask_describe(user, ctx):
    req, err = _guard(user, ctx)
    if err:
        return 'Cannot open this request', err, 'Close'
    who = _target_name(ctx.get('a'))
    return (f'Ask {who} about {req.ref}?',
            f'{_summary(req)}. This raises a task for {who} to come back to you on '
            f'this request, and tells the CFO you are waiting. The request stays '
            f'open — nothing is approved or refused.',
            f'Yes, ask {who}')


def _ask_act(user, ctx):
    req, err = _guard(user, ctx)
    if err:
        return False, err
    if req.status not in _OPEN:
        return False, (f'{req.ref} is "{req.get_status_display()}" — there is '
                       'nothing open to ask about.')

    email = (ctx.get('a') or '').strip().lower()
    who = _target_name(email)
    person = _user_by_email(email)
    if person is None:
        return False, (f'{who} has no active Omni account, so no task could be '
                       'raised. Please reply to the email instead.')

    note = {'asked_at': timezone.now().isoformat(), 'of': email, 'name': who}
    if not _record_question(req, note):
        req.refresh_from_db()
        return False, (f'{req.ref} is "{req.get_status_display()}" — it was decided '
                       'while this page was open, so the question was not raised.')

    _raise_task(
        assignee=person,
        title=f'CEO question on {req.ref} — come back to Mr Iyer',
        body=(f'Mr Iyer has a question about payment authorisation {req.ref} '
              f'({_summary(req)}) and has asked you directly.\n\n'
              f'Call or email him today with the answer, then tell the CFO it is '
              f'done. The authorisation is on hold until he is satisfied.\n\n'
              f'Open it in Omni: Payments -> Large Payment Requests -> {req.ref}'),
        urgent=True,
    )
    _tell_the_cfo(req, f'CEO has asked {who} a question',
                  f'Mr Iyer has asked {who} about {req.ref}. A task is raised and '
                  'the authorisation is on hold until he is satisfied.')
    return True, (f'Asked. {who} has a task to come back to you today, and the CFO '
                  'has been told. The request stays open.')


# ── Ask for more detail ──────────────────────────────────────────────────────

def _detail_describe(user, ctx):
    req, err = _guard(user, ctx)
    if err:
        return 'Cannot open this request', err, 'Close'
    return (f'Ask for more detail on {req.ref}?',
            f'{_summary(req)}. This tells the CFO and Finance you want fuller '
            f'supporting detail before you decide — the claim files, the '
            f'assessments, whatever you need. The request stays open.',
            'Yes, send me more detail')


def _detail_act(user, ctx):
    req, err = _guard(user, ctx)
    if err:
        return False, err
    if req.status not in _OPEN:
        return False, (f'{req.ref} is "{req.get_status_display()}" — there is '
                       'nothing open to add detail to.')

    note = {'asked_at': timezone.now().isoformat(), 'of': 'more_detail',
            'name': 'more supporting detail'}
    if not _record_question(req, note):
        req.refresh_from_db()
        return False, (f'{req.ref} is "{req.get_status_display()}" — it was decided '
                       'while this page was open, so nothing was changed.')

    _tell_the_cfo(req, 'CEO wants more detail',
                  f'Mr Iyer has asked for fuller supporting detail on {req.ref} '
                  f'({_summary(req)}) before he decides. The authorisation is on '
                  'hold.', urgent=True)
    return True, ('Asked. The CFO and Finance have been told you want more detail. '
                  'The request stays open until you decide.')


# ── shared plumbing ──────────────────────────────────────────────────────────

def _record_question(req, note) -> bool:
    """Append a question and hold the request, under a row lock. True if recorded.

    A plain save() here could REGRESS a decided request (Fable 5.1, 2026-09-12):
    the CEO taps Approve on one device and Ask on another, approve wins the
    conditional UPDATE and closes everything, then the ask save() writes
    status='question' back over it — re-opening a request he had already decided,
    with his tasks already closed.

    The single-use jti in core.magic_action does NOT prevent this: the cache is
    LocMem and this runs across several gunicorn workers, so each worker has its
    own idea of which tokens are spent. The database is the only shared truth
    here, so the lock is taken in the database.
    """
    from django.db import transaction
    from .models import LargePaymentRequest

    with transaction.atomic():
        locked = (LargePaymentRequest.objects
                  .select_for_update()
                  .filter(pk=req.pk, status__in=_OPEN)
                  .first())
        if locked is None:
            return False
        locked.ceo_questions = list(locked.ceo_questions or []) + [note]
        locked.status = LargePaymentRequest.Status.QUESTION
        locked.save(update_fields=['ceo_questions', 'status', 'updated_at'])
    req.refresh_from_db()
    return True


def _target_name(email) -> str:
    from .ceo_email import QUESTION_TARGETS
    email = (email or '').strip().lower()
    for addr, name in QUESTION_TARGETS:
        if addr == email:
            return name
    return email or 'Finance'


def _user_by_email(email):
    from django.contrib.auth import get_user_model
    if not email:
        return None
    return (get_user_model().objects
            .filter(email__iexact=email, is_active=True).first())


def _raise_task(*, assignee, title, body, urgent=False):
    """Best effort. A notification that fails must never undo the decision."""
    from core.models import OmniTask
    from .permissions import cfo_user
    try:
        assigner = cfo_user() or assignee
        return OmniTask.objects.create(
            assigner=assigner, assignee=assignee,
            title=title[:200], body=body,
            priority=OmniTask.Priority.URGENT if urgent else OmniTask.Priority.HIGH,
            due_at=timezone.localtime().date(),
            source='large_payment_ceo',
        )
    except Exception:                                     # noqa: BLE001
        log.exception('large_payments: could not raise the task "%s"', title[:60])
        return None


def _tell_the_cfo(req, headline, body, *, urgent=False):
    from .permissions import cfo_user
    cfo = cfo_user()
    if cfo is None:
        log.warning('large_payments: no CFO account to tell about %s', req.ref)
        return
    _raise_task(assignee=cfo, title=f'{headline} — {req.ref}', body=body,
                urgent=urgent)


def _close_tasks(req):
    """Close the CFO's and the CEO's tasks once the CEO has decided."""
    from core.models import OmniTask
    now = timezone.now()
    for task in (req.cfo_task, req.ceo_task):
        if task and task.status not in (OmniTask.Status.DONE,
                                        OmniTask.Status.CANCELLED):
            try:
                task.status = OmniTask.Status.DONE
                task.completed_at = now
                task.save(update_fields=['status', 'completed_at', 'updated_at'])
            except Exception:                             # noqa: BLE001
                log.exception('large_payments: could not close task on %s', req.ref)


#: The four handlers, in core.magic_action's shape.
HANDLERS = {
    'lp_approve_all': {'describe': _approve_describe, 'act': _approve_act},
    'lp_reject':      {'describe': _reject_describe,  'act': _reject_act},
    'lp_ask':         {'describe': _ask_describe,     'act': _ask_act},
    'lp_more_detail': {'describe': _detail_describe,  'act': _detail_act},
}


def register() -> None:
    """Add the handlers to core.magic_action.ACTIONS. Idempotent.

    setdefault alone would SILENTLY lose to a future action registered under the
    same name, and the symptom would be the CEO's Approve button quietly doing
    somebody else's thing. A clash is a programming error, so it is raised at
    startup rather than discovered in production (Fable 5.1, 2026-09-12).
    """
    from core.magic_action import ACTIONS
    for kind, handler in HANDLERS.items():
        existing = ACTIONS.get(kind)
        if existing is not None and existing is not handler:
            raise RuntimeError(
                f'magic action "{kind}" is already registered by something else; '
                'rename the large_payments action rather than shadowing it.')
        ACTIONS[kind] = handler
