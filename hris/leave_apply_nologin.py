"""
hris/leave_apply_nologin.py — apply for leave from an email link, WITHOUT signing in.

CFO 2026-08-07 (approved from a shortlist): approving leave has been one tap from
Outlook since 2026-07-14, but *asking* for it still meant finding a computer and
signing in. Everyone does this every month, so it was the biggest remaining piece
of friction after the locked-out catch-22.

Security — DELIBERATELY DIFFERENT from the report-a-problem link:
  * That form uses ONE shared token, because a bug report only needs an audience
    gate; whoever clicks types their own email and the worst case is a duplicate
    report. **Leave is different: it files a request in a named person's name and
    spends their balance.** So this token is minted PER PERSON — signing.dumps
    binds the profile id, exactly like one-click leave approval binds the
    approver. A shared link here would let anyone book anyone's leave.
  * Max age 60 days, so a forwarded old email cannot be replayed indefinitely.
  * GET has NO side effect (Outlook Safe-Links prefetches every link in an email).
  * POST is csrf-exempt — the signed token is the credential.

Everything downstream is the in-app path: the same LeaveRequest, PENDING, the same
compute_days() in save(), and the same notify_leave_pending_approval() so the
manager gets the usual one-tap approve email. Nothing here bypasses the approval
chain — it only removes the sign-in from the front of it.
"""
from __future__ import annotations

import logging
from datetime import date
from decimal import Decimal

from django.conf import settings
from django.core import signing
from django.db import transaction
from django.http import HttpResponse
from django.utils.html import escape
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from hris.models import LeaveRequest

log = logging.getLogger(__name__)

_SALT = 'leave-apply'
_MAX_AGE = 60 * 60 * 24 * 60          # 60 days

NAVY = '#0D1B2A'
ORANGE = '#F4A623'
RED = '#C62828'


def make_leave_apply_token(profile) -> str:
    return signing.dumps({'p': str(profile.id)}, salt=_SALT)


def leave_apply_url(profile) -> str:
    """Under /hris/api/ so Caddy proxies it to Django (bare /hris/* is Next)."""
    base = getattr(settings, 'PUBLIC_BASE_URL', 'https://omni.alphadirect.co.bw').rstrip('/')
    return f'{base}/hris/api/apply-leave/{make_leave_apply_token(profile)}/'


def _load_profile(token: str):
    # HRISProfile — there is no EmployeeProfile in hris.models, and importing
    # one raised ImportError on EVERY request, so this page returned a 500 the
    # moment it was opened. The tests exercised the token helpers but never the
    # view, so nothing caught it (found while gating this path, 2026-08-07).
    from hris.models import HRISProfile
    try:
        data = signing.loads(token, salt=_SALT, max_age=_MAX_AGE)
    except signing.SignatureExpired:
        return None, 'This link has expired. Please apply inside Omni instead.'
    except signing.BadSignature:
        return None, 'This link is not valid.'
    p = HRISProfile.objects.filter(pk=data.get('p')).select_related('employee').first()
    if p is None:
        return None, 'We could not find your staff record.'
    return p, ''


# ── page shell ──────────────────────────────────────────────────────────────

def _page(title: str, inner: str, *, status: int = 200) -> HttpResponse:
    html = f"""<!DOCTYPE html><html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>{escape(title)} — Alpha Direct</title>
<style>
  * {{ box-sizing:border-box; }}
  body {{ margin:0; font-family:'Segoe UI',Arial,sans-serif; background:#F3F4F6; color:#1F2937; }}
  .wrap {{ max-width:560px; margin:0 auto; padding:24px 16px; }}
  .card {{ background:#fff; border-radius:14px; overflow:hidden; box-shadow:0 8px 30px rgba(13,27,42,.10); }}
  .head {{ background:{NAVY}; padding:22px 26px; }}
  .head .tag {{ color:{ORANGE}; font-size:11px; letter-spacing:2.2px; text-transform:uppercase; font-weight:700; }}
  .head h1 {{ margin:6px 0 0; color:#fff; font-size:21px; font-weight:700; }}
  .body {{ padding:24px 26px; }}
  label {{ display:block; font-size:13px; font-weight:600; color:{NAVY}; margin:16px 0 6px; }}
  select, input[type=date], textarea {{ width:100%; padding:11px 12px; border:1px solid #D1D5DB;
      border-radius:9px; font-family:inherit; font-size:15px; background:#fff; }}
  textarea {{ min-height:90px; resize:vertical; }}
  .row {{ display:flex; gap:12px; }} .row > div {{ flex:1; }}
  .hint {{ color:#6B7280; font-size:12.5px; margin-top:6px; }}
  .btn {{ display:block; width:100%; text-align:center; padding:15px; border:none; border-radius:10px;
          font-size:16px; font-weight:700; cursor:pointer; margin-top:20px; background:{ORANGE}; color:{NAVY}; }}
  .err {{ background:#FEF2F2; border-left:4px solid {RED}; color:#7F1D1D; padding:12px 14px;
          border-radius:6px; font-size:14px; margin-bottom:8px; }}
  .ok {{ background:#ECFDF5; border-left:4px solid #059669; color:#065F46; padding:14px 16px;
         border-radius:6px; font-size:15px; }}
  .who {{ background:#F9FAFB; border:1px solid #EEF0F3; border-radius:9px; padding:10px 13px;
          font-size:13.5px; color:#374151; }}
  .foot {{ text-align:center; color:#9CA3AF; font-size:11px; padding:16px; }}
</style></head>
<body><div class="wrap"><div class="card">
  <div class="head"><div class="tag">Alpha Direct · Omni</div><h1>{escape(title)}</h1></div>
  <div class="body">{inner}</div>
</div><div class="foot">Omni ERP — omni.alphadirect.co.bw</div></div></body></html>"""
    return HttpResponse(html, status=status)


def _form(profile, *, error: str = '', post=None) -> HttpResponse:
    from hris.models import LeaveType
    post = post or {}
    types = LeaveType.objects.all().order_by('name')
    opts = ''.join(
        f'<option value="{t.id}"{" selected" if str(post.get("leave_type")) == str(t.id) else ""}>'
        f'{escape(t.name)}</option>' for t in types)
    err = f'<div class="err">{escape(error)}</div>' if error else ''
    name = escape(getattr(profile.employee, 'full_name', '') or '')
    inner = f"""
    {err}
    <div class="who">Applying as <b>{name}</b>. Not you? Do not use this link — it was
      sent to {name} personally.</div>
    <form method="post">
      <label for="leave_type">Type of leave</label>
      <select id="leave_type" name="leave_type" required>{opts}</select>

      <div class="row">
        <div><label for="start_date">From</label>
          <input id="start_date" type="date" name="start_date" required
                 value="{escape(str(post.get('start_date', '')))}"></div>
        <div><label for="end_date">To</label>
          <input id="end_date" type="date" name="end_date" required
                 value="{escape(str(post.get('end_date', '')))}"></div>
      </div>
      <div class="hint">Same day for both if you only need one day.</div>

      <label for="reason">Reason</label>
      <textarea id="reason" name="reason" required
        placeholder="A short line is enough.">{escape(str(post.get('reason', '')))}</textarea>

      <button class="btn" type="submit">Send it to my manager</button>
    </form>
    <p class="hint" style="margin-top:14px">Your manager gets an email and can approve
      it with one tap. You will be emailed either way.</p>"""
    return _page('Apply for leave', inner)


# ── view ────────────────────────────────────────────────────────────────────

@csrf_exempt
@require_http_methods(['GET', 'POST'])
def apply_leave(request, token: str):
    profile, why = _load_profile(token)
    if profile is None:
        return _page('Link not valid', f'<div class="err">{escape(why)}</div>', status=400)

    if request.method == 'GET':
        return _form(profile)                      # no side effect — scanners prefetch

    from hris.models import LeaveType
    p = request.POST
    lt = LeaveType.objects.filter(pk=p.get('leave_type')).first()
    reason = (p.get('reason') or '').strip()

    def bad(msg):
        return _form(profile, error=msg, post=p)

    if lt is None:
        return bad('Please choose the type of leave.')
    try:
        start = date.fromisoformat((p.get('start_date') or '').strip())
        end = date.fromisoformat((p.get('end_date') or '').strip())
    except ValueError:
        return bad('Please give both dates.')
    if end < start:
        return bad('The "to" date is before the "from" date.')
    if not reason:
        return bad('Please give a short reason.')

    with transaction.atomic():
        lr = LeaveRequest.objects.create(
            profile=profile,
            leave_type=lt,
            start_date=start,
            end_date=end,
            # days is recomputed server-side in save() via compute_days().
            days=Decimal((end - start).days + 1),
            reason=reason,
            status=LeaveRequest.Status.PENDING,
        )

    # Same long-overdue-task gate the in-app path applies (CFO 2026-08-07).
    # Without this, the no-login link would be the way round the executive
    # countersignature — apply from your phone and skip the check entirely.
    # Sick / compassionate / maternity / paternity stay exempt. The applicant
    # here is the profile's own login, resolved from the signed token.
    signoff = None
    try:
        from hris import exec_signoff_service
        from hris.exec_signoff_models import ExecSignoff
        applicant = getattr(getattr(profile, 'employee', None), 'user', None)
        if applicant is not None:
            signoff = exec_signoff_service.require_signoff(
                ExecSignoff.Module.LEAVE, lr, applicant, leave_type=lt)
    except Exception:                              # noqa: BLE001 — never block a submit
        # Fail open, never in silence — same rule as the in-app path.
        log.exception('OVERDUE GATE FAILED OPEN on no-login leave %s — accepted '
                      'WITHOUT the executive check.', lr.pk)

    # Same notification the in-app path fires — the manager's one-tap approve email.
    try:
        from core.notifications import notify_leave_pending_approval
        notify_leave_pending_approval(lr)
    except Exception:                              # noqa: BLE001 — never block a submit
        pass

    # Name the tasks, don't just count them — a person cannot clear work they
    # have not been shown (CFO 2026-08-07).
    overdue_note = ''
    if signoff is not None:
        rows = ''.join(
            f'<li style="margin:3px 0">{escape(t.get("title") or "—")} — '
            f'<b>{int(t.get("days_overdue") or 0)} days late</b></li>'
            for t in (signoff.overdue_snapshot or {}).get('tasks', [])[:10])
        overdue_note = (
            f'<div style="background:#FFFBEB;border:1px solid #FDE68A;border-radius:9px;'
            f'padding:12px 14px;margin-top:14px;font-size:13.5px;color:#92400E">'
            f'<b>This one needs a CEO or CFO signature first.</b><br>'
            f'You have {signoff.overdue_count} task(s) more than 2 days past the due date, '
            f'so your manager cannot approve it until an executive signs. '
            f'Finish these — or agree a new date with whoever set them:'
            f'<ul style="margin:8px 0 0;padding-left:18px">{rows}</ul></div>')

    return _page('Sent to your manager', f"""
      <div class="ok"><b>Done — it is with your manager.</b><br>
        {escape(lt.name)}, {escape(lr.day_breakdown())}.</div>
      {overdue_note}
      <p class="hint" style="margin-top:14px">You will get an email when it is approved
        or declined. Reference {escape(str(lr.pk))}.</p>""")
