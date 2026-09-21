import logging
from datetime import timedelta

from django.conf import settings
from django.utils import timezone
from django.utils.html import escape

from core.notifications import send_html_with_cfo_cc

logger = logging.getLogger(__name__)

AGENT_DOMAINS = ('insurance.co.bw',)


def policy():
    value = getattr(settings, 'SSO_NEW_LOGIN_POLICY', 'report')
    return value if value in ('report', 'enforce') else 'report'


def on_payroll(email):
    """Return True if an active payroll Employee exists for this email."""
    email = (email or '').strip().lower()
    if not email:
        return False

    try:
        from payroll.models import Employee

        qs = Employee.objects.filter(is_archived=False, email__iexact=email)
        if qs.filter(status__in=['active', 'on_leave']).exists():
            return True

        # Explicitly allow someone linked to a user and still active, even if
        # the email case differs or the link is the source of truth.
        if qs.filter(user__isnull=False, status__in=['active', 'on_leave']).exists():
            return True
    except Exception:
        logger.exception('on_payroll check failed for %s', email)
        return False

    return False


def decide_new_login(email):
    email = (email or '').strip().lower()
    if not email:
        return ('block', 'missing email')

    if on_payroll(email):
        return ('allow', 'on payroll')

    domain = email.rsplit('@', 1)[1] if '@' in email else ''
    if domain in AGENT_DOMAINS:
        return ('block', 'UniCoin agent (insurance.co.bw) not on payroll')

    if policy() == 'enforce':
        return ('block', 'not on payroll')

    return ('report', 'not on payroll — watch-only until enforcement')


def _build_email(email, verdict, reason):
    e_email = escape(email)
    e_verdict = escape(verdict)
    e_reason = escape(reason)
    e_policy = escape(str(policy()))

    if verdict == 'block':
        action_detail = 'Omni kept this person out because they are not on payroll.'
    else:
        action_detail = 'Omni allowed this person in for now, but they are not on payroll.'

    return f"""<!DOCTYPE html>
<html>
<body style="margin:0;padding:0;background-color:#f4f4f4;font-family:Arial,sans-serif;">
  <table width="640" cellpadding="0" cellspacing="0" border="0" align="center"
         style="background:#ffffff;border:1px solid #dddddd;">
    <tr>
      <td style="background:#0D1B2A;padding:16px 20px;">
        <div style="color:#ffffff;font-size:12px;letter-spacing:1px;font-variant:small-caps;">
          Alpha Direct &middot; Omni
        </div>
      </td>
    </tr>
    <tr>
      <td style="padding:20px;">
        <h2 style="color:#F4A623;margin:0 0 12px;">New Omni sign-in</h2>
        <p><strong>Email:</strong> {e_email}</p>
        <p><strong>Omni action:</strong> {e_verdict}</p>
        <p><strong>Reason:</strong> {e_reason}</p>
        <p>{escape(action_detail)}</p>
        <p>If they are staff, onboard them in Omni (People &rarr; Onboard) so
           they appear on payroll; their next sign-in will then work.</p>
        <p style="color:#666666;">Policy: {e_policy}</p>
      </td>
    </tr>
  </table>
</body>
</html>"""


def record(email, verdict, reason):
    """Audit the login decision and notify HR for report/block verdicts.

    This function intentionally never raises. Email de-duplication is based on
    existing 'SSO new login' audit rows for the same email in the last 24h.
    """
    email = (email or '').strip().lower()
    try:
        from core.models import AuditLog

        since = timezone.now() - timedelta(hours=24)
        recent = AuditLog.objects.filter(
            record_id=email,
            description__startswith='SSO new login',
            created_at__gte=since,
        ).exists()

        AuditLog.objects.create(
            table_name='auth_user',
            record_id=email,
            action='create' if verdict != 'block' else 'read',
            description=f'SSO new login {verdict}: {reason}',
            new_values={'email': email, 'verdict': verdict, 'reason': reason},
        )

        if verdict in ('report', 'block') and not recent:
            from hris.hr_settings import get_setting

            recipients = get_setting(
                'contract_reminder_recipients',
                ['dikgopoleng@alphadirect.co.bw', 'ubutale@alphadirect.co.bw'],
            )
            if not isinstance(recipients, (list, tuple)):
                recipients = [recipients]

            subject = f'New Omni sign-in {"blocked" if verdict == "block" else "to check"}: {email}'
            send_html_with_cfo_cc(
                subject=subject,
                html=_build_email(email, verdict, reason),
                to=list(recipients),
                text_fallback='',
                cc=None,
                attachments=None,
                cc_cfo=True,
                no_reply=True,
                allow_named_exec=False,
            )
    except Exception:
        logger.exception('core.login_gate.record failed for %s (%s)', email, verdict)


class LoginBlocked(Exception):
    pass
