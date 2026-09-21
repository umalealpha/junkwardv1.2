"""chase_manager_reviews — daily chase for Time Doctor follow-ups a manager has
left undecided. It emails each manager once, grouping the workday explanations
that are still EXPLAINED and the unpaid Time Doctor deductions that are still
PENDING on their desk.

Why it exists: the CFO 2026-09-20 control check looked at the undecided work.
One group of items had 137 staff explanations sitting in "explained" with no
manager ruling, and 9 unpaid deductions sat pending — six of them with an
approver whose login had last been used on 11-Aug. Nothing chased anyone.

The command never decides: it only reminds. Where the reminder goes is HR
(and, after five working days, the manager's own manager) exactly as stated in
the notebook.
"""
from __future__ import annotations

import datetime
import logging

from django.conf import settings
from django.core.management.base import BaseCommand
from django.utils import timezone
from django.utils.html import escape

from hris.models import HRISProfile, LeaveRequest, PublicHoliday, WorkdayJustification as WJ
from payroll.models import Employee

log = logging.getLogger(__name__)


def _local_date(value):
    """Return a date for a datetime/date field, always in the local daytime."""
    if isinstance(value, datetime.datetime):
        return timezone.localtime(value).date()
    if isinstance(value, datetime.date):
        return value
    return None


def _bw_holidays():
    """Active Botswana public holidays — loaded ONCE per run, not once per row."""
    return set(PublicHoliday.objects.filter(country_code='BW', is_active=True)
               .values_list('holiday_date', flat=True))


def _working_days_between(start_date, end_date, holiday_dates=frozenset()):
    """Working days strictly after ``start_date`` up to and including ``end_date``.

    Working days are Monday-Friday and exclude active Botswana public holidays.
    NOTE: this is the manager-RESPONSE clock, so Saturday does not count here —
    unlike the hours rule, where Saturday IS a working day (3 h required).
    """
    if not start_date or not end_date or end_date <= start_date:
        return 0
    count = 0
    day = start_date + datetime.timedelta(days=1)
    while day <= end_date:
        if day.weekday() < 5 and day not in holiday_dates:
            count += 1
        day += datetime.timedelta(days=1)
    return count


def approver_for(profile):
    """The human who should decide a workday justification.

    The chosen leave approver first, otherwise the line manager's login.
    None means nobody reachable for a decision.
    """
    user = getattr(profile, 'default_leave_approver', None)
    if user is not None and getattr(user, 'is_active', False):
        return user

    manager_employee = getattr(profile, 'manager', None)
    manager_user = getattr(manager_employee, 'user', None) if manager_employee else None
    if manager_user is not None and getattr(manager_user, 'is_active', False):
        return manager_user
    return None


def _escalation_email(manager_user):
    """Return the email of the manager's own manager, if we can find one."""
    employee = Employee.objects.filter(user=manager_user).first()
    if not employee:
        return None

    profile = HRISProfile.objects.filter(employee=employee).select_related('manager__user').first()
    if not profile:
        return None

    parent_employee = profile.manager
    if not parent_employee:
        return None

    email = getattr(parent_employee, 'email', None)
    if not email:
        email = getattr(getattr(parent_employee, 'user', None), 'email', None)
    return email


def _add_cc(cc, address, manager_email):
    """Add a copy recipient, never the manager and never a duplicate."""
    if not address:
        return
    if address.lower() == manager_email.lower():
        return
    if not any(existing.lower() == address.lower() for existing in cc):
        cc.append(address)


def _format_date(value):
    if not value:
        return ''
    return value.strftime('%d %b')


class Command(BaseCommand):
    help = 'Chase managers about Time Doctor follow-ups waiting on their decision.'

    def add_arguments(self, parser):
        parser.add_argument('--today', default=None)
        parser.add_argument('--send', action='store_true', default=False)
        parser.add_argument('--min-days', type=int, default=3)
        parser.add_argument('--escalate-days', type=int, default=5)

    def handle(self, *args, **options):
        from core.notifications import no_reply_banner, send_html_with_cfo_cc

        today = datetime.date.fromisoformat(options['today']) if options.get('today') else timezone.localdate()
        min_days = options['min_days']
        escalate_days = options['escalate_days']
        send = options['send']

        hr_cc = list(getattr(settings, 'TD_CHASE_HR_CC', ['ubutale@alphadirect.co.bw']))
        holidays = _bw_holidays()

        buckets = {}

        # Floor at the day leave enforcement began (1-Sep-2026). The first prod dry-run
        # (20-Sep) reached back to July: 16 managers, 320 items, one with 60 — a list
        # that long is ignored on day one, which defeats the chase. Older rows are
        # already in monthly feedback; nothing is decided here either way.
        from hris.leave_accountability import STRICT_ENFORCEMENT_START
        explanations = WJ.objects.filter(
            status=WJ.Status.EXPLAINED, work_date__gte=STRICT_ENFORCEMENT_START,
        ).select_related(
            'profile__employee',
            'profile__manager__user',
            'profile__default_leave_approver',
        )
        for item in explanations:
            started = _local_date(item.responded_at) or _local_date(item.updated_at)
            if not started:
                continue
            age = _working_days_between(started, today, holidays)
            if age < min_days:
                continue

            manager = approver_for(item.profile)
            if not manager or not getattr(manager, 'email', None):
                continue

            email = manager.email
            key = email.lower()
            bucket = buckets.setdefault(key, {
                'email': email,
                'user': manager,
                'explanations': [],
                'deductions': [],
                'escalated': False,
            })
            bucket['explanations'].append({
                'person': item.profile.employee.full_name,
                'work_date': item.work_date,
                'age': age,
                'text': item.justification or '',
            })
            if age >= escalate_days:
                bucket['escalated'] = True

        deductions = LeaveRequest.objects.filter(
            status=LeaveRequest.Status.PENDING,
            leave_type__code='td_deduct',
            start_date__gte=STRICT_ENFORCEMENT_START,
        ).select_related('profile__employee', 'requested_approver')
        for item in deductions:
            started = _local_date(item.created_at)
            if not started:
                continue
            age = _working_days_between(started, today, holidays)
            if age < min_days:
                continue

            manager = item.requested_approver
            if not manager or not getattr(manager, 'is_active', False) or not getattr(manager, 'email', None):
                continue

            email = manager.email
            key = email.lower()
            bucket = buckets.setdefault(key, {
                'email': email,
                'user': manager,
                'explanations': [],
                'deductions': [],
                'escalated': False,
            })
            bucket['deductions'].append({
                'person': item.profile.employee.full_name,
                'start_date': item.start_date,
                'end_date': item.end_date,
                'age': age,
            })
            if age >= escalate_days:
                bucket['escalated'] = True

        sent = 0
        for key, bucket in buckets.items():
            manager = bucket['user']
            email = bucket['email']

            escalation_email = None
            if bucket['escalated']:
                escalation_email = _escalation_email(manager)

            cc = []
            for address in hr_cc:
                _add_cc(cc, address, email)
            if escalation_email:
                _add_cc(cc, escalation_email, email)

            self.stdout.write(
                f"  {email}: {len(bucket['explanations'])} explanation(s), "
                f"{len(bucket['deductions'])} deduction(s), "
                f"escalated={'yes' if bucket['escalated'] else 'no'}"
            )

            if not send:
                continue

            subject = 'Waiting on you: Time Doctor follow-ups'
            html = self._build_html(manager, bucket, no_reply_banner, bool(escalation_email))
            try:
                send_html_with_cfo_cc(
                    subject,
                    html,
                    [email],
                    cc=cc,
                    cc_cfo=False,
                    no_reply=True,
                )
                sent += 1
            except Exception:
                log.exception("Failed to send Time Doctor chase email to %s", email)

        if send:
            self.stdout.write(f"[SEND] {len(buckets)} manager(s) chased, sent={sent}")
        else:
            self.stdout.write(f"[DRY-RUN] {len(buckets)} manager(s) would be chased")

    def _build_html(self, manager, bucket, no_reply_banner, escalated_copied):
        first_name = manager.first_name or manager.email

        explanation_rows = []
        for item in bucket['explanations']:
            text = item['text'] or ''
            if len(text) > 120:
                text = text[:120] + '…'
            explanation_rows.append(
                f"<li><strong>{escape(item['person'])}</strong> — "
                f"{_format_date(item['work_date'])}, "
                f"waiting {item['age']} working day(s) — {escape(text)}</li>"
            )

        deduction_rows = []
        for item in bucket['deductions']:
            if item['start_date'] == item['end_date']:
                dates = _format_date(item['start_date'])
            else:
                dates = f"{_format_date(item['start_date'])}..{_format_date(item['end_date'])}"
            deduction_rows.append(
                f"<li><strong>{escape(item['person'])}</strong> — {dates}, "
                f"waiting {item['age']} working day(s)</li>"
            )

        hr_sentence = 'HR is copied on this email.'
        escalation_sentence = ''
        if escalated_copied:
            escalation_sentence = (
                ' Your own manager is also copied because this has been waiting '
                'for at least 5 working days.'
            )

        return f"""
<div style="font-family: 'Book Antiqua', Georgia, serif; color:#1b1b1b; max-width:760px; margin:0 auto;">
  {no_reply_banner()}
  <div style="background:#0D1B2A; padding:18px 24px; border-radius:8px 8px 0 0;">
    <h1 style="color:#F4A623; margin:0; font-size:24px;">Waiting on you: Time Doctor follow-ups</h1>
  </div>
  <div style="border:1px solid #e3e0d8; border-top:none; border-radius:0 0 8px 8px; padding:24px;">
    <p>Hi {escape(first_name)},</p>
    <p>These Time Doctor follow-ups need your decision:</p>

    <h2 style="color:#0D1B2A; font-size:18px;">Explanations waiting for your decision</h2>
    <ul>{''.join(explanation_rows) or '<li>None</li>'}</ul>

    <h2 style="color:#0D1B2A; font-size:18px;">Unpaid Time Doctor deductions waiting for your decision</h2>
    <ul>{''.join(deduction_rows) or '<li>None</li>'}</ul>

    <p>Review explanations at <a href="https://omni.alphadirect.co.bw/app/team">https://omni.alphadirect.co.bw/app/team</a>.</p>
    <p>Review unpaid deductions at <a href="https://omni.alphadirect.co.bw/app/approve">https://omni.alphadirect.co.bw/app/approve</a>.</p>
    <p>{hr_sentence}{escalation_sentence}</p>
  </div>
</div>
"""
