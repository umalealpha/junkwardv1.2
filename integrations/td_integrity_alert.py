"""
integrations/td_integrity_alert.py

The "Time-tracking integrity" block for the 09:00 (SAST) manager Morning Brief:
a daily alert listing anyone the frozen-screen fraud detector flagged yesterday
(heavy typing on a screen that never changes — the weight-on-a-key cheat).

Design goals (CFO 2026-09-01, "carefully design so we don't miss"):
  * It runs EVERY morning off the already-live detector (td_screenshot_integrity).
  * Silence never means "not checked": a clean day still prints a green
    "✓ checked N people, nothing flagged" line; a data outage prints an amber
    "could not run, will retry" line. There is no blank/absent state.
  * It can NEVER break the Morning Brief — morning_integrity_html() catches
    everything and returns the amber box on any failure, so the brief still sends.
  * Privacy (AD-POL-AI-GOV-001): names + counts only. No window titles, no images.
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from html import escape

NAVY, ORANGE, INK, MUT = '#1D3270', '#F47C20', '#1F2937', '#6B7280'
RED, AMBER, GREEN = '#B91C1C', '#B45309', '#0F7B3F'

_SAST = timezone(timedelta(hours=2))          # Botswana, no DST
_USER_BATCH = 10                              # TD truncates a whole-company files pull


def _sast_bounds(day: date):
    start = datetime.combine(day, time(0, 0), _SAST).astimezone(timezone.utc)
    return start, start + timedelta(days=1)


def _wrap(inner: str) -> str:
    return (f'<div style="margin:22px 0 0 0;border:1px solid #E6E9EF;border-radius:10px;'
            f'overflow:hidden;font-family:Georgia,\'Book Antiqua\',serif">'
            f'<div style="background:{NAVY};padding:12px 16px;color:#fff;font-size:15px;'
            f'font-weight:700">&#9201; Time-tracking integrity</div>'
            f'<div style="padding:14px 16px;font-size:13px;color:{INK};line-height:1.5">'
            f'{inner}</div></div>')


def _line(color: str, text: str) -> str:
    return f'<p style="margin:0;color:{color};font-weight:600">{text}</p>'


def render(day, flags, checked: int) -> str:
    """Build the block HTML from an already-computed flagged list (pure — testable
    offline). `flags` is a list of td_screenshot_integrity.ScreenSignal."""
    try:
        day_lbl = day.strftime('%a %d %b %Y')
    except Exception:      # noqa: BLE001
        day_lbl = str(day)

    if not flags and not checked:
        # No screenshots came back at all -> the check did NOT run. Never show the
        # green tick over zero people (Fable 2026-09-01).
        return _wrap(_line(AMBER,
            f'&#9888;&#65039; No screenshot data came back for {day_lbl} &mdash; the '
            f'integrity check could not run; check Time Doctor screenshots.'))
    if not flags:
        return _wrap(
            _line(GREEN, f'&#10003; Checked {checked} staff for {day_lbl} &mdash; nothing flagged.')
            + f'<p style="margin:8px 0 0 0;color:{MUT}">This line appears every morning so you '
              f'know the check ran, even when it is clean.</p>')

    rows = ''
    for s in flags:
        badge = RED if s.suspicion == 'suspicious' else AMBER
        name = escape((s.name or str(s.user_id or '?'))[:40])
        rows += (
            f'<tr>'
            f'<td style="padding:6px 10px;border-bottom:1px solid #EEF0F3;color:{INK}">{name}</td>'
            f'<td style="padding:6px 10px;border-bottom:1px solid #EEF0F3;text-align:center">'
            f'<span style="color:#fff;background:{badge};padding:2px 8px;border-radius:10px;'
            f'font-size:11px">{s.suspicion}</span></td>'
            f'<td style="padding:6px 10px;border-bottom:1px solid #EEF0F3;text-align:right;'
            f'color:{INK}">{s.frozen_typing_pct*100:.0f}%</td>'
            f'<td style="padding:6px 10px;border-bottom:1px solid #EEF0F3;text-align:right;'
            f'color:{MUT}">{s.frozen_typing_hours:.1f}h</td>'
            f'</tr>')
    n_susp = sum(1 for s in flags if s.suspicion == 'suspicious')
    head = _line(RED if n_susp else AMBER,
                 f'&#9888;&#65039; {len(flags)} to review for {day_lbl} '
                 f'({n_susp} strong, {len(flags) - n_susp} watch).')
    table = (
        f'<table style="border-collapse:collapse;width:100%;margin:10px 0 8px 0;font-size:13px">'
        f'<thead><tr style="background:#F3F5F9">'
        f'<th style="padding:6px 10px;text-align:left;color:{MUT}">Employee</th>'
        f'<th style="padding:6px 10px;text-align:center;color:{MUT}">Flag</th>'
        f'<th style="padding:6px 10px;text-align:right;color:{MUT}">Frozen-screen typing</th>'
        f'<th style="padding:6px 10px;text-align:right;color:{MUT}">Hours</th>'
        f'</tr></thead><tbody>{rows}</tbody></table>')
    note = (f'<p style="margin:6px 0 0 0;color:{MUT}">These staff logged heavy typing on a screen '
            f'that never changed (the weight-on-a-key pattern). This is a prompt to have them '
            f'explain their day &mdash; not a conclusion. Faking tracked time is misconduct under '
            f'the disciplinary code and the Employment Act.</p>')
    return _wrap(head + table + note)


def morning_integrity_html(day, *, client=None, users=None) -> str:
    """Compute the block for `day` (a date). NEVER raises: any failure returns the
    amber 'could not run' box so the Morning Brief can always still send."""
    try:
        from integrations.timedoctor import TimeDoctorClient
        from integrations.td_screenshot_integrity import analyze_day, flagged

        if client is None:
            client = TimeDoctorClient.from_settings()
        if not getattr(client, 'configured', False):
            return _wrap(_line(AMBER, '&#9888;&#65039; Time Doctor not configured &mdash; '
                                      'integrity check skipped today.'))
        if users is None:
            users = client.users()
        ids = [u.get('id') for u in (users or []) if u.get('id')]
        d_from, d_to = _sast_bounds(day)
        files = []
        for i in range(0, len(ids), _USER_BATCH):
            files.extend(client.files(d_from, d_to, user_ids=ids[i:i + _USER_BATCH]) or [])
        sigs = analyze_day(files, users)
        return render(day, flagged(sigs), checked=len(sigs))
    except Exception:      # noqa: BLE001 — must never break the brief
        return _wrap(
            _line(AMBER, '&#9888;&#65039; The integrity check could not run this morning; '
                         'it will retry tomorrow.')
            + f'<p style="margin:8px 0 0 0;color:{MUT}">You are seeing this line (not a blank) '
              f'on purpose, so a missed check is never mistaken for a clean day.</p>')
