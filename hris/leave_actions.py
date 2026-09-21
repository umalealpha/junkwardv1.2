"""
hris/leave_actions.py — one-click leave approval from the notification email.

CFO 2026-07-14: "Approving leave should be a seemingly easy exercise." The
approval email carries Approve / Decline buttons that work on a phone or
computer WITHOUT signing in, because a manager reading Outlook on their phone
has no omni session.

Security model — a signed, time-limited token IS the credential:
  * django.core.signing binds {leave_id, approver_user_id} into a tamper-proof
    token (salt 'leave-action', max-age 14 days). It cannot be forged or
    altered without SECRET_KEY.
  * The emailed button opens a GET page that has NO side effect — this defeats
    Outlook Safe-Links / mail-scanner prefetch, which would otherwise "click"
    a direct approve link. The manager then taps Approve on that page (one tap).
  * The POST re-checks the SAME guardrails as the in-app queue (decide_leave):
    approver still active + holds approve_team_leave + is not the requester +
    the request is still pending. Idempotent — a second submit shows the
    already-decided state instead of flipping it again.
"""
from __future__ import annotations

from django.conf import settings
from django.core import signing
from django.http import HttpResponse
from django.utils import timezone
from django.utils.html import escape
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from hris.models import LeaveRequest

_SALT = 'leave-action'
_MAX_AGE = 60 * 60 * 24 * 14  # 14 days

NAVY = '#0D1B2A'
ORANGE = '#F4A623'


def make_leave_action_token(leave_request, approver_user) -> str:
    """Sign {leave_id, approver_user_id} into a URL-safe token."""
    return signing.dumps(
        {'lr': str(leave_request.id), 'u': int(approver_user.id)},
        salt=_SALT,
    )


def action_url(leave_request, approver_user) -> str:
    # Under /hris/api/ so Caddy proxies it to Django (only /api/* and
    # /hris/api/* reach the backend; bare /hris/* goes to the Next frontend).
    base = getattr(settings, 'PUBLIC_BASE_URL', 'https://omni.alphadirect.co.bw').rstrip('/')
    token = make_leave_action_token(leave_request, approver_user)
    return f'{base}/hris/api/leave-action/{token}/'


# ── branded page shell ──────────────────────────────────────────────────────

def _page(title: str, inner: str, *, status: int = 200) -> HttpResponse:
    html = f"""<!DOCTYPE html><html lang="en"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{escape(title)} — Alpha Direct</title>
<style>
  * {{ box-sizing: border-box; }}
  body {{ margin:0; font-family:'Segoe UI',Arial,sans-serif; background:#F3F4F6; color:#1F2937; }}
  .wrap {{ max-width:560px; margin:0 auto; padding:24px 16px; }}
  .card {{ background:#fff; border-radius:14px; overflow:hidden; box-shadow:0 8px 30px rgba(13,27,42,.10); }}
  .head {{ background:{NAVY}; padding:22px 26px; }}
  .head h1 {{ margin:0; color:{ORANGE}; font-size:19px; font-weight:700; }}
  .body {{ padding:24px 26px; }}
  .body h2 {{ margin:0 0 4px; font-size:20px; color:{NAVY}; }}
  .muted {{ color:#6B7280; font-size:13px; }}
  table.kv {{ width:100%; border-collapse:collapse; margin:16px 0; font-size:14px; }}
  table.kv td {{ padding:8px 0; border-bottom:1px solid #EEF0F3; vertical-align:top; }}
  table.kv td:first-child {{ color:#6B7280; width:42%; }}
  table.kv td:last-child {{ font-weight:600; text-align:right; }}
  .btn {{ display:block; width:100%; text-align:center; padding:15px; border:none;
          border-radius:10px; font-size:16px; font-weight:700; cursor:pointer; margin-top:12px; }}
  .btn-ok {{ background:{ORANGE}; color:{NAVY}; }}
  .btn-no {{ background:#DC2626; color:#fff; }}
  textarea {{ width:100%; padding:10px; border:1px solid #D1D5DB; border-radius:8px;
             font-family:inherit; font-size:14px; margin-top:6px; }}
  .pill {{ display:inline-block; padding:3px 12px; border-radius:999px; font-size:13px; font-weight:700; }}
  .pill-ok {{ background:#ECFDF5; color:#059669; }}
  .pill-no {{ background:#FEF2F2; color:#DC2626; }}
  .pill-wait {{ background:#FFFBEB; color:#92400E; }}
  .foot {{ text-align:center; color:#9CA3AF; font-size:11px; padding:16px; }}
</style></head>
<body><div class="wrap"><div class="card">
  <div class="head"><h1>Alpha Direct · Leave Approval</h1></div>
  <div class="body">{inner}</div>
</div><div class="foot">Omni ERP — omni.alphadirect.co.bw</div></div></body></html>"""
    return HttpResponse(html, status=status)


def _leave_summary_rows(lr: LeaveRequest) -> str:
    emp = lr.profile.employee
    lt = lr.leave_type.name if lr.leave_type_id else 'Leave'
    return f"""
      <tr><td>Employee</td><td>{escape(emp.full_name)}</td></tr>
      <tr><td>Leave type</td><td>{escape(lt)}</td></tr>
      <tr><td>When</td><td>{escape(lr.day_breakdown())}</td></tr>
      <tr><td>Reason</td><td>{escape(lr.reason or '—')}</td></tr>"""


# ── views ─────────────────────────────────────────────────────────────────

def _load(token: str):
    """(leave_request, approver_user) or (None, reason_str)."""
    from django.contrib.auth.models import User
    try:
        data = signing.loads(token, salt=_SALT, max_age=_MAX_AGE)
    except signing.SignatureExpired:
        return None, 'This approval link has expired. Please use the leave queue in omni.'
    except signing.BadSignature:
        return None, 'This approval link is not valid.'
    lr = (LeaveRequest.objects
          .filter(pk=data.get('lr'))
          .select_related('leave_type', 'profile__employee', 'profile__employee__user')
          .first())
    approver = User.objects.filter(pk=data.get('u'), is_active=True).first()
    if lr is None:
        return None, 'That leave request no longer exists.'
    if approver is None:
        return None, 'Your account is not active. Please use the leave queue in omni.'
    return lr, approver


def _may_approve(approver, lr) -> str | None:
    """None if allowed, else a human reason string (same rules as decide_leave)."""
    from core.hris_access import ROLE_CAPABILITIES, hris_role
    caps = ROLE_CAPABILITIES.get(hris_role(approver), set())
    if 'approve_team_leave' not in caps:
        return 'Your account cannot approve leave. Please ask HR.'
    emp_user_id = getattr(lr.profile.employee, 'user_id', None)
    if emp_user_id and emp_user_id == approver.id:
        return 'You cannot approve your own leave.'
    return None


@require_http_methods(['GET'])
def leave_action_page(request, token: str):
    """Side-effect-free confirmation page. Safe for mail-scanner prefetch."""
    lr, approver = _load(token)
    if lr is None:
        return _page('Link problem', f'<h2>Sorry</h2><p class="muted">{escape(approver)}</p>', status=400)

    if lr.status not in (LeaveRequest.Status.PENDING, LeaveRequest.Status.DRAFT):
        pill = ('pill-ok' if lr.status == LeaveRequest.Status.APPROVED else 'pill-no')
        return _page('Already decided', f"""
          <h2>Already {escape(lr.get_status_display().lower())}</h2>
          <p class="muted">This request was already handled — nothing more to do.</p>
          <table class="kv">{_leave_summary_rows(lr)}
          <tr><td>Status</td><td><span class="pill {pill}">{escape(lr.get_status_display())}</span></td></tr></table>""")

    blocked = _may_approve(approver, lr)
    if blocked:
        return _page('Cannot approve', f'<h2>Heads up</h2><p class="muted">{escape(blocked)}</p>'
                     f'<table class="kv">{_leave_summary_rows(lr)}</table>', status=403)

    # balances + tasks-at-risk context (read-only)
    from hris.leave_email import balance_context_rows, tasks_at_risk_rows
    bal_html = balance_context_rows(lr)
    task_html = tasks_at_risk_rows(lr)

    return _page('Approve leave', f"""
      <h2>{escape(lr.profile.employee.full_name)}</h2>
      <p class="muted">has applied for leave — approve or decline below.</p>
      <table class="kv">{_leave_summary_rows(lr)}</table>
      {bal_html}
      {task_html}
      <form method="POST" action="/hris/api/leave-action/{escape(token)}/submit/">
        <input type="hidden" name="decision" value="approve">
        <button class="btn btn-ok" type="submit">✓ Approve leave</button>
      </form>
      <form id="decline" method="POST" action="/hris/api/leave-action/{escape(token)}/submit/" style="margin-top:16px;">
        <input type="hidden" name="decision" value="reject">
        <label class="muted">If declining, add a short reason:</label>
        <textarea name="notes" rows="2" placeholder="Reason for declining…"></textarea>
        <button class="btn btn-no" type="submit">✕ Decline</button>
      </form>""")


@csrf_exempt
@require_http_methods(['POST'])
def leave_action_submit(request, token: str):
    """Performs the decision. The signed token is the credential (csrf-exempt)."""
    lr, approver = _load(token)
    if lr is None:
        return _page('Link problem', f'<h2>Sorry</h2><p class="muted">{escape(approver)}</p>', status=400)

    if lr.status not in (LeaveRequest.Status.PENDING, LeaveRequest.Status.DRAFT):
        pill = ('pill-ok' if lr.status == LeaveRequest.Status.APPROVED else 'pill-no')
        return _page('Already decided', f"""
          <h2>Already {escape(lr.get_status_display().lower())}</h2>
          <p class="muted">No change made — someone already handled this one.</p>
          <table class="kv">{_leave_summary_rows(lr)}
          <tr><td>Status</td><td><span class="pill {pill}">{escape(lr.get_status_display())}</span></td></tr></table>""")

    blocked = _may_approve(approver, lr)
    if blocked:
        return _page('Cannot approve', f'<h2>Heads up</h2><p class="muted">{escape(blocked)}</p>', status=403)

    decision = (request.POST.get('decision') or '').strip().lower()
    notes = (request.POST.get('notes') or '').strip()
    if decision not in ('approve', 'reject'):
        return _page('Try again', '<h2>Hmm</h2><p class="muted">No decision was submitted.</p>', status=400)

    # Long-overdue-task gate (CFO 2026-08-07) — the same block the in-app queue
    # applies, so the email route cannot be used to bypass the countersignature.
    if decision == 'approve':
        from hris import exec_signoff_service
        from hris.exec_signoff_models import ExecSignoff
        pending = exec_signoff_service.blocking_signoff(ExecSignoff.Module.LEAVE, lr.pk)
        if pending is not None:
            return _page('Waiting on a signature', f"""
              <h2>Not yet</h2>
              <p class="muted">{escape(exec_signoff_service.block_message(pending))}</p>
              <table class="kv">{_leave_summary_rows(lr)}
              <tr><td>Status</td><td><span class="pill pill-wait">Awaiting CEO / CFO</span></td></tr></table>""",
                         status=409)
        # Discretionary leave (CFO 2026-09-10) — the same belt-and-braces check
        # the in-app queue applies: no SIGNED countersignature, no approval,
        # even if the pending row above was never written.
        from hris import discretionary_leave as _dl
        unsigned = _dl.approval_blocked_reason(lr)
        if unsigned:
            return _page('Waiting on the CFO', f"""
              <h2>Not yet</h2>
              <p class="muted">{escape(unsigned)}</p>
              <table class="kv">{_leave_summary_rows(lr)}
              <tr><td>Status</td><td><span class="pill pill-wait">Awaiting CFO</span></td></tr></table>""",
                         status=409)

    lr.status = (LeaveRequest.Status.APPROVED if decision == 'approve'
                 else LeaveRequest.Status.REFUSED)
    lr.approver = approver
    lr.decided_at = timezone.now()
    if notes:
        lr.decision_notes = notes
    lr.save(update_fields=['status', 'approver', 'decided_at', 'decision_notes', 'updated_at'])

    # Clear the attendance days this leave covers (CFO 2026-08-03). Without this
    # the leave is approved in the register while the same days stay
    # `unjustified` on the daily record — which is what manager_accountability
    # reads, so the person gets chased again for leave that was granted.
    from hris.leave_backfill import backfill_workdays_for_leave
    backfill_workdays_for_leave(lr)

    # Tell the employee the answer (CFO 2026-08-07). The in-app queue does the
    # same; without it here, a manager deciding from their phone left the
    # person with no word at all.
    try:
        from core.notifications import notify_leave_decided
        notify_leave_decided(lr)
    except Exception:   # noqa: BLE001
        pass

    ok = lr.status == LeaveRequest.Status.APPROVED
    return _page('Done', f"""
      <h2>{'✓ Approved' if ok else '✕ Declined'}</h2>
      <p class="muted">{escape(lr.profile.employee.full_name)}'s leave has been
        {'approved' if ok else 'declined'}. They will see the update in omni.</p>
      <table class="kv">{_leave_summary_rows(lr)}
      <tr><td>Decision by</td><td>{escape(approver.get_full_name() or approver.username)}</td></tr>
      <tr><td>Status</td><td><span class="pill {'pill-ok' if ok else 'pill-no'}">{escape(lr.get_status_display())}</span></td></tr></table>""")
