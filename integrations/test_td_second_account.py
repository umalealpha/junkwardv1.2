"""TD-MERGE-02 — a person's SECOND Time Doctor account must fold onto their
confirmed identity, not vanish (bug 5dffc022, N. Nthite 2026-09-03).

What happened. On 2 Sep her Time Doctor desktop client re-registered under the
Windows machine SID instead of her email, so TD created a second user account:

  adkFmpg_AyGD8aDl  nnthite@alphadirect.co.bw                    confirmed → Employee
  apfkTcCt1f8NLyM6  S-1-5-21-2646779368-…@<company>.alphadirect… unmatched

Both carried the SAME deviceId (D82C7A28-…) — one laptop, two accounts. Her real
5.73 h at 99.5 % productive landed on the unmatched one, so /my-omni and her
morning brief read 0 h for the day and no amount of waiting would ever fix it:
the settle samples show 20,556 tracked seconds present from the 03:00 pull.

The merge machinery already existed (TD_ACCOUNT_ALIASES_*) but was a hand-typed
allow-list — Prathap, Paul Beka and Amantle Thake were each added only after a
human noticed. So every new second account silently drops that person's hours
until someone complains. This makes the fold automatic when it is PROVABLE: the
unmatched account's normalised name equals that of an account a human has
already confirmed against payroll.

The hardcoded table still wins, and the reported figure is still the BUSIEST
machine, never the sum (FROZEN, CFO 2026-07-29).
"""
from django.test import TestCase

from integrations.models import TimeDoctorUserMap
from integrations import td_matching
from integrations.td_matching import (canonical_identity, collapse_users,
                                      fold_uid, norm_name)
from payroll.models import Employee


def _reset():
    """Drop the alias memo between tests. Tolerates the helper being absent so
    a revert-the-fix run fails on the missing FOLD, not on a missing name."""
    getattr(td_matching, 'reset_dynamic_alias_cache', lambda: None)()


CONFIRMED_UID = 'adkFmpg_AyGD8aDl'      # her email account, payroll-linked
SID_UID       = 'apfkTcCt1f8NLyM6'      # her machine-SID account, unmatched
SID_EMAIL     = ('S-1-5-21-2646779368-1787180032-2412987570-2002'
                 '@XnInT13-PAAEpEkJ.alphadirect.co.bw')


class SecondTDAccountFoldsTests(TestCase):
    def setUp(self):
        _reset()
        self.emp = Employee.objects.create(full_name='Natasha Nthite', employee_number='TD-T1',
                                           email='nnthite@alphadirect.co.bw')
        TimeDoctorUserMap.objects.create(
            td_user_id=CONFIRMED_UID, td_name='Natasha Nthite',
            td_email='nnthite@alphadirect.co.bw', employee=self.emp,
            confirmed=True, source=TimeDoctorUserMap.Source.MANUAL)
        TimeDoctorUserMap.objects.create(
            td_user_id=SID_UID, td_name='Natasha Nthite', td_email=SID_EMAIL,
            employee=None, confirmed=False)

    def tearDown(self):
        _reset()

    # ── the fold itself ─────────────────────────────────────────────────────
    def test_sid_account_folds_onto_the_confirmed_one(self):
        uid, name = canonical_identity(SID_UID, 'Natasha Nthite')
        self.assertEqual(uid, CONFIRMED_UID)
        self.assertTrue(name.endswith('+'), name)

    def test_fold_uid_agrees(self):
        self.assertEqual(fold_uid(SID_UID), CONFIRMED_UID)

    def test_confirmed_account_is_its_own_canonical(self):
        uid, _ = canonical_identity(CONFIRMED_UID, 'Natasha Nthite')
        self.assertEqual(uid, CONFIRMED_UID)

    def test_canonical_name_still_matches_payroll(self):
        # norm_name() must drop the "+" or the matcher loses her again.
        _, name = canonical_identity(SID_UID, 'Natasha Nthite')
        self.assertEqual(norm_name(name), norm_name('Natasha Nthite'))

    def test_collapse_users_yields_one_roster_row(self):
        rows = collapse_users([
            {'id': CONFIRMED_UID, 'name': 'Natasha Nthite',
             'email': 'nnthite@alphadirect.co.bw'},
            {'id': SID_UID, 'name': 'Natasha Nthite', 'email': SID_EMAIL},
        ])
        self.assertEqual(len(rows), 1, rows)
        self.assertEqual(rows[0].get('id') or rows[0].get('user_id'), CONFIRMED_UID)

    # ── it must not guess ───────────────────────────────────────────────────
    def test_role_accounts_never_fold(self):
        # 'User' / 'OTHERS' are role rigs, not people — a single token must
        # never be folded onto anybody.
        for junk in ('User', 'OTHERS', 'Admin'):
            TimeDoctorUserMap.objects.create(
                td_user_id=f'junk-{junk}', td_name=junk, td_email='x@y.z',
                employee=None, confirmed=False)
            _reset()
            self.assertEqual(fold_uid(f'junk-{junk}'), f'junk-{junk}', junk)

    def test_unmatched_account_with_no_confirmed_twin_stays_put(self):
        # Kelvin Kimani has a SID account and NO confirmed payroll-linked
        # account — there is nothing to fold onto, so he must stay in the
        # review queue rather than be attached to a stranger.
        TimeDoctorUserMap.objects.create(
            td_user_id='anG8rh-J2z97XiXc', td_name='Kelvin Kimani',
            td_email='S-1-5-21-999@x.y', employee=None, confirmed=False)
        _reset()
        self.assertEqual(fold_uid('anG8rh-J2z97XiXc'), 'anG8rh-J2z97XiXc')

    def test_ambiguous_confirmed_namesakes_never_fold(self):
        # Two DIFFERENT confirmed employees carrying the same TD display name:
        # folding would credit one person's hours to the other. Skip instead.
        other = Employee.objects.create(full_name='Natasha Nthite',
                                        employee_number='TD-T2',
                                        email='n.nthite2@alphadirect.co.bw')
        TimeDoctorUserMap.objects.create(
            td_user_id='second-confirmed', td_name='Natasha Nthite',
            td_email='n.nthite2@alphadirect.co.bw', employee=other,
            confirmed=True, source=TimeDoctorUserMap.Source.MANUAL)
        _reset()
        self.assertEqual(fold_uid(SID_UID), SID_UID)

    def test_an_unconfirmed_target_is_not_authority_enough(self):
        # Only a HUMAN-confirmed payroll link may act as a fold target.
        TimeDoctorUserMap.objects.filter(td_user_id=CONFIRMED_UID).update(confirmed=False)
        _reset()
        self.assertEqual(fold_uid(SID_UID), SID_UID)

    def test_hardcoded_aliases_still_win(self):
        # The frozen table is the authority; the dynamic pass only fills gaps.
        self.assertEqual(fold_uid('aetqn--rymG6S7qQ'), 'XnseOWIRLwAEEwM1')
        self.assertEqual(fold_uid('amHAqdPdPz1edaae'), 'Y8eRQCG4OcfgpaUU')
        self.assertEqual(fold_uid('anrw7sg53nktLafo'), 'ao7FWooeVHTHZtPk')

    def test_merge_marker_is_never_doubled(self):
        # Paul Beka's canonical row is literally named 'Paul Beka +', so a naive
        # f'{name} +' produced 'Paul Beka + +' in every report.
        emp = Employee.objects.create(full_name='Paul Beka',
                                      employee_number='TD-T4',
                                      email='pbeka@alphadirect.co.bw')
        TimeDoctorUserMap.objects.create(
            td_user_id='pb-canonical', td_name='Paul Beka +',
            td_email='pbeka@alphadirect.co.bw', employee=emp,
            confirmed=True, source=TimeDoctorUserMap.Source.MANUAL)
        TimeDoctorUserMap.objects.create(
            td_user_id='pb-second-machine', td_name='Paul Beka',
            td_email='S-1-5-21-777@x.y', employee=None, confirmed=False)
        _reset()
        _, name = canonical_identity('pb-second-machine', 'Paul Beka')
        self.assertEqual(name, 'Paul Beka +')

    def test_accounts_owned_by_the_frozen_table_are_left_to_it(self):
        # amHAqdPdPz1edaae is Paul Beka's second machine, already hardcoded.
        # The dynamic pass must not build a duplicate entry for it.
        self.assertNotIn('amHAqdPdPz1edaae',
                         td_matching.dynamic_account_aliases())

    def test_no_map_rows_is_a_no_op(self):
        TimeDoctorUserMap.objects.all().delete()
        _reset()
        self.assertEqual(fold_uid(SID_UID), SID_UID)
        self.assertEqual(canonical_identity('whoever', 'Some One'),
                         ('whoever', 'Some One'))


class MergedFigureIsBusiestMachineTests(TestCase):
    """FROZEN (CFO 2026-07-29): a merged person reports the BUSIEST machine,
    never the sum. Her 2-Sep day was 0.0 h on the email account and 5.73 h on
    the SID account — the honest figure is 5.73 h."""

    def setUp(self):
        _reset()
        emp = Employee.objects.create(full_name='Natasha Nthite',
                                      employee_number='TD-T3',
                                      email='nnthite@alphadirect.co.bw')
        TimeDoctorUserMap.objects.create(
            td_user_id=CONFIRMED_UID, td_name='Natasha Nthite',
            td_email='nnthite@alphadirect.co.bw', employee=emp,
            confirmed=True, source=TimeDoctorUserMap.Source.MANUAL)
        TimeDoctorUserMap.objects.create(
            td_user_id=SID_UID, td_name='Natasha Nthite', td_email=SID_EMAIL,
            employee=None, confirmed=False)

    def tearDown(self):
        _reset()

    def test_her_real_day_is_reported(self):
        from datetime import date

        from integrations.timedoctor import aggregate
        users = [
            {'id': CONFIRMED_UID, 'name': 'Natasha Nthite',
             'email': 'nnthite@alphadirect.co.bw'},
            {'id': SID_UID, 'name': 'Natasha Nthite', 'email': SID_EMAIL},
        ]
        # Her real 2-Sep day: 20,626 observed seconds on the SID machine,
        # nothing on the email account.
        worklog = [
            {'userId': SID_UID, 'time': 20626, 'mode': 'computer'},
            {'userId': CONFIRMED_UID, 'time': 0, 'mode': 'computer'},
        ]
        timeuse = [
            [{'userId': SID_UID, 'time': 20532, 'score': 4},
             {'userId': SID_UID, 'time': 94, 'score': 1}],
            [],
        ]
        out = aggregate(users, worklog, timeuse, [], [],
                        as_of=date(2026, 9, 2),
                        td_user_ids=[SID_UID, CONFIRMED_UID])
        rows = [r for r in out['members']
                if norm_name(r['name']) == 'natasha nthite']
        self.assertEqual(len(rows), 1, rows)
        self.assertEqual(rows[0]['user_id'], CONFIRMED_UID)
        self.assertEqual(rows[0]['hours_tracked'], 5.73)
        self.assertEqual(rows[0]['machine_count'], 2)
