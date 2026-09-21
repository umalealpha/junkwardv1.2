"""records/test_file_request.py — staff file-request + approve/deny trail.

Feature: Tshepo Maswabi 2026-08-11. Any staff member may request a physical file;
only Human Capital / Records (a register title) may approve or deny; the
requester may not decide their own request (SoD); everything is entity-clamped.

Run: manage.py test records.test_file_request
"""
from django.contrib.auth.models import User
from django.core.exceptions import PermissionDenied, ValidationError
from django.test import TestCase
from rest_framework.test import APIClient

from core.models import Company, UserCompanyAccess, UserProfile
from records import request_service as svc
from records.models import RecordCategory, RecordItem
from records.request_models import RecordFileRequest


def _hc_user(username, company):
    """A Human Capital / Records approver: an hr_manager title (in REGISTER_TITLES),
    entity-restricted to `company`."""
    u = User.objects.create_user(username, email=f'{username}@example.com', password='x')
    UserProfile.objects.update_or_create(
        user=u, defaults=dict(title='hr_manager', is_administrator=False))
    UserCompanyAccess.objects.create(user=u, company=company, can_view=True)
    return u


def _plain_user(username, company=None):
    u = User.objects.create_user(username, email=f'{username}@example.com', password='x')
    if company is not None:
        UserCompanyAccess.objects.create(user=u, company=company, can_view=True)
    return u


class FileRequestServiceTests(TestCase):
    def setUp(self):
        self.co = Company.objects.create(code='ADIC', name='Alpha Direct')
        self.cat = RecordCategory.objects.create(name='HR files')
        self.rec = RecordItem.objects.create(reference='REC-001', title='Test file',
                                             category=self.cat, company=self.co)
        self.requester = _plain_user('req', self.co)     # can request (granted co)
        self.hc1 = _hc_user('hc1', self.co)              # HC approver A
        self.hc2 = _hc_user('hc2', self.co)              # HC approver B
        self.outsider = _plain_user('out')               # no title, no grant

    def _make(self, user=None, reason='need the file for an audit'):
        return svc.create_request(user=user or self.requester, record=self.rec, reason=reason)

    def test_create_is_pending(self):
        r = self._make()
        self.assertEqual(r.status, RecordFileRequest.Status.PENDING)

    def test_reason_required(self):
        with self.assertRaises(ValidationError):
            self._make(reason='')

    def test_requester_needs_entity_access(self):
        with self.assertRaises(PermissionDenied):
            self._make(user=self.outsider)   # no grant to the record's company

    def test_hc_approves(self):
        r = self._make()
        svc.approve_request(r, self.hc2)
        r.refresh_from_db()
        self.assertEqual(r.status, RecordFileRequest.Status.APPROVED)
        self.assertEqual(r.decided_by, self.hc2)

    def test_sod_cannot_approve_own(self):
        r = self._make(user=self.hc1)          # an HC person submits
        with self.assertRaises(ValidationError):
            svc.approve_request(r, self.hc1)   # …cannot approve their own

    def test_non_hc_cannot_approve(self):
        r = self._make()
        with self.assertRaises(PermissionDenied):
            svc.approve_request(r, self.requester)   # requester is not HC/Records

    def test_deny_requires_note(self):
        r = self._make()
        with self.assertRaises(ValidationError):
            svc.deny_request(r, self.hc2, '')
        svc.deny_request(r, self.hc2, 'Sorry, that file is under legal hold.')
        r.refresh_from_db()
        self.assertEqual(r.status, RecordFileRequest.Status.DENIED)
        self.assertEqual(r.decision_note, 'Sorry, that file is under legal hold.')

    def test_pending_for_approver_scope_and_sod(self):
        self._make(user=self.requester)   # pending #1
        self._make(user=self.hc1)         # pending #2 (hc1's own)
        self.assertEqual(svc.pending_for_approver(self.hc2).count(), 2)   # sees both
        self.assertEqual(svc.pending_for_approver(self.hc1).count(), 1)   # not own
        self.assertEqual(svc.pending_for_approver(self.outsider).count(), 0)  # not HC


class FileRequestApiTests(TestCase):
    def setUp(self):
        self.co = Company.objects.create(code='ADIC', name='Alpha Direct')
        self.cat = RecordCategory.objects.create(name='HR files')
        self.rec = RecordItem.objects.create(reference='REC-9', title='F', category=self.cat, company=self.co)
        self.su1 = User.objects.create_superuser('boss1', 'boss1@example.com', 'x')
        self.su2 = User.objects.create_superuser('boss2', 'boss2@example.com', 'x')

    def test_create_then_approve_via_api(self):
        c1 = APIClient(); c1.force_authenticate(self.su1)
        r = c1.post('/api/v1/records/file-requests/',
                    {'record_id': str(self.rec.id), 'reason': 'audit review please'}, format='json')
        self.assertEqual(r.status_code, 201)
        rid = r.data['request']['id']
        c2 = APIClient(); c2.force_authenticate(self.su2)   # different approver (SoD)
        r2 = c2.post(f'/api/v1/records/file-requests/{rid}/approve/', {}, format='json')
        self.assertEqual(r2.status_code, 200)
        self.assertEqual(RecordFileRequest.objects.get(id=rid).status, 'approved')

    def test_self_approve_blocked_via_api(self):
        c1 = APIClient(); c1.force_authenticate(self.su1)
        rid = c1.post('/api/v1/records/file-requests/',
                      {'record_id': str(self.rec.id), 'reason': 'audit review please'},
                      format='json').data['request']['id']
        r2 = c1.post(f'/api/v1/records/file-requests/{rid}/approve/', {}, format='json')
        self.assertEqual(r2.status_code, 400)   # SoD


class FileRequestEntityScopeTests(TestCase):
    """H33 — an HC approver granted entity A must not see or decide a file request
    on entity B's record. Mirrors payroll AdditionEntityScopeTests; this class goes
    red if the _in_scope / record__company_id clamp is dropped (Fable K6)."""

    def setUp(self):
        self.a = Company.objects.create(code='ADIC', name='Alpha Direct')
        self.b = Company.objects.create(code='ADSA', name='Alpha SA')
        self.cat = RecordCategory.objects.create(name='HR files')
        self.rec_b = RecordItem.objects.create(reference='REC-B', title='B file',
                                               category=self.cat, company=self.b)
        # A requester granted entity B raises a pending request on B's record.
        self.reqB = _plain_user('reqb', self.b)
        self.reqB_req = svc.create_request(user=self.reqB, record=self.rec_b,
                                           reason='need the B file for an audit')
        # An HC approver granted ONLY entity A.
        self.hcA = _hc_user('hca', self.a)

    def test_A_scoped_approver_does_not_see_B_request(self):
        self.assertEqual(svc.pending_for_approver(self.hcA).count(), 0)

    def test_A_scoped_approver_cannot_decide_B_request(self):
        c = APIClient(); c.force_authenticate(self.hcA)
        self.assertEqual(
            c.post(f'/api/v1/records/file-requests/{self.reqB_req.id}/approve/', {}, format='json').status_code, 403)
        self.assertEqual(
            c.post(f'/api/v1/records/file-requests/{self.reqB_req.id}/deny/', {'note': 'no'}, format='json').status_code, 403)
