"""core/privacy_views.py — staff privacy-notice pop-up endpoints.

  GET  /api/v1/privacy-notice/       {version, title, html, acknowledged}
  POST /api/v1/privacy-notice/ack/   record the current user's acknowledgement

The pop-up (frontend PrivacyNoticeModal) calls GET on every dashboard load and
shows itself until `acknowledged` is true for the current NOTICE_VERSION. HR
sees who has NOT signed in the daily HRIS email (core.privacy_notice.
outstanding_users). CFO directive 2026-07-09.
"""
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response

from core import privacy_notice as pn


@api_view(["GET"])
@permission_classes([AllowAny])
def privacy_notices_public(request):
    """Public standalone privacy notices (DPA Part VIII; audit S-3/H-2). The
    policyholder notice must be reachable without login. ?audience=staff|policyholder
    returns one; otherwise both."""
    from core.privacy_notices import ALL_NOTICES, NOTICES_VERSION
    which = request.query_params.get("audience")
    if which in ALL_NOTICES:
        return Response({"notice": ALL_NOTICES[which], "version": NOTICES_VERSION})
    return Response({"notices": ALL_NOTICES, "version": NOTICES_VERSION})


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def privacy_notice(request):
    return Response({
        "version": pn.NOTICE_VERSION,
        "title": pn.NOTICE_TITLE,
        "html": pn.NOTICE_HTML,
        "checkbox_label": pn.ACK_CHECKBOX_LABEL,
        "cta_label": pn.ACK_CTA_LABEL,
        "acknowledged": pn.has_acknowledged(request.user),
    })


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def privacy_notice_ack(request):
    pn.record_ack(request.user)
    return Response({"acknowledged": True, "version": pn.NOTICE_VERSION})
