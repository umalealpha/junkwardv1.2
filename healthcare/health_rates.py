"""
healthcare/health_rates.py — Alpha Direct Health OFFICE rate card.

Source of truth: Finance financial model (JC_20250901 Alpha Direct Health -
Financial Model), sheet "Final Rates", the OFFICE-rate columns (what AD charges
the client). CFO confirmed 2026-06-17 the office rate is the customer-facing
premium shown on the quote. BWP per life per month, EXCL VAT.

Keyed RATES[tier][gender][age_band] = {main, adult_dep, child_dep}.
"""
from decimal import Decimal

VAT_RATE = Decimal("0.14")   # VAT Act Cap 50:01

# (min_age, max_age, band_label) — matches the model's age groups.
AGE_BANDS = [
    (0, 19, "0 to 19"), (20, 24, "20 to 24"), (25, 29, "25 to 29"),
    (30, 34, "30 to 34"), (35, 39, "35 to 39"), (40, 44, "40 to 44"),
    (45, 49, "45 to 49"), (50, 54, "50 to 54"), (55, 59, "55 to 59"),
    (60, 64, "60 to 64"), (65, 200, "65+"),
]

PLAN_LABELS = {
    "AD_LITE": "AD Lite", "AD_ESSENTIAL": "AD Essential", "AD_CORE": "AD Core",
    "AD_PREMIER": "AD Premier", "AD_STATUS": "AD Status",
}
MEMBER_TYPES = {"main": "Policy Holder", "adult_dep": "Adult Dependant", "child_dep": "Child Dependant"}

RATES = {
    "AD_LITE": {
        "M": {
            "0 to 19": {"main": Decimal("0.00"), "adult_dep": Decimal("0.00"), "child_dep": Decimal("46.18")},
            "20 to 24": {"main": Decimal("76.41"), "adult_dep": Decimal("64.98"), "child_dep": Decimal("0.00")},
            "25 to 29": {"main": Decimal("76.93"), "adult_dep": Decimal("65.36"), "child_dep": Decimal("0.00")},
            "30 to 34": {"main": Decimal("83.02"), "adult_dep": Decimal("69.86"), "child_dep": Decimal("0.00")},
            "35 to 39": {"main": Decimal("86.86"), "adult_dep": Decimal("72.69"), "child_dep": Decimal("0.00")},
            "40 to 44": {"main": Decimal("90.99"), "adult_dep": Decimal("75.75"), "child_dep": Decimal("0.00")},
            "45 to 49": {"main": Decimal("95.43"), "adult_dep": Decimal("79.03"), "child_dep": Decimal("0.00")},
            "50 to 54": {"main": Decimal("100.91"), "adult_dep": Decimal("83.07"), "child_dep": Decimal("0.00")},
            "55 to 59": {"main": Decimal("106.86"), "adult_dep": Decimal("87.47"), "child_dep": Decimal("0.00")},
            "60 to 64": {"main": Decimal("113.33"), "adult_dep": Decimal("92.25"), "child_dep": Decimal("0.00")},
            "65+": {"main": Decimal("117.14"), "adult_dep": Decimal("95.07"), "child_dep": Decimal("0.00")},
        },
        "F": {
            "0 to 19": {"main": Decimal("32.64"), "adult_dep": Decimal("32.64"), "child_dep": Decimal("43.11")},
            "20 to 24": {"main": Decimal("92.53"), "adult_dep": Decimal("76.89"), "child_dep": Decimal("0.00")},
            "25 to 29": {"main": Decimal("98.45"), "adult_dep": Decimal("81.26"), "child_dep": Decimal("0.00")},
            "30 to 34": {"main": Decimal("104.37"), "adult_dep": Decimal("85.63"), "child_dep": Decimal("0.00")},
            "35 to 39": {"main": Decimal("110.36"), "adult_dep": Decimal("90.06"), "child_dep": Decimal("0.00")},
            "40 to 44": {"main": Decimal("115.27"), "adult_dep": Decimal("93.68"), "child_dep": Decimal("0.00")},
            "45 to 49": {"main": Decimal("120.18"), "adult_dep": Decimal("97.31"), "child_dep": Decimal("0.00")},
            "50 to 54": {"main": Decimal("121.92"), "adult_dep": Decimal("98.60"), "child_dep": Decimal("0.00")},
            "55 to 59": {"main": Decimal("123.71"), "adult_dep": Decimal("99.92"), "child_dep": Decimal("0.00")},
            "60 to 64": {"main": Decimal("125.53"), "adult_dep": Decimal("101.26"), "child_dep": Decimal("0.00")},
            "65+": {"main": Decimal("127.38"), "adult_dep": Decimal("102.64"), "child_dep": Decimal("0.00")},
        },
    },
    "AD_ESSENTIAL": {
        "M": {
            "0 to 19": {"main": Decimal("0.00"), "adult_dep": Decimal("0.00"), "child_dep": Decimal("113.30")},
            "20 to 24": {"main": Decimal("280.55"), "adult_dep": Decimal("217.30"), "child_dep": Decimal("0.00")},
            "25 to 29": {"main": Decimal("283.41"), "adult_dep": Decimal("219.41"), "child_dep": Decimal("0.00")},
            "30 to 34": {"main": Decimal("317.11"), "adult_dep": Decimal("244.31"), "child_dep": Decimal("0.00")},
            "35 to 39": {"main": Decimal("338.34"), "adult_dep": Decimal("259.99"), "child_dep": Decimal("0.00")},
            "40 to 44": {"main": Decimal("361.19"), "adult_dep": Decimal("276.87"), "child_dep": Decimal("0.00")},
            "45 to 49": {"main": Decimal("385.78"), "adult_dep": Decimal("295.04"), "child_dep": Decimal("0.00")},
            "50 to 54": {"main": Decimal("416.07"), "adult_dep": Decimal("317.42"), "child_dep": Decimal("0.00")},
            "55 to 59": {"main": Decimal("449.00"), "adult_dep": Decimal("341.75"), "child_dep": Decimal("0.00")},
            "60 to 64": {"main": Decimal("484.81"), "adult_dep": Decimal("368.20"), "child_dep": Decimal("0.00")},
            "65+": {"main": Decimal("505.87"), "adult_dep": Decimal("383.76"), "child_dep": Decimal("0.00")},
        },
        "F": {
            "0 to 19": {"main": Decimal("38.40"), "adult_dep": Decimal("38.40"), "child_dep": Decimal("96.30")},
            "20 to 24": {"main": Decimal("369.75"), "adult_dep": Decimal("283.19"), "child_dep": Decimal("0.00")},
            "25 to 29": {"main": Decimal("402.49"), "adult_dep": Decimal("307.38"), "child_dep": Decimal("0.00")},
            "30 to 34": {"main": Decimal("435.22"), "adult_dep": Decimal("331.57"), "child_dep": Decimal("0.00")},
            "35 to 39": {"main": Decimal("468.38"), "adult_dep": Decimal("356.06"), "child_dep": Decimal("0.00")},
            "40 to 44": {"main": Decimal("495.52"), "adult_dep": Decimal("376.12"), "child_dep": Decimal("0.00")},
            "45 to 49": {"main": Decimal("522.67"), "adult_dep": Decimal("396.18"), "child_dep": Decimal("0.00")},
            "50 to 54": {"main": Decimal("532.35"), "adult_dep": Decimal("403.33"), "child_dep": Decimal("0.00")},
            "55 to 59": {"main": Decimal("542.22"), "adult_dep": Decimal("410.62"), "child_dep": Decimal("0.00")},
            "60 to 64": {"main": Decimal("552.29"), "adult_dep": Decimal("418.06"), "child_dep": Decimal("0.00")},
            "65+": {"main": Decimal("562.56"), "adult_dep": Decimal("425.64"), "child_dep": Decimal("0.00")},
        },
    },
    "AD_CORE": {
        "M": {
            "0 to 19": {"main": Decimal("0.00"), "adult_dep": Decimal("0.00"), "child_dep": Decimal("202.64")},
            "20 to 24": {"main": Decimal("529.29"), "adult_dep": Decimal("403.57"), "child_dep": Decimal("0.00")},
            "25 to 29": {"main": Decimal("597.38"), "adult_dep": Decimal("453.87"), "child_dep": Decimal("0.00")},
            "30 to 34": {"main": Decimal("645.23"), "adult_dep": Decimal("489.22"), "child_dep": Decimal("0.00")},
            "35 to 39": {"main": Decimal("674.08"), "adult_dep": Decimal("510.54"), "child_dep": Decimal("0.00")},
            "40 to 44": {"main": Decimal("702.93"), "adult_dep": Decimal("531.85"), "child_dep": Decimal("0.00")},
            "45 to 49": {"main": Decimal("1037.87"), "adult_dep": Decimal("779.30"), "child_dep": Decimal("0.00")},
            "50 to 54": {"main": Decimal("1194.67"), "adult_dep": Decimal("895.15"), "child_dep": Decimal("0.00")},
            "55 to 59": {"main": Decimal("1283.65"), "adult_dep": Decimal("960.88"), "child_dep": Decimal("0.00")},
            "60 to 64": {"main": Decimal("1625.53"), "adult_dep": Decimal("1213.46"), "child_dep": Decimal("0.00")},
            "65+": {"main": Decimal("1967.41"), "adult_dep": Decimal("1466.04"), "child_dep": Decimal("0.00")},
        },
        "F": {
            "0 to 19": {"main": Decimal("48.00"), "adult_dep": Decimal("48.00"), "child_dep": Decimal("384.78")},
            "20 to 24": {"main": Decimal("1008.75"), "adult_dep": Decimal("757.79"), "child_dep": Decimal("0.00")},
            "25 to 29": {"main": Decimal("1087.10"), "adult_dep": Decimal("815.67"), "child_dep": Decimal("0.00")},
            "30 to 34": {"main": Decimal("1262.45"), "adult_dep": Decimal("945.22"), "child_dep": Decimal("0.00")},
            "35 to 39": {"main": Decimal("1292.20"), "adult_dep": Decimal("967.20"), "child_dep": Decimal("0.00")},
            "40 to 44": {"main": Decimal("1344.36"), "adult_dep": Decimal("1005.73"), "child_dep": Decimal("0.00")},
            "45 to 49": {"main": Decimal("1401.91"), "adult_dep": Decimal("1048.25"), "child_dep": Decimal("0.00")},
            "50 to 54": {"main": Decimal("1604.34"), "adult_dep": Decimal("1197.80"), "child_dep": Decimal("0.00")},
            "55 to 59": {"main": Decimal("1837.03"), "adult_dep": Decimal("1369.71"), "child_dep": Decimal("0.00")},
            "60 to 64": {"main": Decimal("2104.51"), "adult_dep": Decimal("1567.33"), "child_dep": Decimal("0.00")},
            "65+": {"main": Decimal("2411.99"), "adult_dep": Decimal("1794.48"), "child_dep": Decimal("0.00")},
        },
    },
    "AD_PREMIER": {
        "M": {
            "0 to 19": {"main": Decimal("0.00"), "adult_dep": Decimal("0.00"), "child_dep": Decimal("345.82")},
            "20 to 24": {"main": Decimal("941.13"), "adult_dep": Decimal("712.01"), "child_dep": Decimal("0.00")},
            "25 to 29": {"main": Decimal("1065.21"), "adult_dep": Decimal("803.68"), "child_dep": Decimal("0.00")},
            "30 to 34": {"main": Decimal("1152.42"), "adult_dep": Decimal("868.11"), "child_dep": Decimal("0.00")},
            "35 to 39": {"main": Decimal("1205.00"), "adult_dep": Decimal("906.95"), "child_dep": Decimal("0.00")},
            "40 to 44": {"main": Decimal("1257.58"), "adult_dep": Decimal("945.80"), "child_dep": Decimal("0.00")},
            "45 to 49": {"main": Decimal("1867.98"), "adult_dep": Decimal("1396.76"), "child_dep": Decimal("0.00")},
            "50 to 54": {"main": Decimal("2153.75"), "adult_dep": Decimal("1607.88"), "child_dep": Decimal("0.00")},
            "55 to 59": {"main": Decimal("2315.91"), "adult_dep": Decimal("1727.68"), "child_dep": Decimal("0.00")},
            "60 to 64": {"main": Decimal("2938.97"), "adult_dep": Decimal("2187.99"), "child_dep": Decimal("0.00")},
            "65+": {"main": Decimal("3562.03"), "adult_dep": Decimal("2648.30"), "child_dep": Decimal("0.00")},
        },
        "F": {
            "0 to 19": {"main": Decimal("64.00"), "adult_dep": Decimal("64.00"), "child_dep": Decimal("677.77")},
            "20 to 24": {"main": Decimal("1814.92"), "adult_dep": Decimal("1357.56"), "child_dep": Decimal("0.00")},
            "25 to 29": {"main": Decimal("1957.71"), "adult_dep": Decimal("1463.05"), "child_dep": Decimal("0.00")},
            "30 to 34": {"main": Decimal("2277.27"), "adult_dep": Decimal("1699.13"), "child_dep": Decimal("0.00")},
            "35 to 39": {"main": Decimal("2331.49"), "adult_dep": Decimal("1739.19"), "child_dep": Decimal("0.00")},
            "40 to 44": {"main": Decimal("2426.55"), "adult_dep": Decimal("1809.42"), "child_dep": Decimal("0.00")},
            "45 to 49": {"main": Decimal("2531.44"), "adult_dep": Decimal("1886.91"), "child_dep": Decimal("0.00")},
            "50 to 54": {"main": Decimal("2900.35"), "adult_dep": Decimal("2159.46"), "child_dep": Decimal("0.00")},
            "55 to 59": {"main": Decimal("3324.42"), "adult_dep": Decimal("2472.76"), "child_dep": Decimal("0.00")},
            "60 to 64": {"main": Decimal("3811.90"), "adult_dep": Decimal("2832.90"), "child_dep": Decimal("0.00")},
            "65+": {"main": Decimal("4372.26"), "adult_dep": Decimal("3246.88"), "child_dep": Decimal("0.00")},
        },
    },
    "AD_STATUS": {
        "M": {
            "0 to 19": {"main": Decimal("0.00"), "adult_dep": Decimal("0.00"), "child_dep": Decimal("547.89")},
            "20 to 24": {"main": Decimal("1401.07"), "adult_dep": Decimal("1072.70"), "child_dep": Decimal("0.00")},
            "25 to 29": {"main": Decimal("1578.89"), "adult_dep": Decimal("1204.08"), "child_dep": Decimal("0.00")},
            "30 to 34": {"main": Decimal("1703.87"), "adult_dep": Decimal("1296.41"), "child_dep": Decimal("0.00")},
            "35 to 39": {"main": Decimal("1779.22"), "adult_dep": Decimal("1352.08"), "child_dep": Decimal("0.00")},
            "40 to 44": {"main": Decimal("1854.57"), "adult_dep": Decimal("1407.75"), "child_dep": Decimal("0.00")},
            "45 to 49": {"main": Decimal("2729.38"), "adult_dep": Decimal("2054.04"), "child_dep": Decimal("0.00")},
            "50 to 54": {"main": Decimal("3138.93"), "adult_dep": Decimal("2356.62"), "child_dep": Decimal("0.00")},
            "55 to 59": {"main": Decimal("3371.33"), "adult_dep": Decimal("2528.31"), "child_dep": Decimal("0.00")},
            "60 to 64": {"main": Decimal("4264.27"), "adult_dep": Decimal("3188.00"), "child_dep": Decimal("0.00")},
            "65+": {"main": Decimal("5157.21"), "adult_dep": Decimal("3847.69"), "child_dep": Decimal("0.00")},
        },
        "F": {
            "0 to 19": {"main": Decimal("0.00"), "adult_dep": Decimal("0.00"), "child_dep": Decimal("1023.63")},
            "20 to 24": {"main": Decimal("2653.34"), "adult_dep": Decimal("1997.87"), "child_dep": Decimal("0.00")},
            "25 to 29": {"main": Decimal("2857.97"), "adult_dep": Decimal("2149.04"), "child_dep": Decimal("0.00")},
            "30 to 34": {"main": Decimal("3315.95"), "adult_dep": Decimal("2487.40"), "child_dep": Decimal("0.00")},
            "35 to 39": {"main": Decimal("3393.66"), "adult_dep": Decimal("2544.81"), "child_dep": Decimal("0.00")},
            "40 to 44": {"main": Decimal("3529.88"), "adult_dep": Decimal("2645.45"), "child_dep": Decimal("0.00")},
            "45 to 49": {"main": Decimal("3680.21"), "adult_dep": Decimal("2756.50"), "child_dep": Decimal("0.00")},
            "50 to 54": {"main": Decimal("4208.92"), "adult_dep": Decimal("3147.11"), "child_dep": Decimal("0.00")},
            "55 to 59": {"main": Decimal("4816.68"), "adult_dep": Decimal("3596.11"), "child_dep": Decimal("0.00")},
            "60 to 64": {"main": Decimal("5515.30"), "adult_dep": Decimal("4112.25"), "child_dep": Decimal("0.00")},
            "65+": {"main": Decimal("6318.38"), "adult_dep": Decimal("4705.55"), "child_dep": Decimal("0.00")},
        },
    },
}


# ── Per-tier quotation discounts (Tlamelo Chimidza, 2026-06-25) ──────────────
# Standardised discount the salesperson may apply to the OFFICE premium per plan
# tier, to make a group quote more attractive. Defaults below are Tlamelo's
# proposed table; each can be overridden per quote (saved on HealthQuote.
# tier_discounts). Premier/Status are gated to under-35 groups and default to 0%.
TIER_DISCOUNT_DEFAULTS = {
    "AD_LITE":      Decimal("25"),
    "AD_ESSENTIAL": Decimal("15"),
    "AD_CORE":      Decimal("10"),
    "AD_PREMIER":   Decimal("0"),
    "AD_STATUS":    Decimal("0"),
}
# Informational only — the eligibility note shown next to the tier on the quote.
TIER_DISCOUNT_GATES = {
    "AD_PREMIER": "Under-35 group",
    "AD_STATUS":  "Under-35 group",
}


def discount_for(tier: str, overrides) -> Decimal:
    """Discount % for a tier from the discounts stored ON THIS QUOTE.

    Returns the stored value (clamped 0–100), else 0. It deliberately does NOT
    fall back to TIER_DISCOUNT_DEFAULTS — those are FE prefill only, captured
    into each quote at creation. Falling back here would retroactively discount
    every existing (incl. already-approved/invoiced) quote, whose stored map is
    empty. So a quote with no stored discount = no discount."""
    overrides = overrides or {}
    raw = overrides.get(tier)
    if raw not in (None, ""):
        try:
            pct = Decimal(str(raw))
            return max(Decimal("0"), min(Decimal("100"), pct))
        except (ArithmeticError, ValueError, TypeError):
            pass
    return Decimal("0")


def band_for(age: int) -> str:
    for lo, hi, label in AGE_BANDS:
        if lo <= age <= hi:
            return label
    return "65+"

def rate_for(tier: str, gender: str, member_type: str, age: int) -> Decimal:
    """OFFICE premium (excl VAT) for one member. 0 if no rate (e.g. an adult
    in a child band, or a child in an adult band)."""
    t = RATES.get(tier) or {}
    g = t.get((gender or "M").upper()[:1]) or {}
    row = g.get(band_for(int(age))) or {}
    key = member_type if member_type in ("main", "adult_dep", "child_dep") else "main"
    return Decimal(row.get(key, Decimal("0.00")))
