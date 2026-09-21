"""Daily: switch off Microsoft 365 for leavers past their last working day.

Dry-run by default. With --commit and M365_LEAVER_DISABLE=enforce it acts; in
'report' mode it only emails HR + IT what it would do. See hris/m365_offboarding.py.
"""
from django.core.management.base import BaseCommand
from django.utils.html import escape

from core.notifications import send_html_with_cfo_cc
from hris.hr_settings import get_setting
from hris.joiner_pack import house_email
from hris.m365_offboarding import policy, run


def _recipients():
    from assets.control_models import AssetControlPolicy
    it = AssetControlPolicy.objects.filter(is_active=True).first()
    people = list(it.it_officer_emails or []) if it else []
    people += [r for r in (get_setting('contract_reminder_recipients', []) or []) if r]
    return list(dict.fromkeys(people))


class Command(BaseCommand):
    help = "Switch off leavers' Microsoft 365 accounts the day after their last working day."

    def add_arguments(self, parser):
        parser.add_argument('--commit', action='store_true', help='Act and email. Default is dry-run.')

    def handle(self, *args, **options):
        commit = options['commit']
        mode = policy()
        if mode == 'off':
            self.stdout.write('policy off — nothing to do')
            return
        rows = run(commit=commit)
        for case, action, message in rows:
            self.stdout.write(f'{case.employee.full_name}: {action} — {message}')
        # Email only when something happened; skipped rows repeat daily, so they ride along only then.
        acted = [r for r in rows if r[1] not in ('skipped', 'already reported')]
        if not (commit and acted):
            return
        to = _recipients()
        if not to:
            self.stdout.write('no HR/IT recipients configured — email not sent')
            return
        items = ''.join(
            f'<tr><td style="padding:6px 0;border-bottom:1px solid #e5e7eb;">'
            f'<b>{escape(c.employee.full_name)}</b> (last day {c.last_working_day:%d %b %Y}) — '
            f'{escape(action)}: {escape(msg)}</td></tr>' for c, action, msg in rows)
        title = 'Leaver Microsoft accounts switched off' if mode == 'enforce' else 'Leaver Microsoft accounts due to be switched off'
        body = (f'<table role="presentation" width="100%" style="font-size:14px;">{items}</table>'
                '<p>This follows the Leavers page in Omni: Microsoft 365 goes off the day after the '
                'last working day. To keep someone on, tick "keep access after exit" on their record.</p>')
        send_html_with_cfo_cc(subject=title, html=house_email(title, body), to=to, cc_cfo=False)
