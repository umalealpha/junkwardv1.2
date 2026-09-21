import calendar
import logging
from datetime import date, timedelta

from django.conf import settings
from django.utils.html import escape

from core.notifications import send_html_with_cfo_cc
from payroll.models import Employee, EmploymentContract
from hris.models import ContractReminderLog, ContractReminderRule, ContractRenewalDecision, HRISProfile

logger = logging.getLogger(__name__)

DEFAULT_MONTHS = {
    'expatriate': 6,
    'controller': 4,
    'c_suite': 4,
    'senior_manager': 4,
    'employee': 2,
}


def _profile_for(employee):
    try:
        return employee.hris_profile
    except AttributeError:
        return None


def category_for(employee):
    profile = _profile_for(employee)
    if profile is None:
        return 'employee'
    if profile.is_expatriate:
        return 'expatriate'
    if profile.is_controller:
        return 'controller'
    grade = profile.grade
    if grade is not None:
        grade_code = grade.code or ''
        if grade_code.startswith('EX'):
            return 'c_suite'
        grade_name = grade.name or ''
        if grade_name and 'manager' in grade_name.lower():
            return 'senior_manager'
    return 'employee'


def months_before(category):
    rule = ContractReminderRule.objects.filter(category=category, is_active=True).order_by('-months_before').first()
    if rule is not None:
        return rule.months_before
    return DEFAULT_MONTHS.get(category, 2)


def add_months(d, n):
    month = d.month - 1 + n
    year = d.year + month // 12
    month = month % 12 + 1
    day = min(d.day, calendar.monthrange(year, month)[1])
    return date(year, month, day)


def reminder_date(contract):
    if contract.end_date is None:
        return None
    return add_months(contract.end_date, -months_before(category_for(contract.employee)))


def latest_decision(contract):
    return ContractRenewalDecision.objects.filter(contract=contract).order_by('-created_at').first()


def _iso_date(value):
    return value.isoformat() if value else None


def _iso_datetime(value):
    return value.isoformat() if value else ''


def contract_row(contract, today):
    employee = contract.employee
    profile = _profile_for(employee)
    grade_code = ''
    if profile is not None and profile.grade is not None:
        grade_code = profile.grade.code or ''

    decision = latest_decision(contract)
    due = False
    if contract.end_date and decision is None:
        rdate = reminder_date(contract)
        if rdate is not None and today >= rdate:
            due = True

    on_probation = bool(contract.probation_end_date and contract.probation_end_date >= today)

    decided_by_name = ''
    if decision is not None and decision.decided_by is not None:
        decided_by_name = decision.decided_by.get_full_name() or decision.decided_by.username

    return {
        'id': str(contract.id),
        'employee_id': employee.id,
        'employee_name': employee.full_name,
        'employee': employee.full_name,  # the page reads these two names
        'company': employee.company.name if employee.company else '',
        'contract_type_label': contract.get_contract_type_display() if contract.contract_type else '',
        'company_name': employee.company.name if employee.company else '',
        'department': employee.department or '',
        'job_title': employee.job_title or '',
        'grade_code': grade_code,
        'category': category_for(employee),
        'contract_type': contract.contract_type or '',
        'start_date': _iso_date(contract.start_date),
        'end_date': _iso_date(contract.end_date),
        'probation_end_date': _iso_date(contract.probation_end_date),
        'retirement_fund': contract.retirement_fund or '',
        'days_to_end': (contract.end_date - today).days if contract.end_date else None,
        'reminder_from': _iso_date(reminder_date(contract)),
        'due_for_renewal': due,
        'on_probation': on_probation,
        'decision': decision.decision if decision else '',
        'decision_note': decision.note if decision else '',
        'decided_by_name': decided_by_name,
        'decided_at': _iso_datetime(decision.created_at) if decision else '',
    }


def active_contracts():
    return EmploymentContract.objects.filter(
        status='active',
        employee__status__in=['active', 'on_leave'],
    ).select_related(
        'employee',
        'employee__company',
        'employee__hris_profile__grade',
    )


def recipients_for(contract):
    from hris.hr_settings import get_setting

    data = get_setting(
        'contract_reminder_recipients',
        ['dikgopoleng@alphadirect.co.bw', 'ubutale@alphadirect.co.bw', 'hc@alphadirect.co.bw'],
    )
    if isinstance(data, str):
        data = [part.strip() for part in data.split(',') if part.strip()]
    to = [email for email in data if email]

    employee = contract.employee
    profile = _profile_for(employee)
    if profile is not None and profile.manager_id:
        manager_email = profile.manager.email.strip() if profile.manager.email else ''
        if manager_email and manager_email not in to:
            to.append(manager_email)

    include_cfo = category_for(employee) in ('controller', 'expatriate', 'c_suite')
    return to, include_cfo


def _category_label(category):
    return category.replace('_', ' ').title()


def _html_email(title, paragraphs, button_url, button_label):
    paragraph_html = ''.join(f'<p style="font-size:14px;line-height:1.5;color:#1a1a1a;">{p}</p>' for p in paragraphs)
    return f"""
<table width="100%" cellpadding="0" cellspacing="0" style="background:#ffffff;font-family:Arial,Helvetica,sans-serif;">
  <tr>
    <td style="background:#0D1B2A;padding:18px 24px;color:#ffffff;font-size:12px;font-variant:small-caps;letter-spacing:1px;">
      ALPHA DIRECT &middot; OMNI
    </td>
  </tr>
  <tr>
    <td style="padding:24px;max-width:640px;">
      <h1 style="color:#F4A623;font-size:20px;margin:0 0 16px;">{escape(title)}</h1>
      {paragraph_html}
      <p style="margin:22px 0;">
        <a href="{escape(button_url)}" style="display:inline-block;background:#F4A623;color:#0D1B2A;text-decoration:none;padding:12px 22px;border-radius:4px;font-weight:bold;">{escape(button_label)}</a>
      </p>
      <p style="font-size:14px;font-weight:bold;color:#0D1B2A;margin:20px 0 6px;">The three choices are:</p>
      <ul style="font-size:14px;color:#1a1a1a;line-height:1.6;margin-top:0;">
        <li>Renew on the same terms</li>
        <li>Renew with changes</li>
        <li>End the contract</li>
      </ul>
    </td>
  </tr>
</table>
"""


def run_reminders(today, commit=False):
    counts = {'sent': 0, 'skipped': 0, 'errors': 0}
    for contract in active_contracts():
        try:
            if not contract.end_date or contract.end_date < today:
                counts['skipped'] += 1
                continue

            if latest_decision(contract) is not None:
                counts['skipped'] += 1
                continue

            rdate = reminder_date(contract)
            if rdate is None or today < rdate:
                counts['skipped'] += 1
                continue

            category = category_for(contract.employee)
            category_label = _category_label(category)
            days_left = (contract.end_date - today).days
            stage = 'escalation' if days_left <= 30 else 'reminder'

            if ContractReminderLog.objects.filter(
                contract=contract,
                stage=stage,
                sent_on__gte=today - timedelta(days=7),
            ).exists():
                counts['skipped'] += 1
                continue

            to, include_cfo = recipients_for(contract)
            escalation = stage == 'escalation'
            if escalation:
                cfo_email = 'pganesharajah@alphadirect.co.bw'
                if cfo_email not in to:
                    to.append(cfo_email)

            employee = contract.employee
            end_date_text = contract.end_date.strftime('%-d %B %Y')
            subject = f"Contract ending {end_date_text} — {employee.full_name} ({category_label})"
            if escalation:
                subject = "ACTION NEEDED: " + subject

            months = months_before(category)
            paragraphs = [
                escape(
                    f"{employee.full_name} is on a {contract.get_contract_type_display().lower()} contract as "
                    f"{employee.job_title or 'staff member'} at {employee.company.name if employee.company else 'Alpha Direct'}."
                ),
                escape(f"The contract ends on {end_date_text} ({days_left} days from today)."),
                escape(f"Group: {category_label}. HR is reminded {months} months before this group's contracts end."),
            ]

            frontend_url = getattr(settings, 'FRONTEND_URL', None) or 'https://omni.alphadirect.co.bw'
            button_url = f"{frontend_url}/hris/contracts?contract={contract.id}"
            button_label = "Record the decision in Omni"
            html = _html_email(subject, paragraphs, button_url, button_label)

            if commit:
                try:
                    send_html_with_cfo_cc(
                        subject=subject,
                        html=html,
                        to=to,
                        cc_cfo=include_cfo or escalation,
                        no_reply=True,
                        # Recipients are deliberate (HR, the line manager, the CFO): a
                        # manager who is an executive must not be dropped silently.
                        allow_named_exec=True,
                    )
                    ContractReminderLog.objects.create(
                        contract=contract,
                        stage=stage,
                        sent_on=today,
                        recipients=to,
                    )
                except Exception:
                    logger.exception('Failed to send contract reminder for contract %s', contract.id)
                    counts['errors'] += 1
                else:
                    counts['sent'] += 1
            else:
                counts['sent'] += 1
        except Exception:
            logger.exception('Failed to process contract reminder for contract %s', contract.id)
            counts['errors'] += 1

    return counts
