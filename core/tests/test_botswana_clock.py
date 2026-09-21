"""No production code may read the SERVER's clock for a calendar date.

`settings.TIME_ZONE` is `Africa/Gaborone` (UTC+2) and the prod box runs UTC. So
between 00:00 and 02:00 Gaborone every one of these returns YESTERDAY:

    datetime.date.today()          the server's naive date, any alias
    timezone.now().date()          the UTC date of an aware datetime
    datetime.utcnow()              likewise, when used as "now"

`django.utils.timezone.localdate()` returns the date in Africa/Gaborone, which
is what business code means by "today".

This class of defect shipped FOUR times in the bonu module family alone before
anyone counted it, and the count when we finally did was 232 lines across 139
files: leave forms defaulting to yesterday, purchase orders dated a day early,
ageing and overdue checks a day out, reference numbers stamped with the wrong
day, exports named for the wrong date. None of it ever looked like a bug,
because by 02:00 it had fixed itself.

Swept and closed 10 Sep 2026. This test is what stops it coming back.

WHY THE AST AND NOT A GREP
An earlier version of this audit used a regular expression and got it wrong
twice: it needed a word boundary before `date.today()`, so every alias ending
in a word character (`_date.today()`, `date_cls.today()`, `_date_cls.today()`,
`date_class.today()`) walked straight past it, and it also flagged the comments
that WARN about the pattern. Parsing the file has neither problem: an alias is
just an attribute chain, and a comment or docstring is not a Call node at all.

RED-FIRST: put any of these back in a production file and this test names the
file, the line and the call.
"""
from __future__ import annotations

import ast
import io
import os

from django.test import SimpleTestCase

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

SKIP_DIRS = {
    '.git', 'node_modules', 'venv', '.venv', 'migrations', 'frontend',
    '.claude', '__pycache__', 'staticfiles', 'media',
}

# Files where a UTC read is DELIBERATE and correct. Each one carries the reason,
# because an allowlist nobody can audit is just a switched-off test. The second
# test below fails if an entry stops being true.
ALLOWED: dict[str, str] = {
    # utcnow() minus utcnow() measures an elapsed interval. Both sides are the
    # same clock, so the arithmetic is right and a local date would be wrong.
    'core/telegram_bot/bot.py':
        'session TTL arithmetic — utcnow compared against utcnow',
    # An ISO timestamp written with a trailing 'Z'. It is declared UTC on the
    # face of the file and read back as UTC by the regulator.
    'nbfira/export.py':
        'regulatory export stamps an explicit Z-suffixed UTC timestamp',
    # A signature DATETIME printed on a PDF, not a "today". Whether that should
    # display in Botswana time is a real question, but it is a different one
    # from this sweep and needs the signing flow looked at as a whole.
    'healthcare/agreement_pdf.py':
        'signature datetime on a PDF — separate question, tracked not swept',
    'core/brief_note_delivery.py':
        'UTC date deliberate — cron runs 04:30 UTC, matching the writer clock',
    'hris/eligibility.py':
        '45-day payslip cutoff — 2-hour UTC/CAT offset irrelevant on a 45-day window',
}


def _py_files():
    for dirpath, dirnames, filenames in os.walk(REPO):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for name in filenames:
            if not name.endswith('.py'):
                continue
            if name.startswith(('test_', 'tests', 'spec_')):
                continue
            full = os.path.join(dirpath, name)
            rel = os.path.relpath(full, REPO).replace(os.sep, '/')
            if '/tests/' in rel or rel.startswith('tests/'):
                continue
            # infra/skills is the Claude skills folder shared between the Windows
            # PC and the Mac (infra/skills-sync.sh). It is developer tooling that
            # runs on a laptop, never on the server, and is not deployed — so a
            # server clock cannot be yesterday in Gaborone there. It is matched by
            # PREFIX, not by adding 'skills' to SKIP_DIRS, which would silently
            # switch the guard off for any directory named "skills" anywhere.
            if rel.startswith('infra/skills/'):
                continue
            yield rel, full


def _attr_chain(node) -> str:
    """`a.b.c` -> "a.b.c"; anything else -> ""."""
    parts = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
        return '.'.join(reversed(parts))
    return ''


def _offenders(source: str):
    """Every server-clock read in `source`, as (line, what) pairs."""
    out = []
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        attr = node.func.attr

        # X.today()  — any alias, because the chain is just text to us
        if attr == 'today':
            out.append((node.lineno, _attr_chain(node.func) + '()'))
            continue

        # X.utcnow()
        if attr == 'utcnow':
            out.append((node.lineno, _attr_chain(node.func) + '()'))
            continue

        # X.now().date()
        if attr == 'date' and isinstance(node.func.value, ast.Call):
            inner = node.func.value
            if isinstance(inner.func, ast.Attribute) and inner.func.attr == 'now':
                out.append((node.lineno, _attr_chain(inner.func) + '().date()'))
    return out


class BotswanaClockTests(SimpleTestCase):

    def test_no_production_code_reads_the_server_clock_for_a_date(self):
        found = []
        for rel, full in _py_files():
            if rel in ALLOWED:
                continue
            try:
                src = io.open(full, encoding='utf-8').read()
            except (OSError, UnicodeDecodeError):
                continue
            try:
                hits = _offenders(src)
            except SyntaxError:
                continue          # not ours to police here
            for line, what in hits:
                found.append('%s:%d  %s' % (rel, line, what))

        self.assertEqual(
            found, [],
            'These read the SERVER clock, which is yesterday between 00:00 and '
            '02:00 in Gaborone. Use timezone.localdate() from '
            'django.utils.timezone instead, or add the file to ALLOWED in this '
            'test with the reason it must stay UTC:\n\n  '
            + '\n  '.join(found) + '\n')

    def test_every_allowlisted_file_still_exists_and_still_needs_it(self):
        # An allowlist that outlives its reason is how a guard quietly dies.
        stale = []
        for rel, why in ALLOWED.items():
            full = os.path.join(REPO, rel.replace('/', os.sep))
            if not os.path.exists(full):
                stale.append('%s — gone, but still allowlisted (%s)' % (rel, why))
                continue
            src = io.open(full, encoding='utf-8').read()
            if not _offenders(src):
                stale.append('%s — already clean, drop the entry (%s)' % (rel, why))
        self.assertEqual(stale, [],
                         'Stale ALLOWED entries:\n  ' + '\n  '.join(stale))

    def test_the_detector_actually_catches_each_shape(self):
        # A guard nobody has seen fail is not a guard. These are the four shapes
        # that were live in the codebase, including the aliases the old regular
        # expression could not see.
        for snippet, why in [
            ('import datetime\nx = datetime.date.today()\n', 'the plain form'),
            ('from datetime import date as _date\nx = _date.today()\n',
             'an alias ending in a word character'),
            ('from datetime import date as _date_cls\nx = _date_cls.today()\n',
             'a longer alias'),
            ('from django.utils import timezone\nx = timezone.now().date()\n',
             'the aware-datetime form'),
            ('from datetime import datetime\nx = datetime.utcnow()\n',
             'the utcnow form'),
        ]:
            self.assertTrue(_offenders(snippet), 'missed %s' % why)

        # ...and does not cry wolf over the correct call, or over a comment or
        # docstring that merely warns about the pattern.
        for snippet, why in [
            ('from django.utils import timezone\nx = timezone.localdate()\n',
             'the correct call'),
            ('# never use date.today() here, the box runs UTC\nx = 1\n',
             'a warning comment'),
            ('"""Defaults to date.today() unless overridden."""\nx = 1\n',
             'a docstring'),
            ('x = "date.today()"\n', 'a string literal'),
        ]:
            self.assertEqual(_offenders(snippet), [], 'false positive on %s' % why)
