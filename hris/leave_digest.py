"""
hris/leave_digest.py — the HR daily leave digest (Unami Butale, 2026-07-27).

"Send him a daily digest, him only: who is on leave today, and who is going on
leave in the next 2 weeks." One branded email to the HR head each morning.

`build_leave_digest(today)` returns {subject, html} or None when there is nothing
to report (no one on leave today and no one off in the next 14 days) so the cron
stays quiet on empty days.
"""
from __future__ import annotations

import datetime as _dt

from django.utils.html import escape

from core.date_format import format_dt
from hris.models import LeaveRequest
from django.utils import timezone

NAVY = '#0D1B2A'
ORANGE = '#F4A623'


def _fmt_days(n) -> str:
    return f'{float(n or 0):g}'


def _rows_html(requests, *, show_start: bool) -> str:
    cells = ''
    for lr in requests:
        emp = lr.profile.employee
        dept = emp.department or '—'
        lt = lr.leave_type.name if lr.leave_type_id else 'Leave'
        when = (f'from {format_dt(lr.start_date, "%a %-d %b")}' if show_start
                else f'until {format_dt(lr.end_date, "%a %-d %b")}')
        cells += (
            f'<tr>'
            f'<td style="padding:8px 0;border-bottom:1px solid #EEF0F3;font-weight:600;">'
            f'{escape(emp.full_name)}</td>'
            f'<td style="padding:8px 0;border-bottom:1px solid #EEF0F3;color:#6B7280;">'
            f'{escape(dept)}</td>'
            f'<td style="padding:8px 0;border-bottom:1px solid #EEF0F3;">'
            f'{escape(lt)}</td>'
            f'<td style="padding:8px 0;border-bottom:1px solid #EEF0F3;text-align:right;'
            f'white-space:nowrap;color:#6B7280;">{escape(when)} · {_fmt_days(lr.days)}d</td>'
            f'</tr>')
    return cells


def _section(title: str, subtitle: str, rows_html: str, empty_msg: str) -> str:
    body = (f'<table style="width:100%;border-collapse:collapse;font-size:14px;">'
            f'{rows_html}</table>'
            if rows_html else
            f'<p style="color:#6B7280;font-size:14px;margin:4px 0 0;">{escape(empty_msg)}</p>')
    return f"""
      <div style="margin:22px 0 6px;">
        <div style="font-weight:700;color:{NAVY};font-size:16px;">{escape(title)}</div>
        <div style="color:#6B7280;font-size:12px;margin:2px 0 10px;">{escape(subtitle)}</div>
        {body}
      </div>"""


def build_leave_digest(today: _dt.date | None = None) -> dict | None:
    """Assemble the daily leave digest. Returns {subject, html} or None if empty."""
    if today is None:
        from django.utils import timezone
        today = timezone.localdate()
    horizon = today + _dt.timedelta(days=14)

    approved = LeaveRequest.Status.APPROVED
    on_today = list(LeaveRequest.objects
                    .filter(status=approved, start_date__lte=today, end_date__gte=today)
                    .select_related('profile__employee', 'leave_type')
                    .order_by('profile__employee__full_name'))
    upcoming = list(LeaveRequest.objects
                    .filter(status=approved, start_date__gt=today, start_date__lte=horizon)
                    .select_related('profile__employee', 'leave_type')
                    .order_by('start_date'))

    if not on_today and not upcoming:
        return None

    today_section = _section(
        f'On leave today · {len(on_today)}',
        format_dt(today, '%A, %-d %B %Y'),
        _rows_html(on_today, show_start=False),
        'Nobody is on leave today.')
    upcoming_section = _section(
        f'Going on leave in the next 2 weeks · {len(upcoming)}',
        f'Approved leave starting up to {format_dt(horizon, "%-d %b")}',
        _rows_html(upcoming, show_start=True),
        'No approved leave starts in the next 2 weeks.')

    inner = f"""
      <h2 style="margin:0 0 4px;font-size:20px;color:{NAVY};">Daily leave digest</h2>
      <p style="color:#6B7280;font-size:14px;margin:0;">
        {len(on_today)} on leave today · {len(upcoming)} off in the next 2 weeks.</p>
      {today_section}{upcoming_section}"""

    html = f"""<!DOCTYPE html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"></head>
<body style="margin:0;background:#F3F4F6;font-family:'Segoe UI',Arial,sans-serif;color:#1F2937;">
  <div style="max-width:640px;margin:0 auto;padding:20px 12px;">
    <div style="background:#fff;border-radius:14px;overflow:hidden;box-shadow:0 6px 24px rgba(13,27,42,.08);">
      <div style="background:{NAVY};padding:20px 26px;">
        <div style="color:{ORANGE};font-size:18px;font-weight:700;">Alpha Direct · Leave Digest</div>
      </div>
      <div style="padding:22px 26px;">{inner}</div>
    </div>
    <p style="text-align:center;color:#9CA3AF;font-size:11px;margin-top:14px;">
      Omni ERP — omni.alphadirect.co.bw · sent each working morning to HR.</p>
  </div>
</body></html>"""

    subject = f'Leave digest — {len(on_today)} on leave today, {len(upcoming)} off soon ({format_dt(today, "%-d %b")})'
    return {'subject': subject, 'html': html}
