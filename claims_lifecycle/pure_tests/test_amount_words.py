"""B9 — the settlement amount written out in words, in pula and thebe.

"The settlement amount appears in figures AND WRITTEN OUT IN WORDS in pula and
thebe, so a number-to-words step is required and is easy to miss."

No helper existed anywhere in Omni, so this is the whole of it. Money is
Decimal, never float, and rounds HALF UP — rounding is a tax decision in this
company, not a language default.
"""
import sys
import unittest
from decimal import Decimal

from claims_lifecycle.amount_words import pula_in_words

CHECKS = []


def check(name, got, want):
    CHECKS.append((name, got == want, got, want))


def w(value):
    return pula_in_words(Decimal(str(value)))


check('zero', w('0.00'), 'Zero pula and zero thebe')
check('one pula', w('1.00'), 'One pula and zero thebe')
check('two pula', w('2.00'), 'Two pula and zero thebe')
check('thebe only', w('0.50'), 'Zero pula and fifty thebe')
check('one thebe', w('0.01'), 'Zero pula and one thebe')
check('teens', w('13.00'), 'Thirteen pula and zero thebe')
check('twenty one', w('21.00'), 'Twenty-one pula and zero thebe')
check('one hundred', w('100.00'), 'One hundred pula and zero thebe')
check('one hundred and one', w('101.00'), 'One hundred and one pula and zero thebe')
check('nine hundred and ninety nine',
      w('999.99'), 'Nine hundred and ninety-nine pula and ninety-nine thebe')
check('one thousand', w('1000.00'), 'One thousand pula and zero thebe')
check('a real settlement',
      w('85107.50'),
      'Eighty-five thousand one hundred and seven pula and fifty thebe')
check('a million',
      w('1000000.00'), 'One million pula and zero thebe')
check('sum insured sized',
      w('1250000.75'),
      'One million two hundred and fifty thousand pula and seventy-five thebe')

# The trap that made the CFO's "a single cell cannot settle an ambiguous amount"
# rule: three decimal places. 851.075 must round HALF UP to 851.08, never
# truncate to 851.07 and never read as 851,075.
check('three decimals round half up',
      w('851.075'), 'Eight hundred and fifty-one pula and eight thebe')
check('a half thebe rounds up', w('0.005'), 'Zero pula and one thebe')
check('rounding carries into pula', w('0.999'), 'One pula and zero thebe')
check('rounding carries through a nine', w('9.999'), 'Ten pula and zero thebe')

def expect_refused(name, exc_type, value):
    """Record WHICH error came back, so a wrong error cannot pass as the right one."""
    try:
        pula_in_words(value)
    except exc_type as exc:
        CHECKS.append((name, True, type(exc).__name__, exc_type.__name__))
    except Exception as exc:  # noqa: BLE001 — the wrong error is a failure, not a pass
        CHECKS.append((name, False, type(exc).__name__, exc_type.__name__))
    else:
        CHECKS.append((name, False, 'no error', exc_type.__name__))


# Negatives are a caller bug on an Agreement of Loss, not a business case.
expect_refused('a negative settlement is refused', ValueError, Decimal('-1.00'))

# A float must not be accepted silently — floats are how money goes wrong.
expect_refused('a float is refused', TypeError, 1.10)

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
