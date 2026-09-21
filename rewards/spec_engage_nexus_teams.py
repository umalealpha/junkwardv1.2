"""Spec for rewards/nexus_teams.py — pure, no Django.

CFO 2026-09-08 ("6 is good"): family and friend teams. The two failure modes
that matter are a team total that DOUBLE-COUNTS a member, and one member being
able to see another team's data. Visibility is enforced in the view, but the
membership test lives here so both sides use one rule.
"""
import pytest

from rewards.nexus_teams import team_summary, is_team_member, MAX_TEAM_SIZE


def _m(mid, km=0.0, points=0, trips=0):
    return {'id': mid, 'name': f'M{mid}', 'km': km, 'points': points, 'trips': trips}


class TestTotals:
    def test_totals_are_the_sum_of_the_members(self):
        r = team_summary([_m('a', km=10, points=5, trips=2), _m('b', km=4.5, points=3, trips=1)])
        assert r['size'] == 2
        assert r['totalKm'] == 14.5
        assert r['totalPoints'] == 8
        assert r['totalTrips'] == 3

    def test_a_member_listed_twice_is_counted_once(self):
        # The named failure mode: a duplicate row must not inflate the team.
        dup = _m('a', km=10, points=5, trips=2)
        r = team_summary([dup, dict(dup)])
        assert r['size'] == 1
        assert r['totalKm'] == 10
        assert r['totalPoints'] == 5

    def test_an_empty_team_is_zero_not_an_error(self):
        r = team_summary([])
        assert r['size'] == 0
        assert r['totalKm'] == 0
        assert r['totalPoints'] == 0
        assert r['full'] is False

    def test_members_are_returned_ranked_by_points(self):
        r = team_summary([_m('a', points=1), _m('b', points=9), _m('c', points=5)])
        assert [m['id'] for m in r['members']] == ['b', 'c', 'a']

    def test_missing_or_junk_numbers_count_as_zero_never_crash(self):
        r = team_summary([{'id': 'a'}, {'id': 'b', 'km': None, 'points': None}])
        assert r['size'] == 2
        assert r['totalKm'] == 0
        assert r['totalPoints'] == 0

    def test_a_row_with_no_id_is_ignored(self):
        r = team_summary([_m('a', km=3), {'km': 99}])
        assert r['size'] == 1
        assert r['totalKm'] == 3


class TestCapacity:
    def test_a_team_at_the_cap_is_full(self):
        r = team_summary([_m(str(i)) for i in range(MAX_TEAM_SIZE)])
        assert r['size'] == MAX_TEAM_SIZE
        assert r['full'] is True

    def test_one_below_the_cap_has_room(self):
        r = team_summary([_m(str(i)) for i in range(MAX_TEAM_SIZE - 1)])
        assert r['full'] is False
        assert r['spacesLeft'] == 1


class TestMembership:
    def test_a_member_of_the_team_is_recognised(self):
        assert is_team_member('a', [_m('a'), _m('b')]) is True

    def test_an_outsider_is_not_a_member(self):
        assert is_team_member('z', [_m('a'), _m('b')]) is False

    def test_nobody_is_a_member_of_an_empty_team(self):
        assert is_team_member('a', []) is False

    def test_a_blank_id_is_never_a_member(self):
        # Guard: a missing session id must not read as "everyone".
        for blank in (None, '', '   '):
            assert is_team_member(blank, [_m('a')]) is False
