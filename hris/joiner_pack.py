import logging
from datetime import timedelta

from django.conf import settings
from django.utils import timezone
from django.utils.html import escape

from assets.control_models import AssetControlPolicy
from assets.control_services import assets_blocking_offboarding
from core.notifications import send_html_with_cfo_cc
from hris.hr_settings import get_setting
from hris.models import (
    EmployeeAcknowledgement,
    HRDocument,
    HRISProfile,
    OnboardingTask,
    RoleSystemRequirement,
)
from payroll.models import Employee

logger = logging.getLogger(__name__)

REQUIRED_DOCS = ['id_document', 'bank_letter', 'police_clearance', 'onboarding']
CONTROLLER_DOCS = ['nbfira_letter', 'controller_docs']

DOC_LABELS = {
    'id_document': 'ID document',
    'bank_letter': 'Bank letter',
    'police_clearance': 'Police clearance',
    'onboarding': 'Onboarding documents',
    'nbfira_letter': 'NBFIRA letter',
    'controller_docs': 'Controller documents',
}


def _profile_for(employee):
    try:
        return employee.hris_profile
    except HRISProfile.DoesNotExist:
        return None


def _base_url():
    return getattr(settings, 'OMNI_BASE_URL', getattr(settings, 'BASE_URL', ''))


def house_email(title, body_html):
    """House style HTML email: navy header, orange title, 640px table."""
    return f"""<!DOCTYPE html>
<html>
<body style="margin:0;padding:0;background:#f2f2f2;">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#f2f2f2;padding:20px 0;">
  <tr>
    <td align="center">
      <table role="presentation" width="640" cellpadding="0" cellspacing="0" style="width:640px;max-width:100%;background:#ffffff;border:1px solid #e5e5e5;">
        <tr>
          <td style="background:#0D1B2A;padding:20px 30px;">
            <div style="color:#ffffff;font-size:11px;letter-spacing:2px;text-transform:uppercase;">ALPHA DIRECT · OMNI</div>
            <div style="color:#F4A623;font-size:22px;font-weight:bold;margin-top:8px;">{escape(title)}</div>
          </td>
        </tr>
        <tr>
          <td style="padding:20px 30px;font-family:Arial,sans-serif;font-size:14px;color:#222;">{body_html}</td>
        </tr>
      </table>
    </td>
  </tr>
</table>
</body>
</html>"""


def systems_for(employee):
    """RoleSystemRequirement '*' plus department-specific systems, de-duplicated."""
    systems = []
    star = RoleSystemRequirement.objects.filter(department='*').first()
    if star:
        systems.extend(star.systems or [])

    department = (employee.department or '').strip()
    if department:
        row = RoleSystemRequirement.objects.filter(department__iexact=department).first()
        if row:
            systems.extend(row.systems or [])

    seen = set()
    result = []
    for system in systems:
        key = str(system).strip().lower()
        if key and key not in seen:
            seen.add(key)
            result.append(system)
    return result


def documents_status(employee):
    """Return the required document categories and whether they exist for the joiner."""
    profile = _profile_for(employee)
    categories = list(REQUIRED_DOCS)
    if profile is not None and profile.is_controller:
        categories.extend(CONTROLLER_DOCS)

    rows = []
    for category in categories:
        present = False
        if profile is not None and profile.pk:
            present = HRDocument.objects.filter(employee=profile, category=category).exists()
        rows.append({
            'category': category,
            'label': DOC_LABELS.get(category, category),
            'present': present,
        })
    return rows


def _start_joiner_pack(employee, actor):
    today = timezone.localdate()
    due = (employee.hire_date or today) + timedelta(days=30)
    profile = _profile_for(employee)
    manager = profile.manager if profile else None
    base = _base_url()

    # 30-day onboarding checklist
    hc_tasks = [
        ('Collect ID, bank letter and police clearance', 'paperwork'),
        ('File signed contract and onboarding documents', 'paperwork'),
    ]
    for title, category in hc_tasks:
        if not OnboardingTask.objects.filter(employee=employee, title=title).exists():
            OnboardingTask.objects.create(
                employee=employee,
                owner_user=None,
                title=title,
                category=category,
                due_date=due,
                status='open',
            )

    manager_owner = manager.user if manager else None
    manager_tasks = [
        ('Welcome meeting and introduce the team', 'buddy'),
        ('Agree 30-day objectives', 'training'),
    ]
    for title, category in manager_tasks:
        if not OnboardingTask.objects.filter(employee=employee, title=title).exists():
            OnboardingTask.objects.create(
                employee=employee,
                owner_user=manager_owner,
                title=title,
                category=category,
                due_date=due,
                status='open',
            )

    employee_owner = employee.user if employee.user_id else None
    employee_tasks = [
        ('Read and sign all company policies', 'training'),
        ('Sign your job description', 'paperwork'),
    ]
    for title, category in employee_tasks:
        if not OnboardingTask.objects.filter(employee=employee, title=title).exists():
            OnboardingTask.objects.create(
                employee=employee,
                owner_user=employee_owner,
                title=title,
                category=category,
                due_date=due,
                status='open',
            )

    # Job description acknowledgement
    jd_title = f'Job description — {employee.job_title}'
    if not EmployeeAcknowledgement.objects.filter(
        employee=employee, kind='job_description', title=jd_title
    ).exists():
        jd_doc = None
        if profile is not None:
            jd_doc = HRDocument.objects.filter(
                employee=profile, category='job_description'
            ).order_by('-pk').first()
        EmployeeAcknowledgement.objects.create(
            employee=employee,
            kind='job_description',
            title=jd_title,
            document=jd_doc,
            due_date=due,
            needs_manager=True,
        )

    # Company policy acknowledgements
    policy_docs = HRDocument.objects.filter(category='policy', is_personal=False)
    for doc in policy_docs:
        if not EmployeeAcknowledgement.objects.filter(
            employee=employee, kind='policy', title=doc.title
        ).exists():
            EmployeeAcknowledgement.objects.create(
                employee=employee,
                kind='policy',
                title=doc.title,
                document=doc,
                due_date=due,
                needs_manager=False,
            )

    start_display = (employee.hire_date or today).strftime('%d %b %Y')
    company_name = employee.company.name if employee.company_id else ''

    # Notify line manager
    if manager and manager.email:
        subject = f'New team member: {employee.full_name} starts {start_display}'
        body_html = (
            f'<p><strong>Name:</strong> {escape(employee.full_name)}</p>'
            f'<p><strong>Role:</strong> {escape(employee.job_title)}</p>'
            f'<p><strong>Department:</strong> {escape(employee.department)}</p>'
            f'<p><strong>Company:</strong> {escape(company_name)}</p>'
            f'<p><strong>Start date:</strong> {escape(start_display)}</p>'
            '<p>Please welcome them, agree 30-day objectives, and sign the job '
            'description with them in Omni.</p>'
            f'<p><a href="{escape(base)}/hris/joiners" style="color:#F4A623;">Open joiners</a></p>'
        )
        send_html_with_cfo_cc(
            subject=subject,
            html=house_email('New team member', body_html),
            to=[manager.email],
            cc_cfo=False,
            allow_named_exec=True,
        )

    # Notify IT
    policy = AssetControlPolicy.objects.filter(is_active=True).first()
    it_emails = list(policy.it_officer_emails or []) if policy else []
    if not it_emails:
        it_emails = [r for r in (get_setting('contract_reminder_recipients', []) or []) if r]

    if it_emails:
        subject = f'Set up systems for {employee.full_name} ({employee.department or ""})'
        systems = systems_for(employee)
        systems_html = ''.join(f'<li>{escape(s)}</li>' for s in systems) if systems else '<li>No specific systems</li>'
        body_html = (
            f'<p><strong>New joiner:</strong> {escape(employee.full_name)} '
            f'({escape(employee.department)})</p>'
            f'<p><strong>Start date:</strong> {escape(start_display)}</p>'
            '<p>Please set up the following systems:</p>'
            f'<ul>{systems_html}</ul>'
            '<p>Issue the laptop/phone through Assets → Requisitions so it is recorded '
            'against them.</p>'
            f'<p><a href="{escape(base)}/assets/requisitions" style="color:#F4A623;">Open requisitions</a></p>'
        )
        send_html_with_cfo_cc(
            subject=subject,
            html=house_email('Set up systems', body_html),
            to=it_emails,
            cc_cfo=False,
        )


def start_joiner_pack(employee, actor):
    """Idempotent new joiner pack creation. Never raises; logs exceptions."""
    try:
        _start_joiner_pack(employee, actor)
    except Exception:
        logger.exception('start_joiner_pack failed for employee %s', employee.pk)


def _months_back(today, months):
    import calendar
    total_months = today.year * 12 + (today.month - 1) - months
    year, month_index = divmod(total_months, 12)
    month = month_index + 1
    day = min(today.day, calendar.monthrange(year, month)[1])
    return today.replace(year=year, month=month, day=day)


def start_monthly_reviews(today=None):
    """Create one monthly review acknowledgement for active employees hired in the
    last 6 months who have a manager. Returns the number created."""
    today = today or timezone.localdate()
    period = today.strftime('%Y-%m')
    start = _months_back(today, 6)

    employees = Employee.objects.filter(
        status='active',
        hire_date__gte=start,
        hire_date__lte=today,
        hris_profile__manager__isnull=False,
    ).distinct()

    created = 0
    for employee in employees.iterator():
        if EmployeeAcknowledgement.objects.filter(
            employee=employee, kind='monthly_review', period=period
        ).exists():
            continue
        EmployeeAcknowledgement.objects.create(
            employee=employee,
            kind='monthly_review',
            title=f'Monthly review {period}',
            period=period,
            due_date=today + timedelta(days=10),
            needs_manager=True,
        )
        created += 1
    return created


def joiner_row(employee):
    profile = _profile_for(employee)
    manager = profile.manager if profile else None
    tasks = OnboardingTask.objects.filter(employee=employee)
    today = timezone.localdate()

    acks = []
    for ack in EmployeeAcknowledgement.objects.filter(employee=employee):
        overdue = bool(
            ack.due_date
            and ack.due_date < today
            and (
                ack.employee_signed_at is None
                or (ack.needs_manager and ack.manager_signed_at is None)
            )
        )
        acks.append({
            'id': ack.pk,
            'kind': ack.kind,
            'title': ack.title,
            'due_date': ack.due_date,
            'employee_signed_at': ack.employee_signed_at,
            'manager_signed_at': ack.manager_signed_at,
            'needs_manager': ack.needs_manager,
            'overdue': overdue,
        })

    try:
        items_held = [
            f'{asset.tag_number} — {getattr(asset, "description", "") or getattr(asset, "name", "")}'
            for asset in assets_blocking_offboarding(employee)
        ]
    except Exception:
        logger.exception('assets_blocking_offboarding failed for employee %s', employee.pk)
        items_held = []

    return {
        'id': employee.pk,
        'name': employee.full_name,
        'job_title': employee.job_title,
        'department': employee.department,
        'company': employee.company.name if employee.company_id else '',
        'hire_date': employee.hire_date,
        'manager_name': manager.full_name if manager else '',
        'tasks': {'done': tasks.filter(status='done').count(), 'total': tasks.count()},
        'acks': acks,
        'documents': documents_status(employee),
        'items_held': items_held,
        'systems': systems_for(employee),
    }
