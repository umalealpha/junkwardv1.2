"""recruitment/authority_actions.py — one-click Authority-to-Recruit signing
from the notification email or the morning brief.

CFO 2026-08-18: "when I clicked the recruitment requirement it took me to a sign
in page — can't it be a simple approved/rejected with reasons?" A signatory
reading Outlook on their phone has no omni session, so the in-app queue loops on
sign-in. This adds Approve / Decline buttons that work on any phone or computer
WITHOUT signing in — the same login-free signed-token pattern as leave-action and
incentive-action.

Security model — a signed, time-limited token IS the credential:
  * django.core.signing binds {authority_id, signatory_user_id} into a
    tamper-proof token (salt 'authority-action', max-age 14 days). Each
    signatory's email carries only THEIR OWN token, so a tap signs the right
    slot — a shared link that anyone on a To/CC line could use is never sent.
  * The emailed button opens a GET page with NO side effect — defeating Outlook
    Safe-Links / mail-scanner prefetch. The signatory then taps Approve/Decline.
  * The POST re-runs the SAME guardrails as the in-app route (authority_access
    .can_sign + authority_views.record_decision): named signatory, not already
    decided, document still open. A decline still requires a reason. Idempotent
    — a second submit shows the current state, never a double-sign.
"""
from __future__ import annotations

from django.conf import settings
from django.core import signing
from django.core.exceptions import ValidationError
from django.http import HttpResponse
from django.utils.html import escape
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from . import authority_access as access
from .models import AuthorityToRecruit

_SALT = 'authority-action'
_MAX_AGE = 60 * 60 * 24 * 14  # 14 days

NAVY = '#0D1B2A'
ORANGE = '#F4A623'


def make_authority_action_token(authority, signatory_user) -> str:
    """Sign {authority_id, signatory_user_id} into a URL-safe token."""
    return signing.dumps(
        {'ar': str(authority.id), 'u': int(signatory_user.id)},
        salt=_SALT,
    )


def action_url(authority, signatory_user) -> str:
    # Under /api/ so Caddy proxies it to Django (bare paths go to the Next FE).
    base = getattr(settings, 'PUBLIC_BASE_URL', 'https://omni.alphadirect.co.bw').rstrip('/')
    token = make_authority_action_token(authority, signatory_user)
    return f'{base}/api/authority-action/{token}/'


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
  .why {{ background:#F7F9FC; border:1px solid #EEF0F3; border-radius:8px; padding:12px 14px;
          margin:12px 0; font-size:13px; color:#44506A; }}
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
  <div class="head"><h1>Alpha Direct · Recruitment Approval</h1></div>
  <div class="body">{inner}</div>
</div><div class="foot">Omni ERP — omni.alphadirect.co.bw</div></div></body></html>"""
    return HttpResponse(html, status=status)


def _summary_rows(a: AuthorityToRecruit) -> str:
    chain = a.signatory_chain()
    signed = len(chain) - len(a.outstanding_signatories())
    total = len(chain)
    cost_m, cost_a = a.cost_to_company()
    eff = a.effective_date.isoformat() if a.effective_date else '—'
    tier_row = (f'<tr><td>Tier</td><td>Tier {a.tier.tier} — {escape(a.tier.name)}</td></tr>'
                if a.tier_id else (f'<tr><td>Level</td><td>{escape(a.level)}</td></tr>' if a.level else ''))
    exc_row = ('<tr><td>Salary band</td><td><span class="pill pill-no">Above tier ceiling — '
               'exception</span></td></tr>' if a.is_salary_exception() else '')
    return f"""
      <tr><td>Reference</td><td>{escape(a.reference)}</td></tr>
      <tr><td>Type</td><td>{escape(a.get_kind_display())}</td></tr>
      <tr><td>Person</td><td>{escape(a.person_name)}</td></tr>
      <tr><td>Position</td><td>{escape(a.position)}</td></tr>
      <tr><td>Entity</td><td>{escape(a.entity)}</td></tr>
      {f'<tr><td>Department</td><td>{escape(a.department)}</td></tr>' if a.department else ''}
      {tier_row}
      {exc_row}
      <tr><td>Headcount</td><td>{a.headcount}</td></tr>
      <tr><td>Effective date</td><td>{escape(eff)}</td></tr>
      <tr><td>Quoted package (CTC)</td><td>{escape(a.currency)} {a.quoted_ctc_monthly:,.2f} / mo</td></tr>
      <tr><td>True cost to company</td><td>{escape(a.currency)} {cost_m:,.2f} / mo</td></tr>
      <tr><td>Signed so far</td><td>{signed} of {total}</td></tr>"""


def _why_block(a: AuthorityToRecruit) -> str:
    if not (a.justification or '').strip():
        return ''
    return f'<div class="why"><b>Justification:</b> {escape(a.justification)}</div>'


# ── views ─────────────────────────────────────────────────────────────────

def _load(token: str):
    """(authority, signatory_user) or (None, reason_str)."""
    from django.contrib.auth.models import User
    try:
        data = signing.loads(token, salt=_SALT, max_age=_MAX_AGE)
    except signing.SignatureExpired:
        return None, 'This approval link has expired. Please open it in omni.'
    except signing.BadSignature:
        return None, 'This approval link is not valid.'
    a = AuthorityToRecruit.objects.filter(pk=data.get('ar')).first()
    user = User.objects.filter(pk=data.get('u'), is_active=True).first()
    if a is None:
        return None, 'That authority no longer exists.'
    if user is None:
        return None, 'Your account is not active. Please open it in omni.'
    return a, user


def _decided_page(a: AuthorityToRecruit) -> HttpResponse:
    pill = 'pill-ok' if a.status == AuthorityToRecruit.Status.APPROVED else 'pill-no'
    return _page('Already decided', f"""
      <h2>Already {escape(a.get_status_display().lower())}</h2>
      <p class="muted">This authority was already handled — nothing more to do here.</p>
      <table class="kv">{_summary_rows(a)}
      <tr><td>Status</td><td><span class="pill {pill}">{escape(a.get_status_display())}</span></td></tr></table>""")


@require_http_methods(['GET'])
def authority_action_page(request, token: str):
    """Side-effect-free confirmation page. Safe for mail-scanner prefetch."""
    a, user = _load(token)
    if a is None:
        return _page('Link problem', f'<h2>Sorry</h2><p class="muted">{escape(user)}</p>', status=400)

    if a.status != AuthorityToRecruit.Status.PENDING:
        return _decided_page(a)

    may, why = access.can_sign(user, a)
    if not may:
        # Already signed by this person, or not a signatory — show the state.
        return _page('Cannot sign',
                     f'<h2>Heads up</h2><p class="muted">{escape(why)}</p>'
                     f'<table class="kv">{_summary_rows(a)}</table>', status=403)

    return _page('Sign authority', f"""
      <h2>{escape(a.person_name)}</h2>
      <p class="muted">{escape(a.get_kind_display())} — awaiting your signature.
      An offer cannot go out until all signatories are in.</p>
      <table class="kv">{_summary_rows(a)}</table>
      {_why_block(a)}
      <form method="POST" action="/api/authority-action/{escape(token)}/submit/">
        <input type="hidden" name="decision" value="approve">
        <button class="btn btn-ok" type="submit">✓ Approve</button>
      </form>
      <form id="decline" method="POST" action="/api/authority-action/{escape(token)}/submit/" style="margin-top:16px;">
        <input type="hidden" name="decision" value="decline">
        <label class="muted">A reason is required to decline:</label>
        <textarea name="notes" rows="2" placeholder="Reason for declining…"></textarea>
        <button class="btn btn-no" type="submit">✕ Decline</button>
      </form>""")


@csrf_exempt
@require_http_methods(['POST'])
def authority_action_submit(request, token: str):
    """Performs the decision. The signed token is the credential (csrf-exempt)."""
    from .authority_views import record_decision
    a, user = _load(token)
    if a is None:
        return _page('Link problem', f'<h2>Sorry</h2><p class="muted">{escape(user)}</p>', status=400)

    if a.status != AuthorityToRecruit.Status.PENDING:
        return _decided_page(a)

    may, why = access.can_sign(user, a)
    if not may:
        return _page('Cannot sign', f'<h2>Heads up</h2><p class="muted">{escape(why)}</p>'
                     f'<table class="kv">{_summary_rows(a)}</table>', status=403)

    decision = (request.POST.get('decision') or '').strip().lower()
    notes = (request.POST.get('notes') or '').strip()
    if decision not in ('approve', 'decline'):
        return _page('Try again', '<h2>Hmm</h2><p class="muted">No decision was submitted.</p>', status=400)
    if decision == 'decline' and not notes:
        return _page('One more thing', f"""
          <h2>Add a reason</h2>
          <p class="muted">Please say why you are declining — go back and add a short reason.</p>
          <table class="kv">{_summary_rows(a)}</table>""", status=400)

    try:
        record_decision(a, user, decision, notes)
    except ValidationError as e:
        msg = '; '.join(e.messages) if hasattr(e, 'messages') else str(e)
        return _page('Cannot do that', f'<h2>Heads up</h2><p class="muted">{escape(msg)}</p>'
                     f'<table class="kv">{_summary_rows(a)}</table>', status=400)

    a.refresh_from_db()
    if a.status == AuthorityToRecruit.Status.DECLINED:
        return _page('Done', f"""
          <h2>✕ Declined</h2>
          <p class="muted">This authority has been declined and is now closed. The raiser and
          the other signatories will be told.</p>
          <table class="kv">{_summary_rows(a)}</table>""")

    if a.status == AuthorityToRecruit.Status.APPROVED:
        return _page('Done', f"""
          <h2>✓ Fully approved</h2>
          <p class="muted">All signatures are in. Recruitment can proceed to the offer step.</p>
          <table class="kv">{_summary_rows(a)}</table>""")

    return _page('Done', f"""
      <h2>✓ Your signature is recorded</h2>
      <p class="muted">Thank you. This authority still needs the remaining signatures before an
      offer can go out. You do not need to do anything more.</p>
      <table class="kv">{_summary_rows(a)}</table>""")
