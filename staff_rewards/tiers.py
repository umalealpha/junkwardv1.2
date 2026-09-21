"""staff_rewards/tiers.py — staff tier ladder (Bronze → Diamond).

Distinct from the customer rewards ladder (rewards.RewardMember uses
Bronze/Silver/Gold/Platinum on different thresholds). Spec section 5:

    Bronze    0    – 999      (Entry)
    Silver    1,000 – 2,499
    Gold      2,500 – 4,499
    Platinum  5,000 – 7,499
    Diamond   7,500+          (Top contributors)
"""
from __future__ import annotations

# (label, lower_inclusive, upper_inclusive_or_None) ordered low → high.
STAFF_TIERS = [
    ('Bronze',   0,    999),
    ('Silver',   1000, 2499),
    ('Gold',     2500, 4499),
    ('Platinum', 5000, 7499),
    ('Diamond',  7500, None),
]


def tier_for(points: int) -> str:
    """Return the tier label for a points total."""
    p = int(points or 0)
    if p < 0:
        p = 0
    for label, lo, hi in STAFF_TIERS:
        if hi is None:
            if p >= lo:
                return label
        elif lo <= p <= hi:
            return label
    return STAFF_TIERS[0][0]


def progress_to_next(points: int) -> dict:
    """Progress toward the next tier.

    Returns {tier, next_tier, points, next_at, to_next, pct} where:
      next_tier / next_at are None at the top (Diamond),
      pct is 0-100 progress through the CURRENT band.
    """
    p = int(points or 0)
    if p < 0:
        p = 0
    current = tier_for(p)
    for i, (label, lo, hi) in enumerate(STAFF_TIERS):
        is_current = (hi is None and p >= lo) or (hi is not None and lo <= p <= hi)
        if not is_current:
            continue
        if hi is None:  # Diamond — top tier
            return {
                'tier':      label,
                'next_tier': None,
                'points':    p,
                'next_at':   None,
                'to_next':   0,
                'pct':       100,
            }
        next_label, next_lo, _ = STAFF_TIERS[i + 1]
        band_size = next_lo - lo
        in_band = p - lo
        pct = int(round((in_band / band_size) * 100)) if band_size else 0
        return {
            'tier':      label,
            'next_tier': next_label,
            'points':    p,
            'next_at':   next_lo,
            'to_next':   max(next_lo - p, 0),
            'pct':       max(0, min(pct, 100)),
        }
    return {
        'tier': current, 'next_tier': STAFF_TIERS[1][0], 'points': p,
        'next_at': STAFF_TIERS[1][1], 'to_next': STAFF_TIERS[1][1] - p, 'pct': 0,
    }
