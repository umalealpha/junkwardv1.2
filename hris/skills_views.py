"""
hris/skills_views.py — Skills & Gaps Map API.

A grid of who has which skill across the org (proficiency 1-5), plus a
gap analysis per skill: how many people are proficient (level >= 3), and
whether the skill is a single point of failure (0-1 proficient people —
risk if that person leaves).

Endpoints (whitelist-gated like the rest of the talent cluster):

  GET  /api/v1/hris/skills/matrix/      → matrix + gap analysis
  POST /api/v1/hris/skills/skill/       → create a Skill
  POST /api/v1/hris/skills/set-level/   → upsert one EmployeeSkill

Reads are company-scoped like every other HRIS surface (core.mixins.
apply_company_scope). Writing a level for an employee outside the
caller's entity scope is rejected the same way
hris/performance_views.py:monthly_checkins rejects it.
"""
from __future__ import annotations

from django.db import IntegrityError
from django.db.models import Count
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from core.mixins import apply_company_scope
from hris.api_views import _deny_if_not_whitelisted
from hris.models import HRISProfile
from hris.skills_models import EmployeeSkill, Skill
from payroll.models import Employee

PROFICIENT_LEVEL = 3     # level >= this counts as "proficient" on a skill
SPOF_MAX_HOLDERS = 1     # <= this many proficient people = single point of failure


def _scoped_profiles(request):
    """HRISProfile queryset for the caller's entity scope, current staff only."""
    qs = (HRISProfile.objects
          .select_related('employee')
          .exclude(employee__status=Employee.Status.TERMINATED))
    return apply_company_scope(request, qs, 'employee__company_id')


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def skills_matrix(request):
    """The full skills x people grid, plus a gap analysis per skill."""
    denied = _deny_if_not_whitelisted(request)
    if denied is not None:
        return denied

    skills = list(Skill.objects.all())
    profiles = list(_scoped_profiles(request).order_by('employee__full_name'))
    profile_ids = [p.pk for p in profiles]

    holdings = (EmployeeSkill.objects
                .filter(profile_id__in=profile_ids)
                .values_list('profile_id', 'skill_id', 'level'))
    levels = {f'{pid}:{sid}': level for pid, sid, level in holdings}

    proficient_counts = dict(
        EmployeeSkill.objects
        .filter(profile_id__in=profile_ids, level__gte=PROFICIENT_LEVEL)
        .values('skill_id')
        .annotate(n=Count('id'))
        .values_list('skill_id', 'n')
    )

    return Response({
        'skills': [
            {'id': str(s.id), 'name': s.name, 'category': s.category}
            for s in skills
        ],
        'people': [
            {
                'profile_id': str(p.id),
                'name': p.employee.full_name,
                'department': p.employee.department,
            }
            for p in profiles
        ],
        'levels': levels,
        'gaps': [
            {
                'skill_id': str(s.id),
                'name': s.name,
                'proficient': proficient_counts.get(s.id, 0),
                'single_point_of_failure': proficient_counts.get(s.id, 0) <= SPOF_MAX_HOLDERS,
            }
            for s in skills
        ],
    })


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def create_skill(request):
    """Add a new skill to the org-wide catalogue."""
    denied = _deny_if_not_whitelisted(request)
    if denied is not None:
        return denied

    data = request.data or {}
    name = str(data.get('name') or '').strip()[:120]
    if not name:
        return Response({'detail': 'name is required.'},
                        status=status.HTTP_400_BAD_REQUEST)
    if Skill.objects.filter(name__iexact=name).exists():
        return Response({'detail': 'A skill with that name already exists.'},
                        status=status.HTTP_400_BAD_REQUEST)

    try:
        skill = Skill.objects.create(
            name=name,
            category=str(data.get('category') or '').strip()[:60],
        )
    except IntegrityError:
        return Response({'detail': 'A skill with that name already exists.'},
                        status=status.HTTP_400_BAD_REQUEST)

    return Response(
        {'id': str(skill.id), 'name': skill.name, 'category': skill.category},
        status=status.HTTP_201_CREATED,
    )


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def set_level(request):
    """Upsert one employee's proficiency level (1-5) on one skill."""
    denied = _deny_if_not_whitelisted(request)
    if denied is not None:
        return denied

    data = request.data or {}
    profile_id = data.get('profile_id')
    skill_id = data.get('skill_id')
    if not profile_id or not skill_id:
        return Response({'detail': 'profile_id and skill_id are required.'},
                        status=status.HTTP_400_BAD_REQUEST)
    try:
        level = int(data.get('level'))
    except (TypeError, ValueError):
        return Response({'detail': 'level must be an integer 1-5.'},
                        status=status.HTTP_400_BAD_REQUEST)
    if level < 1 or level > 5:
        return Response({'detail': 'level must be between 1 and 5.'},
                        status=status.HTTP_400_BAD_REQUEST)

    profile = _scoped_profiles(request).filter(pk=profile_id).first()
    if profile is None:
        return Response({'detail': 'Employee not found or outside your entity scope.'},
                        status=status.HTTP_404_NOT_FOUND)
    skill = Skill.objects.filter(pk=skill_id).first()
    if skill is None:
        return Response({'detail': 'Skill not found.'},
                        status=status.HTTP_404_NOT_FOUND)

    note = str(data.get('note') or '').strip()[:200]
    holding, _created = EmployeeSkill.objects.update_or_create(
        profile=profile, skill=skill,
        defaults={'level': level, 'note': note},
    )
    return Response({
        'profile_id': str(holding.profile_id),
        'skill_id': str(holding.skill_id),
        'level': holding.level,
        'note': holding.note,
    })
