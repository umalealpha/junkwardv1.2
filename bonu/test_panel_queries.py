"""
bonu/test_panel_queries.py — the new controls, tested where they decide something.

These are pure-arithmetic and text functions on purpose, so they are tested with light
stand-in objects rather than a database. The point of each test is a DECISION the code
makes: which firm looks expensive, which case is running hot, what a letter claims, when a
letter gets chased. Get any of those wrong and a person acts on a wrong number.
"""
import datetime as dt
from decimal import Decimal

from django.test import SimpleTestCase

from bonu import panel as P
from bonu import queries as Q

D = Decimal


class Obj:
    """Minimal stand-in with attribute access."""

    def __init__(self, **kw):
        self.__dict__.update(kw)


def firm(pk, name, rate=None, email=''):
    return Obj(pk=pk, name=name, agreed_hourly_rate=rate, trading_name='', contact_email=email)


def line(f, amount, mtype='divorce', ref='M1', units=None, rate=None, basis='hourly',
         date=None, src='firm', earner='A'):
    inv = Obj(firm_id=f.pk, firm=f, invoice_number='I1', invoice_date=date or dt.date(2026, 7, 1))
    return Obj(pk=f'{ref}-{amount}', invoice=inv, amount=D(str(amount)), matter_type=mtype,
               matter_ref=ref, member_ref='MB1', units=units and D(str(units)),
               rate=rate and D(str(rate)), basis=basis, service_date=date or dt.date(2026, 7, 1),
               matter_type_source=src, fee_earner=earner)


def case(f, ref, status='active', instructed=None, closed=None, last=None, mtype='divorce'):
    c = Obj(firm_id=f.pk, case_ref=ref, status=status, matter_type=mtype,
            instructed_on=instructed or dt.date(2026, 1, 10), closed_on=closed,
            last_activity_on=last, first_action_on=None,
            is_open=status in ('instructed', 'active', 'court', 'member'))
    c.days_quiet = lambda as_of=None, _c=c: (
        (as_of or dt.date.today()) - (_c.last_activity_on or _c.instructed_on)).days
    return c


def letter(f, ref, queried=0, conceded=0, status='sent', sent=None, due=None, replied=None,
           chased=0, last_chased=None):
    L = Obj(pk=ref, firm_id=f.pk, firm=f, reference=ref, amount_queried=D(str(queried)),
            amount_conceded=D(str(conceded)), status=status, sent_on=sent, reply_due_on=due,
            replied_on=replied, chased_count=chased, last_chased_on=last_chased)
    L.Status = Obj(SENT='sent')
    return L


class TypicalCostTests(SimpleTestCase):

    def test_typical_is_the_median_so_one_runaway_case_cannot_move_it(self):
        f = firm(1, 'A')
        lines = [line(f, 1000, ref='M1'), line(f, 1000, ref='M2'), line(f, 1000, ref='M3'),
                 line(f, 90000, ref='M4')]
        out = P.typical_cost_by_matter_type(lines)
        # Mean would be 23,250. The median refuses to be dragged by the outlier.
        self.assertEqual(out['divorce']['typical'], D('1000'))

    def test_too_few_cases_says_so_instead_of_inventing_a_yardstick(self):
        f = firm(1, 'A')
        out = P.typical_cost_by_matter_type([line(f, 5000, ref='M1')])
        self.assertIsNone(out['divorce']['typical'])
        self.assertIn('too few', out['divorce']['basis'])

    def test_an_agreed_figure_beats_our_computed_one(self):
        f = firm(1, 'A')
        lines = [line(f, 1000, ref=f'M{i}') for i in range(6)]
        th = Obj(matter_type='divorce', typical_cost=D('4000'), approval_above=D('20000'),
                 warn_multiple=D('2.00'))
        out = P.typical_cost_by_matter_type(lines, [th])
        self.assertEqual(out['divorce']['typical'], D('4000'))
        self.assertIn('agreed', out['divorce']['basis'])

    def test_lines_are_grouped_into_matters_before_being_compared(self):
        # Four lines, two matters. "Typical" must be per MATTER, not per line.
        f = firm(1, 'A')
        lines = [line(f, 500, ref='M1'), line(f, 500, ref='M1'),
                 line(f, 500, ref='M2'), line(f, 500, ref='M2')]
        out = P.typical_cost_by_matter_type(lines)
        self.assertEqual(out['divorce']['matters'], 2)


class EarlyWarningTests(SimpleTestCase):

    def _history(self, f):
        # Six ordinary divorces at 2,000 each → typical 2,000.
        return [line(f, 2000, ref=f'H{i}') for i in range(6)]

    def test_a_case_at_double_the_normal_cost_is_flagged(self):
        f = firm(1, 'A')
        lines = self._history(f) + [line(f, 4000, ref='HOT')]
        hot = [w for w in P.case_early_warnings(lines) if w['matter_ref'] == 'HOT'][0]
        self.assertTrue(hot['running_hot'])
        self.assertEqual(hot['multiple'], D('2.00'))
        self.assertIn('2.00x', hot['message'].replace('×', 'x'))

    def test_a_normal_case_is_not_flagged_and_says_nothing(self):
        f = firm(1, 'A')
        lines = self._history(f) + [line(f, 2100, ref='FINE')]
        fine = [w for w in P.case_early_warnings(lines) if w['matter_ref'] == 'FINE'][0]
        self.assertFalse(fine['running_hot'])
        self.assertEqual(fine['message'], '')

    def test_past_the_approval_limit_is_flagged_even_below_the_warn_multiple(self):
        f = firm(1, 'A')
        th = Obj(matter_type='divorce', typical_cost=None, approval_above=D('3000'),
                 warn_multiple=D('5.00'))
        lines = self._history(f) + [line(f, 3500, ref='OVER')]
        over = [w for w in P.case_early_warnings(lines, [th]) if w['matter_ref'] == 'OVER'][0]
        self.assertTrue(over['over_approval'])
        self.assertIn('approval limit', over['message'])

    def test_an_uncomparable_case_is_reported_not_silently_dropped(self):
        f = firm(1, 'A')
        out = P.case_early_warnings([line(f, 9000, mtype='tenancy', ref='ONLY')])
        self.assertIn('too few', out[0]['message'])


class LeagueTableTests(SimpleTestCase):

    def test_cost_per_matter_and_real_hourly_rate(self):
        f = firm(1, 'A', rate=D('1200'))
        lines = [line(f, 3000, ref='M1', units=2, rate=1500),
                 line(f, 1500, ref='M2', units=1, rate=1500)]
        row = P.league_table([f], lines)[0]
        self.assertEqual(row['spend'], D('4500'))
        self.assertEqual(row['matters'], 2)
        self.assertEqual(row['cost_per_matter'], D('2250.00'))
        self.assertEqual(row['effective_hourly_rate'], D('1500.00'))
        self.assertTrue(row['over_tariff'])          # 1500 charged against 1200 agreed

    def test_no_tariff_on_file_cannot_be_over_tariff(self):
        f = firm(1, 'A', rate=None)
        row = P.league_table([f], [line(f, 3000, units=2, rate=1500)])[0]
        self.assertFalse(row['over_tariff'])

    def test_quiet_cases_and_days_to_close(self):
        f = firm(1, 'A')
        as_of = dt.date(2026, 8, 1)
        cases = [
            case(f, 'C1', status='active', last=dt.date(2026, 5, 1)),        # 92 days quiet
            case(f, 'C2', status='active', last=dt.date(2026, 7, 28)),       # fine
            case(f, 'C3', status='settled', instructed=dt.date(2026, 1, 1),
                 closed=dt.date(2026, 3, 2)),                                # 60 days
        ]
        row = P.league_table([f], [line(f, 1000)], cases, as_of=as_of)[0]
        self.assertEqual(row['open_cases'], 2)
        self.assertEqual(row['cases_gone_quiet'], 1)
        self.assertEqual(row['median_days_to_close'], 60)

    def test_concede_rate_is_money_back_over_money_queried(self):
        f = firm(1, 'A')
        letters = [letter(f, 'Q1', queried=10000, conceded=4000, sent=dt.date(2026, 7, 1),
                          replied=dt.date(2026, 7, 6))]
        row = P.league_table([f], [line(f, 1000)], [], letters)[0]
        self.assertEqual(row['concede_rate'], D('40.0'))
        self.assertEqual(row['median_days_to_answer'], 5)

    def test_most_expensive_per_matter_sorts_first(self):
        cheap, dear = firm(1, 'Cheap'), firm(2, 'Dear')
        lines = [line(cheap, 1000, ref='A'), line(dear, 9000, ref='B')]
        rows = P.league_table([cheap, dear], lines)
        self.assertEqual(rows[0]['firm'], 'Dear')


class UnbilledEstimateTests(SimpleTestCase):

    def test_estimate_is_typical_less_what_is_already_billed(self):
        f = firm(1, 'A')
        history = [line(f, 2000, ref=f'H{i}') for i in range(6)]     # typical 2,000
        billed_on_open = [line(f, 500, ref='OPEN')]
        cases = [case(f, 'OPEN', status='active')]
        out = P.unbilled_estimate(cases, history + billed_on_open)
        self.assertEqual(out['estimated'], D('1500'))
        self.assertEqual(out['cases_priced'], 1)

    def test_a_case_already_over_typical_does_not_accrue_a_negative(self):
        f = firm(1, 'A')
        history = [line(f, 2000, ref=f'H{i}') for i in range(6)]
        cases = [case(f, 'OVER', status='active')]
        out = P.unbilled_estimate(cases, history + [line(f, 9000, ref='OVER')])
        self.assertEqual(out['estimated'], D('0'))

    def test_closed_cases_are_not_accrued(self):
        f = firm(1, 'A')
        history = [line(f, 2000, ref=f'H{i}') for i in range(6)]
        out = P.unbilled_estimate([case(f, 'C', status='settled')], history)
        self.assertEqual(out['estimated'], D('0'))
        self.assertEqual(out['open_cases'], 0)

    def test_an_unpriceable_case_type_is_counted_and_reported(self):
        f = firm(1, 'A')
        out = P.unbilled_estimate([case(f, 'C', mtype='tenancy')], [line(f, 1000, mtype='tenancy')])
        self.assertEqual(out['cases_not_priced'], 1)
        self.assertIn('estimate', out['caveat'].lower())


class ClassificationTests(SimpleTestCase):

    def test_ai_guesses_and_unset_types_are_reported_as_unconfirmed(self):
        f = firm(1, 'A')
        lines = [line(f, 1000, src='firm'), line(f, 1000, src='ai'),
                 line(f, 2000, src='default')]
        out = P.unconfirmed_classification(lines)
        self.assertEqual(out['unconfirmed'], D('3000'))
        self.assertEqual(out['unconfirmed_pct'], D('75.0'))

    def test_all_confirmed_reports_zero(self):
        f = firm(1, 'A')
        out = P.unconfirmed_classification([line(f, 1000, src='manual')])
        self.assertEqual(out['unconfirmed'], D('0'))


class QueryLetterTests(SimpleTestCase):

    def _finding(self, amount, title='Same matter billed twice', question='Please confirm.'):
        return Obj(amount_at_risk=D(str(amount)), title=title, question_for_firm=question)

    def test_the_letter_totals_what_it_is_holding(self):
        f = firm(1, 'Jeremiah & Taldi')
        out = Q.compose(f, [self._finding(4000), self._finding(1500)], today=dt.date(2026, 8, 3))
        self.assertEqual(out['amount_queried'], D('5500'))
        self.assertIn('P5,500.00', out['body'])
        self.assertIn('Jeremiah & Taldi', out['body'])

    def test_biggest_item_is_asked_first(self):
        f = firm(1, 'A')
        out = Q.compose(f, [self._finding(100, question='Small one.'),
                            self._finding(9000, question='Big one.')])
        self.assertLess(out['body'].index('Big one.'), out['body'].index('Small one.'))

    def test_it_carries_a_reply_date(self):
        f = firm(1, 'A')
        out = Q.compose(f, [self._finding(500)], today=dt.date(2026, 8, 3))
        self.assertEqual(out['reply_due_on'], dt.date(2026, 8, 13))
        self.assertIn('13 August 2026', out['body'])

    def test_a_letter_stays_answerable_and_says_what_it_left_out(self):
        f = firm(1, 'A')
        out = Q.compose(f, [self._finding(100 + i) for i in range(20)])
        self.assertEqual(len(out['findings']), Q.MAX_FINDINGS_PER_LETTER)
        self.assertEqual(out['left_out'], 20 - Q.MAX_FINDINGS_PER_LETTER)

    def test_a_finding_with_no_question_still_gets_asked_something_useful(self):
        f = firm(1, 'A')
        out = Q.compose(f, [self._finding(500, title='Rate above tariff', question='')])
        self.assertIn('Rate above tariff', out['body'])

    def test_reference_is_readable_and_unique_per_firm(self):
        f = firm(1, 'Jeremiah & Taldi')
        ref = Q.next_reference(f, 0, today=dt.date(2026, 8, 3))
        self.assertTrue(ref.startswith('BONU-Q-2026-08-'))
        self.assertNotEqual(ref, Q.next_reference(f, 1, today=dt.date(2026, 8, 3)))


class ChaseTests(SimpleTestCase):

    def test_a_letter_past_its_reply_date_is_chased(self):
        f = firm(1, 'A')
        L = letter(f, 'Q1', queried=5000, sent=dt.date(2026, 7, 1), due=dt.date(2026, 7, 11))
        out = Q.chase_list([L], dt.date(2026, 7, 20))
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]['days_overdue'], 9)

    def test_a_letter_still_within_its_date_is_left_alone(self):
        f = firm(1, 'A')
        L = letter(f, 'Q1', sent=dt.date(2026, 7, 1), due=dt.date(2026, 7, 30))
        self.assertEqual(Q.chase_list([L], dt.date(2026, 7, 20)), [])

    def test_it_does_not_chase_the_same_firm_every_single_day(self):
        f = firm(1, 'A')
        L = letter(f, 'Q1', sent=dt.date(2026, 7, 1), due=dt.date(2026, 7, 11),
                   chased=1, last_chased=dt.date(2026, 7, 19))
        self.assertEqual(Q.chase_list([L], dt.date(2026, 7, 20)), [])

    def test_two_chases_escalates_to_the_cfo(self):
        f = firm(1, 'A')
        L = letter(f, 'Q1', queried=5000, sent=dt.date(2026, 7, 1), due=dt.date(2026, 7, 11),
                   chased=2, last_chased=dt.date(2026, 7, 12))
        out = Q.chase_list([L], dt.date(2026, 7, 20))
        self.assertTrue(out[0]['escalate_to_cfo'])
        self.assertIn('escalate', out[0]['line'])

    def test_an_unsent_draft_is_never_chased(self):
        f = firm(1, 'A')
        L = letter(f, 'Q1', status='draft', due=dt.date(2026, 7, 1))
        self.assertEqual(Q.chase_list([L], dt.date(2026, 7, 20)), [])

    def test_recovery_rate_is_what_actually_came_back(self):
        f = firm(1, 'A')
        letters = [letter(f, 'Q1', queried=10000, conceded=2500, status='conceded'),
                   letter(f, 'Q2', queried=10000, conceded=0, status='rejected')]
        out = Q.recovery_summary(letters)
        self.assertEqual(out['amount_recovered'], D('2500'))
        self.assertEqual(out['recovery_rate'], D('12.5'))
        self.assertEqual(out['answered'], 2)
