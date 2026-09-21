"""Pure-logic tests for the Leave & Productive-Hours Accountability rules.

No Django / DB needed — these exercise hris/leave_accountability.py directly,
mirroring how workforce.py is tested. Run standalone:

    python hris/tests/test_leave_accountability.py
"""
from datetime import date
from decimal import Decimal

from hris.leave_accountability import (
    leave_hours_for_shortfall,
    enforcement_active,
    flag_excuse,
    is_flagged,
    word_frequency,
)


def test_shortfall_is_pure_hourly():
    assert leave_hours_for_shortfall(Decimal('2.0')) == Decimal('2.00')
    assert leave_hours_for_shortfall(Decimal('0.5')) == Decimal('0.50')
    assert leave_hours_for_shortfall(0) == Decimal('0.00')
    assert leave_hours_for_shortfall(Decimal('-1')) == Decimal('0.00')


def test_enforcement_switches_on_1_sep_2026():
    assert enforcement_active(date(2026, 9, 1)) is True
    assert enforcement_active(date(2026, 8, 31)) is False
    assert enforcement_active(date(2026, 7, 22)) is False


def test_power_cut_at_home_is_flagged():
    assert flag_excuse('There was a power cut at home the whole morning') == ['power cut']
    assert is_flagged('load shedding knocked out my wifi at home')


def test_office_excuse_is_not_flagged():
    # Followed the rule: came to the office despite the power cut -> clean.
    assert flag_excuse('Power cut at home so I came to the office instead') == []


def test_clean_excuse_has_no_flags():
    assert flag_excuse('I was at a client meeting at ABC until noon') == []


def test_word_frequency_strips_stopwords():
    texts = [
        'power cut at home again',
        'power cut at home this morning',
        'client visit at ABC client',
    ]
    freq = dict(word_frequency(texts, top_n=10))
    assert freq.get('power') == 2
    assert freq.get('client') == 2
    assert 'the' not in freq and 'was' not in freq


if __name__ == '__main__':
    import sys
    fns = [v for k, v in sorted(globals().items()) if k.startswith('test_') and callable(v)]
    failed = 0
    for fn in fns:
        try:
            fn()
            print(f'PASS {fn.__name__}')
        except AssertionError as e:
            failed += 1
            print(f'FAIL {fn.__name__}: {e}')
    print(f'\n{len(fns) - failed}/{len(fns)} passed')
    sys.exit(1 if failed else 0)
