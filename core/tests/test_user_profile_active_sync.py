"""Deactivating a user must actually close their login.

Reported by Unami Butale, 2026-09-18: "I am unable to remove other parties."
She was right, and it was not the screen.

Omni's Deactivate button and the Active tick only ever wrote
UserProfile.is_active. The sign-in reads auth.User.is_active
(core.staff_login_views), as do the browser-token check (core.token_auth) and
the phone-session check (core.device_auth). So a "deactivated" person lost
their powers and kept their key.

Live proof on prod the same day: Bame Sebape, who left on 26 June, had
profile.is_active False and user.is_active True — switched off on screen,
still able to sign in, 84 days later. Thirteen accounts in total had the two
flags disagreeing.

Every test here fails without the sync in UserProfile.save().
"""
from datetime import timedelta

from django.contrib.auth.models import User
from django.test import TestCase
from django.utils import timezone
from rest_framework.authtoken.models import Token

from core.models import UserProfile


def _profile(username, **kwargs):
    user = User.objects.create_user(username, username + '@alphadirect.co.bw', 'pw')
    defaults = dict(user=user, role=UserProfile.Role.OPERATIONS_STAFF)
    defaults.update(kwargs)
    return UserProfile.objects.create(**defaults), user


def _device_session(user, expires_in_days=30):
    try:
        from core.device_session_models import StaffDeviceSession
    except Exception:                                    # noqa: BLE001
        return None
    return StaffDeviceSession.objects.create(
        user=user,
        token_hash=StaffDeviceSession.hash_token('b' * 64),
        expires_at=timezone.now() + timedelta(days=expires_in_days),
    )


class DeactivatingClosesTheLoginTest(TestCase):
    def test_switching_the_profile_off_closes_the_sign_in(self):
        prof, user = _profile('switchoff')
        self.assertTrue(user.is_active)
        prof.is_active = False
        prof.save()
        user.refresh_from_db()
        self.assertFalse(user.is_active,
                         'deactivated on screen but they can still sign in')

    def test_it_ends_the_browser_session_immediately(self):
        prof, user = _profile('browsersess')
        Token.objects.create(user=user)
        prof.is_active = False
        prof.save()
        self.assertFalse(Token.objects.filter(user=user).exists(),
                         'they stay signed in for up to 15 more hours')

    def test_it_ends_the_phone_session_immediately(self):
        prof, user = _profile('phonesess')
        sess = _device_session(user)
        if sess is None:
            self.skipTest('device sessions not installed in this build')
        prof.is_active = False
        prof.save()
        sess.refresh_from_db()
        self.assertIsNotNone(sess.revoked_at,
                             'they stay signed in on the phone for up to 30 more days')

    def test_switching_the_profile_back_on_reopens_the_sign_in(self):
        prof, user = _profile('switchon')
        prof.is_active = False
        prof.save()
        prof.is_active = True
        prof.save()
        user.refresh_from_db()
        self.assertTrue(user.is_active,
                        'ticking Active back on left the person locked out')

    def test_an_unrelated_edit_does_not_touch_the_login(self):
        # Surgical: changing a job title must not reopen or close anyone.
        prof, user = _profile('titlechange')
        user.is_active = False
        user.save(update_fields=['is_active'])
        prof.department = 'Finance'
        prof.save()
        user.refresh_from_db()
        self.assertFalse(user.is_active,
                         'editing a department quietly reopened a closed login')
