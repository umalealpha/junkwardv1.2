"""
hris/exec_signoff_actions.py — the CEO/CFO one-click countersignature page
(CFO 2026-08-07).

Same security model as hris.leave_actions: a signed, time-limited token binds
{signoff_id, signer_user_id}; the GET page has NO side effect so mail scanners
cannot sign anything by prefetching; the POST re-checks that the signer still
holds a CEO/CFO title and that the record is still pending.
"""
from __future__ import annotations

from django.conf import settings
from django.core import signing
from django.utils.html import escape
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from hris import exec_signoff_service as svc
from hris.exec_signoff_models import ExecSignoff
from hris.oneclick_page import page

_SALT = 'exec-signoff'
_MAX_AGE = 60 * 60 * 24 * 14  # 14 days
_HEADER = 'Executive Sign-off'


def make_token(signoff, signer_user) -> str:
    return signing.dumps({'s': str(signoff.id), 'u': int(signer_user.id)}, salt=_SALT)


def action_url(signoff, signer_user) -> str:
    # Under /hris/api/ so Caddy proxies it to Django (bare /hris/* is the Next app).
    base = getattr(settings, 'PUBLIC_BASE_URL', 'https://omni.alphadirect.co.bw').rstrip('/')
    return f'{base}/hris/api/exec-signoff/{make_token(signoff, signer_user)}/'


def _load(token: str):
    """(signoff, signer_user) or (None, reason_str)."""
    from django.contrib.auth.models import User
    try:
        data = signing.loads(token, salt=_SALT, max_age=_MAX_AGE)
    except signing.SignatureExpired:
        return None, 'This sign-off link has expired. Please use omni instead.'
    except signing.BadSignature:
        return None, 'This sign-off link is not valid.'
    so = ExecSignoff.objects.filter(pk=data.get('s')).first()
    signer = User.objects.filter(pk=data.get('u'), is_active=True).first()
    if so is None:
        return None, 'That request no longer exists.'
    if signer is None:
        return None, 'Your account is not active. Please use omni instead.'
    if not svc.user_can_sign(signer, so):
        return None, ('Only the CFO can sign this.' if so.cfo_only
                      else 'Only the CEO or CFO can sign this.')
    return so, signer


def _summary_rows(so: ExecSignoff) -> str:
    return f"""
      <tr><td>Person</td><td>{escape(so.applicant_name)}</td></tr>
      <tr><td>Applying for</td><td>{escape(so.get_module_display())}</td></tr>
      <tr><td>Applied</td><td>{escape(so.created_at.strftime('%d %b %Y %H:%M'))}</td></tr>"""


def _overdue_block(so: ExecSignoff) -> str:
    tasks = (so.overdue_snapshot or {}).get('tasks') or []
    if not tasks:
        return ''
    items = ''.join(
        f'<li>{escape(t.get("title") or "—")} — due {escape(str(t.get("due") or "?"))}, '
        f'<b>{int(t.get("days_overdue") or 0)} days late</b></li>'
        for t in tasks[:10])
    more = '' if len(tasks) <= 10 else f'<li>… and {len(tasks) - 10} more</li>'
    return (f'<div class="warn"><b>Why this needs your signature</b>'
            f'<ul>{items}{more}</ul></div>')


def _leave_policy_block(so: ExecSignoff) -> str:
    """Everything the applicant answered, plus how often they use this type.

    The CFO signs from his phone, so the answers have to be ON the page — a
    link back into Omni would defeat the point (CFO 2026-09-10).
    """
    if so.kind != ExecSignoff.Kind.LEAVE_POLICY:
        return ''
    from hris import discretionary_leave as dl
    from hris.models import LeaveRequest
    lr = LeaveRequest.objects.select_related(
        'leave_type', 'profile__employee').filter(pk=so.object_id).first()
    if lr is None:
        return ''
    code = lr.leave_type.code if lr.leave_type_id else ''
    rows = ''.join(
        f'<tr><td>{escape(a["label"])}</td><td>{escape(a["value"])}</td></tr>'
        for a in dl.display_answers(code, lr.policy_answers))
    hist = dl.history_for(lr.profile, code)
    warn = ''
    if hist['count'] > 1:
        warn = (f'<div class="warn"><b>Pattern</b><ul><li>This is request '
                f'<b>#{hist["count"]}</b> of {escape(lr.leave_type.name)} in the '
                f'last {hist["months"]} months — {hist["days"]:g} day(s) in total.'
                f'</li></ul></div>')
    return (f'{warn}'
            f'<div class="warn"><b>Dates</b><ul><li>{escape(str(lr.start_date))} → '
            f'{escape(str(lr.end_date))} · {escape(lr.day_breakdown())}</li></ul></div>'
            f'<table class="kv">{rows}'
            f'<tr><td>Their motivation</td><td>{escape(lr.reason or "—")}</td></tr>'
            f'<tr><td>Acknowledged</td><td>'
            f'{"Yes — stays at work until approved" if lr.policy_ack else "NO"}</td></tr>'
            f'</table>')


def _decided_page(so: ExecSignoff, *, note: str):
    ok = so.status == ExecSignoff.Status.APPROVED
    return page(_HEADER, 'Already decided', f"""
      <h2>Already {escape(so.get_status_display().lower())}</h2>
      <p class="muted">{escape(note)}</p>
      <table class="kv">{_summary_rows(so)}
      <tr><td>Status</td><td><span class="pill {'pill-ok' if ok else 'pill-no'}">
        {escape(so.get_status_display())}</span></td></tr></table>""")


@require_http_methods(['GET'])
def exec_signoff_page(request, token: str):
    """Side-effect-free confirmation page. Safe for mail-scanner prefetch."""
    so, signer = _load(token)
    if so is None:
        return page(_HEADER, 'Link problem',
                    f'<h2>Sorry</h2><p class="muted">{escape(signer)}</p>', status=400)
    if not so.is_pending:
        return _decided_page(so, note='This one was already handled — nothing more to do.')

    if so.kind == ExecSignoff.Kind.LEAVE_POLICY:
        lead = ('has applied for leave that is granted at the company\'s '
                'discretion, not earned. Their manager cannot approve it until '
                'you sign. Their answers are below.')
    elif (so.overdue_snapshot or {}).get('check_failed'):
        lead = (f'has applied for {escape(so.get_module_display().lower())}. The '
                f'overdue-work check could not run, so this is held for your eye '
                f'rather than passed through unchecked. Nothing is known to be wrong.')
    else:
        lead = (f'has applied for {escape(so.get_module_display().lower())} while carrying '
                f'work that is past its due date. Their manager cannot approve it until you sign.')
    return page(_HEADER, 'Sign off', f"""
      <h2>{escape(so.applicant_name)}</h2>
      <p class="muted">{lead}</p>
      <table class="kv">{_summary_rows(so)}</table>
      {_overdue_block(so)}
      {_leave_policy_block(so)}
      <form method="POST" action="/hris/api/exec-signoff/{escape(token)}/submit/">
        <input type="hidden" name="decision" value="approve">
        <button class="btn btn-ok" type="submit">✓ Allow it to go ahead</button>
      </form>
      <form method="POST" action="/hris/api/exec-signoff/{escape(token)}/submit/" style="margin-top:16px;">
        <input type="hidden" name="decision" value="decline">
        <label class="fld">If declining, add a short reason:</label>
        <textarea name="notes" rows="2" placeholder="Reason…"></textarea>
        <button class="btn btn-no" type="submit">{
            '✕ Decline — they must work' if so.kind == ExecSignoff.Kind.LEAVE_POLICY
            else '✕ Decline — clear the tasks first'}</button>
      </form>""")


@csrf_exempt
@require_http_methods(['POST'])
def exec_signoff_submit(request, token: str):
    so, signer = _load(token)
    if so is None:
        return page(_HEADER, 'Link problem',
                    f'<h2>Sorry</h2><p class="muted">{escape(signer)}</p>', status=400)
    if not so.is_pending:
        return _decided_page(so, note='No change made — someone already handled this one.')

    decision = (request.POST.get('decision') or '').strip().lower()
    notes = (request.POST.get('notes') or '').strip()
    if decision not in ('approve', 'decline'):
        return page(_HEADER, 'Try again',
                    '<h2>Hmm</h2><p class="muted">No decision was submitted.</p>', status=400)

    try:
        svc.decide(so, signer, decision == 'approve', notes)
    except svc.SignoffRefused as exc:
        return page(_HEADER, 'Cannot sign',
                    f'<h2>Heads up</h2><p class="muted">{escape(str(exc))}</p>', status=409)
    # A decline kills the application outright — the person is told to clear
    # their overdue work first, rather than leaving it hanging for a manager.
    if so.status == ExecSignoff.Status.DECLINED:
        _refuse_underlying(so)

    ok = so.status == ExecSignoff.Status.APPROVED
    tail = ('Their manager can now approve it in the normal way.' if ok
            else 'The application has been declined and they have been told why.')
    return page(_HEADER, 'Done', f"""
      <h2>{'✓ Signed' if ok else '✕ Declined'}</h2>
      <p class="muted">{escape(tail)}</p>
      <table class="kv">{_summary_rows(so)}
      <tr><td>Decision by</td><td>{escape(signer.get_full_name() or signer.username)}</td></tr>
      <tr><td>Status</td><td><span class="pill {'pill-ok' if ok else 'pill-no'}">
        {escape(so.get_status_display())}</span></td></tr></table>""")


def _refuse_underlying(so: ExecSignoff) -> None:
    """Push the decline through to the application itself.

    Every module, not just leave — a declined loan or incentive left sitting at
    "pending" would hang there for good with nobody told (DeepSeek review round
    2, 2026-08-07). Best-effort: a failure here is logged, and the decline still
    blocks the approval either way (see exec_signoff_service.blocking_signoff).
    """
    import logging
    from django.utils import timezone
    log = logging.getLogger(__name__)
    who = ((so.decided_by.get_full_name() or so.decided_by.username)
           if so.decided_by_id else 'the executive')
    reason = (f'Declined by {who} — overdue work must be cleared first.'
              + (f' {so.decision_notes}' if so.decision_notes else ''))
    try:
        if so.module == ExecSignoff.Module.LEAVE:
            from hris.models import LeaveRequest
            lr = LeaveRequest.objects.filter(pk=so.object_id).first()
            if lr and lr.status in (LeaveRequest.Status.PENDING, LeaveRequest.Status.DRAFT):
                lr.status = LeaveRequest.Status.REFUSED
                lr.decided_at = timezone.now()
                lr.decision_notes = reason
                lr.save(update_fields=['status', 'decided_at', 'decision_notes', 'updated_at'])
                from core import notifications
                notifications.notify_leave_decided(lr)

        elif so.module == ExecSignoff.Module.LOAN:
            from staff_loans.models import StaffLoanApplication
            app = StaffLoanApplication.objects.filter(pk=so.object_id).first()
            if app and app.status == StaffLoanApplication.Status.PENDING_CFO:
                app.status = StaffLoanApplication.Status.DECLINED
                app.decline_reason = reason
                app.cfo_decided_by = so.decided_by
                app.cfo_decided_at = timezone.now()
                app.save(update_fields=['status', 'decline_reason', 'cfo_decided_by',
                                        'cfo_decided_at', 'updated_at'])

            _tell_applicant(so, 'staff loan', reason)

        elif so.module == ExecSignoff.Module.INCENTIVE:
            from hris.incentive_models import IncentiveRequest
            req = IncentiveRequest.objects.filter(pk=so.object_id).first()
            if req and req.status == IncentiveRequest.Status.PENDING:
                req.status = IncentiveRequest.Status.REJECTED
                req.rejected_by = so.decided_by
                req.rejected_at = timezone.now()
                req.decision_notes = reason
                req.save(update_fields=['status', 'rejected_by', 'rejected_at',
                                        'decision_notes', 'updated_at'])
                _tell_applicant(so, 'incentive request', reason)
    except Exception as exc:      # noqa: BLE001
        log.warning('Could not apply exec decline to %s %s: %s', so.module, so.object_id, exc)


def _tell_applicant(so: ExecSignoff, what: str, reason: str) -> None:
    """A decline that kills the application must reach the person who applied —
    on every module, not just the one that happened to be wired first (Fable
    review 2026-08-07). Leave has its own richer notice; this covers loans and
    incentives, which had no applicant notification on ANY decline path.
    Best-effort: never raises into the signer's page.
    """
    import logging
    log = logging.getLogger(__name__)
    try:
        to = (getattr(so.applicant, 'email', '') or '').strip()
        if not to:
            return
        from core.notifications import send_html_with_cfo_cc
        NAVY, ORANGE = '#0D1B2A', '#F4A623'
        inner = (
            f'<h2 style="margin:0 0 6px;font-size:20px;color:#DC2626;">'
            f'Your {escape(what)} was declined</h2>'
            f'<p style="color:#374151;font-size:14px;margin:0 0 10px;">{escape(reason)}</p>'
            f'<p style="color:#6B7280;font-size:13px;margin:0;">Clear the work that is past '
            f'its due date — or agree a new date with whoever set it — then apply again.</p>')
        html = (f'<!DOCTYPE html><html><head><meta charset="utf-8">'
                f'<meta name="viewport" content="width=device-width, initial-scale=1"></head>'
                f'<body style="margin:0;background:#F3F4F6;font-family:\'Segoe UI\',Arial,sans-serif;">'
                f'<div style="max-width:600px;margin:0 auto;padding:20px 12px;">'
                f'<div style="background:#fff;border-radius:14px;overflow:hidden;">'
                f'<div style="background:{NAVY};padding:20px 26px;">'
                f'<div style="color:{ORANGE};font-size:18px;font-weight:700;">'
                f'Alpha Direct · {escape(what.title())}</div></div>'
                f'<div style="padding:22px 26px;">{inner}</div></div>'
                f'<p style="text-align:center;color:#9CA3AF;font-size:11px;margin-top:14px;">'
                f'Omni ERP — omni.alphadirect.co.bw</p></div></body></html>')
        send_html_with_cfo_cc(
            f'Your {what} was declined', html, [to],
            text_fallback=reason, cc=None, cc_cfo=False)
    except Exception as exc:      # noqa: BLE001
        log.warning('Could not tell the applicant about the %s decline: %s', so.module, exc)
