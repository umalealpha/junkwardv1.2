"""Business service for controlled employee onboarding."""
from __future__ import annotations

import hashlib
import json
import re
import uuid
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
from difflib import SequenceMatcher

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.db import IntegrityError, transaction
from django.utils import timezone

from core.hris_access import hris_role
from core.models import AuditLog, allowed_company_ids, user_can_write_company
from hris.departments import DEPARTMENTS, canonical_department
from hris.models import HRISProfile, OnboardingTask
from hris.onboarding_models import OnboardingRequest
from payroll.models import Employee

User = get_user_model()
DECISION_ROLES = {'hr', 'hris', 'admin', 'superadmin'}


def _clean(value, limit: int) -> str:
    return str(value or '').strip()[:limit]


def _digits(value: str) -> str:
    return re.sub(r'\D', '', value or '')


def _can_manage(user) -> bool:
    return bool(user and getattr(user, 'is_authenticated', False)
                and hris_role(user) in DECISION_ROLES)


def _can_write_company(user, company) -> bool:
    """Respect explicit entity grants; otherwise restrict HR users to own entity."""
    allowed = allowed_company_ids(user)
    if allowed == {'*'}:
        return True
    if allowed:
        return user_can_write_company(user, company.pk)
    own = getattr(user, 'employee_record', None)
    return bool(own and own.company_id == company.pk and _can_manage(user))


def _normalise_payload(data: dict) -> dict:
    from core.models import Company

    full_name = _clean(data.get('full_name'), 200)
    email = _clean(data.get('email'), 254).lower()
    if not full_name:
        raise ValidationError('Full name is required.')
    if not email:
        raise ValidationError('Work email is required — it links the person to their login.')
    try:
        validate_email(email)
    except ValidationError as exc:
        raise ValidationError('Enter a valid work email address.') from exc

    company_id = _clean(data.get('company_id'), 64)
    if not company_id:
        raise ValidationError('Entity is required before onboarding can be submitted.')
    company = Company.objects.filter(pk=company_id, is_active=True).first()
    if company is None:
        raise ValidationError('That active entity was not found.')

    raw_date = _clean(data.get('hire_date'), 20)
    if not raw_date:
        raise ValidationError('Start date is required.')
    try:
        hire_date = date.fromisoformat(raw_date)
    except ValueError as exc:
        raise ValidationError('Start date must use YYYY-MM-DD.') from exc

    manager = None
    manager_id = _clean(data.get('manager_id'), 64)
    if manager_id:
        manager = Employee.objects.filter(pk=manager_id, is_archived=False).exclude(
            status=Employee.Status.TERMINATED).first()
        if manager is None:
            raise ValidationError('The selected line manager is not an active employee.')
        if manager.company_id != company.pk:
            raise ValidationError('The selected line manager must belong to the chosen entity.')

    leave_approver = None
    leave_approver_id = _clean(data.get('leave_approver_id'), 64)
    if leave_approver_id:
        leave_approver = User.objects.filter(pk=leave_approver_id, is_active=True).first()
        linked = getattr(leave_approver, 'employee_record', None) if leave_approver else None
        if not linked or linked.is_archived or linked.status == Employee.Status.TERMINATED:
            raise ValidationError('The selected leave approver must have an active employee login.')
        if linked.company_id != company.pk:
            raise ValidationError('The selected leave approver must belong to the chosen entity.')
        if manager is not None and linked.pk != manager.pk and not HRISProfile.objects.filter(manager=linked).exists():
            raise ValidationError('The selected leave approver must be a recognised people manager.')

    employee_number = _clean(data.get('employee_number'), 30).upper()
    if employee_number and Employee.objects.filter(employee_number__iexact=employee_number).exists():
        existing = Employee.objects.filter(employee_number__iexact=employee_number).first()
        if (existing.email or '').strip().lower() != email:
            raise ValidationError('That employee number already belongs to another employee.')

    # Annual leave entitlement (days/year) — optional; seeds accrual on approval
    # (bug c48f10b0). Blank means "use the CoS default", not zero.
    ale_raw = _clean(data.get('annual_leave_entitlement'), 12)
    annual_leave_entitlement = None
    if ale_raw:
        try:
            annual_leave_entitlement = Decimal(ale_raw)
        except (InvalidOperation, ValueError) as exc:
            raise ValidationError('Annual leave entitlement must be a number of days.') from exc
        if annual_leave_entitlement < 0 or annual_leave_entitlement > 365:
            raise ValidationError('Annual leave entitlement must be between 0 and 365 days.')

    # Department is required and must be one of the approved names (CFO
    # 18-Sep-2026). No default: a blank used to go through and nothing
    # downstream could tell which team the new hire belonged to.
    department_raw = _clean(data.get('department'), 100)
    if not department_raw:
        raise ValidationError('Choose a department.')
    department = canonical_department(department_raw)
    if department is None:
        raise ValidationError(
            f'"{department_raw}" is not an approved department. '
            f'Choose one of: {", ".join(DEPARTMENTS)}.')

    return {
        'full_name': full_name,
        'email': email,
        'employee_number': employee_number,
        'department': department,
        'job_title': _clean(data.get('job_title'), 100),
        'phone': _clean(data.get('phone'), 50),
        'hire_date': hire_date,
        'annual_leave_entitlement': annual_leave_entitlement,
        'company': company,
        'manager': manager,
        'default_leave_approver': leave_approver,
    }


def _payload_hash(payload: dict) -> str:
    stable = {
        'full_name': payload['full_name'].casefold(),
        'email': payload['email'],
        'employee_number': payload['employee_number'],
        'department': payload['department'].casefold(),
        'job_title': payload['job_title'].casefold(),
        'phone': _digits(payload['phone']),
        'hire_date': payload['hire_date'].isoformat(),
        'annual_leave_entitlement': str(payload['annual_leave_entitlement'])
        if payload['annual_leave_entitlement'] is not None else '',
        'company_id': str(payload['company'].pk),
        'manager_id': str(payload['manager'].pk) if payload['manager'] else '',
        'default_leave_approver_id': str(payload['default_leave_approver'].pk)
        if payload['default_leave_approver'] else '',
    }
    raw = json.dumps(stable, sort_keys=True, separators=(',', ':')).encode()
    return hashlib.sha256(raw).hexdigest()


def duplicate_warnings(payload: dict) -> list[dict]:
    warnings: list[dict] = []
    email = payload['email']
    full_name = payload['full_name']
    phone_digits = _digits(payload['phone'])

    for emp in Employee.objects.filter(email__iexact=email)[:3]:
        warnings.append({
            'code': 'existing_email', 'severity': 'high', 'employee_id': str(emp.pk),
            'message': f'An employee already uses {email}; approval will complete that record, not duplicate it.',
        })
        current_dept = (emp.department or '').strip()
        if current_dept and current_dept.casefold() != payload['department'].casefold():
            # Approval only fills BLANK fields on an existing record, so the
            # requested department would be silently dropped. Say so up front.
            warnings.append({
                'code': 'department_differs', 'severity': 'medium', 'employee_id': str(emp.pk),
                'message': (f'{emp.full_name} is already in "{current_dept}". Approval will NOT '
                            f'change that to "{payload["department"]}"; use Amendments if it should move.'),
            })

    for emp in Employee.objects.filter(full_name__iexact=full_name).exclude(email__iexact=email)[:5]:
        warnings.append({
            'code': 'matching_name', 'severity': 'medium', 'employee_id': str(emp.pk),
            'message': f'Existing employee has the same name: {emp.full_name}.',
        })

    employee_number = payload['employee_number']
    if employee_number:
        for emp in Employee.objects.filter(employee_number__iexact=employee_number).exclude(email__iexact=email)[:3]:
            warnings.append({
                'code': 'matching_employee_number', 'severity': 'high', 'employee_id': str(emp.pk),
                'message': f'Employee number {employee_number} is already in use.',
            })

    if phone_digits:
        for emp in Employee.objects.exclude(phone='').only('id', 'full_name', 'phone')[:1000]:
            if _digits(emp.phone) == phone_digits:
                warnings.append({
                    'code': 'matching_phone', 'severity': 'high', 'employee_id': str(emp.pk),
                    'message': f'Phone number matches existing employee {emp.full_name}.',
                })
                break

    local, _, domain = email.partition('@')
    if len(local) >= 4 and domain:
        candidates = Employee.objects.filter(email__iendswith='@' + domain).exclude(
            email__iexact=email).exclude(email='').only('id', 'full_name', 'email')[:500]
        for emp in candidates:
            score = SequenceMatcher(None, email, emp.email.lower()).ratio()
            if score >= 0.88:
                warnings.append({
                    'code': 'similar_email', 'severity': 'medium', 'employee_id': str(emp.pk),
                    'message': f'Email is very similar to {emp.email} ({emp.full_name}).',
                })
                break

    return warnings


def request_dict(req: OnboardingRequest, *, user=None) -> dict:
    return {
        'id': str(req.pk),
        'correlation_id': str(req.correlation_id),
        'status': req.status,
        'full_name': req.full_name,
        'email': req.email,
        'employee_number': req.employee_number,
        'department': req.department,
        'job_title': req.job_title,
        'phone': req.phone,
        'hire_date': req.hire_date.isoformat(),
        'company': {'id': str(req.company_id), 'code': req.company.code, 'name': req.company.name},
        'manager': ({'id': str(req.manager_id), 'name': req.manager.full_name}
                    if req.manager_id else None),
        'default_leave_approver': ({
            'id': req.default_leave_approver_id,
            'name': (getattr(getattr(req.default_leave_approver, 'employee_record', None),
                             'full_name', '')
                     or req.default_leave_approver.get_full_name()
                     or req.default_leave_approver.username),
        } if req.default_leave_approver_id else None),
        'risk_warnings': req.risk_warnings or [],
        'maker': req.maker_email or req.maker.username,
        'approver': req.approver_email or (req.approver.username if req.approver_id else ''),
        'decision_notes': req.decision_notes,
        'employee_id': str(req.employee_id) if req.employee_id else None,
        'created_at': req.created_at.isoformat(),
        'decided_at': req.decided_at.isoformat() if req.decided_at else None,
        'can_decide': bool(user and _can_manage(user) and user.pk != req.maker_id
                           and req.status == OnboardingRequest.Status.PENDING
                           and _can_write_company(user, req.company)),
    }


def _audit(req: OnboardingRequest, *, action: str, user, event: str,
           extra: dict | None = None) -> None:
    values = {
        'event': event,
        'status': req.status,
        'email': req.email,
        'company': req.company.code,
        'correlation_id': str(req.correlation_id),
    }
    values.update(extra or {})
    AuditLog.objects.create(
        table_name='OnboardingRequest', record_id=str(req.pk), action=action,
        new_values=values, user=user,
        description=f'Onboarding {event}: {req.full_name} ({req.company.code})',
    )


def submit_onboarding_request(*, maker, data: dict) -> tuple[OnboardingRequest, bool]:
    if not _can_manage(maker):
        raise ValidationError('Only authorised HR personnel can submit onboarding requests.')
    payload = _normalise_payload(data)
    if not _can_write_company(maker, payload['company']):
        raise ValidationError('You do not have write access to that entity.')
    digest = _payload_hash(payload)

    with transaction.atomic():
        pending = (OnboardingRequest.objects.select_for_update()
                   .filter(email__iexact=payload['email'], status=OnboardingRequest.Status.PENDING)
                   .first())
        if pending:
            if pending.payload_hash != digest:
                raise ValidationError(
                    'A different onboarding request for this email is already pending. '
                    'Reject it before submitting changed details.')
            _audit(pending, action=AuditLog.Action.UPDATE, user=maker,
                   event='idempotent_retry', extra={'payload_hash': digest})
            return pending, True

        warnings = duplicate_warnings(payload)
        try:
            # Inner savepoint: on Django 5.2 the partial-unique-index violation
            # marks the OUTER atomic block for rollback, so without this savepoint
            # the IntegrityError fallback's .get() would raise
            # TransactionManagementError instead of gracefully reusing the pending
            # request on a concurrent double-submit. (Fable H26.)
            with transaction.atomic():
                req = OnboardingRequest.objects.create(
                    **payload, risk_warnings=warnings, payload_hash=digest,
                    maker=maker, maker_email=(maker.email or '').lower(),
                )
        except IntegrityError:
            req = OnboardingRequest.objects.get(
                email__iexact=payload['email'], status=OnboardingRequest.Status.PENDING)
            if req.payload_hash != digest:
                raise ValidationError(
                    'A different onboarding request for this email is already pending.')
            return req, True
        _audit(req, action=AuditLog.Action.CREATE, user=maker, event='submitted',
               extra={'warning_count': len(warnings), 'payload_hash': digest})
        return req, False


def _apply_request(req: OnboardingRequest, approver) -> tuple[Employee, bool, bool]:
    existing = Employee.objects.filter(email__iexact=req.email).first()
    created = existing is None
    if existing is not None and (existing.is_archived or existing.status == Employee.Status.TERMINATED):
        raise ValidationError('This email belongs to a terminated or archived employee; use the supported rehire process.')

    if created:
        emp = Employee(
            employee_number=req.employee_number or 'NEW-' + uuid.uuid4().hex[:12].upper(),
            full_name=req.full_name,
            email=req.email,
            department=req.department,
            job_title=req.job_title,
            phone=req.phone,
            hire_date=req.hire_date,
            company=req.company,
            status=Employee.Status.ACTIVE,
            external_ref=f'omni-onboard:{req.correlation_id}',
        )
        emp.save(audit_user=approver,
                 audit_description=f'Approved onboarding request {req.pk}')
    else:
        emp = existing
        updates: list[str] = []
        for field, value in (
            ('employee_number', req.employee_number), ('department', req.department),
            ('job_title', req.job_title), ('phone', req.phone), ('hire_date', req.hire_date),
            ('company', req.company),
        ):
            current = getattr(emp, field)
            if (current in ('', None)) and value not in ('', None):
                setattr(emp, field, value)
                updates.append(field)
        if updates:
            emp.save(update_fields=updates, audit_user=approver,
                     audit_description=f'Completed by approved onboarding request {req.pk}')

    profile, profile_created = HRISProfile.objects.get_or_create(
        employee=emp,
        defaults={
            'talent_segment': HRISProfile.TalentSegment.NEW_HIRE,
            'manager': req.manager,
            'default_leave_approver': req.default_leave_approver,
        },
    )
    if not profile_created:
        profile_updates: list[str] = []
        if profile.manager_id is None and req.manager_id:
            profile.manager = req.manager
            profile_updates.append('manager')
        if profile.default_leave_approver_id is None and req.default_leave_approver_id:
            profile.default_leave_approver = req.default_leave_approver
            profile_updates.append('default_leave_approver')
        if not profile.talent_segment:
            profile.talent_segment = HRISProfile.TalentSegment.NEW_HIRE
            profile_updates.append('talent_segment')
        if profile_updates:
            profile.save(update_fields=profile_updates, audit_user=approver,
                         audit_description=f'Assignments from onboarding request {req.pk}')

    # Seed the annual-leave opening balance so the new joiner shows on the Leave
    # Report and accrues from their start date — instead of zero until a manual
    # CSV upload (bug c48f10b0). Idempotent: skips if a row already exists.
    from hris.leave_onboarding import seed_annual_opening
    seed_annual_opening(profile, req.hire_date, req.annual_leave_entitlement, approver)

    if not emp.user_id:
        login = User.objects.filter(email__iexact=req.email, is_active=True).first()
        if login:
            emp.user = login
            emp.save(update_fields=['user'], audit_user=approver,
                     audit_description=f'Login linked by onboarding request {req.pk}')

    owner = (req.manager.user if req.manager_id and req.manager.user_id else approver)
    base = req.hire_date
    tasks = [
        ('Set up Microsoft and Omni access', OnboardingTask.Category.IT, base),
        ('Complete induction and policy acknowledgements', OnboardingTask.Category.TRAINING, base + timedelta(days=7)),
        ('30-day manager check-in', OnboardingTask.Category.CHECK30, base + timedelta(days=30)),
        ('60-day manager check-in', OnboardingTask.Category.CHECK60, base + timedelta(days=60)),
        ('90-day manager check-in', OnboardingTask.Category.CHECK90, base + timedelta(days=90)),
    ]
    for title, category, due in tasks:
        OnboardingTask.objects.get_or_create(
            employee=emp, title=title,
            defaults={'category': category, 'due_date': due, 'owner_user': owner},
        )
    return emp, created, profile_created


def approve_onboarding_request(*, request_id, approver, notes: str = '') -> tuple[OnboardingRequest, bool]:
    if not _can_manage(approver):
        raise ValidationError('You are not authorised to approve onboarding requests.')
    with transaction.atomic():
        # Lock only the request row. PostgreSQL refuses FOR UPDATE across the
        # nullable outer joins created by select_related(manager/approver/employee).
        req = OnboardingRequest.objects.select_for_update().get(pk=request_id)
        if req.status == OnboardingRequest.Status.APPROVED:
            return req, True
        if req.status != OnboardingRequest.Status.PENDING:
            raise ValidationError(f'This onboarding request is {req.status}, not pending.')
        if req.maker_id == approver.pk:
            raise ValidationError('The maker cannot approve their own onboarding request.')
        if not _can_write_company(approver, req.company):
            raise ValidationError('You do not have write access to that entity.')

        emp, created, profile_created = _apply_request(req, approver)
        # New-joiner pack (HC 19-Sep-2026): 30-day checklist, JD + policy sign-offs,
        # manager told, IT told the systems for the role. After commit, never blocks.
        from hris.joiner_pack import start_joiner_pack
        transaction.on_commit(lambda: start_joiner_pack(emp, approver))
        req.status = OnboardingRequest.Status.APPROVED
        req.approver = approver
        req.approver_email = (approver.email or '').lower()
        req.decided_at = timezone.now()
        req.decision_notes = _clean(notes, 2000)
        req.employee = emp
        req.save(update_fields=[
            'status', 'approver', 'approver_email', 'decided_at',
            'decision_notes', 'employee', 'updated_at',
        ])
        _audit(req, action=AuditLog.Action.APPROVE, user=approver, event='approved',
               extra={'employee_id': str(emp.pk), 'employee_created': created,
                      'profile_created': profile_created})
        return req, False


def reject_onboarding_request(*, request_id, approver, notes: str) -> OnboardingRequest:
    if not _can_manage(approver):
        raise ValidationError('You are not authorised to reject onboarding requests.')
    clean_notes = _clean(notes, 2000)
    if not clean_notes:
        raise ValidationError('A rejection reason is required.')
    with transaction.atomic():
        req = (OnboardingRequest.objects.select_for_update()
               .select_related('company', 'maker').get(pk=request_id))
        if req.status != OnboardingRequest.Status.PENDING:
            raise ValidationError(f'This onboarding request is {req.status}, not pending.')
        if req.maker_id == approver.pk:
            raise ValidationError('The maker cannot decide their own onboarding request.')
        if not _can_write_company(approver, req.company):
            raise ValidationError('You do not have write access to that entity.')
        req.status = OnboardingRequest.Status.REJECTED
        req.approver = approver
        req.approver_email = (approver.email or '').lower()
        req.decided_at = timezone.now()
        req.decision_notes = clean_notes
        req.save(update_fields=[
            'status', 'approver', 'approver_email', 'decided_at',
            'decision_notes', 'updated_at',
        ])
        _audit(req, action=AuditLog.Action.UPDATE, user=approver, event='rejected')
        return req
