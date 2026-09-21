"""
reporting/finance_monitoring_views.py

API for Keetile's six Finance monitoring reports. Read-only, financially
permissioned, no side effects.

  GET /api/v1/finance-monitoring/                 → the six, with cadence + owner
  GET /api/v1/finance-monitoring/<slug>/          → one report (rows/summary/meta)
  GET /api/v1/finance-monitoring/<slug>/?download=csv → the same, as a file
"""

from __future__ import annotations

import csv
import datetime as _dt
import logging

from django.http import HttpResponse
from rest_framework import status as http
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from core.permissions import CanViewFinancials
from reporting.finance_monitoring import REPORTS, ReplicaUnavailable

log = logging.getLogger(__name__)

#: Only these may be passed through to a builder, and each is coerced below.
#: An unknown query parameter is ignored rather than forwarded — a builder is a
#: SQL surface and must never take arbitrary keyword arguments from a URL.
_INT_PARAMS = ('limit', 'days_ahead')
_DATE_PARAMS = ('date_from', 'date_to')


def _parse_date(raw):
    try:
        return _dt.date.fromisoformat(str(raw)[:10])
    except (TypeError, ValueError):
        return None


def _builder_kwargs(request, builder) -> dict:
    """Query parameters the requested builder actually accepts, coerced."""
    import inspect
    accepted = set(inspect.signature(builder).parameters)
    out = {}
    for key in _DATE_PARAMS:
        if key in accepted and request.GET.get(key):
            val = _parse_date(request.GET.get(key))
            if val:
                out[key] = val
    for key in _INT_PARAMS:
        if key in accepted and request.GET.get(key):
            try:
                out[key] = max(1, min(int(request.GET[key]), 5000))
            except (TypeError, ValueError):
                pass
    return out


@api_view(['GET'])
@permission_classes([IsAuthenticated, CanViewFinancials])
def finance_monitoring_index(request):
    """The six reports, so the screen does not hard-code them."""
    return Response({'reports': [
        {'slug': slug, 'title': cfg['title'], 'cadence': cfg['cadence'],
         'owner': cfg['owner']}
        for slug, cfg in REPORTS.items()
    ]})


@api_view(['GET'])
@permission_classes([IsAuthenticated, CanViewFinancials])
def finance_monitoring_detail(request, slug: str):
    cfg = REPORTS.get(slug)
    if not cfg:
        return Response({'detail': 'No such report.'}, status=http.HTTP_404_NOT_FOUND)

    try:
        result = cfg['builder'](**_builder_kwargs(request, cfg['builder']))
    except ReplicaUnavailable as exc:
        # 503, not 200-with-no-rows. These reports answer "what is wrong out
        # there", so an empty result reads as all-clear; a dead connection must
        # never be able to look like a clean bill of health.
        return Response({'detail': str(exc), 'report': slug},
                        status=http.HTTP_503_SERVICE_UNAVAILABLE)

    # `download`, NOT `format`. `?format=` is reserved by DRF's content
    # negotiation: it looks for a renderer of that name, finds none, and answers
    # 404 "Not found" — so the JSON endpoint returned 200 while the download
    # button beside it failed. Caught by hitting the live URL, not by a test.
    if request.GET.get('download') == 'csv':
        return _csv_response(slug, result)
    return Response({'slug': slug, 'title': cfg['title'],
                     'cadence': cfg['cadence'], **result})


def _csv_response(slug: str, result: dict) -> HttpResponse:
    rows = result.get('rows') or []
    resp = HttpResponse(content_type='text/csv')
    resp['Content-Disposition'] = f'attachment; filename="{slug}.csv"'
    if not rows:
        resp.write('No rows\n')
        return resp
    writer = csv.DictWriter(resp, fieldnames=list(rows[0].keys()),
                            extrasaction='ignore')
    writer.writeheader()
    for r in rows:
        writer.writerow({k: _csv_safe(v) for k, v in r.items()})
    return resp


def _csv_safe(v):
    """Neutralise a value Excel would otherwise execute as a formula."""
    s = '' if v is None else str(v)
    return "'" + s if s[:1] in ('=', '+', '-', '@') else s
