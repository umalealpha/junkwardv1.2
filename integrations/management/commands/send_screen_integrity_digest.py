"""send_screen_integrity_digest — weekly frozen-screen hand-off to HR.

Why it exists: the detector flagged Snehal 3× suspicious + 12× watch and
Natasha 3× in 30 days, and nobody told; found in the CFO 20-Sep-2026 control
check. The CFO chose a weekly note to HR.
"""
from __future__ import annotations

import datetime
import logging
from decimal import Decimal

from django.conf import settings
from django.core.management.base import BaseCommand
from django.utils import timezone
from django.utils.html import escape

from hris.models import HRISProfile
from integrations.models import ScreenIntegrityFlag, TimeDoctorUserMap

log = logging.getLogger(__name__)


def _manager_email_for_td_user(td_user_id):
    """Return the email of the line manager for a Time Doctor account, if any."""
    if not td_user_id:
        return None

    mapping = (
        TimeDoctorUserMap.objects
        .filter(td_user_id=td_user_id, employee__isnull=False, confirmed=True)
        .select_related('employee')
        .first()
    )
    if not mapping or not mapping.employee:
        return None

    profile = (
        HRISProfile.objects
        .filter(employee=mapping.employee)
        .select_related('manager__user')
        .first()
    )
    if not profile or not profile.manager:
        return None

    manager = profile.manager
    email = getattr(manager, 'email', None)
    if not email:
        email = getattr(getattr(manager, 'user', None), 'email', None)
    return email


def _add_cc(cc, address, hr_emails):
    """Add a copy recipient, never an HR address and never a duplicate."""
    if not address:
        return
    address_lower = address.lower()
    if address_lower in hr_emails:
        return
    if not any(existing.lower() == address_lower for existing in cc):
        cc.append(address)


def _format_hours(value):
    """Format Decimal hours for display: 3.50 becomes 3.5, 1.00 becomes 1.0."""
    return f"{value:.1f}"


def _person_word(count):
    """Return the singular/plural word for the summary line."""
    return 'person' if count == 1 else 'people'


class Command(BaseCommand):
    help = 'Send HR the weekly frozen-screen flag digest.'

    def add_arguments(self, parser):
        parser.add_argument('--today', default=None)
        parser.add_argument('--send', action='store_true', default=False)
        parser.add_argument('--days', type=int, default=7)

    def handle(self, *args, **options):
        from core.notifications import no_reply_banner, send_html_with_cfo_cc

        today = datetime.date.fromisoformat(options['today']) if options.get('today') else timezone.localdate()
        days = options['days']
        send = options['send']

        window_start = today - datetime.timedelta(days=days)
        # Mon-Sun of the week just ended: the detector scans YESTERDAY at 04:20 UTC, so a
        # Monday run must include last Monday (today-7) and must exclude today (not scanned
        # yet). Fable review 20-Sep-2026: day__gt/day__lte silently dropped every Monday.
        flags = list(ScreenIntegrityFlag.objects.filter(day__gte=window_start, day__lt=today).order_by('day'))

        if not flags:
            self.stdout.write(f"Frozen-screen digest: nothing to report for the last {days} days.")
            return

        groups = {}
        for flag in flags:
            key = flag.td_user_id or flag.name or 'unknown'
            group = groups.setdefault(key, {
                'td_user_id': flag.td_user_id or '',
                'name': flag.name or flag.td_user_id or key,
                'days': 0,
                'suspicious': 0,
                'watch': 0,
                'hours': Decimal('0'),
            })
            if flag.name:
                group['name'] = flag.name
            group['days'] += 1
            if flag.suspicion == 'suspicious':
                group['suspicious'] += 1
            else:
                group['watch'] += 1
            group['hours'] += flag.frozen_typing_hours or Decimal('0')

        groups_list = list(groups.values())
        for group in groups_list:
            group['worst'] = 'suspicious' if group['suspicious'] else 'watch'

        groups_list.sort(key=lambda g: (g['worst'] != 'suspicious', -g['hours']))

        for group in groups_list:
            self.stdout.write(
                f"  {group['name']}: {group['days']} day(s), "
                f"suspicious {group['suspicious']}, watch {group['watch']}, "
                f"{_format_hours(group['hours'])} h"
            )

        if not send:
            self.stdout.write(
                f"[DRY-RUN] {len(groups_list)} {_person_word(len(groups_list))} would be reported to HR"
            )
            return

        hr_cc = list(getattr(settings, 'TD_CHASE_HR_CC', ['ubutale@alphadirect.co.bw']))
        hr_emails = {addr.lower() for addr in hr_cc if addr}
        cc = []
        for group in groups_list:
            address = _manager_email_for_td_user(group['td_user_id']) if group['td_user_id'] else None
            _add_cc(cc, address, hr_emails)

        subject = 'Frozen-screen check — last week'
        html = self._build_html(groups_list, no_reply_banner)

        sent = 0
        try:
            send_html_with_cfo_cc(
                subject,
                html,
                hr_cc,
                cc=cc,
                cc_cfo=False,
                no_reply=True,
            )
            sent = 1
        except Exception:
            log.exception("Failed to send frozen-screen digest to HR")

        self.stdout.write(
            f"[SEND] {len(groups_list)} {_person_word(len(groups_list))} reported to HR, sent={sent}"
        )

    def _build_html(self, groups, no_reply_banner):
        rows = []
        for group in groups:
            name = escape(group['name'])
            days_cell = '1 day' if group['days'] == 1 else f"{group['days']} days"
            hours_text = _format_hours(group['hours'])
            rows.append(
                f"<tr>"
                f"<td style='padding:8px; border-bottom:1px solid #e3e0d8;'><b>{name}</b></td>"
                f"<td style='padding:8px; border-bottom:1px solid #e3e0d8;'>{days_cell}</td>"
                f"<td style='padding:8px; border-bottom:1px solid #e3e0d8;'>{group['suspicious']}</td>"
                f"<td style='padding:8px; border-bottom:1px solid #e3e0d8;'>{group['watch']}</td>"
                f"<td style='padding:8px; border-bottom:1px solid #e3e0d8;'>{hours_text} h</td></tr>"
            )

        return f"""
<div style="font-family: 'Book Antiqua', Georgia, serif; color:#1b1b1b; max-width:760px; margin:0 auto;">
  {no_reply_banner()}
  <div style="background:#0D1B2A; padding:18px 24px; border-radius:8px 8px 0 0;">
    <h1 style="color:#F4A623; margin:0; font-size:24px;">Frozen-screen check — last week</h1>
  </div>
  <div style="border:1px solid #e3e0d8; border-top:none; border-radius:0 0 8px 8px; padding:24px;">
    <p>Hi Unami,</p>
    <p>These people were flagged by the frozen-screen check — heavy typing on a screen that never changed, with no mouse. This is a flag to look into with the person and their manager, not a verdict; nothing has been deducted.</p>
    <table style="border-collapse:collapse; width:100%;">
      <thead>
        <tr>
          <th align="left" style="padding:8px; background:#0D1B2A; color:#ffffff;">Person</th>
          <th align="left" style="padding:8px; background:#0D1B2A; color:#ffffff;">Days flagged</th>
          <th align="left" style="padding:8px; background:#0D1B2A; color:#ffffff;">Suspicious</th>
          <th align="left" style="padding:8px; background:#0D1B2A; color:#ffffff;">Watch</th>
          <th align="left" style="padding:8px; background:#0D1B2A; color:#ffffff;">Hours credited on a frozen screen</th>
        </tr>
      </thead>
      <tbody>
        {''.join(rows)}
      </tbody>
    </table>
    <p>The line managers of the people above are copied.</p>
    <p><a href="https://omni.alphadirect.co.bw/hris/screen-integrity">Open the Screen Integrity page</a></p>
  </div>
</div>
"""
