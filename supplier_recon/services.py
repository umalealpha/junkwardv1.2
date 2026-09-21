"""Supplier Payables Reconciliation — the engine.

Public entry points
-------------------
build_recon_run(company, period_label, prepared_by=None)
    Idempotently (re)builds a month: pulls the in-scope vendor-bill population,
    upserts supplier lines + bill items, recomputes the 3-way match and the
    payment position **as at the period end**.
action_item(item, user, reason_code=..., justification=..., hold=...)
    Records a payables decision on one bill, enforcing reason + justification.
resolve_escalation(esc, user, status, note)
    Reviewer closes out an escalation.
finalise_run(run, user) / reopen_run(run, user)
    Locks / unlocks the month. Finalise refuses while anything is unexplained.

Why the numbers are computed, not copied
----------------------------------------
``billing.Invoice.amount_paid`` is "as at now". A reconciliation for July must
show the position **as at 31 July**, so paid amounts are re-derived from
``payments.PaymentAllocation`` restricted to confirmed/reconciled payments with
``payment_date <= period_end``. Copying Invoice.amount_paid would silently
back-date August payments into the July board.

Population
----------
Matches ``reporting._build_aging('vendor_bill', …)`` — the same open statuses
and the same ``issue_date <= as_of`` cut — so this board and the AP Aging report
cannot disagree. On top of that open population it also pulls bills *issued* in
the month and bills *settled* in the month, because the board has to show who
was paid, not only who was not.

Currency
--------
Roll-ups are BWP, converted at the bill's locked ``exchange_rate`` (1.0 for the
BWP book, which is effectively all of ADIC's vendor ledger). The transaction
currency and face amount stay on the serialized item so an FX bill is never
silently added to a Pula total.
"""

from __future__ import annotations

import calendar
import datetime as dt
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import (Count, DecimalField, F, OuterRef, Q, Subquery,
                              Sum, Value)
from django.db.models.functions import Coalesce
from django.utils import timezone

from .constants import (
    CENT,
    CLAIM_BACKED_CATEGORIES,
    LedgerStage,
    EscalationStatus,
    LIVE_ESCALATION_STATUSES,
    LineStatus,
    MIN_JUSTIFICATION_CHARS,
    MatchStatus,
    NOT_FULLY_PAID,
    PaymentStatus,
    RunStatus,
    SupplierCategory,
    ZERO,
)
from .models import (
    Escalation,
    InvoiceReconItem,
    ReconActionLog,
    ReconOwner,
    ReconSupplierProfile,
    SupplierReconLine,
    SupplierReconRun,
)

_MONEY = DecimalField(max_digits=18, decimal_places=2)

# The only fields a rebuild may touch on an EXISTING row - observed facts, never
# a payables decision. A full save() would race action_item() and silently
# overwrite the clerk's reason, justification and actioned flag: a lost update
# on an accountability record (Fable 5 review 2026-07-25).
OBSERVED_FIELDS = [
    'amount', 'amount_paid', 'due_date', 'payment_status', 'ledger_stage',
    'match_status', 'purchase_order', 'goods_receipt', 'claim_reference',
    'updated_at',
]


# ---------------------------------------------------------------------------
# Period helpers
# ---------------------------------------------------------------------------

def period_bounds(period_label: str) -> tuple[dt.date, dt.date]:
    """'2026-07' -> (2026-07-01, 2026-07-31). Raises on anything else."""
    raw = (period_label or '').strip()
    parts = raw.split('-')
    if len(parts) != 2:
        raise ValidationError(
            f"Invalid period '{period_label}' — expected YYYY-MM, e.g. 2026-07."
        )
    try:
        year, month = int(parts[0]), int(parts[1])
    except ValueError:
        raise ValidationError(
            f"Invalid period '{period_label}' — expected YYYY-MM, e.g. 2026-07."
        )
    if not 1 <= month <= 12 or not 2000 <= year <= 2100:
        raise ValidationError(
            f"Invalid period '{period_label}' — month must be 01-12 and the "
            f"year must be realistic."
        )
    last_day = calendar.monthrange(year, month)[1]
    return dt.date(year, month, 1), dt.date(year, month, last_day)


def assert_user_in_entity(user, company_id, *, require_user=False):
    """Defence in depth: every mutating service call re-checks the entity.

    The API already scopes each queryset via
    ``permissions.scope_to_allowed_companies``, so this is a second lock on the
    same door — it holds if a future view, management command or shell caller
    forgets to scope. Vendors do not cross legal entities (CFO directive
    2026-05-18), and a payables decision is a record, so a wrong-entity write
    must be impossible rather than merely unlikely.

    ``require_user=True`` on the decision paths (action, escalate, resolve,
    finalise, reopen). Those are accountability records — an unattributable
    payables decision is worthless, so a None user is refused outright rather
    than waved through. Only the build path accepts None, because the nightly
    ``build_supplier_recon`` cron legitimately runs with no user and records
    nothing but observed facts.
    """
    if user is None:
        if require_user:
            raise ValidationError(
                'This action has to be attributable to a person — no signed-in '
                'user was supplied.'
            )
        return  # system/cron caller: observed facts only, no decision recorded.
    from core.models import allowed_company_ids
    allowed = allowed_company_ids(user)
    if allowed == {'*'}:
        return
    if str(company_id) not in allowed:
        raise ValidationError(
            'You are not permitted in the legal entity this reconciliation '
            'belongs to.'
        )


def _fiscal_period_for(company, period_end):
    """Best-effort link to the accounting period. Never blocks the build."""
    from ledger.models import FiscalPeriod
    return (FiscalPeriod.objects
            .filter(company=company, start_date__lte=period_end,
                    end_date__gte=period_end)
            .first())


# ---------------------------------------------------------------------------
# Bill population + money, as at the period end
# ---------------------------------------------------------------------------

def _paid_as_at_subquery(as_of, *, since=None):
    """Sum of confirmed/reconciled allocations against the outer Invoice.

    ``since`` restricts to payments made on/after a date — used to detect bills
    settled *within* the month.
    """
    from payments.models import Payment, PaymentAllocation

    flt = {
        'invoice': OuterRef('pk'),
        'payment__status__in': [Payment.Status.CONFIRMED, Payment.Status.RECONCILED],
        'payment__payment_date__lte': as_of,
    }
    if since is not None:
        flt['payment__payment_date__gte'] = since
    return Subquery(
        PaymentAllocation.objects
        .filter(**flt)
        .values('invoice')
        .annotate(total=Sum('amount_allocated'))
        .values('total')[:1],
        output_field=_MONEY,
    )


def in_scope_bills(company, period_start, period_end, supplier_ids=None):
    """The vendor-bill population for one company's month, annotated with the
    paid position as at period end.

    Open statuses and the issue-date cut mirror reporting's AP Aging exactly.
    """
    from billing.models import Invoice

    # POSTED / PARTIALLY_PAID / OVERDUE mirror reporting's AP Aging. PAID is
    # added so the board can show who WAS settled. DRAFT and PENDING_APPROVAL
    # are added because on 2026-07-25 every vendor bill in omni was still a
    # draft: PO-matched, real, owed — but invisible to the GL and to AP Aging.
    # Excluding them would have shown the CFO an empty board and implied nothing
    # was owed. They are flagged via ledger_stage and totalled separately, so
    # the posted subset still ties to AP Aging exactly.
    open_statuses = [
        Invoice.Status.POSTED,
        Invoice.Status.PARTIALLY_PAID,
        Invoice.Status.OVERDUE,
        Invoice.Status.PAID,
        Invoice.Status.DRAFT,
        Invoice.Status.PENDING_APPROVAL,
    ]

    qs = (Invoice.objects
          .filter(invoice_type=Invoice.InvoiceType.VENDOR_BILL,
                  status__in=open_statuses,
                  company=company,
                  issue_date__lte=period_end)
          .select_related('contact', 'currency_code', 'purchase_order'))

    if supplier_ids is not None:
        qs = qs.filter(contact_id__in=supplier_ids)

    qs = qs.annotate(
        paid_as_at=Coalesce(_paid_as_at_subquery(period_end),
                            Value(ZERO), output_field=_MONEY),
        paid_in_period=Coalesce(_paid_as_at_subquery(period_end, since=period_start),
                                Value(ZERO), output_field=_MONEY),
    )

    # Keep a bill if it was still open at month end, was raised in the month, or
    # was settled in the month. Anything else belongs to an earlier board.
    return qs.filter(
        Q(paid_as_at__lt=F('total_amount') - CENT)
        | Q(issue_date__gte=period_start)
        | Q(paid_in_period__gt=ZERO)
    ).order_by('contact__name', 'due_date', 'invoice_number')


def _ledger_stage(inv) -> str:
    """Has this bill actually reached the general ledger?

    Keyed off ``journal_entry``, not ``status``. Status is not a safe proxy:
    ``payments._update_invoice_from_allocations`` rewrites a bill's status to
    PAID/PARTIALLY_PAID by direct DB update whenever an allocation is confirmed,
    including on a bill that was never posted — so a draft bill can end up
    labelled "paid" with no journal behind it. The journal entry is the fact.
    """
    return LedgerStage.POSTED if inv.journal_entry_id else LedgerStage.DRAFT


def _bwp(amount, rate) -> Decimal:
    """Convert a transaction amount to BWP at the bill's locked rate."""
    amt = amount or ZERO
    r = rate if rate is not None else Decimal('1')
    return (amt * r).quantize(Decimal('0.01'))


def _due_date(inv):
    """Invoices carry a due date; fall back to issue date like AP Aging does."""
    return inv.due_date or inv.issue_date


# ---------------------------------------------------------------------------
# 3-way match
# ---------------------------------------------------------------------------

def _linked_purchase_order(inv):
    """The PO that authorised this spend.

    ``billing.Invoice.purchase_order`` is the canonical link — every vendor bill
    must reference an approved PO before it can post. ``POBillMatch`` is the
    match record; fall back to it for legacy bills whose FK was never set.
    """
    if inv.purchase_order_id:
        return inv.purchase_order
    from procurement.models import POBillMatch
    match = (POBillMatch.objects
             .filter(bill=inv)
             .select_related('purchase_order')
             .order_by('-created_at')
             .first())
    return match.purchase_order if match else None


def _po_variance_flagged(inv) -> bool:
    """True when procurement's own 3-way match recorded a variance.

    Reuses the existing match engine rather than re-deriving quantities here.
    """
    from procurement.models import POBillMatch
    variance = {
        POBillMatch.MatchStatus.VARIANCE_QUANTITY,
        POBillMatch.MatchStatus.VARIANCE_PRICE,
        POBillMatch.MatchStatus.VARIANCE_BOTH,
        POBillMatch.MatchStatus.NEEDS_TIER1_APPROVAL,
        POBillMatch.MatchStatus.NEEDS_TIER2_APPROVAL,
        POBillMatch.MatchStatus.TIER1_APPROVED,
    }
    return POBillMatch.objects.filter(bill=inv, match_status__in=variance).exists()


def _linked_goods_receipt(po):
    """A posted goods receipt against the PO — proof the work/parts arrived."""
    if po is None:
        return None
    from procurement.models import GoodsReceiptNote
    return (GoodsReceiptNote.objects
            .filter(purchase_order=po, status=GoodsReceiptNote.Status.POSTED)
            .order_by('-receipt_date')
            .first())


def _claim_reference(po) -> str:
    """The claim the spend was authorised under.

    omni holds a reference, not the claim itself — the master claims register
    lives in Graphite (see PurchaseOrder.related_claim_reference).
    """
    if po is None:
        return ''
    return (po.related_claim_reference or '').strip()


def _apply_three_way_match(item, inv, category):
    """Bill <-> PO <-> claim authorisation.

    CFO decision 2026-07-27: a matching PO-to-invoice is sufficient — a formal
    goods receipt is NOT required. Alpha Direct barely captures GRNs (a handful
    across thousands of POs) and claims/service bills (panel beaters, glass,
    repairs) have no "goods receipt" step at all, so requiring one flagged 100%
    of bills as unmatched. The goods receipt is still LINKED for information, but
    the match is now 2-way: PO present + no PO/invoice variance = MATCHED.

    Evaluated worst-first so the status names the single most useful thing the
    payables clerk has to go and fix.
    """
    po = _linked_purchase_order(inv)
    grn = _linked_goods_receipt(po)
    claim_ref = _claim_reference(po)

    item.purchase_order = po
    item.goods_receipt = grn          # informational only — no longer gates the match
    item.claim_reference = claim_ref

    claim_required = category in CLAIM_BACKED_CATEGORIES

    if po is None:
        item.match_status = MatchStatus.NO_PO
    elif claim_required and not claim_ref:
        item.match_status = MatchStatus.NO_CLAIM
    elif _po_variance_flagged(inv):
        item.match_status = MatchStatus.PARTIAL
    else:
        item.match_status = MatchStatus.MATCHED


# ---------------------------------------------------------------------------
# Build
# ---------------------------------------------------------------------------

@transaction.atomic
def build_recon_run(company, period_label: str, *, prepared_by=None,
                    audit_user=None) -> SupplierReconRun:
    """Create or refresh one company-month. Refreshes observed facts only —
    a payables decision (reason, justification, hold, actioned) is never
    overwritten by a rebuild."""
    start, end = period_bounds(period_label)
    actor = audit_user or prepared_by
    assert_user_in_entity(actor, company.id)

    run = SupplierReconRun.objects.filter(company=company,
                                          period_label=period_label).first()
    if run is None:
        run = SupplierReconRun(company=company, period_label=period_label,
                               period_start=start, period_end=end,
                               prepared_by=prepared_by,
                               owner=ReconOwner.for_company(company),
                               fiscal_period=_fiscal_period_for(company, end))
        run.save(audit_user=actor,
                 audit_description=f"Built supplier recon {period_label} "
                                   f"for {company.code}")
    elif run.is_locked:
        raise ValidationError(
            f"{period_label} is finalised. Reopen it before rebuilding."
        )

    # Only classified, in-scope suppliers are reconciled. Unclassified vendors
    # are reported separately by unclassified_vendors() so nothing hides.
    profiles = {
        str(p.contact_id): p
        for p in ReconSupplierProfile.objects
        .filter(in_scope=True, contact__company=company)
        .select_related('contact')
    }
    if not profiles:
        recompute_run_totals(run)
        return run

    bills = in_scope_bills(company, start, end, supplier_ids=list(profiles.keys()))

    lines: dict[str, SupplierReconLine] = {}
    seen_item_ids: set = set()

    for inv in bills:
        supplier_key = str(inv.contact_id)
        profile = profiles.get(supplier_key)
        if profile is None:
            continue

        line = lines.get(supplier_key)
        if line is None:
            line, created = SupplierReconLine.objects.get_or_create(
                run=run, supplier_id=inv.contact_id,
                defaults={'category': profile.category},
            )
            if not created and line.category != profile.category:
                line.category = profile.category
                line.save(update_fields=['category', 'updated_at'])
            lines[supplier_key] = line

        item = _upsert_item(line, inv, profile, actor)
        seen_item_ids.add(item.pk)

    # A bill can drop out of the month (e.g. it was cancelled after the last
    # build). Remove only items nobody has actioned — an actioned item is
    # evidence and stays, so the trail is never quietly rewritten.
    stale = (InvoiceReconItem.objects
             .filter(line__run=run, actioned=False)
             .exclude(pk__in=seen_item_ids))
    stale.delete()

    for line in run.lines.all():
        recompute_line_totals(line)
    # Drop supplier lines left with nothing on them.
    run.lines.filter(invoice_count=0).delete()

    run.last_built_at = timezone.now()
    run.owner = ReconOwner.for_company(company)
    if run.status == RunStatus.OPEN:
        run.status = RunStatus.IN_PROGRESS
    run.save(update_fields=['status', 'last_built_at', 'owner', 'updated_at'],
             skip_audit=True)
    recompute_run_totals(run)
    return run


def _upsert_item(line, inv, profile, actor) -> InvoiceReconItem:
    """Refresh the observed facts for one bill. Decisions are left alone."""
    amount = _bwp(inv.total_amount, inv.exchange_rate)
    paid = _bwp(getattr(inv, 'paid_as_at', ZERO), inv.exchange_rate)
    # Guard against an over-allocation upstream showing as negative outstanding.
    if paid > amount:
        paid = amount

    item = InvoiceReconItem.objects.filter(line=line, invoice=inv).first()
    existing = item is not None
    if item is None:
        item = InvoiceReconItem(line=line, invoice=inv)

    item.amount = amount
    item.amount_paid = paid
    item.due_date = _due_date(inv)
    item.ledger_stage = _ledger_stage(inv)
    item.payment_status = _derive_payment_status(item, amount, paid)
    _apply_three_way_match(item, inv, line.category)

    # Bypass full_clean here on purpose: a freshly-built UNPAID item legitimately
    # has no reason code yet — that is exactly what the payables team is being
    # asked to supply. The rule is enforced in action_item() and at finalise.
    if existing:
        item.save(update_fields=OBSERVED_FIELDS, skip_audit=True)
    else:
        item.save(audit_user=actor, skip_audit=True)
    return item


def _derive_payment_status(item, amount, paid) -> str:
    """Observed settlement position. A payables HELD decision is sticky."""
    if item.pk and item.payment_status == PaymentStatus.HELD:
        return PaymentStatus.HELD
    if paid <= ZERO:
        return PaymentStatus.UNPAID
    if paid < (amount - CENT):
        return PaymentStatus.PARTIALLY_PAID
    return PaymentStatus.PAID


def unclassified_vendors(company, period_start, period_end):
    """Vendors with bills in the month but no ReconSupplierProfile.

    Surfaced on the dashboard so 'not in scope' is a visible decision rather
    than an accidental omission.
    """
    classified = ReconSupplierProfile.objects.values_list('contact_id', flat=True)
    return (in_scope_bills(company, period_start, period_end)
            .exclude(contact_id__in=classified)
            .values('contact_id', 'contact__name')
            # Converted to BWP at each bill's locked rate. Summing raw
            # total_amount would add Rand to Pula and then render the result
            # with a P sign - the exact error this module exists to prevent.
            .annotate(bills=Count('id'),
                      invoiced=Sum(F('total_amount') * F('exchange_rate'),
                                   output_field=_MONEY))
            .order_by('-invoiced'))


# ---------------------------------------------------------------------------
# Payables actions
# ---------------------------------------------------------------------------

@transaction.atomic
def action_item(item: InvoiceReconItem, user, *, reason_code=None,
                justification: str = '', hold: bool | None = None,
                assigned_to=None) -> InvoiceReconItem:
    """Record a payables decision on one bill.

    Enforces reason + justification for anything not fully paid, and auto-raises
    the escalation when the position warrants one.
    """
    # Re-read under lock so two clerks cannot action the same bill at once and
    # so a run finalised mid-edit is caught (TOCTOU).
    # NOTE: no select_related here. Postgres refuses FOR UPDATE across the
    # nullable side of an outer join, which select_related on the optional FKs
    # (reason_code, purchase_order, …) produces. Lock the row alone; the few
    # related reads that follow are cheap.
    locked = (InvoiceReconItem.objects
              .select_for_update()
              .get(pk=item.pk))
    assert_user_in_entity(user, locked.line.run.company_id, require_user=True)
    if locked.line.run.is_locked:
        raise ValidationError(
            f"{locked.line.run.period_label} is finalised. Reopen it to change "
            f"a decision."
        )

    from_status = locked.payment_status

    if hold is True:
        locked.payment_status = PaymentStatus.HELD
    elif hold is False and locked.payment_status == PaymentStatus.HELD:
        # Releasing a hold: fall back to what the money actually says.
        locked.payment_status = _observed_status(locked)

    if reason_code is not None:
        locked.reason_code = reason_code
    if justification:
        locked.justification = justification
    if assigned_to is not None:
        locked.assigned_to = assigned_to

    # Enforce the reason + justification gate BEFORE marking the bill actioned.
    # needs_justification short-circuits to False once `actioned` is True (so an
    # already-explained bill stops nagging on the board — d6e5cfa2). Flipping
    # `actioned` first would let that same short-circuit disable the very check
    # meant to guard an unpaid bill, allowing it to be actioned with no reason.
    # Validate the pre-action state, then stamp who/when.
    locked.full_clean(exclude=['invoice', 'line', 'purchase_order',
                               'goods_receipt', 'assigned_to', 'actioned_by'])

    locked.actioned = True
    locked.actioned_by = user
    locked.actioned_at = timezone.now()
    locked.save(audit_user=user,
                audit_description=f"Payables actioned bill "
                                  f"{locked.invoice.invoice_number}: "
                                  f"{locked.payment_status}")

    ReconActionLog.objects.create(
        item=locked, actor=user,
        action='hold' if hold else 'action',
        from_status=from_status, to_status=locked.payment_status,
        note=(justification or '')[:2000],
    )

    if locked.requires_escalation and not locked.has_live_escalation:
        raise_escalation(
            locked, user,
            justification or 'Auto-raised: bill is overdue or its reason code '
                             'requires escalation.',
        )

    recompute_line_totals(locked.line)
    recompute_run_totals(locked.line.run)
    return locked


def _observed_status(item) -> str:
    """Settlement position ignoring any sticky HELD flag."""
    if item.amount_paid <= ZERO:
        return PaymentStatus.UNPAID
    if item.amount_paid < (item.amount - CENT):
        return PaymentStatus.PARTIALLY_PAID
    return PaymentStatus.PAID


@transaction.atomic
def raise_escalation(item: InvoiceReconItem, user, justification: str,
                     raised_to=None) -> Escalation:
    """Raise an escalation. Defaults the recipient to the board owner so a held
    bill lands with a named person rather than in a queue nobody owns."""
    assert_user_in_entity(user, item.line.run.company_id, require_user=True)
    # One live escalation per bill. Without this, clicking Escalate twice made
    # two OPEN rows and inflated the run's escalated total, because the amount is
    # summed per escalation - the CFO's tile would overstate the exposure.
    live = item.escalations.filter(
        status__in=list(LIVE_ESCALATION_STATUSES)).first()
    if live is not None:
        return live
    text = (justification or '').strip()
    if len(text) < MIN_JUSTIFICATION_CHARS:
        raise ValidationError({
            'justification': f'An escalation needs at least '
                             f'{MIN_JUSTIFICATION_CHARS} characters explaining '
                             f'what the reviewer must decide.',
        })
    if raised_to is None:
        raised_to = (item.line.run.owner
                     or ReconOwner.for_company(item.line.run.company))
    esc = Escalation(item=item, raised_by=user, raised_to=raised_to,
                     justification=text, amount=item.amount_outstanding)
    esc.save(audit_user=user,
             audit_description=f"Escalated bill {item.invoice.invoice_number} "
                               f"({item.amount_outstanding})")
    ReconActionLog.objects.create(item=item, actor=user, action='escalate',
                                  note=text[:2000])
    recompute_run_totals(item.line.run)
    return esc


@transaction.atomic
def resolve_escalation(esc: Escalation, user, status: str,
                       note: str = '') -> Escalation:
    """Reviewer closes out an escalation (resolved / waived / acknowledged)."""
    assert_user_in_entity(user, esc.item.line.run.company_id, require_user=True)
    if status not in {EscalationStatus.RESOLVED, EscalationStatus.WAIVED,
                      EscalationStatus.ACKNOWLEDGED}:
        raise ValidationError(f"'{status}' is not a valid escalation outcome.")
    text = (note or '').strip()
    if status in {EscalationStatus.RESOLVED, EscalationStatus.WAIVED} and \
            len(text) < MIN_JUSTIFICATION_CHARS:
        raise ValidationError({
            'note': f'Closing an escalation needs at least '
                    f'{MIN_JUSTIFICATION_CHARS} characters on record.',
        })
    esc.status = status
    esc.resolution_note = text
    if status in {EscalationStatus.RESOLVED, EscalationStatus.WAIVED}:
        esc.resolved_by = user
        esc.resolved_at = timezone.now()
    esc.save(audit_user=user,
             audit_description=f"Escalation {status} on bill "
                               f"{esc.item.invoice.invoice_number}")
    ReconActionLog.objects.create(item=esc.item, actor=user,
                                 action=f'escalation_{status}', note=text[:2000])
    recompute_run_totals(esc.item.line.run)
    return esc


# ---------------------------------------------------------------------------
# Roll-ups
# ---------------------------------------------------------------------------

def recompute_line_totals(line: SupplierReconLine) -> SupplierReconLine:
    items = line.items.all()
    agg = items.aggregate(invoiced=Sum('amount'), paid=Sum('amount_paid'))
    line.invoiced = agg['invoiced'] or ZERO
    line.paid = agg['paid'] or ZERO
    line.unpaid = line.invoiced - line.paid
    line.held = (items.filter(payment_status=PaymentStatus.HELD)
                 .aggregate(s=Sum('amount'))['s'] or ZERO)
    line.invoice_count = items.count()
    line.unactioned_count = items.filter(actioned=False).count()

    # Overdue split: what is owed AND already past due, plus how many days the
    # oldest overdue bill has been waiting (the "overdue by" figure). Keyed off
    # due_date, so a bill still within its terms is not counted (Bharath,
    # 2026-07-27). Computed in the DB — same due-date cut as ageing_bucket.
    today = timezone.localdate()
    overdue_items = items.exclude(payment_status=PaymentStatus.PAID).filter(
        due_date__lt=today)
    line.overdue = (overdue_items.aggregate(
        s=Sum(F('amount') - F('amount_paid'), output_field=_MONEY))['s'] or ZERO)
    oldest_due = (overdue_items.order_by('due_date')
                  .values_list('due_date', flat=True).first())
    line.max_days_past_due = (today - oldest_due).days if oldest_due else 0

    open_positions = items.filter(payment_status__in=list(NOT_FULLY_PAID)).count()
    if line.unactioned_count:
        line.status = LineStatus.PENDING
    elif open_positions:
        line.status = LineStatus.EXCEPTION
    else:
        line.status = LineStatus.CLEARED
    line.save(update_fields=['invoiced', 'paid', 'unpaid', 'held',
                             'overdue', 'max_days_past_due',
                             'invoice_count', 'unactioned_count', 'status',
                             'updated_at'])
    return line


def recompute_run_totals(run: SupplierReconRun) -> SupplierReconRun:
    agg = run.lines.aggregate(invoiced=Sum('invoiced'), paid=Sum('paid'),
                              unpaid=Sum('unpaid'), held=Sum('held'))
    run.total_invoiced = agg['invoiced'] or ZERO
    run.total_paid = agg['paid'] or ZERO
    run.total_unpaid = agg['unpaid'] or ZERO
    run.total_held = agg['held'] or ZERO
    run.total_not_posted = (
        InvoiceReconItem.objects
        .filter(line__run=run, ledger_stage=LedgerStage.DRAFT)
        .aggregate(s=Sum('amount'))['s'] or ZERO
    )
    run.total_escalated = (
        Escalation.objects
        .filter(item__line__run=run, status__in=list(LIVE_ESCALATION_STATUSES))
        .aggregate(s=Sum('amount'))['s'] or ZERO
    )
    run.save(update_fields=['total_invoiced', 'total_paid', 'total_unpaid',
                            'total_held', 'total_escalated',
                            'total_not_posted', 'updated_at'],
             skip_audit=True)
    return run


# ---------------------------------------------------------------------------
# Finalisation
# ---------------------------------------------------------------------------

def blocking_exceptions(run: SupplierReconRun) -> list[dict]:
    """Everything standing between this run and a signed-off month."""
    problems: list[dict] = []
    items = (InvoiceReconItem.objects
             .filter(line__run=run)
             .select_related('invoice', 'reason_code', 'line__supplier')
             .prefetch_related('escalations'))

    for item in items:
        if item.payment_status == PaymentStatus.PAID and item.is_posted:
            continue
        reasons = []
        if not item.is_posted:
            reasons.append('bill still in draft — never posted to the ledger')
        if not item.actioned:
            reasons.append('not actioned by payables')
        if item.reason_code_id is None:
            reasons.append('no reason code')
        if len((item.justification or '').strip()) < MIN_JUSTIFICATION_CHARS:
            reasons.append('no written justification')
        if item.requires_escalation and not any(
                e.status in LIVE_ESCALATION_STATUSES
                or e.status == EscalationStatus.RESOLVED
                for e in item.escalations.all()):
            reasons.append('escalation required but none raised')
        if reasons:
            problems.append({
                'item_id': str(item.pk),
                'invoice_number': item.invoice.invoice_number,
                'supplier': item.line.supplier.name,
                'amount_outstanding': item.amount_outstanding,
                'problems': reasons,
            })
    return problems


@transaction.atomic
def finalise_run(run: SupplierReconRun, user) -> SupplierReconRun:
    """Lock the month. Refuses while any bill is unexplained."""
    locked = SupplierReconRun.objects.select_for_update().get(pk=run.pk)
    assert_user_in_entity(user, locked.company_id, require_user=True)
    if locked.is_locked:
        raise ValidationError(f"{locked.period_label} is already finalised.")
    if locked.prepared_by_id and locked.prepared_by_id == getattr(user, 'id', None):
        raise ValidationError(
            'Segregation of duties: the person who built the run cannot also '
            'sign it off. Ask a Financial Controller, Finance Manager or the '
            'CFO to finalise.'
        )
    problems = blocking_exceptions(locked)
    if problems:
        raise ValidationError({
            'blocking': f"{len(problems)} bill(s) block finalisation.",
            'items': problems[:50],
        })
    locked.status = RunStatus.FINALISED
    locked.reviewed_by = user
    locked.finalised_at = timezone.now()
    locked.save(update_fields=['status', 'reviewed_by', 'finalised_at',
                               'updated_at'],
                audit_user=user,
                audit_description=f"Finalised supplier recon "
                                  f"{locked.period_label} "
                                  f"({locked.company.code})")
    return locked


@transaction.atomic
def reopen_run(run: SupplierReconRun, user, reason: str = '') -> SupplierReconRun:
    """Unlock a finalised month. Requires a reason — reopening a signed-off
    reconciliation is an audit event, not a convenience."""
    locked = SupplierReconRun.objects.select_for_update().get(pk=run.pk)
    assert_user_in_entity(user, locked.company_id, require_user=True)
    if not locked.is_locked:
        raise ValidationError(f"{locked.period_label} is not finalised.")
    # Symmetry with finalise_run. Blocking the preparer from signing off but
    # letting them unlock a month somebody else signed would leave the lock
    # decorative: they could reopen, change a decision, and have it re-signed.
    if locked.prepared_by_id and locked.prepared_by_id == getattr(user, 'id', None):
        raise ValidationError(
            'Segregation of duties: you built this run, so you cannot reopen it '
            'after sign-off. Ask another Financial Controller, Finance Manager '
            'or the CFO.'
        )
    text = (reason or '').strip()
    if len(text) < MIN_JUSTIFICATION_CHARS:
        raise ValidationError({
            'reason': f'Reopening a finalised month needs at least '
                      f'{MIN_JUSTIFICATION_CHARS} characters on record.',
        })
    locked.status = RunStatus.REOPENED
    locked.finalised_at = None
    locked.save(update_fields=['status', 'finalised_at', 'updated_at'],
                audit_user=user,
                audit_description=f"Reopened supplier recon "
                                  f"{locked.period_label}: {text[:200]}")
    return locked
