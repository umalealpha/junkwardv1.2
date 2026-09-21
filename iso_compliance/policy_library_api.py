"""
iso_compliance/policy_library_api.py — Policy & Legal Framework Library.

Developer Build Brief (DPO Oratile, 2026-09-08): turn the DPO's 73-row DPA 2024
mapping into a live library so the ROPA module cites real requirements instead of
keyword guesses. Reads FrameworkRequirement + InternalPolicy + RequirementPolicyLink.

  GET  /api/v1/policy-library/            requirements (grouped) + policies + summary
  GET  /api/v1/policy-library/citation/?topic=<text>|?code=<S48.1>
                                          a requirement + its governing policies,
                                          for the ROPA module to cite
"""
from __future__ import annotations

from django.utils import timezone
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import FrameworkRequirement, InternalPolicy, RequirementPolicyLink

CAT_LABELS = dict(FrameworkRequirement.CATEGORY_CHOICES)
POL_LABELS = dict(InternalPolicy.STATUS_CHOICES)


def _links_for(req: FrameworkRequirement):
    out = []
    for lk in req.links.select_related('policy').all():
        out.append({
            'policy_id': str(lk.policy.policy_id),
            'policy_name': lk.policy.policy_name,
            'policy_status': lk.policy.status,
            'policy_status_label': POL_LABELS.get(lk.policy.status, lk.policy.status),
            'specific_section': lk.specific_section,
            'confirmed': lk.confirmed,
            'is_placeholder': lk.policy.is_placeholder,
            'is_gap': lk.policy.is_gap,
        })
    return out


def _req_row(req: FrameworkRequirement):
    return {
        'requirement_code': req.requirement_code,
        'requirement_name': req.requirement_name,
        'plain_description': req.plain_description,
        'category': req.category,
        'category_label': CAT_LABELS.get(req.category, req.category),
        'source_act': req.source_act,
        'policies': _links_for(req),
    }


class PolicyLibraryView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        reqs = list(
            FrameworkRequirement.objects.prefetch_related('links__policy').all()
        )
        policies = list(InternalPolicy.objects.all())
        links = RequirementPolicyLink.objects.count()

        by_cat = {}
        for r in reqs:
            by_cat.setdefault(r.category, 0)
            by_cat[r.category] += 1
        categories = [
            {'key': k, 'label': CAT_LABELS[k], 'count': by_cat.get(k, 0)}
            for k, _ in FrameworkRequirement.CATEGORY_CHOICES if by_cat.get(k)
        ]
        pol_status = {}
        for p in policies:
            pol_status[p.status] = pol_status.get(p.status, 0) + 1

        return Response({
            'as_of': timezone.localdate().isoformat(),
            'summary': {
                'requirements_total': len(reqs),
                'policies_total': len(policies),
                'policies_placeholder': sum(1 for p in policies if p.is_placeholder),
                'policies_gap': sum(1 for p in policies if p.is_gap),
                'links_total': links,
                'links_unconfirmed': RequirementPolicyLink.objects.filter(confirmed=False).count(),
                'policy_status_counts': pol_status,
                'policy_status_labels': POL_LABELS,
                'categories': categories,
            },
            'requirements': [_req_row(r) for r in reqs],
            'policies': [
                {
                    'policy_id': str(p.policy_id), 'policy_name': p.policy_name,
                    'status': p.status, 'status_label': POL_LABELS.get(p.status, p.status),
                    'version': p.version, 'document_link': p.document_link,
                    'last_reviewed': p.last_reviewed.isoformat() if p.last_reviewed else None,
                    'next_review_due': p.next_review_due.isoformat() if p.next_review_due else None,
                    'is_placeholder': p.is_placeholder, 'is_gap': p.is_gap, 'gap_note': p.gap_note,
                    'requirement_count': p.requirements.count(),
                }
                for p in policies
            ],
        })


class LegalCitationView(APIView):
    """For the ROPA module: return a requirement + its governing policies, by exact
    code (?code=S48.1) or best free-text topic match (?topic=cross-border)."""
    permission_classes = [IsAuthenticated]

    def get(self, request):
        code = (request.query_params.get('code') or '').strip()
        topic = (request.query_params.get('topic') or '').strip().lower()
        if code:
            req = FrameworkRequirement.objects.filter(requirement_code__iexact=code).first()
            if not req:
                return Response({'code': code, 'match': None})
            return Response({'code': code, 'match': _req_row(req)})
        if not topic:
            return Response({'detail': 'Pass ?code=<S48.1> or ?topic=<text>.'}, status=400)
        best, best_score = None, 0
        for r in FrameworkRequirement.objects.prefetch_related('links__policy').all():
            hay = f'{r.requirement_code} {r.requirement_name} {r.plain_description} {r.category}'.lower()
            score = sum(1 for w in topic.split() if w and w in hay)
            if score > best_score:
                best, best_score = r, score
        if not best or best_score == 0:
            return Response({'topic': topic, 'match': None,
                             'note': 'No matching requirement.'})
        return Response({'topic': topic, 'match': _req_row(best)})
