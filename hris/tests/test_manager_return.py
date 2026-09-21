"""Tests for the Monthly Manager Return (CFO 2026-07-26).

Covers the things that would actually hurt if they broke:
  * role-specific questions — an IT manager or a driver is NEVER asked about sales
    (the CFO's explicit correction), a sales manager IS;
  * Aria's output is untrusted — junk, prose, wrong types and key collisions are
    all rejected without breaking the form;
  * the completeness gate cannot be bypassed;
  * separation of duties — nobody clears their own return;
  * a cleared return is locked and immutable;
  * the facts half is frozen at submit and cannot be typed by the filer.
"""
from decimal import Decimal

from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.test import TestCase, override_settings
from django.urls import reverse
from rest_framework.test import APITestCase

from core.models import Company, UserCompanyAccess, UserProfile
from hris.models import HRISProfile
from hris.manager_return_models import ManagerMonthlyReturn, ReturnStatus, ReviewVerdict
from hris.manager_return_questions import (
    _parse_ai, department_block, question_set, wants_sales, wants_sla,
)
from hris.manager_return_service import (
    build_team_pack, clear, get_or_create_draft, resolve_reviewer, save_draft,
    send_back, submit,
)
from payroll.models import Employee


class RoleSpecificQuestionsTest(TestCase):
    """CFO 2026-07-26: "we can't ask IT team and driver for sales targets"."""

    def test_it_manager_is_not_asked_about_sales(self):
        self.assertFalse(wants_sales('Information Technology', 'IT Manager'))
        spec = question_set('IT Manager', 'Information Technology', use_ai=False)
        self.assertFalse(spec['asks_sales'])
        keys = [q['key'] for b in spec['blocks'] for q in b['questions']]
        self.assertNotIn('sales_target_met', keys)
        self.assertNotIn('new_sales_amount', keys)

    def test_driver_is_not_asked_about_sales(self):
        self.assertFalse(wants_sales('Fleet', 'Driver'))
        spec = question_set('Driver', 'Fleet', use_ai=False)
        self.assertFalse(spec['asks_sales'])

    def test_sales_manager_is_asked_about_sales(self):
        self.assertTrue(wants_sales('Sales & Marketing', 'Sales Manager'))
        spec = question_set('Sales Manager', 'Sales & Marketing', use_ai=False)
        self.assertTrue(spec['asks_sales'])
        keys = [q['key'] for b in spec['blocks'] for q in b['questions']]
        self.assertIn('sales_target_met', keys)
        self.assertIn('new_sales_amount', keys)

    def test_it_gets_it_questions_and_aml_gets_kyc(self):
        key, qs = department_block('Information Technology', 'IT Manager')
        self.assertEqual(key, 'it')
        self.assertIn('it_backups', [q['key'] for q in qs])

        key, qs = department_block('AML', 'AML Officer')
        self.assertEqual(key, 'aml')
        self.assertIn('aml_kyc_backlog', [q['key'] for q in qs])

    def test_it_does_not_match_inside_another_word(self):
        """'it' must not match 'credit' / 'audit' / 'security' — that would give a
        credit controller the IT questionnaire."""
        for dept, title in (('Credit Control', 'Credit Controller'),
                            ('Internal Audit', 'Audit Manager')):
            key, _qs = department_block(dept, title)
            self.assertNotEqual(key, 'it', f'{dept}/{title} wrongly matched IT')

    def test_claims_gets_sla_questions(self):
        self.assertTrue(wants_sla('Claims', 'Claims Manager'))
        spec = question_set('Claims Manager', 'Claims', use_ai=False)
        self.assertTrue(spec['asks_sla'])
        keys = [q['key'] for b in spec['blocks'] for q in b['questions']]
        self.assertIn('sla_breaches', keys)

    def test_unknown_department_still_gets_the_base_set(self):
        spec = question_set('Chief Widget Officer', 'Widgets', use_ai=False)
        keys = [q['key'] for b in spec['blocks'] for q in b['questions']]
        for required in ('leave_action', 'innovation', 'fy27_actions', 'overstaffed'):
            self.assertIn(required, keys)


class AriaOutputIsUntrustedTest(TestCase):
    """The model is a suggestion engine, not a source of truth."""

    def test_junk_and_prose_are_rejected(self):
        for junk in ('', 'I cannot help with that.', 'null', '{}', '[]',
                     'Here are some questions: 1. How are you?'):
            self.assertEqual(_parse_ai(junk, 4), [])

    def test_fenced_json_is_still_parsed(self):
        raw = '```json\n[{"key":"it_patching","label":"Are servers patched?","kind":"bool"}]\n```'
        out = _parse_ai(raw, 4)
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]['key'], 'ai_it_patching')
        self.assertEqual(out[0]['kind'], 'bool')

    def test_ai_cannot_collide_with_a_real_column(self):
        """A model returning key 'innovation' or 'sales_target_met' must not be
        able to shadow the real field — that would let it hijack the form."""
        raw = ('[{"key":"innovation","label":"x?","kind":"text"},'
               '{"key":"sales_target_met","label":"y?","kind":"bool"}]')
        self.assertEqual(_parse_ai(raw, 4), [])

    def test_bad_keys_bad_kinds_and_overlong_labels_are_dropped(self):
        raw = ('[{"key":"BAD KEY","label":"a?"},'
               '{"key":"ok_one","label":"' + 'x' * 250 + '"},'
               '{"key":"ok_two","label":"fine?","kind":"telepathy"}]')
        out = _parse_ai(raw, 4)
        self.assertEqual([q['key'] for q in out], ['ai_ok_two'])
        self.assertEqual(out[0]['kind'], 'text')      # unknown kind falls back

    def test_limit_is_honoured(self):
        raw = '[' + ','.join(
            f'{{"key":"q{i}","label":"Q{i}?"}}' for i in range(20)) + ']'
        self.assertEqual(len(_parse_ai(raw, 3)), 3)

    def test_duplicate_keys_collapse(self):
        raw = '[{"key":"dup","label":"a?"},{"key":"dup","label":"b?"}]'
        self.assertEqual(len(_parse_ai(raw, 4)), 1)


@override_settings(ELRA_PERF_ENABLED=True)
class EntityIsolationTest(APITestCase):
    """DeepSeek round 1, HIGH: the league table and the Development-Dialogue
    rollup had no entity clamp, while the sibling perf_monthly_views does. An HR
    user granted only Company A must never see Company B's managers — omni's
    standing cross-entity-leak invariant.
    """

    @classmethod
    def setUpTestData(cls):
        cls.co_a = Company.objects.create(code='COA', name='Company A')
        cls.co_b = Company.objects.create(code='COB', name='Company B')

        cls.mgr_a = Employee.objects.create(
            employee_number='A1', full_name='Manager Alpha', company=cls.co_a,
            department='Finance', job_title='Finance Manager')
        cls.rep_a = Employee.objects.create(
            employee_number='A2', full_name='Report Alpha', company=cls.co_a)
        HRISProfile.objects.create(employee=cls.rep_a, manager=cls.mgr_a)

        cls.mgr_b = Employee.objects.create(
            employee_number='B1', full_name='Manager Bravo', company=cls.co_b,
            department='Claims', job_title='Claims Manager')
        cls.rep_b = Employee.objects.create(
            employee_number='B2', full_name='Report Bravo', company=cls.co_b)
        HRISProfile.objects.create(employee=cls.rep_b, manager=cls.mgr_b)

        # An HRIS user explicitly scoped to Company A only.
        # Deliberately NOT superuser and NOT is_administrator: core.models
        # .allowed_company_ids puts both of those in the UNRESTRICTED bucket, so
        # an admin fixture would pass this test without the clamp doing anything.
        # 'kago' is on the HRIS whitelist, so the user reaches the view on merit.
        cls.hr_a = User.objects.create_user('kago', email='kago@alphadirect.co.bw')
        UserCompanyAccess.objects.create(user=cls.hr_a, company=cls.co_a,
                                         can_view=True)

    def test_league_table_does_not_leak_another_entity(self):
        self.client.force_authenticate(self.hr_a)
        r = self.client.get(reverse('hris:api-manager-return-league'),
                            {'year': 2026, 'month': 6})
        self.assertEqual(r.status_code, 200, r.content)
        names = [row['manager'] for row in r.json()['managers']]
        self.assertIn('Manager Alpha', names)
        self.assertNotIn('Manager Bravo', names)

    def test_hr_fallback_cannot_clear_another_entitys_return(self):
        """DeepSeek round 2: the HR/exec fallback (used when a manager has no line
        manager on record) is a ROLE, not a named relationship, so it must be
        entity-clamped. Company A's HR user must not clear Company B's return."""
        from hris.manager_return_models import ManagerMonthlyReturn, ReturnStatus
        ret = ManagerMonthlyReturn.objects.create(
            manager=self.mgr_b, period_year=2026, period_month=6,
            status=ReturnStatus.SUBMITTED, submitted_to=None)
        self.client.force_authenticate(self.hr_a)
        r = self.client.post(
            reverse('hris:api-manager-return-decide', args=[ret.id]),
            {'action': 'clear', 'verdict': 'on_track', 'notes': 'ok'}, format='json')
        self.assertEqual(r.status_code, 403, r.content)
        ret.refresh_from_db()
        self.assertEqual(ret.status, ReturnStatus.SUBMITTED)
        self.assertFalse(ret.is_locked)

    def test_cross_entity_reporting_line_is_deliberately_allowed(self):
        """The counter-case that must KEEP working: a manager in one entity who
        genuinely manages someone in another (the CFO manages Tshephang in
        Veritas) must still be able to clear their report's return. Clamping the
        reporting-line path by company would break the live org chart."""
        from hris.manager_return_models import ManagerMonthlyReturn, ReturnStatus
        reviewer_user = User.objects.create_user('xco', email='xco@alphadirect.co.bw')
        reviewer = Employee.objects.create(
            employee_number='X1', full_name='Cross Entity Boss',
            company=self.co_a, email='xco@alphadirect.co.bw', user=reviewer_user)
        ret = ManagerMonthlyReturn.objects.create(
            manager=self.mgr_b,             # Company B
            period_year=2026, period_month=6,
            status=ReturnStatus.SUBMITTED,
            submitted_to=reviewer)          # reviewer sits in Company A
        self.client.force_authenticate(reviewer_user)
        r = self.client.post(
            reverse('hris:api-manager-return-decide', args=[ret.id]),
            {'action': 'clear', 'verdict': 'on_track', 'notes': 'Good month.'},
            format='json')
        self.assertEqual(r.status_code, 200, r.content)
        ret.refresh_from_db()
        self.assertEqual(ret.status, ReturnStatus.CLEARED)

    def test_rollup_refuses_an_out_of_scope_employee(self):
        self.client.force_authenticate(self.hr_a)
        ok = self.client.get(reverse('hris:api-manager-return-rollup'),
                             {'employee': str(self.mgr_a.id), 'year': 2026})
        self.assertEqual(ok.status_code, 200, ok.content)
        leak = self.client.get(reverse('hris:api-manager-return-rollup'),
                               {'employee': str(self.mgr_b.id), 'year': 2026})
        self.assertEqual(leak.status_code, 403, leak.content)


@override_settings(ELRA_PERF_ENABLED=True)
class ReturnWorkflowTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.company = Company.objects.create(code='MRT', name='Return Test Co.')
        cls.boss_user = User.objects.create_user('boss', email='boss@alphadirect.co.bw')
        cls.mgr_user = User.objects.create_user('mgr', email='mgr@alphadirect.co.bw')

        cls.boss = Employee.objects.create(
            employee_number='B1', full_name='The Boss', company=cls.company,
            email='boss@alphadirect.co.bw', user=cls.boss_user,
            department='Executive', job_title='Chief Executive Officer')
        cls.mgr = Employee.objects.create(
            employee_number='M1', full_name='Team Lead', company=cls.company,
            email='mgr@alphadirect.co.bw', user=cls.mgr_user,
            department='Information Technology', job_title='IT Manager')
        cls.report = Employee.objects.create(
            employee_number='R1', full_name='Team Member', company=cls.company,
            department='Information Technology', job_title='Developer')

        HRISProfile.objects.create(employee=cls.mgr, manager=cls.boss)
        cls.report_profile = HRISProfile.objects.create(
            employee=cls.report, manager=cls.mgr)

    def _draft(self):
        return get_or_create_draft(self.mgr, 2026, 6)

    def _fill(self, ret, **over):
        answers = {
            'leave_action': 'Docked two days and spoke to both of them.',
            'innovation': 'Automated the nightly backup verification.',
            'fy27_actions': 'Cutting licence spend and cross-training the team.',
            'work_finished_on_time': True,
            'dashboard_cleared_on_time': True,
            'overstaffed': False,
            'fy27_aligned': True,
        }
        answers.update(over)
        # answer every shown department/Aria question
        extras = {k: 'Answered.' for k, _l in ret._shown_extra_questions()}
        save_draft(ret, answers, extra_answers=extras,
                   allowed_extra_keys=set(extras.keys()))
        return ret

    # ── the gate ────────────────────────────────────────────────────────────
    def test_cannot_submit_an_empty_return(self):
        ret = self._draft()
        with self.assertRaises(ValidationError):
            submit(ret)
        ret.refresh_from_db()
        self.assertEqual(ret.status, ReturnStatus.DRAFT)

    def test_missing_list_names_what_is_needed(self):
        ret = self._draft()
        gaps = ret.missing_for_submit()
        self.assertTrue(any('lost hours' in g for g in gaps))
        self.assertTrue(any('overstaffed' in g.lower() for g in gaps))

    def test_it_manager_gate_does_not_demand_sales(self):
        """The whole point of the CFO's correction: an IT manager can file a
        complete return without ever answering a sales question."""
        ret = self._fill(self._draft())
        self.assertEqual(ret.missing_for_submit(), [])
        self.assertIsNone(ret.sales_target_met)
        submit(ret)
        self.assertEqual(ret.status, ReturnStatus.SUBMITTED)

    def test_sales_manager_gate_does_demand_sales(self):
        self.mgr.department = 'Sales & Marketing'
        self.mgr.job_title = 'Sales Manager'
        self.mgr.save(update_fields=['department', 'job_title'])
        ret = self._fill(self._draft())
        self.assertTrue(any('sales target' in g.lower() for g in ret.missing_for_submit()))

    def test_overstaffed_yes_needs_a_plan(self):
        ret = self._fill(self._draft(), overstaffed=True)
        self.assertTrue(any('overstaffed' in g.lower() for g in ret.missing_for_submit()))
        ret = self._fill(ret, overstaffed=True,
                         overstaffed_comment='Not replacing the two leavers.')
        self.assertEqual(ret.missing_for_submit(), [])

    def test_declared_sla_breaches_must_be_explained(self):
        ret = self._fill(self._draft(), sla_breaches=4)
        self.assertTrue(any('SLA' in g for g in ret.missing_for_submit()))
        ret = self._fill(ret, sla_breaches=4, sla_explanation='Two assessors on leave.')
        self.assertEqual(ret.missing_for_submit(), [])

    def test_shown_department_questions_must_be_answered(self):
        ret = self._draft()
        save_draft(ret, {
            'leave_action': 'Spoke to them.', 'innovation': 'Scripted the backups.',
            'fy27_actions': 'Cutting spend.', 'work_finished_on_time': True,
            'dashboard_cleared_on_time': True, 'overstaffed': False,
            'fy27_aligned': True})
        # IT block was shown but left blank -> still incomplete
        self.assertTrue(ret._shown_extra_questions())
        self.assertTrue(ret.missing_for_submit())

    # ── the facts half is not typeable ──────────────────────────────────────
    def test_filer_cannot_type_the_facts(self):
        ret = self._draft()
        save_draft(ret, {'leave_action': 'x'},
                   extra_answers={'team_snapshot': 'fake', 'headcount_equivalent': 99},
                   allowed_extra_keys={'team_snapshot', 'headcount_equivalent'})
        ret.refresh_from_db()
        self.assertEqual(ret.team_snapshot, [])          # untouched
        self.assertIsNone(ret.headcount_equivalent)

    def test_unshown_extra_keys_are_ignored_not_stored(self):
        ret = self._draft()
        save_draft(ret, {}, extra_answers={'evil_key': 'payload'},
                   allowed_extra_keys=set())
        ret.refresh_from_db()
        self.assertNotIn('evil_key', ret.dept_answers)

    def test_submit_freezes_the_snapshot(self):
        ret = submit(self._fill(self._draft()))
        self.assertEqual(len(ret.team_snapshot), 1)
        self.assertEqual(ret.team_snapshot[0]['name'], 'Team Member')
        self.assertEqual(ret.submitted_to_id, self.boss.id)
        self.assertIsNotNone(ret.submitted_at)

    def test_cannot_edit_after_submit(self):
        ret = submit(self._fill(self._draft()))
        with self.assertRaises(ValidationError):
            save_draft(ret, {'innovation': 'changed my mind'})

    def test_cannot_submit_twice(self):
        ret = submit(self._fill(self._draft()))
        with self.assertRaises(ValidationError):
            submit(ret)

    # ── review ──────────────────────────────────────────────────────────────
    def test_reviewer_is_the_filers_own_manager(self):
        self.assertEqual(resolve_reviewer(self.mgr).id, self.boss.id)

    def test_clear_locks_the_record(self):
        ret = submit(self._fill(self._draft()))
        clear(ret, self.boss_user, ReviewVerdict.ON_TRACK, 'Good month.')
        ret.refresh_from_db()
        self.assertEqual(ret.status, ReturnStatus.CLEARED)
        self.assertTrue(ret.is_locked)
        with self.assertRaises(ValidationError):
            save_draft(ret, {'innovation': 'tampered'})

    def test_nobody_clears_their_own_return(self):
        ret = submit(self._fill(self._draft()))
        with self.assertRaises(ValidationError):
            clear(ret, self.mgr_user, ReviewVerdict.ON_TRACK, 'Self-signed.')
        ret.refresh_from_db()
        self.assertEqual(ret.status, ReturnStatus.SUBMITTED)

    def test_watch_and_intervene_need_a_reason(self):
        ret = submit(self._fill(self._draft()))
        for verdict in (ReviewVerdict.WATCH, ReviewVerdict.INTERVENE):
            with self.assertRaises(ValidationError):
                clear(ret, self.boss_user, verdict, '   ')

    def test_bad_verdict_is_rejected(self):
        ret = submit(self._fill(self._draft()))
        with self.assertRaises(ValidationError):
            clear(ret, self.boss_user, 'excellent', 'nice')

    def test_send_back_reopens_for_the_filer(self):
        ret = submit(self._fill(self._draft()))
        send_back(ret, self.boss_user, 'Say more about the SLA breaches.')
        ret.refresh_from_db()
        self.assertEqual(ret.status, ReturnStatus.RETURNED)
        save_draft(ret, {'innovation': 'Expanded answer.'})   # editable again

    def test_cannot_clear_a_draft(self):
        ret = self._draft()
        with self.assertRaises(ValidationError):
            clear(ret, self.boss_user, ReviewVerdict.ON_TRACK, 'too early')

    # ── dates + capacity signal ─────────────────────────────────────────────
    def test_due_dates_are_the_month_after_the_period(self):
        ret = self._draft()                       # period = June 2026
        self.assertEqual(ret.submit_due.isoformat(), '2026-07-05')
        self.assertEqual(ret.clear_due.isoformat(), '2026-07-10')

    def test_december_period_rolls_into_january(self):
        ret = ManagerMonthlyReturn(manager=self.mgr, period_year=2026, period_month=12)
        self.assertEqual(ret.submit_due.isoformat(), '2027-01-05')
        self.assertEqual(ret.clear_due.isoformat(), '2027-01-10')

    def test_capacity_signal_is_unknown_not_zero_when_nobody_tracked(self):
        """A month with no tracked hours must read 'unknown', never '0 people' —
        that would accuse every manager of being fully overstaffed."""
        pack = build_team_pack(self.mgr, 2026, 6)
        self.assertEqual(pack['headcount'], 1)
        self.assertIsNone(pack['headcount_equivalent'])

    def test_terminated_reports_drop_out_of_the_team(self):
        self.report.status = Employee.Status.TERMINATED
        self.report.save(update_fields=['status'])
        self.assertEqual(build_team_pack(self.mgr, 2026, 6)['headcount'], 0)

    def test_negative_sales_is_rejected(self):
        ret = self._draft()
        with self.assertRaises(ValidationError):
            save_draft(ret, {'new_sales_amount': Decimal('-5.00')})

    def test_prior_commitment_carries_forward(self):
        may = get_or_create_draft(self.mgr, 2026, 5)
        save_draft(may, {'next_month_commitment': 'Ship the backup automation.'})
        june = get_or_create_draft(self.mgr, 2026, 6)
        self.assertEqual(june.prior_commitments, 'Ship the backup automation.')

    def test_period_year_bounds_are_enforced_on_the_model(self):
        """DeepSeek round 1: the view clamps the period, but the model must not be
        able to store a nonsense year through any other path."""
        for bad_year in (1999, 9999):
            ret = ManagerMonthlyReturn(manager=self.mgr, period_year=bad_year,
                                       period_month=6)
            with self.assertRaises(ValidationError):
                ret.clean()

    def test_one_return_per_manager_per_month(self):
        a = get_or_create_draft(self.mgr, 2026, 6)
        b = get_or_create_draft(self.mgr, 2026, 6)
        self.assertEqual(a.id, b.id)
        self.assertEqual(ManagerMonthlyReturn.objects.filter(
            manager=self.mgr, period_year=2026, period_month=6).count(), 1)
