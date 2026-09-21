"""
core/notifications_view.py — pending-approval inbox for the logged-in user.

CFO directive 2026-05-21: every pending approval must be visible. A
silent red dot on the bell isn't enough. This endpoint feeds the
notification center (bell dropdown + sticky banner) with a consolidated
list of every item awaiting THIS user's review.

GET /api/v1/notifications/pending/

Response:
    {
      "count": 7,
      "by_type": {"purchase_order": 4, "journal_entry": 2, "leave": 1},
      "items": [
        {
          "type": "purchase_order",
          "id":   "<uuid>",
          "title": "PO PO-ADM-2026-000007 — 108 Media Pty Ltd",
          "subtitle": "Pending FM Approval · BWP 2,000.00",
          "amount": "2000.00",
          "currency": "BWP",
          "url": "/purchase-orders/<uuid>",
          "raised_by": "pganesharajah",
          "raised_at": "2026-05-21T14:13:00Z"
        },
        ...
      ]
    }
"""
from __future__ import annotations

from datetime import datetime, timedelta

from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView


def _po_awaits_user(user, po) -> bool:
    """True if THIS user is the intended approver for the PO's CURRENT leg, so
    it belongs in their task inbox. Backstop authority (CFO / superuser may
    approve anything) does NOT flood the inbox with every PO — only the leg's
    routed approver sees it here (CFO directive 2026-07-09):

        PENDING_CFO_APPROVAL         -> CFO
        PENDING_FM_APPROVAL, claims  -> Claims Manager / Team Leader / Senior Claims Associate
        PENDING_FM_APPROVAL, other   -> Finance Manager / Financial Controller

    Claims POs are single-step (approved by claims seniors, no CFO leg), so the
    CFO/FM must NOT see them in their inbox — only claims seniors do.
    """
    from core.models import get_user_profile, UserProfile
    from procurement.models import PurchaseOrder

    profile = get_user_profile(user)
    if profile is None or not getattr(profile, 'is_active', False):
        return False
    title = profile.title
    if po.status == PurchaseOrder.Status.PENDING_CFO_APPROVAL:
        return title == UserProfile.Title.CFO
    if po.department == PurchaseOrder.Department.CLAIMS:
        return title in {
            UserProfile.Title.CLAIMS_MANAGER,
            UserProfile.Title.CLAIMS_TEAM_LEADER,
            UserProfile.Title.SENIOR_CLAIMS_ASSOCIATE,
        }
    return title in {
        UserProfile.Title.FINANCE_MANAGER,
        UserProfile.Title.FINANCIAL_CONTROLLER,
    }


def _po_items(user, limit=50):
    """POs whose CURRENT approval leg is THIS user's to action (see
    _po_awaits_user). The inbox is a task list, not an audit view: claims
    seniors see their (single-step) claims POs, Finance sees operational
    FM-leg POs, and the CFO sees only the final CFO leg — he is no longer
    shown claims/operational POs he is merely a backstop for.
    CFO directive 2026-07-09."""
    from procurement.models import PurchaseOrder

    qs = (PurchaseOrder.objects
          .filter(status__in=[
              PurchaseOrder.Status.PENDING_FM_APPROVAL,
              PurchaseOrder.Status.PENDING_CFO_APPROVAL,
          ])
          .select_related('supplier', 'company', 'created_by')
          .order_by('-submitted_at', '-created_at'))

    out = []
    for po in qs:
        if not _po_awaits_user(user, po):
            continue
        out.append({
            'type':      'purchase_order',
            'id':        str(po.id),
            'title':     f"PO {po.po_number} — {po.supplier.name}",
            'subtitle':  f"{po.status_display_label} · "
                         f"{(po.currency_code_id or 'BWP')} "
                         f"{po.total_amount:,.2f}",
            'amount':    str(po.total_amount or 0),
            'currency':  po.currency_code_id or 'BWP',
            'url':       f"/purchase-orders/{po.id}",
            'raised_by': (getattr(po.created_by, 'username', '') or '—'),
            'raised_at': (po.submitted_at.isoformat()
                          if po.submitted_at else
                          (po.created_at.isoformat() if po.created_at else None)),
        })
        if len(out) >= limit:
            break
    return out


def _je_items(user, limit=50):
    """Journal entries pending approval."""
    from ledger.models import JournalEntry

    qs = (JournalEntry.objects
          .filter(status=JournalEntry.Status.PENDING_APPROVAL)
          .select_related('created_by', 'company')
          .order_by('-submitted_at', '-created_at'))

    out = []
    for je in qs[:limit]:
        out.append({
            'type':      'journal_entry',
            'id':        str(je.id),
            'title':     f"JE {je.entry_number} — {je.description[:60]}",
            'subtitle':  f"Pending approval · {je.entry_date}",
            'amount':    None,
            'currency':  je.currency_code_id or 'BWP',
            'url':       f"/journal-entries/{je.id}",
            'raised_by': (getattr(je.created_by, 'username', '') or '—'),
            'raised_at': (je.submitted_at.isoformat()
                          if je.submitted_at else
                          (je.created_at.isoformat() if je.created_at else None)),
        })
    return out


def _leave_items(user, limit=50):
    """HRIS leave requests pending manager approval."""
    try:
        from hris.models import LeaveRequest
    except Exception:    # noqa: BLE001
        return []

    qs = (LeaveRequest.objects
          .filter(status='pending')
          .select_related('employee')
          .order_by('-created_at'))

    out = []
    for lr in qs[:limit]:
        emp_name = getattr(lr.employee, 'full_name', None) or \
                   getattr(lr.employee, 'name', None) or '—'
        out.append({
            'type':      'leave',
            'id':        str(lr.id),
            'title':     f"Leave: {emp_name}",
            'subtitle':  (f"{lr.start_date} → {lr.end_date} · "
                          f"{(lr.leave_type or '').replace('_', ' ').title()}"),
            'amount':    None,
            'currency':  None,
            'url':       f"/hris/inbox",
            'raised_by': emp_name,
            'raised_at': (lr.created_at.isoformat() if lr.created_at else None),
        })
    return out


def _import_batch_items(user, limit=50):
    """Asset / claim recovery / payroll import batches pending approval."""
    try:
        from assets.models import AssetImportBatch
    except Exception:    # noqa: BLE001
        return []
    qs = AssetImportBatch.objects.filter(status='pending_approval') \
        .order_by('-created_at')
    out = []
    for b in qs[:limit]:
        out.append({
            'type':      'asset_import',
            'id':        str(b.id),
            'title':     f"Asset import batch · {b.filename or '—'}",
            'subtitle':  f"Pending approval · {b.row_count or '?'} rows",
            'amount':    None,
            'currency':  None,
            'url':       f"/approvals",
            'raised_by': getattr(b.uploaded_by, 'username', '') if b.uploaded_by_id else '—',
            'raised_at': b.created_at.isoformat() if b.created_at else None,
        })
    return out


class PendingNotificationsView(APIView):
    """Consolidated 'awaiting action' inbox feed."""
    permission_classes = [IsAuthenticated]

    def get(self, request):
        user = request.user

        items = []
        # Each fetch is wrapped so one bad source doesn't kill the rest.
        for fetcher in (_po_items, _je_items, _leave_items, _import_batch_items):
            try:
                items.extend(fetcher(user))
            except Exception:        # noqa: BLE001
                continue

        by_type: dict[str, int] = {}
        for it in items:
            by_type[it['type']] = by_type.get(it['type'], 0) + 1

        return Response({
            'count':   len(items),
            'by_type': by_type,
            'items':   items[:80],
        })
