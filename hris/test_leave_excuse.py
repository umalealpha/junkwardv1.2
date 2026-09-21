"""
hris/test_leave_excuse.py — Leave Excuse Response dashboard (CFO 2026-07-22).

Covers:
  - build_day LOW / NO productive-hours classification + WorkdayJustification join
  - leave_excuse_rules.evaluate Rule A (power cut) / Rule B (tracker, ticket) logic
  - process_leave_excuses idempotency with LEAVE_EXCUSE_AUTOSEND OFF (records the
    ledger row, sends NOTHING, and never double-records on re-run)

Run:  python manage.py test hris.test_leave_excuse
"""
from __future__ import annotations

import os
from datetime import date
from decimal import Decimal
from unittest import mock

from django.core import mail
from django.core.management import call_command
from django.test import TestCase, override_settings

from hris import leave_excuse_rules as rules
from hris.leave_excuse_service import build_day
from hris.models import HRISProfile, LeaveExcuseAutoResponse, TrackingDirective, WorkdayJustification
from integrations.models import TimeDoctorDailySnapshot
from payroll.models import Employee

DAY = date(2026, 7, 21)


def _person(num: str, name: str, email: str) -> HRISProfile:
    """An active payroll employee, HRIS profile, force-tracked (no payslip needed)."""
    emp = Employee.objects.create(employee_number=num, full_name=name, status='active', email=email)
    prof = HRISProfile.objects.create(employee=emp)
    TrackingDirective.objects.create(employee=emp, expected_to_track=True)
    return prof


def _td_row(prof: HRISProfile, uid: str, productive: float, tracked: float | None = None) -> dict:
    return {
        'user_id': uid, 'name': prof.employee.full_name, 'email': prof.employee.email,
        'hours_tracked': tracked if tracked is not None else productive,
        'productive_hours': productive, 'productive_pct': 0, 'tracked_today': True,
    }


class BuildDayTests(TestCase):
    def setUp(self):
        self.low = _person('LX-LOW', 'Low Hours', 'low@alphadirect.co.bw')          # 0.5h -> LOW
        self.zero = _person('LX-ZERO', 'Zero Hours', 'zero@alphadirect.co.bw')       # 0h  -> NO
        self.untracked = _person('LX-UNT', 'Untracked Person', 'unt@alphadirect.co.bw')  # absent -> NO
        self.ok = _person('LX-OK', 'Full Day', 'ok@alphadirect.co.bw')               # 6h  -> excluded

        # snapshot: low + zero + ok present; untracked deliberately absent.
        TimeDoctorDailySnapshot.objects.create(
            company_id='test-co', as_of=DAY, totals={},
            payload=[
                _td_row(self.low, 'uid-low', 0.5),
                _td_row(self.zero, 'uid-zero', 0.0, tracked=1.0),
                _td_row(self.ok, 'uid-ok', 6.0),
            ])

    def test_low_no_classification(self):
        rows = build_day(DAY)['rows']
        by_name = {r['employee']: r for r in rows}
        # OK person cleared the 4h bar → not listed.
        self.assertNotIn('Full Day', by_name)
        # The other three are listed with the right band.
        self.assertEqual(by_name['Low Hours']['band'], 'low')
        self.assertEqual(by_name['Zero Hours']['band'], 'no')
        self.assertEqual(by_name['Untracked Person']['band'], 'no')
        self.assertEqual(by_name['Low Hours']['productive_hours'], 0.5)
        self.assertEqual(by_name['Untracked Person']['productive_hours'], 0.0)

    def test_default_date_is_latest_snapshot(self):
        # build_day(None) should resolve to the only snapshot day we have.
        out = build_day()
        self.assertEqual(out['date'], DAY.isoformat())
        self.assertTrue(out['snapshot'])

    def test_justification_join_and_status(self):
        WorkdayJustification.objects.create(
            profile=self.low, work_date=DAY, required_hours=Decimal('8.00'),
            tracked_hours=Decimal('0.50'), justification='Power cut at home all morning.',
            reason=WorkdayJustification.Reason.OTHER,
            status=WorkdayJustification.Status.UNJUSTIFIED)
        row = {r['employee']: r for r in build_day(DAY)['rows']}['Low Hours']
        self.assertEqual(row['explanation'], 'Power cut at home all morning.')
        self.assertEqual(row['reason'], 'other')
        self.assertTrue(row['explained'])
        self.assertTrue(row['flagged'])                 # watch-list "power cut"
        self.assertEqual(row['status'], 'not accepted')  # Rule A rejects it
        self.assertEqual(row['auto_action'], 'not accepted')

    def test_no_explanation_status(self):
        # AI gate off so this stays a test of the STATUS logic; with no engines
        # reachable the guardrail would (correctly) hold the name instead.
        with override_settings(WORKFORCE_DATA_GUARD_AI=False):
            row = {r['employee']: r for r in build_day(DAY)['rows']}['Zero Hours']
        self.assertEqual(row['status'], 'no explanation')
        self.assertFalse(row['explained'])
        self.assertFalse(row['held'])

    def test_guardrail_holds_a_zero_it_cannot_prove(self):
        # People-data guardrail (CFO 2026-08-01): with the AI cross-check
        # unreachable the row must be HELD, not chased — it stays on the CFO
        # dashboard but the chaser skips it.
        out = build_day(DAY)
        row = {r['employee']: r for r in out['rows']}['Zero Hours']
        self.assertTrue(row['held'])
        self.assertEqual(row['status'], 'hours still arriving')
        self.assertIn('Zero Hours', out['held_names'])


class RuleTests(TestCase):
    def test_rule_a_power_cut_rejected(self):
        v = rules.evaluate('There was a power cut at home so I could not work.', 'other')
        self.assertEqual(v.rule, 'A')
        self.assertTrue(v.rejected)
        self.assertEqual(v.label, 'not accepted')

    def test_rule_a_office_ok_not_rejected(self):
        # Came to the office despite the power cut → followed the rule → not rejected.
        v = rules.evaluate('Power cut at home but I came to the office and worked.', 'other')
        self.assertFalse(v.rejected)

    def test_rule_b_tracker_no_ticket_rejected(self):
        v = rules.evaluate('Time Doctor was down all morning so nothing tracked.', 'other')
        self.assertEqual(v.rule, 'B')
        self.assertTrue(v.rejected)
        self.assertEqual(v.label, 'not accepted (no ticket)')

    def test_rule_b_tracker_with_ticket_accepted(self):
        v = rules.evaluate('Time Doctor was down, I logged IT ticket #4821 with the help desk.', 'other')
        self.assertEqual(v.rule, 'B')
        self.assertFalse(v.rejected)

    def test_normal_reason_no_rule(self):
        self.assertFalse(rules.evaluate('I was at a client meeting off-site.', 'client_visit').rejected)
        self.assertFalse(rules.evaluate('', '').rejected)

    def test_subjects_and_email_body(self):
        self.assertEqual(rules.SUBJECT_A, 'Why were you not at the office?')
        self.assertEqual(rules.SUBJECT_B, 'Where is your IT ticket for Time Doctor?')
        subj, body = rules.email_for('A', 'Kagiso Mokoena', '2026-07-21')
        self.assertEqual(subj, rules.SUBJECT_A)
        self.assertIn('Kagiso', body)
        self.assertIn('power cut', body.lower())


class CommandIdempotencyTests(TestCase):
    def setUp(self):
        self.prof = _person('LX-CMD', 'Command Person', 'cmd@alphadirect.co.bw')
        TimeDoctorDailySnapshot.objects.create(
            company_id='test-co', as_of=DAY, totals={},
            payload=[_td_row(self.prof, 'uid-cmd', 0.4)])
        WorkdayJustification.objects.create(
            profile=self.prof, work_date=DAY, required_hours=Decimal('8.00'),
            tracked_hours=Decimal('0.40'), justification='Power cut at home, no lights.',
            reason=WorkdayJustification.Reason.OTHER,
            status=WorkdayJustification.Status.UNJUSTIFIED)

    @mock.patch.dict(os.environ, {'LEAVE_EXCUSE_AUTOSEND': '0'})
    def test_autosend_off_records_but_sends_nothing(self):
        mail.outbox = []
        call_command('process_leave_excuses', '--date', DAY.isoformat())

        resp = LeaveExcuseAutoResponse.objects.filter(
            employee=self.prof.employee, work_date=DAY, rule='A')
        self.assertEqual(resp.count(), 1)              # verdict recorded
        self.assertIsNone(resp.first().sent_at)        # but never sent
        self.assertEqual(len(mail.outbox), 0)          # nothing emailed

    @mock.patch.dict(os.environ, {'LEAVE_EXCUSE_AUTOSEND': '0'})
    def test_rerun_does_not_duplicate(self):
        mail.outbox = []
        call_command('process_leave_excuses', '--date', DAY.isoformat())
        call_command('process_leave_excuses', '--date', DAY.isoformat())
        self.assertEqual(
            LeaveExcuseAutoResponse.objects.filter(employee=self.prof.employee, work_date=DAY).count(),
            1)
        self.assertEqual(len(mail.outbox), 0)
