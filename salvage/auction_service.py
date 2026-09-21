"""salvage/auction_service.py — domain operations for SalvageAuction + Bid.

Three operations:

  schedule_auction(item, opens_at, closes_at, min_bid, user)
    → SalvageAuction in `scheduled` status.

  place_bid(auction, buyer, amount)
    → Bid; validates amount > current high (or >= min_bid if first bid),
      auction is live (or schedule promotes on first bid after opens_at),
      and not withdrawn. Updates auction.current_high_bid pointer.

  close_auction(auction)
    → marks the auction closed. If the winning (highest non-withdrawn)
      bid clears `item.reserve_price`, auto-creates an `accepted`
      BuyerQuote from the bidder's BuyerQuote row at the winning amount.
      If below reserve, routes through the existing SalvageApproval
      workflow (kind=SALE) for CFO/EXCO review.

All three functions are transactional. Errors raise `ValueError` with a
human-readable message that the view layer can surface.
"""
from __future__ import annotations

import logging
from datetime import datetime
from decimal import Decimal
from typing import Optional

from django.contrib.auth.models import User
from django.db import transaction
from django.utils import timezone

from .auction_models import Bid, SalvageAuction
from .models import BuyerQuote, SalvageApproval, SalvageItem

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Schedule
# ---------------------------------------------------------------------------
@transaction.atomic
def schedule_auction(
    item: SalvageItem,
    opens_at: datetime,
    closes_at: datetime,
    min_bid: Decimal,
    user: Optional[User] = None,
) -> SalvageAuction:
    """Create a new SCHEDULED auction for `item`.

    Validation:
      - closes_at must be strictly after opens_at.
      - min_bid must be >= 0.
      - item must not already have a live/scheduled auction (only one
        in-flight auction per item — finished auctions don't block).
    """
    if closes_at <= opens_at:
        raise ValueError('closes_at must be after opens_at.')
    if Decimal(min_bid or 0) < 0:
        raise ValueError('min_bid must be >= 0.')

    in_flight_qs = SalvageAuction.objects.filter(
        item=item,
        status__in=[SalvageAuction.Status.SCHEDULED, SalvageAuction.Status.LIVE],
    )
    if in_flight_qs.exists():
        raise ValueError(
            f'Item {item.item_code} already has an in-flight auction.'
        )

    return SalvageAuction.objects.create(
        item       = item,
        opens_at   = opens_at,
        closes_at  = closes_at,
        min_bid    = Decimal(min_bid or 0),
        created_by = user,
        status     = SalvageAuction.Status.SCHEDULED,
    )


# ---------------------------------------------------------------------------
# Place bid
# ---------------------------------------------------------------------------
@transaction.atomic
def place_bid(
    auction: SalvageAuction,
    buyer: BuyerQuote,
    amount: Decimal,
) -> Bid:
    """Place a Bid against `auction`.

    Validates:
      - amount > current_high.amount, OR (no high yet) amount >= min_bid.
      - auction is LIVE; if it's SCHEDULED and we're past opens_at,
        promote it to LIVE first.
      - auction has not closed (closes_at > now AND status != CLOSED).
      - buyer.item == auction.item (the BuyerQuote was raised on this
        item — prevents cross-item identity mix-ups).

    Returns the saved Bid; updates `auction.current_high_bid` to point
    to it.
    """
    amount = Decimal(amount or 0)
    now = timezone.now()

    if auction.status == SalvageAuction.Status.CANCELLED:
        raise ValueError('Auction is cancelled.')
    if auction.status == SalvageAuction.Status.CLOSED or now >= auction.closes_at:
        raise ValueError('Auction is closed.')
    if now < auction.opens_at:
        raise ValueError('Auction is not open yet.')

    if buyer.item_id != auction.item_id:
        raise ValueError(
            'Buyer (BuyerQuote) is for a different item than the auction.'
        )

    high = None
    if auction.current_high_bid_id:
        # Refresh from DB in case current_high was withdrawn.
        high = Bid.objects.filter(
            pk=auction.current_high_bid_id, withdrawn=False,
        ).first()
    if high is None:
        # Fall back to the real highest non-withdrawn bid (current_high
        # pointer may be stale if a bid was withdrawn).
        high = (
            Bid.objects.filter(auction=auction, withdrawn=False)
            .order_by('-amount', '-placed_at')
            .first()
        )

    if high is None:
        if amount < Decimal(auction.min_bid or 0):
            raise ValueError(
                f'First bid must be at least min_bid ({auction.min_bid}).'
            )
    else:
        if amount <= Decimal(high.amount):
            raise ValueError(
                f'Bid {amount} must be greater than current high '
                f'{high.amount}.'
            )

    # Promote SCHEDULED → LIVE on first bid after opens_at.
    if auction.status == SalvageAuction.Status.SCHEDULED:
        auction.status = SalvageAuction.Status.LIVE

    bid = Bid.objects.create(
        auction = auction,
        buyer   = buyer,
        amount  = amount,
    )

    auction.current_high_bid = bid
    auction.save(update_fields=['status', 'current_high_bid', 'updated_at'])
    return bid


# ---------------------------------------------------------------------------
# Close auction
# ---------------------------------------------------------------------------
@transaction.atomic
def close_auction(
    auction: SalvageAuction,
    *,
    user: Optional[User] = None,
):
    """Close an auction and resolve the outcome.

    Only acts when `timezone.now() > closes_at`. Returns a dict:

        {
          'auction': SalvageAuction,
          'winning_bid': Bid | None,
          'accepted_quote': BuyerQuote | None,   # set if winner >= reserve
          'approval': SalvageApproval | None,    # set if winner < reserve
        }

    If there are no non-withdrawn bids, the auction is just marked
    CLOSED and the outcome dict has None for the other fields.
    """
    now = timezone.now()
    if now <= auction.closes_at:
        raise ValueError(
            f'Auction has not yet reached its close time ({auction.closes_at}).'
        )

    if auction.status == SalvageAuction.Status.CLOSED:
        # Idempotent re-call — just hand back the existing outcome.
        winning = (
            Bid.objects.filter(auction=auction, withdrawn=False)
            .order_by('-amount', '-placed_at')
            .first()
        )
        return {
            'auction':        auction,
            'winning_bid':    winning,
            'accepted_quote': None,
            'approval':       None,
        }

    if auction.status == SalvageAuction.Status.CANCELLED:
        raise ValueError('Auction is cancelled — cannot close.')

    winning = (
        Bid.objects.filter(auction=auction, withdrawn=False)
        .select_related('buyer', 'auction__item')
        .order_by('-amount', '-placed_at')
        .first()
    )

    auction.status = SalvageAuction.Status.CLOSED
    auction.current_high_bid = winning
    auction.save(update_fields=['status', 'current_high_bid', 'updated_at'])

    if winning is None:
        return {
            'auction':        auction,
            'winning_bid':    None,
            'accepted_quote': None,
            'approval':       None,
        }

    item = auction.item
    reserve = Decimal(item.reserve_price or 0)
    bid_amount = Decimal(winning.amount)

    accepted_quote = None
    approval = None

    if reserve <= 0 or bid_amount >= reserve:
        # Auto-accept: create a new BuyerQuote sourced from the
        # winning bidder, status=accepted, at the winning amount.
        src = winning.buyer
        accepted_quote = BuyerQuote.objects.create(
            item          = item,
            buyer_name    = src.buyer_name,
            buyer_email   = src.buyer_email,
            buyer_phone   = src.buyer_phone,
            buyer_company = src.buyer_company,
            offered_price = bid_amount,
            message       = (
                f'Auto-accepted from auction {auction.pk} '
                f'(winning bid).'
            ),
            status        = BuyerQuote.Status.ACCEPTED,
            reviewed_by   = user,
            reviewed_at   = now,
            review_notes  = f'Auction {auction.pk} winning bid.',
        )
        # Flip the item to QUOTED so it isn't double-sold.
        SalvageItem.objects.filter(pk=item.pk).update(
            status=SalvageItem.Status.QUOTED,
        )
    else:
        # Below reserve — route to existing SalvageApproval workflow.
        if user is None:
            raise ValueError(
                'close_auction needs `user` to raise a below-reserve '
                'SalvageApproval row.'
            )
        approval = SalvageApproval.objects.create(
            kind             = SalvageApproval.Kind.SALE,
            item             = item,
            buyer_quote      = winning.buyer,
            requested_by     = user,
            status           = SalvageApproval.Status.PENDING,
            requested_amount = bid_amount,
            threshold_amount = reserve,
            notes            = (
                f'Auction {auction.pk} winning bid P{bid_amount} is below '
                f'reserve P{reserve}. Routed for CFO/EXCO approval.'
            ),
        )

    return {
        'auction':        auction,
        'winning_bid':    winning,
        'accepted_quote': accepted_quote,
        'approval':       approval,
    }
