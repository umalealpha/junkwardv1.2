"""Request-level cover for the collections CSV export (OpenAI judge K4).

The export now carries client NAMES (CFO decision 2026-09-08), so two things
must hold at the request layer, not just in the helpers:
  * a signed-in staff member WITHOUT the finance permission cannot download it;
  * the aggregate screen stays open to any signed-in staff member, which is what
    bug 6d76ec6c asked for.
"""
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient


def _dash_url():
    from django.urls import reverse
    return reverse('v1-realpay-collections-dashboard')


class CollectionsExportPermissionTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.plain = User.objects.create_user('plainstaff', password='x')
        self.client_api = APIClient()

    def _no_graphite(self):
        # Force the uploaded-row path so the test never reaches the replica.
        return patch('realpay.graphite_feed.collections_by_group',
                     return_value={'configured': False})

    def test_export_is_refused_without_the_finance_permission(self):
        self.client_api.force_authenticate(self.plain)
        with self._no_graphite():
            r = self.client_api.get(_dash_url(), {'export': '1'})
        self.assertEqual(
            r.status_code, 403,
            'the CSV carries client names — a bare login must not be able to pull it')

    def test_screen_stays_open_to_any_signed_in_staff_member(self):
        self.client_api.force_authenticate(self.plain)
        with self._no_graphite():
            r = self.client_api.get(_dash_url())
        self.assertEqual(r.status_code, 200,
                         'the aggregate screen is not gated (bug 6d76ec6c)')

    def test_export_is_allowed_for_a_superuser_and_carries_the_new_columns(self):
        User = get_user_model()
        boss = User.objects.create_superuser('bossy', 'b@example.com', 'x')
        self.client_api.force_authenticate(boss)
        with self._no_graphite():
            r = self.client_api.get(_dash_url(), {'export': '1'})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r['Content-Type'], 'text/csv')
        header = r.content.decode().splitlines()[0]
        for col in ('Client Number', 'Client Name', 'Product Code',
                    'Grouping', 'Tracking Start Date', 'Collected Amount'):
            self.assertIn(col, header)
        self.assertIn('ROWS EXPORTED', r.content.decode())


class TimeDoctorApproverEmailWordingTests(TestCase):
    """An auto-raised deduction must not tell the approver the employee applied."""

    def test_td_deduction_text_says_omni_raised_it(self):
        from core import notifications

        class _LT:
            code = 'td_deduct'

        class _Emp:
            full_name = 'A Person'

        class _Prof:
            employee = _Emp()

        class _LR:
            leave_type = _LT()
            leave_type_id = 1
            profile = _Prof()
            # The marker keys on the enforce job's own stamp, not the type —
            # a self-applied td_deduct must NOT be described as auto-raised.
            reason = ('Auto-applied: no Time Doctor tracking and no explanation '
                      'by the 4:00 pm cut-off, Tue 02 Sep 2026.')

            def day_breakdown(self):
                return '2 days'

        sent = {}

        def _capture(subject, html, to, text_fallback=None, cc=None, cc_cfo=True):
            sent['text'] = text_fallback

        with patch.object(notifications, '_enabled', return_value=True), \
             patch('hris.leave_email.build_leave_email',
                   return_value={'subject': 's', 'html': '<p></p>', 'to': ['m@x.co']}), \
             patch.object(notifications, 'send_html_with_cfo_cc', _capture):
            notifications.notify_leave_pending_approval(_LR())

        self.assertIn('did not apply', sent['text'])
        self.assertNotIn('has applied for leave', sent['text'])


class AutoRaisedMarkerTests(TestCase):
    """`td_deduct` is ALSO an employee self-service type (CFO 2026-08-25), so the
    'Omni raised this, you did not apply' marker must key on the enforce job's
    own 'Auto-applied:' stamp — never on the leave type alone."""

    def _lr(self, reason):
        class _LT:
            code = 'td_deduct'

        class _LR:
            leave_type = _LT()
            leave_type_id = 1

        lr = _LR()
        lr.reason = reason
        return lr

    def test_cron_raised_deduction_is_marked_auto(self):
        from hris.leave_email import is_auto_raised
        self.assertTrue(is_auto_raised(self._lr(
            'Auto-applied: no Time Doctor tracking and no explanation by the 4:00 pm cut-off.')))

    def test_self_applied_deduction_is_NOT_marked_auto(self):
        """Keying on the type alone would tell someone who applied for their own
        deduction that they did not apply for it."""
        from hris.leave_email import is_auto_raised
        self.assertFalse(is_auto_raised(self._lr('I forgot to track on Tuesday, please deduct.')))

    def test_a_normal_leave_type_is_never_marked_auto(self):
        from hris.leave_email import is_auto_raised

        class _LT:
            code = 'annual'

        class _LR:
            leave_type = _LT()
            leave_type_id = 1
            reason = 'Auto-applied: something'

        self.assertFalse(is_auto_raised(_LR()))
