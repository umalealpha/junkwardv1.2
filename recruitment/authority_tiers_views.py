"""Position tiers + job-title tagging — API (Unami Hiring-SOP, CFO 2026-09-02).

  GET  recruitment/position-tiers/            list the 5 tiers + their bands + chains
  PUT  recruitment/position-tiers/<tier>/     set a tier's basic-salary band (HCM/CFO)
  GET  recruitment/job-title-tiers/           every current job title + its mapped tier
  POST recruitment/job-title-tiers/           tag a title to a tier (HCM/CFO)

Reading the tiers is open to anyone who may use the Authorities area (they need
it to raise/sign). SETTING a band or tagging a title is an HCM/CFO admin action —
it is salary policy — so it sits behind can_manage_tiers.
"""
from __future__ import annotations

from django.db.models import Count
from django.db.models.functions import Lower
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework import status as http

from . import authority_access as access
from .models import (SIGNATORY_DIRECTORY, JobTitleTier, PositionTier)

# Who may EDIT bands / tag titles — Human Capital, the HR Business Partner
# (Dorothy, who maintains the data day to day, CFO 2026-09-02) and the CFO, plus
# the superuser.
_MANAGE_SLUGS = ('cfo', 'human_capital', 'hr_bp')


def can_manage_tiers(user) -> bool:
    if getattr(user, 'is_superuser', False):
        return True
    email = (getattr(user, 'email', '') or '').strip().lower()
    return email in {SIGNATORY_DIRECTORY[s][1].lower()
                     for s in _MANAGE_SLUGS if SIGNATORY_DIRECTORY.get(s)}


def _tier_dict(t: PositionTier) -> dict:
    return {
        'tier': t.tier,
        'name': t.name,
        'basic_salary_min': str(t.basic_salary_min) if t.basic_salary_min is not None else None,
        'basic_salary_max': str(t.basic_salary_max) if t.basic_salary_max is not None else None,
        'standard_signatories': [{'slug': s, 'label': SIGNATORY_DIRECTORY.get(s, (s.replace('_', ' ').title(),))[0]}
                                 for s in t.standard_slugs()],
        'exception_signatories': [{'slug': s, 'label': SIGNATORY_DIRECTORY.get(s, (s.replace('_', ' ').title(),))[0]}
                                  for s in t.exception_slugs()],
    }


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def position_tiers(request):
    if not access.can_view(request.user):
        return Response({'detail': 'Not available to you.'}, status=http.HTTP_403_FORBIDDEN)
    tiers = list(PositionTier.objects.all())
    return Response({
        'can_manage': can_manage_tiers(request.user),
        'tiers': [_tier_dict(t) for t in tiers],
    })


@api_view(['PUT'])
@permission_classes([IsAuthenticated])
def position_tier_update(request, tier):
    """Set a tier's basic-salary band. Basic salary only (allowances excluded)."""
    if not can_manage_tiers(request.user):
        return Response({'detail': 'Only Human Capital, the HR Business Partner or the CFO '
                                   'may set salary bands.'},
                        status=http.HTTP_403_FORBIDDEN)
    t = PositionTier.objects.filter(tier=tier).first()
    if t is None:
        return Response({'detail': 'Unknown tier.'}, status=http.HTTP_404_NOT_FOUND)
    d = request.data or {}

    def _parse(key):
        v = d.get(key)
        if v in (None, ''):
            return None
        from decimal import Decimal, InvalidOperation
        try:
            return Decimal(str(v))
        except (InvalidOperation, TypeError, ValueError):
            return 'ERR'

    lo = _parse('basic_salary_min')
    hi = _parse('basic_salary_max')
    if lo == 'ERR' or hi == 'ERR':
        return Response({'detail': 'Band values must be numbers.'}, status=http.HTTP_400_BAD_REQUEST)
    if lo is not None and hi is not None and lo > hi:
        return Response({'detail': 'The band minimum cannot be above the maximum.'},
                        status=http.HTTP_400_BAD_REQUEST)
    t.basic_salary_min = lo
    t.basic_salary_max = hi
    t.save(update_fields=['basic_salary_min', 'basic_salary_max', 'updated_at'])
    return Response(_tier_dict(t))


@api_view(['GET', 'POST'])
@permission_classes([IsAuthenticated])
def job_title_tiers(request):
    """GET: every distinct current job title with its mapped tier (for the
    tagging screen). POST: tag one title to a tier."""
    if request.method == 'GET':
        if not access.can_view(request.user):
            return Response({'detail': 'Not available to you.'}, status=http.HTTP_403_FORBIDDEN)
        # Distinct current titles from the master employee record + their counts.
        from payroll.models import Employee
        rows = (Employee.objects
                .exclude(job_title='')
                .values('job_title')
                .annotate(n=Count('id'))
                .order_by('-n', 'job_title'))
        mapped = {jt.title.strip().lower(): jt.tier.tier
                  for jt in JobTitleTier.objects.select_related('tier').all()}
        out = []
        for r in rows:
            title = r['job_title']
            out.append({'title': title, 'count': r['n'],
                        'tier': mapped.get(title.strip().lower())})
        return Response({'can_manage': can_manage_tiers(request.user), 'titles': out})

    # POST — tag a title.
    if not can_manage_tiers(request.user):
        return Response({'detail': 'Only Human Capital, the HR Business Partner or the CFO '
                                   'may tag titles.'},
                        status=http.HTTP_403_FORBIDDEN)
    d = request.data or {}
    title = (d.get('title') or '').strip()
    tier_no = d.get('tier')
    if not title:
        return Response({'detail': 'A job title is required.'}, status=http.HTTP_400_BAD_REQUEST)
    if tier_no in (None, '', 0, '0'):
        # Clear the mapping for this title.
        JobTitleTier.objects.filter(title__iexact=title).delete()
        return Response({'title': title, 'tier': None})
    t = PositionTier.objects.filter(tier=tier_no).first()
    if t is None:
        return Response({'detail': 'Unknown tier.'}, status=http.HTTP_400_BAD_REQUEST)
    existing = JobTitleTier.objects.filter(title__iexact=title).first()
    if existing:
        existing.title = title
        existing.tier = t
        existing.updated_by = request.user
        existing.save()
    else:
        JobTitleTier.objects.create(title=title, tier=t, updated_by=request.user)
    return Response({'title': title, 'tier': t.tier})


def tier_for_title(title: str):
    """The tier mapped to a job title (case-insensitive), or None. Shared helper
    so the create-authority flow can auto-suggest the tier from the position."""
    if not (title or '').strip():
        return None
    jt = (JobTitleTier.objects
          .select_related('tier')
          .annotate(_lt=Lower('title'))
          .filter(_lt=title.strip().lower())
          .first())
    return jt.tier if jt else None
