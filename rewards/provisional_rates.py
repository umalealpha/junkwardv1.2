"""
PROVISIONAL rates for the Nexus retention features — TEST VALUES ONLY.

═══════════════════════════════════════════════════════════════════════════════
  NOT SIGNED OFF. NOT ACTUARIALLY REVIEWED. NOT FOR CUSTOMER EYES.
  CFO 16-Aug-2026: "come up with something for now, it's only for testing
  purposes, we will change it later."
═══════════════════════════════════════════════════════════════════════════════

Every number here is a placeholder chosen so the features can be BUILT and
TESTED before the real rates exist ("illustrative; final % is a CFO decision").
The PREMIUM discount is gated by `customer_views.premium_discount_pct`, which
returns 0 while PROVISIONAL is True and, when live, is driven ONLY by non-health
factors — never the wellness tier (health data must not set insurance pricing).

WHY THIS FILE EXISTS SEPARATELY: so the real sign-off is a single, reviewable
diff to one file — not a hunt through the codebase for scattered magic numbers.

THE HARD RULE (Fable 5, step 3):
    No pula discount figure may render on a REAL customer's screen until a
    CFO-signed discount table exists. A screenshotted promise of "P720 a year"
    is a contractual problem, not a marketing one.
    => `PROVISIONAL = True` below MUST gate every customer-facing render.
       While it is True, show the band ("Excellent") and the points, never the
       pula. Test accounts may see the pula behind the same flag.
"""

# Flip to False ONLY when a signed discount table replaces the numbers below.
# While True: no pula figure reaches a real customer.
PROVISIONAL = True


# ═════════════════════════════════════════════════════════════════════════════
# HARD COMPLIANCE LINE (CFO 16-Aug-2026): Google Play forbids using health/fitness
# data to set insurance pricing. Therefore:
#   * HEALTH-derived points (Health Connect / Apple Health STEPS, wellness scan)
#     earn REWARDS ONLY — perks, vouchers, prizes. They must NEVER feed a premium
#     discount. Do not add any health signal to the discount functions below.
#   * The PREMIUM DISCOUNT is driven ONLY by non-health factors: driving score,
#     claims-free years, tenure.
# A step count must not be reachable from cap_total() or discount_for_score().
# ═════════════════════════════════════════════════════════════════════════════

# ─────────────────────────────────────────────────────────────────────────────
# 1. SAFE-DRIVING DISCOUNT  (Click & Drive score -> % off motor renewal)
#    Driving behaviour is NOT health data — this discount is allowed.
# ─────────────────────────────────────────────────────────────────────────────
# Ceiling deliberately matches the existing wellness ladder's top (15%) so the
# two schemes read as one system rather than two competing promises.
# Bands are wide on purpose: a customer must not see their discount flip because
# of one bad junction.
SAFE_DRIVING_DISCOUNT = {
    # min_score_inclusive : discount %
    90: 15,   # Excellent
    80: 10,   # Very good
    70: 5,    # Good
    60: 0,    # Fair      — no discount, but no penalty either
    0:  0,    # Needs work — coaching message, never a public "bad driver" label
}

# Anti-gaming: a discount must be earned over real distance, not two careful
# trips. Below either threshold the score is shown but the discount is 0.
MIN_TRIPS_FOR_DISCOUNT = 10
MIN_KM_FOR_DISCOUNT = 300

# The score used for renewal is the average over this window, not the last trip.
DISCOUNT_SCORE_WINDOW_DAYS = 90


# ─────────────────────────────────────────────────────────────────────────────
# 2. TOTAL DISCOUNT CAP  — the number that actually protects the loss ratio
# ─────────────────────────────────────────────────────────────────────────────
# Wellness tier, safe driving and any existing no-claims bonus can otherwise
# stack into a premium that no longer covers the risk.
#
# *** AGREED BY THE CFO, 16-Aug-2026: 20%. This one is NOT provisional. ***
# It is the ceiling that protects the loss ratio. Do not raise it without the
# CFO, and it still warrants an actuarial sanity-check before go-live.
MAX_TOTAL_DISCOUNT_PCT = 20
DISCOUNTS_STACK = True   # if False, the customer simply gets the best single one


# ─────────────────────────────────────────────────────────────────────────────
# 3. REFERRALS
# ─────────────────────────────────────────────────────────────────────────────
# Paid on a policy that ACTIVATES and STICKS — never on a signup. Paying at
# signup invites fake referrals; paying at activation-plus-a-waiting-period
# means the reward only lands on business you actually kept.
REFERRAL_POINTS_REFERRER = 500
REFERRAL_POINTS_FRIEND = 500
REFERRAL_QUALIFY_AFTER_DAYS = 60      # policy must still be active after this
REFERRAL_MAX_PER_MEMBER_PER_YEAR = 5  # blunt fraud cap
REFERRAL_SELF_REFERRAL_BLOCKED = True # same phone / same policy / same email


# ─────────────────────────────────────────────────────────────────────────────
# 4. DOCUMENT EXPIRY REMINDERS
# ─────────────────────────────────────────────────────────────────────────────
# Days before expiry to nudge. Three touches, then stop — a fourth is nagging.
EXPIRY_REMINDER_DAYS = (30, 7, 1)
EXPIRY_DOC_TYPES = ('policy_renewal', 'licence_disc', 'roadworthy', 'drivers_licence')


# ─────────────────────────────────────────────────────────────────────────────
# 5. FESTIVE ROAD-SAFETY CHALLENGE  (seasonal, Botswana Dec road-death season)
# ─────────────────────────────────────────────────────────────────────────────
FESTIVE_CHALLENGE = {
    'starts': '2026-12-01',
    'ends':   '2027-01-07',
    'min_trips': 15,
    'target_score': 85,
    'bonus_points': 2000,
    # Never rank on "most kilometres" — that rewards driving more, which is the
    # opposite of what an insurer wants. Rank on score only.
    'rank_by': 'average_score',
}


def discount_for_score(score, trips, km):
    """Provisional safe-driving discount %. Returns 0 when unearned.

    Deliberately returns a % only — the pula conversion belongs to the renewal
    engine, which must respect PROVISIONAL before showing a figure.
    """
    if score is None or trips < MIN_TRIPS_FOR_DISCOUNT or km < MIN_KM_FOR_DISCOUNT:
        return 0
    for floor in sorted(SAFE_DRIVING_DISCOUNT, reverse=True):
        if score >= floor:
            return SAFE_DRIVING_DISCOUNT[floor]
    return 0


def cap_total(*discount_pcts):
    """Combine discounts under the cap. Protects the loss ratio from stacking."""
    if not DISCOUNTS_STACK:
        return min(max(discount_pcts, default=0), MAX_TOTAL_DISCOUNT_PCT)
    return min(sum(discount_pcts), MAX_TOTAL_DISCOUNT_PCT)
