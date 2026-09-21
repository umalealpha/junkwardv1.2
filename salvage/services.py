"""salvage/services.py — domain operations that span more than one model.

CFO directive 2026-05-18: rewrite the salvage GL posting so the books
treat salvage stock correctly. Previous implementation only booked
Dr Cash / Cr Income on sale, which is wrong on two counts:

  1. Salvage taken into the yard is an ASSET on the BS until sold —
     not a memo-only line. The CoA carries it as inventory.
  2. The P&L hit on disposal is a GAIN or LOSS, not gross revenue —
     gross revenue ignores that the asset was already on the BS.

The corrected entries are:

  ── INTAKE (Item created / written off into our yard) ───────────────
     DR  1320 Salvage Inventory              (cost_basis)
     CR  105004 Salvages & Recoveries        (cost_basis)

  The credit reduces gross claims paid — it is a contra-claims account,
  not an income line — because the salvage value is what the insurer
  expects to recover against the claim it just paid.

  ── SALE (Sale row created + posted) ────────────────────────────────
     DR  280001 Bank / cash                  (sale.sale_price)
     CR  1320 Salvage Inventory              (item.cost_basis)
     +  GAIN: CR 400010 Gain on Salvage      (sale_price - cost_basis)
     +  LOSS: DR 400010 Loss on Salvage      (cost_basis - sale_price)

  The inventory release is at carrying value (cost_basis); the
  difference vs sale_price hits Gain/Loss on Salvage in the P&L.

Account codes are read from Django settings so the CFO can tweak the
CoA mapping without code changes:

  SALVAGE_INVENTORY_ACCOUNT_CODE   default '1320'   (asset)
  SALVAGE_RECOVERY_ACCOUNT_CODE    default '105004' (contra-claims)
  SALVAGE_CASH_ACCOUNT_CODE        default '280001' (bank)
  SALVAGE_GAIN_LOSS_ACCOUNT_CODE   default '400010' (gain/loss on disposal)

Threshold-driven approvals (unchanged):
  - Quote accept > threshold → SalvageApproval(kind=quote_accept).
  - Sale below reserve OR above threshold → SalvageApproval(kind=sale).
"""
from __future__ import annotations

import logging
from decimal import Decimal
from typing import Optional

from django.conf import settings
from django.contrib.auth.models import User
from django.db import transaction

from .models import BuyerQuote, Sale, SalvageApproval, SalvageItem

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Company validation — Salvage is recorded under Veritas only
# (Kgosi bug 87a249f3, 2026-09-18)
# ---------------------------------------------------------------------------
SALVAGE_COMPANY_CODE = 'VCM'


def get_salvage_company():
    """Return the Veritas Capital Company record (code='VCM').

    Raises Company.DoesNotExist if Veritas is not set up in the database.
    This is a configuration requirement: the system will not allow salvage
    entries under any other company.
    """
    from core.models import Company
    return Company.objects.get(code=SALVAGE_COMPANY_CODE)


# ---------------------------------------------------------------------------
# Configuration helpers
# ---------------------------------------------------------------------------
def _approval_threshold_bwp() -> Decimal:
    raw = getattr(settings, 'SALVAGE_APPROVAL_THRESHOLD_BWP', '10000')
    try:
        return Decimal(str(raw))
    except Exception:  # noqa: BLE001
        return Decimal('10000')


def _cash_account_code() -> str:
    return getattr(settings, 'SALVAGE_CASH_ACCOUNT_CODE', '280001')


def _inventory_account_code() -> str:
    return getattr(settings, 'SALVAGE_INVENTORY_ACCOUNT_CODE', '1320')


def _recovery_account_code() -> str:
    return getattr(settings, 'SALVAGE_RECOVERY_ACCOUNT_CODE', '105004')


def _gain_loss_account_code() -> str:
    return getattr(settings, 'SALVAGE_GAIN_LOSS_ACCOUNT_CODE', '400010')


# ---------------------------------------------------------------------------
# GL posting
# ---------------------------------------------------------------------------
def _bwp_currency():
    from core.models import Currency
    bwp, _ = Currency.objects.get_or_create(
        code='BWP', defaults={'name': 'Botswana Pula', 'symbol': 'P'},
    )
    return bwp


def _resolve_accounts(codes: list[str]):
    """Return dict {code: Account or None}. Logs misses without raising."""
    from ledger.models import Account
    accounts = {a.code: a for a in Account.objects.filter(code__in=codes)}
    return {c: accounts.get(c) for c in codes}


def _post_lines(je, lines):
    """Bulk-create balanced JE lines. `lines` = list of (account, dr, cr, desc)."""
    from ledger.models import JournalEntryLine
    for acct, dr, cr, desc in lines:
        JournalEntryLine.objects.create(
            journal_entry  = je,
            account        = acct,
            description    = desc,
            debit_amount   = dr,
            credit_amount  = cr,
            debit_bwp      = dr,
            credit_bwp     = cr,
        )


@transaction.atomic
def post_intake_to_gl(item: SalvageItem, *, user: Optional[User] = None):
    """Post salvage INTAKE — recognise the asset on the balance sheet.

      DR  1320 Salvage Inventory      (item.cost_basis)
      CR  105004 Salvages & Recoveries (item.cost_basis)

    Idempotent: if intake_journal_entry FK is already set, returns it.
    Skips silently when cost_basis is zero — there's no movement to book.
    """
    from ledger.models import JournalEntry

    if item.intake_journal_entry_id:
        return item.intake_journal_entry
    cost = Decimal(item.cost_basis or 0)
    if cost <= 0:
        return None

    accts = _resolve_accounts([_inventory_account_code(), _recovery_account_code()])
    inv = accts[_inventory_account_code()]
    rec = accts[_recovery_account_code()]
    if not inv or not rec:
        log.warning(
            'Salvage intake GL skipped — CoA missing %s/%s; item=%s left unposted.',
            _inventory_account_code(), _recovery_account_code(), item.pk,
        )
        return None

    je = JournalEntry.objects.create(
        entry_number   = '',
        entry_date     = item.received_date or item.created_at.date(),
        description    = (f'Salvage intake — {item.item_code} '
                          f'{(item.part_name or "")[:60]}')[:500],
        source_type    = 'salvage_intake',
        journal_type   = 'general',
        currency_code  = _bwp_currency(),
        exchange_rate  = Decimal('1.00000000'),
        company        = item.company,
        created_by     = user or item.received_by or item.created_by,
        status         = JournalEntry.Status.DRAFT,
        notes          = (f'Auto-posted on intake of salvage item {item.pk}; '
                          f'claim={item.claim_number}')[:1000],
    )
    _post_lines(je, [
        (inv, cost, Decimal('0'),
         f'Salvage inventory — {item.item_code} (carrying value)'),
        (rec, Decimal('0'), cost,
         f'Recovery against claim {item.claim_number or "(no claim)"} — {item.item_code}'),
    ])
    je.post(user=user or item.received_by or item.created_by, _allow_direct=True)

    from django.utils import timezone as _tz
    SalvageItem.objects.filter(pk=item.pk).update(
        intake_journal_entry=je,
        intake_posted_at=_tz.now(),
    )
    item.intake_journal_entry = je
    return je


@transaction.atomic
def post_void_to_gl(item: SalvageItem, *, reason: str = '', user: Optional[User] = None):
    """Post the REVERSING JE when a salvage row is voided.

    Kgosi Seboko asked (2026-09-18) for a way to correct data-entry mistakes.
    Rather than DELETE the row (which would leave the intake JE dangling), the
    void action posts:

      DR  105004 Salvages & Recoveries    (item.cost_basis)
      CR  1320 Salvage Inventory          (item.cost_basis)

    which is the mirror of `post_intake_to_gl`. Idempotent — a second call
    on the same voided item is a no-op. Skips when nothing was ever intake-
    posted or cost_basis is zero.
    """
    from ledger.models import JournalEntry

    intake = item.intake_journal_entry
    if intake is None:
        return None
    cost = Decimal(item.cost_basis or 0)
    if cost <= 0:
        return None

    already = JournalEntry.objects.filter(
        source_type='salvage_void',
        notes__contains=f'item {item.pk}',
    ).first()
    if already:
        return already

    accts = _resolve_accounts([_inventory_account_code(), _recovery_account_code()])
    inv = accts[_inventory_account_code()]
    rec = accts[_recovery_account_code()]
    if not inv or not rec:
        log.warning(
            'Salvage void GL skipped — CoA missing %s/%s; item=%s left with intake JE.',
            _inventory_account_code(), _recovery_account_code(), item.pk,
        )
        return None

    from django.utils import timezone as _tz
    je = JournalEntry.objects.create(
        entry_number   = '',
        entry_date     = _tz.localdate(),
        description    = (f'Salvage void — {item.item_code} '
                          f'{(item.part_name or "")[:60]}')[:500],
        source_type    = 'salvage_void',
        journal_type   = 'general',
        currency_code  = _bwp_currency(),
        exchange_rate  = Decimal('1.00000000'),
        company        = item.company,
        created_by     = user or item.created_by,
        status         = JournalEntry.Status.DRAFT,
        notes          = (f'Reversal of intake JE {intake.pk} on void of '
                          f'item {item.pk}. Reason: {reason or "(none)"}.')[:1000],
    )
    _post_lines(je, [
        (rec, cost, Decimal('0'),
         f'Reversal of recovery — {item.item_code} (voided)'),
        (inv, Decimal('0'), cost,
         f'Release salvage inventory — {item.item_code} (voided)'),
    ])
    je.post(user=user or item.created_by, _allow_direct=True)
    return je


@transaction.atomic
def write_down_to_nrv(
    item: SalvageItem,
    new_nrv: Decimal,
    *,
    user: Optional[User] = None,
    reason: str = '',
):
    """Impair a salvage item to net realisable value.

    CFO directive 2026-05-24 (Track-B audit, IAS 2 / IFRS 17 alignment).

    Books:
      DR  Loss on salvage (P&L)        (old_cost - new_nrv)
      CR  Salvage Inventory (BS)       (old_cost - new_nrv)

    Updates `cost_basis` to new_nrv, accumulates `impaired_total_bwp`,
    stamps `impaired_at`. No-op if new_nrv >= current cost_basis
    (IAS 2 only impairs writedowns, not reversals beyond original
    carrying amount).

    Returns the posted JournalEntry, or None if skipped / accounts
    missing.
    """
    from ledger.models import JournalEntry

    new_nrv = Decimal(new_nrv or 0)
    old_cost = Decimal(item.cost_basis or 0)
    delta = old_cost - new_nrv
    if delta <= 0:
        return None  # NRV is at or above carrying amount — nothing to impair.

    accts = _resolve_accounts([_inventory_account_code(),
                               _gain_loss_account_code()])
    inv = accts[_inventory_account_code()]
    pnl = accts[_gain_loss_account_code()]
    if not inv or not pnl:
        log.warning(
            'Salvage NRV writedown skipped — CoA missing %s/%s; item=%s.',
            _inventory_account_code(), _gain_loss_account_code(), item.pk,
        )
        return None

    from django.utils import timezone as _tz
    je = JournalEntry.objects.create(
        entry_number   = '',
        entry_date     = _tz.localdate(),
        description    = (f'Salvage NRV writedown — {item.item_code} '
                          f'{(item.part_name or "")[:60]}')[:500],
        source_type    = 'salvage_impairment',
        journal_type   = 'general',
        currency_code  = _bwp_currency(),
        exchange_rate  = Decimal('1.00000000'),
        company        = item.company,
        created_by     = user or item.received_by or item.created_by,
        status         = JournalEntry.Status.DRAFT,
        notes          = (f'NRV writedown of P {delta:,.2f} on salvage item '
                          f'{item.pk}. Reason: {reason or "(none)"}.'
                          f' Carrying value {old_cost} -> {new_nrv}.')[:1000],
    )
    _post_lines(je, [
        (pnl, delta, Decimal('0'),
         f'Loss on salvage NRV writedown — {item.item_code}'),
        (inv, Decimal('0'), delta,
         f'Release of inventory — {item.item_code} (impair {delta})'),
    ])
    je.post(user=user or item.received_by or item.created_by, _allow_direct=True)

    SalvageItem.objects.filter(pk=item.pk).update(
        cost_basis=new_nrv,
        impaired_at=_tz.now(),
        impaired_total_bwp=Decimal(item.impaired_total_bwp or 0) + delta,
    )
    item.cost_basis = new_nrv
    item.impaired_at = _tz.now()
    item.impaired_total_bwp = Decimal(item.impaired_total_bwp or 0) + delta
    return je


@transaction.atomic
def post_sale_to_gl(sale: Sale, *, user: Optional[User] = None):
    """Post a Sale: release inventory + recognise gain / loss.

      DR  280001 Bank / cash             (sale.sale_price)
      CR  1320 Salvage Inventory         (item.cost_basis)
      GAIN  → CR 400010 Gain on Salvage  (sale_price - cost_basis)
      LOSS  → DR 400010 Loss on Salvage  (cost_basis - sale_price)

    Idempotent: if Sale.journal_entry FK is set, returns the existing JE.
    If the item was never intake-posted (cost_basis = 0), we degrade
    gracefully to the old Dr Cash / Cr Income behaviour so legacy items
    don't block the close.
    """
    from ledger.models import JournalEntry

    if sale.journal_entry_id:
        return sale.journal_entry

    item = sale.item
    proceeds = Decimal(sale.sale_price or 0)
    carrying = Decimal(item.cost_basis or 0)

    accts = _resolve_accounts([
        _cash_account_code(), _inventory_account_code(),
        _recovery_account_code(), _gain_loss_account_code(),
    ])
    cash  = accts[_cash_account_code()]
    inv   = accts[_inventory_account_code()]
    gl    = accts[_gain_loss_account_code()]
    rec   = accts[_recovery_account_code()]
    if not cash or not gl:
        log.warning(
            'Salvage sale GL skipped — CoA missing cash=%s gain/loss=%s; sale=%s.',
            _cash_account_code(), _gain_loss_account_code(), sale.pk,
        )
        return None

    journal_type = 'general'
    if hasattr(JournalEntry, 'JournalType') and hasattr(JournalEntry.JournalType, 'SALES'):
        journal_type = JournalEntry.JournalType.SALES

    je = JournalEntry.objects.create(
        entry_number   = '',
        entry_date     = sale.sale_date,
        description    = (f'Salvage sale — {item.item_code} '
                          f'{(item.part_name or "")[:60]} to {sale.buyer_name[:40]}')[:500],
        source_type    = 'salvage_sale',
        journal_type   = journal_type,
        currency_code  = _bwp_currency(),
        exchange_rate  = Decimal('1.00000000'),
        company        = item.company,
        created_by     = user or sale.sold_by,
        status         = JournalEntry.Status.DRAFT,
        notes          = (f'Auto-posted from salvage.Sale {sale.pk}; '
                          f'buyer={sale.buyer_name}, payment_method={sale.payment_method}, '
                          f'ref={sale.payment_ref}; carrying={carrying}, proceeds={proceeds}')[:1000],
    )

    lines = []
    # Cash side — always.
    lines.append((cash, proceeds, Decimal('0'),
                  f'Salvage proceeds — {sale.buyer_name[:80]}'))

    if carrying > 0 and inv:
        # Proper accounting path: release inventory + book gain/loss.
        lines.append((inv, Decimal('0'), carrying,
                      f'Release salvage inventory — {item.item_code}'))
        delta = proceeds - carrying
        if delta > 0:
            lines.append((gl, Decimal('0'), delta,
                          f'Gain on salvage — {item.item_code}'))
        elif delta < 0:
            lines.append((gl, -delta, Decimal('0'),
                          f'Loss on salvage — {item.item_code}'))
        # delta == 0: no gain/loss line needed; entry balances at proceeds.
    else:
        # Legacy fallback — no carrying value on BS → all proceeds hit
        # the contra-claims (or gain) account. Logs a warning so the
        # close team can backfill cost_basis later.
        log.warning(
            'Salvage sale fallback (no cost_basis) — sale=%s item=%s, '
            'booking full proceeds to gain/loss.', sale.pk, item.pk,
        )
        lines.append((gl, Decimal('0'), proceeds,
                      f'Salvage proceeds (no carrying) — {item.item_code}'))

    _post_lines(je, lines)
    je.post(user=user or sale.sold_by, _allow_direct=True)

    Sale.objects.filter(pk=sale.pk).update(journal_entry=je)
    sale.journal_entry = je
    return je


# ---------------------------------------------------------------------------
# Threshold-driven approval auto-creation
# ---------------------------------------------------------------------------
def maybe_create_approval_for_quote(
    quote: BuyerQuote, *, user: User,
) -> Optional[SalvageApproval]:
    """Called from BuyerQuoteViewSet.review() after status flip. Creates a
    PENDING SalvageApproval(kind=quote_accept) when:

      - quote.status == 'accepted', AND
      - quote.offered_price > settings.SALVAGE_APPROVAL_THRESHOLD_BWP

    Returns the new SalvageApproval row, or None.
    """
    if quote.status != BuyerQuote.Status.ACCEPTED:
        return None

    threshold = _approval_threshold_bwp()
    if quote.offered_price <= threshold:
        return None

    existing = SalvageApproval.objects.filter(
        buyer_quote=quote, kind=SalvageApproval.Kind.QUOTE_ACCEPT,
    ).first()
    if existing:
        return existing

    return SalvageApproval.objects.create(
        kind             = SalvageApproval.Kind.QUOTE_ACCEPT,
        item             = quote.item,
        buyer_quote      = quote,
        requested_by     = user,
        status           = SalvageApproval.Status.PENDING,
        requested_amount = quote.offered_price,
        threshold_amount = threshold,
        notes            = (
            f'Auto-created: quote acceptance of P{quote.offered_price} for '
            f'{quote.item.item_code} exceeds approval threshold P{threshold}.'
        ),
    )


def maybe_create_approval_for_sale(
    sale: Sale, *, user: User,
) -> Optional[SalvageApproval]:
    """Called from SaleViewSet.perform_create after the sale is saved.
    Creates a PENDING SalvageApproval(kind=sale) when:

      - sale.sale_price < sale.item.reserve_price, OR
      - sale.sale_price > settings.SALVAGE_APPROVAL_THRESHOLD_BWP

    Returns the new SalvageApproval, or None.
    """
    item = sale.item
    threshold = _approval_threshold_bwp()
    below_reserve = (item.reserve_price or Decimal('0')) > Decimal('0') and \
                    sale.sale_price < item.reserve_price
    above_threshold = sale.sale_price > threshold

    if not (below_reserve or above_threshold):
        return None

    existing = SalvageApproval.objects.filter(
        sale=sale, kind=SalvageApproval.Kind.SALE,
    ).first()
    if existing:
        return existing

    reason_bits = []
    if below_reserve:
        reason_bits.append(
            f'sale_price P{sale.sale_price} below reserve P{item.reserve_price}'
        )
    if above_threshold:
        reason_bits.append(
            f'sale_price P{sale.sale_price} above threshold P{threshold}'
        )

    return SalvageApproval.objects.create(
        kind             = SalvageApproval.Kind.SALE,
        item             = item,
        sale             = sale,
        buyer_quote      = sale.buyer_quote,
        requested_by     = user,
        status           = SalvageApproval.Status.PENDING,
        requested_amount = sale.sale_price,
        threshold_amount = item.reserve_price if below_reserve else threshold,
        notes            = (
            f'Auto-created — ' + '; '.join(reason_bits) + '.'
        ),
    )


# ---------------------------------------------------------------------------
# Reserve auto-suggest (CFO directive 2026-05-24)
# ---------------------------------------------------------------------------
_DEFAULT_RECOVERY_PCT = Decimal('0.30')


def suggest_reserve(item: SalvageItem) -> Decimal:
    """Suggest a reserve price for `item`.

    Algorithm:
      1. Look up the gross claim amount linked to `item.claim_number`.
         Tries `claims.Salvage` (closest analogue — `estimated_value`)
         then `claims.Subrogation.claim_paid_amount` as a fallback.
         If `claims.Claim` model is ever added, plug it in here.
      2. Multiply by the category's `expected_recovery_pct`, or the
         module-level default (0.30) when no category is set.
      3. Return a 2dp Decimal. Returns 0 when no gross can be resolved
         (caller can show "no suggestion available").

    NEVER mutates the item. The caller decides whether to apply.
    """
    if not (item.claim_number or '').strip():
        return Decimal('0.00')

    pct = _DEFAULT_RECOVERY_PCT
    if item.category_id:
        # Pull via FK so we don't depend on the attribute being prefetched.
        from .models import PartCategory
        cat = PartCategory.objects.filter(pk=item.category_id).only(
            'expected_recovery_pct',
        ).first()
        if cat is not None and cat.expected_recovery_pct is not None:
            pct = Decimal(cat.expected_recovery_pct)

    gross = _resolve_claim_gross(item.claim_number)
    if gross is None or gross <= 0:
        return Decimal('0.00')

    return (gross * pct).quantize(Decimal('0.01'))


def _resolve_claim_gross(claim_reference: str) -> Optional[Decimal]:
    """Return the gross claim amount for `claim_reference` or None.

    Looks up in this order (most specific first):
      - claims.Salvage.estimated_value (same claim_reference)
      - claims.Subrogation.claim_paid_amount

    Wrapped in a try/except so a future schema drift in claims/ doesn't
    break the salvage suggest endpoint.
    """
    try:
        from claims.models import Salvage as ClaimsSalvage, Subrogation
    except Exception:                                          # noqa: BLE001
        return None

    try:
        sal = ClaimsSalvage.objects.filter(
            claim_reference=claim_reference,
        ).order_by('-created_at').first()
        if sal and sal.estimated_value:
            return Decimal(sal.estimated_value)
    except Exception:                                          # noqa: BLE001
        log.warning('suggest_reserve: claims.Salvage lookup failed', exc_info=True)

    try:
        sub = Subrogation.objects.filter(
            claim_reference=claim_reference,
        ).order_by('-created_at').first()
        if sub and sub.claim_paid_amount:
            return Decimal(sub.claim_paid_amount)
    except Exception:                                          # noqa: BLE001
        log.warning('suggest_reserve: claims.Subrogation lookup failed', exc_info=True)

    return None
