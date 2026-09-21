import logging

from django.utils.html import escape

from core.models import AuditLog
from core.notifications import send_html_with_cfo_cc

from .models import HRSetting

logger = logging.getLogger(__name__)

CFO_EMAIL = 'pganesharajah@alphadirect.co.bw'

HR_TEAM_ROLES = {
    'HR_MANAGER': 'Full HR (incl. payroll)',
    'HR_VIEWER': 'View only (no pay)',
}


def get_setting(key, default):
    """Return HRSetting.value for key, or default. Never raises."""
    try:
        row = HRSetting.objects.get(key=key)
        return row.value if row.value is not None else default
    except Exception:
        return default


def is_hr_head(user):
    """Return True only for HR heads who may change settings."""
    if not user or not getattr(user, 'is_authenticated', False):
        return False

    if user.is_superuser:
        return True

    email = (getattr(user, 'email', '') or '').strip().lower()
    if email == CFO_EMAIL:
        return True

    heads = [str(e).strip().lower() for e in get_setting('hr_heads', [])]
    return bool(email) and user.is_active and email in heads


def _house_html(body_text):
    body = escape(body_text)
    return ('''<!DOCTYPE html>
<html>
<head><meta charset="utf-8"><title>HR settings changed</title></head>
<body style="margin:0;padding:0;background-color:#F5F6F8;">
  <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background-color:#F5F6F8;padding:24px 0;">
    <tr>
      <td align="center">
        <table role="presentation" width="640" cellpadding="0" cellspacing="0" style="max-width:640px;width:100%;background-color:#ffffff;border:1px solid #E2E5EA;border-radius:8px;overflow:hidden;">
          <tr>
            <td style="background-color:#0D1B2A;padding:24px;text-align:center;">
              <div style="color:#ffffff;font-family:Arial, sans-serif;font-size:12px;letter-spacing:2px;text-transform:uppercase;">Alpha Direct · Omni</div>
              <div style="color:#F4A623;font-family:Arial, sans-serif;font-size:22px;font-weight:bold;margin-top:8px;">HR settings changed</div>
            </td>
          </tr>
          <tr>
            <td style="padding:24px;font-family:Arial, sans-serif;font-size:14px;color:#1E2733;line-height:1.5;">
              <p style="margin:0 0 16px;">Dear CFO,</p>
              <p style="margin:0 0 16px;">The following HR dashboard setting change was made:</p>
              <p style="margin:0 0 16px;background:#FFF7E6;border-left:4px solid #F4A623;padding:12px;color:#5A4A22;">''' + body + '''</p>
              <p style="margin:0;">This is an automated notification from Omni.</p>
            </td>
          </tr>
        </table>
      </td>
    </tr>
  </table>
</body>
</html>''')


def notify_cfo(user, what):
    """Notify the CFO of an HR settings change. Never block the change."""
    name = user.get_full_name() or user.get_username()
    subject = f"HR settings changed by {name}"
    html = _house_html(what)
    try:
        send_html_with_cfo_cc(
            subject,
            html,
            to=[CFO_EMAIL],
            cc_cfo=False,
            allow_named_exec=True,
            no_reply=True,
        )
    except Exception:
        logger.exception("HR settings CFO notification failed")


def audit(user, key, old, new, description):
    return AuditLog.objects.create(
        table_name='hris_hrsetting',
        record_id=str(key),
        action='update',
        old_values=old,
        new_values=new,
        user=user,
        description=description,
    )
