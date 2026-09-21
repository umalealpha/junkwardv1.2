"""Risk register — label rename + Excel upload (Unopa Male, 2026-09-18).

Each test proves ONE promise. Revert the fix and the corresponding test
must go red.

The rename is UI-only, but two backend guarantees underpin it:

  * Risk ID is auto-generated. The frontend now sends `ref=''` on a new
    row and it must not fail the CharField unique index at ''.
  * The Excel upload maps five columns (Division / Source, Risk Category,
    Risk Description, Risk Owner, Action / Mitigation Plan) and ignores
    the rest. Risk ID column is IGNORED — Omni always assigns its own.
"""
from __future__ import annotations

import io

from django.contrib.auth import get_user_model
from rest_framework.test import APITestCase

from iso_compliance.models import Risk

User = get_user_model()


def _officer():
    u = User.objects.create_user(username='unopa', email='umale@alphadirect.co.bw',
                                 password='x', is_staff=True, is_superuser=True)
    return u


class RiskAutoRefTests(APITestCase):

    def setUp(self):
        self.client.force_authenticate(user=_officer())

    def test_new_risk_gets_an_auto_ref(self):
        """Frontend now hides Risk ID; server must fill it in."""
        r = self.client.post('/api/v1/iso/risks/', {
            'title': 'Ops',
            'description': 'Server room flooded because rain drain blocked.',
        }, format='json')
        self.assertEqual(r.status_code, 201, r.content)
        self.assertEqual(r.data['ref'], 'R-001')

    def test_ref_from_client_is_ignored(self):
        """A caller trying to fix the ref cannot — the auto counter wins."""
        Risk.objects.create(ref='R-005', title='seed', description='x')
        r = self.client.post('/api/v1/iso/risks/', {
            'ref': 'HACKED-42', 'title': 'Ops', 'description': 'y',
        }, format='json')
        self.assertEqual(r.status_code, 201, r.content)
        self.assertEqual(r.data['ref'], 'R-006')


class RiskImportTests(APITestCase):

    def setUp(self):
        self.client.force_authenticate(user=_officer())

    def _csv(self, text: str):
        return io.BytesIO(text.encode('utf-8'))

    def test_import_maps_the_five_columns(self):
        csv = (
            'Risk ID,Division / Source,Risk Category,Risk Description,'
            'Risk Owner,Action / Mitigation Plan,Target Date,Remarks\n'
            'IGN-1,Finance,Operational,'
            '"Manual payments approved with no dual sign-off.",'
            'CFO,Enable dual authorisation in FNB.,2026-12-31,notes here\n'
        )
        f = self._csv(csv); f.name = 'risks.csv'
        r = self.client.post('/api/v1/iso/risks/import/', {'file': f})
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(r.data['created'], 1)
        risk = Risk.objects.get()
        # Risk ID column is IGNORED — server assigns.
        self.assertEqual(risk.ref, 'R-001')
        self.assertEqual(risk.title, 'Finance')
        self.assertEqual(risk.asset, 'Operational')
        self.assertIn('dual sign-off', risk.description)
        self.assertEqual(risk.owner, 'CFO')
        self.assertIn('dual authorisation', risk.treatment_plan)

    def test_import_skips_rows_with_no_description(self):
        csv = (
            'Division / Source,Risk Category,Risk Description,Risk Owner,Action / Mitigation Plan\n'
            'IT,Cyber,,IT Lead,\n'
            'Ops,Fraud,Cash handled without receipts,Ops Manager,Petty-cash log required\n'
        )
        f = self._csv(csv); f.name = 'risks.csv'
        r = self.client.post('/api/v1/iso/risks/import/', {'file': f})
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(r.data['created'], 1)
        self.assertEqual(r.data['skipped'], 1)

    def test_import_needs_a_description_column(self):
        csv = 'Ref,Title\nR-001,ok\n'
        f = self._csv(csv); f.name = 'risks.csv'
        r = self.client.post('/api/v1/iso/risks/import/', {'file': f})
        self.assertEqual(r.status_code, 400, r.content)

    def test_import_needs_a_file(self):
        r = self.client.post('/api/v1/iso/risks/import/', {})
        self.assertEqual(r.status_code, 400, r.content)


class RiskImportPermissionTests(APITestCase):
    """The compliance officer may upload the register, not only a superuser.

    kbotana (bug d5388386, 2026-09-18) could add a risk one at a time and got
    "Forbidden." on the spreadsheet: `import_risks` gated on `_can_run_audit`
    while the CRUD viewset used `_RiskRWPermission`. Every test above runs as
    a superuser, so nothing caught it. These run as the real officer.
    """

    CSV = (
        'Division / Source,Risk Category,Risk Description,Risk Owner,'
        'Action / Mitigation Plan\n'
        'Compliance,Regulatory,Late FIA filing,MLRO,Calendar reminder\n'
    )

    @staticmethod
    def _officer(username, department):
        from core.models import UserProfile
        u = User.objects.create_user(username,
                                     email=f'{username}@alphadirect.co.bw')
        UserProfile.objects.create(user=u, role=UserProfile.Role.OPERATIONS_STAFF,
                                   department=department, is_active=True)
        return u

    def _upload(self, user):
        f = io.BytesIO(self.CSV.encode()); f.name = 'risks.csv'
        self.client.force_authenticate(user=user)
        return self.client.post('/api/v1/iso/risks/import/', {'file': f})

    def test_compliance_officer_may_upload_the_register(self):
        r = self._upload(self._officer('kb-compliance', 'Compliance'))
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(r.data['created'], 1)
        self.assertEqual(Risk.objects.count(), 1)

    def test_upload_right_matches_the_right_to_add_one_risk(self):
        """Whoever may POST a single risk may POST the spreadsheet."""
        user = self._officer('kb-parity', 'Risk')
        self.client.force_authenticate(user=user)
        single = self.client.post('/api/v1/iso/risks/', {
            'title': 'Compliance', 'asset': 'Regulatory',
            'description': 'Late FIA filing', 'owner': 'MLRO',
            'likelihood': 3, 'impact': 3,
        }, format='json')
        self.assertIn(single.status_code, (200, 201), single.content)
        self.assertEqual(self._upload(user).status_code, 200)

    def test_an_outsider_still_cannot_upload(self):
        outsider = self._officer('kb-sales', 'Sales')
        self.assertEqual(self._upload(outsider).status_code, 403)


class ComplianceManagerOwnsHerModuleTests(APITestCase):
    """CFO 2026-09-18: "she should be able to do in the compliance areas she is
    the compliance manager."

    Until now SoA / CAPA / policies / evidence / internal audits / management
    reviews were writable only by `_can_run_audit` — superusers plus the `cfo`
    and `iso_auditor` groups, and NEITHER GROUP EXISTS in the database. So the
    Compliance Manager could read her own module and write almost none of it.

    These test the permission object itself, which is the thing that changed.
    An endpoint test here would be hostage to each viewset's own required
    fields and to the SSL-redirect artefact that turns a plain test POST into a
    301; the risk-register tests above already drive a real endpoint end to end.
    """

    @staticmethod
    def _officer(username, department):
        from core.models import UserProfile
        u = User.objects.create_user(username, email=f'{username}@alphadirect.co.bw')
        UserProfile.objects.create(user=u, role=UserProfile.Role.OPERATIONS_STAFF,
                                   department=department, is_active=True)
        return u

    @staticmethod
    def _asks(user, method):
        """Would the shared compliance permission let this through?"""
        from rest_framework.test import APIRequestFactory
        from rest_framework.request import Request
        from iso_compliance.views import _RWPermission
        raw = getattr(APIRequestFactory(), method.lower())('/api/v1/iso/soa/')
        req = Request(raw)
        req.user = user
        return _RWPermission().has_permission(req, None)

    def setUp(self):
        self.mgr = self._officer('kb-mgr', 'Compliance')
        self.outsider = self._officer('kb-sales2', 'Sales')

    def test_the_compliance_manager_may_now_write(self):
        for method in ('POST', 'PUT', 'PATCH'):
            self.assertTrue(self._asks(self.mgr, method),
                            f'{method} still refused to the Compliance Manager')

    def test_she_may_still_not_delete(self):
        """Maintaining a register is adding and correcting, not erasing."""
        self.assertFalse(self._asks(self.mgr, 'DELETE'))

    def test_an_outsider_still_cannot_write(self):
        for method in ('POST', 'PATCH', 'DELETE'):
            self.assertFalse(self._asks(self.outsider, method),
                             f'{method} let an outsider into the compliance records')

    def test_everyone_signed_in_can_still_read(self):
        self.assertTrue(self._asks(self.outsider, 'GET'))

    def test_accepting_a_finding_is_still_the_cfos_call(self):
        """The module calls accept "CFO accepts the risk" — a governance act,
        deliberately NOT included in the 2026-09-18 widening."""
        from iso_compliance.views import (AcceptFindingView, ResolveFindingView,
                                          _can_run_audit, _can_write_compliance_area)
        self.assertFalse(AcceptFindingView.compliance_may_do_it)
        self.assertTrue(ResolveFindingView.compliance_may_do_it)
        # and the two rights really are different for her
        self.assertFalse(_can_run_audit(self.mgr))
        self.assertTrue(_can_write_compliance_area(self.mgr))
