"""TD-ORPHAN-01 — the mirror guard: hours that belong to NOBODY must be raised.

Bug 5dffc022 (Natasha Nthite, 3-Sep-2026) got past every guard we had, and it
got past them because all of them face the same way.

The settle gate and the DeepSeek/Gemini cross-check both answer ONE question:
"are we about to accuse someone unfairly?" So they fail safe by going QUIET —
hold the name, say nothing. That is right when the risk is a false accusation.

Her bug was the mirror image. Nobody accused her. Her 5.73 h simply landed on a
Time Doctor account that belonged to no one, and Omni reconciled people → hours
but never hours → people. 18.29 h of real recorded work sat on unowned accounts
that day and nothing anywhere raised a hand. A guard that fails safe by going
quiet cannot catch a bug whose only symptom IS quiet.

So this gate balances the other side of the books, and its fail direction is
inverted: when in doubt it RAISES. Silence is the failure mode being guarded.

Verified against the real 2-Sep prod snapshot: Natasha 5.73 h, Kelvin Kimani
4.30 h, Unaludo Mafuraga 3.49 h, Lorato Molosiwa 2.97 h, 'Bokanihp' 0.61 h,
'User' 0.66 h, 'Arun Iyer (ExCo)' 0.53 h.
"""
import datetime

from django.test import TestCase

from hris import people_data_guard as pdg


DAY = datetime.date(2026, 9, 2)

# The real 2-Sep rows, trimmed to the fields the gate reads.
PAYLOAD = [
    {'user_id': 'adkFmpg_AyGD8aDl', 'name': 'Natasha Nthite',    'tracked_seconds': 0},
    {'user_id': 'apfkTcCt1f8NLyM6', 'name': 'Natasha Nthite',    'tracked_seconds': 20626},
    {'user_id': 'anG8rh-J2z97XiXc', 'name': 'Kelvin Kimani',     'tracked_seconds': 15480},
    {'user_id': 'apaaPSdZKzvk50Vz', 'name': 'Unaludo Mafuraga',  'tracked_seconds': 12564},
    {'user_id': 'apaa4TNe4JSjG93z', 'name': 'Lorato Molosiwa',   'tracked_seconds': 10692},
    {'user_id': 'apfgmsCt1f8NLiUH', 'name': 'Bokanihp',          'tracked_seconds': 2196},
    {'user_id': 'apfbX6rahHNN-b28', 'name': 'User',              'tracked_seconds': 2376},
    {'user_id': 'well-owned-uid',   'name': 'Somebody Owned',    'tracked_seconds': 28800},
]
OWNED = {'adkFmpg_AyGD8aDl', 'well-owned-uid'}


class OrphanHoursGateTests(TestCase):
    def test_it_finds_the_unowned_hours(self):
        orphans = pdg.orphan_hours_gate(PAYLOAD, OWNED)
        names = sorted(o['name'] for o in orphans)
        self.assertEqual(names, ['Bokanihp', 'Kelvin Kimani', 'Lorato Molosiwa',
                                 'Natasha Nthite', 'Unaludo Mafuraga', 'User'])

    def test_owned_accounts_are_never_orphans(self):
        orphans = pdg.orphan_hours_gate(PAYLOAD, OWNED)
        self.assertNotIn('well-owned-uid', {o['uid'] for o in orphans})
        self.assertNotIn('adkFmpg_AyGD8aDl', {o['uid'] for o in orphans})

    def test_an_unowned_account_with_no_hours_is_not_an_alarm(self):
        # A dormant contractor / test rig tracking nothing costs nobody their
        # hours. Only LOST WORK is the alarm.
        rows = [{'user_id': 'idle', 'name': 'Idle Rig', 'tracked_seconds': 0}]
        self.assertEqual(pdg.orphan_hours_gate(rows, set()), [])

    def test_the_total_lost_is_reported(self):
        orphans = pdg.orphan_hours_gate(PAYLOAD, OWNED)
        lost = round(sum(o['hours'] for o in orphans), 2)
        # 18.29 h went missing on 2-Sep in total; this fixture omits Arun
        # Iyer's 0.53 h row, so the six rows here account for 17.76 h.
        self.assertEqual(lost, 17.76)

    def test_a_trivial_sliver_can_be_thresholded_out(self):
        # A couple of minutes on a spare machine is noise, not lost work.
        orphans = pdg.orphan_hours_gate(PAYLOAD, OWNED, min_seconds=3600)
        self.assertEqual(sorted(o['name'] for o in orphans),
                         ['Kelvin Kimani', 'Lorato Molosiwa', 'Natasha Nthite',
                          'Unaludo Mafuraga'])

    def test_empty_inputs_are_a_clean_no_op(self):
        self.assertEqual(pdg.orphan_hours_gate([], set()), [])
        self.assertEqual(pdg.orphan_hours_gate(None, None), [])

    def test_rows_are_ordered_worst_first(self):
        orphans = pdg.orphan_hours_gate(PAYLOAD, OWNED)
        hours = [o['hours'] for o in orphans]
        self.assertEqual(hours, sorted(hours, reverse=True))


class OrphanGuardFailsLOUDTests(TestCase):
    """The inverted fail direction. Every other gate holds when unsure; this one
    raises. A guard against silence must never be able to fall silent."""

    def test_ai_unavailable_still_raises_every_orphan(self):
        orphans = pdg.orphan_hours_gate(PAYLOAD, OWNED)
        out = pdg.screen_orphan_hours(orphans, ['Natasha Nthite'], DAY,
                                      use_ai=False)
        self.assertEqual(len(out['raise']), len(orphans))
        self.assertTrue(out['ai_unavailable'])

    def test_a_dead_ai_engine_cannot_suppress_a_name(self):
        def _boom(*a, **k):
            raise RuntimeError('deepseek down')

        orphans = pdg.orphan_hours_gate(PAYLOAD, OWNED)
        out = pdg.screen_orphan_hours(orphans, ['Natasha Nthite'], DAY,
                                      engines=[_boom, _boom])
        self.assertEqual(len(out['raise']), len(orphans))
        self.assertTrue(out['ai_unavailable'])

    def test_the_ai_can_only_ADD_a_suggested_owner_never_remove_a_row(self):
        # The AI is an assistant here, not a veto: it names a likely owner so a
        # human can confirm in one click. It must never drop the row.
        import json

        def _engine(text, **kw):
            return json.dumps({'verdicts': [{'id': 0, 'owner': 'Natasha Nthite'}]})

        orphans = pdg.orphan_hours_gate(PAYLOAD, OWNED)
        out = pdg.screen_orphan_hours(orphans, ['Natasha Nthite'], DAY,
                                      engines=[_engine])
        self.assertEqual(len(out['raise']), len(orphans))
        self.assertFalse(out['ai_unavailable'])
        suggested = {o['name']: o.get('likely_owner') for o in out['raise']}
        self.assertEqual(suggested.get('Natasha Nthite'), 'Natasha Nthite')

    def test_a_matched_person_is_a_valid_suggested_owner(self):
        # THE blind spot that let her bug through: ai_screen_ghosts only ever
        # considered payroll people with NO tracker. She HAD one. So the roster
        # handed to the screen must include people who already track.
        import json

        def _engine(text, **kw):
            self.assertIn('Natasha Nthite', text)
            return json.dumps({'verdicts': []})

        orphans = pdg.orphan_hours_gate(PAYLOAD, OWNED)
        pdg.screen_orphan_hours(orphans, ['Natasha Nthite'], DAY,
                                engines=[_engine])


class OrphanSectionIsAlwaysRenderedTests(TestCase):
    """A guard against silence that could itself be silent would be worthless,
    so the section is NAMED in the email and never suppressed."""

    def _data(self, **over):
        base = {
            'day': DAY, 'summary': {'did_not_track': 0, 'tracked': 1, 'roster': 1,
                        'total_h': 8.0, 'avg_h': 8.0, 'prod_h': 7.5},
            'did_not_track': [], 'did_not_track_uids': [], 'held': [], 'held_ai_down': 0,
            'on_leave': [], 'planned': [], 'alarm': [], 'critical': [], 'low': [],
            'below6': [], 'late': [], 'unproductive': [], 'ghosts': [],
            'matched_names': [], 'unmatched_td': [], 'day_map': {},
            'momentum': {}, 'leaderboard': [], 'lb_hidden': [], 'focus': {},
            'shortfall': [], 'unexplained': {},
            'orphan_hours': [], 'orphan_hours_total': 0.0, 'orphan_hours_ai_down': False,
        }
        base.update(over)
        return base

    def test_the_lost_hours_are_named_in_the_email(self):
        from hris.exceptions_report import build_html
        html = build_html(self._data(
            orphan_hours=[{'uid': 'apfkTcCt1f8NLyM6', 'name': 'Natasha Nthite',
                           'seconds': 20626, 'hours': 5.73,
                           'likely_owner': 'Natasha Nthite'},
                          {'uid': 'anG8rh-J2z97XiXc', 'name': 'Kelvin Kimani',
                           'seconds': 15480, 'hours': 4.3}],
            orphan_hours_total=10.03))
        self.assertIn('belongs to', html)
        self.assertIn('Natasha Nthite', html)
        self.assertIn('Kelvin Kimani', html)
        self.assertIn('10.03h', html)
        self.assertIn('5.73h', html)

    def test_an_owner_the_ai_could_not_name_still_appears(self):
        from hris.exceptions_report import build_html
        html = build_html(self._data(
            orphan_hours=[{'uid': 'x', 'name': 'Bokanihp', 'seconds': 2196,
                           'hours': 0.61}],
            orphan_hours_total=0.61, orphan_hours_ai_down=True))
        self.assertIn('Bokanihp', html)
        self.assertIn('owner unknown', html)
        self.assertIn('could not run', html)

    def test_a_clean_day_adds_no_section(self):
        from hris.exceptions_report import build_html
        html = build_html(self._data())
        self.assertNotIn('belongs to', html)


class OwnershipIsNotReportScopeTests(TestCase):
    """First live run of the gate cried wolf on six people.

    `owned` had been built from matcher.employee_for_uid — the accounts the
    DAY'S REPORT resolved, which is scoped to tracking-eligible profiles. Refilwe
    Ramphaleng, Morati Segwe, Tumelo Molefe, Gofiwa Elias and Ntjidzi Nleya all
    hold a confirmed payroll link; they simply were not in that day's reporting
    scope, and got named as lost hours.

    Ownership is a FACT (does a map row point at an employee?), not a reporting
    decision. A guard that cries wolf gets ignored — which would restore exactly
    the silence it exists to prevent.
    """

    def setUp(self):
        from integrations.models import TimeDoctorUserMap
        from payroll.models import Employee
        self.emp = Employee.objects.create(full_name='Refilwe Ramphaleng',
                                           employee_number='ORPH-1',
                                           email='rramphaleng@alphadirect.co.bw')
        TimeDoctorUserMap.objects.create(
            td_user_id='anbOglWSv46Ujr1w', td_name='Refilwe Ramphaleng',
            td_email='r@x.y', employee=self.emp, confirmed=True)
        TimeDoctorUserMap.objects.create(
            td_user_id='anG8rh-J2z97XiXc', td_name='Kelvin Kimani',
            td_email='S-1-5-21-1@x.y', employee=None, confirmed=False)

    def test_a_confirmed_link_counts_as_owned_even_outside_report_scope(self):
        owned = pdg.owned_td_uids()
        self.assertIn('anbOglWSv46Ujr1w', owned)
        self.assertNotIn('anG8rh-J2z97XiXc', owned)

    def test_the_gate_then_names_only_the_genuinely_unowned(self):
        rows = [
            {'user_id': 'anbOglWSv46Ujr1w', 'name': 'Refilwe Ramphaleng',
             'tracked_seconds': 15768},
            {'user_id': 'anG8rh-J2z97XiXc', 'name': 'Kelvin Kimani',
             'tracked_seconds': 15480},
        ]
        orphans = pdg.orphan_hours_gate(rows, pdg.owned_td_uids())
        self.assertEqual([o['name'] for o in orphans], ['Kelvin Kimani'])

    def test_extra_uids_from_the_caller_are_honoured_too(self):
        owned = pdg.owned_td_uids(extra={'anG8rh-J2z97XiXc'})
        self.assertIn('anG8rh-J2z97XiXc', owned)


class AISuggestionMustBeCorroboratedTests(TestCase):
    """The first live run also hallucinated owners: 'Kelvin Kimani' was offered
    as 'Oitiretse Kao Galotshoge', 'User' as 'Kotswana Kotswana'. The name passed
    only because it existed on payroll.

    A wrong owner next to real hours is the worst outcome this guard could
    produce — a human clicks confirm and one person's work is credited to
    another. So a suggestion must survive a DETERMINISTIC name-likeness check;
    the AI proposes, arithmetic disposes. The row is still raised either way.
    """

    def _screen(self, orphan_name, ai_owner, roster):
        import json

        def _engine(text, **kw):
            return json.dumps({'verdicts': [{'id': 0, 'owner': ai_owner}]})

        orphans = [{'uid': 'u1', 'name': orphan_name, 'seconds': 3600, 'hours': 1.0}]
        return pdg.screen_orphan_hours(orphans, roster, DAY, engines=[_engine])

    def test_a_hallucinated_owner_is_dropped(self):
        out = self._screen('Kelvin Kimani', 'Oitiretse Kao Galotshoge',
                           ['Oitiretse Kao Galotshoge'])
        self.assertEqual(len(out['raise']), 1)              # still raised
        self.assertIsNone(out['raise'][0].get('likely_owner'))

    def test_a_role_rig_gets_no_owner(self):
        out = self._screen('User', 'Kotswana Kotswana', ['Kotswana Kotswana'])
        self.assertIsNone(out['raise'][0].get('likely_owner'))

    def test_a_genuine_likeness_survives(self):
        out = self._screen('Amantle Thake', 'Amantle Adelaide Thake',
                           ['Amantle Adelaide Thake'])
        self.assertEqual(out['raise'][0].get('likely_owner'),
                         'Amantle Adelaide Thake')

    def test_an_exact_name_survives(self):
        out = self._screen('Natasha Nthite', 'Natasha Nthite', ['Natasha Nthite'])
        self.assertEqual(out['raise'][0].get('likely_owner'), 'Natasha Nthite')

    def test_a_single_shared_first_name_is_not_enough(self):
        # 'Refilwe Ramphaleng' vs 'Caroline Refilwe Otukile' shares one token and
        # was one of the live false suggestions.
        out = self._screen('Refilwe Ramphaleng', 'Caroline Refilwe Otukile',
                           ['Caroline Refilwe Otukile'])
        self.assertIsNone(out['raise'][0].get('likely_owner'))
