"""Phase 1 — call-centre claim intake + the case register.

The point of the feature: a matter is opened and allocated to a firm the moment
the member calls, instead of only surfacing when the firm's first bill lands. And
a call-centre agent can do that WITHOUT being handed the BONU financials.
"""
import datetime
from unittest.mock import patch

from django.contrib.auth.models import User
from django.test import TestCase
from rest_framework.test import APIRequestFactory, force_authenticate

from bonu.cases import case_detail, case_event_add, cases
from bonu.models import CaseEvent, LawFirm, LegalCase
from bonu.views import dashboard
from core.models import Company, UserProfile

CALL_CENTRE = 'agent.desk@alphadirect.co.bw'


class ClaimIntakeTests(TestCase):
    def setUp(self):
        self.company = (Company.objects.filter(code='ADIC').first()
                        or Company.objects.create(code='ADIC', name='ADIC'))
        self.firm = LawFirm.objects.create(name='Test & Co', is_active=True)
        self.other_firm = LawFirm.objects.create(name='Second & Co', is_active=True)
        self.rf = APIRequestFactory()

    def _user(self, email, title=None, superuser=False):
        if superuser:
            u = User.objects.create_superuser(email.split('@')[0], email, 'x')
        else:
            u = User.objects.create_user(email.split('@')[0], email=email, password='x')
            if title:
                UserProfile.objects.update_or_create(
                    user=u, defaults={'title': title, 'is_active': True})
        return User.objects.get(pk=u.pk)

    def _post(self, view, body, user, **kw):
        req = self.rf.post('/api/v1/bonu/cases/', body, format='json')
        force_authenticate(req, user=user)
        return view(req, **kw)

    def _get(self, view, user, qs='', **kw):
        req = self.rf.get('/api/v1/bonu/cases/' + qs)
        force_authenticate(req, user=user)
        return view(req, **kw)

    # ---- the core capability -------------------------------------------------
    def test_finance_opens_a_case_and_it_carries_a_first_event(self):
        acct = self._user('acc@alphadirect.co.bw', UserProfile.Title.ACCOUNTANT)
        resp = self._post(cases, {'firm_id': str(self.firm.pk), 'member_ref': 'BONU-0001',
                                  'matter_type': 'divorce', 'description': 'Member phoned in.'}, acct)
        self.assertEqual(resp.status_code, 201, resp.data)
        c = LegalCase.objects.get()
        self.assertEqual(c.firm_id, self.firm.pk)          # allocation
        self.assertEqual(c.member_ref, 'BONU-0001')
        self.assertEqual(c.status, LegalCase.Status.INSTRUCTED)
        self.assertEqual(c.last_activity_on, c.instructed_on)
        self.assertEqual(c.events.count(), 1)              # opening is a provable event
        self.assertEqual(c.events.first().kind, CaseEvent.Kind.INSTRUCTED)

    def test_case_ref_is_auto_generated_when_blank(self):
        acct = self._user('acc@alphadirect.co.bw', UserProfile.Title.ACCOUNTANT)
        resp = self._post(cases, {'firm_id': str(self.firm.pk), 'member_ref': 'M1'}, acct)
        self.assertEqual(resp.status_code, 201, resp.data)
        self.assertTrue(resp.data['case']['case_ref'].startswith('BL-'), resp.data)

    def test_duplicate_firm_and_ref_is_refused(self):
        acct = self._user('acc@alphadirect.co.bw', UserProfile.Title.ACCOUNTANT)
        body = {'firm_id': str(self.firm.pk), 'member_ref': 'M1', 'case_ref': 'CV/1/2026'}
        self.assertEqual(self._post(cases, body, acct).status_code, 201)
        self.assertEqual(self._post(cases, body, acct).status_code, 409)

    def test_a_ref_race_answers_409_not_500(self):
        """Two intake requests for the same (firm, case_ref): the pre-check can read
        False for both, so the DB constraint is the real guard — and it must surface
        as a graceful 409, never an unhandled IntegrityError 500."""
        acct = self._user('acc@alphadirect.co.bw', UserProfile.Title.ACCOUNTANT)
        body = {'firm_id': str(self.firm.pk), 'member_ref': 'M1', 'case_ref': 'RACE/1'}
        self.assertEqual(self._post(cases, body, acct).status_code, 201)
        # Force the pre-check to miss the existing row, exactly as a race would.
        with patch('django.db.models.query.QuerySet.exists', return_value=False):
            resp = self._post(cases, body, acct)
        self.assertEqual(resp.status_code, 409, resp.data)

    def test_firm_and_member_ref_are_required(self):
        acct = self._user('acc@alphadirect.co.bw', UserProfile.Title.ACCOUNTANT)
        self.assertEqual(self._post(cases, {'member_ref': 'M1'}, acct).status_code, 400)
        self.assertEqual(self._post(cases, {'firm_id': str(self.firm.pk)}, acct).status_code, 400)

    def test_unknown_matter_type_falls_back_to_other(self):
        acct = self._user('acc@alphadirect.co.bw', UserProfile.Title.ACCOUNTANT)
        resp = self._post(cases, {'firm_id': str(self.firm.pk), 'member_ref': 'M1',
                                  'matter_type': 'nonsense'}, acct)
        self.assertEqual(resp.status_code, 201, resp.data)
        self.assertEqual(resp.data['case']['matter_type'], 'other')

    def test_an_event_moves_last_activity_and_first_action(self):
        acct = self._user('acc@alphadirect.co.bw', UserProfile.Title.ACCOUNTANT)
        created = self._post(cases, {'firm_id': str(self.firm.pk), 'member_ref': 'M1',
                                     'instructed_on': '2026-08-01'}, acct)
        cid = created.data['case']['id']
        resp = self._post(case_event_add, {'kind': 'letter', 'happened_on': '2026-08-15',
                                           'detail': 'Demand letter sent.'}, acct, case_id=cid)
        self.assertEqual(resp.status_code, 201, resp.data)
        c = LegalCase.objects.get(pk=cid)
        self.assertEqual(c.last_activity_on, datetime.date(2026, 8, 15))
        self.assertEqual(c.first_action_on, datetime.date(2026, 8, 15))
        self.assertEqual(c.events.count(), 2)

    def test_status_change_closes_the_case(self):
        acct = self._user('acc@alphadirect.co.bw', UserProfile.Title.ACCOUNTANT)
        cid = self._post(cases, {'firm_id': str(self.firm.pk), 'member_ref': 'M1'}, acct).data['case']['id']
        resp = self._post(case_detail, {'status': 'settled'}, acct, case_id=cid)
        self.assertEqual(resp.status_code, 200, resp.data)
        c = LegalCase.objects.get(pk=cid)
        self.assertFalse(c.is_open)
        self.assertIsNotNone(c.closed_on)

    def test_list_carries_the_form_dropdowns(self):
        acct = self._user('acc@alphadirect.co.bw', UserProfile.Title.ACCOUNTANT)
        resp = self._get(cases, acct)
        self.assertEqual(resp.status_code, 200)
        meta = resp.data['meta']
        self.assertTrue(any(f['id'] == str(self.firm.pk) for f in meta['firms']))
        self.assertTrue(meta['matter_types'] and meta['statuses'] and meta['event_kinds'])

    # ---- the access separation — the whole reason for a narrow gate ----------
    def test_a_garbage_firm_filter_does_not_500(self):
        acct = self._user('acc@alphadirect.co.bw', UserProfile.Title.ACCOUNTANT)
        self.assertEqual(self._get(cases, acct, qs='?firm_id=not-a-uuid').status_code, 200)

    def test_reallocating_onto_an_existing_ref_is_409(self):
        acct = self._user('acc@alphadirect.co.bw', UserProfile.Title.ACCOUNTANT)
        self._post(cases, {'firm_id': str(self.firm.pk), 'member_ref': 'M', 'case_ref': 'CV/9'}, acct)
        made = self._post(cases, {'firm_id': str(self.other_firm.pk), 'member_ref': 'M', 'case_ref': 'CV/9'}, acct)
        cid = made.data['case']['id']  # on other_firm
        r = self._post(case_detail, {'firm_id': str(self.firm.pk)}, acct, case_id=cid)  # collides with firm's CV/9
        self.assertEqual(r.status_code, 409, r.data)

    def test_a_plain_operations_agent_is_refused(self):
        rando = self._user('rando@alphadirect.co.bw', UserProfile.Title.OPERATIONS)
        self.assertEqual(self._get(cases, rando).status_code, 403)
        self.assertEqual(self._post(cases, {'firm_id': str(self.firm.pk), 'member_ref': 'M'}, rando).status_code, 403)

    def test_a_named_call_centre_agent_opens_cases_but_sees_no_financials(self):
        import os
        os.environ['BONU_INTAKE_EMAILS'] = CALL_CENTRE
        try:
            agent = self._user(CALL_CENTRE, UserProfile.Title.OPERATIONS)
            # intake: allowed
            opened = self._post(cases, {'firm_id': str(self.firm.pk), 'member_ref': 'M9'}, agent)
            self.assertEqual(opened.status_code, 201, opened.data)
            # financial dashboard: refused — the separation is the point
            req = self.rf.get('/api/v1/bonu/dashboard/')
            force_authenticate(req, user=agent)
            self.assertEqual(dashboard(req).status_code, 403)
        finally:
            os.environ.pop('BONU_INTAKE_EMAILS', None)
