"""
integrations/services.py

EventProcessor: turns incoming Graphite events into Finance records.

Supported event types
─────────────────────
policy_issued          → customer invoice  (premium + VAT)
claim_approved         → vendor bill       (claims payable)
commission_calculated  → vendor bill       (broker commission)
policy_cancelled       → credit note       (negative customer invoice)
"""

from __future__ import annotations

import logging
from datetime import date, timedelta
from decimal import Decimal

from django.db import transaction
from django.utils import timezone

from django.contrib.auth.models import User

from billing.models import Contact, Invoice, InvoiceLine
from core.models import TaxRate
from ledger.models import Account

from .models import IntegrationEvent

log = logging.getLogger(__name__)

ZERO = Decimal('0')


# ---------------------------------------------------------------------------
# System user for integration-generated records
# ---------------------------------------------------------------------------

def _system_user() -> User:
    """Returns the first superuser, used as created_by for system-generated records."""
    user = User.objects.filter(is_superuser=True).order_by('date_joined').first()
    if user is None:
        raise RuntimeError(
            "No superuser exists. Run 'python manage.py setup_initial_data' first."
        )
    return user


# ---------------------------------------------------------------------------
# Account lookups (soft — returns None rather than raising)
# ---------------------------------------------------------------------------

def _account(code: str) -> Account | None:
    return Account.objects.filter(code=code, is_active=True).first()


# ---------------------------------------------------------------------------
# Contact resolution: find by graphite_id, or create stub
# ---------------------------------------------------------------------------

def _resolve_contact(
    graphite_id: str,
    name: str,
    contact_type: str = 'customer',
) -> Contact:
    contact = Contact.objects.filter(graphite_id=graphite_id).first()
    if contact:
        return contact
    contact = Contact.objects.create(
        graphite_id=graphite_id,
        name=name,
        contact_type=contact_type,
        currency_code_id='BWP',
        payment_terms_days=30,
        is_resident=True,
        wht_exempt=(contact_type == 'customer'),
    )
    log.info('Created stub contact %s (%s)', name, graphite_id)
    return contact


# ---------------------------------------------------------------------------
# Tax rate helper
# ---------------------------------------------------------------------------

def _tax_rate(code: str) -> TaxRate | None:
    return TaxRate.objects.filter(tax_code=code, is_active=True).first()


# ---------------------------------------------------------------------------
# EventProcessor
# ---------------------------------------------------------------------------

class EventProcessor:
    """
    Processes a single IntegrationEvent and updates its status in place.
    Call ``run(event)`` — returns the updated event.
    """

    #: The claim-lifecycle names Graphite fires. Omni does not process these
    #: here — the claims machine in claims_automation already owns the claim
    #: lifecycle, so this door hands them straight over (CFO board item B2).
    CLAIM_LIFECYCLE_TYPES = frozenset({
        IntegrationEvent.EventType.CLAIM_REGISTERED,
        IntegrationEvent.EventType.FORM_SUBMITTED,
        IntegrationEvent.EventType.PREMIUM_CHECKED,
        IntegrationEvent.EventType.ASSESSMENT_RECEIVED,
        IntegrationEvent.EventType.WRITE_OFF_FLAGGED,
        IntegrationEvent.EventType.DECISION_RECORDED,
    })

    def run(self, event: IntegrationEvent) -> IntegrationEvent:
        # A replayed event must not fire its rule a second time. The door is
        # idempotent on the sender's own key, but the same stored row can be
        # handed to the processor again by a retry or a sweep.
        if event.status == IntegrationEvent.Status.PROCESSED:
            return event

        self._not_acted = None

        event.status = IntegrationEvent.Status.PROCESSING
        event.save(update_fields=['status'])

        try:
            with transaction.atomic():
                result_type, result_obj = self._dispatch(event)
            not_acted = getattr(self, '_not_acted', None)
            event.status = (
                IntegrationEvent.Status.SKIPPED if not_acted
                else IntegrationEvent.Status.PROCESSED
            )
            event.result_type = result_type
            event.result_id   = result_obj.id if result_obj is not None else None
            event.processed_at = timezone.now()
            # Keep the handler's plain-words note. Blanking it here is how
            # "stored, not acted on" became a silent success.
            event.error_message = not_acted
        except Exception as exc:
            log.exception('Failed to process event %s', event.id)
            event.status        = IntegrationEvent.Status.FAILED
            event.error_message = str(exc)
            event.retry_count   += 1

        event.save(update_fields=[
            'status', 'result_type', 'result_id',
            'processed_at', 'error_message', 'retry_count',
        ])
        return event

    # ------------------------------------------------------------------
    # Dispatcher
    # ------------------------------------------------------------------

    def _dispatch(self, event: IntegrationEvent):
        handlers = {
            IntegrationEvent.EventType.POLICY_ISSUED:         self._policy_issued,
            IntegrationEvent.EventType.CLAIM_APPROVED:        self._claim_approved,
            IntegrationEvent.EventType.COMMISSION_CALCULATED:  self._commission_calculated,
            IntegrationEvent.EventType.POLICY_CANCELLED:      self._policy_cancelled,
            IntegrationEvent.EventType.BUS_PING:              self._bus_ping,
        }
        if event.event_type in self.CLAIM_LIFECYCLE_TYPES:
            return self._claim_lifecycle(event)
        handler = handlers.get(event.event_type)
        if not handler:
            event.status = IntegrationEvent.Status.SKIPPED
            event.save(update_fields=['status'])
            raise ValueError(f"No handler for event type: {event.event_type}")
        return handler(event.event_data)

    # ------------------------------------------------------------------
    # the six claim-lifecycle names → the claims machine
    # ------------------------------------------------------------------

    def _claim_lifecycle(self, event: IntegrationEvent):
        """Hand a claim event to claims_automation and record that we did.

        Nothing is decided here and nothing moves money: claims_automation
        writes notifications, tasks and drafts, every one of them behind its
        own switch.
        """
        from django.conf import settings as dj

        if not getattr(dj, 'CLAIMS_LIFECYCLE_FORWARD_EVENTS', False):
            # Stored, not acted on. B14 — every piece behind its own switch, and
            # nothing is switched on until it is armed. The event is safe on
            # disk and can be replayed once the switch is flipped. SKIPPED is
            # the honest status: 'processed' would claim we did something.
            self._not_acted = (
                'stored, not acted on: CLAIMS_LIFECYCLE_FORWARD_EVENTS is off'
            )
            return None, None

        from claims_automation import processor

        data = dict(event.event_data or {})
        # Graphite's spec says "form submitted"; the claims machine has always
        # called the same thing claim_form_submitted. Translate at the door
        # rather than renaming a live enum underneath it.
        etype = {'form_submitted': 'claim_form_submitted'}.get(
            event.event_type, event.event_type
        )
        data['event_type'] = etype
        # The DOOR's key is authoritative, never a value from inside the body:
        # a caller able to set the downstream dedupe key could make two
        # different events collide, or replay one under a fresh key.
        data['idempotency_key'] = event.idempotency_key or str(event.id)
        if not data.get('claim_ref'):
            data['claim_ref'] = (data.get('claim') or {}).get('claim_ref') or ''
        processor.receive(data, received_via=event.received_via or 'integrations')
        return None, None

    # ------------------------------------------------------------------
    # bus.ping → bus.pong  (WS1 two-way state-bus harness)
    # ------------------------------------------------------------------

    def _bus_ping(self, data):
        """Prove the round trip: a ping arrived from Graphite; push a 'bus.pong'
        straight back onto the durable outbound bus so the record on the Graphite
        side flips to confirmed. Facts/states only, never money — and behind the
        BUS_PING_ENABLED switch (off by default, no redeploy to flip).
        """
        from django.conf import settings as dj

        if not getattr(dj, 'BUS_PING_ENABLED', False):
            self._not_acted = 'stored, not acted on: BUS_PING_ENABLED is off'
            return None, None

        from .models import OutboundEvent
        from .outbound import enqueue_and_deliver

        data = data or {}
        ref = str(data.get('ref') or '')
        callback = str(data.get('callback_url') or '')
        if not callback:
            self._not_acted = 'no callback_url in bus.ping payload'
            return None, None

        enqueue_and_deliver(
            target=OutboundEvent.Target.GRAPHITE,
            event_type='bus.pong',
            endpoint=callback,
            auth_kind=OutboundEvent.AuthKind.NONE,
            idempotency_key=('pong:' + ref) if ref else '',
            payload={'ref': ref, 'status': 'confirmed', 'omni_event_id': 'omni-bus'},
        )
        return None, None

    # ------------------------------------------------------------------
    # policy_issued → customer invoice
    # ------------------------------------------------------------------

    def _policy_issued(self, data: dict):
        """
        Expected payload:
          policy_number, insured_name, insured_graphite_id,
          premium_amount, vat_amount (optional),
          policy_start, issue_date (optional), due_days (optional)
        """
        contact = _resolve_contact(
            graphite_id=data['insured_graphite_id'],
            name=data['insured_name'],
            contact_type='customer',
        )

        issue_date = date.fromisoformat(
            data.get('issue_date') or timezone.localdate().isoformat()
        )
        due_days   = int(data.get('due_days', 30))
        due_date   = issue_date + timedelta(days=due_days)

        sys_user = _system_user()
        invoice = Invoice(
            invoice_type  = Invoice.InvoiceType.CUSTOMER_INVOICE,
            contact       = contact,
            currency_code_id = 'BWP',
            issue_date    = issue_date,
            due_date      = due_date,
            description   = f"Policy {data['policy_number']} - {data.get('policy_type','General')} Insurance",
            created_by    = sys_user,
        )
        invoice.save(audit_user=sys_user)

        premium = Decimal(str(data['premium_amount']))
        vat_amt = Decimal(str(data.get('vat_amount', '0')))

        # Premium line
        revenue_acct = _account('4100')
        if not revenue_acct:
            raise ValueError("GL account 4100 (Gross written premium) not found")

        zero_vat = _tax_rate('VAT_ZERO') or _tax_rate('VAT_EXEMPT')
        std_vat  = _tax_rate('VAT_STD') or _tax_rate('VAT_ZERO')

        InvoiceLine.objects.create(
            invoice    = invoice,
            account    = revenue_acct,
            description = f"Insurance premium - policy {data['policy_number']}",
            quantity   = Decimal('1'),
            unit_price = premium,
            tax_code   = std_vat if vat_amt else zero_vat,
        )

        invoice.recalculate_totals()
        invoice.save(audit_user=sys_user)

        return IntegrationEvent.ResultType.INVOICE, invoice

    # ------------------------------------------------------------------
    # claim_approved → vendor bill (claims payable)
    # ------------------------------------------------------------------

    def _claim_approved(self, data: dict):
        """
        Expected payload:
          claim_number, claimant_name, claimant_graphite_id,
          claim_amount, claim_type (optional), issue_date (optional)
        """
        contact = _resolve_contact(
            graphite_id=data['claimant_graphite_id'],
            name=data['claimant_name'],
            contact_type='vendor',
        )

        issue_date = date.fromisoformat(
            data.get('issue_date') or timezone.localdate().isoformat()
        )
        due_date = issue_date + timedelta(days=14)  # Claims paid within 14 days

        sys_user = _system_user()
        invoice = Invoice(
            invoice_type  = Invoice.InvoiceType.VENDOR_BILL,
            contact       = contact,
            currency_code_id = 'BWP',
            issue_date    = issue_date,
            due_date      = due_date,
            description   = f"Claim {data['claim_number']} - {data.get('claim_type','General')}",
            created_by    = sys_user,
        )
        invoice.save(audit_user=sys_user)

        claims_acct = _account('5100') or _account('5110')
        if not claims_acct:
            raise ValueError("GL account 5100/5110 (Claims expense) not found")

        zero_vat = _tax_rate('VAT_ZERO') or _tax_rate('VAT_EXEMPT')

        InvoiceLine.objects.create(
            invoice     = invoice,
            account     = claims_acct,
            description = f"Claim settlement - {data['claim_number']}",
            quantity    = Decimal('1'),
            unit_price  = Decimal(str(data['claim_amount'])),
            tax_code    = zero_vat,
        )

        invoice.recalculate_totals()
        invoice.save(audit_user=sys_user)

        return IntegrationEvent.ResultType.INVOICE, invoice

    # ------------------------------------------------------------------
    # commission_calculated → vendor bill (broker commission)
    # ------------------------------------------------------------------

    def _commission_calculated(self, data: dict):
        """
        Expected payload:
          broker_name, broker_graphite_id,
          commission_amount, wht_applicable (bool, default True),
          period_start, period_end
        """
        contact = _resolve_contact(
            graphite_id=data['broker_graphite_id'],
            name=data['broker_name'],
            contact_type='broker',
        )

        issue_date = timezone.localdate()
        due_date   = issue_date + timedelta(days=30)

        period_start = data.get('period_start', '')
        period_end   = data.get('period_end',   '')
        period_label = f"{period_start} to {period_end}" if period_start else ''

        sys_user = _system_user()
        invoice = Invoice(
            invoice_type  = Invoice.InvoiceType.VENDOR_BILL,
            contact       = contact,
            currency_code_id = 'BWP',
            issue_date    = issue_date,
            due_date      = due_date,
            description   = f"Broker commission - {data['broker_name']}{(' (' + period_label + ')') if period_label else ''}",
            created_by    = sys_user,
        )
        invoice.save(audit_user=sys_user)

        commission_acct = _account('5400')
        if not commission_acct:
            raise ValueError("GL account 5400 (Commission expense) not found")

        zero_vat = _tax_rate('VAT_ZERO') or _tax_rate('VAT_EXEMPT')

        InvoiceLine.objects.create(
            invoice     = invoice,
            account     = commission_acct,
            description = f"Commission - {data['broker_name']}",
            quantity    = Decimal('1'),
            unit_price  = Decimal(str(data['commission_amount'])),
            tax_code    = zero_vat,
        )

        invoice.recalculate_totals()
        invoice.save(audit_user=sys_user)

        return IntegrationEvent.ResultType.INVOICE, invoice

    # ------------------------------------------------------------------
    # policy_cancelled → credit note
    # ------------------------------------------------------------------

    def _policy_cancelled(self, data: dict):
        """
        Expected payload:
          policy_number, insured_name, insured_graphite_id,
          refund_amount, original_invoice_number (optional)
        """
        contact = _resolve_contact(
            graphite_id=data['insured_graphite_id'],
            name=data['insured_name'],
            contact_type='customer',
        )

        issue_date = timezone.localdate()
        due_date   = issue_date + timedelta(days=14)

        sys_user = _system_user()
        invoice = Invoice(
            invoice_type  = Invoice.InvoiceType.CREDIT_NOTE,
            contact       = contact,
            currency_code_id = 'BWP',
            issue_date    = issue_date,
            due_date      = due_date,
            description   = (
                f"Credit note - policy cancellation {data['policy_number']}"
                + (f" (reversal of {data['original_invoice_number']})"
                   if data.get('original_invoice_number') else '')
            ),
            created_by    = sys_user,
        )
        invoice.save(audit_user=sys_user)

        revenue_acct = _account('4100')
        if not revenue_acct:
            raise ValueError("GL account 4100 (Gross written premium) not found")

        zero_vat = _tax_rate('VAT_ZERO') or _tax_rate('VAT_EXEMPT')

        InvoiceLine.objects.create(
            invoice     = invoice,
            account     = revenue_acct,
            description = f"Premium refund - policy {data['policy_number']}",
            quantity    = Decimal('1'),
            unit_price  = -abs(Decimal(str(data['refund_amount']))),
            tax_code    = zero_vat,
        )

        invoice.recalculate_totals()
        invoice.save(audit_user=sys_user)

        return IntegrationEvent.ResultType.INVOICE, invoice
