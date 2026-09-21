"""The CFO's money-by-email tap must reach the server, not just the email.

/fabe 2026-09-13, the blocker. `is_decidable_task` grew an `actor` argument and
the brief RENDERS its buttons with `actor=ACTOR`, but the view that handles the
CLICK still called it without one. The default falls back to the CEO handle,
which is not in MAY_DECIDE_MONEY_BY_EMAIL, so the CFO saw Approve on his own
payment task, tapped it, and was told "this one has to be done in Omni".

It failed CLOSED, so nothing unsafe happened — the feature was simply dead, and
the original tests missed it because they tested the predicate, not the HTTP
path. That is the lesson: a gate function that gains an argument must be
regression-tested THROUGH THE VIEW.

Run: manage.py test core.tests.test_money_by_email_click
"""
from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from core.ceo_monitor_views import make_decision_token
from core.models import OmniTask


class TheCfoCanActuallyTapApproveOnAPaymentTask(TestCase):

    def setUp(self):
        self.cfo = User.objects.create_user(
            'pganesharajah', 'pganesharajah@alphadirect.co.bw', 'x',
            first_name='Prathap', last_name='Ganesharajah', is_staff=True)
        self.task = OmniTask.objects.create(
            title='Pay Jere Attorneys', source='payment_request',
            assignee=self.cfo, assigner=self.cfo, status=OmniTask.Status.PENDING)

    def _url(self, actor):
        tok = make_decision_token(task_id=self.task.pk, action='approve', actor=actor)
        return reverse('ceo-monitor-decide') + f'?t={tok}'

    def test_the_confirm_page_opens_for_the_cfo(self):
        r = self.client.get(self._url('pganesharajah'))
        self.assertEqual(
            r.status_code, 200,
            'the CFO tapped Approve on a payment task and the server refused — '
            'the click path is not passing the actor through')
        self.assertNotIn(b'has to be done in Omni', r.content)

    def test_it_still_refuses_for_someone_who_is_not_the_cfo(self):
        """The gate is not removed, only wired up. Anyone else is still sent
        into Omni for a money decision."""
        r = self.client.get(self._url('someoneelse'))
        self.assertEqual(r.status_code, 400)
