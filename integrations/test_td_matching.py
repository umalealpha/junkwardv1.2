"""
integrations/test_td_matching.py

Tests for THE one shared Time Doctor ↔ payroll matcher (Fable review
2026-07-14) and the workforce circuit breaker. The matcher tests run with
use_map=False so they are pure (no DB); the map-table pass is covered by
one DB test at the end.

Run:  python manage.py test integrations.test_td_matching
"""
from __future__ import annotations

from unittest import mock

from django.test import SimpleTestCase, TestCase

from integrations.td_matching import (TDMatcher, active_td_users, norm_name, name_tokens,
                                       fold_uid, collapse_users)
from hris import exceptions_report as er
from integrations.timedoctor import aggregate as td_aggregate
import datetime as _dt


class MultiMachineMergeTests(SimpleTestCase):
    """A person on two machines (two TD accounts) must appear ONCE — and their
    hours must be the BUSIEST MACHINE, never the sum (CFO 2026-07-29).

    These tests used to assert the sum. That was the bug: the CFO's 28 July
    reached all 79 staff as 21.64 h in a 24-hour day, because both machines'
    clocks were added together. Uses the real Prathap laptop/desktop alias uids."""
    LAPTOP = 'aetqn--rymG6S7qQ'
    DESKTOP = 'XnseOWIRLwAEEwM1'

    def test_fold_uid_maps_both_machines_to_one(self):
        self.assertEqual(fold_uid(self.LAPTOP), self.DESKTOP)
        self.assertEqual(fold_uid(self.DESKTOP), self.DESKTOP)
        self.assertEqual(fold_uid('someone-else'), 'someone-else')   # no-op for others
        self.assertIsNone(fold_uid(None))

    def test_collapse_users_yields_one_canonical_row(self):
        users = [{'id': self.LAPTOP, 'name': 'PrathapAsus'},
                 {'id': self.DESKTOP, 'name': 'Prathap Ganesharajah (ExCo)'},
                 {'id': 'z9', 'name': 'Someone Else'}]
        out = collapse_users(users)
        ids = sorted(u['id'] for u in out)
        self.assertEqual(ids, sorted([self.DESKTOP, 'z9']))          # laptop folded away
        pra = [u for u in out if u['id'] == self.DESKTOP][0]
        self.assertEqual(pra['name'], 'Prathap Ganesharajah +')

    def test_per_user_day_reports_busiest_machine_not_the_sum(self):
        users = collapse_users([{'id': self.LAPTOP, 'name': 'PrathapAsus'},
                                {'id': self.DESKTOP, 'name': 'Prathap Ganesharajah (ExCo)'}])
        wl = [[{'userId': self.DESKTOP, 'time': 3600, 'start': '2026-07-24T07:00:00Z'}],
              [{'userId': self.LAPTOP, 'time': 1800, 'start': '2026-07-24T13:00:00Z'}]]
        day = er.per_user_day(users, wl)
        self.assertNotIn(self.LAPTOP, day)                            # no separate laptop row
        self.assertEqual(day[self.DESKTOP]['sec'], 3600)              # busiest machine, NOT 5400
        self.assertEqual(day[self.DESKTOP]['devices'], 2)             # but we know there were two
        # Arrival/departure still span BOTH machines — being at work is about the
        # person, not which laptop happened to notice first.
        self.assertIsNotNone(day[self.DESKTOP]['start'])
        self.assertIsNotNone(day[self.DESKTOP]['end'])

    def test_per_user_day_keeps_manual_time_out_of_the_clock(self):
        """mode='manual' is time somebody typed in, not time a device observed.
        It inflated the CFO's 28 July by 4.70 h on top of the double count."""
        users = collapse_users([{'id': self.DESKTOP, 'name': 'Prathap Ganesharajah (ExCo)'}])
        wl = [[{'userId': self.DESKTOP, 'time': 3600, 'mode': 'computer',
                'start': '2026-07-24T07:00:00Z'},
               {'userId': self.DESKTOP, 'time': 7200, 'mode': 'manual',
                'start': '2026-07-24T13:00:00Z'}]]
        day = er.per_user_day(users, wl)
        self.assertEqual(day[self.DESKTOP]['sec'], 3600)              # observed only
        self.assertEqual(day[self.DESKTOP]['manual_sec'], 7200)       # kept, but separate

    def test_aggregate_reports_busiest_machine_across_machines(self):
        users = collapse_users([{'id': self.LAPTOP, 'name': 'PrathapAsus'},
                                {'id': self.DESKTOP, 'name': 'Prathap Ganesharajah (ExCo)'}])
        wl = [[{'userId': self.DESKTOP, 'time': 3600, 'start': '2026-07-24T07:00:00Z'}],
              [{'userId': self.LAPTOP, 'time': 1800, 'start': '2026-07-24T13:00:00Z'}]]
        # timeuse: one productive bucket per machine, attributed by request order
        tu = [[{'time': 3600, 'score': 4}], [{'time': 1800, 'score': 4}]]
        agg = td_aggregate(users, wl, tu, [], [], as_of=_dt.date(2026, 7, 24),
                           td_user_ids=[self.DESKTOP, self.LAPTOP])
        members = {m['user_id']: m for m in agg['members']}
        self.assertIn(self.DESKTOP, members)
        self.assertNotIn(self.LAPTOP, members)                       # one member, not two
        # The busiest machine's record is taken WHOLE — tracked and productive
        # together — so the derived percentages stay coherent. Mixing one
        # machine's clock with another's productivity split would corrupt them.
        self.assertEqual(members[self.DESKTOP]['tracked_seconds'], 3600)
        self.assertEqual(members[self.DESKTOP]['productive_seconds'], 3600)
        self.assertEqual(members[self.DESKTOP]['machine_count'], 2)


class FakeEmp:
    _seq = 0

    def __init__(self, full_name, email=''):
        FakeEmp._seq += 1
        self.id = FakeEmp._seq
        self.full_name = full_name
        self.email = email


def td(uid, name, email=''):
    return {'id': uid, 'name': name, 'email': email}


class NormTests(SimpleTestCase):
    def test_norm_strips_tags_and_punctuation(self):
        self.assertEqual(norm_name('Prathap Ganesharajah (ExCo)'), 'prathap ganesharajah')
        self.assertEqual(norm_name('Wangu W. Moses'), 'wangu w moses')

    def test_tokens_drop_single_initials(self):
        self.assertEqual(name_tokens(norm_name('Wangu W. Moses')), frozenset({'wangu', 'moses'}))


class MatcherTests(SimpleTestCase):
    def m(self, users, emps):
        return TDMatcher(users, emps, use_map=False)

    def test_exact_name_and_middle_name_variants_match(self):
        emps = [FakeEmp('Wangu Moses'), FakeEmp('Refilwe Otukile'), FakeEmp('Atlang Dikgang')]
        users = [td('u1', 'Wangu W. Moses'), td('u2', 'Caroline Refilwe Otukile'),
                 td('u3', 'Atlang Dikgang')]
        m = self.m(users, emps)
        self.assertEqual(len(m.employee_for_uid), 3)
        self.assertEqual(m.employee_for_uid['u1'].full_name, 'Wangu Moses')
        self.assertEqual(m.employee_for_uid['u2'].full_name, 'Refilwe Otukile')

    def test_ambiguous_token_subset_never_guesses(self):
        # Two TD accounts both subset-match the same employee → no match at all
        # (guessing could email one person another person's hours).
        emps = [FakeEmp('John Smith')]
        users = [td('u1', 'John Peter Smith'), td('u2', 'John A. Smith')]
        m = self.m(users, emps)
        self.assertEqual(m.employee_for_uid, {})
        self.assertEqual(len(m.unmatched_td), 2)
        self.assertEqual(len(m.unmatched_employees), 1)

    def test_reverse_ambiguity_never_guesses(self):
        # One TD account whose tokens subset-match two employees → no match.
        emps = [FakeEmp('John Smith'), FakeEmp('John Smith Junior')]
        m = self.m([td('u1', 'John Smith')], emps)
        # exact-name pass still links the exact 'John Smith' uniquely…
        self.assertEqual(m.employee_for_uid['u1'].full_name, 'John Smith')
        # …but a middle-name variant forces the subset pass, which must refuse:
        emps2 = [FakeEmp('John Peter Smith'), FakeEmp('John David Smith')]
        m2 = self.m([td('u9', 'John Smith')], emps2)
        self.assertNotIn('u9', m2.employee_for_uid)
        self.assertEqual(len(m2.unmatched_td), 1)

    def test_single_token_names_never_match(self):
        emps = [FakeEmp('John Smith')]
        m = self.m([td('u1', 'John')], emps)
        self.assertNotIn('u1', m.employee_for_uid)

    def test_exact_duplicate_names_never_guess(self):
        emps = [FakeEmp('Anna Kea'), FakeEmp('Anna Kea')]
        users = [td('u1', 'Anna Kea')]
        m = self.m(users, emps)
        self.assertEqual(m.employee_for_uid, {})

    def test_email_beats_name(self):
        emps = [FakeEmp('K Mo', 'kmo@alphadirect.co.bw')]
        users = [td('u1', 'Completely Different', 'KMO@alphadirect.co.bw')]
        m = self.m(users, emps)
        self.assertEqual(m.employee_for_uid['u1'].email, 'kmo@alphadirect.co.bw')

    def test_each_side_used_once(self):
        emps = [FakeEmp('Bame Tsala'), FakeEmp('Bame Tsala Junior')]
        users = [td('u1', 'Bame Tsala'), td('u2', 'Bame Tsala Junior')]
        m = self.m(users, emps)
        self.assertEqual(len(m.employee_for_uid), 2)
        self.assertNotEqual(m.employee_for_uid['u1'].id, m.employee_for_uid['u2'].id)

    def test_ghosts_and_queue_split(self):
        emps = [FakeEmp('Has Tracker'), FakeEmp('No Tracker At All')]
        users = [td('u1', 'Has Tracker'), td('u2', 'TheRiskCo Contractor')]
        m = self.m(users, emps)
        self.assertEqual([e.full_name for e in m.unmatched_employees], ['No Tracker At All'])
        self.assertEqual([u['name'] for u in m.unmatched_td], ['TheRiskCo Contractor'])

    def test_active_roster_filter_is_conservative(self):
        users = [td('u1', 'Live'), {**td('u2', 'Gone'), 'archived': True},
                 {**td('u3', 'Old'), 'status': 'archived'}, {**td('u4', 'NoFlag'), 'active': False}]
        kept = [u['name'] for u in active_td_users(users)]
        # only explicit archived/deleted flags drop a user; odd fields don't
        self.assertEqual(kept, ['Live', 'NoFlag'])


class CanonicalIdentityTests(SimpleTestCase):
    """The CFO's two machines fold onto one identity (CFO 2026-07-22)."""

    def test_alias_by_uid_both_accounts(self):
        from integrations.td_matching import canonical_identity
        self.assertEqual(canonical_identity('aetqn--rymG6S7qQ', 'PrathapAsus'),
                         ('XnseOWIRLwAEEwM1', 'Prathap Ganesharajah +'))
        self.assertEqual(canonical_identity('XnseOWIRLwAEEwM1', 'Prathap Ganesharajah (ExCo)'),
                         ('XnseOWIRLwAEEwM1', 'Prathap Ganesharajah +'))

    def test_alias_by_name_fallback_when_uid_unknown(self):
        from integrations.td_matching import canonical_identity
        self.assertEqual(canonical_identity(None, 'PrathapAsus'),
                         ('XnseOWIRLwAEEwM1', 'Prathap Ganesharajah +'))

    def test_non_aliased_account_passes_through_unchanged(self):
        from integrations.td_matching import canonical_identity
        self.assertEqual(canonical_identity('u123', 'Jane Doe'), ('u123', 'Jane Doe'))
        self.assertEqual(canonical_identity(None, 'Jane Doe'), (None, 'Jane Doe'))

    def test_canonical_name_still_matches_payroll(self):
        # The "+" is stripped by norm_name, so the merged row still links to the
        # payroll employee "Prathap Ganesharajah".
        self.assertEqual(norm_name('Prathap Ganesharajah +'), 'prathap ganesharajah')


class AggregateMergeTests(SimpleTestCase):
    """aggregate() collapses a person's multiple TD machines into one member."""

    def test_two_cfo_machines_merge_to_the_busiest(self):
        from integrations.timedoctor import aggregate
        from datetime import date
        asus, exco, other = 'aetqn--rymG6S7qQ', 'XnseOWIRLwAEEwM1', 'u-other'
        users = [
            {'id': asus, 'name': 'PrathapAsus', 'email': 's-1-5-junk@x'},
            {'id': exco, 'name': 'Prathap Ganesharajah (ExCo)', 'email': 'pganesharajah@alphadirect.co.bw'},
            {'id': other, 'name': 'Jane Doe', 'email': 'jdoe@alphadirect.co.bw'},
        ]
        worklog = [
            [{'userId': asus, 'time': 3600}],    # 1.0h laptop
            [{'userId': exco, 'time': 7200}],    # 2.0h desktop
            [{'userId': other, 'time': 1800}],   # 0.5h someone else
        ]
        out = aggregate(users, worklog, [], [], [], as_of=date(2026, 7, 22),
                        td_user_ids=[asus, exco, other])
        members = out['members']
        prathap = [m for m in members if str(m['name']).startswith('Prathap')]
        self.assertEqual(len(prathap), 1)                       # one row, not two
        self.assertEqual(prathap[0]['name'], 'Prathap Ganesharajah +')
        self.assertEqual(prathap[0]['user_id'], exco)           # canonical uid
        # Busiest machine (the 2.0 h desktop), NOT 3.0 h of the two added up.
        self.assertEqual(prathap[0]['tracked_seconds'], 7200)
        self.assertEqual(prathap[0]['hours_tracked'], 2.0)
        self.assertEqual(prathap[0]['machine_count'], 2)
        # unrelated user untouched
        jane = [m for m in members if m['name'] == 'Jane Doe']
        self.assertEqual(len(jane), 1)
        self.assertEqual(jane[0]['tracked_seconds'], 1800)
        # Company total no longer double-counts either: the CFO contributes his
        # busiest machine (2.0 h), not both machines (3.0 h). So 2.0 + 0.5 = 2.5,
        # where this used to read 3.5.
        self.assertEqual(out['totals']['total_hours'], 2.5)


class BreakerTests(SimpleTestCase):
    def b(self, tracked, dnt, roster):
        from hris.exceptions_report import circuit_breaker
        return circuit_breaker({'tracked': tracked, 'did_not_track': dnt, 'roster': roster})

    def test_healthy_day_passes(self):
        tripped, _ = self.b(90, 6, 96)
        self.assertFalse(tripped)

    def test_normal_absence_does_not_trip(self):
        # ~40% not tracking on a given day is normal absence, NOT an outage —
        # the report must still send and flag them (threshold is 60%).
        tripped, _ = self.b(58, 38, 96)
        self.assertFalse(tripped)

    def test_collapse_trips(self):
        # A genuine collapse (most of the matched roster silent) = outage-level.
        tripped, why = self.b(15, 81, 96)
        self.assertTrue(tripped)
        self.assertIn('%', why)

    def test_zero_tracked_trips(self):
        self.assertTrue(self.b(0, 96, 96)[0])

    def test_empty_roster_trips(self):
        self.assertTrue(self.b(0, 0, 0)[0])


class MapTablePassTests(TestCase):
    def test_confirmed_map_row_wins_over_names(self):
        from payroll.models import Employee
        from integrations.models import TimeDoctorUserMap
        emp = Employee.objects.create(employee_number='TDM-1', full_name='Totally Different Name')
        TimeDoctorUserMap.objects.create(td_user_id='uX', td_name='Ops Laptop 3',
                                         employee=emp, confirmed=True,
                                         source=TimeDoctorUserMap.Source.MANUAL)
        m = TDMatcher([td('uX', 'Ops Laptop 3')], [emp])
        self.assertEqual(m.employee_for_uid['uX'].id, emp.id)

    def test_persist_suggestions_never_touches_confirmed(self):
        from payroll.models import Employee
        from integrations.models import TimeDoctorUserMap
        emp = Employee.objects.create(employee_number='TDM-2', full_name='Bokani Mo')
        other = Employee.objects.create(employee_number='TDM-3', full_name='Someone Else')
        row = TimeDoctorUserMap.objects.create(td_user_id='uY', td_name='Old Name',
                                               employee=other, confirmed=True,
                                               source=TimeDoctorUserMap.Source.MANUAL)
        m = TDMatcher([td('uY', 'Bokani Mo'), td('uZ', 'Bokani Mo Two')], [emp], use_map=False)
        m.persist_suggestions()
        row.refresh_from_db()
        self.assertEqual(row.employee_id, other.id)          # confirmed row untouched
        self.assertEqual(row.td_name, 'Old Name')
        self.assertTrue(TimeDoctorUserMap.objects.filter(td_user_id='uZ').exists())


class HoursByEmployeeTests(TestCase):
    """THE permanent-fix regression (CFO 2026-08-27). A person whose Time Doctor
    display name differs from their HR name (a different surname on the two
    systems) must resolve to their REAL hours via the confirmed account link —
    never read as 0 because of the name, which is what falsely nudged people and
    wrote zero attendance records across five commands. Synthetic names below keep
    the same 'tokens do not subset-match' property as the real incident."""

    def test_confirmed_link_beats_name_mismatch(self):
        from payroll.models import Employee
        from integrations.models import TimeDoctorUserMap
        from integrations.td_matching import hours_by_employee
        hr_name, td_name = 'Sample Alpha Kubu', 'Sample Molefe'   # share only 'sample'
        emp = Employee.objects.create(employee_number='HB-1',
                                      full_name=hr_name, email='hb1@example.test')
        TimeDoctorUserMap.objects.create(td_user_id='UID1', td_name=td_name,
                                         employee=emp, confirmed=True,
                                         source=TimeDoctorUserMap.Source.MANUAL)
        payload = [{'user_id': 'UID1', 'name': td_name,
                    'email': 'sid@example.test', 'hours_tracked': 3.07}]
        out = hours_by_employee(payload, [emp])
        self.assertIn(emp.id, out)                       # resolved (would be absent if name-matched)
        self.assertAlmostEqual(out[emp.id], 3.07, places=2)
        # Prove the OLD name-only path WOULD have missed — the bug this locks out.
        hr = name_tokens(norm_name(hr_name))
        tdn = name_tokens(norm_name(td_name))
        self.assertFalse(hr <= tdn or tdn <= hr)

    def test_no_account_is_absent_not_zero(self):
        from payroll.models import Employee
        from integrations.td_matching import hours_by_employee
        emp = Employee.objects.create(employee_number='HB-2', full_name='No Account Person')
        out = hours_by_employee([{'user_id': 'U9', 'name': 'Someone Else', 'hours_tracked': 5}], [emp])
        self.assertNotIn(emp.id, out)   # caller decides the default; never a false 0-as-fact


class HoursReminderGuardTests(TestCase):
    """The DeepSeek+Gemini send-guard (CFO 2026-08-27): never email wrong info."""

    def _items(self, *, mapped=True):
        return [{'ref': 1, 'hours': 0.0, 'threshold': 2.0, 'typical': 7.0,
                 'mapped': mapped, 'arrival': None}]

    def _safe(self):
        from core.ai_assist import SafetyReport
        return SafetyReport(safe=True, redacted_text='refs', redactions_made=0, notes=[])

    def test_unconfirmed_account_is_held(self):
        from hris.hours_reminder_guard import hold_suspect_reminders
        held, _reason, _ran = hold_suspect_reminders(self._items(mapped=False))
        self.assertIn(1, held)

    def test_data_error_is_held(self):
        from hris.hours_reminder_guard import hold_suspect_reminders
        with mock.patch('core.ai_assist.is_safe_for_ai', return_value=self._safe()), \
             mock.patch('core.ai_assist.deepseek_complete',
                        return_value='{"verdicts":[{"ref":1,"verdict":"data_error"}]}'), \
             mock.patch('core.ai_assist.gemini_complete',
                        return_value='{"verdicts":[{"ref":1,"verdict":"genuine"}]}'):
            held, _reason, ran = hold_suspect_reminders(self._items())
        self.assertIn(1, held)
        self.assertTrue(ran)

    def test_genuine_shortfall_is_sent(self):
        from hris.hours_reminder_guard import hold_suspect_reminders
        with mock.patch('core.ai_assist.is_safe_for_ai', return_value=self._safe()), \
             mock.patch('core.ai_assist.deepseek_complete',
                        return_value='{"verdicts":[{"ref":1,"verdict":"genuine"}]}'), \
             mock.patch('core.ai_assist.gemini_complete',
                        return_value='{"verdicts":[{"ref":1,"verdict":"genuine"}]}'):
            held, _reason, _ran = hold_suspect_reminders(self._items())
        self.assertNotIn(1, held)

    def test_fail_closed_when_engines_down(self):
        from hris.hours_reminder_guard import hold_suspect_reminders
        with mock.patch('core.ai_assist.is_safe_for_ai', return_value=self._safe()), \
             mock.patch('core.ai_assist.deepseek_complete', side_effect=RuntimeError('down')), \
             mock.patch('core.ai_assist.gemini_complete', side_effect=RuntimeError('down')):
            held, _reason, _ran = hold_suspect_reminders(self._items())
        self.assertIn(1, held)   # fail-closed: no verification → no send

    def test_both_engines_garbage_is_held(self):
        from hris.hours_reminder_guard import hold_suspect_reminders
        with mock.patch('core.ai_assist.is_safe_for_ai', return_value=self._safe()), \
             mock.patch('core.ai_assist.deepseek_complete', return_value='sorry, I cannot help'), \
             mock.patch('core.ai_assist.gemini_complete', return_value='no json here'):
            held, _reason, _ran = hold_suspect_reminders(self._items())
        self.assertIn(1, held)   # both replies unparseable = nothing usable → fail-closed

    def test_uncertain_is_held(self):
        from hris.hours_reminder_guard import hold_suspect_reminders
        with mock.patch('core.ai_assist.is_safe_for_ai', return_value=self._safe()), \
             mock.patch('core.ai_assist.deepseek_complete',
                        return_value='{"verdicts":[{"ref":1,"verdict":"uncertain"}]}'), \
             mock.patch('core.ai_assist.gemini_complete',
                        return_value='{"verdicts":[{"ref":1,"verdict":"uncertain"}]}'):
            held, _reason, _ran = hold_suspect_reminders(self._items())
        self.assertIn(1, held)   # not positively cleared by either engine → held
