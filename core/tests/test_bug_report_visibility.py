"""Bug eb32a0ba: reports vanished from the reporter's list when SSO
re-provisioning recreated their Django User row (new id, same email).
The list now matches on reporter FK OR reporter_email."""
from django.contrib.auth.models import User
from rest_framework.test import APIClient, APITestCase

from core.models import BugReport


class BugReportVisibilityTest(APITestCase):
    def test_reports_match_by_email_after_user_reprovision(self):
        old_user = User.objects.create_user(
            'kago.old', email='ktshutlhedi@alphadirect.co.bw', password='x')
        BugReport.objects.create(
            reporter=old_user, reporter_email='ktshutlhedi@alphadirect.co.bw',
            description='Original report before re-provision', word_count=60)
        # SSO recreates the account: new User row, same mailbox.
        old_user.delete()
        new_user = User.objects.create_user(
            'kago.new', email='KTshutlhedi@alphadirect.co.bw', password='x')

        c = APIClient()
        c.force_authenticate(user=new_user)
        r = c.get('/api/v1/bug-reports/')
        self.assertEqual(r.status_code, 200)
        descs = [x['description'] for x in r.json()['results']]
        self.assertTrue(any('Original report' in d for d in descs),
                        f'orphaned report not visible: {descs}')

    def test_all_reports_visible_to_everyone(self):
        # CFO directive 2026-06-15: the bug list is visible to all staff.
        # A non-triager now sees other users' reports too.
        alice = User.objects.create_user('alice', email='a@alphadirect.co.bw', password='x')
        BugReport.objects.create(
            reporter=alice, reporter_email='a@alphadirect.co.bw',
            description='Alice report visible to all', word_count=60)
        bob = User.objects.create_user('bob', email='b@alphadirect.co.bw', password='x')
        c = APIClient()
        c.force_authenticate(user=bob)
        r = c.get('/api/v1/bug-reports/')
        self.assertEqual(r.status_code, 200)
        descs = [x['description'] for x in r.json()['results']]
        self.assertTrue(any('Alice report' in d for d in descs),
                        f"non-triager should see all reports now: {descs}")
        self.assertFalse(r.json()['is_triager'])

    def test_non_triager_cannot_change_status(self):
        # Visibility is open, but editing a status stays triager-only.
        alice = User.objects.create_user('alice2', email='a2@alphadirect.co.bw', password='x')
        rep = BugReport.objects.create(
            reporter=alice, reporter_email='a2@alphadirect.co.bw',
            description='Status guard report', word_count=60)
        bob = User.objects.create_user('bob2', email='b2@alphadirect.co.bw', password='x')
        c = APIClient()
        c.force_authenticate(user=bob)
        r = c.patch(f'/api/v1/bug-reports/{rep.id}/', {'status': 'resolved'}, format='json')
        self.assertEqual(r.status_code, 403)
