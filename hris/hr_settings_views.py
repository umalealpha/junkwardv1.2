import uuid
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from core.hris_access import user_can_access_hris
from core.models import Role, UserRoleAssignment
from payroll.models import Employee

from .hr_settings import HR_TEAM_ROLES, audit, get_setting, is_hr_head, notify_cfo
from .models import HRISProfile, HRSetting, ContractReminderRule

TEAM_ROLE_CODES = set(HR_TEAM_ROLES.keys()) | {'HRIS'}
WRITE_DENIED = {'detail': 'Only HR heads can change HR settings.'}


def _team_payload():
    assignments = (
        UserRoleAssignment.objects
        .select_related('user', 'role')
        .filter(role__code__in=TEAM_ROLE_CODES)
        .order_by('user__email')
    )
    return [{
        'user_id': a.user_id,
        'name': a.user.get_full_name() or a.user.email or a.user.username,
        'email': a.user.email or '',
        'role_code': a.role.code,
        'role_label': HR_TEAM_ROLES.get(a.role.code, a.role.name or a.role.code),
        'assignment_id': a.id,
    } for a in assignments]


def _bool(value):
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in ('1', 'true', 'yes', 'on')
    return bool(value)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def hr_settings_overview(request):
    user = request.user
    if not (user_can_access_hris(user) or is_hr_head(user)):
        return Response({'detail': 'You do not have access to HR settings.'}, status=403)

    try:
        locked = {row.key: row.locked for row in HRSetting.objects.all()}
    except Exception:
        locked = {}
    for key in ('hr_heads', 'contract_reminder_recipients', 'rules_lock'):
        locked.setdefault(key, False)

    rules = []
    for rule in ContractReminderRule.objects.order_by('category'):
        rules.append({
            'category': rule.category,
            'label': getattr(rule, 'get_category_display', lambda: rule.category)(),
            'months_before': rule.months_before,
            'is_active': rule.is_active,
        })

    people = []
    employees = Employee.objects.filter(status='active').select_related('company').order_by('full_name')
    for employee in employees:
        profile = None
        if hasattr(employee, 'hris_profile'):
            profile = employee.hris_profile
        people.append({
            'employee_id': str(employee.id),
            'name': employee.full_name,
            'department': employee.department,
            'company': employee.company.name if employee.company else '',
            'is_controller': bool(profile.is_controller) if profile else False,
            'is_expatriate': bool(profile.is_expatriate) if profile else False,
        })

    return Response({
        'can_edit': is_hr_head(user),
        'heads': get_setting('hr_heads', []),
        'recipients': get_setting('contract_reminder_recipients', []),
        'locked': locked,
        'rules': rules,
        'team': _team_payload(),
        'people': people,
    })


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def hr_setting_update(request):
    if not is_hr_head(request.user):
        return Response(WRITE_DENIED, status=403)

    key = request.data.get('key')
    if key not in ('hr_heads', 'contract_reminder_recipients'):
        return Response({'detail': 'Unknown setting key.'}, status=400)

    value = request.data.get('value')
    if not isinstance(value, list):
        return Response({'detail': 'Value must be a list of email addresses.'}, status=400)

    cleaned = []
    seen = set()
    for item in value:
        email = str(item).strip().lower()
        try:
            validate_email(email)
        except ValidationError:
            return Response({'detail': f'Invalid email: {item}'}, status=400)
        if email not in seen:
            seen.add(email)
            cleaned.append(email)

    if key == 'hr_heads' and not cleaned:
        return Response({'detail': 'At least one HR head is needed.'}, status=400)

    try:
        row = HRSetting.objects.get(key=key)
        if row.locked:
            return Response({'detail': 'This setting is locked. Unlock it first.'}, status=409)
        old = row.value
        row.value = cleaned
        row.updated_by = request.user
        row.save()
    except HRSetting.DoesNotExist:
        old = []
        HRSetting.objects.create(key=key, value=cleaned, locked=False, updated_by=request.user)

    audit(request.user, key, old, cleaned, f'Updated {key}')
    notify_cfo(request.user, f'{key} updated to {len(cleaned)} address(es).')
    return Response({'ok': True, 'key': key, 'value': cleaned})


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def hr_setting_lock(request):
    if not is_hr_head(request.user):
        return Response(WRITE_DENIED, status=403)

    key = request.data.get('key')
    if key not in ('hr_heads', 'contract_reminder_recipients', 'rules_lock'):
        return Response({'detail': 'Unknown setting key.'}, status=400)

    locked = request.data.get('locked')
    if not isinstance(locked, bool):
        return Response({'detail': 'locked must be a boolean.'}, status=400)

    try:
        row = HRSetting.objects.get(key=key)
        old = row.locked
        row.locked = locked
        row.updated_by = request.user
        row.save()
    except HRSetting.DoesNotExist:
        old = False
        HRSetting.objects.create(key=key, value={}, locked=locked, updated_by=request.user)

    audit(request.user, key, {'locked': old}, {'locked': locked}, f"{'Locked' if locked else 'Unlocked'} {key}")
    notify_cfo(request.user, f"{key} {'locked' if locked else 'unlocked'}")
    return Response({'ok': True, 'key': key, 'locked': locked})


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def hr_rule_update(request):
    if not is_hr_head(request.user):
        return Response(WRITE_DENIED, status=403)

    rules_lock = HRSetting.objects.filter(key='rules_lock').first()
    if rules_lock and rules_lock.locked:
        return Response({'detail': 'HR rules are locked.'}, status=409)

    category = request.data.get('category')
    try:
        allowed_categories = [c for c, _ in ContractReminderRule.Category.choices]
    except AttributeError:
        allowed_categories = ['expatriate', 'controller', 'c_suite', 'senior_manager', 'employee']

    if category not in allowed_categories:
        return Response({'detail': 'Unknown rule category.'}, status=400)

    months_before = request.data.get('months_before')
    if isinstance(months_before, bool) or not isinstance(months_before, int) or months_before < 1 or months_before > 24:
        return Response({'detail': 'months_before must be an integer from 1 to 24.'}, status=400)

    is_active = request.data.get('is_active')
    if not isinstance(is_active, bool):
        return Response({'detail': 'is_active must be a boolean.'}, status=400)

    old_rule = ContractReminderRule.objects.filter(category=category).first()
    old = {'months_before': old_rule.months_before, 'is_active': old_rule.is_active} if old_rule else None
    rule, _ = ContractReminderRule.objects.update_or_create(
        category=category,
        defaults={'months_before': months_before, 'is_active': is_active},
    )
    new = {'months_before': rule.months_before, 'is_active': rule.is_active}

    audit(request.user, f'rule:{category}', old, new, f'Updated contract reminder rule {category}')
    notify_cfo(request.user, f'Contract reminder rule {category} updated.')
    return Response({'ok': True, 'category': category, 'months_before': rule.months_before, 'is_active': rule.is_active})


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def hr_team_add(request):
    if not is_hr_head(request.user):
        return Response(WRITE_DENIED, status=403)

    email = str(request.data.get('email') or '').strip().lower()
    if not email:
        return Response({'detail': 'Email is required.'}, status=400)

    try:
        validate_email(email)
    except ValidationError:
        return Response({'detail': 'Invalid email.'}, status=400)

    role_code = request.data.get('role_code')
    if role_code not in HR_TEAM_ROLES:
        return Response({'detail': 'Unknown role code.'}, status=400)

    User = get_user_model()
    added_user = User.objects.filter(email__iexact=email, is_active=True).first()
    if not added_user:
        return Response({'detail': 'No Omni login for that email yet.'}, status=404)

    role = Role.objects.filter(code=role_code).first()
    if not role:
        return Response({'detail': 'Role not found.'}, status=404)

    assignment = UserRoleAssignment.objects.filter(user=added_user, role=role).first()
    created = assignment is None
    if created:
        assignment = UserRoleAssignment.objects.create(
            user=added_user,
            role=role,
            assigned_by=request.user,
            scope_department='',
        )
        old_values = {}
    else:
        old_values = {'email': added_user.email, 'role_code': role_code, 'existed': True}
        assignment.assigned_by = request.user
        assignment.save(update_fields=['assigned_by'])

    new_values = {
        'email': added_user.email,
        'role_code': role_code,
        'assigned_by': request.user.email or request.user.username,
    }
    audit(request.user, f'team:{added_user.email}', old_values, new_values, f'Added {added_user.email} to role {role_code}')
    notify_cfo(request.user, f'Added {added_user.email} as {role_code}.')
    return Response({'ok': True, 'team': _team_payload()})


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def hr_team_remove(request):
    if not is_hr_head(request.user):
        return Response(WRITE_DENIED, status=403)

    try:
        assignment_id = uuid.UUID(str(request.data.get('assignment_id')))
    except (TypeError, ValueError):
        return Response({'detail': 'Assignment not found.'}, status=404)

    assignment = (
        UserRoleAssignment.objects
        .select_related('user', 'role')
        .filter(id=assignment_id, role__code__in=TEAM_ROLE_CODES)
        .first()
    )
    if not assignment:
        return Response({'detail': 'Assignment not found.'}, status=404)

    if assignment.user_id == request.user.id:
        own_count = UserRoleAssignment.objects.filter(
            user=request.user,
            role__code__in=TEAM_ROLE_CODES,
        ).count()
        if own_count <= 1:
            return Response({'detail': 'You cannot remove your own access.'}, status=400)

    email = assignment.user.email
    role_code = assignment.role.code
    old_values = {'email': email, 'role_code': role_code}
    assignment.delete()

    audit(request.user, f'team:{email}', old_values, None, f'Removed {email} from role {role_code}')
    notify_cfo(request.user, f'Removed {email} from role {role_code}.')
    return Response({'ok': True, 'team': _team_payload()})


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def hr_person_flags(request):
    if not is_hr_head(request.user):
        return Response(WRITE_DENIED, status=403)

    try:
        employee_id = uuid.UUID(str(request.data.get('employee_id')))
    except (TypeError, ValueError):
        return Response({'detail': 'Employee not found.'}, status=404)

    employee = Employee.objects.filter(id=employee_id).first()
    if not employee:
        return Response({'detail': 'Employee not found.'}, status=404)

    if 'is_controller' not in request.data and 'is_expatriate' not in request.data:
        return Response({'detail': 'No flags supplied.'}, status=400)

    profile, _ = HRISProfile.objects.get_or_create(employee=employee)
    old_values = {
        'employee_id': str(employee.id),
        'is_controller': bool(profile.is_controller),
        'is_expatriate': bool(profile.is_expatriate),
    }

    if 'is_controller' in request.data:
        profile.is_controller = _bool(request.data.get('is_controller'))
    if 'is_expatriate' in request.data:
        profile.is_expatriate = _bool(request.data.get('is_expatriate'))
    profile.save()

    new_values = {
        'employee_id': str(employee.id),
        'is_controller': bool(profile.is_controller),
        'is_expatriate': bool(profile.is_expatriate),
    }
    audit(request.user, f'person-flags:{employee.id}', old_values, new_values, f'Updated HR flags for {employee.full_name}')
    return Response({
        'ok': True,
        'employee_id': str(employee.id),
        'is_controller': bool(profile.is_controller),
        'is_expatriate': bool(profile.is_expatriate),
    })


@api_view(['GET', 'POST'])
@permission_classes([IsAuthenticated])
def hr_role_systems(request):
    """Which systems IT sets up per department (HC 19-Sep-2026). HR heads edit."""
    from .models import RoleSystemRequirement
    if request.method == 'GET':
        if not (user_can_access_hris(request.user) or is_hr_head(request.user)):
            return Response({'detail': 'HR access required.'}, status=403)
        rows = [{'department': r.department, 'systems': r.systems or [], 'note': r.note}
                for r in RoleSystemRequirement.objects.order_by('department')]
        return Response({'rows': rows, 'can_edit': is_hr_head(request.user)})
    if not is_hr_head(request.user):
        return Response(WRITE_DENIED, status=403)
    department = str(request.data.get('department') or '').strip()[:100]
    systems = request.data.get('systems') or []
    if not department or not isinstance(systems, list):
        return Response({'detail': 'A department and a list of systems are needed.'}, status=400)
    systems = [str(s).strip()[:120] for s in systems if str(s).strip()]
    old = list(RoleSystemRequirement.objects.filter(department=department).values('systems', 'note'))
    if not systems:
        RoleSystemRequirement.objects.filter(department=department).exclude(department='*').delete()
    else:
        RoleSystemRequirement.objects.update_or_create(
            department=department,
            defaults={'systems': systems, 'note': str(request.data.get('note') or '')[:300]})
    audit(request.user, f'role-systems:{department}', {'rows': old}, {'systems': systems},
          f'Systems for {department} updated')
    notify_cfo(request.user, f'Systems for new joiners in {department}: {", ".join(systems) or "(removed)"}')
    return hr_role_systems_get(request)


def hr_role_systems_get(request):
    from .models import RoleSystemRequirement
    rows = [{'department': r.department, 'systems': r.systems or [], 'note': r.note}
            for r in RoleSystemRequirement.objects.order_by('department')]
    return Response({'rows': rows, 'can_edit': True})
