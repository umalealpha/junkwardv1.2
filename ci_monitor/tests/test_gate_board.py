"""The gate board, and the two things that must not break: who can write to it,
and whether its countdown can lie.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import hmac
import json

from django.test import Client, TestCase, override_settings
from django.utils import timezone

from ci_monitor.models import CiJob, CiRun, eta_seconds, typical_seconds

SECRET = 'test-secret-not-a-real-one'
URL = '/api/v1/ci/webhook/'


def sign(body: bytes, secret: str = SECRET) -> str:
    return 'sha256=' + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def run_payload(run_id=1, status='in_progress', conclusion=None, branch='main'):
    return {'workflow_run': {
        'id': run_id, 'run_number': 7, 'name': 'CI — guardrails',
        'head_branch': branch, 'head_sha': 'a' * 40,
        'head_commit': {'message': 'fix(ledger): the thing\n\nbody'},
        'actor': {'login': 'Prathap-Alpha'}, 'event': 'push',
        'html_url': 'https://github.com/x/y/actions/runs/1',
        'status': status, 'conclusion': conclusion,
        'run_started_at': '2026-09-09T10:00:00Z',
        'updated_at': '2026-09-09T10:09:00Z',
    }}


@override_settings(CI_WEBHOOK_SECRET=SECRET)
class WebhookAuthTests(TestCase):
    """The board is only worth reading if only GitHub can write to it."""

    def setUp(self):
        self.client = Client()

    def _post(self, body: bytes, signature: str | None, event='workflow_run'):
        headers = {'HTTP_X_GITHUB_EVENT': event}
        if signature is not None:
            headers['HTTP_X_HUB_SIGNATURE_256'] = signature
        return self.client.post(URL, data=body,
                                content_type='application/json', **headers)

    def test_a_correctly_signed_delivery_is_accepted(self):
        body = json.dumps(run_payload()).encode()
        self.assertEqual(self._post(body, sign(body)).status_code, 200)
        self.assertTrue(CiRun.objects.filter(run_id=1).exists())

    def test_an_unsigned_delivery_is_refused(self):
        body = json.dumps(run_payload()).encode()
        self.assertEqual(self._post(body, None).status_code, 403)
        self.assertFalse(CiRun.objects.exists())

    def test_a_wrongly_signed_delivery_is_refused(self):
        body = json.dumps(run_payload()).encode()
        self.assertEqual(self._post(body, sign(body, 'the-wrong-secret')).status_code, 403)
        self.assertFalse(CiRun.objects.exists())

    def test_a_tampered_body_is_refused(self):
        body = json.dumps(run_payload()).encode()
        signature = sign(body)
        tampered = json.dumps(run_payload(branch='attacker-branch')).encode()
        self.assertEqual(self._post(tampered, signature).status_code, 403)
        self.assertFalse(CiRun.objects.exists())

    @override_settings(CI_WEBHOOK_SECRET='')
    def test_no_secret_configured_refuses_everything(self):
        """Fails CLOSED. An unset secret must never mean 'let everyone in' —
        a build board the internet can write to is a board that lies."""
        body = json.dumps(run_payload()).encode()
        self.assertEqual(self._post(body, sign(body)).status_code, 403)
        self.assertFalse(CiRun.objects.exists())

    def test_an_unknown_event_is_accepted_and_ignored(self):
        """Adding an event on GitHub must never turn into failing deliveries."""
        body = json.dumps({'zen': 'hello'}).encode()
        self.assertEqual(self._post(body, sign(body), event='ping').status_code, 200)
        self.assertFalse(CiRun.objects.exists())


@override_settings(CI_WEBHOOK_SECRET=SECRET)
class WebhookRecordingTests(TestCase):
    def setUp(self):
        self.client = Client()

    def _send(self, payload, event='workflow_run'):
        body = json.dumps(payload).encode()
        return self.client.post(URL, data=body, content_type='application/json',
                                HTTP_X_GITHUB_EVENT=event,
                                HTTP_X_HUB_SIGNATURE_256=sign(body))

    def test_the_same_run_twice_is_one_row(self):
        self._send(run_payload(status='in_progress'))
        self._send(run_payload(status='completed', conclusion='success'))
        self.assertEqual(CiRun.objects.count(), 1)
        self.assertEqual(CiRun.objects.get().status, CiRun.Status.SUCCESS)

    def test_it_shows_the_commit_subject_not_the_workflow_name(self):
        """'CI — guardrails' tells him nothing about WHICH work is building."""
        self._send(run_payload())
        self.assertEqual(CiRun.objects.get().title, 'fix(ledger): the thing')

    def test_a_job_arriving_before_its_run_is_kept(self):
        """GitHub does not promise an order. Dropping the job would lose the
        only thing a countdown can be built from."""
        self._send({'workflow_job': {
            'id': 55, 'run_id': 99, 'name': 'shard (1)', 'status': 'in_progress',
            'started_at': '2026-09-09T10:00:00Z', 'head_branch': 'feat/x',
        }}, event='workflow_job')
        self.assertTrue(CiJob.objects.filter(job_id=55).exists())
        self.assertEqual(CiJob.objects.get().run.run_id, 99)

    def test_a_cancelled_run_is_not_recorded_as_passed(self):
        self._send(run_payload(status='completed', conclusion='cancelled'))
        self.assertEqual(CiRun.objects.get().status, CiRun.Status.CANCELLED)


class EtaTests(TestCase):
    """A countdown that can lie is worse than no countdown."""

    def _finished(self, name, seconds, status=CiRun.Status.SUCCESS, run_id=None):
        run = CiRun.objects.create(run_id=run_id or CiRun.objects.count() + 1000,
                                   branch='main')
        start = timezone.now() - dt.timedelta(hours=1)
        return CiJob.objects.create(
            run=run, job_id=CiJob.objects.count() + 1, name=name, status=status,
            started_at=start, ended_at=start + dt.timedelta(seconds=seconds))

    def test_no_estimate_until_there_is_enough_history(self):
        """Two runs is not a pattern. Saying nothing is honest; inventing a
        number is not."""
        self._finished('shard (1)', 300)
        self._finished('shard (1)', 320)
        self.assertIsNone(typical_seconds('shard (1)'))

    def test_it_uses_the_median_so_one_stalled_run_cannot_skew_it(self):
        for secs in (300, 310, 320, 2400):     # one run stuck on a queued runner
            self._finished('shard (1)', secs)
        self.assertLess(typical_seconds('shard (1)'), 400)

    def test_failed_runs_are_ignored(self):
        """A job that failed in 90s finished early because it did NOT do the
        work. Counting those makes the gate look faster the more it breaks."""
        for secs in (300, 310, 320):
            self._finished('shard (2)', secs)
        for _ in range(6):
            self._finished('shard (2)', 20, status=CiRun.Status.FAILURE)
        self.assertGreater(typical_seconds('shard (2)'), 200)

    def test_a_finished_job_has_no_countdown(self):
        job = self._finished('shard (3)', 300)
        self.assertIsNone(eta_seconds(job))

    def test_the_countdown_never_goes_negative(self):
        """An overrunning job says 0 left, not '-4 minutes'."""
        for secs in (300, 310, 320):
            self._finished('shard (4)', secs)
        run = CiRun.objects.create(run_id=7777, branch='main')
        slow = CiJob.objects.create(
            run=run, job_id=999, name='shard (4)', status=CiRun.Status.RUNNING,
            started_at=timezone.now() - dt.timedelta(seconds=9999))
        self.assertEqual(eta_seconds(slow), 0)
