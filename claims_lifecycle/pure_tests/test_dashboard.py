"""B8 — the Claims Performance Dashboard, and the split that is a real control.

"THE SPLIT IS ENFORCED IN THE BACKEND BY ROLE AND NOT BY HIDING A COLUMN,
because a hidden column is not a control."

So the money side must be ABSENT from the payload for someone not entitled to
it — not present-but-hidden, not zeroed, not empty-stringed. This file proves
absence.

  Performance side  everyone named on the dashboard
  Money side        claims, finance, operations, executive
  Underwriters      money on THEIR OWN BOOK only
  Human Capital     no money at all
"""
import sys
import unittest

from claims_lifecycle.dashboard import MONEY_KEYS, PERFORMANCE_KEYS, build_dashboard

CHECKS = []


def check(name, got, want):
    CHECKS.append((name, got == want, got, want))


FACTS = {
    'claims_by_step': {'registered': 4, 'assessed': 2},
    'lights': {'green': 3, 'amber': 2, 'red': 1},
    'average_completion': 61,
    'stuck_under_half': 2,
    'clocks': {'supplier': 4, 'aol': 3, 'repudiation': 11},
    'notification_to_first_contact': 2,
    'repudiation_rate': 7,
    'repudiation_duration': 12,
    'assessor_turnaround': {'MotoLink': 3},
    'salvage_in_yard': 5,
    'salvage_invoices_not_raised': 1,
    'reopened_after_closing': 1,
    'settlements': '1200000.00',
    'supplier_invoices': '340000.00',
    'average_settlement': '48000.00',
    'salvage_recovered': '96000.00',
    'excess_collected': '31000.00',
    'by_underwriter': {'u1': {'settlements': '10.00'}, 'u2': {'settlements': '20.00'}},
}


def keys_of(*roles, **kw):
    return set(build_dashboard(FACTS, roles=list(roles), **kw).keys())


# --- everybody named sees the performance side ---------------------------
for role in ('claims', 'finance', 'operations', 'executive', 'underwriting', 'hris'):
    got = keys_of(role)
    check(f'{role} sees the performance side', PERFORMANCE_KEYS <= got, True)

# --- the money side -------------------------------------------------------
for role in ('claims', 'finance', 'operations', 'executive'):
    check(f'{role} sees the money side', MONEY_KEYS <= keys_of(role), True)

hc = keys_of('hris')
check('Human Capital sees no money key at all', MONEY_KEYS & hc, set())
check('Human Capital has no settlements key', 'settlements' in hc, False)
check('Human Capital money is ABSENT, not zeroed',
      any(k in hc for k in MONEY_KEYS), False)

# --- underwriters: their own book only ------------------------------------
uw = build_dashboard(FACTS, roles=['underwriting'], underwriter_id='u1')
check('an underwriter gets a money section', 'settlements' in uw, True)
check('an underwriter sees only their own book', uw['settlements'], '10.00')
check('an underwriter cannot see the whole book', uw['settlements'] == FACTS['settlements'], False)
check('an underwriter cannot see another underwriter',
      'u2' in str(uw.get('by_underwriter', {})), False)

uw_nobook = build_dashboard(FACTS, roles=['underwriting'], underwriter_id=None)
check('an underwriter with no book sees no money', MONEY_KEYS & set(uw_nobook), set())

# --- a role nobody granted gets nothing extra -----------------------------
check('an unknown role gets no money', MONEY_KEYS & keys_of('random_role'), set())
check('no roles at all gets no money', MONEY_KEYS & keys_of(), set())

# --- one entitled role is enough, and a mix does not leak -----------------
check('claims plus hris still sees money', MONEY_KEYS <= keys_of('claims', 'hris'), True)
check('hris plus an unknown role still sees none',
      MONEY_KEYS & keys_of('hris', 'random_role'), set())

# --- the two key sets must not overlap, or the control is meaningless -----
check('the money keys and the performance keys are separate sets',
      MONEY_KEYS & PERFORMANCE_KEYS, set())
check('the money side is not empty', len(MONEY_KEYS) >= 5, True)
check('the performance side is not empty', len(PERFORMANCE_KEYS) >= 10, True)

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
