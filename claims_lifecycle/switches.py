"""B14 — every piece behind its own switch, armed in order.

"Every switch listed in one place with its owner, and each one proven to turn
its feature off cleanly." This module IS that one place.

One switch per board item, not one switch over five: a switch that covers five
features cannot turn one misbehaving feature off in a minute, which is the
whole reason the CFO asked for switches instead of releases.

Arming order on the morning of 1 October, safest first: the read-only screens,
then the customer link, then the premium check, then the AI summaries (advice
only), then the money leg, and last the repudiation drafts, which still need a
manager's button. NOTHING is switched on between 07:45 and 12:00.

Owners are ROLES, never a colleague's name — this file is read by people who
join and leave.
"""

ARMING_ORDER = [
    'screens',
    'customer',
    'premium',
    'ai',
    'money',
    'repudiation',
]

_FREEZE_START = '07:45'
_FREEZE_END = '12:00'


def freeze_window():
    return (_FREEZE_START, _FREEZE_END)


def is_on(key, switches):
    value = switches.get(key, False)
    if value is True:
        return True
    if isinstance(value, str) and value.lower() == 'true':
        return True
    return False


def may_arm_now(now):
    start_h, start_m = (int(part) for part in _FREEZE_START.split(':'))
    end_h, end_m = (int(part) for part in _FREEZE_END.split(':'))
    hours, minutes = (int(part) for part in now.split(':'))

    current = hours * 60 + minutes
    start = start_h * 60 + start_m
    end = end_h * 60 + end_m

    return not (start <= current < end)


SWITCHES = [
    # ---- wave 1: read-only screens. Nothing here writes anything. ----------
    {
        'key': 'tracker_notification_date',
        'owner': 'Claims Manager',
        'description': 'Turns off the Claims Life Cycle Tracker naming and the '
                       'notification date stamped on each claim.',
        'wave': 'screens', 'default': False, 'items': ['B1'],
    },
    {
        'key': 'tracker_lights_and_bar',
        'owner': 'Claims Manager',
        'description': 'Turns off the traffic light, the completion bar and the '
                       'holder shown against each step.',
        'wave': 'screens', 'default': False, 'items': ['B3'],
    },
    {
        'key': 'clocks_supplier_and_aol',
        'owner': 'Finance',
        'description': 'Turns off clock one (supplier invoice to bank confirmation) '
                       'and clock two (Agreement of Loss to bank confirmation).',
        'wave': 'screens', 'default': False, 'items': ['B4'],
    },
    {
        'key': 'clock_repudiation',
        'owner': 'Claims Manager',
        'description': 'Turns off clock three, notification to the decline letter '
                       'being sent to the client.',
        'wave': 'screens', 'default': False, 'items': ['B5'],
    },
    {
        'key': 'performance_dashboard',
        'owner': 'Claims Manager',
        'description': 'Turns off the Claims Performance Dashboard, both the '
                       'performance side and the money side.',
        'wave': 'screens', 'default': False, 'items': ['B8'],
    },

    # ---- wave 3: the premium check ----------------------------------------
    {
        'key': 'premium_checked_events',
        'owner': 'Finance',
        'description': 'Turns off Omni acting on the premium-checked event '
                       'Graphite fires. The door still accepts and stores it.',
        'wave': 'premium', 'default': False, 'items': ['B2'],
    },

    # ---- wave 4: the AI summaries. Advice only, nothing acts on them. ------
    {
        'key': 'ai_claim_summary',
        'owner': 'Claims Manager',
        'description': 'Turns off the AI summary and suggested next step written '
                       'onto a claim. Advice only; nothing acts on it.',
        'wave': 'ai', 'default': False, 'items': ['B13'],
    },

    # ---- wave 5: the money leg --------------------------------------------
    {
        'key': 'settlement_feedback_to_graphite',
        'owner': 'Finance',
        'description': 'Turns off telling Graphite that the bank confirmed a claim '
                       'payment left, so the claim settles itself.',
        'wave': 'money', 'default': False, 'items': ['B6'],
    },
    {
        'key': 'supplier_recon_feed',
        'owner': 'Finance',
        'description': 'Turns off feeding an approved claim\'s repairer and parts '
                       'invoices straight into supplier reconciliation.',
        'wave': 'money', 'default': False, 'items': ['B7'],
    },
    {
        'key': 'agreement_of_loss',
        'owner': 'Finance',
        'description': 'Turns off producing the Agreement of Loss. Stays off until '
                       'Finance confirms what comes off the settlement besides the excess.',
        'wave': 'money', 'default': False, 'items': ['B9'],
    },
    {
        'key': 'write_off_notices',
        'owner': 'Underwriting',
        'description': 'Turns off the write-off notices and tasks, one insured item '
                       'versus a fleet. Notices and tasks only; nothing is cancelled.',
        'wave': 'money', 'default': False, 'items': ['B12'],
    },

    # ---- wave 6: the repudiation drafts, last -----------------------------
    {
        'key': 'repudiation_drafts',
        'owner': 'Claims Manager',
        'description': 'Turns off drafting the decline letter. A draft still needs '
                       'a manager\'s button before anything reaches a client.',
        'wave': 'repudiation', 'default': False, 'items': ['B10'],
    },
    {
        'key': 'policy_clause_lookup',
        'owner': 'Claims Manager',
        'description': 'Turns off pulling the policy clause and cross-checking it '
                       'against the claim documents before a decline is drafted.',
        'wave': 'repudiation', 'default': False, 'items': ['B11'],
    },
]
