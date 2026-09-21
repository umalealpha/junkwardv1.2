"""recruitment/candidate_status.py — one-click, no-login applicant status page.

CFO 2026-08-26 (H2). An applicant gets a link (in their acknowledgement email)
that opens a page telling them, in the plainest terms, where their application
stands. It is deliberately COARSE — received / under review / not successful —
and shows ONLY that person's own name + the role. It never shows a score, a
note, an interview scorecard, or anything about any other candidate.

The token (django signing) encodes ONLY {application_id} — signed (can't be
forged), expires (default 180 days). CSRF-exempt because the token is the gate,
and the page is read-only (GET) so there is nothing to forge a POST against.
Mirrors hris.leave_explain.
"""
from __future__ import annotations

from django.core import signing
from django.http import HttpResponse
from django.utils.html import escape
from django.views.decorators.csrf import csrf_exempt

SALT = 'applicant-status-v1'
MAX_AGE = 60 * 60 * 24 * 180   # 180 days
BASE = 'https://omni.alphadirect.co.bw'
NAVY, ORANGE, INK, MUT = '#0D1B2A', '#F4A623', '#1F2937', '#6B7280'

# Stage → the three coarse states the CFO fixed. Nothing finer is revealed:
# an offer/hired candidate still reads "under review" here (the offer itself
# reaches them through HR directly, not this page).
_COARSE = {
    'applied':     ('received',       'We have received your application.'),
    'screening':   ('under review',   'Your application is being reviewed.'),
    'interview_1': ('under review',   'Your application is being reviewed.'),
    'interview_2': ('under review',   'Your application is being reviewed.'),
    'offer':       ('under review',   'Your application is being reviewed.'),
    'hired':       ('under review',   'Your application is being reviewed.'),
    'rejected':    ('not successful', 'Your application was not successful on this occasion.'),
}


def coarse_status(stage: str) -> tuple[str, str]:
    """(label, sentence) for a stage. Unknown stages read as 'received'."""
    return _COARSE.get((stage or '').strip(), _COARSE['applied'])


def make_token(application_id) -> str:
    return signing.dumps({'a': str(application_id)}, salt=SALT)


def read_token(token: str):
    try:
        return signing.loads(token, salt=SALT, max_age=MAX_AGE)
    except signing.BadSignature:
        return None


def status_url(application) -> str:
    return f'{BASE}/api/applicant-status/{make_token(application.id)}/'


def _shell(inner: str, title: str = 'Application status — Omni') -> str:
    return f"""<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{escape(title)}</title></head>
<body style="margin:0;background:#EEF0F3;font-family:Georgia,'Book Antiqua',serif;color:{INK}">
<div style="max-width:600px;margin:24px auto;background:#fff;border-radius:12px;overflow:hidden;box-shadow:0 1px 6px rgba(0,0,0,.08)">
<div style="background:{NAVY};padding:16px 24px"><span style="color:#fff;font-size:20px;letter-spacing:1px">OMNI</span>
<span style="color:{ORANGE};font-size:13px;margin-left:8px">Alpha Direct</span></div>
<div style="padding:24px;font-size:15px;line-height:1.55">{inner}</div></div></body></html>"""


@csrf_exempt
def status_page(request, token):
    data = read_token(token)
    if not data:
        return HttpResponse(_shell(
            '<p>This link is invalid or has expired. Please contact our HR team '
            'if you need an update on your application.</p>'), status=400)
    # Import here so the module has no import-time model dependency.
    from .models import Application
    app = (Application.objects
           .filter(pk=data.get('a'))
           .select_related('candidate', 'requisition')
           .first())
    if app is None:
        return HttpResponse(_shell(
            '<p>We could not find this application. Please contact our HR team.</p>'),
            status=404)
    name = (app.candidate.full_name or '').strip() or 'there'
    role = (app.requisition.title or 'the role').strip()
    label, sentence = coarse_status(app.stage)
    pill = {'received': '#2563EB', 'under review': ORANGE, 'not successful': '#6B7280'}.get(label, ORANGE)
    return HttpResponse(_shell(f"""
<p style="margin-top:0">Hello {escape(name)},</p>
<p>Thank you for applying for <strong>{escape(role)}</strong> at Alpha Direct.</p>
<p style="margin:18px 0 6px;color:{MUT};font-size:13px">Your application status</p>
<div style="display:inline-block;background:{pill};color:#fff;font-weight:700;
     font-size:15px;padding:10px 18px;border-radius:8px;text-transform:capitalize">{escape(label)}</div>
<p style="margin-top:16px">{escape(sentence)}</p>
<p style="color:{MUT};font-size:12px;margin-top:22px">This page shows only the current stage of your
own application. For anything more, please contact our HR team. Thank you for your interest in Alpha Direct.</p>
"""))
