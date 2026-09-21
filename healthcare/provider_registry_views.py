"""healthcare/provider_registry_views.py — ADH service-provider registry API.

Gated by IsServiceProviderManager (ADH staff allowlist + superuser). Endpoints:

  GET   health/service-providers/            list + dashboard counts (search/filter)
  GET   health/service-providers/<pk>/       one provider (full detail)
  PATCH health/service-providers/<pk>/       QC-edit whitelisted fields / deactivate
  POST  health/service-providers/import/preview/   multipart xlsx -> classified preview
  POST  health/service-providers/import/commit/    JSON approved rows -> write
  GET   health/service-providers/export/?filter=all  filtered .xlsx report

Import is preview-then-commit; readiness is derived and never accepted from the
client; there are no hard deletes.
"""
from __future__ import annotations

from django.http import HttpResponse
from django.utils import timezone
from rest_framework import status
from rest_framework.parsers import MultiPartParser, FormParser, JSONParser
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.throttling import AnonRateThrottle
from rest_framework.views import APIView

from . import provider_registry as reg
from .models import (ProviderDashboardConfig, ServiceProvider,
                     ServiceProviderApplication)
from .permissions import IsServiceProviderManager


def _client_ip(request) -> str:
    xff = request.META.get("HTTP_X_FORWARDED_FOR", "")
    if xff:
        return xff.split(",")[0].strip()[:45]
    return (request.META.get("REMOTE_ADDR", "") or "")[:45]

_MAX_BYTES = 25 * 1024 * 1024

# Fields a QC/manager may edit by hand. Derived readiness (afa_registered,
# adh_ready, ready_mismatch) is a PROPERTY — unsettable by design. The manual
# 'adh_acceptance' column is maintained via import, not here.
_EDITABLE = {
    "qc_confirmed", "qc_date", "sticker_displayed", "welcome_pack",
    "provider_orientation", "comment", "contract_status", "onboarding_link",
    "date_contacted", "is_active",
}


def _serialize(p: ServiceProvider) -> dict:
    return {
        "id": str(p.id),
        "practice_number": p.practice_number,
        "name": p.name,
        "discipline": p.discipline,
        "town": p.town,
        "email": p.email,
        "contact_number": p.contact_number,
        "location": p.location,
        "contract_status": p.contract_status,
        "afa_registered": p.afa_registered,           # derived
        "welcome_pack": p.welcome_pack,
        "sticker_displayed": p.sticker_displayed,
        "provider_orientation": p.provider_orientation,
        "qc_confirmed": p.qc_confirmed,
        "qc_date": p.qc_date.isoformat() if p.qc_date else "",
        "adh_ready": p.adh_ready,                      # derived
        "adh_acceptance": p.adh_acceptance,            # their manual call
        "ready_mismatch": p.ready_mismatch,            # derived
        "vendor_system": p.vendor_system,
        "onboarding_link": p.onboarding_link,
        "date_contacted": p.date_contacted,
        "comment": p.comment,
        "is_active": p.is_active,
        "last_imported_at": p.last_imported_at.isoformat() if p.last_imported_at else "",
    }


class ServiceProviderListView(APIView):
    permission_classes = [IsServiceProviderManager]

    def get(self, request):
        qs = ServiceProvider.objects.all()
        if request.query_params.get("active_only", "1") == "1":
            qs = qs.filter(is_active=True)

        q = (request.query_params.get("q") or "").strip()
        if q:
            from django.db.models import Q
            qs = qs.filter(
                Q(name__icontains=q) | Q(practice_number__icontains=q)
                | Q(town__icontains=q) | Q(email__icontains=q)
            )
        discipline = (request.query_params.get("discipline") or "").strip()
        if discipline:
            qs = qs.filter(discipline__iexact=discipline)

        providers = list(qs)

        # Derived-field filters (can't be done in SQL — readiness is a property).
        rf = (request.query_params.get("readiness") or "").strip()
        if rf == "ready":
            providers = [p for p in providers if p.adh_ready]
        elif rf == "afa_registered":
            providers = [p for p in providers if p.afa_registered == "Yes"]
        elif rf == "pending":
            providers = [p for p in providers if p.afa_registered == "Pending"]
        elif rf == "registered_not_qc":
            providers = [p for p in providers if p.afa_registered == "Yes" and not p.qc_confirmed_flag]
        elif rf == "mismatch":
            providers = [p for p in providers if p.ready_mismatch]

        return Response({
            "counts": reg.dashboard_counts(),
            "results": [_serialize(p) for p in providers],
        })


class ServiceProviderDetailView(APIView):
    permission_classes = [IsServiceProviderManager]

    def get(self, request, pk):
        p = ServiceProvider.objects.filter(pk=pk).first()
        if not p:
            return Response({"detail": "Not found."}, status=status.HTTP_404_NOT_FOUND)
        return Response(_serialize(p))

    def patch(self, request, pk):
        p = ServiceProvider.objects.filter(pk=pk).first()
        if not p:
            return Response({"detail": "Not found."}, status=status.HTTP_404_NOT_FOUND)

        data = request.data or {}
        rejected = [k for k in data.keys() if k not in _EDITABLE]
        touched = {}
        for f in _EDITABLE:
            if f not in data:
                continue
            val = data[f]
            if f in {"qc_confirmed", "is_active"}:
                val = bool(val)
            elif f == "qc_date":
                val = val or None
            else:
                val = ("" if val is None else str(val)).strip()
            setattr(p, f, val)
            touched[f] = val

        if not touched:
            return Response(
                {"detail": "No editable fields supplied.", "rejected": rejected},
                status=status.HTTP_400_BAD_REQUEST,
            )

        p.save(
            audit_user=request.user,
            audit_ip=request.META.get("REMOTE_ADDR"),
            audit_description=f"QC edit: {', '.join(sorted(touched))}",
        )
        return Response({"ok": True, "provider": _serialize(p), "rejected": rejected})


class ServiceProviderImportPreviewView(APIView):
    permission_classes = [IsServiceProviderManager]
    parser_classes = [MultiPartParser, FormParser]

    def post(self, request):
        f = request.FILES.get("file")
        if not f:
            return Response({"detail": "No file uploaded (field 'file')."},
                            status=status.HTTP_400_BAD_REQUEST)
        if f.size > _MAX_BYTES:
            return Response({"detail": "File too large (max 25 MB)."},
                            status=status.HTTP_400_BAD_REQUEST)
        try:
            preview = reg.preview_import(f)
        except Exception as e:  # noqa: BLE001 — surface a clean message, never a 500
            return Response({"detail": f"Could not read the spreadsheet: {e}"},
                            status=status.HTTP_400_BAD_REQUEST)
        preview["source_file"] = f.name
        return Response(preview)


class ServiceProviderImportCommitView(APIView):
    permission_classes = [IsServiceProviderManager]
    parser_classes = [JSONParser]

    def post(self, request):
        rows = (request.data or {}).get("rows")
        source_file = (request.data or {}).get("source_file", "")
        if not isinstance(rows, list) or not rows:
            return Response({"detail": "No rows to import."},
                            status=status.HTTP_400_BAD_REQUEST)
        result = reg.commit_import(rows, source_file=source_file, user=request.user)
        return Response({"ok": True, **result, "counts": reg.dashboard_counts()})


class ServiceProviderExportView(APIView):
    permission_classes = [IsServiceProviderManager]

    def get(self, request):
        filter_key = (request.query_params.get("filter") or "all").strip()
        data, fname = reg.build_export(filter_key)
        resp = HttpResponse(
            data,
            content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
        resp["Content-Disposition"] = f'attachment; filename="{fname}"'
        return resp


# ---------------------------------------------------------------------------
# Public apply / onboarding link for new providers (no login).
# ---------------------------------------------------------------------------

class ProviderApplyThrottle(AnonRateThrottle):
    # DEFAULT_THROTTLE_RATES is unset project-wide, so anon throttles carry
    # their own rate (same pattern as VendorSignThrottle / SpeakerFeedbackThrottle).
    scope = "provider_apply"
    THROTTLE_RATES = {"provider_apply": "20/min"}


class ProviderApplyView(APIView):
    """PUBLIC: a new provider applies to join the ADH network. Creates a pending
    ServiceProviderApplication for the ADH team to review. No login."""
    permission_classes = [AllowAny]
    throttle_classes = [ProviderApplyThrottle]
    parser_classes = [JSONParser, FormParser, MultiPartParser]

    def post(self, request):
        d = request.data or {}
        name = (d.get("name") or "").strip()
        email = (d.get("email") or "").strip()
        contact = (d.get("contact_number") or "").strip()
        if not name:
            return Response({"detail": "Please give the practice / provider name."},
                            status=status.HTTP_400_BAD_REQUEST)
        if not email and not contact:
            return Response({"detail": "Please give an email or a contact number so we can reach you."},
                            status=status.HTTP_400_BAD_REQUEST)
        app = ServiceProviderApplication.objects.create(
            name=name[:255],
            discipline=(d.get("discipline") or "").strip()[:128],
            town=(d.get("town") or "").strip()[:128],
            contact_number=contact[:128],
            email=email[:255],
            practice_number=(d.get("practice_number") or "").strip()[:32],
            note=(d.get("note") or "").strip()[:2000],
            submitter_ip=_client_ip(request),
            submitter_user_agent=(request.META.get("HTTP_USER_AGENT", "") or "")[:1000],
        )
        return Response(
            {"ok": True, "id": str(app.id),
             "message": "Thank you. Your application has reached the Alpha Direct Health team. "
                        "We will be in touch."},
            status=status.HTTP_201_CREATED,
        )


def _serialize_app(a: ServiceProviderApplication) -> dict:
    return {
        "id": str(a.id), "name": a.name, "discipline": a.discipline, "town": a.town,
        "contact_number": a.contact_number, "email": a.email,
        "practice_number": a.practice_number, "note": a.note, "status": a.status,
        "review_note": a.review_note,
        "created_at": a.created_at.isoformat() if a.created_at else "",
        "reviewed_at": a.reviewed_at.isoformat() if a.reviewed_at else "",
    }


class ProviderApplicationListView(APIView):
    permission_classes = [IsServiceProviderManager]

    def get(self, request):
        qs = ServiceProviderApplication.objects.all()
        st = (request.query_params.get("status") or "").strip()
        if st:
            qs = qs.filter(status=st)
        # pending first, then newest
        apps = sorted(qs, key=lambda a: (a.status != "pending", -(a.created_at.timestamp() if a.created_at else 0)))
        return Response({"results": [_serialize_app(a) for a in apps]})


class ProviderApplicationDetailView(APIView):
    permission_classes = [IsServiceProviderManager]

    def patch(self, request, pk):
        a = ServiceProviderApplication.objects.filter(pk=pk).first()
        if not a:
            return Response({"detail": "Not found."}, status=status.HTTP_404_NOT_FOUND)
        new_status = (request.data or {}).get("status", "").strip()
        if new_status not in {"pending", "accepted", "declined"}:
            return Response({"detail": "status must be pending, accepted or declined."},
                            status=status.HTTP_400_BAD_REQUEST)
        a.status = new_status
        a.review_note = ((request.data or {}).get("review_note", "") or "").strip()[:255]
        a.reviewed_by = request.user
        a.reviewed_at = timezone.now()
        a.save()
        return Response({"ok": True, "application": _serialize_app(a)})


class ProviderDashboardToggleView(APIView):
    """The evening exec-dashboard ON/OFF switch. The ADH team turns it ON once
    the latest provider file is imported, so the 6pm send only goes out on fresh
    data. OFF by default."""
    permission_classes = [IsServiceProviderManager]

    def _payload(self, cfg):
        last = (ServiceProvider.objects.filter(last_imported_at__isnull=False)
                .order_by("-last_imported_at").values_list("last_imported_at", flat=True).first())
        return {
            "enabled": cfg.enabled,
            "updated_at": cfg.updated_at.isoformat() if cfg.updated_at else "",
            "updated_by": (getattr(cfg.updated_by, "get_full_name", lambda: "")()
                           or getattr(cfg.updated_by, "username", "") or ""),
            "last_import_at": last.isoformat() if last else "",
        }

    def get(self, request):
        return Response(self._payload(ProviderDashboardConfig.current()))

    def post(self, request):
        cfg = ProviderDashboardConfig.current()
        cfg.enabled = bool((request.data or {}).get("enabled"))
        cfg.updated_by = request.user
        cfg.save()
        return Response({"ok": True, **self._payload(cfg)})


class ProviderTrendView(APIView):
    """GET /health/service-providers/trend/ — daily snapshots for the growth chart.
    Returns the last 90 days by default; ?days=N overrides.

    Gated the same as every other registry view: the counts are the registry's own
    figures, so IsAuthenticated would have let any signed-in staff member read the
    ADH network's position.
    """
    permission_classes = [IsServiceProviderManager]

    def get(self, request):
        from healthcare.models import ProviderDailySnapshot
        try:
            days = max(1, min(int(request.query_params.get("days", 90)), 365))
        except (ValueError, TypeError):
            return Response({"detail": "days must be an integer"}, status=400)
        cutoff = timezone.localdate() - timezone.timedelta(days=days)
        rows = (ProviderDailySnapshot.objects
                .filter(date__gte=cutoff)
                .order_by("date")
                .values("date", "total", "afa_registered", "afa_pending",
                        "adh_ready", "qc_confirmed", "mismatches",
                        "pending_applications"))
        return Response({
            "days": days,
            "snapshots": [
                {**r, "date": r["date"].isoformat()} for r in rows
            ],
        })
