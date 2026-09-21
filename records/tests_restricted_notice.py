"""Bug 49c9d8d3 — a register that withholds rows must SAY it withholds rows.

The Records officer's title opens the register but does not clear him for
restricted personal data. On prod that meant he saw 2 of 55 records and
reasonably concluded the log was broken. The access rule is deliberate; the
silence was not. These assertions fail before the `list()` override exists.

No real employee name appears here — the fixture references are invented.
"""
from __future__ import annotations

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIRequestFactory, force_authenticate

from core.models import Company, UserProfile
from records.api_views import RecordItemViewSet
from records.models import RecordCategory, RecordItem

User = get_user_model()


class RestrictedNoticeTests(TestCase):

    @classmethod
    def setUpTestData(cls):
        cls.company = Company.objects.create(code='RNT', name='Notice Co.')
        cls.cat = RecordCategory.objects.create(name='Files', retention_months=72)
        # 3 restricted + 1 internal — the prod shape in miniature.
        for i in range(3):
            RecordItem.objects.create(
                reference=f'RES-{i}', title='Personnel file', category=cls.cat,
                company=cls.company, current_location='Store',
                confidentiality=RecordItem.Confidentiality.RESTRICTED)
        RecordItem.objects.create(
            reference='INT-0', title='Policy copy', category=cls.cat,
            company=cls.company, current_location='Store',
            confidentiality=RecordItem.Confidentiality.INTERNAL)

    def _list_as(self, title):
        user = User.objects.create_user(
            f'u{title}', f'u{title}@alphadirect.co.bw', 'x')
        UserProfile.objects.update_or_create(user=user, defaults={'title': title})
        request = APIRequestFactory().get('/api/v1/records/')
        force_authenticate(request, user=user)
        return RecordItemViewSet.as_view({'get': 'list'})(request)

    def test_uncleared_role_is_told_rows_are_withheld(self):
        r = self._list_as(UserProfile.Title.ACCOUNTANT)
        self.assertEqual(r.status_code, 200)
        # still only the internal record — access is UNCHANGED
        self.assertEqual(len(r.data['results']), 1)
        # ...but the response now explains the gap
        self.assertEqual(r.data['restricted_hidden'], 3)
        self.assertIn('restricted', r.data['restricted_notice'].lower())
        self.assertIn('Human Capital', r.data['restricted_notice'])

    def test_a_cleared_role_sees_everything_and_gets_no_notice(self):
        r = self._list_as(UserProfile.Title.HR_MANAGER)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(len(r.data['results']), 4)
        self.assertNotIn('restricted_hidden', r.data)
        self.assertNotIn('restricted_notice', r.data)

    def test_the_notice_never_leaks_what_the_hidden_records_are(self):
        r = self._list_as(UserProfile.Title.ACCOUNTANT)
        blob = str(r.data)
        for i in range(3):
            self.assertNotIn(f'RES-{i}', blob)
        self.assertNotIn('Personnel file', blob)


class RestrictedNoticeRoutingTests(TestCase):
    """Through the REAL url + middleware, not just the view callable.

    APIRequestFactory bypasses routing and middleware, so a passing unit test
    can sit alongside a broken endpoint. This drives /api/v1/records/.
    """

    @classmethod
    def setUpTestData(cls):
        cls.company = Company.objects.create(code='RNR', name='Routing Co.')
        cls.cat = RecordCategory.objects.create(name='Files', retention_months=72)
        for i in range(2):
            RecordItem.objects.create(
                reference=f'R-{i}', title='Personnel file', category=cls.cat,
                company=cls.company, current_location='Store',
                confidentiality=RecordItem.Confidentiality.RESTRICTED)

    def test_the_live_endpoint_returns_the_notice(self):
        from rest_framework.test import APIClient
        user = User.objects.create_user('rt', 'rt@alphadirect.co.bw', 'x')
        UserProfile.objects.update_or_create(
            user=user, defaults={'title': UserProfile.Title.ACCOUNTANT})
        c = APIClient()
        c.force_authenticate(user=user)
        r = c.get('/api/v1/records/')
        self.assertEqual(r.status_code, 200, r.content[:200])
        self.assertEqual(r.data['restricted_hidden'], 2)

    def test_a_search_cannot_be_used_to_confirm_a_hidden_record(self):
        """The count must NOT track the filter — that would be an oracle."""
        from rest_framework.test import APIClient
        user = User.objects.create_user('rt2', 'rt2@alphadirect.co.bw', 'x')
        UserProfile.objects.update_or_create(
            user=user, defaults={'title': UserProfile.Title.ACCOUNTANT})
        c = APIClient()
        c.force_authenticate(user=user)
        hit = c.get('/api/v1/records/', {'q': 'Personnel'})
        miss = c.get('/api/v1/records/', {'q': 'zzzz-no-such-thing'})
        # Same number either way, so typing a real name reveals nothing.
        self.assertEqual(hit.data['restricted_hidden'],
                         miss.data['restricted_hidden'])
        self.assertEqual(len(hit.data['results']), 0)
