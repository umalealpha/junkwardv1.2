"""commissions/test_statement_download.py — reviewer downloads the stored statement.

Bokani Makosha 2026-08-12: a reviewer can download the uploaded workbook to verify
before approving. Only a commission reviewer may fetch it; a missing file is 404.

Run: manage.py test commissions.test_statement_download
"""
from decimal import Decimal

from django.contrib.auth.models import User
from django.core.files.base import ContentFile
from django.test import TestCase
from rest_framework.test import APIClient

from commissions.models import CommissionAgent, CommissionGroup, CommissionSubmission


class StatementDownloadTests(TestCase):
    def setUp(self):
        self.grp = CommissionGroup.objects.create(
            key='ztest-indep', name='ZTest Independent', withholding_rate=Decimal('0'))
        self.agent = CommissionAgent.objects.create(name='Test Agent', agent_code='ZT-A1', group=self.grp)
        self.sub = CommissionSubmission.objects.create(
            agent=self.agent, group=self.grp, period_label='2026-08')
        self.sub.statement_file.save('stmt.xlsx', ContentFile(b'PKdummyworkbookbytes'), save=True)
        # Bokani Makosha is a Stage-1 reviewer by name (commissions/access._ROSTER_NAMES).
        self.reviewer = User.objects.create_user('bok', email='bok@example.com', password='x',
                                                 first_name='Bokani', last_name='Makosha')
        self.outsider = User.objects.create_user('nobody', email='no@example.com', password='x')

    def test_reviewer_can_download(self):
        c = APIClient(); c.force_authenticate(self.reviewer)
        r = c.get(f'/api/v1/commissions/submissions/{self.sub.id}/statement/')
        self.assertEqual(r.status_code, 200)

    def test_non_reviewer_forbidden(self):
        c = APIClient(); c.force_authenticate(self.outsider)
        r = c.get(f'/api/v1/commissions/submissions/{self.sub.id}/statement/')
        self.assertIn(r.status_code, (403, 404))  # denied either way; scoping 404s first

    def test_no_file_and_no_lines_is_404(self):
        sub2 = CommissionSubmission.objects.create(
            agent=self.agent, group=self.grp, period_label='2026-09')   # no file, no lines
        c = APIClient(); c.force_authenticate(self.reviewer)
        r = c.get(f'/api/v1/commissions/submissions/{sub2.id}/statement/')
        self.assertEqual(r.status_code, 404)

    def test_no_file_falls_back_to_csv_from_lines(self):
        # Bokani's current queue: no raw file kept, but the parsed lines are stored —
        # the reviewer still gets a downloadable statement (CSV) to verify.
        from commissions.models import CommissionSubmissionLine
        sub3 = CommissionSubmission.objects.create(
            agent=self.agent, group=self.grp, period_label='2026-10')
        CommissionSubmissionLine.objects.create(
            submission=sub3, policy_number='POL-1', client_name='A Client',
            amount_collected=Decimal('100.00'), commission_amount=Decimal('10.00'))
        c = APIClient(); c.force_authenticate(self.reviewer)
        r = c.get(f'/api/v1/commissions/submissions/{sub3.id}/statement/')
        self.assertEqual(r.status_code, 200)
        self.assertIn('text/csv', r['Content-Type'])
