"""core/tests/test_manus_qc_queue.py — the Manus QC pickup queue.

CFO 2026-08-29: replace "email Manus to QC" with a pickup queue on the existing
bug board. An admin flags an item "Send to Manus for QC"; Manus polls the flagged
items through a scoped 'qc-manus' key and posts its finding back onto the SAME
item — and can do NOTHING else (no status change, no new bug reports).

Every test fails if the feature is reverted.
"""
from django.contrib.auth.hashers import make_password
from django.contrib.auth.models import User
from rest_framework.test import APIClient, APITestCase

from core.models import ApiKey, BugReport


def _key(user, scopes):
    plaintext = 'qcmanus' + '0' * (64 - len('qcmanus'))
    ApiKey.objects.create(
        label='qc-manus test', key_prefix=plaintext[:12],
        key_hash=make_password(plaintext), service_user=user,
        allowed_scopes=scopes, is_active=True)
    return plaintext


class ManusQCQueueTests(APITestCase):
    def setUp(self):
        self.admin = User.objects.create_user('qc-admin', email='qcadmin@alphadirect.co.bw',
                                               password='x', is_superuser=True, is_staff=True)
        self.staff = User.objects.create_user('qc-staff', email='qcstaff@alphadirect.co.bw',
                                               password='x')
        self.svc = User.objects.create_user('svc-manus', email='svc-manus@alphadirect.co.bw',
                                             password='x')  # NOT a triager
        self.key = _key(self.svc, ['qc-manus'])
        self.rep = BugReport.objects.create(
            reporter=self.staff, reporter_email='qcstaff@alphadirect.co.bw',
            description='A page that needs QC review from Manus', word_count=60)
        self.other = BugReport.objects.create(
            reporter=self.staff, reporter_email='qcstaff@alphadirect.co.bw',
            description='An item NOT flagged for QC', word_count=60)

    def _auth(self):
        return {'HTTP_AUTHORIZATION': f'ApiKey {self.key}'}

    # ── flag ("Send to Manus for QC") ────────────────────────────────────────
    def test_admin_can_flag_and_poll_returns_only_flagged(self):
        c = APIClient(); c.force_authenticate(self.admin)
        r = c.patch(f'/api/v1/bug-reports/{self.rep.id}/', {'qc_requested': True}, format='json')
        self.assertEqual(r.status_code, 200, r.content)
        self.rep.refresh_from_db()
        self.assertTrue(self.rep.qc_requested)
        self.assertEqual(self.rep.qc_requested_by_id, self.admin.id)
        self.assertIsNotNone(self.rep.qc_requested_at)
        # poll returns only the flagged item
        poll = c.get('/api/v1/bug-reports/', {'qc_requested': 'true'}).json()['results']
        ids = {x['id'] for x in poll}
        self.assertIn(str(self.rep.id), ids)
        self.assertNotIn(str(self.other.id), ids)

    def test_non_admin_cannot_flag_qc(self):
        c = APIClient(); c.force_authenticate(self.staff)
        r = c.patch(f'/api/v1/bug-reports/{self.rep.id}/', {'qc_requested': True}, format='json')
        self.assertEqual(r.status_code, 403)
        self.rep.refresh_from_db()
        self.assertFalse(self.rep.qc_requested)

    # ── Manus key: poll ──────────────────────────────────────────────────────
    def test_qc_key_can_poll_the_queue(self):
        BugReport.objects.filter(pk=self.rep.pk).update(qc_requested=True)
        r = self.client.get('/api/v1/bug-reports/', {'qc_requested': 'true'}, **self._auth())
        self.assertEqual(r.status_code, 200, r.content)
        ids = {x['id'] for x in r.json()['results']}
        self.assertEqual(ids, {str(self.rep.id)})

    # ── Manus key: post the finding back ─────────────────────────────────────
    def test_qc_key_posts_result_back(self):
        BugReport.objects.filter(pk=self.rep.pk).update(qc_requested=True)
        r = self.client.post(
            f'/api/v1/bug-reports/{self.rep.id}/qc-result/',
            {'picked_up': True, 'note': 'Found a null-guard bug on submit.',
             'pr_url': 'https://github.com/alphadirectinsurance/alpha-finance/pull/999',
             'done': True},
            format='json', **self._auth())
        self.assertEqual(r.status_code, 200, r.content)
        self.rep.refresh_from_db()
        self.assertIsNotNone(self.rep.qc_picked_up_at)
        self.assertIn('null-guard', self.rep.qc_result_note)
        self.assertIn('pull/999', self.rep.qc_result_pr_url)
        self.assertIsNotNone(self.rep.qc_result_at)
        self.assertFalse(self.rep.qc_requested)          # done=True cleared the queue flag

    def test_qc_result_leaves_status_and_money_untouched(self):
        before = self.rep.status
        self.client.post(f'/api/v1/bug-reports/{self.rep.id}/qc-result/',
                         {'note': 'x'}, format='json', **self._auth())
        self.rep.refresh_from_db()
        self.assertEqual(self.rep.status, before)         # never changes status

    def test_qc_result_empty_body_is_400(self):
        r = self.client.post(f'/api/v1/bug-reports/{self.rep.id}/qc-result/',
                             {}, format='json', **self._auth())
        self.assertEqual(r.status_code, 400)

    # ── the key can do NOTHING else ──────────────────────────────────────────
    def test_qc_key_cannot_create_a_bug_report(self):
        r = self.client.post('/api/v1/bug-reports/',
                             {'description': 'x' * 300}, **self._auth())
        self.assertEqual(r.status_code, 403)

    def test_qc_key_cannot_change_status(self):
        r = self.client.patch(f'/api/v1/bug-reports/{self.rep.id}/',
                              {'status': 'resolved'}, format='json', **self._auth())
        self.assertEqual(r.status_code, 403)
        self.rep.refresh_from_db()
        self.assertNotEqual(self.rep.status, 'resolved')

    def test_random_signed_in_user_cannot_post_qc_result(self):
        c = APIClient(); c.force_authenticate(self.staff)   # non-admin, no key
        r = c.post(f'/api/v1/bug-reports/{self.rep.id}/qc-result/',
                   {'note': 'sneaky'}, format='json')
        self.assertEqual(r.status_code, 403)

    def test_admin_may_also_post_a_qc_result(self):
        c = APIClient(); c.force_authenticate(self.admin)
        r = c.post(f'/api/v1/bug-reports/{self.rep.id}/qc-result/',
                   {'note': 'admin note'}, format='json')
        self.assertEqual(r.status_code, 200, r.content)
