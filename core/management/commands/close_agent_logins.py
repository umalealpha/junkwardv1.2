from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.db.models import Q
from django.utils import timezone
from django.utils.html import escape

from core.login_gate import AGENT_DOMAINS, on_payroll
from core.models import AuditLog, UserProfile
from core.session_teardown import clear_browser_tokens, revoke_device_sessions
from core.notifications import send_html_with_cfo_cc
from hris.hr_settings import get_setting


def _is_staff_login(user):
    """Keep a login that belongs to real staff even when its email is not the
    payroll email (live 19-Sep-2026: an active UniCoin employee signs in with an
    insurance.co.bw login while payroll holds her alphadirect.co.bw address).
    Staff = linked to an active Employee, or an exact full-name match to one."""
    from payroll.models import Employee
    active = Employee.objects.filter(status__in=['active', 'on_leave'], is_archived=False)
    if active.filter(user=user).exists():
        return True
    name = (user.get_full_name() or '').strip()
    return bool(name) and active.filter(full_name__iexact=name).exists()


class Command(BaseCommand):
    help = 'Deactivate Omni logins for UniCoin agents not on payroll (CFO rule 19-Sep-2026).'

    def add_arguments(self, parser):
        parser.add_argument(
            '--commit',
            action='store_true',
            default=False,
            help='Actually deactivate users and send summary email.',
        )

    def handle(self, *args, **options):
        commit = options.get('commit', False)
        User = get_user_model()

        agent_q = Q()
        for domain in AGENT_DOMAINS:
            agent_q |= Q(email__iendswith=domain)

        candidates = User.objects.filter(
            is_active=True,
            is_superuser=False,
        ).filter(agent_q).order_by('email')

        targets = [u for u in candidates if not on_payroll(u.email) and not _is_staff_login(u)]

        if not targets:
            self.stdout.write('No active UniCoin agent logins to close.')
            return

        if not commit:
            self.stdout.write(
                f'DRY-RUN: {len(targets)} active UniCoin agent login(s) to close:'
            )
            for user in targets:
                self.stdout.write(f'  {user.username} <{user.email}>')
            return

        closed = []
        for user in targets:
            # Same close as a leaver (payroll/offboard_access.py): phone sessions and
            # browser tokens go, and the profile goes too — UserProfile.save() copies
            # is_active back onto the login, so closing only the login can be undone.
            revoke_device_sessions(user, timezone.now())
            clear_browser_tokens(user)
            user.is_active = False
            user.save(update_fields=['is_active'])
            profile = UserProfile.objects.filter(user=user).first()
            if profile is not None and profile.is_active:
                profile.is_active = False
                profile.save(update_fields=['is_active'])

            AuditLog.objects.create(
                table_name='auth_user',
                record_id=str(user.pk),
                action='update',
                old_values={'is_active': True},
                new_values={'is_active': False},
                description='Closed: UniCoin agent not on payroll (CFO rule 19-Sep-2026)',
            )

            name = user.get_full_name() or user.username
            closed.append((name, user.email))

        self.stdout.write(f'Closed {len(closed)} UniCoin agent login(s).')
        self._send_summary(closed)

    def _send_summary(self, closed):
        if not closed:
            return

        recipients = get_setting(
            'contract_reminder_recipients',
            ['dikgopoleng@alphadirect.co.bw', 'ubutale@alphadirect.co.bw'],
        )
        if not isinstance(recipients, (list, tuple)):
            recipients = [recipients]

        subject = f'Closed {len(closed)} UniCoin agent Omni login(s) not on payroll'
        html = self._build_summary_html(closed)

        try:
            send_html_with_cfo_cc(
                subject=subject,
                html=html,
                to=list(recipients),
                text_fallback='',
                cc=None,
                attachments=None,
                cc_cfo=True,
                no_reply=True,
                allow_named_exec=False,
            )
        except Exception:
            self.stderr.write('Failed to send summary email.')

    def _build_summary_html(self, closed):
        rows = ''.join(
            '<tr>'
            f'<td style="padding:6px;border:1px solid #dddddd;">{escape(name)}</td>'
            f'<td style="padding:6px;border:1px solid #dddddd;">{escape(email)}</td>'
            '</tr>'
            for name, email in closed
        )

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
        <h2 style="color:#F4A623;margin:0 0 12px;">Closed UniCoin agent Omni logins</h2>
        <p>The following UniCoin agent Omni logins were closed because they are
           not on payroll.</p>
        <table width="100%" cellpadding="0" cellspacing="0" border="0"
               style="border-collapse:collapse;">
          <tr>
            <th align="left" style="padding:6px;border:1px solid #dddddd;background:#f0f0f0;">Name</th>
            <th align="left" style="padding:6px;border:1px solid #dddddd;background:#f0f0f0;">Email</th>
          </tr>
          {rows}
        </table>
        <p>If any of these is actually on our staff, reply-log it in Omni and
           HR can reopen it.</p>
      </td>
    </tr>
  </table>
</body>
</html>"""
