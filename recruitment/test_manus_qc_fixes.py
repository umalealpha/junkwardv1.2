"""
Manus QC round, 2026-08-26 — regression tests for the five reported defects.

Every test here is RED on the code as it stood before the fix. The two P0s are
the dangerous ones: an executable renamed cv.pdf was accepted and stored, and an
interview score of 999 was silently saved as 100.
"""
from __future__ import annotations

import io
import zipfile

from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase

from core.upload_safety import (looks_executable, sniff_document,
                                validate_document_upload)
from recruitment.models import (Application, AuthorityToRecruit, Candidate,
                                InterviewScorecard, JobRequisition)

PDF = b'%PDF-1.7\n1 0 obj\n<<>>\nendobj\ntrailer\n'
EXE = b'MZ\x90\x00\x03\x00\x00\x00' + b'\x00' * 64
ELF = b'\x7fELF\x02\x01\x01\x00' + b'\x00' * 64


def _docx_bytes() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w') as z:
        z.writestr('[Content_Types].xml', '<Types/>')
        z.writestr('word/document.xml', '<w:document/>')
    return buf.getvalue()


def _xlsx_bytes() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w') as z:
        z.writestr('[Content_Types].xml', '<Types/>')
        z.writestr('xl/workbook.xml', '<workbook/>')
    return buf.getvalue()


class UploadSafetyUnitTests(TestCase):
    def test_executable_magic_detected(self):
        self.assertTrue(looks_executable(EXE))
        self.assertTrue(looks_executable(ELF))
        self.assertTrue(looks_executable(b'#!/bin/sh\n'))
        self.assertFalse(looks_executable(PDF))

    def test_sniff_identifies_real_types(self):
        self.assertEqual(sniff_document(PDF), 'pdf')
        self.assertEqual(sniff_document(_docx_bytes()[:8]), 'docx')
        self.assertIsNone(sniff_document(b'just text'))

    def test_exe_renamed_pdf_is_refused(self):
        f = SimpleUploadedFile('cv.pdf', EXE, content_type='application/pdf')
        err = validate_document_upload(f)
        self.assertIsNotNone(err)
        self.assertIn('program', err.lower())

    def test_real_pdf_accepted(self):
        f = SimpleUploadedFile('cv.pdf', PDF, content_type='application/pdf')
        self.assertIsNone(validate_document_upload(f))

    def test_real_docx_accepted(self):
        f = SimpleUploadedFile('cv.docx', _docx_bytes())
        self.assertIsNone(validate_document_upload(f))

    def test_xlsx_renamed_docx_is_refused(self):
        # A zip is not automatically a Word file.
        f = SimpleUploadedFile('cv.docx', _xlsx_bytes())
        self.assertIsNotNone(validate_document_upload(f))

    def test_extension_must_match_real_type(self):
        f = SimpleUploadedFile('cv.docx', PDF)
        err = validate_document_upload(f)
        self.assertIsNotNone(err)
        self.assertIn('extension', err.lower())


class RtfCompatibilityTests(TestCase):
    """Tightening a validator must not start rejecting real applicants."""

    RTF = b'{\\rtf1\\ansi Curriculum Vitae}'

    def test_rtf_content_named_doc_is_accepted(self):
        f = SimpleUploadedFile('cv.doc', self.RTF)
        self.assertIsNone(validate_document_upload(f))

    def test_rtf_named_rtf_is_accepted(self):
        f = SimpleUploadedFile('cv.rtf', self.RTF)
        self.assertIsNone(validate_document_upload(f))


def _docx_with(extra_name: str, extra_bytes: bytes = b'x') -> bytes:
    """A minimally-valid Word zip plus one extra member (macro/embed/exe)."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w') as z:
        z.writestr('[Content_Types].xml', '<Types/>')
        z.writestr('word/document.xml', '<w:document/>')
        z.writestr(extra_name, extra_bytes)
    return buf.getvalue()


class UploadSafetyDeepScanTests(TestCase):
    """Manus QC round 2, 2026-08-27 — every test here is RED on the byte-0-only
    validator: an executable appended to a real PDF, and macro/embedded/exe DOCX
    parts, all passed; a valid PDF behind a BOM was wrongly rejected."""

    # A real PE carries the DOS-stub sentence and a 'PE\0\0' signature.
    _FAKE_PE = (b'MZ\x90\x00' + b'\x00' * 0x38 + b'\x40\x00\x00\x00'
                + b'This program cannot be run in DOS mode\r\n' + b'PE\x00\x00'
                + b'\x00' * 32)

    def test_pdf_with_appended_executable_is_refused(self):
        payload = PDF + b'\n%%EOF\n' + self._FAKE_PE
        f = SimpleUploadedFile('cv.pdf', payload, content_type='application/pdf')
        err = validate_document_upload(f)
        self.assertIsNotNone(err)
        self.assertIn('hidden', err.lower())

    def test_docx_with_macros_is_refused(self):
        f = SimpleUploadedFile('cv.docx', _docx_with('word/vbaProject.bin'))
        err = validate_document_upload(f)
        self.assertIsNotNone(err)
        self.assertIn('macro', err.lower())

    def test_docx_with_embedded_object_is_refused(self):
        f = SimpleUploadedFile('cv.docx', _docx_with('word/embeddings/oleObject1.bin'))
        err = validate_document_upload(f)
        self.assertIsNotNone(err)
        self.assertIn('embedded', err.lower())

    def test_docx_with_embedded_executable_member_is_refused(self):
        f = SimpleUploadedFile('cv.docx', _docx_with('payload.exe', EXE))
        err = validate_document_upload(f)
        self.assertIsNotNone(err)

    def test_ole_doc_with_embedded_executable_is_refused(self):
        # An OLE .doc is accepted by both CV endpoints and skipped the PDF-only
        # body scan (Fable review 2026-08-27). A PE hidden in it must be refused.
        ole = b'\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1' + b'\x00' * 512 + self._FAKE_PE
        f = SimpleUploadedFile('cv.doc', ole)
        err = validate_document_upload(f, allowed=('pdf', 'doc', 'docx'))
        self.assertIsNotNone(err)
        self.assertIn('hidden', err.lower())

    def test_pdf_with_leading_bom_is_accepted(self):
        f = SimpleUploadedFile('cv.pdf', b'\xef\xbb\xbf' + PDF,
                               content_type='application/pdf')
        self.assertIsNone(validate_document_upload(f))

    def test_pdf_with_leading_whitespace_is_accepted(self):
        f = SimpleUploadedFile('cv.pdf', b'\r\n   ' + PDF,
                               content_type='application/pdf')
        self.assertIsNone(validate_document_upload(f))

    def test_clean_pdf_and_docx_still_accepted(self):
        self.assertIsNone(validate_document_upload(
            SimpleUploadedFile('cv.pdf', PDF)))
        self.assertIsNone(validate_document_upload(
            SimpleUploadedFile('cv.docx', _docx_bytes())))


class PublicApplyTests(TestCase):
    def setUp(self):
        self.req = JobRequisition.objects.create(
            title='Underwriter', department='UW', status='open')

    def _post(self, **over):
        data = {'full_name': 'Test Person', 'email': 'test@example.com',
                'phone': '+267 71 000 000',
                'cv': SimpleUploadedFile('cv.pdf', PDF, content_type='application/pdf')}
        data.update(over)
        return self.client.post(f'/api/v1/recruitment/public/jobs/{self.req.id}/apply/', data)

    def test_exe_renamed_pdf_is_rejected_at_the_public_form(self):
        r = self._post(cv=SimpleUploadedFile('cv.pdf', EXE, content_type='application/pdf'))
        self.assertEqual(r.status_code, 400)
        self.assertEqual(Candidate.objects.count(), 0)

    def test_invalid_email_is_rejected(self):
        r = self._post(email='not-an-email')
        self.assertEqual(r.status_code, 400)
        self.assertEqual(Candidate.objects.count(), 0)

    def test_a_valid_application_still_works(self):
        r = self._post()
        self.assertIn(r.status_code, (200, 201))
        self.assertEqual(Candidate.objects.count(), 1)


class HrUploadTests(TestCase):
    def setUp(self):
        self.hr = User.objects.create_superuser('hr', 'hr@x.co', 'x')
        self.client.force_login(self.hr)
        self.req = JobRequisition.objects.create(
            title='Claims Officer', department='Claims', status='open')

    def _upload(self, **over):
        data = {'full_name': 'Dupe Person', 'email': 'dupe@example.com',
                'cv': SimpleUploadedFile('cv.pdf', PDF, content_type='application/pdf')}
        data.update(over)
        return self.client.post(
            f'/api/v1/recruitment/requisitions/{self.req.id}/candidates/', data)

    def test_hr_upload_refuses_an_executable(self):
        r = self._upload(cv=SimpleUploadedFile('cv.pdf', EXE))
        self.assertEqual(r.status_code, 400)
        self.assertEqual(Candidate.objects.count(), 0)

    def test_duplicate_hr_upload_reuses_the_candidate(self):
        self._upload()
        self._upload()
        self.assertEqual(Candidate.objects.filter(email__iexact='dupe@example.com').count(), 1)
        self.assertEqual(Application.objects.filter(requisition=self.req).count(), 1)


    def test_new_cv_rescores_the_candidates_other_applications(self):
        # A Candidate is the PERSON — their CV is shared. Replacing it must not
        # leave another vacancy showing a score for a CV that is no longer there.
        other_req = JobRequisition.objects.create(
            title='Reinsurance Analyst', department='UW', status='open',
            required_skills=['reinsurance', 'excel'])
        self._upload()
        cand = Candidate.objects.get(email__iexact='dupe@example.com')
        other_app = Application.objects.create(
            requisition=other_req, candidate=cand, match_score=99)
        # Re-upload with a CV that clearly matches the other vacancy's skills.
        cv = SimpleUploadedFile(
            'cv.pdf', PDF + b'reinsurance treaty and advanced excel modelling\n')
        self._upload(cv=cv)
        other_app.refresh_from_db()
        self.assertNotEqual(other_app.match_score, 99,
                            'other application kept a score for a replaced CV')


    def test_email_belonging_to_a_different_person_is_refused(self):
        # A typo must not silently overwrite someone else's record.
        self._upload()
        r = self._upload(full_name='Someone Else Entirely')
        self.assertEqual(r.status_code, 409)
        cand = Candidate.objects.get(email__iexact='dupe@example.com')
        self.assertEqual(cand.full_name, 'Dupe Person')

    def test_existing_candidate_added_without_a_cv_keeps_their_score(self):
        self._upload()
        other_req = JobRequisition.objects.create(
            title='Second Role', department='UW', status='open',
            required_skills=['underwriting'])
        cand = Candidate.objects.get(email__iexact='dupe@example.com')
        cand.cv_text = 'seasoned underwriting professional'
        cand.skills = ['underwriting']
        cand.save(update_fields=['cv_text', 'skills'])
        r = self.client.post(
            f'/api/v1/recruitment/requisitions/{other_req.id}/candidates/',
            {'full_name': 'Dupe Person', 'email': 'dupe@example.com'})
        self.assertIn(r.status_code, (200, 201))
        app = Application.objects.get(requisition=other_req, candidate=cand)
        self.assertGreater(app.match_score, 0,
                           'scored against empty text despite a CV on file')


class ScorecardTests(TestCase):
    def setUp(self):
        self.hr = User.objects.create_superuser('hr', 'hr@x.co', 'x')
        self.client.force_login(self.hr)
        req = JobRequisition.objects.create(title='Analyst', department='Fin', status='open')
        cand = Candidate.objects.create(full_name='S Person', email='s@example.com')
        self.app = Application.objects.create(requisition=req, candidate=cand)

    def _post(self, payload):
        return self.client.post(
            f'/api/v1/recruitment/applications/{self.app.id}/scorecards/',
            payload, content_type='application/json')

    def test_out_of_range_score_is_rejected_not_clamped(self):
        r = self._post({'round': 'interview_1', 'score': 999, 'recommendation': 'yes'})
        self.assertEqual(r.status_code, 400)
        self.assertEqual(InterviewScorecard.objects.count(), 0)

    def test_blank_score_is_rejected_not_saved_as_zero(self):
        r = self._post({'round': 'interview_1', 'score': '', 'recommendation': 'yes'})
        self.assertEqual(r.status_code, 400)
        self.assertEqual(InterviewScorecard.objects.count(), 0)

    def test_negative_score_is_rejected(self):
        r = self._post({'round': 'interview_1', 'score': -5, 'recommendation': 'yes'})
        self.assertEqual(r.status_code, 400)

    def test_valid_score_is_stored_exactly(self):
        r = self._post({'round': 'interview_1', 'score': 72, 'recommendation': 'yes'})
        self.assertEqual(r.status_code, 201)
        self.assertEqual(InterviewScorecard.objects.get().score, 72)


class StageTransitionTests(TestCase):
    def setUp(self):
        self.hr = User.objects.create_superuser('hr', 'hr@x.co', 'x')
        self.client.force_login(self.hr)
        self.req = JobRequisition.objects.create(title='UW', department='UW', status='open')
        cand = Candidate.objects.create(full_name='Stage Person', email='st@example.com')
        self.app = Application.objects.create(requisition=self.req, candidate=cand)

    def _move(self, stage):
        return self.client.post(
            f'/api/v1/recruitment/applications/{self.app.id}/stage/',
            {'stage': stage}, content_type='application/json')

    def test_applied_straight_to_hired_is_refused(self):
        r = self._move('hired')
        self.assertEqual(r.status_code, 409)
        self.app.refresh_from_db()
        self.assertEqual(self.app.stage, 'applied')

    def test_normal_forward_move_still_works(self):
        self.assertEqual(self._move('screening').status_code, 200)
        self.app.refresh_from_db()
        self.assertEqual(self.app.stage, 'screening')

    def test_stage_change_writes_an_audit_row(self):
        # Manus QC 2026-08-27 (HIGH): a stage move used to leave no audit trail.
        from core.models import AuditLog
        self.assertEqual(self._move('screening').status_code, 200)
        row = AuditLog.objects.filter(
            table_name='recruitment.Application',
            record_id=str(self.app.id)).order_by('-created_at').first()
        self.assertIsNotNone(row)
        self.assertEqual(row.old_values.get('stage'), 'applied')
        self.assertEqual(row.new_values.get('stage'), 'screening')
        self.assertEqual(row.user_id, self.hr.id)

    def test_hired_needs_an_approved_authority(self):
        self.app.stage = 'offer'
        self.app.save(update_fields=['stage'])
        # No authority yet → refused.
        self.assertEqual(self._move('hired').status_code, 409)
        # A PENDING authority is not enough.
        auth = AuthorityToRecruit.objects.create(
            person_name='Stage Person', requisition=self.req, application=self.app,
            status=AuthorityToRecruit.Status.PENDING)
        self.assertEqual(self._move('hired').status_code, 409)
        # Approved → allowed.
        auth.status = AuthorityToRecruit.Status.APPROVED
        auth.save(update_fields=['status'])
        self.assertEqual(self._move('hired').status_code, 200)
        self.app.refresh_from_db()
        self.assertEqual(self.app.stage, 'hired')

    def test_rejecting_from_any_live_stage_is_allowed(self):
        self.assertEqual(self._move('rejected').status_code, 200)


class CandidateIdentityTests(TestCase):
    """Manus QC 2026-08-27 (MEDIUM): one person = one Candidate, and a safe
    merge for any that predate the constraint."""

    def test_email_is_normalised_on_save(self):
        c = Candidate.objects.create(full_name='A', email='  Ann@X.com ')
        c.refresh_from_db()
        self.assertEqual(c.email, 'ann@x.com')

    def test_duplicate_email_is_refused_at_the_db(self):
        from django.db import IntegrityError, transaction
        Candidate.objects.create(full_name='A', email='dup@x.com')
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                Candidate.objects.create(full_name='B', email='DUP@x.com')

    def test_blank_emails_are_exempt(self):
        Candidate.objects.create(full_name='Walk-in 1', email='')
        Candidate.objects.create(full_name='Walk-in 2', email='')   # no error

    def test_merge_moves_applications_and_drops_collisions(self):
        from recruitment.merge import merge_candidates
        req1 = JobRequisition.objects.create(title='R1', department='D', status='open')
        req2 = JobRequisition.objects.create(title='R2', department='D', status='open')
        keep = Candidate.objects.create(full_name='Keep', email='keep@x.com')
        drop = Candidate.objects.create(full_name='Drop', email='drop@x.com')
        Application.objects.create(requisition=req1, candidate=keep)
        Application.objects.create(requisition=req1, candidate=drop)   # collides -> dropped
        Application.objects.create(requisition=req2, candidate=drop)   # unique  -> moved
        out = merge_candidates(keep, drop)
        self.assertEqual(out['moved'], 1)
        self.assertEqual(out['dropped_dup'], 1)
        self.assertFalse(Candidate.objects.filter(pk=drop.pk).exists())
        self.assertEqual(Application.objects.filter(candidate=keep).count(), 2)
