"""B14 — every piece behind its own switch, armed in order.

"Every switch listed in one place with its owner, and each one proven to turn
its feature off cleanly." So the registry IS the one place, and this file is the
proof that it covers the batch and that OFF really means off.

Arming order on the morning of 1 October, safest first:
  1 read-only screens (tracker, colours, bars, dashboard)
  2 the customer link
  3 the premium check
  4 the AI summaries (advice only)
  5 the money leg (orders, Agreement of Loss, salvage, settlement feedback)
  6 the repudiation drafts (still need a manager's button)

Nothing is switched on between 07:45 and 12:00.
"""
import sys
import unittest

from claims_lifecycle.switches import (
    ARMING_ORDER,
    SWITCHES,
    freeze_window,
    is_on,
    may_arm_now,
)

CHECKS = []


def check(name, got, want):
    CHECKS.append((name, got == want, got, want))


# --- the registry ---------------------------------------------------------
keys = [s['key'] for s in SWITCHES]
check('every switch key is unique', len(keys), len(set(keys)))
check('every switch names an owner', all(s['owner'] for s in SWITCHES), True)
check('every switch says what it turns off in plain words',
      all(len(s['description']) > 20 for s in SWITCHES), True)
check('every switch is in a wave', all(s['wave'] in ARMING_ORDER for s in SWITCHES), True)
check('every switch defaults to OFF', all(s['default'] is False for s in SWITCHES), True)

# "EVERY PIECE behind its own switch" — one switch per board item, not one
# switch covering five. And it must be in the RIGHT wave: an earlier version of
# this file had the money leg sitting under the customer link and the AI
# summaries under the repudiation drafts, and the only test was "does the item
# appear somewhere", which passed either way.
EXPECTED_WAVE = {
    # read-only screens: the tracker, the colours, the bars and the dashboard
    'B1': 'screens', 'B3': 'screens', 'B4': 'screens', 'B5': 'screens', 'B8': 'screens',
    # the premium check
    'B2': 'premium',
    # the AI summaries, advice only
    'B13': 'ai',
    # the money leg: orders, Agreement of Loss, salvage and settlement feedback
    'B6': 'money', 'B7': 'money', 'B9': 'money', 'B12': 'money',
    # last, the repudiation drafts, which still need a manager's button
    'B10': 'repudiation', 'B11': 'repudiation',
}
for item, wave in EXPECTED_WAVE.items():
    owners = [sw for sw in SWITCHES if item in sw['items']]
    check(f'{item} has exactly one switch of its own', len(owners), 1)
    if owners:
        check(f'{item} is armed in the {wave} wave', owners[0]['wave'], wave)

check('no board item is left without a switch',
      sorted({i for sw in SWITCHES for i in sw['items']}), sorted(EXPECTED_WAVE))
check('the money leg is not armed before the AI summaries',
      all(ARMING_ORDER.index(EXPECTED_WAVE[i]) >= ARMING_ORDER.index('ai')
          for i in ('B6', 'B7', 'B9', 'B12')), True)

# --- the arming order -----------------------------------------------------
check('there are six waves', len(ARMING_ORDER), 6)
check('read-only screens are armed first', ARMING_ORDER[0], 'screens')
check('the repudiation drafts are armed last', ARMING_ORDER[-1], 'repudiation')
check('the money leg is armed before the repudiation drafts',
      ARMING_ORDER.index('money') < ARMING_ORDER.index('repudiation'), True)
check('the AI summaries are armed before the money leg',
      ARMING_ORDER.index('ai') < ARMING_ORDER.index('money'), True)

# --- off really means off -------------------------------------------------
empty = {}
check('an unknown switch is off', is_on('no_such_switch', empty), False)
for s in SWITCHES:
    check(f"{s['key']} is off when nothing is stored", is_on(s['key'], empty), False)
    check(f"{s['key']} is off when stored false", is_on(s['key'], {s['key']: False}), False)
    check(f"{s['key']} is on when stored true", is_on(s['key'], {s['key']: True}), True)

# A truthy-looking string must not switch money on by accident.
check('the string "false" does not switch a feature on',
      is_on(SWITCHES[0]['key'], {SWITCHES[0]['key']: 'false'}), False)
check('the string "0" does not switch a feature on',
      is_on(SWITCHES[0]['key'], {SWITCHES[0]['key']: '0'}), False)
check('the string "true" does switch a feature on',
      is_on(SWITCHES[0]['key'], {SWITCHES[0]['key']: 'true'}), True)

# --- the staff freeze -----------------------------------------------------
check('the freeze window is 07:45 to 12:00', freeze_window(), ('07:45', '12:00'))
check('08:00 is inside the freeze', may_arm_now('08:00'), False)
check('07:45 is inside the freeze', may_arm_now('07:45'), False)
check('11:59 is inside the freeze', may_arm_now('11:59'), False)
check('12:00 is outside the freeze', may_arm_now('12:00'), True)
check('07:44 is outside the freeze', may_arm_now('07:44'), True)
check('19:00 is outside the freeze', may_arm_now('19:00'), True)

def run():
    """Print every check and exit non-zero on the first failure.

    Guarded, because Django's test runner IMPORTS every test module it finds in
    an installed app: a sys.exit at import time reads as "Failed to import test
    module" and turns the whole CI shard red.
    """
    failed = [c for c in CHECKS if not c[1]]
    for name, ok, got, want in CHECKS:
        print(('ok   ' if ok else 'FAIL ') + name
              + ('' if ok else f'  got={got!r} want={want!r}'))
    print(f'{len(CHECKS) - len(failed)}/{len(CHECKS)} checks passed')
    return 1 if failed else 0


class PureChecks(unittest.TestCase):
    """So the Django runner exercises these too, instead of skipping them."""

    def test_every_check_passes(self):
        for name, ok, got, want in CHECKS:
            with self.subTest(check=name):
                self.assertTrue(ok, f'{name}: got={got!r} want={want!r}')


if __name__ == '__main__':
    sys.exit(run())
