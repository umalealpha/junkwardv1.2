"""
hris/incentive_actions.py — one-click incentive approval from the email.

CFO 2026-07-24: "When I tap approve on my phone it should open fast and let me
approve without signing into omni." The current email only linked to the SSO
omni page, which loops on a phone with no session (the "circling"). This adds
Approve / Decline buttons that work on any phone or computer WITHOUT signing in
— a fast, login-free page — mirroring the leave one-click (hris/leave_actions).

Security model — a signed, time-limited token IS the credential:
  * django.core.signing binds {incentive_id, approver_user_id} into a
    tamper-proof token (salt 'incentive-action', max-age 14 days). It cannot be
    forged without SECRET_KEY, and each approver's email carries only THEIR own
    token (so the CFO's tap signs the CFO slot, HR's signs the HR slot).
  * The emailed button opens a GET page with NO side effect — defeating Outlook
    Safe-Links / mail-scanner prefetch. The approver then taps Approve (one tap).
  * The POST re-runs the SAME service guardrails (incentive_service): slot check,
    segregation of duties (never your own request; CFO ≠ HR), status still
    pending. Idempotent — a second submit shows the current state, never a
    double-sign. Money is never moved here; Finance still marks payroll in omni.
"""
from __future__ import annotations

from django.conf import settings
from django.core import signing
from django.core.exceptions import ValidationError
from django.http import HttpResponse
from django.utils.html import escape
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from .incentive_models import IncentiveRequest
from .incentive_service import approve_request, reject_request, slot_for

_SALT = 'incentive-action'
_MAX_AGE = 60 * 60 * 24 * 14  # 14 days

NAVY = '#0D1B2A'
ORANGE = '#F4A623'


def make_incentive_action_token(req, approver_user) -> str:
    """Sign {incentive_id, approver_user_id} into a URL-safe token."""
    return signing.dumps(
        {'ir': str(req.id), 'u': int(approver_user.id)},
        salt=_SALT,
    )


def action_url(req, approver_user) -> str:
    # Under /hris/api/ so Caddy proxies it to Django (bare /hris/* → Next FE).
    base = getattr(settings, 'PUBLIC_BASE_URL', 'https://omni.alphadirect.co.bw').rstrip('/')
    token = make_incentive_action_token(req, approver_user)
    return f'{base}/hris/api/incentive-action/{token}/'


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
  table.lines {{ width:100%; border-collapse:collapse; margin:12px 0; font-size:13px; }}
  table.lines td, table.lines th {{ padding:7px 10px; border:1px solid #E5E7EB; text-align:left; }}
  table.lines th {{ background:{NAVY}; color:#fff; font-weight:600; }}
  table.lines td.amt, table.lines th.amt {{ text-align:right; }}
  table.lines tr.total td {{ background:#FFF7ED; font-weight:700; }}
  table.lines tr.motiv td {{ background:#F8FAFC; font-weight:400; font-size:13px;
                             line-height:1.5; color:#374151; text-align:left; }}
  table.lines tr.motiv .flags {{ margin-top:6px; font-size:12px; font-weight:600; color:#059669; }}
  table.lines tr.motiv .flags.warn {{ color:#B45309; }}
  table.lines tr.motiv .nomotiv {{ color:#B45309; font-style:italic; }}
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
  <div class="head"><h1>Alpha Direct · Incentive Approval</h1></div>
  <div class="body">{inner}</div>
</div><div class="foot">Omni ERP — omni.alphadirect.co.bw</div></div></body></html>"""
    return HttpResponse(html, status=status)


def _motiv_row(l) -> str:
    """The motivation the approver is actually signing off on — shown clearly
    under the person it belongs to (CFO 2026-08-22). Mirrors the email body."""
    flags = []
    if l.beyond_normal_duties:
        flags.append("✓ Beyond normal duties")
    if l.on_time:
        flags.append("✓ On time")
    if l.error_free:
        flags.append("✓ Error-free")
    flag_html = (f"<div class='flags'>{escape(' · '.join(flags))}</div>"
                 if flags else '')
    if l.needed_manager_fix:
        flag_html += "<div class='flags warn'>⚠ Needed a manager fix</div>"
    why = (l.justification or '').strip()
    why_html = (f"<b>Why:</b> {escape(why)}" if why
                else "<span class='nomotiv'>No motivation was written.</span>")
    return (f"<tr class='motiv'><td colspan='3'>{why_html}{flag_html}</td></tr>")


def _lines_rows(req) -> str:
    body = ''.join(
        f"<tr><td>{escape(l.name)}</td><td>{escape(l.basis or '—')}</td>"
        f"<td class='amt'>BWP {l.amount:,.2f}</td></tr>"
        f"{_motiv_row(l)}"
        for l in req.lines.all())
    return (
        "<table class='lines'><tr><th>Name / category</th><th>Basis</th>"
        "<th class='amt'>Amount</th></tr>"
        f"{body}"
        f"<tr class='total'><td colspan='2'>Total</td>"
        f"<td class='amt'>BWP {req.total:,.2f}</td></tr></table>")


def _summary_rows(req) -> str:
    cfo = ('<span class="pill pill-ok">signed</span>' if req.cfo_approved_at
           else '<span class="pill pill-wait">waiting</span>')
    hr = ('<span class="pill pill-ok">signed</span>' if req.hr_approved_at
          else '<span class="pill pill-wait">waiting</span>')
    attested = ('<span class="pill pill-ok">yes</span>' if req.manager_attested
                else '<span class="pill pill-no">no</span>')
    return f"""
      <tr><td>Request</td><td>{escape(req.title)}</td></tr>
      <tr><td>Period</td><td>{escape(req.period)}</td></tr>
      {f'<tr><td>Department</td><td>{escape(req.department)}</td></tr>' if req.department else ''}
      <tr><td>Requested by</td><td>{escape(req.maker_email or 'unknown')}</td></tr>
      <tr><td>Manager attested</td><td>{attested}</td></tr>
      <tr><td>CFO signature</td><td>{cfo}</td></tr>
      <tr><td>HR signature</td><td>{hr}</td></tr>"""


# ── views ─────────────────────────────────────────────────────────────────

def _load(token: str):
    """(req, approver_user) or (None, reason_str)."""
    from django.contrib.auth.models import User
    try:
        data = signing.loads(token, salt=_SALT, max_age=_MAX_AGE)
    except signing.SignatureExpired:
        return None, 'This approval link has expired. Please use the Incentives page in omni.'
    except signing.BadSignature:
        return None, 'This approval link is not valid.'
    req = (IncentiveRequest.objects
           .filter(pk=data.get('ir'))
           .prefetch_related('lines')
           .first())
    approver = User.objects.filter(pk=data.get('u'), is_active=True).first()
    if req is None:
        return None, 'That incentive request no longer exists.'
    if approver is None:
        return None, 'Your account is not active. Please use the Incentives page in omni.'
    return req, approver


def _decided_page(req) -> HttpResponse:
    pill = 'pill-ok' if req.status == IncentiveRequest.Status.APPROVED else 'pill-no'
    return _page('Already decided', f"""
      <h2>Already {escape(req.get_status_display().lower())}</h2>
      <p class="muted">This request was already handled — nothing more to do here.</p>
      <table class="kv">{_summary_rows(req)}
      <tr><td>Status</td><td><span class="pill {pill}">{escape(req.get_status_display())}</span></td></tr></table>
      {_lines_rows(req)}""")


@require_http_methods(['GET'])
def incentive_action_page(request, token: str):
    """Side-effect-free confirmation page. Safe for mail-scanner prefetch."""
    req, approver = _load(token)
    if req is None:
        return _page('Link problem', f'<h2>Sorry</h2><p class="muted">{escape(approver)}</p>', status=400)

    if req.status != IncentiveRequest.Status.PENDING:
        return _decided_page(req)

    slot = slot_for(approver)
    if slot is None and not getattr(approver, 'is_superuser', False):
        return _page('Cannot approve',
                     '<h2>Heads up</h2><p class="muted">Only the CFO and Head of Human '
                     'Capital approve incentive requests.</p>'
                     f'<table class="kv">{_summary_rows(req)}</table>', status=403)
    if req.maker_id and approver.pk == req.maker_id:
        return _page('Cannot approve',
                     '<h2>Heads up</h2><p class="muted">You cannot approve your own '
                     'request (segregation of duties).</p>'
                     f'<table class="kv">{_summary_rows(req)}</table>', status=403)

    slot_label = 'CFO' if slot == 'cfo' else ('HR' if slot == 'hr' else 'authorised')
    return _page('Approve incentive', f"""
      <h2>{escape(req.title)}</h2>
      <p class="muted">Incentive request awaiting your signature (as {escape(slot_label)}).
      Both the CFO and HR must sign before Finance processes it in payroll.</p>
      <table class="kv">{_summary_rows(req)}</table>
      {_lines_rows(req)}
      <form method="POST" action="/hris/api/incentive-action/{escape(token)}/submit/">
        <input type="hidden" name="decision" value="approve">
        <button class="btn btn-ok" type="submit">✓ Approve</button>
      </form>
      <form id="decline" method="POST" action="/hris/api/incentive-action/{escape(token)}/submit/" style="margin-top:16px;">
        <input type="hidden" name="decision" value="reject">
        <label class="muted">If declining, add a short reason:</label>
        <textarea name="notes" rows="2" placeholder="Reason for declining…"></textarea>
        <button class="btn btn-no" type="submit">✕ Decline</button>
      </form>""")


@csrf_exempt
@require_http_methods(['POST'])
def incentive_action_submit(request, token: str):
    """Performs the decision. The signed token is the credential (csrf-exempt)."""
    req, approver = _load(token)
    if req is None:
        return _page('Link problem', f'<h2>Sorry</h2><p class="muted">{escape(approver)}</p>', status=400)

    if req.status != IncentiveRequest.Status.PENDING:
        return _decided_page(req)

    decision = (request.POST.get('decision') or '').strip().lower()
    notes = (request.POST.get('notes') or '').strip()
    if decision not in ('approve', 'reject'):
        return _page('Try again', '<h2>Hmm</h2><p class="muted">No decision was submitted.</p>', status=400)

    try:
        if decision == 'approve':
            approve_request(req, approver)
        else:
            reject_request(req, approver, notes=notes)
    except ValidationError as e:
        msg = '; '.join(e.messages) if hasattr(e, 'messages') else str(e)
        return _page('Cannot do that', f'<h2>Heads up</h2><p class="muted">{escape(msg)}</p>'
                     f'<table class="kv">{_summary_rows(req)}</table>', status=400)

    req.refresh_from_db()
    if req.status == IncentiveRequest.Status.REJECTED:
        return _page('Done', f"""
          <h2>✕ Declined</h2>
          <p class="muted">This incentive request has been declined. The requester will be notified.</p>
          <table class="kv">{_summary_rows(req)}</table>""")

    if req.status == IncentiveRequest.Status.APPROVED:
        return _page('Done', f"""
          <h2>✓ Fully approved</h2>
          <p class="muted">Both signatures are in. Finance will process it in the payroll run.</p>
          <table class="kv">{_summary_rows(req)}</table>
          {_lines_rows(req)}""")

    # One leg signed, still waiting on the other.
    waiting = 'HR' if not req.hr_approved_at else 'the CFO'
    return _page('Done', f"""
      <h2>✓ Your approval is recorded</h2>
      <p class="muted">Thank you. This request still needs {escape(waiting)}'s signature before
      Finance can process it. You do not need to do anything more.</p>
      <table class="kv">{_summary_rows(req)}</table>""")
