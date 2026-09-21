"""commissions/test_inhouse_detail_upload.py — bug f3a02295 (Bokani Makosha,
2026-08-21): an in-house reviewer could not upload a DETAILED sheet.

The "Load agent sheets" wizard defaults the in-house group to summary mode, so a
per-policy workbook was either rejected ("wants a summary") or stored as a single
gross line — "just a number of which i can't see what the commission is based on".

Two fixes proven here:
  1. An in-house upload that actually carries a per-policy table is loaded as
     DETAIL (per-policy lines), keeping the basis — auto-detected, so a genuine
     agent-summary sheet still takes the summary path.
  2. own=1 (the "My commission" tab) binds the upload to the signed-in user's OWN
     agent, per-policy, even when that user is also a reviewer.

Run: DB_ENGINE=sqlite SECRET_KEY=x python manage.py test commissions.test_inhouse_detail_upload
"""
import os
from decimal import Decimal
from pathlib import Path
from unittest import mock

os.environ.setdefault('COMMISSIONS_STAGE2_EMAILS', 'pako@x.co')

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from rest_framework.test import APIRequestFactory, force_authenticate

from core.deadline_test_utils import submission_window_open

from . import importer
from .api_views import CommissionSubmissionViewSet
from .models import CommissionAgent, CommissionGroup, CommissionSubmission

User = get_user_model()
DATA = Path(__file__).resolve().parent / 'testdata'


class PerPolicyDetectionTests(TestCase):
    """The detector tells a detailed sheet from an agent-summary sheet."""

    def test_detailed_sheet_detected(self):
        self.assertTrue(importer.has_per_policy_table(str(DATA / 'comm_test.xlsx')))

    def test_summary_sheet_not_detected(self):
        self.assertFalse(importer.has_per_policy_table(str(DATA / 'comm_inhouse.xlsx')))


@submission_window_open   # 16th submission deadline (2026-08-28)
class InhouseDetailUploadTests(TestCase):
    """Item 1 — an in-house upload of a DETAILED sheet keeps per-policy lines."""

    def setUp(self):
        CommissionGroup.objects.get_or_create(
            key='in_house', defaults={'name': 'In-house / payroll agents',
                                      'pays_via': 'payroll'})

    def _upload(self, filename, **extra):
        raw = (DATA / filename).read_bytes()
        f = SimpleUploadedFile(filename, raw)
        data = {'file': f, 'period': '2026-08', 'group': 'in_house',
                'inhouse': '1', 'submit': '1', **extra}
        req = APIRequestFactory().post('/x', data, format='multipart')
        reviewer = User.objects.create_user('rv', 'rv@x.co', 'pw',
                                            first_name='Rev', last_name='Iewer')
        force_authenticate(req, user=reviewer)
        return CommissionSubmissionViewSet.as_view({'post': 'upload'})(req)

    @mock.patch.dict(os.environ, {'COMMISSIONS_STAGE1_EMAILS': 'rv@x.co'})
    def test_detailed_inhouse_upload_keeps_lines(self):
        resp = self._upload('comm_test.xlsx', agent='Detailed Agent')
        self.assertEqual(resp.status_code, 200, getattr(resp, 'data', None))
        sub = CommissionSubmission.objects.get(period_label='2026-08')
        # per-policy detail preserved — NOT collapsed to one "(in-house gross)" line
        self.assertEqual(sub.lines.count(), 2)
        self.assertNotEqual(sub.lines.first().policy_number, '')
        self.assertEqual(sub.gross_commission, Decimal('250'))

    @mock.patch.dict(os.environ, {'COMMISSIONS_STAGE1_EMAILS': 'rv@x.co'})
    def test_genuine_summary_still_loads_as_summary(self):
        # regression — a real agent-summary sheet is NOT mis-read as one agent's
        # detail; it still creates one submission per agent (gross only).
        resp = self._upload('comm_inhouse.xlsx')
        self.assertEqual(resp.status_code, 200, getattr(resp, 'data', None))
        self.assertEqual(resp.data.get('agents'), 2)   # Agent One + Agent Two


@submission_window_open   # 16th submission deadline (2026-08-28)
class OwnUploadBindsToSelfTests(TestCase):
    """Item 2 — own=1 binds a REVIEWER's upload to their own agent (per-policy)."""

    def setUp(self):
        self.grp, _ = CommissionGroup.objects.get_or_create(
            key='in_house', defaults={'name': 'In-house / payroll agents',
                                      'pays_via': 'payroll'})
        # a reviewer who is ALSO an agent (Bokani's exact situation)
        self.user = User.objects.create_user('bok', 'bok@x.co', 'pw',
                                              first_name='Bokani', last_name='Makosha')
        self.agent = CommissionAgent.objects.create(
            name='Bokani Makosha', email='bok@x.co', group=self.grp)

    @mock.patch.dict(os.environ, {'COMMISSIONS_STAGE1_EMAILS': 'bok@x.co'})
    def test_own_upload_binds_to_own_agent_not_filename(self):
        raw = (DATA / 'comm_test.xlsx').read_bytes()
        # filename that would derive a DIFFERENT agent name if not forced to self
        f = SimpleUploadedFile('SOMEONE ELSE commission.xlsx', raw)
        req = APIRequestFactory().post(
            '/x', {'file': f, 'period': '2026-08', 'own': '1'}, format='multipart')
        force_authenticate(req, user=self.user)
        resp = CommissionSubmissionViewSet.as_view({'post': 'upload'})(req)
        self.assertEqual(resp.status_code, 200, getattr(resp, 'data', None))
        sub = CommissionSubmission.objects.get(period_label='2026-08')
        self.assertEqual(sub.agent_id, self.agent.id)      # bound to Bokani, not "SOMEONE ELSE"
        self.assertEqual(sub.lines.count(), 2)             # per-policy detail
        self.assertEqual(sub.status, CommissionSubmission.Status.DRAFT)  # own upload never auto-submits
        self.assertEqual(CommissionAgent.objects.filter(name__icontains='someone').count(), 0)


@submission_window_open   # 16th submission deadline (2026-08-28)
class AiUploadHintTests(TestCase):
    """CFO 2026-08-22 — a failed upload gets Aria's plain-English 'why + fix',
    best-effort: present when the AI answers, absent (unchanged message) when it
    doesn't. The AI never drives the import — this is a diagnosis message only."""

    def setUp(self):
        CommissionGroup.objects.get_or_create(
            key='independent', defaults={'name': 'Independent agents'})

    def _upload_unreadable(self):
        # a summary sheet uploaded on the PER-POLICY path → deterministic parse
        # fails → the error branch runs (where the hint is added).
        raw = (DATA / 'comm_inhouse.xlsx').read_bytes()
        f = SimpleUploadedFile('weird.xlsx', raw)
        req = APIRequestFactory().post(
            '/x', {'file': f, 'period': '2026-08', 'agent': 'X', 'group': 'independent'},
            format='multipart')
        rv = User.objects.create_user('rv2', 'rv2@x.co', 'pw')
        force_authenticate(req, user=rv)
        return CommissionSubmissionViewSet.as_view({'post': 'upload'})(req)

    @mock.patch.dict(os.environ, {'COMMISSIONS_STAGE1_EMAILS': 'rv2@x.co'})
    @mock.patch('core.ai_assist.is_safe_for_ai')
    @mock.patch('core.ai_assist.reasoning_complete')
    def test_hint_appended_when_ai_answers(self, mock_reason, mock_safe):
        from core.ai_assist import SafetyReport
        mock_safe.return_value = SafetyReport(safe=True, redacted_text='rows', redactions_made=0, notes=[])
        mock_reason.return_value = 'Tip: this looks like a summary sheet — switch the group to In-house.'
        resp = self._upload_unreadable()
        self.assertEqual(resp.status_code, 400)
        self.assertIn('Tip:', resp.data['detail'])

    @mock.patch.dict(os.environ, {'COMMISSIONS_STAGE1_EMAILS': 'rv2@x.co'})
    @mock.patch('core.ai_assist.reasoning_complete', side_effect=RuntimeError('AI down'))
    def test_message_unchanged_when_ai_unavailable(self, _mock_reason):
        resp = self._upload_unreadable()
        self.assertEqual(resp.status_code, 400)              # still a clean 400, no 500
        self.assertNotIn('Tip:', resp.data['detail'])        # graceful — no AI text
        self.assertIn("Couldn't read", resp.data['detail'])

    def test_no_raw_pii_sent_when_redaction_empty(self):
        """C5 — if the PII firewall returns empty redacted text, the AI is NOT
        called with raw cells (no client names / policy numbers leak)."""
        from commissions import ai_upload_assist
        from core.ai_assist import SafetyReport
        with mock.patch('core.ai_assist.is_safe_for_ai',
                        return_value=SafetyReport(safe=True, redacted_text='',
                                                  redactions_made=0, notes=[])), \
             mock.patch('core.ai_assist.reasoning_complete') as mock_reason:
            hint = ai_upload_assist.explain_upload_problem(
                str(DATA / 'comm_test.xlsx'), inhouse=False)
        self.assertEqual(hint, '')
        mock_reason.assert_not_called()   # never reached the model with raw data
