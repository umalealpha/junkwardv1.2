"""genric/cession.py — the six-step cession, in the treaty's exact order.

The build prompt fixes both the steps AND their order, and the order is not
cosmetic: taking the ceding commission off before the quota share, or stripping
VAT after ceding, both produce a different invoice total from the same bank
figures. The July 2026 worked example is the acceptance test —

    1  GWP excl VAT = confirmed GWP incl VAT ÷ 1.15        R8,006.09
    2  Less collection charges (R0 unless advised)         R0.00
    3  Net premium base                                    R8,006.09
    4  Ceded at 90%                                        R7,205.48
    5  Less ceding commission 21.5%                       (R1,549.18)
    6  NET REINSURANCE PREMIUM DUE (invoice total)         R5,656.30

— and it is pinned in ``tests/test_cession.py``. Every step is quantised HALF
UP as it is produced, because the human re-keying the invoice adds the printed
steps, not the full-precision ones.

Step 5 is 21.5%, from the signed treaty. The older MOU said 10%.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

from . import constants as K
from .money import q2


@dataclass(frozen=True)
class CessionStep:
    number: int
    label: str
    amount: Decimal
    basis: str          # plain words: what this figure was calculated from


@dataclass(frozen=True)
class Cession:
    confirmed_gwp_incl_vat: Decimal
    gwp_excl_vat: Decimal
    collection_charges: Decimal
    net_premium_base: Decimal
    ceded: Decimal
    ceding_commission: Decimal
    net_reinsurance_premium_due: Decimal
    retained: Decimal
    steps: list = field(default_factory=list)

    @property
    def invoice_total(self) -> Decimal:
        return self.net_reinsurance_premium_due


def compute_cession(confirmed_gwp_incl_vat,
                    collection_charges=Decimal('0.00')) -> Cession:
    """Run the six steps. ``confirmed_gwp_incl_vat`` comes from the BANK."""
    K.assert_treaty_self_consistent()

    gwp_incl = q2(confirmed_gwp_incl_vat)
    charges = q2(collection_charges)

    # 1 — strip SA VAT. Divide by (1 + rate); do NOT multiply by a hard-coded
    #     0.8696, which is a rounded reciprocal and drifts at scale.
    gwp_excl = q2(gwp_incl / (Decimal('1') + K.SA_VAT_RATE))

    # 2/3 — collection charges are R0 unless Finance advises otherwise.
    net_base = q2(gwp_excl - charges)

    # 4 — quota share to GENRIC.
    ceded = q2(net_base * K.QUOTA_SHARE_CEDED)

    # 5 — ceding commission GENRIC pays back, on the CEDED amount (not on the
    #     base, and not on the VAT-inclusive figure).
    commission = q2(ceded * K.CEDING_COMMISSION_RATE)

    # 6 — what we invoice.
    net_due = q2(ceded - commission)

    # Retained is reported alongside so the pack shows the whole book, and so a
    # ceded + retained that no longer equals the base is visible on the face of
    # the report rather than discovered at year end.
    retained = q2(net_base * K.RETENTION)

    steps = [
        CessionStep(1, 'GWP excl VAT', gwp_excl,
                    f'confirmed GWP incl VAT {gwp_incl} ÷ {Decimal("1") + K.SA_VAT_RATE} '
                    f'(SA VAT {K.SA_VAT_RATE:.0%})'),
        CessionStep(2, 'Less collection charges', -charges,
                    'R0.00 unless advised by Finance'),
        CessionStep(3, 'Net premium base', net_base,
                    'step 1 less step 2'),
        CessionStep(4, f'Ceded at {K.QUOTA_SHARE_CEDED:.0%}', ceded,
                    f'net premium base × {K.QUOTA_SHARE_CEDED}'),
        CessionStep(5, f'Less ceding commission {K.CEDING_COMMISSION_RATE:.1%}', -commission,
                    f'ceded × {K.CEDING_COMMISSION_RATE} (signed treaty; the older MOU said 10%)'),
        CessionStep(6, 'NET REINSURANCE PREMIUM DUE', net_due,
                    'step 4 less step 5 — this is the invoice total'),
    ]

    return Cession(
        confirmed_gwp_incl_vat=gwp_incl,
        gwp_excl_vat=gwp_excl,
        collection_charges=charges,
        net_premium_base=net_base,
        ceded=ceded,
        ceding_commission=commission,
        net_reinsurance_premium_due=net_due,
        retained=retained,
        steps=steps,
    )
