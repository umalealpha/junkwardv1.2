"""
budgets/spend_actions.py — one-click Spend & Event request approval from the
notification email (CFO 2026-07-14: "can't I approve these from my phone, a
link would be great"). Mirrors hris/leave_actions.py exactly — same signed-
token security model, same GET-page-then-POST pattern, same house styling —
scoped to budgets.SpendRequest instead of hris.LeaveRequest.

Security model — a signed, time-limited token IS the credential:
  * django.core.signing binds {sr_id, approver_user_id} into a tamper-proof
    token (salt 'spend-action', max-age 14 days). Cannot be forged without
    SECRET_KEY.
  * The emailed link opens a GET page with NO side effect (safe for mail-
    scanner/Safe-Links prefetch). The approver then taps Approve/Reject.
  * The POST re-checks the SAME guardrail as the in-app decide endpoint
    (budgets.spend_views._can_approve_spend — CFO/EXCO only) and is
    idempotent — a second submit shows the already-decided state.
"""
from __future__ import annotations

from django.conf import settings
from django.core import signing
from django.http import HttpResponse
from django.utils import timezone
from django.utils.html import escape
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from budgets.models import SpendRequest

_SALT = 'spend-action'
_MAX_AGE = 60 * 60 * 24 * 14  # 14 days

NAVY = '#0D1B2A'
ORANGE = '#F4A623'


def make_spend_action_token(spend_request, approver_user) -> str:
    """Sign {sr_id, approver_user_id} into a URL-safe token."""
    return signing.dumps(
        {'sr': str(spend_request.id), 'u': int(approver_user.id)},
        salt=_SALT,
    )


def action_url(spend_request, approver_user) -> str:
    # Under the versioned /api/v1/ prefix, already proxied to Django (same
    # prefix the rest of spend-requests/... uses).
    base = getattr(settings, 'PUBLIC_BASE_URL', 'https://omni.alphadirect.co.bw').rstrip('/')
    token = make_spend_action_token(spend_request, approver_user)
    return f'{base}/api/v1/spend-action/{token}/'


# ── branded page shell (mirrors hris/leave_actions.py's _page) ─────────────

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
  .btn-ok {{ background:#059669; color:#fff; }}
  .btn-no {{ background:#fff; color:#DC2626; border:1.5px solid #DC2626; }}
  textarea {{ width:100%; padding:10px; border:1px solid #D1D5DB; border-radius:8px;
             font-family:inherit; font-size:14px; margin-top:6px; }}
  .pill {{ display:inline-block; padding:3px 12px; border-radius:999px; font-size:13px; font-weight:700; }}
  .pill-ok {{ background:#ECFDF5; color:#059669; }}
  .pill-no {{ background:#FEF2F2; color:#DC2626; }}
  .pill-wait {{ background:#FFFBEB; color:#92400E; }}
  .flag {{ background:#FEF3C7; color:#92400E; border-radius:8px; padding:8px 12px; font-size:13px; margin:10px 0; }}
  .foot {{ text-align:center; color:#9CA3AF; font-size:11px; padding:16px; }}
</style></head>
<body><div class="wrap"><div class="card">
  <div class="head"><h1>Alpha Direct · Spend Approval</h1></div>
  <div class="body">{inner}</div>
</div><div class="foot">Omni ERP — omni.alphadirect.co.bw</div></div></body></html>"""
    return HttpResponse(html, status=status)


def _spend_summary_rows(sr: SpendRequest) -> str:
    return f"""
      <tr><td>Requester</td><td>{escape(sr.requester.get_full_name() or sr.requester.username)}</td></tr>
      <tr><td>Type</td><td>{escape(sr.get_request_type_display())}</td></tr>
      <tr><td>Title</td><td>{escape(sr.title)}</td></tr>
      <tr><td>Amount</td><td>BWP {sr.amount:,.2f}</td></tr>
      <tr><td>Event date</td><td>{escape(sr.event_date.isoformat()) if sr.event_date else '—'}</td></tr>
      <tr><td>Within budget?</td><td>{'Yes' if sr.within_budget else 'No'}</td></tr>"""


def _ai_summary_html(sr: SpendRequest) -> str:
    if sr.ai_status != SpendRequest.AIStatus.DONE or not sr.ai_summary:
        return ''
    flags = ''.join(f'<div class="flag">⚠ {escape(f)}</div>' for f in (sr.ai_flags or []))
    return f'<p class="muted"><b>Budget read (AI):</b> {escape(sr.ai_summary)}</p>{flags}'


# ── views ─────────────────────────────────────────────────────────────────

def _load(token: str):
    """(spend_request, approver_user) or (None, reason_str)."""
    from django.contrib.auth.models import User
    try:
        data = signing.loads(token, salt=_SALT, max_age=_MAX_AGE)
    except signing.SignatureExpired:
        return None, 'This approval link has expired. Please use Spend Requests in omni.'
    except signing.BadSignature:
        return None, 'This approval link is not valid.'
    sr = (SpendRequest.objects
          .filter(pk=data.get('sr'))
          .select_related('requester')
          .first())
    approver = User.objects.filter(pk=data.get('u'), is_active=True).first()
    if sr is None:
        return None, 'That spend request no longer exists.'
    if approver is None:
        return None, 'Your account is not active. Please use Spend Requests in omni.'
    return sr, approver


def _may_approve(approver) -> str | None:
    """None if allowed, else a human reason string (same rule as spend_request_decide)."""
    from budgets.spend_views import _can_approve_spend
    if not _can_approve_spend(approver):
        return 'Only the CFO / EXCO may approve spend requests.'
    return None


@require_http_methods(['GET'])
def spend_action_page(request, token: str):
    """Side-effect-free confirmation page. Safe for mail-scanner prefetch."""
    sr, approver = _load(token)
    if sr is None:
        return _page('Link problem', f'<h2>Sorry</h2><p class="muted">{escape(approver)}</p>', status=400)

    if sr.status not in (SpendRequest.Status.SUBMITTED, SpendRequest.Status.DRAFT):
        pill = 'pill-ok' if sr.status == SpendRequest.Status.APPROVED else 'pill-no'
        return _page('Already decided', f"""
          <h2>Already {escape(sr.get_status_display().lower())}</h2>
          <p class="muted">This request was already handled — nothing more to do.</p>
          <table class="kv">{_spend_summary_rows(sr)}
          <tr><td>Status</td><td><span class="pill {pill}">{escape(sr.get_status_display())}</span></td></tr></table>""")

    blocked = _may_approve(approver)
    if blocked:
        return _page('Cannot approve', f'<h2>Heads up</h2><p class="muted">{escape(blocked)}</p>'
                     f'<table class="kv">{_spend_summary_rows(sr)}</table>', status=403)

    return _page('Approve spend request', f"""
      <h2>{escape(sr.requester.get_full_name() or sr.requester.username)}</h2>
      <p class="muted">has requested pre-spend approval — approve or reject below.</p>
      <table class="kv">{_spend_summary_rows(sr)}</table>
      {_ai_summary_html(sr)}
      <form method="POST" action="/api/v1/spend-action/{escape(token)}/submit/">
        <input type="hidden" name="decision" value="approve">
        <button class="btn btn-ok" type="submit">✓ Approve</button>
      </form>
      <form method="POST" action="/api/v1/spend-action/{escape(token)}/submit/" style="margin-top:16px;">
        <input type="hidden" name="decision" value="reject">
        <label class="muted">If rejecting, add a short reason:</label>
        <textarea name="notes" rows="2" placeholder="Reason for rejecting…"></textarea>
        <button class="btn btn-no" type="submit">✕ Reject</button>
      </form>""")


@csrf_exempt
@require_http_methods(['POST'])
def spend_action_submit(request, token: str):
    """Performs the decision. The signed token is the credential (csrf-exempt)."""
    sr, approver = _load(token)
    if sr is None:
        return _page('Link problem', f'<h2>Sorry</h2><p class="muted">{escape(approver)}</p>', status=400)

    if sr.status not in (SpendRequest.Status.SUBMITTED, SpendRequest.Status.DRAFT):
        pill = 'pill-ok' if sr.status == SpendRequest.Status.APPROVED else 'pill-no'
        return _page('Already decided', f"""
          <h2>Already {escape(sr.get_status_display().lower())}</h2>
          <p class="muted">No change made — someone already handled this one.</p>
          <table class="kv">{_spend_summary_rows(sr)}
          <tr><td>Status</td><td><span class="pill {pill}">{escape(sr.get_status_display())}</span></td></tr></table>""")

    blocked = _may_approve(approver)
    if blocked:
        return _page('Cannot approve', f'<h2>Heads up</h2><p class="muted">{escape(blocked)}</p>', status=403)

    decision = (request.POST.get('decision') or '').strip().lower()
    notes = (request.POST.get('notes') or '').strip()
    if decision not in ('approve', 'reject'):
        return _page('Try again', '<h2>Hmm</h2><p class="muted">No decision was submitted.</p>', status=400)

    sr.status = (SpendRequest.Status.APPROVED if decision == 'approve'
                 else SpendRequest.Status.REJECTED)
    sr.approver = approver
    sr.decided_at = timezone.now()
    if notes:
        sr.decision_notes = notes
    sr.save(update_fields=['status', 'approver', 'decided_at', 'decision_notes', 'updated_at'])

    ok = sr.status == SpendRequest.Status.APPROVED
    return _page('Done', f"""
      <h2>{'✓ Approved' if ok else '✕ Rejected'}</h2>
      <p class="muted">{escape(sr.requester.get_full_name() or sr.requester.username)}'s spend request has been
        {'approved' if ok else 'rejected'}. They will see the update in omni.</p>
      <table class="kv">{_spend_summary_rows(sr)}
      <tr><td>Decision by</td><td>{escape(approver.get_full_name() or approver.username)}</td></tr>
      <tr><td>Status</td><td><span class="pill {'pill-ok' if ok else 'pill-no'}">{escape(sr.get_status_display())}</span></td></tr></table>""")
