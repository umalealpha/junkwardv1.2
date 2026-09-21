"""
Confidentiality of personal HR documents — Development Dialogues (CFO 2026-07-18).

A review may be seen ONLY by: the employee it is about, their direct line
manager, the CEO / CFO / COO (executive tier), and HR. Everyone else — including
other employees and other managers — is refused. These tests lock that rule.
"""
from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from rest_framework.test import APIClient

from core.models import Company, Currency, UserProfile
from hris.document_access import can_access_hr_document, is_hr_doc_admin, visible_hr_documents
from hris.models import HRDocument, HRISProfile
from payroll.models import Employee


def _dd(profile, category='development_dialogue', name='Alice Molefe'):
    return HRDocument.objects.create(
        title=f'Development Dialogue 2025 — {name}', category=category,
        is_personal=True, employee=profile, employee_name=name,
        file=SimpleUploadedFile('dd.xlsx', b'binary-review-data'))


class DocumentAccessTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        Currency.objects.get_or_create(code='BWP', defaults={'name': 'Botswana Pula', 'symbol': 'P'})
        cls.co = Company.objects.create(code='ADIC', name='Alpha Direct')

        # manager
        cls.mgr_u = User.objects.create_user('mgr', email='mgr@ad.co.bw', password='x')
        cls.mgr_e = Employee.objects.create(employee_number='M1', full_name='Manager One',
                                            company=cls.co, user=cls.mgr_u)
        HRISProfile.objects.create(employee=cls.mgr_e)

        # subject (Alice) — reports to manager
        cls.alice_u = User.objects.create_user('alice', email='alice@ad.co.bw', password='x')
        cls.alice_e = Employee.objects.create(employee_number='E1', full_name='Alice Molefe',
                                              company=cls.co, user=cls.alice_u)
        cls.alice_hp = HRISProfile.objects.create(employee=cls.alice_e, manager=cls.mgr_e)

        # an unrelated employee
        cls.bob_u = User.objects.create_user('bob', email='bob@ad.co.bw', password='x')
        cls.bob_e = Employee.objects.create(employee_number='E2', full_name='Bob N',
                                            company=cls.co, user=cls.bob_u)
        HRISProfile.objects.create(employee=cls.bob_e)

        # HR + the three named GROUP C-suite (CFO directive 2026-07-18).
        # C-suite is matched by exact account (local-part), NOT by title:
        # Arun=CEO aiyer@, Arjun=COO arjuniyer@ (his omni title is 'operations'),
        # Prathap=CFO pganesharajah@.
        cls.hr_u = User.objects.create_user('hr', email='hr@ad.co.bw', password='x')
        UserProfile.objects.create(user=cls.hr_u, title=UserProfile.Title.HR_MANAGER, is_active=True)
        cls.cfo_u = User.objects.create_user('pganesharajah', email='pganesharajah@ad.co.bw', password='x')
        UserProfile.objects.create(user=cls.cfo_u, title=UserProfile.Title.CFO, is_active=True)
        cls.ceo_u = User.objects.create_user('aiyer', email='aiyer@ad.co.bw', password='x')
        # COO carries the plain 'operations' title in prod — proves C-suite is
        # keyed on the account, not a title.
        cls.exec_u = User.objects.create_user('arjuniyer', email='arjuniyer@ad.co.bw', password='x')
        UserProfile.objects.create(user=cls.exec_u, title=UserProfile.Title.OPERATIONS, is_active=True)

        cls.doc = _dd(cls.alice_hp)

    # ---- the access predicate ----
    def test_subject_and_manager_and_leadership_can_access(self):
        for u in (self.alice_u, self.mgr_u, self.hr_u, self.cfo_u, self.ceo_u, self.exec_u):
            self.assertTrue(can_access_hr_document(u, self.doc), u.username)

    def test_other_employee_cannot_access(self):
        self.assertFalse(can_access_hr_document(self.bob_u, self.doc))

    def test_generic_admin_or_finance_manager_is_not_elevated(self):
        # Kago's case (CFO 2026-07-18): an omni administrator / Finance manager
        # is NOT the C-suite or HR — must NOT see everyone's review.
        kago = User.objects.create_user('kago', email='kago@ad.co.bw', password='x')
        UserProfile.objects.create(user=kago, title=UserProfile.Title.FINANCE_MANAGER,
                                   is_active=True, is_administrator=True)
        self.assertFalse(is_hr_doc_admin(kago))
        self.assertFalse(can_access_hr_document(kago, self.doc))
        self.assertEqual(visible_hr_documents(kago).count(), 0)

    def test_readonly_executive_title_is_not_csuite(self):
        # Finding, Fable 2026-07-18: the read-only 'Executive' title is NOT the
        # C-suite. In prod pbeka@ holds it but is not CEO/COO/CFO — must NOT see
        # everyone's review. Likewise a stray/external 'CFO' *title* (e.g.
        # siddharth@cuberoute) does not grant sight; only the named accounts do.
        pbeka = User.objects.create_user('pbeka', email='pbeka@ad.co.bw', password='x')
        UserProfile.objects.create(user=pbeka, title=UserProfile.Title.EXECUTIVE, is_active=True)
        self.assertFalse(is_hr_doc_admin(pbeka))
        self.assertFalse(can_access_hr_document(pbeka, self.doc))
        self.assertEqual(visible_hr_documents(pbeka).count(), 0)

        ext_cfo = User.objects.create_user('sid', email='siddharth@cuberoute.co.bw', password='x')
        UserProfile.objects.create(user=ext_cfo, title=UserProfile.Title.CFO, is_active=True)
        self.assertFalse(is_hr_doc_admin(ext_cfo))

    def test_hr_role_grant_must_be_live(self):
        # An HRIS/HR_MANAGER *role assignment* confers full sight — but only
        # while LIVE. A revoked, expired, or inactive-role grant must not keep
        # an ex-HR staffer or lapsed break-glass account reading every review.
        # (Fable review 2026-07-18; DPA-relevant.)
        from django.utils import timezone
        from datetime import timedelta
        from core.models import Role, UserRoleAssignment
        role, _ = Role.objects.get_or_create(code='HRIS', defaults={'name': 'HRIS', 'level': 5})
        exhr = User.objects.create_user('exhr', email='exhr@ad.co.bw', password='x')

        live = UserRoleAssignment.objects.create(user=exhr, role=role)
        self.assertTrue(is_hr_doc_admin(exhr))                       # live grant → admin
        self.assertTrue(can_access_hr_document(exhr, self.doc))

        live.revoked_at = timezone.now(); live.save()
        exhr._active_role_assignments = None                         # bust request cache
        self.assertFalse(is_hr_doc_admin(exhr))                      # revoked → refused
        self.assertEqual(visible_hr_documents(exhr).count(), 0)

        live.revoked_at = None
        live.expires_at = timezone.now() - timedelta(days=1); live.save()
        exhr._active_role_assignments = None
        self.assertFalse(is_hr_doc_admin(exhr))                      # expired → refused

        live.expires_at = None; live.save()
        role.is_active = False; role.save()
        exhr._active_role_assignments = None
        self.assertFalse(is_hr_doc_admin(exhr))                      # role deactivated → refused

    def test_leadership_flags(self):
        self.assertTrue(is_hr_doc_admin(self.hr_u))
        self.assertTrue(is_hr_doc_admin(self.cfo_u))
        self.assertTrue(is_hr_doc_admin(self.ceo_u))
        self.assertTrue(is_hr_doc_admin(self.exec_u))
        self.assertFalse(is_hr_doc_admin(self.alice_u))
        self.assertFalse(is_hr_doc_admin(self.mgr_u))

    def test_non_dd_category_not_self_visible(self):
        # a disciplinary doc for Alice: Alice must NOT reach it; HR still can.
        disc = _dd(self.alice_hp, category='disciplinary')
        self.assertFalse(can_access_hr_document(self.alice_u, disc))
        self.assertTrue(can_access_hr_document(self.hr_u, disc))

    def test_unlinked_doc_is_hr_only(self):
        orphan = HRDocument.objects.create(
            title='DD (unmatched)', category='development_dialogue', is_personal=True,
            file=SimpleUploadedFile('x.xlsx', b'data'))   # employee=None
        self.assertFalse(can_access_hr_document(self.alice_u, orphan))
        self.assertTrue(can_access_hr_document(self.hr_u, orphan))

    # ---- the queryset scoping ----
    def test_visible_scoping(self):
        self.assertEqual(list(visible_hr_documents(self.alice_u)), [self.doc])      # own
        self.assertEqual(list(visible_hr_documents(self.mgr_u)), [self.doc])        # report's
        self.assertEqual(list(visible_hr_documents(self.bob_u)), [])               # nothing
        self.assertIn(self.doc, visible_hr_documents(self.hr_u))                    # all

    # ---- the HTTP endpoints enforce it ----
    def _c(self, u):
        c = APIClient(); c.force_authenticate(user=u); return c

    def test_download_endpoint_enforces_access(self):
        url = f'/api/v1/hris/documents/{self.doc.id}/download/'
        self.assertEqual(self._c(self.alice_u).get(url).status_code, 200)   # self
        self.assertEqual(self._c(self.mgr_u).get(url).status_code, 200)     # manager
        self.assertEqual(self._c(self.hr_u).get(url).status_code, 200)      # HR
        self.assertEqual(self._c(self.bob_u).get(url).status_code, 403)     # outsider

    def test_my_documents_endpoint_scoped(self):
        r = self._c(self.alice_u).get('/api/v1/hris/my-documents/')
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()['count'], 1)
        self.assertEqual(self._c(self.bob_u).get('/api/v1/hris/my-documents/').json()['count'], 0)
