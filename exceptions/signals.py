"""
exceptions/signals.py

Auto-create exceptions when other apps' models reach interesting states.

The handlers are wrapped in defensive try/except blocks so the exception
app loads cleanly on a system where the source models have not yet shipped
(e.g. before PRs #20 / #23 land on main). When the model exists, signals
fire; otherwise the connect call fails silently and is skipped.
"""

from __future__ import annotations

import logging

from django.db.models.signals import post_save
from django.dispatch import receiver

from .models import Exception as ExceptionModel
from .services import create_exception


log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# POBillMatch — fire when an auto-match lands in NEEDS_TIER1 / NEEDS_TIER2
# ---------------------------------------------------------------------------

def _on_po_bill_match_save(sender, instance, created, **_kw):  # noqa: ANN001
    """Auto-raise an exception when a match is awaiting a human decision."""
    PENDING = {
        'needs_tier1_approval',
        'needs_tier2_approval',
        'tier1_approved',  # tier-2 still pending
        'variance_quantity',
        'variance_price',
        'variance_both',
    }
    status = getattr(instance, 'match_status', None)
    if status not in PENDING:
        return

    bill = getattr(instance, 'bill', None)
    po   = getattr(instance, 'purchase_order', None)
    if not (bill and po):
        return

    # variance_pct may not exist on older POBillMatch versions
    variance_pct = getattr(instance, 'variance_pct', None)
    variance_str = f' ({variance_pct}%)' if variance_pct else ''

    severity = (
        ExceptionModel.Severity.HIGH
        if status in {'needs_tier2_approval', 'tier1_approved'}
        else ExceptionModel.Severity.MEDIUM
    )
    requires_role = (
        ExceptionModel.RequiresRole.FINANCE_MANAGER
        if status in {'needs_tier2_approval', 'tier1_approved'}
        else ExceptionModel.RequiresRole.DEPT_MANAGER
    )

    try:
        create_exception(
            exception_type=ExceptionModel.Type.PO_BILL_MISMATCH,
            severity=severity,
            requires_role=requires_role,
            title=f'PO ↔ Bill variance: {bill.invoice_number} vs {po.po_number}{variance_str}',
            description=(
                f'Bill {bill.invoice_number} (total {bill.currency_code_id} '
                f'{bill.total_amount}) does not match PO {po.po_number} '
                f'(total {po.currency_code_id} {po.total_amount}). '
                f'Match status: {status}. Resolve via the bill ↔ PO match '
                f'page to clear this exception.'
            ),
            source_app='procurement',
            source_model='POBillMatch',
            source_id=str(instance.pk),
            source_label=f'{bill.invoice_number} ↔ {po.po_number}',
            metadata={
                'po_number':       po.po_number,
                'bill_number':     bill.invoice_number,
                'match_status':    status,
                'variance_pct':    str(variance_pct) if variance_pct is not None else None,
                'po_total':        str(po.total_amount),
                'bill_total':      str(bill.total_amount),
                'po_department':   getattr(po, 'department', ''),
            },
        )
    except Exception as e:  # noqa: BLE001
        log.warning('Could not raise exception for POBillMatch %s: %s', instance.pk, e)


# ---------------------------------------------------------------------------
# VendorBankAccount — fire on every banking-detail change requiring approval
# ---------------------------------------------------------------------------

def _on_vendor_bank_account_save(sender, instance, created, **_kw):  # noqa: ANN001
    """
    Banking-detail changes are the highest-risk vendor fraud vector. Every
    new DRAFT or PENDING_APPROVAL row gets an exception that ONLY the FM
    or CFO can clear (the maker-checker workflow). Once the row goes ACTIVE,
    the linked exception can be auto-resolved.
    """
    status = getattr(instance, 'status', None)
    contact = getattr(instance, 'contact', None)
    if not contact:
        return

    if status in ('draft', 'pending_approval'):
        try:
            create_exception(
                exception_type=ExceptionModel.Type.BANKING_CHANGE,
                severity=ExceptionModel.Severity.HIGH,
                requires_role=ExceptionModel.RequiresRole.FINANCE_MANAGER,
                title=f'Banking detail change: {contact.name}',
                description=(
                    f'A bank account record for {contact.name} is in '
                    f'{status} state. Banking-detail changes must be '
                    f'cleared by the Finance Manager or CFO before any '
                    f'outbound payment can use this account. Verify against '
                    f'the bank confirmation letter; reject if the holder '
                    f'name does not match the vendor name.'
                ),
                source_app='procurement',
                source_model='VendorBankAccount',
                source_id=str(instance.pk),
                source_label=f'{contact.name} — {getattr(instance, "bank_name", "")}',
                metadata={
                    'vendor_name':         contact.name,
                    'bank_name':           getattr(instance, 'bank_name', ''),
                    'account_holder_name': getattr(instance, 'account_holder_name', ''),
                    'currency_code':       str(getattr(instance, 'currency_code_id', '')),
                    'name_mismatch':       getattr(instance, 'name_mismatch', False),
                    'status':              status,
                },
            )
        except Exception as e:  # noqa: BLE001
            log.warning('Could not raise BANKING_CHANGE exception for %s: %s',
                        instance.pk, e)
    elif status == 'active':
        # Auto-resolve any open banking exception for this record
        ExceptionModel.objects.filter(
            source_app='procurement',
            source_model='VendorBankAccount',
            source_id=str(instance.pk),
            status__in=[ExceptionModel.Status.OPEN,
                        ExceptionModel.Status.ACKNOWLEDGED,
                        ExceptionModel.Status.IN_PROGRESS],
        ).update(
            status=ExceptionModel.Status.RESOLVED,
            resolved_at=__import__('django.utils.timezone',
                                    fromlist=['now']).now(),
            resolution_notes=(
                'Auto-resolved: vendor bank account moved to ACTIVE '
                'after FM/CFO approval.'
            ),
        )


# ---------------------------------------------------------------------------
# BillAIVerification — escalate AI-detected anomalies and fraud cues
# ---------------------------------------------------------------------------

def _on_ai_verification_save(sender, instance, created, **_kw):  # noqa: ANN001
    if not created:
        return
    verdict = getattr(instance, 'verdict', None)
    if verdict not in ('anomaly', 'fraud_cue'):
        return

    bill = getattr(instance, 'bill', None)
    po   = getattr(instance, 'po', None)
    if not (bill and po):
        return

    severity = (ExceptionModel.Severity.CRITICAL if verdict == 'fraud_cue'
                else ExceptionModel.Severity.HIGH)
    requires_role = (ExceptionModel.RequiresRole.CFO if verdict == 'fraud_cue'
                     else ExceptionModel.RequiresRole.FINANCE_MANAGER)
    exc_type = (ExceptionModel.Type.AI_FRAUD_CUE if verdict == 'fraud_cue'
                else ExceptionModel.Type.AI_ANOMALY)

    try:
        create_exception(
            exception_type=exc_type,
            severity=severity,
            requires_role=requires_role,
            title=f'AI {verdict}: {bill.invoice_number} vs {po.po_number}',
            description=(
                f'Aria flagged this bill ↔ PO comparison. '
                f'Notes: {getattr(instance, "notes", "")[:200]}. '
                f'Confidence: {getattr(instance, "confidence", 0)}%. '
                f'Flags: {", ".join(getattr(instance, "flags", []) or [])}.'
            ),
            source_app='procurement',
            source_model='BillAIVerification',
            source_id=str(instance.pk),
            source_label=f'AI {verdict}: {bill.invoice_number}',
            metadata={
                'verdict':       verdict,
                'flags':         list(getattr(instance, 'flags', []) or []),
                'confidence':    int(getattr(instance, 'confidence', 0) or 0),
                'po_number':     po.po_number,
                'bill_number':   bill.invoice_number,
            },
        )
    except Exception as e:  # noqa: BLE001
        log.warning('Could not raise AI_VERDICT exception for %s: %s',
                    instance.pk, e)


# ---------------------------------------------------------------------------
# Registration — defensive against missing models
# ---------------------------------------------------------------------------

def register_signals() -> None:
    """Connect post_save handlers to whichever models exist on this branch."""
    from django.apps import apps as django_apps

    def _try_connect(app_label, model_name, handler):
        try:
            model = django_apps.get_model(app_label, model_name)
        except LookupError:
            return False
        post_save.connect(handler, sender=model,
                          dispatch_uid=f'exceptions:{app_label}.{model_name}')
        return True

    _try_connect('procurement', 'POBillMatch', _on_po_bill_match_save)
    _try_connect('procurement', 'VendorBankAccount', _on_vendor_bank_account_save)
    _try_connect('procurement', 'BillAIVerification', _on_ai_verification_save)
