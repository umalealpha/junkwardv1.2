"""salvage/auction_models.py — SalvageAuction + Bid.

CFO directive (2026-05-24): the salvage portal needs a structured auction
workflow on top of the existing BuyerQuote flow. Buyers can place
competing bids against a `SalvageItem` between `opens_at` and
`closes_at`; the highest valid bid at close is rolled into an accepted
`BuyerQuote` (when it clears the item's `reserve_price`) or routed to
the existing `SalvageApproval` workflow (when it falls below reserve).

Kept in a dedicated module so the `models.py` core stays readable; the
file is imported from `salvage/models.py` so Django picks the models up
without needing app-config changes.

NOTE: no schema is created here — see `salvage/migrations/0005_auction.py`.
"""
from __future__ import annotations

from decimal import Decimal

from django.contrib.auth.models import User
from django.db import models

from core.models import BaseModel


class SalvageAuction(BaseModel):
    """A timed auction window for a single SalvageItem.

    Lifecycle:
        scheduled  → live (when opens_at hits)
        live       → closed (when closes_at hits AND close_auction() runs)
        scheduled / live → cancelled (manual)

    `current_high_bid` is a denormalised pointer maintained by the
    auction service for cheap lookup; the canonical source remains the
    Bid table (highest non-withdrawn `amount`).
    """

    class Status(models.TextChoices):
        SCHEDULED = 'scheduled', 'Scheduled'
        LIVE      = 'live',      'Live'
        CLOSED    = 'closed',    'Closed'
        CANCELLED = 'cancelled', 'Cancelled'

    item             = models.ForeignKey(
                           'salvage.SalvageItem', on_delete=models.PROTECT,
                           related_name='auctions',
                       )
    opens_at         = models.DateTimeField()
    closes_at        = models.DateTimeField()
    min_bid          = models.DecimalField(
                           max_digits=18, decimal_places=2,
                           default=Decimal('0'),
                           help_text='Minimum opening bid amount (BWP).',
                       )
    # Denormalised pointer to the highest Bid; kept in sync by the
    # auction service. The FK target lives in the same module below — we
    # use the lazy string form to avoid a cyclic import order issue.
    current_high_bid = models.ForeignKey(
                           'salvage.Bid', on_delete=models.SET_NULL,
                           null=True, blank=True, related_name='+',
                           help_text='Pointer to the highest non-withdrawn bid.',
                       )
    status           = models.CharField(
                           max_length=12, choices=Status.choices,
                           default=Status.SCHEDULED,
                       )
    created_by       = models.ForeignKey(
                           User, on_delete=models.SET_NULL,
                           null=True, blank=True,
                           related_name='salvage_auctions_created',
                       )

    class Meta(BaseModel.Meta):
        ordering = ['-created_at']
        indexes  = [
            models.Index(fields=['status']),
            models.Index(fields=['item']),
            models.Index(fields=['closes_at']),
        ]

    def __str__(self):
        return f"Auction {self.item_id} [{self.status}]"


class Bid(BaseModel):
    """A single bid placed against a SalvageAuction.

    `buyer` is the buyer-side FK. Per the directive, we reuse the
    BuyerQuote model's buyer source — there is no standalone Buyer
    table, so we point at BuyerQuote (the row that captured the buyer's
    name/email/phone when they first registered interest). A buyer with
    no prior quote can be onboarded with a minimal "registration"
    BuyerQuote row.

    `withdrawn=True` removes the bid from auction evaluation without
    deleting history — important for audit + dispute resolution.
    """

    auction   = models.ForeignKey(
                    SalvageAuction, on_delete=models.PROTECT,
                    related_name='bids',
                )
    buyer     = models.ForeignKey(
                    'salvage.BuyerQuote', on_delete=models.PROTECT,
                    related_name='bids',
                    help_text='Buyer identity sourced from BuyerQuote row.',
                )
    amount    = models.DecimalField(max_digits=18, decimal_places=2)
    placed_at = models.DateTimeField(auto_now_add=True)
    withdrawn = models.BooleanField(default=False)

    class Meta(BaseModel.Meta):
        ordering = ['-amount', '-placed_at']
        indexes  = [
            models.Index(fields=['auction']),
            models.Index(fields=['withdrawn']),
        ]

    def __str__(self):
        return f"Bid {self.amount} on {self.auction_id}"
