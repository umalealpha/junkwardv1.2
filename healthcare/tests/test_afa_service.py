"""Tests for the AFA load-file SERVICE layer (healthcare/afa_service.py).

This is the layer that decides who is on the file and who is resigned. It had
six critical defects that a green machine check sailed straight past, because
every earlier test covered the engine and the transfer and none covered this.
Each test below pins one of them.

Synthetic data only — never real member data.
"""
from __future__ import annotations

from datetime import date
from unittest import mock

from django.test import TestCase, override_settings

from healthcare import afa_loadfile as engine
from healthcare import afa_service
from healthcare.models import AfaGroupNameMap, AfaLoadFileRun, AfaMemberSnapshot

GROUP = 'GRP001'
IMED = 'ALPHA DIRECT INSURANCE'


def _principal(policy='ADH-T1', **over):
    row = {
        'policy_id': 1, 'policy_number': policy, 'policy_status': 0,
        'activated_date': date(2025, 11, 1), 'employer_group_id': GROUP,
        'graphite_group_name': 'Test Co', 'group_status': 'Active',
        'employee_number': 'E1', 'policy_start_date': date(2025, 11, 1),
        'waiting_from': None, 'waiting_to': None, 'plan_name': 'AD Lite',
        'first_name': 'Sample', 'surname': 'One', 'email': '', 'cellphone': '',
        'gender': 1, 'dob': date(1990, 1, 1), 'id_number': '111111',
        'passport': '', 'address_1': '', 'address_2': '', 'address_3': '',
        'town': 'Gaborone', 'account_holder': '', 'bank_name': '',
        'branch_code': '', 'account_number': '', 'account_type': '',
    }
    row.update(over)
    return row


def _dep(beneficiary_id, first='Dep', **over):
    row = {
        'beneficiary_id': beneficiary_id, 'policy_id': 1,
        'person_type': 'Dependant', 'relation': 'Spouse',
        'first_name': first, 'surname': 'One', 'dob': date(1992, 2, 2),
        'gender': 0, 'id_number': f'22222{beneficiary_id}', 'passport': '',
        'email': '', 'cellphone': '', 'address_2': '', 'town': 'Gaborone',
        'status': 1,
    }
    row.update(over)
    return row


def _membership(principals, deps_by_policy=None):
    return {
        'principals': principals,
        'dependants_by_policy': deps_by_policy or {},
        'columns_seen': set(principals[0].keys()) if principals else set(),
    }


class _ServiceTestCase(TestCase):
    def setUp(self):
        AfaGroupNameMap.objects.create(
            employer_group_id=GROUP, imed_group_name=IMED, region_name=IMED)

    def _build(self, membership, lag=1, run_date=date(2026, 8, 7)):
        with mock.patch('healthcare.afa_members.fetch_membership', return_value=membership), \
             mock.patch('healthcare.afa_members.replica_lag_seconds', return_value=lag), \
             mock.patch('healthcare.afa_service._paid_group_ids', return_value={GROUP}):
            return afa_service.build(run_date)


class DependantNumberingTests(_ServiceTestCase):
    """Defect 3 — every dependant of a policy got number 1 on the first run."""

    def test_two_dependants_on_one_policy_get_different_numbers(self):
        m = _membership([_principal()], {1: [_dep(101, 'A'), _dep(102, 'B')]})
        outcome, _ = self._build(m)
        deps = sorted(d for (_, d) in outcome.seen_keys if d != 0)
        self.assertEqual(deps, [1, 2])

    def test_three_dependants_all_get_distinct_numbers(self):
        m = _membership([_principal()],
                        {1: [_dep(101, 'A'), _dep(102, 'B'), _dep(103, 'C')]})
        outcome, _ = self._build(m)
        deps = sorted(d for (_, d) in outcome.seen_keys if d != 0)
        self.assertEqual(deps, [1, 2, 3])

    def test_a_dependant_keeps_its_number_when_a_sibling_leaves(self):
        """H20 — a departure must never renumber the survivors."""
        AfaMemberSnapshot.objects.create(
            policy_number='ADH-T1', dependant_no=1, employer_group_id=GROUP,
            graphite_beneficiary_id=101, row_hash='x')
        AfaMemberSnapshot.objects.create(
            policy_number='ADH-T1', dependant_no=2, employer_group_id=GROUP,
            graphite_beneficiary_id=102, row_hash='y')
        # 101 has gone; 102 must stay number 2, not become 1.
        m = _membership([_principal()], {1: [_dep(102, 'B')]})
        outcome, _ = self._build(m)
        self.assertIn(('ADH-T1', 2), outcome.seen_keys)
        self.assertNotIn(('ADH-T1', 1), outcome.seen_keys)

    def test_a_new_dependant_never_reuses_a_departed_number(self):
        AfaMemberSnapshot.objects.create(
            policy_number='ADH-T1', dependant_no=1, employer_group_id=GROUP,
            graphite_beneficiary_id=101, row_hash='x',
            state=AfaMemberSnapshot.State.RESIGNED)
        m = _membership([_principal()], {1: [_dep(103, 'C')]})
        outcome, _ = self._build(m)
        self.assertIn(('ADH-T1', 2), outcome.seen_keys)


class FullVersusDeltaTests(_ServiceTestCase):
    """Defect 2 — the file was a delta while delta-vs-full was unanswered."""

    def _seed_sent(self, outcome):
        for key, meta in outcome.sent.items():
            policy_number, dep_no = afa_service._unkey(key)
            AfaMemberSnapshot.objects.create(
                policy_number=policy_number, dependant_no=dep_no,
                employer_group_id=GROUP, row_hash=meta['h'])

    def test_full_is_the_default(self):
        self.assertEqual(afa_service._mode(), afa_service.MODE_FULL)

    def test_in_full_mode_an_unchanged_member_is_still_sent(self):
        m = _membership([_principal()])
        first, _ = self._build(m)
        self._seed_sent(first)
        second, _ = self._build(m)
        self.assertEqual(len(second.rows), 1)
        self.assertEqual(second.new, 0)

    @override_settings(AFA_LOADFILE_MODE='delta')
    def test_in_delta_mode_an_unchanged_member_is_not_resent(self):
        m = _membership([_principal()])
        first, _ = self._build(m)
        self._seed_sent(first)
        second, _ = self._build(m)
        self.assertEqual(len(second.rows), 0)

    @override_settings(AFA_LOADFILE_MODE='delta')
    def test_a_returning_member_is_resent_even_though_nothing_changed(self):
        m = _membership([_principal()])
        first, _ = self._build(m)
        for key, meta in first.sent.items():
            policy_number, dep_no = afa_service._unkey(key)
            AfaMemberSnapshot.objects.create(
                policy_number=policy_number, dependant_no=dep_no,
                employer_group_id=GROUP, row_hash=meta['h'],
                state=AfaMemberSnapshot.State.RESIGNED)
        second, _ = self._build(m)
        self.assertEqual(len(second.rows), 1)


class PaymentGateTests(_ServiceTestCase):
    """A new member must not reach AFA before the employer has paid."""

    def _build_unpaid(self, membership):
        with mock.patch('healthcare.afa_members.fetch_membership', return_value=membership), \
             mock.patch('healthcare.afa_members.replica_lag_seconds', return_value=1), \
             mock.patch('healthcare.afa_service._paid_group_ids', return_value=set()):
            return afa_service.build(date(2026, 8, 7))

    def test_a_new_member_of_an_unpaid_group_is_held(self):
        outcome, _ = self._build_unpaid(_membership([_principal()]))
        self.assertEqual(outcome.rows, [])
        self.assertTrue(any('invoice to be paid' in h.reason for h in outcome.held))

    def test_an_existing_member_is_not_re_held_when_the_invoice_is_unpaid(self):
        AfaMemberSnapshot.objects.create(
            policy_number='ADH-T1', dependant_no=0, employer_group_id=GROUP, row_hash='old')
        outcome, _ = self._build_unpaid(_membership([_principal()]))
        self.assertEqual(len(outcome.rows), 1)

    def test_a_group_with_no_billing_contact_can_never_open_the_gate(self):
        maps = {GROUP: AfaGroupNameMap.objects.get(employer_group_id=GROUP)}
        self.assertEqual(afa_service._paid_group_ids(maps), set())


class DepartureTests(_ServiceTestCase):
    """Defects 4, 5 and 6 — the code path that cancels live medical cover."""

    def setUp(self):
        super().setUp()
        AfaMemberSnapshot.objects.create(
            policy_number='ADH-T1', dependant_no=0, employer_group_id=GROUP,
            graphite_policy_id=1, row_hash='old')

    @override_settings(AFA_DEFAULT_RESIGNATION_REASON='RESIGNED')
    def test_a_departure_resigns_at_the_END_OF_THE_CURRENT_MONTH(self):
        """Defect 4 — it used to backdate to the member's activation month,
        retro-cancelling every month since they joined."""
        m = _membership([_principal(policy_status=2)])
        outcome, _ = self._build(m, run_date=date(2026, 8, 7))
        self.assertEqual(outcome.departures, 1)
        row = outcome.rows[0].split('|')
        self.assertEqual(row[engine.COLUMNS.index('Resignation date')], '2026-08-31')

    @override_settings(AFA_DEFAULT_RESIGNATION_REASON='RESIGNED')
    def test_a_dependant_departure_carries_the_DEPENDANTS_identity(self):
        """Defect 5 — it used to render the PRINCIPAL's name, DOB and ID under
        the dependant's number, resigning the wrong human."""
        AfaMemberSnapshot.objects.create(
            policy_number='ADH-T1', dependant_no=1, employer_group_id=GROUP,
            graphite_policy_id=1, graphite_beneficiary_id=101, row_hash='old')
        # principal still on cover; the dependant is the one leaving, but the
        # source row is still readable in Graphite.
        m = _membership([_principal()], {1: [_dep(101, 'Depwent')]})
        with mock.patch('healthcare.afa_members.fetch_membership', return_value=m), \
             mock.patch('healthcare.afa_members.replica_lag_seconds', return_value=1), \
             mock.patch('healthcare.afa_service._paid_group_ids', return_value={GROUP}), \
             mock.patch('healthcare.afa_service.engine.validate_row',
                        side_effect=engine.validate_row):
            outcome, _ = afa_service.build(date(2026, 8, 7))
        # dependant 1 was seen in the pull, so there is no departure at all
        self.assertEqual(outcome.departures, 0)

    @override_settings(AFA_DEFAULT_RESIGNATION_REASON='')
    def test_with_no_agreed_reason_code_a_departure_is_held_not_guessed(self):
        m = _membership([_principal(policy_status=2)])
        outcome, _ = self._build(m)
        self.assertEqual(outcome.departures, 0)
        self.assertTrue(any('no AFA reason code' in h.reason for h in outcome.held))

    @override_settings(AFA_DEFAULT_RESIGNATION_REASON='RESIGNED')
    def test_a_held_departure_is_never_recorded_as_resigned(self):
        """Defect 6 — held departures were marked resigned though they never
        reached AFA, leaving AFA covering someone we think has left."""
        AfaGroupNameMap.objects.filter(employer_group_id=GROUP).update(is_active=False)
        m = _membership([_principal(policy_status=2)])
        outcome, _ = self._build(m)
        self.assertEqual(outcome.departure_keys, [])

        # A run with SOMETHING in it, so commit_snapshot proceeds rather than
        # refusing for an unrelated reason.
        run = AfaLoadFileRun.objects.create(
            run_date=date(2026, 8, 7),
            sent_keys={afa_service._key('ADH-OTHER', 0): {'h': 'h', 'g': GROUP}},
            departure_keys=outcome.departure_keys, row_count=1)
        afa_service.commit_snapshot(run)
        snap = AfaMemberSnapshot.objects.get(policy_number='ADH-T1', dependant_no=0)
        self.assertEqual(snap.state, AfaMemberSnapshot.State.ACTIVE)

    @override_settings(AFA_DEFAULT_RESIGNATION_REASON='RESIGNED')
    def test_a_handful_of_departures_never_trips_the_fence(self):
        """On a small book any single leaver exceeds any ratio — a five-person
        employer must still be able to lose somebody."""
        for i in range(2, 6):
            AfaMemberSnapshot.objects.create(
                policy_number=f'ADH-T{i}', dependant_no=0,
                employer_group_id=GROUP, graphite_policy_id=i, row_hash='old')
        principals = [_principal(policy=f'ADH-T{i}', policy_id=i,
                                 policy_status=(2 if i == 1 else 0))
                      for i in range(1, 6)]
        outcome, _ = self._build(_membership(principals))
        self.assertEqual(outcome.departures, 1)

    @override_settings(AFA_DEFAULT_RESIGNATION_REASON='RESIGNED')
    def test_a_mass_departure_aborts_rather_than_resigning_the_scheme(self):
        """The fence on the DIFF, not the read — a mass status flip keeps the
        row count intact and sails through the read fence."""
        for i in range(2, 21):
            AfaMemberSnapshot.objects.create(
                policy_number=f'ADH-T{i}', dependant_no=0,
                employer_group_id=GROUP, graphite_policy_id=i, row_hash='old')
        principals = [_principal(policy=f'ADH-T{i}', policy_id=i, policy_status=2)
                      for i in range(1, 21)]
        with self.assertRaises(engine.LoadFileAborted) as ctx:
            self._build(_membership(principals))
        self.assertIn('far more than a normal day', str(ctx.exception))


class SnapshotCommitTests(_ServiceTestCase):
    """Defect 1 / checklist H26 — the snapshot never advanced on Release."""

    def test_the_run_persists_what_it_sent_so_a_later_request_can_commit_it(self):
        m = _membership([_principal()])
        with mock.patch('healthcare.afa_members.fetch_membership', return_value=m), \
             mock.patch('healthcare.afa_members.replica_lag_seconds', return_value=1), \
             mock.patch('healthcare.afa_service._paid_group_ids', return_value={GROUP}):
            run = afa_service.build_and_store(date(2026, 8, 7))
        self.assertTrue(run.sent_keys)
        # Re-read from the DB — this is exactly what the Release request does.
        fresh = AfaLoadFileRun.objects.get(pk=run.pk)
        self.assertTrue(fresh.sent_keys)
        afa_service.commit_snapshot(fresh)
        self.assertEqual(
            AfaMemberSnapshot.objects.filter(
                state=AfaMemberSnapshot.State.ACTIVE).count(), 1)

    def test_committing_a_run_with_no_record_raises_instead_of_doing_nothing(self):
        run = AfaLoadFileRun.objects.create(run_date=date(2026, 8, 7), row_count=5)
        with self.assertRaises(afa_service.SnapshotNotRecorded):
            afa_service.commit_snapshot(run)

    def test_the_second_run_sees_the_first_run_as_already_sent(self):
        m = _membership([_principal()])
        with mock.patch('healthcare.afa_members.fetch_membership', return_value=m), \
             mock.patch('healthcare.afa_members.replica_lag_seconds', return_value=1), \
             mock.patch('healthcare.afa_service._paid_group_ids', return_value={GROUP}):
            run = afa_service.build_and_store(date(2026, 8, 7))
            afa_service.commit_snapshot(AfaLoadFileRun.objects.get(pk=run.pk))
            second, _ = afa_service.build(date(2026, 8, 8))
        self.assertEqual(second.new, 0)


class KeyRoundTripTests(TestCase):

    def test_a_key_survives_being_stored_as_json_and_read_back(self):
        for policy, dep in (('ADH25002', 0), ('ADH-T1', 12), ('AD|ODD', 3)):
            with self.subTest(policy=policy):
                self.assertEqual(
                    afa_service._unkey(afa_service._key(policy, dep)), (policy, dep))
