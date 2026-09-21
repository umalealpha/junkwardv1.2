from datetime import date, timedelta
from django.conf import settings
import logging

from django.db import transaction
from django.utils import timezone
from django.utils.html import escape

from assets.control_models import AssetControlPolicy
from assets.control_services import assets_blocking_offboarding
from core.hris_access import user_can_access_hris
from core.models import AuditLog, UserProfile, UserRoleAssignment
from core.notifications import send_html_with_cfo_cc
from core.session_teardown import clear_browser_tokens, revoke_device_sessions
from hris.hr_settings import get_setting
from hris.models import HRDocument, OffboardingCase, OffboardingStep


CFO_EMAIL = 'pganesharajah@alphadirect.co.bw'
REQUIRED_STEPS = [kind for kind, _label in OffboardingStep.Kind.choices]

UPLOAD_KINDS = {
    OffboardingStep.Kind.RESIGNATION_LETTER: 'resignation',
    OffboardingStep.Kind.IT_DOCUMENT: 'offboarding_it',
    OffboardingStep.Kind.HC_DOCUMENT: 'offboarding_hc',
}


logger = logging.getLogger(__name__)


def seniority_for(employee):
    """Return the offboarding seniority bucket for an employee."""
    profile = getattr(employee, 'hris_profile', None)
    if profile is None or profile.grade is None:
        return OffboardingCase.Seniority.EMPLOYEE

    grade_code = profile.grade.code or ''
    grade_name = profile.grade.name or ''

    if grade_code.startswith('EX'):
        return OffboardingCase.Seniority.C_SUITE
    if 'manager' in grade_name.lower():
        return OffboardingCase.Seniority.SENIOR_MANAGER
    if 'senior' in grade_name.lower():
        return OffboardingCase.Seniority.SENIOR_ASSOCIATE
    return OffboardingCase.Seniority.EMPLOYEE


def close_access_now(case):
    """Revoke Omni access for the leaver unless they are a protected login."""
    employee = case.employee
    if employee.user is None:
        return

    user = employee.user
    if user.is_superuser or user.email.lower() == CFO_EMAIL:
        return

    now = timezone.now()
    revoke_device_sessions(user, now)
    clear_browser_tokens(user)

    user.is_active = False
    user.save(update_fields=['is_active'])

    profile, _ = UserProfile.objects.get_or_create(user=user)
    profile.is_active = False
    profile.save(update_fields=['is_active'])

    case.access_removed_at = now
    case.save(update_fields=['access_removed_at'])

    AuditLog.objects.create(
        table_name='hris_offboardingcase',
        record_id=str(case.id),
        action='update',
        user=case.opened_by,
        description=f'Omni access removed for {employee.full_name} during offboarding.',
    )


def _safe_it_ticket(case_pk):
    try:
        send_it_ticket(OffboardingCase.objects.get(pk=case_pk))
    except Exception:  # noqa: BLE001 — never let an email failure surface as a 500
        logger.exception('Offboarding IT ticket failed for case %s', case_pk)


def send_it_ticket(case):
    """Send the IT offboarding ticket using the active AssetControlPolicy or HR recipients."""
    employee = case.employee
    policy = AssetControlPolicy.objects.filter(is_active=True).first()
    if policy and policy.it_officer_emails:
        to = policy.it_officer_emails
    else:
        to = get_setting('contract_reminder_recipients', [])

    if not to:
        return

    base = getattr(settings, 'PUBLIC_BASE_URL', 'https://omni.alphadirect.co.bw')
    link = escape(f"{base}/hris/offboarding?case={case.id}")

    seniority_label = OffboardingCase.Seniority(case.seniority).label
    what_to_do = (
        "Remove access to Graphite and every other system NOW. Keep the email mailbox."
        if case.seniority != OffboardingCase.Seniority.EMPLOYEE
        else f"Remove system access at close of business on {escape(str(case.last_working_day))}. "
             "Keep the email mailbox unless HR says otherwise."
    )

    items = assets_blocking_offboarding(employee)
    if items:
        item_rows = ''.join(
            f'<tr><td>{escape(getattr(item, "tag_number", ""))}</td>'
            f'<td>{escape(getattr(item, "description", "") or getattr(item, "name", ""))}</td></tr>'
            for item in items
        )
        items_html = (
            '<p>Please collect the following before the last day:</p>'
            '<table cellpadding="4" cellspacing="0" border="0">'
            '<tr><th align="left">Tag</th><th align="left">Item</th></tr>'
            f'{item_rows}</table>'
        )
    else:
        items_html = '<p>No assets are listed as held by this leaver.</p>'

    html = f"""
    <!doctype html>
    <html>
      <body style="margin:0;padding:0;background:#f5f5f5;font-family:Arial,Helvetica,sans-serif;">
        <table width="640" cellpadding="0" cellspacing="0" border="0" align="center" style="background:#ffffff;border:1px solid #dddddd;">
          <tr>
            <td style="background:#0D1B2A;padding:18px 24px;">
              <span style="color:#ffffff;font-size:12px;letter-spacing:2px;text-transform:uppercase;">Alpha Direct · Omni</span><br>
              <span style="color:#F4A623;font-size:20px;font-weight:bold;">Offboarding IT Ticket</span>
            </td>
          </tr>
          <tr>
            <td style="padding:24px;">
              <p>Please action the offboarding for:</p>
              <ul>
                <li><strong>Leaver:</strong> {escape(employee.full_name)}</li>
                <li><strong>Job title:</strong> {escape(employee.job_title or '')}</li>
                <li><strong>Company:</strong> {escape(employee.company.name if employee.company else '')}</li>
                <li><strong>Department:</strong> {escape(employee.department or '')}</li>
                <li><strong>Last working day:</strong> {escape(str(case.last_working_day))}</li>
                <li><strong>Seniority:</strong> {escape(seniority_label)}</li>
              </ul>
              <p><strong>What to do:</strong> {what_to_do}</p>
              {items_html}
              <p style="margin-top:24px;">
                <a href="{link}" style="color:#0D1B2A;font-weight:bold;">Open in Omni</a>
              </p>
            </td>
          </tr>
        </table>
      </body>
    </html>
    """

    subject = f'Offboarding IT ticket: {employee.full_name}'
    send_html_with_cfo_cc(subject=subject, html=html, to=to, cc_cfo=False, no_reply=True)

    case.it_ticket_sent_at = timezone.now()
    case.save(update_fields=['it_ticket_sent_at'])


def _user_has_finance_role(user):
    """Finance sign-off: a FINANCE_MANAGER / CFO role, OR the Finance Manager /
    Financial Controller title — the same people payroll treats as Finance
    (payroll/amendment_views.py). Fable gate 19-Sep-2026."""
    if UserRoleAssignment.objects.filter(
        user=user, role__code__in=('FINANCE_MANAGER', 'CFO'),
    ).exists():
        return True
    from core.models import UserProfile
    profile = UserProfile.objects.filter(user=user, is_active=True).first()
    return bool(profile and profile.title in (
        UserProfile.Title.FINANCIAL_CONTROLLER, UserProfile.Title.FINANCE_MANAGER))


def can_do_step(user, case, kind):
    if user.is_superuser:
        return True

    kind = str(kind)

    if kind in (OffboardingStep.Kind.RESIGNATION_LETTER,
                OffboardingStep.Kind.HC_DOCUMENT,
                OffboardingStep.Kind.SIGNOFF_HC):
        return user_can_access_hris(user)

    if kind == OffboardingStep.Kind.IT_DOCUMENT:
        policy = AssetControlPolicy.objects.filter(is_active=True).first()
        if policy and policy.it_officer_emails:
            if user.email.lower() in [email.lower() for email in policy.it_officer_emails]:
                return True
        return user_can_access_hris(user)

    if kind == OffboardingStep.Kind.SIGNOFF_MANAGER:
        profile = getattr(case.employee, 'hris_profile', None)
        manager = getattr(profile, 'manager', None)
        if manager and manager.user_id == user.id:
            return True
        return user_can_access_hris(user)

    if kind == OffboardingStep.Kind.SIGNOFF_FINANCE:
        if _user_has_finance_role(user):
            return True
        if user.email.lower() == CFO_EMAIL:
            return True
        return False

    return False


def open_case(employee, *, reason, last_working_day, user):
    with transaction.atomic():
        if OffboardingCase.objects.filter(
            employee=employee,
            status=OffboardingCase.Status.OPEN,
        ).exists():
            raise ValueError('An open offboarding case already exists for this employee.')

        seniority = seniority_for(employee)

        case = OffboardingCase.objects.create(
            employee=employee,
            reason=reason,
            last_working_day=last_working_day,
            seniority=seniority,
            status=OffboardingCase.Status.OPEN,
            opened_by=user,
        )

        for kind in REQUIRED_STEPS:
            OffboardingStep.objects.create(case=case, kind=kind)

        if not employee.termination_date:
            employee.termination_date = last_working_day
            employee.save(update_fields=['termination_date'])

        if seniority != OffboardingCase.Seniority.EMPLOYEE:
            close_access_now(case)

        # After commit: a mail failure must not roll back the case (Fable 19-Sep-2026).
        transaction.on_commit(lambda: _safe_it_ticket(case.pk))

        AuditLog.objects.create(
            table_name='hris_offboardingcase',
            record_id=str(case.id),
            action='create',
            user=user,
            description=f'Opened offboarding case for {employee.full_name} ({seniority}).',
        )

        return case


def record_step(case, kind, user, *, upload=None, note=''):
    if not can_do_step(user, case, kind):
        raise PermissionError('You are not allowed to complete this step.')

    step = OffboardingStep.objects.get(case=case, kind=kind)
    was_done = step.done_at is not None

    upload_category = UPLOAD_KINDS.get(str(kind))
    if upload_category:
        if not upload:
            raise ValueError('Please attach the document.')

        profile = getattr(case.employee, 'hris_profile', None)
        document = HRDocument.objects.create(
            title=upload.name,
            category=upload_category,
            file=upload,
            is_personal=True,
            employee=profile,
            employee_name=case.employee.full_name,
            uploaded_by=user,
        )
        step.document = document

    if not was_done:
        step.done_by = user
        step.done_at = timezone.now()

    step.note = note[:500]
    step.save()

    AuditLog.objects.create(
        table_name='hris_offboardingstep',
        record_id=str(step.id),
        action='update' if was_done else 'create',
        user=user,
        description=f'Offboarding step {kind} recorded for case {case.id}.',
    )

    if not OffboardingStep.objects.filter(case=case, done_at__isnull=True).exists():
        case.status = OffboardingCase.Status.COMPLETE
        case.completed_at = timezone.now()
        case.save(update_fields=['status', 'completed_at'])

        AuditLog.objects.create(
            table_name='hris_offboardingcase',
            record_id=str(case.id),
            action='update',
            user=user,
            description='All offboarding steps completed; case closed.',
        )

    return step


def archive_block_reason(employee):
    open_cases = OffboardingCase.objects.filter(
        employee=employee,
        status=OffboardingCase.Status.OPEN,
    )
    if open_cases.exists():
        case = open_cases.first()
        outstanding = case.steps.filter(done_at__isnull=True)
        count = outstanding.count()
        labels = [OffboardingStep.Kind(step.kind).label for step in outstanding]
        return (
            f"Offboarding is not finished for {employee.full_name}: "
            f"{count} step(s) outstanding ({', '.join(labels)})."
        )

    if not OffboardingCase.objects.filter(employee=employee).exists() and employee.termination_date:
        if employee.termination_date >= date(2026, 9, 19):
            return f"Open an offboarding case for {employee.full_name} first (People → Leavers)."

    return None


def case_dict(case):
    employee = case.employee
    steps = case.steps.all()
    outstanding = [step for step in steps if step.done_at is None]

    steps_data = []
    for step in steps:
        done_by_name = None
        if step.done_by:
            done_by_name = step.done_by.get_full_name() or step.done_by.username

        steps_data.append({
            'kind': step.kind,
            'label': OffboardingStep.Kind(step.kind).label,
            'done': step.done_at is not None,
            'done_by': done_by_name,
            'done_at': step.done_at.isoformat() if step.done_at else None,
            'note': step.note,
            'document_id': str(step.document_id) if step.document_id else None,
            'document_title': step.document.title if step.document else None,
        })

    items_held = []
    for item in assets_blocking_offboarding(employee):
        items_held.append({
            'tag': getattr(item, 'tag_number', ''),
            'description': getattr(item, 'description', '') or getattr(item, 'name', ''),
        })

    return {
        'id': str(case.id),
        'employee': {
            'id': str(employee.id),
            'name': employee.full_name,
            'job_title': employee.job_title,
            'company': employee.company.name if employee.company else '',
            'department': employee.department,
        },
        'reason': case.reason,
        'last_working_day': case.last_working_day.isoformat() if case.last_working_day else None,
        'seniority': case.seniority,
        'seniority_label': OffboardingCase.Seniority(case.seniority).label,
        'status': case.status,
        'access_removed_at': case.access_removed_at.isoformat() if case.access_removed_at else None,
        'it_ticket_sent_at': case.it_ticket_sent_at.isoformat() if case.it_ticket_sent_at else None,
        'm365_disabled_at': case.m365_disabled_at.isoformat() if case.m365_disabled_at else None,
        'm365_note': case.m365_note,
        'steps': steps_data,
        'outstanding_count': len(outstanding),
        'items_held': items_held,
    }
