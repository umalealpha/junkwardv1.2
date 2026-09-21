"""core/tests/test_qc_bot_scope.py — the Omni QC agent's scoped 'qc-bot' key.

CFO 2026-09-07 (ideas #6 + #8): the read-only QC agent on the CFO's Mac may
(a) file ONE bug report per broken page with its single page capture, and
(b) read who still owes monthly feedback so it can nudge managers on Telegram.
Nothing else. Every test here fails if the feature is reverted:
  * without the scope + 1-shot rule, the QC create is 401/400;
  * without the route, the owed read is 404.
"""
from django.contrib.auth.hashers import make_password
from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from rest_framework.test import APIClient, APITestCase

from core.models import ApiKey, Company
from hris.models import HRISProfile
from payroll.models import Employee

PNG = (b'\x89PNG\r\n\x1a\n' + b'\x00' * 64)
FIFTY_WORDS = ('[QC] dashboard — blank ' + 'word ' * 60).strip()


def _key(user, scopes, seed):
    plaintext = seed + '0' * (64 - len(seed))
    ApiKey.objects.create(
        label=f'{seed} test', key_prefix=plaintext[:12],
        key_hash=make_password(plaintext), service_user=user,
        allowed_scopes=scopes, is_active=True)
    return plaintext


class QcBotBugFilingTests(APITestCase):
    def setUp(self):
        self.svc = User.objects.create_user('svc-qc', email='omni-qc-bot@alphadirect.co.bw', password='x')
        self.bot_key = _key(self.svc, ['qc-bot'], 'qcbot')
        self.manus_key = _key(self.svc, ['qc-manus'], 'qcmanus')
        self.staff = User.objects.create_user('qc-staff', email='qcstaff@alphadirect.co.bw', password='x')

    def _post(self, auth, shots=1):
        c = APIClient()
        data = {'description': FIFTY_WORDS, 'page_url': 'https://omni.alphadirect.co.bw/dashboard',
                'screenshots': [SimpleUploadedFile(f's{i}.png', PNG, content_type='image/png') for i in range(shots)]}
        return c.post('/api/v1/bug-reports/', data, format='multipart', **auth)

    def test_qc_bot_files_with_one_screenshot(self):
        r = self._post({'HTTP_AUTHORIZATION': f'ApiKey {self.bot_key}'}, shots=1)
        self.assertIn(r.status_code, (200, 201), r.content)

    def test_manus_key_still_cannot_create(self):
        r = self._post({'HTTP_AUTHORIZATION': f'ApiKey {self.manus_key}'}, shots=1)
        self.assertEqual(r.status_code, 403, r.content)

    def test_humans_still_need_two_screenshots(self):
        c = APIClient(); c.force_authenticate(self.staff)
        data = {'description': FIFTY_WORDS,
                'screenshots': [SimpleUploadedFile('s.png', PNG, content_type='image/png')]}
        r = c.post('/api/v1/bug-reports/', data, format='multipart')
        self.assertEqual(r.status_code, 400, r.content)
        self.assertIn('screenshots', r.json().get('errors', {}))


class OwedFeedbackReadTests(APITestCase):
    def setUp(self):
        self.co = Company.objects.create(code='ADIA', name='ADIC (test)')
        self.mgr_user = User.objects.create(username='medu', email='mtlagae@alphadirect.co.bw')
        self.mgr = Employee.objects.create(employee_number='M1', full_name='Medu T', company=self.co,
                                           user=self.mgr_user, email='mtlagae@alphadirect.co.bw')
        rep_emp = Employee.objects.create(employee_number='R1', full_name='Gosego Makone', company=self.co)
        HRISProfile.objects.create(employee=rep_emp, manager=self.mgr)
        svc = User.objects.create_user('svc-qc2', email='omni-qc-bot2@alphadirect.co.bw', password='x')
        self.bot_key = _key(svc, ['qc-bot'], 'qcbotowed')
        self.staff = User.objects.create_user('plain', email='plain@alphadirect.co.bw', password='x')

    def test_qc_bot_reads_owed_managers_with_tappable_urls(self):
        r = APIClient().get('/hris/api/performance/monthly/owed/',
                            HTTP_AUTHORIZATION=f'ApiKey {self.bot_key}')
        self.assertEqual(r.status_code, 200, r.content)
        managers = {m['email']: m for m in r.json()['managers']}
        self.assertIn('mtlagae@alphadirect.co.bw', managers)
        m = managers['mtlagae@alphadirect.co.bw']
        self.assertEqual([p['name'] for p in m['owed']], ['Gosego Makone'])
        self.assertIn('/hris/api/manager-feedback/', m['owed'][0]['url'])
        self.assertIn('?profile=', m['owed'][0]['url'])

    def test_plain_staff_cannot_read_owed(self):
        c = APIClient(); c.force_authenticate(self.staff)
        r = c.get('/hris/api/performance/monthly/owed/')
        self.assertEqual(r.status_code, 403, r.content)
