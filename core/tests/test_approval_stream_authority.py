"""core/tests/test_approval_stream_authority.py — Authority-to-Recruit shows on
My Approvals for a signatory (CFO 2026-08-31; was email-only).

Fable 5 caught that the first cut passed a Python list to _oldest_days (which
calls .order_by), so the stream silently never appeared whenever there WAS a
pending authority for the user. This test fails on that broken version (the
stream is missing) and passes once oldest_days is computed directly.
"""
from django.contrib.auth.models import User
from django.test import TestCase

from core.approvals_views import pending_approvals_for
from recruitment.models import AuthorityToRecruit


class AuthorityToRecruitStreamTests(TestCase):
    def _pending_authority(self):
        return AuthorityToRecruit.objects.create(
            person_name='New Hire', position='Analyst',
            employment_type='permanent',
            status=AuthorityToRecruit.Status.PENDING, approvals={})

    def test_pending_authority_shows_for_a_signatory(self):
        cfo = User.objects.create_user('pg', email='pganesharajah@alphadirect.co.bw')
        self._pending_authority()
        streams = pending_approvals_for(cfo)
        atr = [s for s in streams if s['key'] == 'authority_to_recruit']
        self.assertEqual(len(atr), 1,
                         f'ATR stream missing; got {[s["key"] for s in streams]}')
        self.assertEqual(atr[0]['count'], 1)

    def test_non_signatory_sees_no_authority_stream(self):
        other = User.objects.create_user('joe', email='joe@alphadirect.co.bw')
        self._pending_authority()
        streams = pending_approvals_for(other)
        self.assertNotIn('authority_to_recruit', [s['key'] for s in streams])

    def test_finance_manager_sees_the_stream_for_a_tiered_authority(self):
        """A tier signatory who is NOT on the legacy five (the Finance Manager)
        must still see the stream — the slot is resolved per-authority now
        (2026-09-02). A fixed-five lookup here would drop them entirely."""
        from recruitment.models import PositionTier
        fm = User.objects.create_user('kt', email='ktshutlhedi@alphadirect.co.bw')
        AuthorityToRecruit.objects.create(
            person_name='Junior', position='Associate',
            tier=PositionTier.objects.get(tier=1),
            hiring_manager_email='linemgr@alphadirect.co.bw', hiring_manager_name='M',
            status=AuthorityToRecruit.Status.PENDING, approvals={})
        streams = pending_approvals_for(fm)
        atr = [s for s in streams if s['key'] == 'authority_to_recruit']
        self.assertEqual(len(atr), 1,
                         f'FM ATR stream missing; got {[s["key"] for s in streams]}')
