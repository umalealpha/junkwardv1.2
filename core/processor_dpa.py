"""
core/processor_dpa.py — signable Data Processing Agreements with external
processors (CFO 2026-07-29; closes DPA-audit finding H-6).

Why this exists
    Alpha Brain's weekly team extracts carry policyholder personal data (names,
    policy numbers, amounts). Before any of that may be emailed to an outside
    company — e.g. ADRisk / The Risk Co (India) — Botswana DPA No. 18 of 2024
    requires a documented controller-processor agreement AND an appropriate
    safeguard for the cross-border transfer (Part IX). This module issues that
    agreement, captures the processor's e-signature, and files it in the Data
    Protection dashboard.

Security model — identical to one-click leave approval (hris/leave_actions):
    * django.core.signing binds {dpa_id} into a tamper-proof, time-limited token
      (salt 'processor-dpa'). It cannot be forged or altered without SECRET_KEY.
    * The emailed link opens a side-effect-free GET page (safe against mail-scanner
      / Safe-Links prefetch). The processor then types their name + title, ticks
      the authority box, and submits ONE form.
    * The POST is CSRF-exempt because the signed token is the credential. It is
      idempotent — a second submit shows the already-signed state, it never
      re-signs. At signing we store a SHA-256 of the exact document shown, so the
      signed text can never be changed afterwards.
"""
from __future__ import annotations

import hashlib

from django.conf import settings
from django.core import signing
from django.http import HttpResponse
from django.utils import timezone
from django.utils.html import escape
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

NAVY = '#0D1B2A'
ORANGE = '#F4A623'

_SALT = 'processor-dpa'
_MAX_AGE = 60 * 60 * 24 * 30  # 30 days to sign


# ── token ───────────────────────────────────────────────────────────────────
def make_sign_token(dpa) -> str:
    return signing.dumps({'dpa': int(dpa.id)}, salt=_SALT)


def sign_url(dpa) -> str:
    base = getattr(settings, 'PUBLIC_BASE_URL', 'https://omni.alphadirect.co.bw').rstrip('/')
    return f'{base}/api/dpa/sign/{make_sign_token(dpa)}/'


def _load(token: str):
    """(dpa, None) or (None, reason)."""
    from core.models import ProcessorDPA
    try:
        data = signing.loads(token, salt=_SALT, max_age=_MAX_AGE)
    except signing.SignatureExpired:
        return None, 'This signing link has expired. Please ask Alpha Direct to reissue it.'
    except signing.BadSignature:
        return None, 'This signing link is not valid.'
    dpa = ProcessorDPA.objects.filter(pk=data.get('dpa')).first()
    if dpa is None:
        return None, 'That agreement no longer exists.'
    return dpa, None


# ── the agreement text (frozen onto the record at creation) ───────────────────
def render_dpa_document(dpa) -> str:
    """Build the agreement body as trusted HTML. Every dynamic value is escaped
    here, once, so the frozen string is safe to embed verbatim on the page."""
    pn = escape(dpa.processor_name)
    pe = escape(dpa.processor_email)
    country = escape(dpa.country or '—')
    purpose = escape(dpa.purpose)
    cats = escape(dpa.data_categories)
    ref = escape(dpa.reference or '—')
    adeq = ('is on' if dpa.adequate else 'is NOT on')
    today = timezone.localdate().isoformat()

    clauses = f"""
<p><strong>Reference:</strong> {ref} &nbsp;·&nbsp; <strong>Issued:</strong> {today}</p>
<p>This Data Processing Agreement (&ldquo;Agreement&rdquo;) is made under the
<em>Data Protection Act No. 18 of 2024</em> (Botswana) between:</p>
<table class="kv">
  <tr><td>Controller</td><td>Alpha Direct Insurance Company (Pty) Ltd, Gaborone, Botswana</td></tr>
  <tr><td>Processor</td><td>{pn}</td></tr>
  <tr><td>Processor contact</td><td>{pe}</td></tr>
  <tr><td>Country of processing</td><td>{country}</td></tr>
</table>

<h3>1. Subject matter and instructions</h3>
<p>The Processor shall process personal data only on the Controller&rsquo;s
documented instructions, for this purpose alone: <strong>{purpose}</strong>.
The Processor shall not process the data for its own purposes.</p>

<h3>2. Categories of data</h3>
<p>The personal data covered is limited to: <strong>{cats}</strong>. The
Controller shall not transfer, and the Processor shall not request, any national
identity (Omang), passport, bank-account, payment-card, biometric or health data,
or any document scans, under this Agreement.</p>

<h3>3. Confidentiality</h3>
<p>The Processor shall keep the data strictly confidential, disclose it only to
personnel who need it for the purpose above and who are under a duty of
confidence, and shall not copy, publish or use it for any other purpose.</p>

<h3>4. Security</h3>
<p>The Processor shall apply appropriate technical and organisational measures:
access on a need-to-know basis, no storage on personal devices or personal email,
and no onward sharing outside the Processor&rsquo;s authorised team.</p>

<h3>5. Sub-processing and onward transfer</h3>
<p>The Processor shall not engage any sub-processor, nor transfer the data to any
further party or country, without the Controller&rsquo;s prior written
authorisation.</p>

<h3>6. Cross-border transfer</h3>
<p>The Controller records that {country} {adeq} Botswana&rsquo;s list of countries
providing an adequate level of protection. This Agreement, together with the
Controller&rsquo;s written authorisation, is relied on as the appropriate
safeguard for the transfer under Part IX of the Act. The Processor submits to the
Act in respect of the data.</p>

<h3>7. Breach notification</h3>
<p>The Processor shall notify the Controller without undue delay, and in any event
within <strong>24 hours</strong>, of becoming aware of any personal-data breach,
so the Controller can meet its 72-hour duty to the Information &amp; Data
Protection Commission.</p>

<h3>8. Data-subject rights and assistance</h3>
<p>The Processor shall assist the Controller in responding to data-subject
requests and shall make available all information necessary to demonstrate
compliance with this Agreement.</p>

<h3>9. Return and deletion</h3>
<p>On the end of the engagement, or on the Controller&rsquo;s request, the
Processor shall return or securely delete all Alpha Direct personal data and hold
no residual copies, save any retention required by law.</p>

<h3>10. Duration</h3>
<p>This Agreement applies for the term of the services engagement between the
parties and to all processing carried out under it.</p>
"""
    return clauses.strip()


# ── branded page shell ────────────────────────────────────────────────────────
def _page(title: str, inner: str, *, status: int = 200) -> HttpResponse:
    html = f"""<!DOCTYPE html><html lang="en"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{escape(title)} — Alpha Direct</title>
<style>
  * {{ box-sizing:border-box; }}
  body {{ margin:0; font-family:'Segoe UI',Arial,sans-serif; background:#F3F4F6; color:#1F2937; line-height:1.5; }}
  .wrap {{ max-width:720px; margin:0 auto; padding:24px 16px; }}
  .card {{ background:#fff; border-radius:14px; overflow:hidden; box-shadow:0 8px 30px rgba(13,27,42,.10); }}
  .head {{ background:{NAVY}; padding:22px 26px; }}
  .head h1 {{ margin:0; color:{ORANGE}; font-size:19px; font-weight:700; }}
  .body {{ padding:24px 28px; }}
  .body h2 {{ margin:0 0 4px; font-size:22px; color:{NAVY}; }}
  .body h3 {{ margin:20px 0 4px; font-size:15px; color:{NAVY}; }}
  .body p {{ margin:8px 0; font-size:14px; }}
  .muted {{ color:#6B7280; font-size:13px; }}
  table.kv {{ width:100%; border-collapse:collapse; margin:14px 0; font-size:14px; }}
  table.kv td {{ padding:8px 0; border-bottom:1px solid #EEF0F3; vertical-align:top; }}
  table.kv td:first-child {{ color:#6B7280; width:38%; }}
  .doc {{ border:1px solid #EEF0F3; border-radius:10px; padding:6px 18px; margin:16px 0; background:#FCFCFD; }}
  label.fld {{ display:block; margin-top:14px; font-size:13px; color:#374151; font-weight:600; }}
  input[type=text] {{ width:100%; padding:11px; border:1px solid #D1D5DB; border-radius:8px;
                      font-family:inherit; font-size:15px; margin-top:5px; }}
  .agree {{ display:flex; gap:10px; align-items:flex-start; margin-top:16px; font-size:14px; }}
  .agree input {{ margin-top:3px; width:18px; height:18px; }}
  .btn {{ display:block; width:100%; text-align:center; padding:15px; border:none;
          border-radius:10px; font-size:16px; font-weight:700; cursor:pointer; margin-top:18px;
          background:{ORANGE}; color:{NAVY}; }}
  .pill {{ display:inline-block; padding:3px 12px; border-radius:999px; font-size:13px; font-weight:700; }}
  .pill-ok {{ background:#ECFDF5; color:#059669; }}
  .pill-wait {{ background:#FFFBEB; color:#92400E; }}
  .foot {{ text-align:center; color:#9CA3AF; font-size:11px; padding:16px; }}
</style></head>
<body><div class="wrap"><div class="card">
  <div class="head"><h1>Alpha Direct · Data Processing Agreement</h1></div>
  <div class="body">{inner}</div>
</div><div class="foot">Omni ERP — omni.alphadirect.co.bw · Botswana Data Protection Act No. 18 of 2024</div></div></body></html>"""
    return HttpResponse(html, status=status)


def _signed_banner(dpa) -> str:
    return f"""
      <table class="kv">
        <tr><td>Status</td><td><span class="pill pill-ok">Signed</span></td></tr>
        <tr><td>Signed by</td><td><strong>{escape(dpa.signatory_name)}</strong>{(' — ' + escape(dpa.signatory_title)) if dpa.signatory_title else ''}</td></tr>
        <tr><td>Signed on</td><td>{dpa.signed_at:%d %b %Y, %H:%M} UTC</td></tr>
        <tr><td>Integrity</td><td class="muted">SHA-256 {escape(dpa.signed_doc_sha256[:16])}…</td></tr>
      </table>"""


# ── views ─────────────────────────────────────────────────────────────────────
@require_http_methods(['GET'])
def dpa_sign_page(request, token: str):
    """Side-effect-free page: shows the agreement, and (if unsigned) a sign form."""
    dpa, err = _load(token)
    if dpa is None:
        return _page('Link problem', f'<h2>Sorry</h2><p class="muted">{escape(err)}</p>', status=400)

    doc = f'<div class="doc">{dpa.document_html}</div>'

    if dpa.status == dpa.Status.SIGNED:
        return _page('Agreement — signed', f"""
          <h2>{escape(dpa.processor_name)}</h2>
          <p class="muted">This Data Processing Agreement has been signed. Nothing further is needed.</p>
          {_signed_banner(dpa)}
          {doc}""")

    return _page('Sign this agreement', f"""
      <h2>{escape(dpa.processor_name)}</h2>
      <p class="muted">Please read the agreement below and sign it. Signing is one tap — no login needed.</p>
      {doc}
      <form method="POST" action="/api/dpa/sign/{escape(token)}/submit/">
        <label class="fld">Your full name
          <input type="text" name="name" value="{escape(dpa.signatory_name)}" placeholder="e.g. Pramod Bisen" required>
        </label>
        <label class="fld">Your job title
          <input type="text" name="title" value="{escape(dpa.signatory_title)}" placeholder="e.g. Sr. IT Manager, The Risk Co" required>
        </label>
        <label class="agree">
          <input type="checkbox" name="agree" value="yes" required>
          <span>I confirm I am authorised to sign for <strong>{escape(dpa.processor_name)}</strong>,
          and on its behalf I accept this Data Processing Agreement.</span>
        </label>
        <button class="btn" type="submit">✓ Sign agreement</button>
      </form>""")


@csrf_exempt
@require_http_methods(['POST'])
def dpa_sign_submit(request, token: str):
    """Records the signature. The signed token is the credential (csrf-exempt).
    Idempotent — a second submit just shows the signed state."""
    dpa, err = _load(token)
    if dpa is None:
        return _page('Link problem', f'<h2>Sorry</h2><p class="muted">{escape(err)}</p>', status=400)

    if dpa.status == dpa.Status.SIGNED:
        return _page('Agreement — signed', f"""
          <h2>Already signed</h2>
          <p class="muted">No change made — this agreement was already signed.</p>
          {_signed_banner(dpa)}""")

    name = (request.POST.get('name') or '').strip()
    title = (request.POST.get('title') or '').strip()
    agree = (request.POST.get('agree') or '').strip().lower() == 'yes'
    if not name or not agree:
        return _page('Try again', '<h2>Almost there</h2>'
                     '<p class="muted">Please enter your name and tick the box confirming you accept.</p>',
                     status=400)

    xff = (request.META.get('HTTP_X_FORWARDED_FOR') or '').split(',')[0].strip()
    ip = xff or request.META.get('REMOTE_ADDR', '')

    dpa.status = dpa.Status.SIGNED
    dpa.signatory_name = name
    dpa.signatory_title = title
    dpa.signatory_ip = ip
    dpa.signed_at = timezone.now()
    dpa.signed_doc_sha256 = hashlib.sha256(dpa.document_html.encode('utf-8')).hexdigest()
    dpa.save(update_fields=['status', 'signatory_name', 'signatory_title', 'signatory_ip',
                            'signed_at', 'signed_doc_sha256', 'updated_at'])

    return _page('Signed — thank you', f"""
      <h2>✓ Signed</h2>
      <p class="muted">Thank you, {escape(name)}. The agreement with
      {escape(dpa.processor_name)} is now signed and filed with Alpha Direct&rsquo;s
      Data Protection Officer.</p>
      {_signed_banner(dpa)}""")


# ── the signer's own dashboard card (CFO 2026-07-29: "put in his dashboard") ────
from rest_framework.decorators import api_view, permission_classes  # noqa: E402
from rest_framework.permissions import IsAuthenticated               # noqa: E402
from rest_framework.response import Response                         # noqa: E402


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def my_processor_dpas(request):
    """Agreements awaiting THIS signed-in user's signature, matched on their email.
    Powers the "you have an agreement to sign" card on the user's own dashboard —
    so an Omni-logged-in processor (e.g. Pramod) can review and sign in-app."""
    from core.models import ProcessorDPA
    email = (getattr(request.user, 'email', '') or '').strip().lower()
    items = []
    if email:
        for d in ProcessorDPA.objects.filter(processor_email__iexact=email):
            items.append({
                'reference': d.reference,
                'processor': d.processor_name,
                'purpose': d.purpose,
                'status': d.status,
                'status_label': d.get_status_display(),
                'signed_at': d.signed_at.date().isoformat() if d.signed_at else None,
                'sign_url': sign_url(d),
            })
    pending = [i for i in items if i['status'] != ProcessorDPA.Status.SIGNED]
    return Response({'pending': pending, 'all': items, 'count_pending': len(pending)})


# ── register (for the Data Protection dashboard) ───────────────────────────────
def processor_dpa_register() -> list:
    from core.models import ProcessorDPA
    out = []
    for d in ProcessorDPA.objects.all():
        out.append({
            'reference': d.reference,
            'processor': d.processor_name,
            'country': d.country,
            'adequate': d.adequate,
            'purpose': d.purpose,
            'data_categories': d.data_categories,
            'status': d.status,
            'status_label': d.get_status_display(),
            'signed_at': d.signed_at.date().isoformat() if d.signed_at else None,
            'signatory': d.signatory_name or None,
            'view_url': sign_url(d),   # same token link — shows signed state once signed
        })
    return out
