"""B4 and B5 — the three claim clocks.

  Clock 1  supplier invoice APPROVED on the claim  ->  the BANK CONFIRMS it left
  Clock 2  Agreement of Loss AUTHORISED            ->  the BANK CONFIRMS the
                                                        client was paid (target 5)
  Clock 3  the date the claim was NOTIFIED         ->  the decline letter is SENT
                                                        to the client (target 14)

The one rule that outranks everything else here:

    APPROVED IS NOT PAID. Neither clock 1 nor clock 2 may stop on an approval,
    only on a confirmed bank payment.

In Omni that confirmation is FNBBatchSubmission.status == 'settled' with its
settled_at timestamp — not PaymentRequest.status == 'paid', which only means
authorised. These functions take the facts, so the rule is testable without a
live payment.

Clock 3 counts from NOTIFICATION, not from the day the file was opened and not
from the day the police report arrived.
"""
import datetime as dt
import sys
import unittest

from claims_lifecycle.clocks import clock_aol, clock_repudiation, clock_supplier

CHECKS = []
NO_HOLIDAYS = set()


def check(name, got, want):
    CHECKS.append((name, got == want, got, want))


D = dt.date
MON = D(2026, 9, 7)      # Monday


# ---------------------------------------------------------------- clock 1
running = clock_supplier(
    invoice_approved_on=MON,
    bank_settled_on=None,
    today=D(2026, 9, 10),
    target_days=5,
    holidays=NO_HOLIDAYS,
)
check('clock 1 runs while the bank has not confirmed', running['running'], True)
check('clock 1 counts working days so far', running['days'], 3)
check('clock 1 has not stopped', running['stopped_on'], None)

stopped = clock_supplier(
    invoice_approved_on=MON,
    bank_settled_on=D(2026, 9, 10),
    today=D(2026, 9, 30),
    target_days=5,
    holidays=NO_HOLIDAYS,
)
check('clock 1 stops on the bank confirmation', stopped['running'], False)
check('clock 1 stops on the settled date, not today', stopped['stopped_on'], D(2026, 9, 10))
check('clock 1 does not keep counting after it stopped', stopped['days'], 3)
check('inside the target is green', stopped['light'], 'green')

# The whole point of the item: an approval must not stop the clock.
approved_not_paid = clock_supplier(
    invoice_approved_on=MON,
    bank_settled_on=None,
    today=D(2026, 9, 30),
    target_days=5,
    holidays=NO_HOLIDAYS,
    payment_request_status='paid',      # authorised in Omni
    fnb_status='acknowledged',          # FNB has it, money has NOT left
)
check('an approved-but-not-settled payment leaves clock 1 running',
      approved_not_paid['running'], True)
check('an approved-but-not-settled payment does not stop clock 1',
      approved_not_paid['stopped_on'], None)
check('a clock past its target is red', approved_not_paid['light'], 'red')

# A rejected or cancelled batch never moved money, so the clock must not stop
# and must not silently disappear.
for dead in ('failed', 'cancelled'):
    d = clock_supplier(
        invoice_approved_on=MON, bank_settled_on=None, today=D(2026, 9, 12),
        target_days=5, holidays=NO_HOLIDAYS, fnb_status=dead,
    )
    check(f'a {dead} batch does not stop clock 1', d['running'], True)

check('clock 1 is absent before an invoice is approved',
      clock_supplier(invoice_approved_on=None, bank_settled_on=None,
                     today=MON, target_days=5, holidays=NO_HOLIDAYS)['applies'], False)


# ---------------------------------------------------------------- clock 2
aol = clock_aol(
    authorised_on=MON,
    bank_settled_on=None,
    today=D(2026, 9, 12),
    target_days=5,
    holidays=NO_HOLIDAYS,
)
check('clock 2 starts at the authorisation', aol['running'], True)
check('clock 2 target is the five working days asked for', aol['target_days'], 5)
check('clock 2 at exactly the target is still green', aol['days'], 5)
check('clock 2 at the target is green, not red', aol['light'], 'green')

aol_paid = clock_aol(
    authorised_on=MON, bank_settled_on=D(2026, 9, 11), today=D(2026, 9, 30),
    target_days=5, holidays=NO_HOLIDAYS,
)
check('clock 2 stops when the client was actually paid', aol_paid['running'], False)
check('clock 2 counts to the settled date', aol_paid['days'], 4)


# ---------------------------------------------------------------- clock 3
rep = clock_repudiation(
    notified_on=MON,
    letter_sent_on=None,
    today=D(2026, 9, 18),
    target_days=14,
    holidays=NO_HOLIDAYS,
    file_opened_on=D(2026, 9, 9),
    police_report_on=D(2026, 9, 15),
)
check('clock 3 runs until the decline letter is sent', rep['running'], True)
check('clock 3 counts from the notification date, not the file-opened date',
      rep['days'], 10)
check('clock 3 says which date it counted from', rep['from_date'], MON)

rep_done = clock_repudiation(
    notified_on=MON, letter_sent_on=D(2026, 9, 18), today=D(2026, 10, 30),
    target_days=14, holidays=NO_HOLIDAYS,
)
check('clock 3 stops when the letter goes to the client', rep_done['running'], False)
check('clock 3 inside fourteen days is green', rep_done['light'], 'green')

# A claim with no notification date cannot be measured — it must say so rather
# than quietly counting from something else.
no_date = clock_repudiation(
    notified_on=None, letter_sent_on=None, today=MON, target_days=14,
    holidays=NO_HOLIDAYS, file_opened_on=D(2026, 9, 1),
)
check('a claim with no notification date does not fall back to another date',
      no_date['applies'], False)
check('a claim with no notification date is not rendered green',
      no_date['light'], 'unknown')

# ---------------------------------------------------- the light, in full
# Read back over the generated module: the clock light only ever returned green
# or red, and it turned red AT the target rather than past it. The steps bar
# already uses green / amber / red with "at the target is still green"; two
# different meanings for the same colour on one screen is its own bug.
at_target = clock_supplier(invoice_approved_on=MON, bank_settled_on=None,
                           today=D(2026, 9, 12), target_days=5, holidays=NO_HOLIDAYS)
check('five working days against a target of five is still green',
      at_target['days'], 5)
check('at the target the light is green', at_target['light'], 'green')
check('one working day past the target is amber',
      clock_supplier(invoice_approved_on=MON, bank_settled_on=None,
                     today=D(2026, 9, 14), target_days=5,
                     holidays=NO_HOLIDAYS)['light'], 'amber')
check('double the target is red',
      clock_supplier(invoice_approved_on=MON, bank_settled_on=None,
                     today=D(2026, 9, 21), target_days=5,
                     holidays=NO_HOLIDAYS)['light'], 'red')

# ------------------------------------------- a dead batch never stops a clock
# The harder case: a settled DATE is present but the batch itself came back
# failed or cancelled. The money did not move, so the clock must keep running —
# otherwise the fnb_status argument is decoration and the clock lies.
for dead in ('failed', 'cancelled', 'unknown'):
    d = clock_supplier(invoice_approved_on=MON, bank_settled_on=D(2026, 9, 10),
                       today=D(2026, 9, 14), target_days=5, holidays=NO_HOLIDAYS,
                       fnb_status=dead)
    check(f'a {dead} batch with a date on it does not stop clock 1', d['running'], True)
    check(f'a {dead} batch does not record a stop date', d['stopped_on'], None)

check('a settled batch with a date does stop clock 1',
      clock_supplier(invoice_approved_on=MON, bank_settled_on=D(2026, 9, 10),
                     today=D(2026, 9, 14), target_days=5, holidays=NO_HOLIDAYS,
                     fnb_status='settled')['running'], False)

# --------------------------------------- one definition of a working day only
# The clocks must count with the SAME helper the rest of the tracker uses, or
# the two drift and nobody notices until a number is argued about.
import claims_lifecycle.clocks as _clocks
from claims_lifecycle.workdays import count_working_days as _cwd
check('the clocks use the shared working-day helper',
      getattr(_clocks, 'count_working_days', None) is _cwd, True)

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
