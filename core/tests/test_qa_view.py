"""The no-sign-in read-only quality-check view (core.qa_view_login).

What must hold, in plain terms:
  * With no key configured on the server, the view does not exist.
  * A wrong key gets nothing.
  * The right key returns a session that can LOOK at anything and CHANGE nothing
    — the change refusal happening in authentication, so it holds in every view.
"""
from django.test import TestCase, override_settings
from django.urls import reverse
from rest_framework.authtoken.models import Token

from core.screenshot_bot import QA_VIEW_USERNAME

GOOD_KEY = 'qa-view-test-key-long-enough-x'   # >= MIN_KEY_LEN


@override_settings(ALLOWED_HOSTS=['*'])
class QaViewOpenTests(TestCase):
    def setUp(self):
        self.url = reverse('v1-qa-view-open')

    def _open(self, key):
        return self.client.post(self.url, {'key': key},
                                content_type='application/json')

    def test_off_when_no_key_configured(self):
        with self.settings():
            import os
            os.environ.pop('OMNI_QA_VIEW_KEY', None)
            r = self._open(GOOD_KEY)
        self.assertEqual(r.status_code, 503)

    def test_short_key_counts_as_unconfigured(self):
        import os
        os.environ['OMNI_QA_VIEW_KEY'] = 'tooshort'
        try:
            r = self._open('tooshort')
        finally:
            os.environ.pop('OMNI_QA_VIEW_KEY', None)
        self.assertEqual(r.status_code, 503)

    def test_wrong_key_denied(self):
        import os
        os.environ['OMNI_QA_VIEW_KEY'] = GOOD_KEY
        try:
            r = self._open('this-is-not-the-key-at-all-no')
        finally:
            os.environ.pop('OMNI_QA_VIEW_KEY', None)
        self.assertEqual(r.status_code, 403)
        self.assertFalse(Token.objects.filter(user__username=QA_VIEW_USERNAME).exists())

    def test_right_key_can_read_but_never_write(self):
        import os
        os.environ['OMNI_QA_VIEW_KEY'] = GOOD_KEY
        try:
            r = self._open(GOOD_KEY)
            self.assertEqual(r.status_code, 200, r.content)
            key = r.json()['token']
            self.assertTrue(r.json()['read_only'])

            auth = {'HTTP_AUTHORIZATION': f'Token {key}'}
            # Looking is allowed — the identity is a superuser, so a normal
            # listing page answers rather than refusing on permissions.
            read = self.client.get('/api/v1/companies/', **auth)
            self.assertEqual(read.status_code, 200, read.content)

            # Changing is refused at the AUTHENTICATION layer, not by a per-view
            # permission — so it holds for every endpoint in Omni, and the
            # message says why in words a non-technical reader understands.
            write = self.client.post('/api/v1/companies/', {'name': 'Nope'},
                                     content_type='application/json', **auth)
            self.assertIn(write.status_code, (401, 403), write.content)
            self.assertIn('read-only', write.json()['detail'])

            delete = self.client.delete('/api/v1/companies/', **auth)
            self.assertIn(delete.status_code, (401, 403), delete.content)
        finally:
            os.environ.pop('OMNI_QA_VIEW_KEY', None)

    def test_reopening_does_not_log_out_the_first_session(self):
        """Two tabs, two devices, or a tab plus the screenshot harness must be
        able to hold the view at once. Caught live 2026-07-29: minting a fresh
        token on every open silently 403'd every page of the older session."""
        import os
        os.environ['OMNI_QA_VIEW_KEY'] = GOOD_KEY
        try:
            first = self._open(GOOD_KEY).json()['token']
            second = self._open(GOOD_KEY).json()['token']
            self.assertEqual(first, second)

            # and the first key still reads
            r = self.client.get('/api/v1/companies/',
                                HTTP_AUTHORIZATION=f'Token {first}')
            self.assertEqual(r.status_code, 200, r.content)
        finally:
            os.environ.pop('OMNI_QA_VIEW_KEY', None)

    def test_a_stale_token_is_replaced(self):
        """A token near the 15-hour ceiling is swapped for a fresh one, so a
        check never dies half way through."""
        import os
        from datetime import timedelta
        from django.utils import timezone
        from core.screenshot_bot import get_or_create_qa_viewer

        viewer, _ = get_or_create_qa_viewer()
        stale = Token.objects.create(user=viewer)
        Token.objects.filter(pk=stale.pk).update(
            created=timezone.now() - timedelta(hours=14, minutes=45))

        os.environ['OMNI_QA_VIEW_KEY'] = GOOD_KEY
        try:
            fresh = self._open(GOOD_KEY).json()['token']
        finally:
            os.environ.pop('OMNI_QA_VIEW_KEY', None)
        self.assertNotEqual(fresh, stale.key)
        self.assertFalse(Token.objects.filter(key=stale.key).exists())

    def test_screenshot_bot_token_survives_a_qa_open(self):
        """The robot and the human view must not knock each other out."""
        from core.screenshot_bot import get_or_create_bot
        bot, _ = get_or_create_bot()
        bot_token = Token.objects.create(user=bot)

        import os
        os.environ['OMNI_QA_VIEW_KEY'] = GOOD_KEY
        try:
            self.assertEqual(self._open(GOOD_KEY).status_code, 200)
        finally:
            os.environ.pop('OMNI_QA_VIEW_KEY', None)

        bot_token.refresh_from_db()          # still there
        self.assertTrue(Token.objects.filter(key=bot_token.key).exists())


@override_settings(ALLOWED_HOSTS=['*'])
class QaViewerIsAnAuditorTests(TestCase):
    """CFO 2026-07-29: "no finance manager, or internal auditor is best."

    The quality-control identity must read like an auditor and hold no authority.
    """

    def test_carries_the_auditor_title_and_no_authority(self):
        from core.models import UserProfile
        from core.screenshot_bot import get_or_create_qa_viewer

        viewer, _ = get_or_create_qa_viewer()
        profile = UserProfile.objects.get(user=viewer)

        self.assertEqual(profile.title, UserProfile.Title.AUDITOR)
        self.assertFalse(profile.is_administrator)
        # Auditor is in none of the sets that confer authority.
        self.assertNotIn(profile.title, UserProfile.APPROVAL_TITLES)
        self.assertNotIn(profile.title, UserProfile.CREATION_TITLES)
        self.assertNotIn(profile.title, UserProfile.PAYROLL_APPROVAL_TITLES)
        # ...but it CAN see financials, or it would be useless for checking them.
        self.assertIn(profile.title, UserProfile.FINANCIALS_VIEW_TITLES)

    def test_a_drifted_title_is_pulled_back(self):
        from core.models import UserProfile
        from core.screenshot_bot import get_or_create_qa_viewer

        viewer, _ = get_or_create_qa_viewer()
        UserProfile.objects.filter(user=viewer).update(
            title=UserProfile.Title.FINANCE_MANAGER, is_administrator=True)

        get_or_create_qa_viewer()          # normalises on every open
        profile = UserProfile.objects.get(user=viewer)
        self.assertEqual(profile.title, UserProfile.Title.AUDITOR)
        self.assertFalse(profile.is_administrator)

    def test_it_has_a_profile_so_personal_panels_do_not_error(self):
        from core.screenshot_bot import get_or_create_qa_viewer
        from core.models import get_user_profile

        viewer, _ = get_or_create_qa_viewer()
        viewer.refresh_from_db()
        self.assertIsNotNone(get_user_profile(viewer))

    def test_it_never_advertises_authority_it_does_not_have(self):
        """Several capability flags grant themselves on is_superuser, and this
        identity IS a superuser so no page is hidden from a check. Live on prod
        2026-07-29 it therefore reported can_approve_payroll and
        can_administer_users as True — authority the auth layer physically
        refuses it. The flags must tell the truth."""
        from core.models import UserProfile
        from core.screenshot_bot import get_or_create_qa_viewer

        viewer, _ = get_or_create_qa_viewer()
        viewer.refresh_from_db()
        p = UserProfile.objects.get(user=viewer)
        self.assertTrue(viewer.is_superuser, 'precondition: it is a superuser')
        self.assertTrue(p.is_read_only_identity)

        for flag in ('can_approve_journal_entries', 'can_create_journal_entries',
                     'can_approve_payroll', 'can_administer_users',
                     'can_post_directly', 'can_manage_periods',
                     'can_originate_controlled_txn', 'can_check_controlled_txn'):
            self.assertFalse(getattr(p, flag),
                             f'{flag} must be False — it cannot write at all')

        # Seeing everything is the whole point, so viewing stays True.
        self.assertTrue(p.can_view_financials)

    def test_a_normal_superuser_keeps_full_authority(self):
        """The guard must be scoped to the locked identities only."""
        from django.contrib.auth import get_user_model
        from core.models import UserProfile
        boss = get_user_model().objects.create_user('realboss', is_superuser=True)
        p = UserProfile.objects.create(user=boss, role=UserProfile.Role.FINANCE_ADMIN,
                                       title=UserProfile.Title.CFO)
        self.assertFalse(p.is_read_only_identity)
        self.assertTrue(p.can_administer_users)
        self.assertTrue(p.can_approve_payroll)
        self.assertTrue(p.can_manage_periods)
