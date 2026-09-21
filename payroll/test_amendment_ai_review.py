"""payroll/test_amendment_ai_review.py — the AI Review (Aria) step must call
DeepSeek with the RIGHT arguments.

Regression for the bug in the screenshot (Pako, 2026-07-23): ai_review_period
called deepseek_complete(system=…, user=…, temperature=…) — none of which are
real parameters — so it always raised
    deepseek_complete() missing 1 required positional argument: 'user_prompt'
and the narrative was never produced. The correct call is
deepseek_complete(<prompt>, system_prompt=…, max_tokens=…).

Run in CI (needs a DB): manage.py test payroll.test_amendment_ai_review
"""
from unittest.mock import patch

from django.contrib.auth.models import User
from django.urls import reverse
from rest_framework.test import APITestCase

from payroll.models import PayrollPeriod


class AmendmentAIReviewTests(APITestCase):
    def setUp(self):
        # A superuser passes the payroll-view gate.
        self.boss = User.objects.create_superuser('root', 'root@alphadirect.co.bw', 'x')
        self.period = PayrollPeriod.objects.create(
            period_name='2026-06', start_date='2026-06-01', end_date='2026-06-30')
        self.url = reverse('v1-payroll-ai-review', args=[self.period.id])

    def test_ai_review_calls_deepseek_with_correct_args(self):
        self.client.force_authenticate(self.boss)
        # The view does `from core.ai_assist import deepseek_complete` at call
        # time, so patching the module attribute captures the real call.
        with patch('core.ai_assist.deepseek_complete', return_value='REVIEW OK') as mock:
            r = self.client.get(self.url)
        self.assertEqual(r.status_code, 200, r.content)
        body = r.json()
        # The narrative came through and no error was surfaced.
        self.assertEqual(body['ai_narrative'], 'REVIEW OK')
        self.assertEqual(body.get('ai_unavailable_reason', ''), '')
        # Called correctly: the prompt is the FIRST POSITIONAL arg (user_prompt),
        # system_prompt is a kwarg, and the dead 'system'/'user'/'temperature'
        # kwargs are gone.
        self.assertEqual(mock.call_count, 1)
        args, kwargs = mock.call_args
        self.assertTrue(args and isinstance(args[0], str) and args[0])
        self.assertIn('system_prompt', kwargs)
        for dead in ('system', 'user', 'temperature'):
            self.assertNotIn(dead, kwargs)
