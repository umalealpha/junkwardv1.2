"""Spec for rewards/referral.py — pure, no Django.

CFO 2026-09-08 ("7 is good"): refer a friend, both get premium credit. This
module owns the CODE and the ELIGIBILITY only. The credit AMOUNT is a CFO
commercial term and is deliberately not in here - nothing in this file may
return a money value.
"""
import pytest

from rewards.referral import make_code, normalise_code, check_referral, CODE_LENGTH


class TestCode:
    def test_a_code_is_stable_for_the_same_member(self):
        a = make_code('11111111-1111-1111-1111-111111111111')
        b = make_code('11111111-1111-1111-1111-111111111111')
        assert a == b

    def test_different_members_get_different_codes(self):
        a = make_code('11111111-1111-1111-1111-111111111111')
        b = make_code('22222222-2222-2222-2222-222222222222')
        assert a != b

    def test_the_code_is_short_upper_case_and_unambiguous(self):
        code = make_code('11111111-1111-1111-1111-111111111111')
        assert len(code) == CODE_LENGTH
        assert code == code.upper()
        # A code is read off a screen and typed by a human: no characters that
        # can be misread for one another.
        for bad in '01IOL':
            assert bad not in code

    def test_codes_do_not_collide_across_many_members(self):
        codes = {make_code(f'{i:08d}-0000-0000-0000-000000000000') for i in range(2000)}
        assert len(codes) == 2000

    def test_typing_it_in_lower_case_with_spaces_still_matches(self):
        code = make_code('11111111-1111-1111-1111-111111111111')
        assert normalise_code(f'  {code.lower()} ') == code
        assert normalise_code(None) == ''


class TestEligibility:
    OWNER = 'aaaaaaaa-0000-0000-0000-000000000000'
    NEW = 'bbbbbbbb-0000-0000-0000-000000000000'

    def test_a_clean_referral_is_allowed(self):
        r = check_referral(code_owner_id=self.OWNER, new_member_id=self.NEW, existing_pairs=[])
        assert r['ok'] is True
        assert r['reason'] == ''

    def test_you_cannot_refer_yourself(self):
        r = check_referral(code_owner_id=self.OWNER, new_member_id=self.OWNER, existing_pairs=[])
        assert r['ok'] is False
        assert 'self' in r['reason']

    def test_an_unknown_code_owner_is_refused(self):
        r = check_referral(code_owner_id=None, new_member_id=self.NEW, existing_pairs=[])
        assert r['ok'] is False
        assert r['reason'] == 'unknown_code'

    def test_the_same_pair_cannot_be_credited_twice(self):
        pairs = [(self.OWNER, self.NEW)]
        r = check_referral(code_owner_id=self.OWNER, new_member_id=self.NEW, existing_pairs=pairs)
        assert r['ok'] is False
        assert 'already' in r['reason']

    def test_a_member_who_was_already_referred_by_someone_else_is_refused(self):
        other = 'cccccccc-0000-0000-0000-000000000000'
        pairs = [(other, self.NEW)]
        r = check_referral(code_owner_id=self.OWNER, new_member_id=self.NEW, existing_pairs=pairs)
        assert r['ok'] is False
        assert 'already' in r['reason']

    def test_one_member_may_refer_many_different_friends(self):
        friend2 = 'dddddddd-0000-0000-0000-000000000000'
        pairs = [(self.OWNER, self.NEW)]
        r = check_referral(code_owner_id=self.OWNER, new_member_id=friend2, existing_pairs=pairs)
        assert r['ok'] is True

    def test_nothing_in_this_module_returns_a_money_value(self):
        r = check_referral(code_owner_id=self.OWNER, new_member_id=self.NEW, existing_pairs=[])
        for key in r:
            assert 'credit' not in key.lower()
            assert 'amount' not in key.lower()
            assert 'pula' not in key.lower()
