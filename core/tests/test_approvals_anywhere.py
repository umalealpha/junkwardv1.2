"""CFO batch 2026-09-12 — approvals from wherever he is.

Three separate complaints, three separate guards:

  1. "In Omni app i am not seeing ... authority of recruit ... it should be
     full approval suit" — Authorities to Recruit and commissions were COUNTED
     in the unified approvals list but never ITEMISED, so on a phone they were
     a link out to a desktop page. Now they are one-tap rows.
  2. "dont talk shit about money decision etc and implement in email links as
     well, i have told u many times money goes out when i go to fnb with duel
     factor authroisation" — payment tasks are decidable from HIS email. The
     CEO's brief is deliberately unchanged.
  3. "my morning breif and cfo brief should not include outstading payments
     rather outstading staff loan request, leave request, incentive and
     commisison request" — the brief shows people decisions, not payments.
"""
import datetime

from django.contrib.auth.models import User
from django.test import TestCase

from core.ceo_monitor_views import is_decidable_task
from core.models import OmniTask


class MoneyDecisionsFromTheCfoEmail(TestCase):
    """Complaint 2."""

    def setUp(self):
        self.assigner = User.objects.create_user('raiser', 'raiser@alphadirect.co.bw', 'x')
        self.cfo = User.objects.create_user('pganesharajah', 'pganesharajah@alphadirect.co.bw', 'x')

    def _task(self, source='other'):
        return OmniTask.objects.create(
            assigner=self.assigner, assignee=self.cfo, title='Payment authorisation',
            source=source, status=OmniTask.Status.PENDING)

    def test_the_cfo_may_decide_a_payment_task_from_his_email(self):
        self.assertTrue(is_decidable_task(self._task(source='payment_request'),
                                          actor='pganesharajah'))

    def test_the_ceo_still_may_not(self):
        """Arun never asked for this and his brief must not change."""
        self.assertFalse(is_decidable_task(self._task(source='payment_request'),
                                           actor='aiyer'))
        # No actor at all means the CEO — the original default.
        self.assertFalse(is_decidable_task(self._task(source='payment_request')))

    def test_a_finished_task_is_still_never_decidable(self):
        t = self._task(source='payment_request')
        t.status = OmniTask.Status.DONE
        t.save(update_fields=['status'])
        self.assertFalse(is_decidable_task(t, actor='pganesharajah'))

    def test_a_non_task_is_still_never_decidable(self):
        self.assertFalse(is_decidable_task(object(), actor='pganesharajah'))


class TheBriefShowsPeopleNotPayments(TestCase):
    """Complaint 3 — the filter that decides what reaches his brief."""

    def test_payment_streams_are_the_ones_left_out(self):
        from hris.morning_brief import PAYMENT_STREAMS
        # The four he named must NOT be filtered out.
        for key in ('leave', 'staff_loans', 'incentives', 'commissions'):
            self.assertNotIn(key, PAYMENT_STREAMS, f'{key} must reach his brief')
        # Payments must be.
        for key in ('payments', 'payment_requests', 'petty_cash', 'po'):
            self.assertIn(key, PAYMENT_STREAMS, f'{key} must not reach his brief')

    def test_every_excluded_key_is_a_real_stream(self):
        """A filter naming keys that do not exist reads as protective and
        filters nothing - the same class of bug as a control keyed on a column
        the data does not carry. The first cut of this set named two keys that
        existed nowhere (petty_cash_reimb, refunds_authorise) while three real
        money streams were missing. Keys are read out of the source so this
        fails the moment either side is renamed."""
        import re
        from pathlib import Path

        from hris.morning_brief import PAYMENT_STREAMS
        import core.approvals_views as av

        src = Path(av.__file__).read_text(encoding='utf-8')
        real = set(re.findall(r'"key": "([a-z_]+)"', src))
        self.assertTrue(real, 'could not read the stream keys out of the source')
        unknown = PAYMENT_STREAMS - real
        self.assertEqual(unknown, set(),
                         f'PAYMENT_STREAMS names streams that do not exist: {sorted(unknown)}')

    def test_people_approvals_never_raises(self):
        """A brief is never worth losing over this block."""
        from hris.morning_brief import people_approvals_for
        out = people_approvals_for(None)
        self.assertEqual(out['count'], 0)
        self.assertEqual(out['streams'], [])


class EveryItemisedStreamCanActuallyBeActioned(TestCase):
    """Complaint 1, and the structural trap behind it: a stream listed with
    checkboxes but with no adapter behind it renders an Approve button that
    does nothing. Every bulk_ok stream must have BOTH adapters."""

    def test_authority_to_recruit_and_commissions_have_adapters(self):
        from core.approvals_views import _BULK_ADAPTERS, _REJECT_ADAPTERS
        for key in ('authority_to_recruit', 'commissions'):
            self.assertIn(key, _BULK_ADAPTERS, f'{key} cannot be approved')
            self.assertIn(key, _REJECT_ADAPTERS, f'{key} cannot be sent back')

    def test_no_bulk_stream_is_left_without_an_approve_adapter(self):
        """Catches the next one too, not just these two.

        This used to iterate the rendered streams on an empty test database, so
        the loop body never ran and the test passed by having nothing to check
        (/fabe 2026-09-13). A test that cannot fail is a comment. It now reads
        the declared stream list, which exists whether or not anything pending
        does.
        """
        from core.approvals_views import (BULK_STREAM_KEYS, _BULK_ADAPTERS,
                                          _REJECT_ADAPTERS)
        self.assertTrue(BULK_STREAM_KEYS, 'no bulk-able streams declared at all')
        for key in BULK_STREAM_KEYS:
            self.assertIn(key, _BULK_ADAPTERS,
                          f'{key} offers a tick box but nothing acts on it')
            self.assertIn(key, _REJECT_ADAPTERS,
                          f'{key} can be approved in bulk but not declined')

    def test_the_declared_bulk_streams_match_what_the_screen_renders(self):
        """That list is only useful if it cannot drift from what is rendered.

        Reads the source of pending_approval_items_for, pulls out every stream
        rendered with a tick box, and asserts that set IS the declared set. Add
        a tenth bulk-able stream without an adapter and this goes red.
        """
        import inspect
        import re

        from core import approvals_views

        src = inspect.getsource(approvals_views.pending_approval_items_for)
        rendered = set()
        for block in src.split('"bulk_ok": True')[:-1]:
            keys = re.findall(r'"key":\s*"([a-z_]+)"', block)
            if keys:
                rendered.add(keys[-1])

        declared = set(approvals_views.BULK_STREAM_KEYS)
        self.assertEqual(
            rendered, declared,
            'the declared bulk streams and the ones actually rendered with a '
            'tick box have drifted apart. '
            f'rendered but not declared: {sorted(rendered - declared)}; '
            f'declared but not rendered: {sorted(declared - rendered)}')


class OneClickApprovalLinks(TestCase):
    """Complaint 1 again, by email: staff loans and commissions had NO email
    route at all, so the only way to approve was to open Omni and sign in."""

    def test_loan_and_commission_actions_are_registered(self):
        from core.magic_action import ACTIONS
        for kind in ('loan_approve', 'commission_approve'):
            self.assertIn(kind, ACTIONS)
            self.assertIn('describe', ACTIONS[kind])
            self.assertIn('act', ACTIONS[kind])

    def test_a_missing_loan_is_reported_not_crashed(self):
        from core.magic_action import ACTIONS
        u = User.objects.create_user('cfo2', 'cfo2@alphadirect.co.bw', 'x')
        ok, msg = ACTIONS['loan_approve']['act'](u, {'loan_id': '00000000-0000-0000-0000-000000000000'})
        self.assertFalse(ok)
        self.assertIn('not found', msg.lower())
