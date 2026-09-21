"""The commission review email must carry the whole basis of the commission.

CFO 2026-09-20, on the "Commission for your review — Brian Ngele · 2026-09"
email: *"its easy for me to approve when I receive these things if it comes with
details or I click and see it in the app, now expecting a busy cfo to go inside
the web version to check is unreasonable"*.

So this walks the real path — a submission with real lines, through the real
pack, into the real email body — and asserts the reviewer can see the policies,
the client, the rate, the deductions and the sanity flags without leaving their
mail app. Every assertion here fails against the email as it was sent on
2026-09-20.

Run: python manage.py test commissions.test_review_email_detail --keepdb
"""
from decimal import Decimal

from django.test import TestCase

from core.approval_pack import build_pack, pack_html
from .models import (CommissionAgent, CommissionGroup, CommissionSubmission,
                     CommissionSubmissionLine)

S = CommissionSubmission.Status


class ReviewEmailDetailTests(TestCase):
    def setUp(self):
        self.grp = CommissionGroup.objects.get(key='in_house')
        # Synthetic name on purpose: a real agent's name pinned to invented
        # commission figures in a fixture is a record of something that never
        # happened (/fabe 2026-09-20).
        self.agent = CommissionAgent.objects.create(name='Test Agent Alpha', group=self.grp)
        self.sub = CommissionSubmission.objects.create(
            agent=self.agent, group=self.grp, period_label='2026-09',
            status=S.SUBMITTED,
            gross_commission=Decimal('3798.59'),
            withholding_rate=Decimal('0.1000'),
            withholding_amount=Decimal('379.86'),
            net_payable=Decimal('3418.73'))
        CommissionSubmissionLine.objects.create(
            submission=self.sub, policy_number='ADI/MOT/001',
            client_name='K Moeng', annualised_premium=Decimal('12000.00'),
            commission_rate=Decimal('12.5'), amount_applicable=Decimal('12000.00'),
            commission_amount=Decimal('1500.00'))
        CommissionSubmissionLine.objects.create(
            submission=self.sub, policy_number='ADI/MOT/002',
            client_name='T Seretse', annualised_premium=Decimal('18388.72'),
            commission_rate=Decimal('12.5'), amount_applicable=Decimal('18388.72'),
            commission_amount=Decimal('2298.59'))

    def _html(self):
        return pack_html(build_pack('commissions', self.sub.id))

    def test_every_policy_line_is_in_the_email_body(self):
        html = self._html()
        self.assertIn('ADI/MOT/001', html)
        self.assertIn('ADI/MOT/002', html)

    def test_client_rate_and_premium_are_shown(self):
        html = self._html()
        self.assertIn('K Moeng', html)           # who the business is
        self.assertIn('12.5%', html)             # the rate it was paid at
        self.assertIn('18,388.72', html)         # the premium it was paid on

    def test_deductions_and_net_are_shown(self):
        html = self._html()
        self.assertIn('3,798.59', html)          # gross
        self.assertIn('379.86', html)            # withholding
        self.assertIn('3,418.73', html)          # net payable

    def test_the_lines_are_totalled_and_counted(self):
        html = self._html()
        self.assertIn('2 lines', html)
        self.assertIn('BWP 3,798.59', html)

    def test_a_clean_sheet_says_so(self):
        self.assertIn('nothing looks out of place', self._html())

    def test_a_duplicate_policy_is_flagged_to_the_reviewer(self):
        """The sanity checks review_flags has computed since 2026-08-22 were
        never shown to the person signing. They are now."""
        CommissionSubmissionLine.objects.create(
            submission=self.sub, policy_number='ADI/MOT/001',
            client_name='K Moeng', commission_amount=Decimal('1500.00'))
        html = self._html()
        self.assertIn('Worth a look', html)
        self.assertIn('ADI/MOT/001', html)

    def test_a_long_sheet_says_how_many_lines_are_not_shown(self):
        """212 lines in a phone email is unreadable, and 40 shown as if they
        were all of them is worse — it has to say what it is holding back."""
        for i in range(60):
            CommissionSubmissionLine.objects.create(
                submission=self.sub, policy_number=f'ADI/BULK/{i:03d}',
                commission_amount=Decimal('10.00'))
        pack = build_pack('commissions', self.sub.id)
        self.assertEqual(pack['row_count'], 62)
        self.assertEqual(pack['shown_count'], 40)
        self.assertIn('first 40 of 62', self._html())

    def test_the_email_itself_carries_the_detail_and_the_app_link(self):
        """Not the pack in isolation — the body that is actually sent."""
        from django.contrib.auth import get_user_model
        from unittest.mock import patch
        User = get_user_model()
        reviewer = User.objects.create_user(
            username='reviewer-detail', email='reviewer-detail@alphadirect.co.bw',
            password='x', first_name='Pako')
        sent = {}

        def _capture(**kw):
            sent.update(kw)
            return 1

        with patch('commissions.access.stage_users', return_value=[reviewer]), \
                patch('commissions.access.stage_of', return_value='stage1'), \
                patch('core.notifications.send_html_with_cfo_cc', side_effect=_capture):
            from . import notify
            notify.notify_commission_awaiting_review(self.sub)

        html = sent.get('html', '')
        self.assertIn('ADI/MOT/001', html)                 # the lines are in the mail
        self.assertIn('3,418.73', html)                    # so is the net
        self.assertIn('/app/approvals', html)              # ...and the way into the app
        self.assertIn('Approve this commission', html)     # one-tap approve survived

    def test_a_crashed_sanity_check_is_never_shown_as_all_clear(self):
        """review_flags returns level 'unknown' when its own checks blow up.
        A renderer branching "not 'check' → green" turns a control that FAILED
        into a green tick in the approver's email (/fabe 2026-09-20)."""
        from unittest.mock import patch
        with patch('commissions.review_flags.review_flags',
                   return_value={'level': 'unknown', 'items': []}):
            html = self._html()
        self.assertNotIn('nothing looks out of place', html)
        self.assertIn('could not be run', html)

    def test_the_email_never_degrades_below_the_old_one(self):
        """build_pack swallows a builder error by design. If the detail cannot
        be built the email must fall back to the agent/period/gross/net table it
        carried before — not send the happy-path wording with nothing under it."""
        from unittest.mock import patch
        from django.contrib.auth import get_user_model
        User = get_user_model()
        reviewer = User.objects.create_user(
            username='reviewer-fallback', email='reviewer-fallback@alphadirect.co.bw',
            password='x', first_name='Pako')
        sent = {}

        with patch('commissions.access.stage_users', return_value=[reviewer]), \
                patch('commissions.access.stage_of', return_value='stage1'), \
                patch('core.approval_pack.build_pack', return_value=None), \
                patch('core.notifications.send_html_with_cfo_cc',
                      side_effect=lambda **kw: sent.update(kw) or 1):
            from . import notify
            notify.notify_commission_awaiting_review(self.sub)

        html = sent.get('html', '')
        self.assertIn('3,798.59', html)                       # gross still there
        self.assertIn('Net payable', html)                    # and the net
        self.assertIn('Approve this commission', html)        # still approvable
        self.assertNotIn('Everything it is based on is', html)  # no empty promise
