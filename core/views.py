import json
import logging
from html import escape as esc

from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST
from django.shortcuts import render
from .models import HelpdeskComment

log = logging.getLogger(__name__)

_PRI_COLORS = {
    "Critical": "#DC2626",
    "High":     "#F97316",
    "Medium":   "#2563EB",
    "Low":      "#6B7280",
}


@csrf_exempt
@require_POST
def helpdesk_notify(request):
    """
    POST /api/helpdesk/notify/
    Called by the Help Desk SPA after a ticket is created in SharePoint.
    Sends a branded HTML confirmation email from omni@alphadirect.co.bw.

    Body (JSON): ticket_id, title, priority, requester_email, requester_name,
                 category (opt), department (opt)

    Restricted to @alphadirect.co.bw recipients. No auth required — same-domain
    SPA only; domain restriction limits blast radius.

    SECURITY (2026-07-17 audit): this endpoint is unauthenticated, so it is
    per-IP rate-limited to blunt spam / internal-phishing abuse. Normal ticket
    creation is well under the limit.
    """
    from django.core.cache import cache
    ip = (request.META.get('HTTP_CF_CONNECTING_IP')
          or request.META.get('REMOTE_ADDR') or 'unknown')
    _rl_key = f'helpdesk_notify_rl:{ip}'
    _hits = cache.get(_rl_key, 0)
    if _hits >= 15:  # 15 sends per 10-minute window per IP
        return JsonResponse({"error": "rate limited, try again later"}, status=429)
    cache.set(_rl_key, _hits + 1, 600)

    try:
        data = json.loads(request.body)
    except (ValueError, TypeError):
        return JsonResponse({"error": "invalid JSON"}, status=400)

    for field in ("ticket_id", "title", "priority", "requester_email", "requester_name"):
        if not data.get(field):
            return JsonResponse({"error": f"missing field: {field}"}, status=400)

    email = data["requester_email"].strip().lower()
    if not email.endswith("@alphadirect.co.bw"):
        return JsonResponse({"error": "restricted to @alphadirect.co.bw"}, status=403)

    tid      = esc(data["ticket_id"])
    title    = esc(data["title"])
    priority = esc(data["priority"])
    category = esc(data.get("category") or "General")
    dept     = esc(data.get("department") or "—")
    name     = esc(data["requester_name"])
    pri_hex  = _PRI_COLORS.get(data["priority"], "#6B7280")

    html = f"""<div style="font-family:'Segoe UI',Arial,sans-serif;color:#1F2937;max-width:640px;">
  <div style="background:#1D3270;padding:20px 24px;border-radius:8px 8px 0 0;">
    <h2 style="color:#fff;margin:0;font-size:18px;">Alpha Direct IT Help Desk</h2>
    <p style="color:#94A3B8;margin:4px 0 0;font-size:13px;">Ticket Confirmation</p>
  </div>
  <div style="border:1px solid #E5E7EB;border-top:none;border-radius:0 0 8px 8px;padding:24px;">
    <p style="margin:0 0 16px;">Hi {name},</p>
    <p style="margin:0 0 20px;">Your IT support ticket has been logged. IT will respond within the SLA window below.</p>
    <div style="background:#F9FAFB;border:1px solid #E5E7EB;border-radius:8px;padding:20px;margin-bottom:20px;">
      <table style="width:100%;border-collapse:collapse;font-size:14px;">
        <tr>
          <td style="padding:6px 0;color:#6B7280;width:130px;">Ticket ID</td>
          <td style="padding:6px 0;">
            <strong style="background:#1D3270;color:#fff;padding:2px 8px;border-radius:4px;font-size:13px;">{tid}</strong>
          </td>
        </tr>
        <tr>
          <td style="padding:6px 0;color:#6B7280;">Title</td>
          <td style="padding:6px 0;"><strong>{title}</strong></td>
        </tr>
        <tr>
          <td style="padding:6px 0;color:#6B7280;">Category</td>
          <td style="padding:6px 0;">{category}</td>
        </tr>
        <tr>
          <td style="padding:6px 0;color:#6B7280;">Priority</td>
          <td style="padding:6px 0;">
            <span style="background:{pri_hex};color:#fff;padding:2px 10px;border-radius:12px;font-size:12px;font-weight:600;">{priority}</span>
          </td>
        </tr>
        <tr>
          <td style="padding:6px 0;color:#6B7280;">Department</td>
          <td style="padding:6px 0;">{dept}</td>
        </tr>
        <tr>
          <td style="padding:6px 0;color:#6B7280;vertical-align:top;">SLA</td>
          <td style="padding:6px 0;font-size:12px;color:#6B7280;">
            Critical 1h &nbsp;&middot;&nbsp; High 2h &nbsp;&middot;&nbsp; Medium 4h &nbsp;&middot;&nbsp; Low 8h (business hours)
          </td>
        </tr>
      </table>
    </div>
    <p style="text-align:center;margin:24px 0;">
      <a href="https://omni.alphadirect.co.bw/helpdesk/"
         style="background:#F47C20;color:#fff;padding:12px 24px;border-radius:6px;text-decoration:none;font-weight:600;font-size:14px;">
        View Your Tickets
      </a>
    </p>
    <p style="color:#6B7280;font-size:12px;margin:16px 0 0;border-top:1px solid #E5E7EB;padding-top:16px;">
      You will receive another email when IT picks up this ticket and when it is resolved.<br>
      Alpha Direct Insurance &middot; IT Help Desk
    </p>
  </div>
</div>"""

    from core.notifications import send_html_with_cfo_cc
    try:
        send_html_with_cfo_cc(
            subject=f"IT ticket {tid} logged — {title}",
            html=html,
            to=[email],
            text_fallback=(
                f"Your IT ticket {tid} has been logged. "
                f"Title: {data['title']}. Priority: {data['priority']}. "
                f"Track it at https://omni.alphadirect.co.bw/helpdesk/"
            ),
            cc_cfo=False,
        )
    except Exception as exc:
        log.warning("helpdesk_notify: send failed: %s", exc)
        return JsonResponse({"error": "send failed"}, status=500)

    return JsonResponse({"ok": True, "ticket_id": data["ticket_id"]})


def helpdesk_comments(request):
    """
    Read-only endpoint — returns Django-side comments for a given ticket ID.

    GET /api/helpdesk/comments/?ticket=TKT-0001
    No auth required: IT comments are operational, not financial or personal data.
    """
    ticket_id = request.GET.get("ticket", "").upper()
    if not ticket_id:
        return JsonResponse({"error": "ticket param required"}, status=400)
    comments = HelpdeskComment.objects.filter(ticket_id=ticket_id).values(
        "id", "author", "text", "created_at", "source"
    )
    return JsonResponse({"comments": list(comments)}, json_dumps_params={"default": str})
