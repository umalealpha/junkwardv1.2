"""
core/tests/test_readiness_gates_2026_08_25.py

The permission and wiring gates from the Manus nine-area retest (2026-08-25):

  P1a  FNB statement pull / batch refresh / connection test were IsAuthenticated
  P1b  bulk vendor upload was IsAuthenticated, and ignored entity write access
  P1c  BankRecRuleViewSet was never registered, so rule CRUD was unreachable

Every assertion here fails on the pre-fix code — the 403s come back 200/201/400,
and the bank-rec-rules route 404s.
"""
import io

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import NoReverseMatch, reverse
from rest_framework.test import APIClient

from core.models import Company, UserCompanyAccess, UserProfile


class FnbReadEndpointGateTest(TestCase):
    """The bank feeds carry full statements and the bank's reject reasons."""

    @classmethod
    def setUpTestData(cls):
        cls.ops = User.objects.create_user('ops', email='ops@x.co', password='x')
        # NOTE: UserProfile.title DEFAULTS to ACCOUNTANT, which is inside
        # FINANCIALS_VIEW_TITLES — so a profile created with no title is finance
        # staff, not operational staff. Name it, or the test proves nothing.
        UserProfile.objects.create(user=cls.ops, title=UserProfile.Title.OPERATIONS)
        cls.cfo = User.objects.create_user('cfo', email='cfo@x.co', password='x')
        UserProfile.objects.create(user=cls.cfo, title=UserProfile.Title.CFO)

    def setUp(self):
        self.client = APIClient()

    def test_operational_staff_cannot_pull_a_bank_statement(self):
        self.client.force_authenticate(user=self.ops)
        r = self.client.post('/api/v1/fnb/pull-statements/', {}, format='json')
        self.assertEqual(r.status_code, 403, r.content)

    def test_operational_staff_cannot_test_the_bank_connection(self):
        self.client.force_authenticate(user=self.ops)
        r = self.client.post('/api/v1/fnb/test-connection/', {}, format='json')
        self.assertEqual(r.status_code, 403, r.content)

    def test_the_cfo_is_past_the_gate(self):
        """Proves the gate refuses on ROLE, not on everything. The CFO gets past
        permissions and fails later on the missing bank account / FNB config —
        anything other than 403 means the gate let them through."""
        self.client.force_authenticate(user=self.cfo)
        r = self.client.post('/api/v1/fnb/pull-statements/', {}, format='json')
        self.assertEqual(r.status_code, 400, r.content)   # past perms, missing id

    def test_the_status_badge_stays_open_to_ordinary_staff(self):
        """FNBStatusView returns config booleans and counts only — no account
        numbers, no amounts. Tightening it would break the /banking/fnb page
        badge for no security gain, so this asserts it was NOT tightened."""
        self.client.force_authenticate(user=self.ops)
        r = self.client.get('/api/v1/fnb/status/')
        self.assertEqual(r.status_code, 200, r.content)


class BulkVendorUploadGateTest(TestCase):
    """`billing.contact_upload.cfo_upload_vendors` — bulk vendor/customer master.

    NOT reachable by URL: the view was written (CFO directive 2026-05-18) but
    never wired into any urlpatterns, exactly like BankRecRuleViewSet was. So
    the live exposure Manus reported is theoretical rather than active — but the
    writer is in the tree and one router line away from being live, which is how
    an ungated writer gets switched on by accident later. Gated now, and tested
    by calling the view directly rather than by routing it (routing it would
    widen the surface, not fix anything).
    """

    @classmethod
    def setUpTestData(cls):
        cls.adic = Company.objects.create(code='ADIC', name='Alpha Direct')
        cls.ops = User.objects.create_user('ops2', email='ops2@x.co', password='x')
        UserProfile.objects.create(user=cls.ops, title=UserProfile.Title.OPERATIONS)
        cls.admin = User.objects.create_user('admin2', email='admin2@x.co', password='x')
        UserProfile.objects.create(user=cls.admin, is_administrator=True)

    @staticmethod
    def _csv(name=b'Acme Supplies'):
        f = io.BytesIO(b'name,email\n' + name + b',acme@x.co\n')
        f.name = 'vendors.csv'
        return f

    def _post(self, user, **data):
        from rest_framework.test import APIRequestFactory, force_authenticate
        from billing.contact_upload import cfo_upload_vendors
        req = APIRequestFactory().post('/cfo-upload-vendors/', data,
                                       format='multipart')
        force_authenticate(req, user=user)
        return cfo_upload_vendors(req)

    def _get_template(self, user):
        from rest_framework.test import APIRequestFactory, force_authenticate
        from billing.contact_upload import cfo_upload_vendors_template
        req = APIRequestFactory().get('/cfo-upload-vendors/template/')
        force_authenticate(req, user=user)
        return cfo_upload_vendors_template(req)

    def test_ordinary_staff_cannot_bulk_upload_vendors(self):
        r = self._post(self.ops, file=self._csv(), company='ADIC', commit='true')
        self.assertEqual(r.status_code, 403, r.data)

    def test_ordinary_staff_cannot_even_fetch_the_template(self):
        r = self._get_template(self.ops)
        self.assertEqual(r.status_code, 403, r.data)

    def test_an_authorised_uploader_can_commit_and_it_is_audited(self):
        from billing.models import Contact
        from core.models import AuditLog
        r = self._post(self.admin, file=self._csv(), company='ADIC', commit='true')
        self.assertEqual(r.status_code, 200, r.data)
        self.assertTrue(Contact.objects.filter(company=self.adic,
                                               name='Acme Supplies').exists())
        self.assertEqual(
            AuditLog.objects.filter(table_name='billing.Contact',
                                    user=self.admin).count(), 1)

    def test_a_dry_run_writes_nothing_and_leaves_no_audit_row(self):
        from billing.models import Contact
        from core.models import AuditLog
        r = self._post(self.admin, file=self._csv(), company='ADIC')
        self.assertEqual(r.status_code, 200, r.data)
        self.assertFalse(Contact.objects.filter(name='Acme Supplies').exists())
        self.assertEqual(
            AuditLog.objects.filter(table_name='billing.Contact').count(), 0)


class BulkVendorUploadEntityScopeTest(TestCase):
    """Holding the upload right is not the same as being allowed every entity.

    The company arrives as a request parameter, so without this check an
    authorised uploader scoped to ADSA could load suppliers straight into ADIC.
    """

    @classmethod
    def setUpTestData(cls):
        cls.adic = Company.objects.create(code='ADIC', name='Alpha Direct')
        cls.adsa = Company.objects.create(code='ADSA', name='Alpha Direct SA')
        cls.uploader = User.objects.create_user('up', email='up@x.co', password='x')
        # Not an administrator and not the CFO, so allowed_company_ids is the
        # explicit grant list rather than '*'.
        UserProfile.objects.create(user=cls.uploader,
                                   title=UserProfile.Title.ACCOUNTANT)
        UserCompanyAccess.objects.create(user=cls.uploader, company=cls.adsa,
                                         can_view=True, can_write=True)
        UserCompanyAccess.objects.create(user=cls.uploader, company=cls.adic,
                                         can_view=True, can_write=False)
        from core.models import Permission, Role, UserRoleAssignment
        perm, _ = Permission.objects.get_or_create(
            code='cfo-upload', defaults={'category': 'uploads',
                                         'description': 'CFO bulk upload'})
        role, _ = Role.objects.get_or_create(
            code='BULK_UPLOADER', defaults={'name': 'Bulk Uploader', 'level': 3})
        role.permissions.add(perm)
        UserRoleAssignment.objects.create(user=cls.uploader, role=role)

    @staticmethod
    def _csv():
        f = io.BytesIO(b'name\nScoped Supplier\n')
        f.name = 'v.csv'
        return f

    def _post(self, company):
        from rest_framework.test import APIRequestFactory, force_authenticate
        from billing.contact_upload import cfo_upload_vendors
        req = APIRequestFactory().post(
            '/cfo-upload-vendors/',
            {'file': self._csv(), 'company': company, 'commit': 'true'},
            format='multipart')
        force_authenticate(req, user=self.uploader)
        return cfo_upload_vendors(req)

    def test_the_upload_role_alone_gets_past_the_permission_gate(self):
        """Proves the entity refusal below is about ENTITY, not about the role."""
        r = self._post('ADSA')
        self.assertNotEqual(r.status_code, 403, r.data)

    def test_read_only_entity_access_is_refused(self):
        r = self._post('ADIC')
        self.assertEqual(r.status_code, 403, r.data)
        self.assertIn('ADIC', r.data.get('detail', ''))

    def test_write_entity_access_is_allowed(self):
        from billing.models import Contact
        r = self._post('ADSA')
        self.assertEqual(r.status_code, 200, r.data)
        self.assertTrue(Contact.objects.filter(company=self.adsa,
                                               name='Scoped Supplier').exists())


class BankRecRuleRouteTest(TestCase):
    """BankRecRuleViewSet existed but was never registered on the router, so
    reconciliation-rule CRUD has been unreachable since the feature shipped."""

    @classmethod
    def setUpTestData(cls):
        cls.ops = User.objects.create_user('ops3', email='ops3@x.co', password='x')
        UserProfile.objects.create(user=cls.ops, title=UserProfile.Title.OPERATIONS)
        cls.cfo = User.objects.create_user('cfo3', email='cfo3@x.co', password='x')
        UserProfile.objects.create(user=cls.cfo, title=UserProfile.Title.CFO)

    def test_the_route_is_registered(self):
        try:
            url = reverse('bank-rec-rule-list')
        except NoReverseMatch:                              # pragma: no cover
            self.fail('bank-rec-rules is not registered on the API router')
        self.assertTrue(url.endswith('/bank-rec-rules/'), url)

    def test_finance_can_list_rules(self):
        c = APIClient()
        c.force_authenticate(user=self.cfo)
        r = c.get('/api/v1/bank-rec-rules/')
        self.assertEqual(r.status_code, 200, r.content)

    def test_operational_staff_cannot(self):
        c = APIClient()
        c.force_authenticate(user=self.ops)
        r = c.get('/api/v1/bank-rec-rules/')
        self.assertEqual(r.status_code, 403, r.content)
