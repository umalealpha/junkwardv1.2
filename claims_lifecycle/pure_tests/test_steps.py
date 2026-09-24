"""B3 — traffic light, completion bar, and who is holding the claim.

Three rules the CFO stated and this file enforces:

  1. Ten weighted steps sum to 100. The weight of a step that DOES NOT APPLY is
     redistributed across the steps that do, so a repair claim reaches a real
     100% instead of sticking at 85%.
  2. The colour comes from the CURRENT step's target, never from the overall age
     of the claim.
  3. Every step records WHO IS HOLDING IT, so a slow bank is never counted
     against Claims and a slow assessor is never counted against Finance.

Pure: no database, no clock. `progress()` is handed the step states and the
settings, and returns the number, the colour and the holder.
"""
import sys
import unittest
from decimal import Decimal

from claims_lifecycle.steps import DEFAULT_STEPS, progress

CHECKS = []


def check(name, got, want):
    CHECKS.append((name, got == want, got, want))


def steps(**overrides):
    """Ten steps, all applicable, none done, holder from the default table."""
    out = []
    for s in DEFAULT_STEPS:
        row = {
            'key': s['key'],
            'weight': s['weight'],
            'holder': s['holder'],
            'target_days': s['target_days'],
            'applies': True,
            'done': False,
            'days_in_step': 0,
        }
        row.update(overrides.get(s['key'], {}))
        out.append(row)
    return out


# --- the shape of the default table itself -------------------------------
check('there are ten steps', len(DEFAULT_STEPS), 10)
check('the weights sum to 100', sum(Decimal(str(s['weight'])) for s in DEFAULT_STEPS), Decimal('100'))
check('every step names a holder', all(s['holder'] for s in DEFAULT_STEPS), True)
check('every step has a target in days', all(int(s['target_days']) > 0 for s in DEFAULT_STEPS), True)

# --- the completion bar ---------------------------------------------------
check('nothing done is 0%', progress(steps())['percent'], Decimal('0'))

all_done = progress(steps(**{s['key']: {'done': True} for s in DEFAULT_STEPS}))
check('everything done is 100%', all_done['percent'], Decimal('100'))

# A step that does not apply must NOT leave the claim short of 100.
not_applicable = {DEFAULT_STEPS[4]['key']: {'applies': False}}
done_the_rest = {s['key']: {'done': True} for s in DEFAULT_STEPS if s['key'] != DEFAULT_STEPS[4]['key']}
mixed = progress(steps(**{**not_applicable, **done_the_rest}))
check('a claim that skips a step still reaches 100%', mixed['percent'], Decimal('100'))
check('a skipped step is not counted as done', mixed['steps_done'], 9)

# --- the traffic light ----------------------------------------------------
# Colour is decided by the CURRENT step against ITS OWN target.
first = DEFAULT_STEPS[0]['key']
check('inside the target is green',
      progress(steps(**{first: {'days_in_step': 0}}))['light'], 'green')
check('at the target is still green',
      progress(steps(**{first: {'days_in_step': DEFAULT_STEPS[0]['target_days']}}))['light'], 'green')
check('one day past the target is amber',
      progress(steps(**{first: {'days_in_step': DEFAULT_STEPS[0]['target_days'] + 1}}))['light'], 'amber')
check('double the target is red',
      progress(steps(**{first: {'days_in_step': DEFAULT_STEPS[0]['target_days'] * 2 + 1}}))['light'], 'red')

# The age of the WHOLE claim must not colour it — only the current step.
old_claim_fresh_step = steps(**{
    **{s['key']: {'done': True, 'days_in_step': 900} for s in DEFAULT_STEPS[:3]},
    DEFAULT_STEPS[3]['key']: {'days_in_step': 0},
})
check('an old claim sitting on a fresh step is green',
      progress(old_claim_fresh_step)['light'], 'green')

# --- who is holding it ----------------------------------------------------
check('the holder is the current step\'s holder',
      progress(steps())['holder'], DEFAULT_STEPS[0]['holder'])
check('the current step is the first one not done',
      progress(steps(**{DEFAULT_STEPS[0]['key']: {'done': True}}))['current_step'],
      DEFAULT_STEPS[1]['key'])
check('a step that does not apply is skipped when picking the current step',
      progress(steps(**{DEFAULT_STEPS[0]['key']: {'done': True},
                        DEFAULT_STEPS[1]['key']: {'applies': False}}))['current_step'],
      DEFAULT_STEPS[2]['key'])
check('a finished claim holds nobody', all_done['holder'], '')
# A finished claim used to come back with light='' — an empty string is not a
# colour, and an empty string in a renderer falls into whichever branch is
# written first. It must NAME the state.
check('a finished claim names its state instead of returning a blank light',
      all_done['light'], 'done')
check('a finished claim is never rendered as a live green',
      all_done['light'] == 'green', False)
# The boundary must mean the same thing here as it does on the clocks, or one
# screen shows two meanings for the same colour.
check('exactly double the target is still amber, as on the clocks',
      progress(steps(**{first: {'days_in_step': DEFAULT_STEPS[0]['target_days'] * 2}}))['light'],
      'amber')
check('a finished claim has no current step', all_done['current_step'], '')

# --- targets are settings, not code ---------------------------------------
# The Claims Manager changes a target without a release: pass the step in with a
# different target and the colour must follow it.
check('the colour follows the target it is given, not a constant',
      progress(steps(**{first: {'days_in_step': 3, 'target_days': 99}}))['light'], 'green')

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
