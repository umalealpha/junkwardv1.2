"""The Jeremiah & Taldi problem: P85,000 a month for ~40 cases.

CFO 2026-08-03: "sometimes they are not managing 40 cases and things are slipping out.
I would like you to find a way to control those things."

The invoice cannot show this — it says P85,000 whatever happens. These tests pin the
maths that CAN show it, using the real numbers.
"""
from __future__ import annotations

import datetime
from decimal import Decimal as D

from django.test import TestCase

from bonu.models import (BonuInvoice, BonuInvoiceLine, CaseEvent, LawFirm, LegalCase,
                         RetainerAgreement)
from bonu.retainer import double_dipping, scorecard

TODAY = datetime.date(2026, 7, 31)


def setup_retainer(committed=40, fee='85000'):
    firm = LawFirm.objects.create(name='Jeremiah & Taldi')
    r = RetainerAgreement.objects.create(
        firm=firm, name='Jeremiah & Taldi — BONU panel', monthly_fee=D(fee),
        committed_cases=committed, start_date=datetime.date(2026, 1, 1),
        max_days_no_activity=30, max_days_to_first_action=5)
    return firm, r


def case(firm, r, ref, status='active', instructed='2026-06-01', last='2026-07-20',
         first='2026-06-02', nxt=None, court=None, mtype='divorce'):
    return LegalCase.objects.create(
        firm=firm, retainer=r, case_ref=ref, member_ref=f'M-{ref}', matter_type=mtype,
        status=status,
        instructed_on=datetime.date.fromisoformat(instructed),
        first_action_on=datetime.date.fromisoformat(first) if first else None,
        last_activity_on=datetime.date.fromisoformat(last) if last else None,
        next_action_due=datetime.date.fromisoformat(nxt) if nxt else None,
        court_date=datetime.date.fromisoformat(court) if court else None)


class FeePerCaseTests(TestCase):
    def test_the_intended_cost_per_case_is_2125(self):
        _, r = setup_retainer()
        self.assertEqual(r.fee_per_committed_case, D('2125.00'))   # 85000 / 40

    def test_carrying_the_full_caseload_costs_what_was_intended(self):
        firm, r = setup_retainer()
        for i in range(40):
            case(firm, r, f'C{i}')
        s = scorecard(r, LegalCase.objects.all(), as_of=TODAY)
        self.assertEqual(s['cases_carried'], 40)
        self.assertEqual(s['utilisation_pct'], 100.0)
        self.assertEqual(s['actual_cost_per_case'], 2125.0)
        self.assertEqual(s['shortfall_cases'], 0)
        self.assertEqual(s['retainer_not_earned'], 0.0)
        self.assertIn('earning its fee', s['verdict'])

    def test_half_the_caseload_doubles_the_cost_per_case(self):
        """The whole point: the invoice still says P85,000, but each case now costs
        P4,250 instead of P2,125."""
        firm, r = setup_retainer()
        for i in range(20):
            case(firm, r, f'C{i}')
        s = scorecard(r, LegalCase.objects.all(), as_of=TODAY)
        self.assertEqual(s['cases_carried'], 20)
        self.assertEqual(s['actual_cost_per_case'], 4250.0)
        self.assertEqual(s['overpay_per_case'], 2125.0)
        self.assertEqual(s['shortfall_cases'], 20)
        self.assertEqual(s['retainer_not_earned'], 42500.0)        # 20 x 2125
        self.assertIn('materially overpaying', s['verdict'])

    def test_twenty_four_cases_is_the_real_world_case(self):
        firm, r = setup_retainer()
        for i in range(24):
            case(firm, r, f'C{i}')
        s = scorecard(r, LegalCase.objects.all(), as_of=TODAY)
        self.assertEqual(s['actual_cost_per_case'], 3541.67)
        self.assertEqual(s['retainer_not_earned'], 34000.0)        # 16 x 2125
        self.assertEqual(s['utilisation_pct'], 60.0)

    def test_closed_cases_do_not_count_toward_the_caseload(self):
        firm, r = setup_retainer()
        for i in range(10):
            case(firm, r, f'A{i}')
        for i in range(30):
            case(firm, r, f'Z{i}', status='settled')
        s = scorecard(r, LegalCase.objects.all(), as_of=TODAY)
        self.assertEqual(s['cases_carried'], 10)
        self.assertEqual(s['cases_closed_ever'], 30)


class SlippageTests(TestCase):
    def test_a_case_with_no_movement_beyond_the_sla_is_stale(self):
        firm, r = setup_retainer(committed=1)
        case(firm, r, 'QUIET', last='2026-05-01')          # 91 days quiet at 31 Jul
        s = scorecard(r, LegalCase.objects.all(), as_of=TODAY)
        self.assertEqual(len(s['slippage']['stale']), 1)
        self.assertEqual(s['slippage']['stale'][0]['case_ref'], 'QUIET')
        self.assertGreater(s['slippage']['stale'][0]['days_quiet'], 30)

    def test_a_recently_active_case_is_not_stale(self):
        firm, r = setup_retainer(committed=1)
        case(firm, r, 'BUSY', last='2026-07-25')
        s = scorecard(r, LegalCase.objects.all(), as_of=TODAY)
        self.assertEqual(s['slippage']['stale'], [])

    def test_never_started_is_caught_separately(self):
        firm, r = setup_retainer(committed=1)
        case(firm, r, 'IGNORED', instructed='2026-07-01', first=None, last=None)
        s = scorecard(r, LegalCase.objects.all(), as_of=TODAY)
        self.assertEqual(len(s['slippage']['never_started']), 1)
        self.assertEqual(s['slippage']['never_started'][0]['days_since_instructed'], 30)

    def test_an_overdue_next_action_is_caught(self):
        firm, r = setup_retainer(committed=1)
        case(firm, r, 'LATE', nxt='2026-07-10')
        s = scorecard(r, LegalCase.objects.all(), as_of=TODAY)
        self.assertEqual(s['slippage']['action_overdue'][0]['days_late'], 21)

    def test_a_court_date_that_passed_with_no_update_is_caught(self):
        firm, r = setup_retainer(committed=1)
        case(firm, r, 'COURT', court='2026-07-15', last='2026-07-01')
        s = scorecard(r, LegalCase.objects.all(), as_of=TODAY)
        self.assertEqual(len(s['slippage']['court_date_passed_no_update']), 1)

    def test_a_court_date_followed_by_an_update_is_fine(self):
        firm, r = setup_retainer(committed=1)
        case(firm, r, 'COURTOK', court='2026-07-15', last='2026-07-16')
        s = scorecard(r, LegalCase.objects.all(), as_of=TODAY)
        self.assertEqual(s['slippage']['court_date_passed_no_update'], [])

    def test_abandoned_cases_are_named(self):
        firm, r = setup_retainer(committed=1)
        case(firm, r, 'GONE', status='abandoned')
        s = scorecard(r, LegalCase.objects.all(), as_of=TODAY)
        self.assertEqual(s['slippage']['abandoned'][0]['case_ref'], 'GONE')

    def test_full_caseload_but_slipping_gets_its_own_verdict(self):
        firm, r = setup_retainer(committed=2)
        case(firm, r, 'OK')
        case(firm, r, 'QUIET', last='2026-04-01')
        s = scorecard(r, LegalCase.objects.all(), as_of=TODAY)
        self.assertEqual(s['utilisation_pct'], 100.0)
        self.assertIn('not moving', s['verdict'])


class DoubleDippingTests(TestCase):
    def test_hourly_billing_on_a_case_the_retainer_already_covers(self):
        """The clearest way a retainer leaks: the flat fee lands AND an invoice arrives
        for the same matter."""
        firm, r = setup_retainer(committed=1)
        c = case(firm, r, 'D/500')
        inv = BonuInvoice.objects.create(firm=firm, invoice_number='JT-9',
                                         invoice_date=TODAY, subtotal=D('7500'))
        BonuInvoiceLine.objects.create(invoice=inv, matter_ref='D/500', member_ref='M-1',
                                       amount=D('7500'), service_date=TODAY)
        d = double_dipping(r, [c], BonuInvoiceLine.objects.select_related('invoice'))
        self.assertEqual(d['count'], 1)
        self.assertEqual(d['amount'], 7500.0)

    def test_billing_a_case_outside_the_retainer_is_not_double_dipping(self):
        firm, r = setup_retainer(committed=1)
        c = case(firm, r, 'D/500')
        inv = BonuInvoice.objects.create(firm=firm, invoice_number='JT-10',
                                         invoice_date=TODAY, subtotal=D('1000'))
        BonuInvoiceLine.objects.create(invoice=inv, matter_ref='OTHER/1', member_ref='M-2',
                                       amount=D('1000'), service_date=TODAY)
        self.assertEqual(double_dipping(r, [c],
                         BonuInvoiceLine.objects.select_related('invoice'))['count'], 0)


class CaseEventTests(TestCase):
    def test_an_event_is_the_evidence_that_work_happened(self):
        firm, r = setup_retainer(committed=1)
        c = case(firm, r, 'E/1')
        CaseEvent.objects.create(case=c, happened_on=TODAY, kind='hearing',
                                 detail='Appeared before the Magistrate')
        self.assertEqual(c.events.count(), 1)
        self.assertEqual(c.events.first().kind, 'hearing')

    def test_days_quiet_falls_back_through_first_action_then_instruction(self):
        firm, r = setup_retainer(committed=1)
        c = case(firm, r, 'F/1', instructed='2026-07-01', first=None, last=None)
        self.assertEqual(c.days_quiet(TODAY), 30)


class UnmeasurableTests(TestCase):
    def test_a_retainer_with_no_committed_caseload_says_so_instead_of_dividing_by_zero(self):
        firm, r = setup_retainer(committed=0)
        s = scorecard(r, [], as_of=TODAY)
        self.assertIsNone(s['utilisation_pct'])
        self.assertIsNone(s['fee_per_committed_case'])
        self.assertIn('cannot be measured', s['verdict'])
