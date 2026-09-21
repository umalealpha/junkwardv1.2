"""
hris/leave_excuse_rules.py

Pure, dependency-free rules for the Leave Excuse Response dashboard (CFO
2026-07-22). Kept free of Django model imports (like leave_accountability.py) so
the rules unit-test against plain strings with no database.

Two auto-response rules the CFO does not accept as reasons for missing productive
hours:

  Rule A — "power cut / no power / load shedding" AT HOME. The rule is
    "come to the office or find a way to work" (CFO 2026-07-21). A power cut at
    home is not an accepted reason. If the person says they DID come to / work
    from the office, it is NOT rejected (they followed the rule).

  Rule B — "Time Doctor / the tracker was down". Accepted ONLY with an IT
    help-desk ticket number in the text. "Time Doctor was down" with no ticket
    number is not accepted; they must log a ticket and reply with the number.

The command (hris/management/commands/process_leave_excuses.py) is the only
thing that emails; sending is gated by env LEAVE_EXCUSE_AUTOSEND (default OFF).
This module only decides the verdict and supplies the email subject + body.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass


# --- Safety gate ------------------------------------------------------------
# Sending real email is OFF by default. OFF = compute the verdict, record it,
# log "would send" — send NOTHING. Flip ON with LEAVE_EXCUSE_AUTOSEND=1.
def autosend_enabled() -> bool:
    """True only when LEAVE_EXCUSE_AUTOSEND is explicitly ON. Default '0' = OFF."""
    return (os.environ.get('LEAVE_EXCUSE_AUTOSEND', '0') or '0').strip().lower() in (
        '1', 'true', 'yes', 'on')


# --- Rule A: power cut at home ---------------------------------------------
# A subset of leave_accountability.WATCH_LIST that specifically means "I could
# not work because the power/lights were off at home".
POWER_TERMS = (
    'power cut',
    'power outage',
    'power failure',
    'power was out',
    'power went out',
    'power went off',
    'load shedding',
    'loadshedding',
    'no electricity',
    'no power',
)

# If they say they came to / worked from / were in the office, they followed the
# rule — do not reject (mirrors leave_accountability._OFFICE_OK).
_OFFICE_OK = re.compile(r'\b(came|went|worked|working|was|were|in)\b[^.]*\boffice\b', re.I)


# --- Rule B: Time Doctor / tracker was down --------------------------------
# An IT help-desk ticket number. Accepts "#12345", "ticket 12345", "INC0012345",
# "IT-4821", "ref: HD-77", etc. Requires at least two digits so a bare "#1" or a
# stray word never counts as a ticket.
TICKET_RE = re.compile(
    r'(#\s*\d{2,}'
    r'|\b(?:ticket|tkt|ref|reference|inc|incident|hd|help\s?desk|it)\b'
    r'[\s#:\-]*[a-z]{0,4}-?\d{2,})',
    re.I,
)


def _mentions_tracker(low: str) -> bool:
    """True if the text blames Time Doctor / the tracker for the missing hours."""
    if any(k in low for k in ('time doctor', 'timedoctor', 'time-doctor', 'tracker')):
        return True
    if 'tracking' in low and any(
        k in low for k in ('down', 'not ', "n't", 'broke', 'crash', 'issue',
                            'problem', 'fail', 'froze', 'stuck', 'wasn')):
        return True
    return False


# --- Verdict ----------------------------------------------------------------
@dataclass
class RuleVerdict:
    """The outcome of evaluating one excuse.

    rule      'A' | 'B' | None (None = no auto-rule applies)
    rejected  True if this excuse is auto-rejected (drives status "not accepted")
    label     granular auto_action label ('not accepted' / 'not accepted (no ticket)')
    """
    rule: str | None
    rejected: bool
    label: str


NO_RULE = RuleVerdict(rule=None, rejected=False, label='')


def evaluate(text: str, reason: str = '') -> RuleVerdict:
    """Classify one excuse. Pure — same string in, same verdict out, no DB.

    Rule A (power cut at home) takes precedence over Rule B. Neither fires on an
    empty explanation (nothing to reject yet — that is "no explanation")."""
    raw = text or ''
    low = raw.lower()
    if not low.strip():
        return NO_RULE

    # Rule A — power cut / load shedding at home, unless they came to the office.
    if not _OFFICE_OK.search(raw) and any(t in low for t in POWER_TERMS):
        return RuleVerdict(rule='A', rejected=True, label='not accepted')

    # Rule B — tracker / Time Doctor problem needs an IT ticket number.
    if _mentions_tracker(low):
        if not TICKET_RE.search(raw):
            return RuleVerdict(rule='B', rejected=True, label='not accepted (no ticket)')
        # A ticket number was supplied — accepted, no auto-email.
        return RuleVerdict(rule='B', rejected=False, label='')

    return NO_RULE


# --- Auto-email subjects + bodies -------------------------------------------
# Insurance-lawyer plain style (house email rule): open with the name, bullets,
# close "Regards". The subjects are asserted verbatim by the tests.
SUBJECT_A = 'Why were you not at the office?'
SUBJECT_B = 'Where is your IT ticket for Time Doctor?'


def email_for(rule: str, name: str, date_str: str) -> tuple[str, str]:
    """Return (subject, plain_text_body) for a rejected excuse. The command wraps
    the body in the Alpha Direct HTML envelope before sending."""
    first = (name or 'there').split()[0] if name else 'there'
    if rule == 'A':
        body = (
            f'{first},\n\n'
            f'Time Doctor recorded low or no productive hours for you on {date_str}.\n\n'
            '- A power cut at home is not an accepted reason for not working.\n'
            '- Staff are expected to come to the office or find another way to work.\n'
            '- Please reply and explain what you did to keep working that day.\n\n'
            'Regards,\nOmni · Alpha Direct'
        )
        return SUBJECT_A, body
    # Rule B
    body = (
        f'{first},\n\n'
        f'You cited a Time Doctor / tracker problem on {date_str}, with no IT ticket number.\n\n'
        '- Log an IT help-desk ticket for the issue.\n'
        '- Reply to this email with the ticket number.\n'
        '- "Time Doctor was down" with no ticket number is not accepted.\n\n'
        'Regards,\nOmni · Alpha Direct'
    )
    return SUBJECT_B, body
