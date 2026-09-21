"""Seed Health Care rate card — Alpha Direct 5-plan.

Source: Omni_Build_Spec.xlsx sheet 10_Health_Pricing — averages from
the AD Office Rate card and Hannover Re reinsurance quote (per life
per month, BWP, 100% cession assumed for Health Care).

To avoid a heavy schema for the first cut we store the rate card in
code. Once Finance signs off, this dict moves to the rate_card /
rate_card_line tables specified in 02_DB_Schema.
"""
from decimal import Decimal


# --- 1. Age-band scaling factors ---------------------------------------------
# Industry-standard age-banded multipliers applied to the plan's MAIN-member
# baseline. CHILD_DEP uses a single low factor (children pool risk).
AGE_BAND_MULTIPLIERS = [
    # (min_age, max_age, MAIN_factor, ADULT_DEP_factor, CHILD_DEP_factor)
    ( 0,  17, 0.00, 0.00, 0.30),   # children -> dependants only
    (18,  24, 0.55, 0.65, 0.00),
    (25,  29, 0.65, 0.80, 0.00),
    (30,  34, 0.80, 0.95, 0.00),
    (35,  39, 1.00, 1.15, 0.00),   # baseline reference band
    (40,  44, 1.18, 1.35, 0.00),
    (45,  49, 1.40, 1.60, 0.00),
    (50,  54, 1.70, 1.95, 0.00),
    (55,  59, 2.10, 2.40, 0.00),
    (60,  64, 2.60, 2.95, 0.00),
    (65, 200, 3.20, 3.60, 0.00),
]


# --- 2. Plan baselines (MAIN @ age 35–39 = factor 1.0) -----------------------
# Office = what AD charges the client. RI = what Hannover Re charges AD.
# Margin = office − RI.  (BWP per life per month.)
HC_PLANS = {
    'AD_LITE': {
        'name':            'AD Lite',
        'office_baseline': Decimal('105.85'),
        'ri_baseline':     Decimal('47.55'),
        'broker_pct':      Decimal('0.10'),
        'nbfira_pct':      Decimal('0.0015'),
    },
    'AD_ESSENTIAL': {
        'name':            'AD Essential',
        'office_baseline': Decimal('435.28'),
        'ri_baseline':     Decimal('249.30'),
        'broker_pct':      Decimal('0.10'),
        'nbfira_pct':      Decimal('0.0015'),
    },
    'AD_CORE': {
        'name':            'AD Core',
        'office_baseline': Decimal('1221.94'),
        'ri_baseline':     Decimal('759.05'),
        'broker_pct':      Decimal('0.10'),
        'nbfira_pct':      Decimal('0.0015'),
    },
    'AD_PREMIER': {
        'name':            'AD Premier',
        'office_baseline': Decimal('2203.44'),
        'ri_baseline':     Decimal('1395.30'),
        'broker_pct':      Decimal('0.10'),
        'nbfira_pct':      Decimal('0.0015'),
    },
    'AD_STATUS': {
        'name':            'AD Status',
        'office_baseline': Decimal('3389.91'),
        'ri_baseline':     Decimal('2058.16'),
        'broker_pct':      Decimal('0.10'),
        'nbfira_pct':      Decimal('0.0015'),
    },
}


def _band_factors(age: int, life_cat: str):
    """Return (office_factor, ri_factor) for a life."""
    for lo, hi, m, ad, ch in AGE_BAND_MULTIPLIERS:
        if lo <= age <= hi:
            if life_cat == 'CHILD_DEP':
                f = ch
            elif life_cat == 'ADULT_DEP':
                f = ad
            else:
                f = m
            return f, f
    return 0.0, 0.0


def lookup_rate(*, plan_code: str, age: int, life_category: str = 'MAIN'):
    """Return {office_monthly_bwp, ri_monthly_bwp, plan_name}."""
    plan = HC_PLANS.get(plan_code.upper())
    if not plan:
        raise ValueError(f"Unknown plan_code '{plan_code}'. "
                         f"Valid: {list(HC_PLANS.keys())}")
    f_off, f_ri = _band_factors(age, life_category)
    if f_off == 0.0:
        # No rate band for this combination (e.g. adult as CHILD_DEP)
        return {
            'plan_name':          plan['name'],
            'office_monthly_bwp': Decimal('0.00'),
            'ri_monthly_bwp':     Decimal('0.00'),
        }
    office = (plan['office_baseline'] * Decimal(str(f_off))).quantize(Decimal('0.01'))
    ri     = (plan['ri_baseline']     * Decimal(str(f_ri))).quantize(Decimal('0.01'))
    return {
        'plan_name':          plan['name'],
        'office_monthly_bwp': office,
        'ri_monthly_bwp':     ri,
    }


def list_plans():
    return [{'code': k, 'name': v['name']} for k, v in HC_PLANS.items()]
