"""
procurement/phase_b_services.py — supporting services for Manus PO Audit
Phase-B (CFO directive 2026-05-20):

  * resolve_approver(user, role)  — out-of-office routing helper
  * request_amendment(po, payload, user) — versioned change order
  * apply_amendment(amendment, user) — write the new lines and bump version
  * link_capex_to_assets(grn, user) — auto-create assets.Asset rows when
                                      a GRN posts against a fixed_asset GL
  * expire_stale_pos(as_of=today)  — flag stale APPROVED POs as EXPIRED
"""

from __future__ import annotations

import logging
from datetime import date
from decimal import Decimal

from django.db import transaction
from django.utils import timezone


log = logging.getLogger(__name__)
ZERO = Decimal('0.00')


# ---------------------------------------------------------------------------
# Approval delegation
# ---------------------------------------------------------------------------

def resolve_approver(user, role: str):
    """Return the user who actually approves on `user`'s behalf today.

    role: 'fm' | 'cfo' | 'tier1_manager'

    If the user has an active ApprovalDelegate for that role covering
    today's date, returns the delegate. Otherwise returns `user` unchanged.
    """
    if user is None:
        return None
    from .models import ApprovalDelegate
    today = timezone.localdate()
    delegate = (ApprovalDelegate.objects
                .filter(delegator=user, role=role,
                        is_active=True,
                        starts_at__lte=today, ends_at__gte=today)
                .order_by('-starts_at')
                .first())
    return delegate.delegate if delegate else user


# ---------------------------------------------------------------------------
# Amendments
# ---------------------------------------------------------------------------

@transaction.atomic
def request_amendment(po, lines_payload, user, reason: str = ''):
    """
    Open a draft amendment on an APPROVED-or-later PO.
      po           — PurchaseOrder instance (must be approved / partially-received / fully-received)
      lines_payload — list of {description, quantity, unit_price, account_id?} dicts
      user         — requester
      reason       — string

    Persists a POAmendment row with a snapshot of the current lines in
    diff.before and the proposed payload in diff.after. Does NOT apply
    yet — call apply_amendment() once status flips to APPROVED.
    """
    from .models import PurchaseOrder, POAmendment
    if po.status not in (
        PurchaseOrder.Status.APPROVED,
        PurchaseOrder.Status.PARTIALLY_RECEIVED,
        PurchaseOrder.Status.FULLY_RECEIVED,
    ):
        raise ValueError(
            f'Amendments can only be raised on APPROVED-or-later POs. '
            f'Current status: {po.status}.'
        )
    from django.db.models import Max
    max_v = po.amendments.aggregate(m=Max('version'))['m'] or 1
    next_version = max_v + 1
    before = [
        {
            'id':           str(ln.id),
            'description':  ln.description,
            'quantity':     str(ln.quantity),
            'unit_price':   str(ln.unit_price),
            'line_total':   str(ln.line_total),
            'account_id':   str(ln.account_id) if ln.account_id else None,
        }
        for ln in po.lines.all()
    ]
    return POAmendment.objects.create(
        purchase_order=po,
        version=next_version,
        requested_by=user,
        status=POAmendment.Status.PENDING_APPROVAL,
        reason=reason[:500],
        diff={'before': before, 'after': lines_payload,
              'total_before_bwp': str(po.total_bwp)},
    )


@transaction.atomic
def apply_amendment(amendment, user):
    """Mark amendment APPROVED + rewrite the PO lines + bump version."""
    from .models import PurchaseOrder, PurchaseOrderLine, POAmendment
    po = amendment.purchase_order
    if amendment.status != POAmendment.Status.PENDING_APPROVAL:
        raise ValueError(
            f'Only PENDING_APPROVAL amendments can be applied (is {amendment.status}).'
        )
    # Fable audit 2026-07-08: this deletes + recreates PO lines. If any goods
    # have been received, GRN/match rows PROTECT the lines (delete → 500) and
    # the received-qty counters would be lost. Block the amendment in that case.
    if any((ln.quantity_received or 0) > 0 for ln in po.lines.all()):
        raise ValueError(
            'This PO already has goods received against it — its lines cannot '
            'be rewritten. Cancel the remaining quantity or raise a new PO.')
    payload = (amendment.diff or {}).get('after') or []
    # Snapshot existing lines if not already in diff.before
    if not (amendment.diff or {}).get('before'):
        amendment.diff = {
            **(amendment.diff or {}),
            'before': [
                {
                    'id':          str(ln.id),
                    'description': ln.description,
                    'quantity':    str(ln.quantity),
                    'unit_price':  str(ln.unit_price),
                    'account_id':  str(ln.account_id) if ln.account_id else None,
                }
                for ln in po.lines.all()
            ],
        }
    # Wipe + rewrite lines (preserve row order via sequence — Fable audit)
    po.lines.all().delete()
    for idx, row in enumerate(payload):
        PurchaseOrderLine.objects.create(
            purchase_order=po,
            sequence=idx,
            description=str(row.get('description') or '')[:500],
            quantity=Decimal(str(row.get('quantity') or 0)),
            unit_price=Decimal(str(row.get('unit_price') or 0)),
            account_id=row.get('account_id'),
        )
    po.amendment_version = (po.amendment_version or 1) + 1
    po.save(audit_user=user, audit_description=f'Amendment v{amendment.version} applied')
    amendment.status = POAmendment.Status.APPROVED
    amendment.approved_by = user
    amendment.applied_at = timezone.now()
    amendment.diff['total_after_bwp'] = str(po.total_bwp)
    amendment.save()
    return amendment


# ---------------------------------------------------------------------------
# Capex auto-link
# ---------------------------------------------------------------------------

def link_capex_to_assets(grn, user):
    """When a GRN posts against a fixed_asset GL line, auto-create an
    `assets.Asset` row keyed by the PO line so the audit trail PO→GRN→Asset
    survives.

    Idempotent on Asset.external_ref = "po:<po_id>:line:<line_id>".
    """
    try:
        from assets.models import Asset, AssetCategory
    except Exception:    # noqa: BLE001
        return []
    created = []
    po = grn.purchase_order
    default_cat = AssetCategory.objects.filter(is_active=True).first()
    for grn_line in grn.lines.select_related('po_line__account').all():
        acct = grn_line.po_line.account if grn_line.po_line_id else None
        if not acct:
            continue
        is_fixed = (
            acct.sub_type == 'fixed_asset'
            or 'fixed_asset' in (getattr(acct, 'sub_type', '') or '')
        )
        if not is_fixed:
            continue
        ref = f'po:{po.id}:line:{grn_line.po_line_id}'
        if Asset.objects.filter(external_ref=ref).exists():
            continue
        cost = Decimal(str(grn_line.po_line.unit_price or 0)) * Decimal(str(grn_line.quantity_received or 0))
        try:
            asset = Asset.objects.create(
                tag_number=f'AUTO-{po.po_number}-{str(grn_line.po_line_id)[:6]}',
                name=(grn_line.po_line.description or 'Auto from PO')[:200],
                company=po.company,
                category=default_cat,
                cost=cost,
                salvage_value=ZERO,
                opening_accumulated_depreciation=ZERO,
                purchase_date=grn.receipt_date or po.issue_date,
                in_service_date=grn.receipt_date or po.issue_date,
                useful_life_months=60,
                method='straight_line',
                status='in_service',
                external_ref=ref,
                created_by=user,
            )
            created.append(asset.id)
        except Exception as e:    # noqa: BLE001
            log.warning('link_capex_to_assets failed for grn=%s line=%s: %s',
                        grn.id, grn_line.po_line_id, e)
    return created


# ---------------------------------------------------------------------------
# PO expiry
# ---------------------------------------------------------------------------

def expire_stale_pos(as_of: date | None = None):
    """Flip APPROVED POs past valid_until + no activity in 90 days to EXPIRED.

    Returns list of PO ids transitioned.
    """
    from datetime import timedelta
    from .models import PurchaseOrder
    today = as_of or timezone.localdate()
    cutoff_inactive = today - timedelta(days=90)
    qs = PurchaseOrder.objects.filter(
        status=PurchaseOrder.Status.APPROVED,
        valid_until__isnull=False,
        valid_until__lt=today,
        updated_at__date__lt=cutoff_inactive,
    )
    flipped = []
    for po in qs:
        po.status = PurchaseOrder.Status.EXPIRED
        po.save(update_fields=['status', 'updated_at'])
        flipped.append(po.id)
    return flipped
