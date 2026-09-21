"""The KYC / evidence register — control brief §5 (Arun P. Iyer, 15-Sep-2026).

Four things have to be true at once, and three of them pull against each other:

  1. Required evidence that is MISSING, UNVERIFIED or EXPIRED blocks a NEW
     onboarding and a NEW placement.
  2. It must NOT stop an already-imported legacy record — the FAC master loader
     is recording signed slips that already carry live FY27 risk.
  3. Uploaded is not verified. Attaching a PDF is not evidence that anyone read
     it, and the checklist must not treat it as such.
  4. The file is never a public URL. `/media/` is not served in this deployment,
     so `document.file.url` 404s in production; everything streams through the
     download view behind the reinsurance permission.

RED-PROOF:
  * Drop `missing.extend(evidence_gaps(reinsurer))` from
    `onboarding.missing_prerequisites` → `test_compliance_cannot_sign_off_...`
    goes green-through, i.e. the test fails.
  * Drop the `require_evidence` block from `Reinsurer.placement_block_reason`
    → `test_a_new_placement_is_blocked_...` fails.
  * Remove `_legacy_import` from the loader's allocation save → the legacy test
    fails with a ValidationError, which is the regression it exists to catch.
"""
from __future__ import annotations

import datetime as dt
from decimal import Decimal

from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from rest_framework.test import APIClient

from reinsurance import onboarding
from reinsurance.models import (FacReinsurerAllocation, FacRiskExposure,
                                Reinsurer, ReinsurerDocument,
                                ReinsurerSecurityAssessment, evidence_gaps)

S = Reinsurer.ApprovalStatus
K = ReinsurerDocument.Kind
V = ReinsurerDocument.Verification


def _pdf(name='evidence.pdf'):
    return SimpleUploadedFile(name, b'%PDF-1.4 evidence', content_type='application/pdf')


def complete_kyc(reinsurer):
    """Give a counterparty a clean, verified, in-date KYC file.

    Shared with the other reinsurance test modules. Before 16-Sep-2026 an
    APPROVED counterparty could be placed on the strength of its status alone;
    the control brief adds verified evidence as a second precondition, so the
    tests that were written under the old rule state the new one explicitly
    rather than being deleted.
    """
    for kind in ReinsurerDocument.REQUIRED_KINDS:
        ReinsurerDocument.objects.create(
            reinsurer=reinsurer, kind=kind, file=_pdf(),
            original_filename='evidence.pdf', content_type='application/pdf',
            verification_status=V.VERIFIED)
    return reinsurer


class EvidenceGapTests(TestCase):

    @classmethod
    def setUpTestData(cls):
        cls.re = Reinsurer.objects.create(
            name='Test Re', short_code='TSTRE', approval_status=S.APPROVED,
            expiry_date=dt.date.today() + dt.timedelta(days=365))

    def _doc(self, kind, *, status=V.VERIFIED, expiry=None):
        return ReinsurerDocument.objects.create(
            reinsurer=self.re, kind=kind, file=_pdf(),
            original_filename='evidence.pdf', content_type='application/pdf',
            verification_status=status, expiry_date=expiry)

    def _complete_file(self):
        for kind in ReinsurerDocument.REQUIRED_KINDS:
            self._doc(kind)

    def test_an_empty_register_reports_every_required_item(self):
        gaps = evidence_gaps(self.re)
        self.assertEqual(len(gaps), len(ReinsurerDocument.REQUIRED_KINDS))
        self.assertTrue(all('nothing on file' in g for g in gaps))

    def test_uploaded_but_unverified_is_still_a_gap(self):
        self._doc(K.INCORPORATION, status=V.PENDING)
        gaps = evidence_gaps(self.re)
        self.assertTrue(any('not yet verified' in g for g in gaps),
                        'an unverified upload was counted as evidence')

    def test_verified_but_expired_is_still_a_gap(self):
        self._doc(K.INCORPORATION, expiry=dt.date.today() - dt.timedelta(days=1))
        self.assertTrue(any('EXPIRED' in g for g in evidence_gaps(self.re)))

    def test_a_complete_verified_in_date_file_has_no_gaps(self):
        self._complete_file()
        self.assertEqual(evidence_gaps(self.re), [])

    def test_a_rejected_document_does_not_paper_over_the_gap(self):
        self._doc(K.LICENCE, status=V.REJECTED)
        self.assertTrue(any('Licence' in g for g in evidence_gaps(self.re)))


class OnboardingGateTests(TestCase):
    """Compliance is the stage that signs KYC off, so it is the stage that needs it."""

    @classmethod
    def setUpTestData(cls):
        cls.re = Reinsurer.objects.create(
            name='Gate Re', short_code='GATRE', legal_name='Gate Re Ltd',
            domicile='MU', onboarding_purposes=['facultative'],
            approval_status=S.PENDING_COMPLIANCE)
        # Verified, dated and scaled — an adequate assessment, not merely one
        # that exists. See SecurityAssessmentAdequacyTests below for what
        # happens when it is not.
        cls.re.security_assessments.create(
            rating='A-', rating_agency='AM Best',
            rating_scale=ReinsurerSecurityAssessment.Scale.INTERNATIONAL,
            evidence_date=dt.date.today(), verified=True)

    def test_compliance_cannot_sign_off_without_the_evidence(self):
        missing = onboarding.missing_prerequisites(self.re, S.PENDING_PRINCIPAL)
        self.assertTrue(missing, 'compliance could sign off on an empty KYC file')
        self.assertTrue(any('nothing on file' in m for m in missing))

    def test_compliance_can_sign_off_once_the_file_is_complete(self):
        for kind in ReinsurerDocument.REQUIRED_KINDS:
            ReinsurerDocument.objects.create(
                reinsurer=self.re, kind=kind, file=_pdf(),
                verification_status=V.VERIFIED)
        self.assertEqual(
            onboarding.missing_prerequisites(self.re, S.PENDING_PRINCIPAL), [])

    def test_the_earlier_stages_are_not_held_up_by_paperwork(self):
        # Underwriting submitting a counterparty for review must not need the
        # KYC file — that is what the review is FOR.
        self.assertEqual(
            onboarding.missing_prerequisites(self.re, S.PENDING_COMPLIANCE), [])


class SecurityAssessmentAdequacyTests(TestCase):
    """A security assessment that merely EXISTS is not evidence.

    Reinsurance control QC, 3885a3ec: "deficient evidence acceptance" — a
    security assessment was accepted with missing or inadequate evidence.
    `missing_prerequisites` only ever checked
    `reinsurer.security_assessments.exists()` before letting compliance send a
    counterparty on to principal review. A blank stub — no rating, no scale, no
    evidence_date, `verified=False` by model default — satisfied that check
    exactly as well as a real one. The KYC document register two lines below
    already treats "uploaded" and "verified" as different facts for the same
    reason (`ReinsurerDocument.counts_as_evidence`); the assessment needs the
    same rule applied to it.

    RED-PROOF: change the check back to `.exists()` (drop the `.filter(...)`
    conditions added for this fix) and every test below that expects `missing`
    to be non-empty goes green through instead.
    """

    def _re(self):
        return Reinsurer.objects.create(
            name='Adequacy Re', short_code='ADEQRE', legal_name='Adequacy Re Ltd',
            domicile='MU', onboarding_purposes=['facultative'],
            approval_status=S.PENDING_COMPLIANCE)

    def _complete_kyc_docs(self, re):
        for kind in ReinsurerDocument.REQUIRED_KINDS:
            ReinsurerDocument.objects.create(
                reinsurer=re, kind=kind, file=_pdf(), verification_status=V.VERIFIED)

    def test_an_unverified_assessment_does_not_satisfy_compliance(self):
        re = self._re()
        re.security_assessments.create(
            rating='A-', rating_agency='AM Best',
            rating_scale=ReinsurerSecurityAssessment.Scale.INTERNATIONAL,
            evidence_date=dt.date.today())          # verified left at its False default
        self._complete_kyc_docs(re)
        missing = onboarding.missing_prerequisites(re, S.PENDING_PRINCIPAL)
        self.assertTrue(
            missing, 'an unverified security assessment satisfied compliance sign-off')

    def test_a_verified_but_blank_assessment_does_not_satisfy_compliance(self):
        re = self._re()
        re.security_assessments.create(verified=True)   # no rating, no scale, no evidence_date
        self._complete_kyc_docs(re)
        missing = onboarding.missing_prerequisites(re, S.PENDING_PRINCIPAL)
        self.assertTrue(
            missing, 'a verified but empty assessment stub satisfied compliance sign-off')

    def test_a_verified_assessment_with_no_evidence_date_does_not_satisfy_compliance(self):
        re = self._re()
        re.security_assessments.create(
            rating='A-', rating_agency='AM Best',
            rating_scale=ReinsurerSecurityAssessment.Scale.INTERNATIONAL,
            verified=True)                            # evidence_date never set
        self._complete_kyc_docs(re)
        missing = onboarding.missing_prerequisites(re, S.PENDING_PRINCIPAL)
        self.assertTrue(missing)

    def test_a_verified_complete_assessment_satisfies_compliance(self):
        re = self._re()
        re.security_assessments.create(
            rating='A-', rating_agency='AM Best',
            rating_scale=ReinsurerSecurityAssessment.Scale.INTERNATIONAL,
            evidence_date=dt.date.today(), verified=True)
        self._complete_kyc_docs(re)
        self.assertEqual(
            onboarding.missing_prerequisites(re, S.PENDING_PRINCIPAL), [])

    def test_an_old_unverified_stub_does_not_hide_behind_a_later_good_one(self):
        """History is kept, never overwritten — so an adequate LATER assessment
        must be what is checked, not merely 'one exists somewhere'."""
        re = self._re()
        re.security_assessments.create(rating='B', rating_agency='Self-reported')
        re.security_assessments.create(
            rating='A-', rating_agency='AM Best',
            rating_scale=ReinsurerSecurityAssessment.Scale.INTERNATIONAL,
            evidence_date=dt.date.today(), verified=True)
        self._complete_kyc_docs(re)
        self.assertEqual(
            onboarding.missing_prerequisites(re, S.PENDING_PRINCIPAL), [])


class PlacementGateTests(TestCase):

    @classmethod
    def setUpTestData(cls):
        cls.re = Reinsurer.objects.create(
            name='Place Re', short_code='PLCRE', approval_status=S.APPROVED,
            expiry_date=dt.date.today() + dt.timedelta(days=365))
        cls.exposure = FacRiskExposure.objects.create(
            reference='FAC-TEST-1', insured_name='A Test Insured')

    def _allocation(self, **extra):
        return FacReinsurerAllocation(
            exposure=self.exposure, reinsurer=self.re,
            share_percent=Decimal('10.00'), allocated_amount=Decimal('1000.00'),
            **extra)

    def test_a_new_placement_is_blocked_while_the_kyc_file_is_incomplete(self):
        with self.assertRaises(ValidationError) as caught:
            self._allocation().save()
        self.assertIn('KYC file is complete', str(caught.exception))
        self.assertEqual(FacReinsurerAllocation.objects.count(), 0)

    def test_a_legacy_import_is_recorded_despite_the_gap(self):
        # The exact regression the brief warns about: an already-placed signed
        # slip must load, gap or no gap. Only the EVIDENCE half is relaxed.
        alloc = self._allocation()
        alloc._legacy_import = True
        alloc.save()
        self.assertEqual(FacReinsurerAllocation.objects.count(), 1)

    def test_a_legacy_import_still_cannot_use_an_unapproved_counterparty(self):
        Reinsurer.objects.filter(pk=self.re.pk).update(approval_status=S.DRAFT)
        self.re.refresh_from_db()
        alloc = self._allocation()
        alloc._legacy_import = True
        with self.assertRaises(ValidationError) as caught:
            alloc.save()
        self.assertIn('not approved for placement', str(caught.exception))

    def test_a_complete_file_lets_the_placement_through(self):
        for kind in ReinsurerDocument.REQUIRED_KINDS:
            ReinsurerDocument.objects.create(
                reinsurer=self.re, kind=kind, file=_pdf(),
                verification_status=V.VERIFIED)
        self._allocation().save()
        self.assertEqual(FacReinsurerAllocation.objects.count(), 1)


class DocumentApiTests(TestCase):

    @classmethod
    def setUpTestData(cls):
        cls.re = Reinsurer.objects.create(name='Api Re', short_code='APIRE')
        cls.admin = User.objects.create_superuser(
            'boss', 'boss@alphadirect.co.bw', 'x')
        cls.outsider = User.objects.create_user(
            'nobody', 'nobody@alphadirect.co.bw', 'x')

    def setUp(self):
        self.api = APIClient()
        self.base = f'/api/v1/reinsurance/counterparties/{self.re.pk}/documents/'

    def test_an_outsider_cannot_read_or_upload(self):
        self.api.force_authenticate(self.outsider)
        self.assertEqual(self.api.get(self.base).status_code, 403)
        self.assertEqual(
            self.api.post(self.base, {'kind': K.LICENCE, 'file': _pdf()},
                          format='multipart').status_code, 403)

    def test_the_gaps_are_reported_even_when_the_register_is_empty(self):
        # An empty register is the WORST case, not a quiet one. If the API says
        # nothing, the screen shows nothing, and nobody chases the evidence.
        self.api.force_authenticate(self.admin)
        res = self.api.get(self.base)
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.data['documents'], [])
        self.assertEqual(len(res.data['gaps']), len(ReinsurerDocument.REQUIRED_KINDS))

    def test_upload_then_verify_then_stream(self):
        self.api.force_authenticate(self.admin)
        up = self.api.post(self.base,
                           {'kind': K.LICENCE, 'file': _pdf('licence.pdf'),
                            'source': 'The reinsurer'}, format='multipart')
        self.assertEqual(up.status_code, 201)
        self.assertEqual(up.data['verification_status'], V.PENDING)
        self.assertFalse(up.data['counts_as_evidence'],
                         'an upload counted as evidence before anyone read it')

        doc_id = up.data['id']
        ok = self.api.post(f'/api/v1/reinsurance/documents/{doc_id}/verify/',
                           {'status': 'verified'}, format='json')
        self.assertEqual(ok.status_code, 200)
        self.assertTrue(ok.data['counts_as_evidence'])
        self.assertIsNotNone(ok.data['verified_at'])

        got = self.api.get(f'/api/v1/reinsurance/documents/{doc_id}/download/')
        self.assertEqual(got.status_code, 200)
        self.assertEqual(got['Content-Type'], 'application/pdf')
        self.assertEqual(b''.join(got.streaming_content), b'%PDF-1.4 evidence')

    def test_an_expiry_date_arrives_as_a_string_and_must_not_500(self):
        """The real form posts dates as text. Proven broken by the ship-gate.

        `request.data.get('expiry_date')` went straight onto a DateField. The
        row SAVED, then `_serialize` called `.isoformat()` on a str and the
        request died 500 — after the commit, so a retry left a phantom document
        behind. Same trap as the disciplinary 500 (PR #614, memory
        r-date-string). Every earlier test here posted no date at all, which is
        why they all passed.

        RED-PROOF: put back `expiry_date=(request.data.get('expiry_date') or None)`
        and this returns 500 with a row already in the table.
        """
        self.api.force_authenticate(self.admin)
        res = self.api.post(self.base,
                            {'kind': K.LICENCE, 'file': _pdf(),
                             'issue_date': '2026-01-31',
                             'expiry_date': '2027-01-31'}, format='multipart')
        self.assertEqual(res.status_code, 201, res.data)
        self.assertEqual(res.data['expiry_date'], '2027-01-31')
        self.assertEqual(res.data['issue_date'], '2026-01-31')
        self.assertEqual(ReinsurerDocument.objects.count(), 1)

    def test_a_bad_date_is_a_400_and_leaves_no_row(self):
        self.api.force_authenticate(self.admin)
        res = self.api.post(self.base,
                            {'kind': K.LICENCE, 'file': _pdf(),
                             'expiry_date': '31/01/2027'}, format='multipart')
        self.assertEqual(res.status_code, 400)
        self.assertEqual(ReinsurerDocument.objects.count(), 0,
                         'a refused upload still committed a document row')

    def test_a_rejection_must_say_why(self):
        self.api.force_authenticate(self.admin)
        up = self.api.post(self.base, {'kind': K.TAX, 'file': _pdf()},
                           format='multipart')
        res = self.api.post(f'/api/v1/reinsurance/documents/{up.data["id"]}/verify/',
                            {'status': 'rejected'}, format='json')
        self.assertEqual(res.status_code, 400)

    def test_a_disallowed_file_type_is_refused(self):
        self.api.force_authenticate(self.admin)
        bad = SimpleUploadedFile('payload.html', b'<script>alert(1)</script>',
                                 content_type='application/pdf')
        res = self.api.post(self.base, {'kind': K.LICENCE, 'file': bad},
                            format='multipart')
        self.assertEqual(res.status_code, 400)
        self.assertEqual(ReinsurerDocument.objects.count(), 0)

    def test_the_stored_content_type_comes_from_the_extension_not_the_caller(self):
        # A caller-supplied content type is caller-supplied control over what
        # the browser does with the bytes. Checklist L6, the same class of
        # mistake as trusting a substring for identity.
        self.api.force_authenticate(self.admin)
        lying = SimpleUploadedFile('sheet.xlsx', b'PK\x03\x04',
                                   content_type='text/html')
        res = self.api.post(self.base, {'kind': K.TAX, 'file': lying},
                            format='multipart')
        self.assertEqual(res.status_code, 201)
        self.assertEqual(
            ReinsurerDocument.objects.get().content_type,
            'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')

        # …and it downloads as an attachment, never inline against our origin.
        got = self.api.get(
            f'/api/v1/reinsurance/documents/{res.data["id"]}/download/')
        self.assertIn('attachment', got['Content-Disposition'])
