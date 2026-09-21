"""Tests for the Staff Incentive Approval workflow (CFO 2026-07-13).

Covers: submit gating (managers+, never ess), dual CFO+HR signatures (one
alone keeps it pending; both approve it), segregation of duties, rejection,
and the Finance payroll-processed tick.
"""
import datetime

from django.contrib.auth.models import User
from django.utils import timezone
from rest_framework.test import APIClient, APITestCase

from core.deadline_test_utils import submission_window_open
from core.models import Company, OmniTask
from hris.models import HRISProfile, IncentiveRequest
from payroll.models import Employee

URL = '/hris/api/incentives/'

# A fully-qualified line (passes the earned-incentive gate): beyond normal
# duties, on time, error-free, no manager fix, with a >=50-word justification.
QUALIFIED = dict(
    beyond_normal_duties=True, on_time=True, error_free=True,
    needed_manager_fix=False,
    justification=("This work went far beyond the employee's normal day-to-day role "
                   'and required substantial extra effort over several evenings and one '
                   'weekend to design, test and deliver a new process that was not part '
                   'of their job description. It was completed on time, was entirely '
                   'error-free, and needed no manager intervention or rework whatsoever '
                   'to finalise.'),
)


@submission_window_open   # the 16th deadline (2026-08-28) blocks post-16th submits
class IncentiveWorkflowTest(APITestCase):
    @classmethod
    def setUpTestData(cls):
        cls.company = Company.objects.create(code='TEST', name='Test Co.')

        # Maker: a genuine people-manager (Bharath-style, NOT whitelisted).
        cls.maker_user = User.objects.create_user(
            'bharath', 'bbalasubramanian@alphadirect.co.bw', 'x')
        cls.maker_emp = Employee.objects.create(
            employee_number='E200', full_name='Bharath B.',
            job_title='Senior Manager', department='Sales',
            email='bbalasubramanian@alphadirect.co.bw',
            company=cls.company, user=cls.maker_user)
        report = Employee.objects.create(
            employee_number='E201', full_name='Report One',
            job_title='Agent', department='Sales', company=cls.company)
        HRISProfile.objects.create(employee=report, manager=cls.maker_emp)

        cls.cfo = User.objects.create_user(
            'prathap', 'pganesharajah@alphadirect.co.bw', 'x')
        cls.unami = User.objects.create_user(
            'unami', 'ubutale@alphadirect.co.bw', 'x')
        cls.pako = User.objects.create_user(
            'pako', 'pkago@alphadirect.co.bw', 'x')
        cls.ess = User.objects.create_user('worker', 'w@example.com', 'x')

    def _client(self, user):
        c = APIClient()
        c.force_authenticate(user)
        return c

    def _submit(self, user=None):
        return self._client(user or self.maker_user).post(URL, {
            'title': 'Motor claims incentives',
            'period': '2026-06',
            'department': 'Sales & Marketing',
            'manager_attested': True,
            'lines': [
                {'name': 'BW instant insurance incentive', 'amount': '10000.00', **QUALIFIED},
                {'name': 'DOM/COM', 'amount': '13,132.44', **QUALIFIED},
                {'name': 'Motor Claims', 'amount': '7500', **QUALIFIED},
            ],
        }, format='json')

    def _qline(self, **over):
        line = {'name': 'A', 'amount': '5000', **QUALIFIED}
        line.update(over)
        return {'title': 'X', 'period': '2026-06', 'manager_attested': True,
                'lines': [line]}

    def test_ess_cannot_submit_or_list(self):
        c = self._client(self.ess)
        self.assertEqual(c.get(URL).status_code, 403)
        self.assertEqual(self._submit(user=self.ess).status_code, 403)

    def test_manager_submits_and_sees_own(self):
        r = self._submit()
        self.assertEqual(r.status_code, 201, r.content)
        body = r.json()
        self.assertEqual(body['status'], 'pending')
        self.assertEqual(len(body['lines']), 3)
        self.assertEqual(body['total'], '30632.44')

        listed = self._client(self.maker_user).get(URL).json()
        self.assertEqual(len(listed['requests']), 1)
        self.assertTrue(listed['requests'][0]['is_own'])
        self.assertTrue(listed['me']['can_submit'])
        self.assertFalse(listed['me']['can_approve'])

    def test_bad_lines_rejected(self):
        c = self._client(self.maker_user)
        r = c.post(URL, {'title': 'X', 'period': '2026-06', 'manager_attested': True,
                         'lines': [{'name': 'A', 'amount': '-5'}]},
                   format='json')
        self.assertEqual(r.status_code, 400)
        r = c.post(URL, {'title': 'X', 'period': 'June 2026', 'manager_attested': True,
                         'lines': [{'name': 'A', 'amount': '5'}]},
                   format='json')
        self.assertEqual(r.status_code, 400)

    def test_earned_gate_blocks_unqualified(self):
        c = self._client(self.maker_user)
        # routine work (not beyond duties) — blocked
        self.assertEqual(
            c.post(URL, self._qline(beyond_normal_duties=False), format='json').status_code, 400)
        # not on time — blocked
        self.assertEqual(
            c.post(URL, self._qline(on_time=False), format='json').status_code, 400)
        # not error-free — blocked
        self.assertEqual(
            c.post(URL, self._qline(error_free=False), format='json').status_code, 400)
        # manager had to fix it — blocked
        self.assertEqual(
            c.post(URL, self._qline(needed_manager_fix=True), format='json').status_code, 400)
        # justification too thin — blocked
        self.assertEqual(
            c.post(URL, self._qline(justification='did well'), format='json').status_code, 400)
        # nothing was created
        self.assertEqual(IncentiveRequest.objects.count(), 0)

    def test_earned_gate_allows_qualified(self):
        r = self._client(self.maker_user).post(URL, self._qline(), format='json')
        self.assertEqual(r.status_code, 201, r.content)
        line = r.json()['lines'][0]
        self.assertTrue(line['beyond_normal_duties'])
        self.assertFalse(line['needed_manager_fix'])
        self.assertTrue(line['justification'])

    # ── employee discipline gate + manager declaration (CFO 2026-08-18) ──────
    def _emp_with_overdue_task(self, num='E300', name='Lemo Overdue',
                               email='lemo@alphadirect.co.bw', days=5):
        """An employee WITH a login carrying a task `days` overdue."""
        u = User.objects.create_user(email.split('@')[0], email, 'x')
        emp = Employee.objects.create(
            employee_number=num, full_name=name, job_title='Agent',
            department='Claims', email=email, company=self.company,
            user=u, status='active')
        OmniTask.objects.create(
            assigner=self.maker_user, assignee=u, title='Clear the backlog',
            due_at=timezone.localdate() - datetime.timedelta(days=days),
            status=OmniTask.Status.PENDING)
        return emp

    def test_manager_declaration_required(self):
        # No tick → refused, even for a perfectly qualified line.
        payload = self._qline()
        payload.pop('manager_attested')
        r = self._client(self.maker_user).post(URL, payload, format='json')
        self.assertEqual(r.status_code, 400)
        self.assertEqual(IncentiveRequest.objects.count(), 0)

    def test_employee_with_overdue_tasks_is_held_others_go_through(self):
        overdue = self._emp_with_overdue_task()
        clean = Employee.objects.create(
            employee_number='E301', full_name='Clean Cleo', job_title='Agent',
            department='Claims', company=self.company, status='active')
        r = self._client(self.maker_user).post(URL, {
            'title': 'Claims', 'period': '2026-06', 'manager_attested': True,
            'lines': [
                {'name': overdue.full_name, 'employee_id': str(overdue.id),
                 'amount': '1000', **QUALIFIED},
                {'name': clean.full_name, 'employee_id': str(clean.id),
                 'amount': '900', **QUALIFIED},
            ]}, format='json')
        self.assertEqual(r.status_code, 201, r.content)
        names = [l['name'] for l in r.json()['lines']]
        self.assertIn('Clean Cleo', names)
        self.assertNotIn('Lemo Overdue', names,
                         'an employee with overdue tasks must be held out')
        self.assertIn('Lemo Overdue', r.json().get('held_notice', ''))

    def test_all_held_when_the_only_employee_is_overdue(self):
        emp = self._emp_with_overdue_task()
        r = self._client(self.maker_user).post(URL, {
            'title': 'Claims', 'period': '2026-06', 'manager_attested': True,
            'lines': [{'name': emp.full_name, 'employee_id': str(emp.id),
                       'amount': '1000', **QUALIFIED}]}, format='json')
        self.assertEqual(r.status_code, 400)
        self.assertIn('overdue', r.json()['detail'].lower())
        self.assertEqual(IncentiveRequest.objects.count(), 0)

    def test_approver_with_overdue_tasks_can_still_approve(self):
        # THE UNAMI FIX (CFO 2026-08-18): the APPROVER's own overdue tasks must
        # never block them from signing — the check is on the employee, not the
        # approver. Give Unami overdue work, then have him approve.
        OmniTask.objects.create(
            assigner=self.maker_user, assignee=self.unami, title='HR backlog',
            due_at=timezone.localdate() - datetime.timedelta(days=9),
            status=OmniTask.Status.PENDING)
        rid = self._submit().json()['id']
        self.assertEqual(self._client(self.cfo).post(
            f'{URL}{rid}/approve/', {'slot': 'cfo'}, format='json').status_code, 200)
        r = self._client(self.unami).post(
            f'{URL}{rid}/approve/', {'slot': 'hr'}, format='json')
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(r.json()['status'], 'approved')

    def test_justification_word_boundary_is_50(self):
        # L2 guard (Fable 5, 2026-08-17): pin the CFO-directed 50-word minimum so
        # a silent revert to 15 can't pass the suite. One word short is rejected;
        # exactly 50 words is accepted. Only the word count varies here — the line
        # is otherwise fully qualified.
        from hris.incentive_service import MIN_JUSTIFICATION_WORDS
        self.assertEqual(MIN_JUSTIFICATION_WORDS, 50)
        c = self._client(self.maker_user)
        forty_nine = ' '.join(['work'] * 49)
        self.assertEqual(
            c.post(URL, self._qline(justification=forty_nine), format='json').status_code, 400)
        fifty = ' '.join(['work'] * 50)
        self.assertEqual(
            c.post(URL, self._qline(justification=fifty), format='json').status_code, 201)

    def test_cfo_and_hr_signoff_approves(self):
        # CFO directive 2026-07-23 (supersedes 2026-07-15): an incentive needs
        # BOTH the CFO and HR (Unami) signature before it is approved — one
        # signature alone keeps it pending. See IncentiveRequest.fully_signed.
        rid = self._submit().json()['id']

        # CFO signs first — one leg only, so still pending.
        r = self._client(self.cfo).post(f'{URL}{rid}/approve/', {}, format='json')
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(r.json()['status'], 'pending')
        self.assertTrue(r.json()['signatures']['cfo']['signed'])
        self.assertFalse(r.json()['signatures']['hr']['signed'])

        # HR (Unami) signs the second leg — now approved.
        r = self._client(self.unami).post(f'{URL}{rid}/approve/', {}, format='json')
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(r.json()['status'], 'approved')
        self.assertTrue(r.json()['signatures']['hr']['signed'])

        # Fully approved — a further sign-off is refused.
        r = self._client(self.cfo).post(f'{URL}{rid}/approve/', {}, format='json')
        self.assertEqual(r.status_code, 400)

    def test_maker_cannot_approve(self):
        rid = self._submit().json()['id']
        r = self._client(self.maker_user).post(
            f'{URL}{rid}/approve/', {}, format='json')
        self.assertEqual(r.status_code, 400)

    def test_reject(self):
        rid = self._submit().json()['id']
        r = self._client(self.unami).post(
            f'{URL}{rid}/reject/', {'notes': 'Not in budget.'}, format='json')
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(r.json()['status'], 'rejected')
        self.assertEqual(r.json()['rejected']['notes'], 'Not in budget.')

    def test_finance_marks_processed_only_after_approval(self):
        rid = self._submit().json()['id']

        # Not approved yet — Finance blocked.
        r = self._client(self.pako).post(
            f'{URL}{rid}/mark-processed/', {}, format='json')
        self.assertEqual(r.status_code, 400)

        self._client(self.cfo).post(f'{URL}{rid}/approve/', {}, format='json')
        self._client(self.unami).post(f'{URL}{rid}/approve/', {}, format='json')

        # Maker cannot mark processed.
        r = self._client(self.maker_user).post(
            f'{URL}{rid}/mark-processed/', {}, format='json')
        self.assertEqual(r.status_code, 400)

        # Finance can.
        r = self._client(self.pako).post(
            f'{URL}{rid}/mark-processed/', {}, format='json')
        self.assertEqual(r.status_code, 200, r.content)
        self.assertTrue(r.json()['payroll']['processed'])

        req = IncentiveRequest.objects.get(pk=rid)
        self.assertTrue(req.payroll_processed)


class IncentiveApprovalPageMotivationTest(APITestCase):
    """The login-free approval page must SHOW the motivation the approver is
    signing off on — not just name/amount (CFO 2026-08-22, PR #701). Regression
    guard for hris.incentive_actions._motiv_row / _lines_rows / _summary_rows.
    """

    def _page_html(self, *, justification, needed_fix=False, attested=True,
                   beyond=True, on_time=True, error_free=True):
        from django.test import RequestFactory
        from django.contrib.auth.models import User
        from hris.incentive_models import IncentiveRequest, IncentiveLine
        from hris.incentive_actions import (
            incentive_action_page, make_incentive_action_token)

        req = IncentiveRequest.objects.create(
            title='Monthly performance', period='2026-08', department='Health',
            maker_email='mtlagae@alphadirect.co.bw', manager_attested=attested,
            status=IncentiveRequest.Status.PENDING)
        IncentiveLine.objects.create(
            request=req, name='Keneilwe Jane', amount='1000.00',
            beyond_normal_duties=beyond, on_time=on_time, error_free=error_free,
            needed_manager_fix=needed_fix, justification=justification)
        # A superuser is an accepted approver (slot_for may be None) — the page
        # renders the approve form for them.
        approver = User.objects.create_superuser('root', 'root@example.com', 'x')
        req.refresh_from_db()
        token = make_incentive_action_token(req, approver)
        resp = incentive_action_page(RequestFactory().get('/'), token)
        self.assertEqual(resp.status_code, 200)
        return resp.content.decode()

    def test_motivation_and_ticks_render(self):
        html = self._page_html(
            justification='Worked overnight onboarding groups; far beyond duties.')
        self.assertIn('Why:', html)
        self.assertIn('Worked overnight onboarding groups', html)
        self.assertIn('Beyond normal duties', html)
        self.assertIn('On time', html)
        self.assertIn('Error-free', html)
        self.assertIn('Manager attested', html)

    def test_justification_is_html_escaped(self):
        html = self._page_html(justification='<script>alert(1)</script> bonus')
        self.assertNotIn('<script>alert(1)</script>', html)
        self.assertIn('&lt;script&gt;', html)

    def test_blank_motivation_is_called_out(self):
        html = self._page_html(justification='   ')
        self.assertIn('No motivation was written', html)

    def test_needed_manager_fix_warns(self):
        html = self._page_html(
            justification='Did the thing.', needed_fix=True)
        self.assertIn('Needed a manager fix', html)

    def test_manager_not_attested_shows_no(self):
        html = self._page_html(justification='Did the thing.', attested=False)
        self.assertIn('Manager attested', html)
        # the 'no' pill class must be present when not attested
        self.assertIn('pill-no', html)
