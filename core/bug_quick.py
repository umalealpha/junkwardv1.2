"""
core/bug_quick.py — report a problem from an email, WITHOUT signing in.

CFO 2026-08-07: *"the banner should not ask to log into omni, it should make it
easy, no login required, so it can wire directly to omni bugs."*

The red do-not-reply banner on every internal email now opens THIS page. Someone
reading Outlook on their phone has no Omni session; sending them to /report-bug
bounced them to a login screen, which is exactly the friction that makes them hit
Reply instead. This page needs no session at all.

Security model — the same one as one-click leave approval (hris/leave_actions.py):
  * A signed, tamper-proof token IS the credential (django.core.signing, salt
    'quick-bug'). Without SECRET_KEY it cannot be forged. The link is only ever
    embedded in internal email, so the endpoint is not discoverable.
  * The GET has NO side effect. Outlook Safe-Links and mail scanners prefetch
    every URL in an email; a link that submitted on GET would file a blank report
    every time a scanner touched it. The report is only created on POST.
  * POST is csrf-exempt because the token is the credential, and the reporter's
    address must be on an internal domain — the same rule as the banner itself.

The report it creates is an ordinary core.BugReport, so it lands on the same board,
gets the same reference and the same status emails as one raised inside Omni.

Deliberate difference from the in-app form: **screenshots are optional here.**
The 50-word minimum stays (CFO 2026-08-06, "50 words compulsory") because a vague
report cannot be acted on — but demanding two screenshots from someone on a phone
in Outlook is the friction this page exists to remove.
"""
from __future__ import annotations

from django.conf import settings
from django.core import signing
from django.http import HttpResponse
from django.utils.html import escape
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

_SALT = 'quick-bug'
# The link is constant so every email can carry the same one; it is the internal
# audience, not the token, that limits who ever sees it.
_PAYLOAD = 'omni-quick-bug'

NAVY = '#0D1B2A'
ORANGE = '#F4A623'
RED = '#C62828'

MIN_WORDS = 50
# An idea is not a fault: there is no screen to describe and nothing broken to
# retell, so 50 words is the wrong bar. Matches the in-app rule (CFO 2026-08-06).
FEATURE_TAG = '[FEATURE REQUEST] '
FEATURE_MIN_WORDS = 25


def make_quick_bug_token() -> str:
    return signing.dumps({'k': _PAYLOAD}, salt=_SALT)


def quick_bug_url() -> str:
    """The URL the email banner points at. Under /api/ so Caddy routes it to
    Django — bare paths are served by the Next frontend and would 404 here."""
    base = getattr(settings, 'PUBLIC_BASE_URL', 'https://omni.alphadirect.co.bw').rstrip('/')
    return f'{base}/api/v1/report-problem/{make_quick_bug_token()}/'


def _token_ok(token: str) -> bool:
    try:
        data = signing.loads(token, salt=_SALT)
    except signing.BadSignature:
        return False
    return isinstance(data, dict) and data.get('k') == _PAYLOAD


def _internal(email: str) -> bool:
    from core.notifications import all_internal
    return all_internal([email])


# ── page shell ──────────────────────────────────────────────────────────────

def _page(title: str, inner: str, *, status: int = 200) -> HttpResponse:
    html = f"""<!DOCTYPE html><html lang="en"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{escape(title)} — Alpha Direct</title>
<style>
  * {{ box-sizing: border-box; }}
  body {{ margin:0; font-family:'Segoe UI',Arial,sans-serif; background:#F3F4F6; color:#1F2937; }}
  .wrap {{ max-width:620px; margin:0 auto; padding:24px 16px; }}
  .card {{ background:#fff; border-radius:14px; overflow:hidden; box-shadow:0 8px 30px rgba(13,27,42,.10); }}
  .head {{ background:{NAVY}; padding:22px 26px; }}
  .head .tag {{ color:{ORANGE}; font-size:11px; letter-spacing:2.2px; text-transform:uppercase; font-weight:700; }}
  .head h1 {{ margin:6px 0 0; color:#fff; font-size:21px; font-weight:700; }}
  .body {{ padding:24px 26px; }}
  label {{ display:block; font-size:13px; font-weight:600; color:{NAVY}; margin:16px 0 6px; }}
  input[type=email], input[type=text], textarea {{ width:100%; padding:11px 12px; border:1px solid #D1D5DB;
      border-radius:9px; font-family:inherit; font-size:15px; }}
  textarea {{ min-height:160px; resize:vertical; }}
  .hint {{ color:#6B7280; font-size:12.5px; margin-top:6px; }}
  .count {{ font-size:12.5px; font-weight:700; margin-top:6px; }}
  .btn {{ display:block; width:100%; text-align:center; padding:15px; border:none;
          border-radius:10px; font-size:16px; font-weight:700; cursor:pointer; margin-top:20px;
          background:{ORANGE}; color:{NAVY}; }}
  .btn[disabled] {{ opacity:.5; cursor:not-allowed; }}
  .err {{ background:#FEF2F2; border-left:4px solid {RED}; color:#7F1D1D; padding:12px 14px;
          border-radius:6px; font-size:14px; margin-bottom:4px; }}
  .ok {{ background:#ECFDF5; border-left:4px solid #059669; color:#065F46; padding:14px 16px;
         border-radius:6px; font-size:15px; }}
  .ref {{ font-family:ui-monospace,Menlo,monospace; font-size:13px; color:#6B7280; margin-top:10px; }}
  .kinds {{ display:flex; gap:10px; }}
  .kind {{ flex:1; margin:0; padding:12px 14px; border:1.5px solid #D1D5DB; border-radius:10px;
           cursor:pointer; font-weight:400; }}
  .kind.on {{ border-color:{ORANGE}; background:#FFF8EC; }}
  .kind input {{ margin-right:8px; }}
  .kind b {{ display:inline; font-size:14px; color:{NAVY}; }}
  .kind span {{ display:block; font-size:12px; color:#6B7280; margin-top:3px; }}
  .foot {{ text-align:center; color:#9CA3AF; font-size:11px; padding:16px; }}
</style></head>
<body><div class="wrap"><div class="card">
  <div class="head"><div class="tag">Alpha Direct · Omni</div><h1>{escape(title)}</h1></div>
  <div class="body">{inner}</div>
</div><div class="foot">Omni ERP — omni.alphadirect.co.bw</div></div></body></html>"""
    return HttpResponse(html, status=status)


def _form(token: str, *, email: str = '', description: str = '', error: str = '',
          kind: str = 'problem') -> HttpResponse:
    err = f'<div class="err">{escape(error)}</div>' if error else ''
    is_idea = (kind == 'idea')
    prob_on, idea_on = ('' if is_idea else ' on'), (' on' if is_idea else '')
    prob_chk, idea_chk = ('' if is_idea else 'checked'), ('checked' if is_idea else '')
    min_w = FEATURE_MIN_WORDS if is_idea else MIN_WORDS
    desc_label = "Tell us your idea" if is_idea else "What went wrong?"
    inner = f"""
    {err}
    <p style="margin:0 0 4px;color:#6B7280;font-size:14px;line-height:1.55">
      This goes straight onto the list. You do not need to sign in. You will get a
      reference number and an email when something happens.
    </p>
    <form method="post" enctype="multipart/form-data">
      <label>What is this?</label>
      <div class="kinds">
        <label class="kind{prob_on}"><input type="radio" name="kind" value="problem" {prob_chk}>
          <b>Something is broken</b><span>A fault to fix</span></label>
        <label class="kind{idea_on}"><input type="radio" name="kind" value="idea" {idea_chk}>
          <b>I have an idea</b><span>Something Omni should do</span></label>
      </div>

      <label for="email">Your work email</label>
      <input id="email" type="email" name="email" required value="{escape(email)}"
             placeholder="you@alphadirect.co.bw">
      <div class="hint">So we can reply to you when it is sorted.</div>

      <label for="description" id="desclabel">{desc_label}</label>
      <textarea id="description" name="description" required
        placeholder="What were you doing, what did you expect, and what happened instead? Which screen were you on? Any message on the screen?">{escape(description)}</textarea>
      <div class="count" id="count">0 / {min_w} words</div>
      <div class="hint">At least <span id="minw">{min_w}</span> words. The detail is what
        lets us act on it without coming back to ask you three questions.</div>

      <label for="shots">Screenshots (optional)</label>
      <input id="shots" type="file" name="screenshots" accept="image/*" multiple>
      <div class="hint">Helpful, but not required — attach them if you have them.</div>

      <button class="btn" type="submit">Send it to the fix-it list</button>
    </form>
    <script>
      (function () {{
        var t = document.getElementById('description'), c = document.getElementById('count');
        var mw = document.getElementById('minw'), dl = document.getElementById('desclabel');
        var min = {min_w};
        function upd() {{
          var n = t.value.trim().split(/\\s+/).filter(Boolean).length;
          c.textContent = n + ' / ' + min + ' words' + (n >= min ? ' \\u2713' : '');
          c.style.color = n >= min ? '#059669' : '{ORANGE}';
        }}
        Array.prototype.forEach.call(document.querySelectorAll('input[name=kind]'), function (r) {{
          r.addEventListener('change', function () {{
            var idea = r.value === 'idea' && r.checked;
            min = idea ? {FEATURE_MIN_WORDS} : {MIN_WORDS};
            mw.textContent = min;
            dl.textContent = idea ? 'Tell us your idea' : 'What went wrong?';
            Array.prototype.forEach.call(document.querySelectorAll('.kind'), function (k) {{
              k.classList.toggle('on', k.contains(r) === r.checked ? k.contains(r) : k.contains(document.querySelector('input[name=kind]:checked')));
            }});
            upd();
          }});
        }});
        t.addEventListener('input', upd); upd();
      }})();
    </script>"""
    return _page('Report a problem', inner)


# ── views ───────────────────────────────────────────────────────────────────

@csrf_exempt
@require_http_methods(['GET', 'POST'])
def report_problem(request, token: str):
    if not _token_ok(token):
        return _page('Link not valid', '<div class="err">This link is not valid. '
                     'Please use the button in a recent Omni email.</div>', status=400)

    if request.method == 'GET':
        # No side effect — mail scanners prefetch every link in an email.
        return _form(token, email=(request.GET.get('e') or '').strip(),
                     kind=(request.GET.get('kind') or 'problem'))

    email = (request.POST.get('email') or '').strip()
    description = (request.POST.get('description') or '').strip()
    kind = (request.POST.get('kind') or 'problem').strip()
    is_idea = kind == 'idea'
    min_words = FEATURE_MIN_WORDS if is_idea else MIN_WORDS
    words = len([w for w in description.split() if w])

    if not _internal(email):
        return _form(token, email=email, description=description, kind=kind,
                     error='Please use your Alpha Direct work email address.')
    if words < min_words:
        return _form(token, email=email, description=description, kind=kind,
                     error=f'Please describe it in at least {min_words} words '
                           f'(you wrote {words}). The detail is what lets us act on it.')

    from django.contrib.auth.models import User
    from core.models import BugReport

    shots = request.FILES.getlist('screenshots')[:10]
    attachments = [(f.name, f.read(), (f.content_type or 'image/png')) for f in shots]

    reporter = User.objects.filter(email__iexact=email).first()
    stored = (FEATURE_TAG + description) if is_idea else description
    report = BugReport.objects.create(
        reporter=reporter,
        reporter_email=email,
        description=stored,
        word_count=words,
        screenshot_count=len(shots),
        page_url='(reported from an Omni email — no sign-in)',
        status=BugReport.Status.NEW,
    )

    try:
        from core.bug_report_views import BugReportView, _BUG_NOTIFY
        from core.notifications import send_html_with_cfo_cc
        html = BugReportView._build_html(
            report_id=str(report.id),
            reporter_name=(reporter.get_full_name() if reporter else email),
            reporter_email=email,
            page_url='reported from an email link',
            word_count=words,
            shot_count=len(shots),
            description=stored,
        )
        # no_reply=False: this one asks the triager to act, and the reply-to is
        # the reporter — a do-not-reply banner here would be wrong.
        sent = send_html_with_cfo_cc(
            subject=(f'[Omni Feature Request] {email} — {words} words (from email link)'
                     if is_idea else
                     f'[Omni Bug Report] {email} — {words} words, {len(shots)} screenshots (from email link)'),
            html=html, to=_BUG_NOTIFY, reply_to=[email],
            attachments=attachments, no_reply=False)
        report.emailed_ok = bool(sent)
        report.save(update_fields=['emailed_ok'])
    except Exception:                                    # noqa: BLE001
        # The row is saved either way — a flaky mailer must never lose a report.
        pass

    return _page('Thank you', f"""
      <div class="ok"><b>Got it — thank you.</b><br>
        {'Your idea is on the list' if is_idea else 'It is on the fix-it list'} now, and we will email you at
        {escape(email)} when the status changes.</div>
      <div class="ref">Reference: {escape(str(report.id))}</div>
      <p class="hint" style="margin-top:18px">You can close this page.</p>""")
