"""hris/leave_explain.py — one-click, no-login "explain your day" page.

CFO 2026-07-22 (CEO-approved): staff flagged with no Time Doctor hours on a day
get an email with a BUTTON. The button opens this page — pre-identified by a
signed token (no login, no navigation) — where they say whether they were
working (office / off-site) or on leave, and write a >=50-word explanation.
It's stored on the backend (WorkdayJustification), shows in the CFO Excuses
feed, and goes to a manager for review. Leave for non-responders is applied
separately (with CFO sign-off), never from this page.

The token (django signing) encodes ONLY {employee_id, date} — the page reveals
just that person's own name + that one date, nothing else. Signed (can't be
forged) and expires (default 21 days). CSRF-exempt because the token is the gate.
"""
from __future__ import annotations

import datetime
from decimal import Decimal

from django.core import signing
from django.http import HttpResponse
from django.utils import timezone
from django.utils.html import escape
from django.views.decorators.csrf import csrf_exempt

from hris import workforce

SALT = 'leave-explain-v1'
MAX_AGE = 60 * 60 * 24 * 21   # 21 days
MIN_CHARS = 25                # CFO 2026-07-27: was 50 WORDS — too high, staff gave up
NAVY, ORANGE, INK, MUT = '#0D1B2A', '#F4A623', '#1F2937', '#6B7280'


def make_token(employee_id, date_iso: str) -> str:
    return signing.dumps({'e': str(employee_id), 'd': date_iso}, salt=SALT)


def make_plan_token(employee_id) -> str:
    """Employee-only token (no date) → the no-login page where they pick ANY
    upcoming/recent day and explain it (golf day, client visit, leave, off-site).
    Used by the morning-brief 'Tell us about an upcoming day' button so it opens
    the explain page directly instead of an omni login."""
    return signing.dumps({'e': str(employee_id)}, salt=SALT)


def read_token(token: str):
    try:
        return signing.loads(token, salt=SALT, max_age=MAX_AGE)
    except signing.BadSignature:
        return None


def _shell(inner: str, title: str = 'Omni') -> str:
    return f"""<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{escape(title)}</title></head>
<body style="margin:0;background:#EEF0F3;font-family:Georgia,'Book Antiqua',serif;color:{INK}">
<div style="max-width:600px;margin:24px auto;background:#fff;border-radius:12px;overflow:hidden;box-shadow:0 1px 6px rgba(0,0,0,.08)">
<div style="background:{NAVY};padding:16px 24px"><span style="color:#fff;font-size:20px;letter-spacing:1px">OMNI</span>
<span style="color:{ORANGE};font-size:13px;margin-left:8px">Alpha Direct</span></div>
<div style="padding:24px;font-size:15px;line-height:1.55">{inner}</div></div></body></html>"""


def _form(name: str, d, *, error='', worked='', place='', text='', planning=False) -> str:
    err = (f'<div style="background:#FEF2F2;border:1px solid #DC2626;border-radius:8px;'
           f'padding:10px 14px;margin-bottom:14px;color:#DC2626">{escape(error)}</div>') if error else ''
    def ck(v, cur): return 'checked' if v == cur else ''
    if planning:
        # Employee-only token: they pick the day themselves (golf day, client
        # visit, leave, off-site) — used by the morning-brief button.
        dval = (d or timezone.localdate()).isoformat()
        intro = ("""<p>Going to be out or short on a day — a golf day, client visit, leave or off-site?
Tell us here so you are not flagged for low hours. This takes a minute and is kept on record.</p>
<p style="font-weight:700;margin:14px 0 6px">Which day?</p>
<input type="date" name="work_date" value="%s"
       style="width:100%%;box-sizing:border-box;padding:10px;border:1px solid #ccc;border-radius:8px;font:inherit">""" % escape(dval))
    else:
        dtxt = d.strftime('%A, %d %B %Y')
        intro = (f'<p>Time Doctor recorded <strong>no hours</strong> for you on <strong>{escape(dtxt)}</strong>.'
                 f' Please tell us what happened. This takes a minute and is kept on record.</p>')
    return _shell(f"""
{err}
<p style="margin-top:0">Hello {escape(name)},</p>
{intro}
<form method="post">
  <p style="font-weight:700;margin-bottom:6px">1. On that day I was:</p>
  <label style="display:block;margin:4px 0"><input type="radio" name="worked" value="worked" {ck('worked',worked)}> Working</label>
  <label style="display:block;margin:4px 0"><input type="radio" name="worked" value="leave" {ck('leave',worked)}> On leave / not working</label>
  <p style="font-weight:700;margin:14px 0 6px">2. If working, where?</p>
  <label style="display:block;margin:4px 0"><input type="radio" name="place" value="office" {ck('office',place)}> In the office</label>
  <label style="display:block;margin:4px 0"><input type="radio" name="place" value="offsite" {ck('offsite',place)}> Off-site (client / field / etc.)</label>
  <p style="font-weight:700;margin:14px 0 6px">3. Explain in your own words (a sentence or two is fine):</p>
  <textarea name="explanation" rows="7" style="width:100%;box-sizing:border-box;padding:10px;border:1px solid #ccc;border-radius:8px;font:inherit">{escape(text)}</textarea>
  <button type="submit" style="margin-top:14px;background:{NAVY};color:#fff;font-weight:700;font-size:15px;border:0;border-radius:8px;padding:12px 22px;cursor:pointer">Submit</button>
</form>
<p style="color:{MUT};font-size:12px;margin-top:18px">If you actually worked and this looks wrong, still submit and say so — it will be reviewed. Questions: your manager or HR.</p>
""", 'Explain your day — Omni')


@csrf_exempt
def explain_page(request, token):
    data = read_token(token)
    if not data:
        return HttpResponse(_shell('<p>This link is invalid or has expired. Please contact HR.</p>'), status=400)
    from hris.models import HRISProfile, WorkdayJustification
    # Two token shapes: {e,d} = a fixed day (the "you were short on <date>" email
    # button); {e} = employee-only, they pick the day (the morning-brief button).
    planning = not data.get('d')
    d = None
    if not planning:
        try:
            d = datetime.date.fromisoformat(data['d'])
        except (KeyError, ValueError):
            return HttpResponse(_shell('<p>This link is invalid. Please contact HR.</p>'), status=400)
    prof = HRISProfile.objects.filter(employee_id=data.get('e')).select_related('employee').first()
    if prof is None or not getattr(prof, 'employee', None):
        return HttpResponse(_shell('<p>We could not find your record. Please contact HR.</p>'), status=404)
    name = (prof.employee.full_name or '').strip() or 'there'

    if request.method == 'POST':
        worked = request.POST.get('worked', '')
        place = request.POST.get('place', '')
        text = (request.POST.get('explanation') or '').strip()
        if planning:
            # Employee chose the date on the page — validate + bound it so the
            # token can't be used to write arbitrary far-off records.
            # The date input pre-fills today. iOS Safari does not always submit an
            # <input type="date"> value the user never tapped, so an untouched
            # submit arrives with work_date empty — treat that as the day shown
            # (today) rather than hard-blocking the staffer (Medu Tlagae hit this
            # 2026-08-13: filled everything, left the date on today, got blocked).
            raw = (request.POST.get('work_date') or '').strip()
            try:
                d = datetime.date.fromisoformat(raw) if raw else timezone.localdate()
            except ValueError:
                return HttpResponse(_form(name, None, error='Please choose the day.',
                                          worked=worked, place=place, text=text, planning=True), status=400)
            today = timezone.localdate()
            if not (today - datetime.timedelta(days=31) <= d <= today + datetime.timedelta(days=60)):
                return HttpResponse(_form(name, d, error='Please choose a day within the last month or the next few weeks.',
                                          worked=worked, place=place, text=text, planning=True), status=400)
        if worked not in ('worked', 'leave'):
            return HttpResponse(_form(name, d, error='Please choose whether you were working or on leave.',
                                      worked=worked, place=place, text=text, planning=planning), status=400)
        # 25 CHARACTERS, not 50 words (CFO 2026-07-27). 50 words was far too high
        # a bar for "why was Saturday short" — staff hit the wall and gave up
        # instead of explaining (Ikanyeng Sechele, 2026-07-27). A short honest
        # sentence is the point; the manager reads it, not a word counter.
        if len(text) < MIN_CHARS:
            return HttpResponse(_form(name, d, error=f'Please write a bit more — at least {MIN_CHARS} '
                                                     f'characters (you wrote {len(text)}).',
                                      worked=worked, place=place, text=text, planning=planning), status=400)
        row, _ = WorkdayJustification.objects.get_or_create(
            profile=prof, work_date=d,
            defaults={'required_hours': workforce.required_hours_for_date(d),
                      'tracked_hours': Decimal('0')})
        if worked == 'leave':
            row.reason = WorkdayJustification.Reason.ON_LEAVE
            prefix = '[On leave] '
            row.location = ''
        else:
            row.reason = WorkdayJustification.Reason.OTHER
            prefix = f"[Worked - {'Office' if place == 'office' else 'Off-site'}] "
            row.location = place
        row.justification = prefix + text
        row.status = WorkdayJustification.Status.EXPLAINED
        row.responded_at = timezone.now()
        row.save()
        return HttpResponse(_shell(
            f'<p style="margin-top:0">Thank you, {escape(name)}.</p>'
            f'<p>Your explanation for <strong>{escape(d.strftime("%A, %d %B %Y"))}</strong> has been '
            f'recorded and sent to management for review. You can close this page.</p>'))
    return HttpResponse(_form(name, d, planning=planning))
