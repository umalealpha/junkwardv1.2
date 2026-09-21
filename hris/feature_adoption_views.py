"""hris/feature_adoption_views.py — the "I am happy with this feature" registry
+ per-person acceptance + team roll-up (CFO directive 2026-07-21).

The FEATURES list is the single source of truth for what the HR team must open
and accept. Adding a feature here makes it appear on the dashboard checklist and
requires a fresh acceptance from everyone.
"""
from __future__ import annotations

import os

from django.contrib.auth.models import User
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from .feature_adoption_models import FeatureAcceptance

# The HRIS features staff must review + accept. key = stable id; route = where the
# button lives (they MUST open the page to accept — that is the whole point).
FEATURES = [
    {'key': 'pulse',             'label': 'Pulse Check',       'route': '/hris/pulse',             'desc': 'Weekly staff mood, with the HR trend line.'},
    {'key': 'flight_risk',       'label': 'Flight-Risk Radar', 'route': '/hris/flight-risk',       'desc': 'Early warning on who might be about to leave.'},
    {'key': 'skills',            'label': 'Skills & Gaps Map', 'route': '/hris/skills',            'desc': 'Who has which skill; where the gaps are.'},
    {'key': 'okr_tree',          'label': 'OKR Alignment Tree','route': '/hris/okr-tree',          'desc': 'Company → department → individual goals.'},
    {'key': 'manager_scorecard', 'label': 'Manager Scorecard', 'route': '/hris/manager-scorecard', 'desc': 'Are managers doing their check-ins & dialogues.'},
]
_KEYS = {f['key'] for f in FEATURES}


def _default_roster() -> set[str]:
    """HR-team emails whose completion the leads watch. Env-overridable; the
    default is the HR head — extend via HRIS_ADOPTION_ROSTER_EMAILS."""
    env = os.environ.get('HRIS_ADOPTION_ROSTER_EMAILS', '')
    base = {e.strip().lower() for e in env.split(',') if e.strip()}
    base |= {'ubutale@alphadirect.co.bw'}
    return base


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def feature_adoption(request):
    """The caller's own checklist — every feature + whether they've accepted it."""
    done = dict(
        FeatureAcceptance.objects.filter(user=request.user)
        .values_list('feature_key', 'created_at'))
    feats = [{
        **f,
        'accepted': f['key'] in done,
        'accepted_at': done[f['key']].isoformat() if f['key'] in done else None,
    } for f in FEATURES]
    return Response({
        'features': feats,
        'done': sum(1 for f in feats if f['accepted']),
        'total': len(feats),
    })


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def accept_feature(request):
    """Record the caller's one-click 'I am happy with this feature'."""
    key = (request.data.get('key') or '').strip()
    if key not in _KEYS:
        return Response({'detail': 'Unknown feature.'}, status=400)
    obj, created = FeatureAcceptance.objects.get_or_create(
        user=request.user, feature_key=key)
    return Response({'ok': True, 'key': key,
                     'accepted_at': obj.created_at.isoformat(), 'newly': created})


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def feature_adoption_team(request):
    """Leads' oversight: who on the HR roster has finished (CFO can chase). Gated
    to the HRIS whitelist."""
    from hris.api_views import _deny_if_not_whitelisted
    deny = _deny_if_not_whitelisted(request)
    if deny:
        return deny
    roster = _default_roster()
    # Anyone on the configured roster OR who has accepted at least one feature.
    accepted_users = set(FeatureAcceptance.objects.values_list('user_id', flat=True))
    users = User.objects.filter(is_active=True).filter(
        models_q(roster, accepted_users))
    total = len(FEATURES)
    rows = []
    counts = {}
    for fa in FeatureAcceptance.objects.values('user_id', 'feature_key'):
        counts.setdefault(fa['user_id'], set()).add(fa['feature_key'])
    for u in users:
        done = len(counts.get(u.id, set()) & _KEYS)
        rows.append({
            'name': u.get_full_name() or u.email or u.username,
            'email': (u.email or '').lower(),
            'done': done, 'total': total,
            'complete': done >= total,
        })
    rows.sort(key=lambda r: (r['complete'], r['done']))
    return Response({'roster': rows, 'total': total})


def models_q(roster_emails, accepted_ids):
    """Users who are on the roster (by email) OR have already accepted something."""
    from django.db.models import Q
    q = Q(id__in=accepted_ids)
    if roster_emails:
        q |= Q(email__iregex=r'^(' + '|'.join(
            e.replace('.', r'\.').replace('+', r'\+') for e in roster_emails) + r')$')
    return q
