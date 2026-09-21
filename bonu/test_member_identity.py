"""
bonu/test_member_identity.py — the same member, without keeping the name.

This is the fraud test the CFO asked for, so the two properties that make it work are both
tested directly: the SAME person always produces the SAME token (or the check silently misses
repeat claimers), and the token can NEVER be turned back into a name (or we have quietly
stored member data in a new field).
"""
import datetime as dt
from decimal import Decimal

from django.test import SimpleTestCase, TestCase

from bonu.member_identity import (looks_like_a_member, member_name_from_bill, normalise, token)
from bonu.member_rules import member_summary, run_member_rules

D = Decimal
SALT = 'a' * 64
# Invented names, in the real Odoo shape.
BILL = ('Odoo BILL/2025/07/0005 — INVOICE NO: 5131 - / ALPHA DIRECT INSURANCE COMPANY '
        '(PTY) LTD / THABO MOKGWEETSI - / ALPHA DIRECT INSURANCE COMPANY (PTY) LTD / '
        'THABO MOKGWEETSI')


class NormaliseTests(SimpleTestCase):

    def test_word_order_does_not_matter(self):
        # One firm writes the surname first. Without this, a repeat claimer walks straight
        # through the check.
        self.assertEqual(normalise('THABO MOKGWEETSI'), normalise('Mokgweetsi, Thabo'))

    def test_case_punctuation_and_accents_do_not_matter(self):
        self.assertEqual(normalise('THABO MOKGWEETSI'), normalise('  thabo  mokgweetsí!  '))

    def test_two_different_people_do_not_collide(self):
        self.assertNotEqual(normalise('THABO MOKGWEETSI'), normalise('THABO SERETSE'))


class TokenTests(SimpleTestCase):

    def test_the_same_person_always_gives_the_same_token(self):
        self.assertEqual(token('THABO MOKGWEETSI', SALT), token('Mokgweetsi Thabo', SALT))

    def test_different_people_give_different_tokens(self):
        self.assertNotEqual(token('THABO MOKGWEETSI', SALT), token('THABO SERETSE', SALT))

    def test_the_token_does_not_contain_the_name(self):
        t = token('THABO MOKGWEETSI', SALT)
        self.assertNotIn('THABO', t.upper())
        self.assertNotIn('MOKGWEETSI', t.upper())

    def test_the_token_is_short_and_prefixed_so_it_reads_as_a_reference(self):
        t = token('THABO MOKGWEETSI', SALT)
        self.assertTrue(t.startswith('M-'))
        self.assertEqual(len(t), 14)

    def test_a_different_salt_gives_a_different_token(self):
        # Which is exactly why the salt is created once and never rotated.
        self.assertNotEqual(token('THABO MOKGWEETSI', SALT), token('THABO MOKGWEETSI', 'b' * 64))

    def test_no_salt_means_no_token_rather_than_a_weak_one(self):
        self.assertEqual(token('THABO MOKGWEETSI', ''), '')


class NameExtractionTests(SimpleTestCase):

    def test_it_finds_the_member_on_a_real_bill_shape(self):
        self.assertEqual(member_name_from_bill(BILL), 'THABO MOKGWEETSI')

    def test_it_does_not_mistake_our_own_company_for_a_member(self):
        desc = 'Odoo BILL/2025/07/0001 — / ALPHA DIRECT INSURANCE COMPANY (PTY) LTD'
        self.assertEqual(member_name_from_bill(desc), '')

    def test_it_does_not_mistake_the_law_firm_for_a_member(self):
        self.assertFalse(looks_like_a_member('KUBANGA ATTORNEYS'))
        self.assertFalse(looks_like_a_member('Monthe Marumo & Co'))

    def test_a_single_word_is_not_treated_as_a_member(self):
        self.assertFalse(looks_like_a_member('MISC'))

    def test_nothing_found_returns_empty_not_a_guess(self):
        self.assertEqual(member_name_from_bill('BONU Clamis'), '')


class MemberRuleTests(TestCase):
    """Real rows — these rules join across invoices and firms."""

    def _line(self, firm_name, ref, member_token, amount='5000', mtype='other', date=None):
        from bonu.models import BonuInvoice, BonuInvoiceLine, LawFirm
        firm, _ = LawFirm.objects.get_or_create(name=firm_name)
        d = date or dt.date(2026, 3, 2)
        inv = BonuInvoice.objects.create(firm=firm, invoice_number=ref, invoice_date=d,
                                         subtotal=D(amount), total=D(amount),
                                         source_file='Odoo GL import')
        return BonuInvoiceLine.objects.create(
            invoice=inv, line_no=1, service_date=d, matter_ref=ref,
            member_token=member_token, matter_type=mtype, amount=D(amount), basis='other')

    def _all(self):
        from bonu.models import BonuInvoiceLine
        return list(BonuInvoiceLine.objects.select_related('invoice', 'invoice__firm'))

    def test_one_member_at_two_firms_is_flagged_high(self):
        self._line('KUBANGA ATTORNEYS', 'A1', 'M-aaaa1111')
        self._line('Jere Attorneys', 'B1', 'M-aaaa1111')
        f = [x for x in run_member_rules(self._all()) if x['code'] == 'MEMBER_MULTIPLE_FIRMS']
        self.assertEqual(len(f), 1)
        self.assertEqual(f[0]['severity'], 'high')
        # No money attached: using the member's whole spend double-counted against the limit
        # breach for the same person and inflated the weekly headline.
        self.assertEqual(f[0]['amount_at_risk'], D('0'))
        # The token appears; a name never does.
        self.assertIn('M-aaaa1111', f[0]['detail'])

    def test_one_member_at_one_firm_is_not_flagged_for_multiple_firms(self):
        self._line('KUBANGA ATTORNEYS', 'A1', 'M-aaaa1111')
        self._line('KUBANGA ATTORNEYS', 'A2', 'M-aaaa1111')
        codes = {x['code'] for x in run_member_rules(self._all())}
        self.assertNotIn('MEMBER_MULTIPLE_FIRMS', codes)

    def test_three_matters_for_one_member_is_flagged(self):
        for i in range(3):
            self._line('KUBANGA ATTORNEYS', f'A{i}', 'M-aaaa1111')
        codes = {x['code'] for x in run_member_rules(self._all())}
        self.assertIn('MEMBER_MANY_MATTERS', codes)

    def test_two_matters_is_not_yet_flagged_as_many(self):
        for i in range(2):
            self._line('KUBANGA ATTORNEYS', f'A{i}', 'M-aaaa1111')
        codes = {x['code'] for x in run_member_rules(self._all())}
        self.assertNotIn('MEMBER_MANY_MATTERS', codes)

    def test_the_same_member_with_two_divorces_is_flagged(self):
        self._line('KUBANGA ATTORNEYS', 'A1', 'M-aaaa1111', mtype='divorce')
        self._line('KUBANGA ATTORNEYS', 'A2', 'M-aaaa1111', mtype='divorce')
        f = [x for x in run_member_rules(self._all()) if x['code'] == 'MEMBER_REPEAT_TYPE']
        self.assertEqual(len(f), 1)
        self.assertIn('divorce', f[0]['detail'])

    def test_unclassified_matters_do_not_trigger_the_repeat_type_rule(self):
        # Everything imported from the ledger is 'other'; treating that as "the same kind of
        # case twice" would flag every member who ever claimed twice.
        self._line('KUBANGA ATTORNEYS', 'A1', 'M-aaaa1111')
        self._line('KUBANGA ATTORNEYS', 'A2', 'M-aaaa1111')
        codes = {x['code'] for x in run_member_rules(self._all())}
        self.assertNotIn('MEMBER_REPEAT_TYPE', codes)

    def test_rows_with_no_member_token_produce_nothing(self):
        self._line('KUBANGA ATTORNEYS', 'A1', '')
        self._line('Jere Attorneys', 'B1', '')
        self.assertEqual(run_member_rules(self._all()), [])

    def test_different_members_are_not_conflated(self):
        self._line('KUBANGA ATTORNEYS', 'A1', 'M-aaaa1111')
        self._line('Jere Attorneys', 'B1', 'M-bbbb2222')
        self.assertEqual(run_member_rules(self._all()), [])


class MemberCoverageTests(TestCase):

    def _line(self, ref, member_token, amount='1000'):
        from bonu.models import BonuInvoice, BonuInvoiceLine, LawFirm
        firm, _ = LawFirm.objects.get_or_create(name='KUBANGA ATTORNEYS')
        inv = BonuInvoice.objects.create(firm=firm, invoice_number=ref,
                                         invoice_date=dt.date(2026, 3, 2),
                                         subtotal=D(amount), total=D(amount))
        return BonuInvoiceLine.objects.create(invoice=inv, line_no=1, matter_ref=ref,
                                              member_token=member_token, amount=D(amount),
                                              basis='other')

    def test_coverage_says_what_share_of_spend_has_a_member_behind_it(self):
        from bonu.models import BonuInvoiceLine
        self._line('A1', 'M-aaaa1111', '3000')
        self._line('A2', '', '1000')
        s = member_summary(list(BonuInvoiceLine.objects.select_related('invoice', 'invoice__firm')))
        self.assertEqual(s['total_spend'], D('4000'))
        self.assertEqual(s['spend_tied_to_a_member'], D('3000'))
        self.assertEqual(s['coverage_pct'], D('75.0'))
        self.assertEqual(s['members_identified'], 1)

    def test_it_says_plainly_that_premium_status_is_not_known_here(self):
        from bonu.models import BonuInvoiceLine
        self._line('A1', 'M-aaaa1111')
        s = member_summary(list(BonuInvoiceLine.objects.select_related('invoice', 'invoice__firm')))
        self.assertIn('premium', s['note'].lower())
        self.assertIn('graphite', s['note'].lower())


class SaltTests(TestCase):

    def test_the_salt_is_created_once_and_reused(self):
        from bonu.member_identity import get_salt
        from bonu.models import MemberTokenSalt
        first = get_salt()
        second = get_salt()
        self.assertEqual(first, second)
        self.assertEqual(MemberTokenSalt.objects.count(), 1)
        self.assertGreaterEqual(len(first), 32)


class OnlyRealMoneyIsCountedTests(TestCase):
    """Two findings about one member must not add their money together.

    Live on 2026-08-03 the weekly digest headline read P1,540,297 because a member's total
    spend was counted once for "43 matters" and again for the limit breach. The defensible
    figure was P755,970.
    """

    def _line(self, tok, amount, ref, date):
        from bonu.models import BonuInvoice, BonuInvoiceLine, LawFirm
        firm, _ = LawFirm.objects.get_or_create(name='KUBANGA ATTORNEYS')
        inv = BonuInvoice.objects.create(firm=firm, invoice_number=ref, invoice_date=date,
                                         subtotal=D(str(amount)), total=D(str(amount)))
        return BonuInvoiceLine.objects.create(invoice=inv, line_no=1, matter_ref=ref,
                                              member_token=tok, amount=D(str(amount)),
                                              basis='other', service_date=date)

    def test_only_the_limit_breach_carries_money(self):
        from bonu.models import BonuInvoiceLine
        for i in range(4):
            self._line('M-a', 50000, f'A{i}', dt.date(2026, 2, 10 + i))
        rows = list(BonuInvoiceLine.objects.select_related('invoice', 'invoice__firm'))
        found = run_member_rules(rows)
        money = {f['code']: f['amount_at_risk'] for f in found}
        # 200,000 spent, 90,000 allowed → 110,000 is the only money in play.
        self.assertEqual(money['MEMBER_OVER_ANNUAL_LIMIT'], D('110000'))
        self.assertEqual(money['MEMBER_MANY_MATTERS'], D('0'))
        self.assertEqual(sum(money.values()), D('110000'))


class AnnualLimitTests(TestCase):
    """P90,000 per member per year — a breach of cover, not a matter of judgement.

    CFO 2026-08-03: "the total admissible legal cost per member is 90,000 BWP in total per
    year, we need to flag if it goes above."
    """

    def _line(self, tok, amount, ref, date, firm_name='KUBANGA ATTORNEYS'):
        from bonu.models import BonuInvoice, BonuInvoiceLine, LawFirm
        firm, _ = LawFirm.objects.get_or_create(name=firm_name)
        inv = BonuInvoice.objects.create(firm=firm, invoice_number=ref, invoice_date=date,
                                         subtotal=D(str(amount)), total=D(str(amount)))
        return BonuInvoiceLine.objects.create(invoice=inv, line_no=1, matter_ref=ref,
                                              member_token=tok, amount=D(str(amount)),
                                              basis='other', service_date=date)

    def _all(self):
        from bonu.models import BonuInvoiceLine
        return list(BonuInvoiceLine.objects.select_related('invoice', 'invoice__firm'))

    def _breach(self):
        return [f for f in run_member_rules(self._all())
                if f['code'] == 'MEMBER_OVER_ANNUAL_LIMIT']

    def test_a_member_over_ninety_thousand_in_one_calendar_year_is_flagged_high(self):
        self._line('M-a', 60000, 'A1', dt.date(2026, 1, 10))
        self._line('M-a', 45000, 'A2', dt.date(2026, 4, 10))
        f = self._breach()
        self.assertEqual(len(f), 1)
        self.assertEqual(f[0]['severity'], 'high')
        # 105,000 - 90,000 = 15,000 above cover.
        self.assertEqual(f[0]['amount_at_risk'], D('15000'))
        self.assertIn('90,000', f[0]['detail'])

    def test_a_member_inside_the_limit_is_not_flagged(self):
        self._line('M-a', 60000, 'A1', dt.date(2026, 1, 10))
        self._line('M-a', 29000, 'A2', dt.date(2026, 4, 10))
        self.assertEqual(self._breach(), [])

    def test_exactly_on_the_limit_is_not_a_breach(self):
        self._line('M-a', 90000, 'A1', dt.date(2026, 1, 10))
        self.assertEqual(self._breach(), [])

    def test_the_year_is_the_calendar_year(self):
        # CFO 2026-08-03: the year is 1 January to 31 December. 60k in December 2025 and 45k in
        # January 2026 is 105k inside twelve months but only 60k and 45k per YEAR, so neither
        # year breaches. It is raised as a watch instead, never as a breach.
        self._line('M-a', 60000, 'A1', dt.date(2025, 12, 10))
        self._line('M-a', 45000, 'A2', dt.date(2026, 1, 15))
        self.assertEqual(self._breach(), [])
        watch = [f for f in run_member_rules(self._all())
                 if f['code'] == 'MEMBER_LIMIT_STRADDLES_YEAR']
        self.assertEqual(len(watch), 1)
        self.assertEqual(watch[0]['severity'], 'medium')
        self.assertEqual(watch[0]['amount_at_risk'], D('0'))   # not money owed back

    def test_a_breach_names_the_year_it_happened_in(self):
        self._line('M-a', 120000, 'A1', dt.date(2025, 5, 10))
        f = self._breach()
        self.assertEqual(len(f), 1)
        self.assertIn('2025', f[0]['title'])
        self.assertIn('1 January to 31 December', f[0]['detail'])

    def test_two_years_each_over_the_limit_give_two_findings(self):
        self._line('M-a', 120000, 'A1', dt.date(2025, 5, 10))
        self._line('M-a', 100000, 'A2', dt.date(2026, 5, 10))
        f = self._breach()
        self.assertEqual(len(f), 2)
        self.assertEqual(sorted(x['amount_at_risk'] for x in f), [D('10000'), D('30000')])

    def test_december_and_january_each_inside_the_limit_is_not_a_breach(self):
        self._line('M-a', 89000, 'A1', dt.date(2025, 12, 31))
        self._line('M-a', 89000, 'A2', dt.date(2026, 1, 1))
        self.assertEqual(self._breach(), [])

    def test_spend_in_different_years_is_not_added_together(self):
        self._line('M-a', 60000, 'A1', dt.date(2024, 1, 10))
        self._line('M-a', 45000, 'A2', dt.date(2026, 4, 10))
        self.assertEqual(self._breach(), [])

    def test_spend_across_two_firms_still_counts_against_one_limit(self):
        # The limit is per MEMBER. Splitting across firms must not create two allowances.
        self._line('M-a', 50000, 'A1', dt.date(2026, 1, 10), firm_name='KUBANGA ATTORNEYS')
        self._line('M-a', 50000, 'B1', dt.date(2026, 2, 10), firm_name='Jere Attorneys')
        f = self._breach()
        self.assertEqual(len(f), 1)
        self.assertEqual(f[0]['amount_at_risk'], D('10000'))

    def test_two_different_members_each_get_their_own_allowance(self):
        self._line('M-a', 80000, 'A1', dt.date(2026, 1, 10))
        self._line('M-b', 80000, 'B1', dt.date(2026, 1, 10))
        self.assertEqual(self._breach(), [])

    def test_undated_spend_is_reported_not_dropped(self):
        from bonu.member_rules import spend_by_calendar_year
        rows = self._all()
        self._line('M-a', 95000, 'A1', dt.date(2026, 2, 10))
        by_year = spend_by_calendar_year(self._all())
        self.assertEqual(by_year[2026], D('95000'))

    def test_the_summary_counts_the_breaches_and_the_money_over(self):
        self._line('M-a', 150000, 'A1', dt.date(2026, 1, 10))
        self._line('M-b', 100000, 'B1', dt.date(2026, 2, 10))
        s = member_summary(self._all())
        self.assertEqual(s['annual_limit'], D('90000'))
        self.assertEqual(s['members_over_limit'], 2)
        # (150,000-90,000) + (100,000-90,000) = 70,000
        self.assertEqual(s['amount_over_limit'], D('70000'))
        self.assertIn('90,000', s['limit_note'])
