"""
hris/leave_accountability.py

Pure, dependency-free rules for the Leave & Productive-Hours Accountability
module (CFO 2026-07-21 — see the `/leave` skill for the full agreed design).

Kept free of Django imports (like workforce.py) so the rules unit-test against
plain values with no database. The Django models/views/cron that use these live
elsewhere; this file is ONLY the settled arithmetic + text rules:

  1. shortfall hours  -> leave hours   (pure hourly, no day-banding)
  2. watch-list flagging of excuses the CFO does not accept ("power cut at home")
  3. word/theme frequency for the CFO "Excuses feed"

Enforcement phasing (warm-up until 2026-08-31, strict from 2026-09-01) and the
annual-balance -> negative -> unpaid + HR-alert money flow are applied by the
callers, not here.
"""

from __future__ import annotations

import re
from collections import Counter
from datetime import date
from decimal import Decimal
from typing import Iterable

# 1 Sep 2026: the day auto-applied leave starts actually biting. Before this we
# run warn-and-collect (emails, excuses feed, manager sign-off) but deduct nothing.
STRICT_ENFORCEMENT_START = date(2026, 9, 1)


def leave_hours_for_shortfall(shortfall_hours) -> Decimal:
    """Pure hourly rule (CFO 2026-07-21): a shortfall of X hours becomes X hours
    of leave. No quarter/half/full-day banding. Never negative; 2dp."""
    hrs = Decimal(shortfall_hours or 0)
    if hrs <= 0:
        return Decimal('0.00')
    return hrs.quantize(Decimal('0.01'))


def enforcement_active(on_date: date) -> bool:
    """True once leave actually applies (from 1 Sep 2026). Before that the whole
    machine runs but no leave/pay is deducted."""
    return on_date >= STRICT_ENFORCEMENT_START


# ---------------------------------------------------------------------------
# Watch-list: excuses the CFO does not accept. The rule (CFO 2026-07-21) is
# "come to the office or find a way to work" — a power cut AT HOME is the biggest
# lie and must surface to the CFO + Unami on sight. Flagging only SURFACES it; a
# human still decides. Someone who says they came to the office is not flagged.
# ---------------------------------------------------------------------------
WATCH_LIST = (
    'power cut',
    'power outage',
    'power failure',
    'load shedding',
    'loadshedding',
    'no electricity',
    'no power',
    'electricity',
    'no internet at home',
    'wifi at home',
    'network at home',
)

# If the excuse also says they worked from / went to the office, don't flag it —
# they followed the rule.
_OFFICE_OK = re.compile(r'\b(came|went|worked|was)\b[^.]*\boffice\b', re.I)


def flag_excuse(text: str) -> list[str]:
    """Return the watch-list phrases found in an excuse (empty list = clean).

    A hit means "surface this to the CFO/Unami", not "auto-reject". If the person
    says they came to / worked from the office despite the power cut, it's clean."""
    if not text:
        return []
    low = text.lower()
    if _OFFICE_OK.search(text):
        return []
    return [phrase for phrase in WATCH_LIST if phrase in low]


def is_flagged(text: str) -> bool:
    return bool(flag_excuse(text))


# ---------------------------------------------------------------------------
# Word / theme frequency for the Excuses feed "most-used words" panel, and as the
# structured hint fed to the weekly Ollama+DeepSeek summary.
# ---------------------------------------------------------------------------
_STOPWORDS = frozenset("""
a an the and or but if then so because as of to in on at for with without from by
into out up down over under again i me my we our you your he she it they them was
were is are be been being have has had do did does not no nor too very can will just
that this these those there here then than i'm i've didn't couldn't wasn't weren't
day today yesterday work working hours hour time did done because able unable due
""".split())

_WORD = re.compile(r"[a-z][a-z'\-]{2,}")


def word_frequency(texts: Iterable[str], top_n: int = 20) -> list[tuple[str, int]]:
    """Most common meaningful words across a set of excuses, stopwords removed.
    Returns [(word, count), ...] highest first."""
    counter: Counter = Counter()
    for t in texts or ():
        for w in _WORD.findall((t or '').lower()):
            if w not in _STOPWORDS:
                counter[w] += 1
    return counter.most_common(top_n)
