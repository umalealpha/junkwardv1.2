"""staff_rewards/points_rules.py — CONFIGURABLE point values.

Spec section 2 + 7: ALL point values are placeholders that live in a
config file, NOT hard-coded in logic, because they require HR
(U. Butale) + CFO sign-off and a points-to-cost budget before launch.
service.py reads POINTS_RULES; never inline a number in the service.

Spec section 6/7: the two health-data features (meal photos, daily
steps) touch special-category data under the Botswana DPA and need a
DPIA + opt-in before build sign-off — so they ship DISABLED via
FEATURE_ENABLED below.
"""
from __future__ import annotations


# ---------------------------------------------------------------------------
# Point schedule — EVERY value is a placeholder pending HR + CFO sign-off.
# ---------------------------------------------------------------------------
POINTS_RULES = {
    # Professional Development & Upskilling (Innovation).
    # payload: {memberId, courseRef, verificationUrl, level: short|intermediate|certification}
    'profdev': {
        'levels': {
            'short':         50,   # TODO(human): placeholder — HR (U. Butale) + CFO sign-off required before launch
            'intermediate':  150,  # TODO(human): placeholder — HR (U. Butale) + CFO sign-off required before launch
            'certification': 300,  # TODO(human): placeholder — HR (U. Butale) + CFO sign-off required before launch
        },
        'annual_cap': 600,         # TODO(human): placeholder — HR (U. Butale) + CFO sign-off required before launch
    },

    # Business Development & Partnerships (Business Impact).
    # payload: {memberId, orgName, agreementRef, reach: local|university, outcomeCount?}
    'bizdev': {
        'reach': {
            'local':      100,     # TODO(human): placeholder — HR (U. Butale) + CFO sign-off required before launch
            'university': 300,     # TODO(human): placeholder — HR (U. Butale) + CFO sign-off required before launch
        },
        'outcome_bonus_per_unit': 25,  # TODO(human): placeholder — HR (U. Butale) + CFO sign-off required before launch
        'outcome_bonus_cap':      150, # TODO(human): placeholder — HR (U. Butale) + CFO sign-off required before launch
    },

    # Running Clubs & Fitness Activities (Health & Wellness).
    # payload: {memberId, kind: session|event, isTeam?}
    'fitness': {
        'per_session':    20,      # TODO(human): placeholder — HR (U. Butale) + CFO sign-off required before launch
        'per_event':      200,     # TODO(human): placeholder — HR (U. Butale) + CFO sign-off required before launch
        'team_bonus':     30,      # TODO(human): placeholder — HR (U. Butale) + CFO sign-off required before launch
    },

    # Customer Compliments & Positive Feedback (Business Impact).
    # payload: {memberId, caseRef, channel, repeatIndex?}
    'compliment': {
        'base':            50,     # TODO(human): placeholder — HR (U. Butale) + CFO sign-off required before launch
        'repeat_bonus':    25,     # TODO(human): placeholder — HR (U. Butale) + CFO sign-off required before launch
        'repeat_bonus_cap': 100,   # TODO(human): placeholder — HR (U. Butale) + CFO sign-off required before launch
    },

    # Healthy Eating & Nutrition (Health & Wellness) — DEFERRED (DPIA).
    # payload: {memberId, aiScore, qualifying, mealsTodayCount?, streakDays?}
    'meal': {
        'per_qualifying_meal':  10,  # TODO(human): placeholder — HR (U. Butale) + CFO sign-off required before launch
        'daily_cap_meals':      2,   # TODO(human): placeholder — HR (U. Butale) + CFO sign-off required before launch
        'weekly_streak_bonus':  50,  # TODO(human): placeholder — HR (U. Butale) + CFO sign-off required before launch
        'streak_days_required': 5,   # TODO(human): placeholder — HR (U. Butale) + CFO sign-off required before launch
    },

    # Daily Step Goals (Health & Wellness) — DEFERRED (DPIA).
    # payload: {memberId, targetMet, weeklyDaysMet?}
    'steps': {
        'per_day_target_met':   10,  # TODO(human): placeholder — HR (U. Butale) + CFO sign-off required before launch
        'weekly_bonus':         50,  # TODO(human): placeholder — HR (U. Butale) + CFO sign-off required before launch
        'weekly_days_required': 5,   # TODO(human): placeholder — HR (U. Butale) + CFO sign-off required before launch
    },

    # Task Performance (Business Impact) — CFO directive 2026-07-13.
    # NOT a maker/approver submission: posted automatically when the assigner
    # (CFO) gives Done / Partially done / Not done feedback on an OmniTask.
    #   Done      -> full points
    #   Partial   -> full points scaled by completion % (25/50/75/100)
    #   Not done  -> 0 (and any points this task earned before are forfeited,
    #                via delta reconciliation in service.award_task_performance)
    # Feeds the Business Impact pillar so it shows on the existing 3-pillar UI.
    'task_performance': {
        'done':     40,   # TODO(human): placeholder — HR (U. Butale) + CFO sign-off required before launch
        'partial':  40,   # TODO(human): base scaled by completion % — placeholder pending sign-off
        'not_done': 0,    # no negative below the employee's other earned points
        'pillar':   'business_impact',
    },
}


# ---------------------------------------------------------------------------
# Feature flags. meal + steps OFF pending DPIA + opt-in (spec §6/§7); the
# other four are buildable now.
# ---------------------------------------------------------------------------
FEATURE_ENABLED = {
    'profdev':    True,
    'bizdev':     True,
    'fitness':    True,
    'compliment': True,
    'meal':       True,    # CFO-approved 2026-06-26 — opt-in + camera-only + score-only storage (image discarded), Member ID not name in prompt
    'steps':      True,    # CFO-approved 2026-06-26 — opt-in + aggregated daily count only (no location/sensor data)
}
