"""Ghost-payroll guard (CFO 2026-08-05).

Covers the fixes made after Omni publicly named eight WORKING people to Exco as
"ghosts" because a stale HR chase-task was never closed once they got matched:

  * resolve_recovered_ghost_tasks — a task auto-closes the moment its person is
    matched again or has no active payroll record (the anti-recurrence guard).
  * ghost tasks are owned by Dorothy Ikgopoleng, and the "left" branch asks for
    the exit date.
  * ai_screen_ghosts — DeepSeek/Gemini hold a likely-working-but-unlinked name off
    the chase task; fail-safe HOLD when the check can't run.
"""
import datetime as dt
from unittest import mock

from django.contrib.auth.models import User
from django.test import TestCase

from core.models import Company, OmniTask
from payroll.models import Employee
from hris import ghost_payroll as gp

DAY = dt.date(2099, 1, 1)


class GhostGuardTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.co = Company.objects.create(code='TEST', name='Test Co.')
        cls.cfo = User.objects.create_user('pg', email='pganesharajah@alphadirect.co.bw',
                                            password='x', is_superuser=True)
        cls.dorothy = User.objects.create_user('dikgopoleng',
                                               email='dikgopoleng@alphadirect.co.bw', password='x')
        cls.unami = User.objects.create_user('ubutale',
                                             email='ubutale@alphadirect.co.bw', password='x')

    def _emp(self, name, status=Employee.Status.ACTIVE, num=None):
        return Employee.objects.create(employee_number=(num or name[:20]), full_name=name,
                                       company=self.co, status=status)

    # -- ownership + body ------------------------------------------------------
    def test_ghost_task_owned_by_dorothy_and_asks_exit_date(self):
        created, _ = gp.raise_ghost_tasks(['Some Ghost'], DAY)
        self.assertEqual(created, ['Some Ghost'])
        t = OmniTask.objects.get(source=gp.SOURCE)
        self.assertEqual(t.assignee, self.dorothy)          # Dorothy, not Unami
        self.assertIn('EXACT LAST WORKING DAY', t.body)     # exit date is asked for

    # -- auto-resolve guard ----------------------------------------------------
    def test_matched_person_task_auto_closes(self):
        gp.raise_ghost_tasks(['Working Person'], DAY)
        closed = gp.resolve_recovered_ghost_tasks(['Working Person'], DAY)
        self.assertEqual(closed, ['Working Person'])
        t = OmniTask.objects.get(title__endswith='Working Person')
        self.assertEqual(t.status, OmniTask.Status.CANCELLED)
        self.assertTrue(t.comments.filter(new_status=OmniTask.Status.CANCELLED).exists())

    def test_genuine_active_ghost_task_is_kept(self):
        self._emp('Real Ghost')                              # active, still unmatched
        gp.raise_ghost_tasks(['Real Ghost'], DAY)
        closed = gp.resolve_recovered_ghost_tasks([], DAY)   # nobody matched
        self.assertEqual(closed, [])
        self.assertEqual(OmniTask.objects.get(title__endswith='Real Ghost').status,
                         OmniTask.Status.PENDING)

    def test_no_active_record_task_auto_closes(self):
        self._emp('Left Person', status=Employee.Status.TERMINATED)
        gp.raise_ghost_tasks(['Left Person'], DAY)
        closed = gp.resolve_recovered_ghost_tasks([], DAY)
        self.assertEqual(closed, ['Left Person'])
        self.assertEqual(OmniTask.objects.get(title__endswith='Left Person').status,
                         OmniTask.Status.CANCELLED)

    def test_matched_twin_does_not_close_live_ghost_task(self):
        # Two people share a name: a matched twin must NOT close the real ghost's
        # task while that name is still a live ghost today (DeepSeek review).
        self._emp('Dup Name')                                # active, still unmatched
        gp.raise_ghost_tasks(['Dup Name'], DAY)
        closed = gp.resolve_recovered_ghost_tasks(['Dup Name'], DAY, current_ghosts=['Dup Name'])
        self.assertEqual(closed, [])
        self.assertEqual(OmniTask.objects.get(title__endswith='Dup Name').status,
                         OmniTask.Status.PENDING)

    # -- DeepSeek/Gemini screen ------------------------------------------------
    def test_screen_reports_all_when_no_unmatched_td(self):
        self.assertEqual(gp.ai_screen_ghosts(['A', 'B'], [], DAY), (['A', 'B'], [], []))

    def test_screen_holds_all_unavailable_on_both_engines_down(self):
        def boom(*a, **k):
            raise RuntimeError('gateway down')
        with mock.patch('core.ai_assist.is_safe_for_ai',
                        return_value=mock.Mock(safe=True, redacted_text='x')), \
             mock.patch('core.ai_assist.deepseek_complete', side_effect=boom), \
             mock.patch('core.ai_assist.gemini_complete', side_effect=boom):
            report, matched, unavail = gp.ai_screen_ghosts(
                ['A'], [{'name': 'Alpha', 'email': 'a@x'}], DAY)
        self.assertEqual(report, [])          # fail-safe: nobody chased
        self.assertEqual(matched, [])         # NOT falsely claimed as a match
        self.assertEqual(unavail, ['A'])      # held only because the screen couldn't run

    def test_screen_holds_positive_match_only(self):
        ds = '{"verdicts":[{"id":0,"match":true},{"id":1,"match":false}]}'
        with mock.patch('core.ai_assist.is_safe_for_ai',
                        return_value=mock.Mock(safe=True, redacted_text='x')), \
             mock.patch('core.ai_assist.deepseek_complete', return_value=ds), \
             mock.patch('core.ai_assist.gemini_complete', side_effect=RuntimeError):
            report, matched, unavail = gp.ai_screen_ghosts(
                ['Match Me', 'Real One'], [{'name': 'Matchme', 'email': 'm@x'}], DAY)
        self.assertEqual(report, ['Real One'])
        self.assertEqual(matched, ['Match Me'])
        self.assertEqual(unavail, [])
