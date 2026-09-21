"""Manager Accountability — the no-login response page (CFO 2026-07-25).

The manager clicks "Answer now" in their note. The signed token (note id) is the
gate; the page shows only that manager's own dark reports + the hard questions,
and captures a required written answer that posts back to the note Human
Resources reads. People matters say Human Resources, never "the CFO"
(CFO 2026-08-03).
"""
from __future__ import annotations

from django.http import HttpResponse
from django.utils import timezone
from django.utils.html import escape
from django.views.decorators.csrf import csrf_exempt

from hris import manager_accountability as ma

NAVY, ORANGE, INK, MUT, RED = ma.NAVY, ma.ORANGE, ma.INK, ma.MUT, ma.RED
MIN_WORDS = 20


def _shell(inner: str, title: str = 'Answer — Omni') -> str:
    return (f'<!doctype html><html><head><meta charset="utf-8">'
            f'<meta name="viewport" content="width=device-width, initial-scale=1">'
            f'<title>{escape(title)}</title></head>'
            f'<body style="margin:0;background:#EEF0F3;font-family:Georgia,\'Book Antiqua\',serif;color:{INK}">'
            f'<div style="max-width:620px;margin:24px auto;background:#fff;border-radius:12px;overflow:hidden;box-shadow:0 1px 6px rgba(0,0,0,.08)">'
            f'<div style="background:{NAVY};padding:16px 24px"><span style="color:#fff;font-size:20px;letter-spacing:1px">OMNI</span>'
            f'<span style="color:{ORANGE};font-size:13px;margin-left:8px">Alpha Direct</span></div>'
            f'<div style="padding:24px;font-size:15px;line-height:1.55">{inner}</div></div></body></html>')


def _reports_html(reports) -> str:
    rows = ''.join(
        f'<li><b>{escape(r.get("name",""))}</b> — last tracked {escape(str(r.get("last_tracked","")))}, '
        f'<span style="color:{RED}">{r.get("days_dark","?")} days dark</span></li>'
        for r in (reports or []))
    return f'<ul style="margin:8px 0 0;padding-left:20px">{rows}</ul>'


@csrf_exempt
def answer_page(request):
    token = request.GET.get('t', '')
    data = ma.read_token(token)
    if not data:
        return HttpResponse(_shell('<p>This link is invalid or has expired. Please contact Human Resources.</p>'),
                            status=400)
    from hris.models import ManagerAccountabilityNote
    note = ManagerAccountabilityNote.objects.filter(id=data.get('n')).select_related('manager').first()
    if note is None:
        return HttpResponse(_shell('<p>We could not find this note. Please contact Human Resources.</p>'), status=404)

    mgr_name = (getattr(note.manager, 'full_name', '') or '').strip() or 'Manager'
    first = mgr_name.split()[0]

    if note.responded_at:
        return HttpResponse(_shell(
            f'<p>Thank you, {escape(first)} — your response was recorded on '
            f'{escape(timezone.localtime(note.responded_at).strftime("%H:%M, %d %b"))} and is with Human Resources. '
            f'Nothing further is needed.</p>'))

    if request.method == 'POST':
        text = (request.POST.get('response') or '').strip()
        if len(text.split()) < MIN_WORDS:
            return HttpResponse(_form(first, note, error=f'Please give a real answer — at least {MIN_WORDS} words.',
                                      text=text), status=400)
        note.response = text
        note.responded_at = timezone.now()
        note.save(update_fields=['response', 'responded_at', 'updated_at'])
        return HttpResponse(_shell(
            f'<p><b>Recorded — thank you, {escape(first)}.</b></p>'
            f'<p>Your answer has gone to Human Resources. Please make sure your team is tracking today; '
            f'a repeat gap with no explanation will be reviewed with Human Capital.</p>'))

    return HttpResponse(_form(first, note))


def _form(first, note, *, error='', text='') -> str:
    err = (f'<div style="background:#FEF2F2;border:1px solid {RED};border-radius:8px;'
           f'padding:10px 14px;margin-bottom:14px;color:{RED}">{escape(error)}</div>') if error else ''
    n = len(note.reports or [])
    return _shell(
        f'{err}'
        f'<p style="margin-top:0">Hello {escape(first)},</p>'
        f'<p><b>{n}</b> of your team recorded <b>no productive time for 3+ days</b>, with no leave or client '
        f'visit logged:</p>{_reports_html(note.reports)}'
        f'<p style="margin-top:16px;font-weight:700">Please answer, on the record:</p>'
        f'<ol style="margin:6px 0 0;padding-left:20px">'
        f'<li>Did you know they were not tracking, and what have you done about it?</li>'
        f'<li>Why was no leave or client visit logged for them?</li>'
        f'<li>If the team can run 3 days with nobody recording work — is it over-staffed? Justify the headcount.</li>'
        f'</ol>'
        f'<form method="post">'
        f'<textarea name="response" rows="8" placeholder="Your answer (at least {MIN_WORDS} words)…" '
        f'style="width:100%;box-sizing:border-box;padding:10px;border:1px solid #ccc;border-radius:8px;'
        f'font:inherit;margin-top:12px">{escape(text)}</textarea>'
        f'<button type="submit" style="margin-top:12px;background:{NAVY};color:{ORANGE};font-weight:700;'
        f'font-size:15px;border:0;border-radius:8px;padding:12px 22px;cursor:pointer">Submit my answer</button>'
        f'</form>'
        f'<p style="color:{MUT};font-size:12px;margin-top:18px">This goes straight to Human Resources. No answer by '
        f'the deadline escalates within Human Resources with your name on it.</p>')
