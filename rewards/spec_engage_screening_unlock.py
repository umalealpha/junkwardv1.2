"""Spec for rewards/screening_unlock.py — pure, no Django.

CFO 2026-09-08 ("10 is good"): pulse checks unlock a free annual screening.
The two failure modes that matter: unlocking WITHOUT the checks, and a member
claiming two screenings in one year. The screening COST is a CFO term and is
not computed here.
"""
from datetime import date

from rewards.screening_unlock import screening_state, REQUIRED_SCANS, WINDOW_DAYS


class TestLocked:
    def test_no_scans_is_locked_and_says_how_many_are_needed(self):
        r = screening_state(scan_count=0, last_unlock=None, today=date(2026, 9, 8))
        assert r['unlocked'] is False
        assert r['remaining'] == REQUIRED_SCANS
        assert str(REQUIRED_SCANS) in r['label']

    def test_one_short_is_still_locked(self):
        r = screening_state(scan_count=REQUIRED_SCANS - 1, last_unlock=None, today=date(2026, 9, 8))
        assert r['unlocked'] is False
        assert r['remaining'] == 1


class TestUnlocked:
    def test_exactly_the_required_scans_unlocks(self):
        r = screening_state(scan_count=REQUIRED_SCANS, last_unlock=None, today=date(2026, 9, 8))
        assert r['unlocked'] is True
        assert r['remaining'] == 0

    def test_more_than_required_stays_unlocked(self):
        r = screening_state(scan_count=REQUIRED_SCANS + 40, last_unlock=None, today=date(2026, 9, 8))
        assert r['unlocked'] is True


class TestOncePerYear:
    def test_a_recent_claim_locks_it_again_even_with_the_scans(self):
        r = screening_state(scan_count=REQUIRED_SCANS + 10, last_unlock=date(2026, 3, 1),
                            today=date(2026, 9, 8))
        assert r['unlocked'] is False
        assert r['nextEligible'] is not None
        assert r['nextEligible'] > date(2026, 9, 8)
        assert 'already' in r['label'].lower() or 'next' in r['label'].lower()

    def test_the_day_before_the_window_closes_is_still_locked(self):
        last = date(2025, 9, 9)
        r = screening_state(scan_count=REQUIRED_SCANS, last_unlock=last, today=date(2026, 9, 8))
        assert (date(2026, 9, 8) - last).days == WINDOW_DAYS - 1
        assert r['unlocked'] is False

    def test_once_the_window_has_passed_it_unlocks_again(self):
        r = screening_state(scan_count=REQUIRED_SCANS, last_unlock=date(2025, 9, 1),
                            today=date(2026, 9, 8))
        assert r['unlocked'] is True


class TestBadData:
    def test_negative_scan_count_is_treated_as_none(self):
        r = screening_state(scan_count=-5, last_unlock=None, today=date(2026, 9, 8))
        assert r['unlocked'] is False
        assert r['remaining'] == REQUIRED_SCANS

    def test_a_future_dated_unlock_still_locks_it(self):
        # Bad data must never open a benefit; it must close it.
        r = screening_state(scan_count=REQUIRED_SCANS, last_unlock=date(2027, 1, 1),
                            today=date(2026, 9, 8))
        assert r['unlocked'] is False

    def test_the_label_is_never_empty(self):
        for scans, last in ((0, None), (REQUIRED_SCANS, None), (REQUIRED_SCANS, date(2026, 8, 1))):
            r = screening_state(scan_count=scans, last_unlock=last, today=date(2026, 9, 8))
            assert isinstance(r['label'], str) and r['label'].strip()
