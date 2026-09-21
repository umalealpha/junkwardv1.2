"""
hris/exec_signoff_email.py — the email that asks the CEO or CFO to countersign
an application made by someone carrying long-overdue work (CFO 2026-08-07).

House style: navy header, orange title, one big button, readable on a phone.
The button opens the sign-in-free page in hris.exec_signoff_actions.
"""
from __future__ import annotations

from django.utils.html import escape

NAVY = '#0D1B2A'
ORANGE = '#F4A623'


def build_exec_signoff_email(signoff, signer) -> dict | None:
    """{subject, html, to} or None when the signer has no email address."""
    from hris.exec_signoff_actions import action_url

    to = (getattr(signer, 'email', '') or '').strip()
    if not to:
        return None

    what = signoff.get_module_display().lower()
    subject = f'Sign-off needed: {signoff.applicant_name} — {what}'
    url = action_url(signoff, signer)

    tasks = (signoff.overdue_snapshot or {}).get('tasks') or []
    rows = ''.join(
        f'<tr><td style="padding:6px 0;border-bottom:1px solid #EEF0F3;font-size:13px;">'
        f'{escape(t.get("title") or "—")}</td>'
        f'<td style="padding:6px 0;border-bottom:1px solid #EEF0F3;font-size:13px;'
        f'text-align:right;color:#B45309;font-weight:700;">'
        f'{int(t.get("days_overdue") or 0)} days late</td></tr>'
        for t in tasks[:10])
    more = ('' if len(tasks) <= 10 else
            f'<tr><td colspan="2" style="padding:6px 0;font-size:12px;color:#6B7280;">'
            f'… and {len(tasks) - 10} more</td></tr>')
    table = (f'<table style="width:100%;border-collapse:collapse;margin:12px 0;">{rows}{more}</table>'
             if rows else '')

    # The fail-safe path has no task list. Saying "carrying work past its due
    # date" above an empty box accuses somebody of something we did not
    # establish (Fable review 2026-08-07).
    if (signoff.overdue_snapshot or {}).get('check_failed'):
        lead = (f'has applied for <b>{escape(what)}</b>. The overdue-work check could not '
                f'run, so it is held for your eye rather than passed through unchecked. '
                f'Nothing is known to be wrong.')
        box = ('<div style="background:#FFFBEB;border:1px solid #FDE68A;border-radius:10px;'
               'padding:12px 14px;font-size:13px;color:#92400E;">'
               'The check could not be completed — one tap clears it.</div>')
    else:
        lead = (f'has applied for <b>{escape(what)}</b> while carrying work that is past its '
                f'due date. Their manager cannot approve it until you sign.')
        box = ('<div style="background:#FFFBEB;border:1px solid #FDE68A;border-radius:10px;'
               'padding:12px 14px;">'
               '<div style="font-size:13px;color:#92400E;font-weight:700;">Overdue work</div>'
               f'{table}</div>')

    inner = f"""
      <h2 style="margin:0 0 6px;font-size:20px;color:{NAVY};">{escape(signoff.applicant_name)}</h2>
      <p style="color:#6B7280;font-size:14px;margin:0 0 10px;">{lead}</p>
      {box}
      <p style="margin:20px 0 0;"><a href="{escape(url)}" style="display:block;text-align:center;
         background:{NAVY};color:#fff;text-decoration:none;padding:15px;border-radius:10px;
         font-weight:700;font-size:16px;">Sign off or decline</a></p>
      <p style="text-align:center;color:#9CA3AF;font-size:12px;margin:8px 0 0;">
        Opens a secure page — no sign-in needed. Works on your phone.</p>"""

    html = f"""<!DOCTYPE html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"></head>
<body style="margin:0;background:#F3F4F6;font-family:'Segoe UI',Arial,sans-serif;color:#1F2937;">
  <div style="max-width:600px;margin:0 auto;padding:20px 12px;">
    <div style="background:#fff;border-radius:14px;overflow:hidden;box-shadow:0 6px 24px rgba(13,27,42,.08);">
      <div style="background:{NAVY};padding:20px 26px;">
        <div style="color:{ORANGE};font-size:18px;font-weight:700;">Alpha Direct · Executive Sign-off</div>
      </div>
      <div style="padding:22px 26px;">{inner}</div>
    </div>
    <p style="text-align:center;color:#9CA3AF;font-size:11px;margin-top:14px;">
      Omni ERP — omni.alphadirect.co.bw</p>
  </div>
</body></html>"""
    return {'subject': subject, 'html': html, 'to': [to]}
