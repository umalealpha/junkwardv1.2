"""
Tests for the AML registers being reachable and writable from the app.

Every test here is aimed at one fact discovered on 2026-09-16: the registers
had shipped a week earlier with no route and no page, so the only way in was
Django admin — and the AML/CFT officer is not a staff user and holds no
groups. Nobody but a superuser could write a single row, while the weekly
objectives counted those rows and reported zero.

So these tests do not merely check that a viewset returns 200. They check the
thing that was actually broken: that the officer accountable for the register
can write to it.
"""
from __future__ import annotations

import datetime as dt

from django.contrib.auth import get_user_model
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from core.models import UserProfile
from iso_compliance.aml_models import SanctionsScreening

User = get_user_model()


def _aml_officer(email='kbotana@alphadirect.co.bw'):
    """The real AML officer shape: active, NOT staff, NO groups, no permissions.

    If this fixture ever gains is_staff or a group, the tests stop testing the
    bug — that shape is precisely what made the registers unwritable.
    """
    u = User.objects.create_user(username='kakale.botana', email=email, password='x')
    u.is_staff = False
    u.is_superuser = False
    u.save()
    UserProfile.objects.update_or_create(
        user=u, defaults={'department': 'Compliance', 'is_active': True})
    return u



def _adic():
    """The register is scoped to Alpha Direct, so a test employee needs a company.

    Before the scoping, these tests created employees with no company at all and
    passed. That is precisely the shape of the 49 phantom rows the scoping
    exists to drop, so they should no longer count.
    """
    from core.models import Company
    return Company.objects.get_or_create(
        code='ADIC', defaults={'name': 'Alpha Direct Insurance'})[0]


class SanctionsRegisterAccessTests(APITestCase):
    """The register must be writable by the officer who owns it."""

    def setUp(self):
        self.officer = _aml_officer()
        self.list_url = '/api/v1/aml/sanctions-screenings/'

    def test_officer_can_record_a_screening(self):
        """THE regression. Before this change there was no route at all, and
        even reaching the model required Django admin, which she cannot open."""
        self.client.force_authenticate(self.officer)
        res = self.client.post(self.list_url, {
            'subject_name': 'Acme Holdings (Pty) Ltd',
            'subject_type': 'supplier',
            'list_source': 'UNSC Consolidated',
            'list_version': '2026-09-12',
            'result': 'clear',
            'pep_status': 'none',
        }, format='json')
        self.assertEqual(res.status_code, status.HTTP_201_CREATED, res.data)
        self.assertEqual(SanctionsScreening.objects.count(), 1)

    def test_the_screening_is_stamped_with_who_ran_it(self):
        """A register that lets you name someone else as the screener is not evidence."""
        self.client.force_authenticate(self.officer)
        self.client.post(self.list_url, {
            'subject_name': 'Acme', 'list_source': 'UNSC', 'list_version': '2026-09-12',
            'screened_by': 99999,          # attempt to attribute it elsewhere
        }, format='json')
        row = SanctionsScreening.objects.get()
        self.assertEqual(row.screened_by_id, self.officer.pk)

    def test_a_screening_without_a_list_version_is_refused(self):
        """A search nobody can repeat cannot be shown to a regulator."""
        self.client.force_authenticate(self.officer)
        res = self.client.post(self.list_url, {
            'subject_name': 'Acme', 'list_source': 'UNSC', 'list_version': '',
        }, format='json')
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('list_version', res.data)

    def test_an_unrelated_employee_cannot_write(self):
        """Widening write access to the compliance function must not mean everyone."""
        outsider = User.objects.create_user(
            username='someone', email='someone@alphadirect.co.bw', password='x')
        UserProfile.objects.update_or_create(
            user=outsider, defaults={'department': 'Claims', 'is_active': True})
        self.client.force_authenticate(outsider)
        res = self.client.post(self.list_url, {
            'subject_name': 'Acme', 'list_source': 'UNSC', 'list_version': '2026-09-12',
        }, format='json')
        self.assertIn(res.status_code,
                      (status.HTTP_403_FORBIDDEN, status.HTTP_401_UNAUTHORIZED))
        self.assertEqual(SanctionsScreening.objects.count(), 0)

    def test_a_confirmed_match_not_reported_to_the_fia_is_surfaced(self):
        """The Act's 'without delay' duty. This is the most serious row the
        register can hold, so it must be visible, not buried in a list."""
        self.client.force_authenticate(self.officer)
        SanctionsScreening.objects.create(
            subject_name='Flagged Party', list_source='UNSC', list_version='2026-09-12',
            result=SanctionsScreening.Result.MATCH, reported_to_fia_at=None)
        res = self.client.get(self.list_url + 'summary/')
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(res.data['awaiting_fia_report'], 1)


class RiskRegisterWriteTests(APITestCase):
    """The risk register had a page all along — the save button refused everyone.

    Writes were gated on `_can_run_audit`, which allows superusers plus the
    groups `cfo` and `iso_auditor`. NEITHER GROUP EXISTS in the database, so in
    practice only a superuser could write, and the register sat empty.
    """

    def setUp(self):
        self.officer = _aml_officer()

    def test_compliance_can_write_a_risk(self):
        self.client.force_authenticate(self.officer)
        res = self.client.post('/api/v1/iso/risks/', {
            'ref': 'R-001',
            'title': 'Sanctions screening is not automated',
            'likelihood': 4, 'impact': 5,
            'owner': 'Kakale Botana',
            'treatment_plan': 'Wire the screening job and switch it on.',
            'status': 'open',
        }, format='json')
        self.assertEqual(res.status_code, status.HTTP_201_CREATED, res.data)

    def test_the_groups_that_gated_this_do_not_need_to_exist(self):
        """Guards the root cause rather than the symptom.

        If someone later re-keys this on a group name, this test goes red the
        moment that group is missing — which is exactly how the original bug
        stayed invisible.
        """
        from django.contrib.auth.models import Group
        self.assertFalse(Group.objects.filter(name__in=['cfo', 'iso_auditor']).exists())
        self.client.force_authenticate(self.officer)
        res = self.client.post('/api/v1/iso/risks/', {
            'ref': 'R-002', 'title': 'Second risk', 'likelihood': 2, 'impact': 2,
            'owner': 'Kakale Botana', 'treatment_plan': 'Plan.', 'status': 'open',
        }, format='json')
        self.assertEqual(res.status_code, status.HTTP_201_CREATED, res.data)


class BoardReportTests(APITestCase):
    def setUp(self):
        self.officer = _aml_officer()
        self.url = '/api/v1/aml/board-reports/'

    def test_cannot_mark_a_report_sent_with_nothing_attached(self):
        """`is_discharged` already refuses to count such a row. Letting the
        status be set anyway would produce a screen saying 'sent' beside a
        counter saying outstanding."""
        self.client.force_authenticate(self.officer)
        res = self.client.post(self.url, {
            'kind': 'aml', 'period_year': 2026, 'period_quarter': 2,
            'title': 'Q2 AML report', 'status': 'sent',
        }, format='json')
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('document', res.data)

    def test_the_dpo_can_file_the_DATA_PROTECTION_report_too(self):
        """The DPO had the same obligation and no screen at all.

        ComplianceReport carries both quarterly reports and separates them by
        `kind` — added 2026-09-09 precisely so the AML pack could not silently
        discharge the DPO's duty. But only the AML side was ever given a way in,
        so the data-protection pack was counted against the officer while
        nothing existed to file it through. Same hole, one kind along.
        """
        from iso_compliance.aml_models import ComplianceReport

        self.client.force_authenticate(self.officer)
        res = self.client.post(self.url, {
            'kind': 'data_prot', 'period_year': 2026, 'period_quarter': 2,
            'title': 'Q2 data protection report', 'status': 'draft',
        }, format='json')
        self.assertEqual(res.status_code, status.HTTP_201_CREATED, res.data)
        self.assertEqual(
            ComplianceReport.objects.filter(kind=ComplianceReport.Kind.DATA_PROT).count(), 1)

    def test_a_filed_AML_pack_does_not_discharge_the_DPO(self):
        """The 9-Sep kind split, guarded properly.

        The first version of this test filed a DRAFT — and a draft is never
        `is_discharged`, so the DPO counter stayed at 1 whether the counter
        filtered on kind or not. It passed with the protection deleted, which
        makes it worse than no test. This files a REAL discharged AML pack for
        the quarter the counter actually reads, then asserts the two counters
        disagree: AML closed, data protection still owing.
        """
        import datetime as dt

        from django.core.files.base import ContentFile

        from hris.objective_counters import (
            omni_board_pack_outstanding,
            omni_dpo_pack_outstanding,
        )
        from iso_compliance.aml_models import ComplianceReport, quarter_of

        # The counter reads the quarter that just ENDED — mirror that exactly,
        # or the pack lands in a quarter nobody is counting and everything
        # passes for the wrong reason.
        today = timezone.localdate()
        first_of_this_q = dt.date(today.year, ((today.month - 1) // 3) * 3 + 1, 1)
        year, quarter = quarter_of(first_of_this_q - dt.timedelta(days=1))

        self.assertEqual(omni_board_pack_outstanding(), 1)
        self.assertEqual(omni_dpo_pack_outstanding(), 1)

        ComplianceReport.objects.create(
            kind=ComplianceReport.Kind.AML, period_year=year, period_quarter=quarter,
            title='Q AML pack', status=ComplianceReport.Status.SENT,
            document=ContentFile(b'x', name='q.pdf'))

        self.assertEqual(omni_board_pack_outstanding(), 0)   # AML discharged
        self.assertEqual(omni_dpo_pack_outstanding(), 1)     # DPO still owes one

    def test_a_filed_report_cannot_be_moved_between_registers(self):
        """Refused by the SERVER, not only by the screen.

        Six people can write these registers. A guard that lives in the page is
        a guard for people using the page.
        """
        from iso_compliance.aml_models import ComplianceReport

        row = ComplianceReport.objects.create(
            kind=ComplianceReport.Kind.AML, period_year=2026, period_quarter=2,
            title='Q2 AML report', status=ComplianceReport.Status.DRAFT)

        self.client.force_authenticate(self.officer)
        res = self.client.patch(f'{self.url}{row.pk}/', {'kind': 'data_prot'}, format='json')
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST, res.data)
        self.assertIn('kind', res.data)
        row.refresh_from_db()
        self.assertEqual(row.kind, ComplianceReport.Kind.AML)

    def test_a_draft_with_no_document_is_fine(self):
        self.client.force_authenticate(self.officer)
        res = self.client.post(self.url, {
            'kind': 'aml', 'period_year': 2026, 'period_quarter': 2,
            'title': 'Q2 AML report', 'status': 'draft',
        }, format='json')
        self.assertEqual(res.status_code, status.HTTP_201_CREATED, res.data)


class TrainingOutstandingTests(APITestCase):
    """The outstanding list must match the objective counter exactly.

    If the screen and the scoreboard disagree, the officer is sent to chase
    people the counter has already cleared, and trust in both goes.
    """

    def setUp(self):
        self.officer = _aml_officer()

    def test_outstanding_matches_the_objective_counter_on_REAL_data(self):
        """Seeded so the right answer is 1, not 0.

        The first version of this test compared the two readers on an EMPTY
        database and asserted 0 == 0, which passes even when the endpoint
        returns something completely wrong, as long as the wrong thing is also
        zero. A drift guard that cannot fail guards nothing.

        Three people, one of each kind that matters: current (excluded),
        lapsed (counted), terminated (excluded whatever their training says).
        """
        from hris.objective_counters import omni_aml_training_outstanding
        from iso_compliance.aml_models import AMLTrainingRecord
        from payroll.models import Employee

        current = Employee.objects.create(
            employee_number='E-CUR', full_name='Currently Trained',
            status=Employee.Status.ACTIVE, company=_adic())
        AMLTrainingRecord.objects.create(
            employee=current, completed_on=timezone.localdate() - dt.timedelta(days=30))

        lapsed = Employee.objects.create(
            employee_number='E-LAP', full_name='Lapsed Person',
            status=Employee.Status.ACTIVE, company=_adic())
        AMLTrainingRecord.objects.create(
            employee=lapsed, completed_on=timezone.localdate() - dt.timedelta(days=400))

        gone = Employee.objects.create(
            employee_number='E-GONE', full_name='Left The Company',
            status=Employee.Status.TERMINATED, company=_adic())

        self.client.force_authenticate(self.officer)
        res = self.client.get('/api/v1/aml/training/outstanding/')
        self.assertEqual(res.status_code, status.HTTP_200_OK)

        # The answer is a real number, and BOTH readers must produce it.
        self.assertEqual(res.data['count'], 1)
        self.assertEqual(omni_aml_training_outstanding(), 1)

        listed = {r['employee'] for r in res.data['results']}
        self.assertEqual(listed, {lapsed.pk})
        self.assertNotIn(current.pk, listed)
        self.assertNotIn(gone.pk, listed)

    def test_lapsed_training_counts_as_outstanding(self):
        """Trained once in 2019 is not a trained workforce."""
        from iso_compliance.aml_models import AMLTrainingRecord
        from payroll.models import Employee

        emp = Employee.objects.create(
            employee_number='E-TEST-1', full_name='Test Person',
            status=Employee.Status.ACTIVE, company=_adic())
        AMLTrainingRecord.objects.create(
            employee=emp, course='AML/CFT awareness',
            completed_on=timezone.localdate() - dt.timedelta(days=400))

        self.client.force_authenticate(self.officer)
        res = self.client.get('/api/v1/aml/training/outstanding/')
        names = [r['employee'] for r in res.data['results']]
        self.assertIn(emp.pk, names)


class RegisterCannotBeErasedTests(APITestCase):
    """A regulatory register is append-and-correct. DELETE is not on offer.

    Caught in review, and it was mine: the registers shipped as plain
    ModelViewSets, so everyone who could record a screening could also erase
    one. The row most worth erasing is a confirmed sanctions match with no FIA
    report against it — deleting that row also silences the alarm that counts
    it.
    """

    def setUp(self):
        self.officer = _aml_officer()

    def test_the_very_user_who_may_record_may_not_delete(self):
        self.client.force_authenticate(self.officer)
        row = SanctionsScreening.objects.create(
            subject_name='Flagged Party', list_source='UNSC', list_version='2026-09-12',
            result=SanctionsScreening.Result.MATCH, reported_to_fia_at=None)

        # She can write — that is the whole point of the change.
        ok = self.client.post('/api/v1/aml/sanctions-screenings/', {
            'subject_name': 'Acme', 'list_source': 'UNSC', 'list_version': '2026-09-12',
        }, format='json')
        self.assertEqual(ok.status_code, status.HTTP_201_CREATED)

        # She cannot erase.
        res = self.client.delete(f'/api/v1/aml/sanctions-screenings/{row.pk}/')
        self.assertEqual(res.status_code, status.HTTP_405_METHOD_NOT_ALLOWED)
        self.assertTrue(SanctionsScreening.objects.filter(pk=row.pk).exists())

    def test_compliance_may_add_a_risk_but_not_delete_one(self):
        from iso_compliance.models import Risk

        self.client.force_authenticate(self.officer)
        created = self.client.post('/api/v1/iso/risks/', {
            'ref': 'R-DEL', 'title': 'A risk', 'likelihood': 3, 'impact': 3,
            'owner': 'Kakale Botana', 'treatment_plan': 'Plan.', 'status': 'open',
        }, format='json')
        self.assertEqual(created.status_code, status.HTTP_201_CREATED, created.data)

        risk_id = created.data['id']
        res = self.client.delete(f'/api/v1/iso/risks/{risk_id}/')
        self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN)
        self.assertTrue(Risk.objects.filter(pk=risk_id).exists())


class SupplierKYCCannotBeRepointedTests(APITestCase):
    """One supplier's KYC file must not be movable onto another supplier.

    Also caught in review, also mine. `contact` was writable, so a single edit
    would carry the TIN, the certificate of incorporation, the bank letter, the
    beneficial owners and the screening verdict across to a different company.
    The record would still look complete. It would just be about the wrong
    supplier, and nothing on screen would say so.
    """

    def setUp(self):
        self.officer = _aml_officer()

    def _vendor(self, name):
        from billing.models import Contact
        return Contact.objects.create(
            contact_type=Contact.ContactType.VENDOR, name=name)

    def test_the_owning_supplier_cannot_be_changed(self):
        from procurement.kyc_models import VendorKYC

        acme = self._vendor('Acme Supplies')
        other = self._vendor('Somebody Else Ltd')
        kyc = VendorKYC.objects.create(
            contact=acme, sanctions_status=VendorKYC.SanctionsStatus.CLEAN)

        self.client.force_authenticate(self.officer)
        res = self.client.patch(
            f'/api/v1/aml/supplier-screening/{kyc.pk}/',
            {'contact': str(other.pk)}, format='json')

        # The write itself is allowed; the reassignment is silently refused.
        self.assertIn(res.status_code, (status.HTTP_200_OK, status.HTTP_400_BAD_REQUEST))
        kyc.refresh_from_db()
        self.assertEqual(kyc.contact_id, acme.pk)

    def test_a_bare_post_is_refused_so_there_is_one_way_in(self):
        acme = self._vendor('Acme Supplies')
        self.client.force_authenticate(self.officer)
        res = self.client.post('/api/v1/aml/supplier-screening/',
                               {'contact': str(acme.pk)}, format='json')
        self.assertEqual(res.status_code, status.HTTP_405_METHOD_NOT_ALLOWED)

    def test_screen_records_the_verdict_and_stamps_the_time(self):
        from procurement.kyc_models import VendorKYC

        acme = self._vendor('Acme Supplies')
        self.client.force_authenticate(self.officer)
        res = self.client.post('/api/v1/aml/supplier-screening/screen/', {
            'contact': str(acme.pk), 'sanctions_status': 'clean',
            'pep_status': 'none', 'list_version': '2026-09-12',
            'notes': 'UNSC Consolidated 2026-09-12',
        }, format='json')
        self.assertEqual(res.status_code, status.HTTP_200_OK, res.data)
        kyc = VendorKYC.objects.get(contact=acme)
        self.assertEqual(kyc.sanctions_status, 'clean')
        self.assertIsNotNone(kyc.sanctions_checked_at)


class TrainingRegisterIsAlphaDirectOnlyTests(APITestCase):
    """The AML/CFT Officer is Alpha Direct's, not the group's.

    The register first counted every active employee in the group and reported
    160. That figure carried 16 UniCoin staff she has no remit over, plus 49
    `M365-` rows which have no login, no company and no payslip and are not
    real staff. An officer measured against people who do not exist cannot
    reach the target however much training she runs.
    """

    def setUp(self):
        self.officer = _aml_officer()

    def _company(self, code):
        from core.models import Company
        return Company.objects.get_or_create(
            code=code, defaults={'name': f'Company {code}'})[0]

    def test_only_ADIC_staff_are_counted(self):
        from hris.objective_counters import omni_aml_training_outstanding
        from payroll.models import Employee

        adic = self._company('ADIC')
        other = self._company('UNI')
        mine = Employee.objects.create(employee_number='E-ADIC-1', full_name='Ours',
                                       status=Employee.Status.ACTIVE, company=adic)
        Employee.objects.create(employee_number='E-UNI-1', full_name='Theirs',
                                status=Employee.Status.ACTIVE, company=other)
        # A phantom: active, no company, no payslip — exactly the M365 shape.
        Employee.objects.create(employee_number='M365-deadbeef', full_name='Phantom',
                                status=Employee.Status.ACTIVE, company=None)

        self.client.force_authenticate(self.officer)
        res = self.client.get('/api/v1/aml/training/outstanding/')
        self.assertEqual(res.status_code, status.HTTP_200_OK)

        listed = {r['employee'] for r in res.data['results']}
        self.assertEqual(listed, {mine.pk})
        self.assertEqual(res.data['count'], 1)
        # And the scoreboard must agree, or she chases people it has cleared.
        self.assertEqual(omni_aml_training_outstanding(), 1)


class SupplierScreeningReadsRealPaymentsTests(APITestCase):
    """Screen who we PAID, not the vendor master.

    The vendor master is replicated per company: 9,410 rows for 1,067 real
    names. Worse, payments name their payee as free text and never point at a
    vendor record, so screening the master missed almost every company money
    actually went to.
    """

    def setUp(self):
        self.officer = _aml_officer()
        self.url = '/api/v1/aml/supplier-screening/suppliers/'
        # The entity is resolved POSITIVELY against the Company table, so the
        # company has to exist. Before the fix these tests passed on a
        # substring and needed no such row — which is the whole point: an
        # entity the system does not know now resolves to nothing rather than
        # falling back to Alpha Direct.
        _adic()

    def _paid(self, payee, entity='Alpha Direct Insurance', loaded=True, days_ago=5):
        import datetime as dt
        import uuid

        from django.utils import timezone

        from taskboard.models import PaymentRequest
        when = timezone.now() - dt.timedelta(days=days_ago)
        pr = PaymentRequest.objects.create(
            ref=f'TEST-{uuid.uuid4().hex[:10]}',
            payee=payee, entity=entity, total=1000,
            fnb_loaded_at=when if loaded else None)
        PaymentRequest.objects.filter(pk=pr.pk).update(created_at=when)
        return pr

    def test_lists_who_we_paid_including_those_with_no_vendor_record(self):
        from billing.models import Contact

        Contact.objects.create(contact_type=Contact.ContactType.VENDOR,
                               name='Known Vendor Ltd')
        self._paid('Known Vendor Ltd')
        self._paid('Never Heard Of Them (Pty) Ltd')     # no vendor record at all

        self.client.force_authenticate(self.officer)
        res = self.client.get(self.url)
        self.assertEqual(res.status_code, status.HTTP_200_OK, res.data)

        names = {r['name'] for r in res.data['results']}
        self.assertEqual(names, {'Known Vendor Ltd', 'Never Heard Of Them (Pty) Ltd'})
        self.assertEqual(res.data['no_vendor_record'], 1)

    def test_a_request_never_loaded_to_the_bank_is_not_a_payment(self):
        """Approval in Omni is a workflow record. The bank is what happened."""
        self._paid('Approved But Never Sent Ltd', loaded=False)

        self.client.force_authenticate(self.officer)
        res = self.client.get(self.url)
        self.assertEqual(res.data['count'], 0)

    def test_another_entity_is_not_her_problem(self):
        self._paid('Unicoin Supplier Ltd', entity='Unicoin')

        self.client.force_authenticate(self.officer)
        res = self.client.get(self.url)
        self.assertEqual(res.data['count'], 0)

    def test_outside_the_window_drops_off(self):
        self._paid('Ancient Supplier Ltd', days_ago=200)

        self.client.force_authenticate(self.officer)
        res = self.client.get(self.url + '?days=60')
        self.assertEqual(res.data['count'], 0)
        res2 = self.client.get(self.url + '?days=365')
        self.assertEqual(res2.data['count'], 1)

    def test_screening_by_name_STILL_needs_the_list_version(self):
        """The by-name path must not be a way round the evidence rule.

        It originally wrote straight to the ORM, which skipped
        `validate_list_version` entirely - so the register gained a back door
        on the same day the rule was added to it. A screening with no list
        version cannot be repeated, and a control that cannot be evidenced is
        not a control.
        """
        from iso_compliance.aml_models import SanctionsScreening

        self.client.force_authenticate(self.officer)
        res = self.client.post('/api/v1/aml/supplier-screening/screen/', {
            'name': 'No Version Supplied Ltd',
            'sanctions_status': 'clean',
            'list_source': 'UNSC Consolidated',
            'list_version': '',
        }, format='json')
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST, res.data)
        self.assertIn('list_version', res.data)
        self.assertFalse(
            SanctionsScreening.objects.filter(subject_name='No Version Supplied Ltd').exists())

    def test_a_SIBLING_entity_is_not_Alpha_Direct(self):
        """The test that tells a substring apart from a real match.

        `test_another_entity_is_not_her_problem` uses 'Unicoin', which is
        excluded by a substring match and by a positive one alike — it cannot
        distinguish them. These can. Alpha Direct Life, Alpha Direct South
        Africa and Alpha Direct Insurtech are REAL group entities defined in
        setup_initial_data, and every one of them contains the fragment
        'Alpha Direct'. A payment raised under the CODE 'ADIC' is the form the
        taskboard tests use, and a substring match drops it.
        """
        from core.models import Company

        Company.objects.get_or_create(code='ADIC', defaults={'name': 'Alpha Direct Insurance'})
        Company.objects.get_or_create(code='ADIL', defaults={'name': 'Alpha Direct Life'})

        self._paid('Sibling Co Supplier', entity='Alpha Direct Life')
        self._paid('Code Form Supplier', entity='ADIC')
        self._paid('Full Name Supplier', entity='Alpha Direct Insurance')

        self.client.force_authenticate(self.officer)
        res = self.client.get(self.url)
        names = {r['name'] for r in res.data['results']}

        self.assertNotIn('Sibling Co Supplier', names)   # substring would let this in
        self.assertIn('Code Form Supplier', names)       # substring would drop this
        self.assertIn('Full Name Supplier', names)

    def test_the_window_runs_from_when_the_bank_got_it(self):
        """A request raised 70 days ago and paid 10 days ago is a recent payment."""
        from core.models import Company
        Company.objects.get_or_create(code='ADIC', defaults={'name': 'Alpha Direct Insurance'})

        import datetime as _dt
        from django.utils import timezone as _tz
        from taskboard.models import PaymentRequest
        import uuid as _uuid

        pr = PaymentRequest.objects.create(
            ref=f'TEST-{_uuid.uuid4().hex[:10]}', payee='Slow Approval Ltd',
            entity='Alpha Direct Insurance', total=500,
            fnb_loaded_at=_tz.now() - _dt.timedelta(days=10))
        PaymentRequest.objects.filter(pk=pr.pk).update(
            created_at=_tz.now() - _dt.timedelta(days=70))

        self.client.force_authenticate(self.officer)
        res = self.client.get(self.url + '?days=60')
        self.assertIn('Slow Approval Ltd', {r['name'] for r in res.data['results']})

    def test_a_payee_with_no_vendor_record_can_still_be_screened(self):
        """Refusing these would leave almost everything we pay unscreened."""
        from iso_compliance.aml_models import SanctionsScreening

        self.client.force_authenticate(self.officer)
        res = self.client.post('/api/v1/aml/supplier-screening/screen/', {
            'name': 'Never Heard Of Them (Pty) Ltd',
            'sanctions_status': 'clean', 'pep_status': 'none',
            'list_source': 'UNSC Consolidated', 'list_version': '2026-09-16',
        }, format='json')
        self.assertEqual(res.status_code, status.HTTP_201_CREATED, res.data)

        row = SanctionsScreening.objects.get(subject_name='Never Heard Of Them (Pty) Ltd')
        self.assertEqual(row.subject_type, SanctionsScreening.SubjectType.SUPPLIER)
        self.assertEqual(row.screened_by_id, self.officer.pk)


class SupplierScreeningShowsWhatWasRecordedTests(APITestCase):
    """Record a screening, re-open the screen, see the answers.

    kbotana (bug 7dc9716b, 2026-09-17): the PEP status, the list version and
    the notes all read back empty. A payee with no vendor record is filed in
    the sanctions register by NAME, but this screen read VendorKYC only — so
    it showed 'unchecked' for something it had just written, and the dialog
    re-opened blank. Nothing was ever lost; the read was pointed elsewhere.
    """

    URL = '/api/v1/aml/supplier-screening/suppliers/'
    PAYEE = 'No Vendor Record Ltd'

    def setUp(self):
        self.officer = _aml_officer()
        _adic()
        import datetime as dt
        import uuid

        from django.utils import timezone

        from taskboard.models import PaymentRequest
        when = timezone.now() - dt.timedelta(days=5)
        pr = PaymentRequest.objects.create(
            ref=f'TEST-{uuid.uuid4().hex[:10]}', payee=self.PAYEE,
            entity='Alpha Direct Insurance', total=1000, fnb_loaded_at=when)
        PaymentRequest.objects.filter(pk=pr.pk).update(created_at=when)
        self.client.force_authenticate(self.officer)

    def _record(self, **over):
        body = {'name': self.PAYEE, 'sanctions_status': 'clean',
                'pep_status': 'pep', 'list_source': 'UNSC Consolidated',
                'list_version': '2026-09-17', 'notes': 'Director is a sitting MP.'}
        body.update(over)
        res = self.client.post('/api/v1/aml/supplier-screening/screen/',
                               body, format='json')
        self.assertEqual(res.status_code, 201, res.data)

    def _row(self):
        res = self.client.get(self.URL)
        self.assertEqual(res.status_code, status.HTTP_200_OK, res.data)
        return next(r for r in res.data['results'] if r['name'] == self.PAYEE)

    def test_the_pep_answer_comes_back(self):
        self._record()
        self.assertEqual(self._row()['pep_status'], 'pep')

    def test_the_list_version_and_notes_come_back(self):
        self._record()
        row = self._row()
        self.assertEqual(row['list_version'], '2026-09-17')
        self.assertEqual(row['list_source'], 'UNSC Consolidated')
        self.assertIn('sitting MP', row['screening_notes'])

    def test_the_screened_date_is_filled_in(self):
        """'Last screened' was blank on a payee screened five minutes ago."""
        self._record()
        self.assertIsNotNone(self._row()['sanctions_checked_at'])

    def test_a_clean_result_reads_as_clean_and_a_possible_match_as_flagged(self):
        self._record()
        self.assertEqual(self._row()['sanctions_status'], 'clean')
        self._record(sanctions_status='flagged', list_version='2026-09-18')
        self.assertEqual(self._row()['sanctions_status'], 'flagged')

    def test_the_latest_screening_wins(self):
        self._record(pep_status='none', list_version='2026-09-16')
        self._record(pep_status='associate', list_version='2026-09-17')
        row = self._row()
        self.assertEqual(row['pep_status'], 'associate')
        self.assertEqual(row['list_version'], '2026-09-17')

    def test_an_unscreened_payee_is_still_unchecked(self):
        row = self._row()
        self.assertEqual(row['pep_status'], 'unchecked')
        self.assertEqual(row['sanctions_status'], 'unchecked')
        self.assertIsNone(row['sanctions_checked_at'])
        self.assertFalse(row['already_screened'])


class VendorRecordScreeningKeepsItsEvidenceTests(SupplierScreeningShowsWhatWasRecordedTests):
    """The rest of bug 7dc9716b (CFO file 2 Medium, 18-Sep-2026).

    The 17-Sep fix covered payees with NO vendor record. A supplier WITH one
    went down a different path that never wrote the register: the list source
    and version were dropped, the list-version rule was skipped, and a cleared
    note never saved. Reproduced live: both vendor-record screenings in the
    prior 14 days had no list version anywhere. Every parent test re-runs here
    against a supplier that has a vendor record.
    """

    PAYEE = 'Has A Vendor Record Ltd'

    def setUp(self):
        super().setUp()
        from billing.models import Contact
        self.vendor = Contact.objects.create(
            contact_type=Contact.ContactType.VENDOR, name=self.PAYEE)

    def _record(self, **over):
        body = {'contact': str(self.vendor.pk), 'name': self.PAYEE,
                'sanctions_status': 'clean', 'pep_status': 'pep',
                'list_source': 'UNSC Consolidated', 'list_version': '2026-09-17',
                'notes': 'Director is a sitting MP.'}
        body.update(over)
        res = self.client.post('/api/v1/aml/supplier-screening/screen/',
                               body, format='json')
        self.assertEqual(res.status_code, 200, res.data)
        return res

    def test_the_row_is_read_from_the_vendor_record(self):
        self._record()
        self.assertTrue(self._row()['has_vendor_record'])

    def test_a_vendor_record_screening_without_a_list_version_is_refused(self):
        from procurement.kyc_models import VendorKYC
        res = self.client.post('/api/v1/aml/supplier-screening/screen/', {
            'contact': str(self.vendor.pk), 'sanctions_status': 'clean',
            'pep_status': 'none', 'list_version': '  '}, format='json')
        self.assertEqual(res.status_code, 400, res.data)
        self.assertFalse(VendorKYC.objects.filter(contact=self.vendor).exists())

    def test_clearing_the_notes_sticks(self):
        self._record()
        self._record(notes='', list_version='2026-09-18')
        self.assertEqual(self._row()['screening_notes'], '')

    def test_leaving_notes_out_keeps_them(self):
        self._record()
        body = {'contact': str(self.vendor.pk), 'sanctions_status': 'clean',
                'pep_status': 'pep', 'list_version': '2026-09-18'}
        res = self.client.post('/api/v1/aml/supplier-screening/screen/', body, format='json')
        self.assertEqual(res.status_code, 200, res.data)
        self.assertIn('sitting MP', self._row()['screening_notes'])

    def test_one_register_row_per_save_and_no_other_supplier_touched(self):
        from iso_compliance.aml_models import SanctionsScreening
        from procurement.kyc_models import VendorKYC
        self._record()
        self.assertEqual(SanctionsScreening.objects.filter(subject_name=self.PAYEE).count(), 1)
        self.assertEqual(VendorKYC.objects.count(), 1)


class ScreeningMustRecordAResultTests(SupplierScreeningShowsWhatWasRecordedTests):
    """'unchecked' is not a screening result; it used to land in the register
    as a possible match and mark the payee screened."""

    def test_unchecked_is_refused_on_both_paths(self):
        from billing.models import Contact
        from iso_compliance.aml_models import SanctionsScreening
        vendor = Contact.objects.create(contact_type=Contact.ContactType.VENDOR, name='Some Vendor')
        for body in ({'name': self.PAYEE}, {'contact': str(vendor.pk)}):
            res = self.client.post('/api/v1/aml/supplier-screening/screen/', dict(
                body, sanctions_status='unchecked', list_version='2026-09-18'), format='json')
            self.assertEqual(res.status_code, 400, res.data)
        self.assertFalse(SanctionsScreening.objects.exists())
