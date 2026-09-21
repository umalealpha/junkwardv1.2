"""
hris/test_disciplinary.py — disciplinary chain (CFO directive 2026-07-22).

Run:  python manage.py test hris.test_disciplinary
"""
from __future__ import annotations

import tempfile
from datetime import date, timedelta
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from rest_framework.test import APIRequestFactory, force_authenticate

from django.core import mail
from django.utils import timezone

from hris import disciplinary_notify as notify
from hris import disciplinary_service as svc
from hris.amendment_service import UNAMI_EMAIL
from hris.disciplinary_models import DisciplinaryAttachment, DisciplinaryCase
from hris.disciplinary_views import (attach_evidence, cases, delete_evidence,
                                    hr_review as hr_review_view,
                                    issue_inquiry as issue_inquiry_view)
from hris.feature_views import my_disciplinary, my_disciplinary_respond
from payroll.models import Employee

User = get_user_model()

# >= 50 words — a proper factual description of a supplier-payment error.
ALLEGATION = (
    'The employee processed a supplier payment to a South African vendor using the '
    'wrong beneficiary bank account and an incorrect exchange rate, and did so without '
    'obtaining the required second authorisation from a finance approver as the payment '
    'policy demands. This resulted in an overpayment that had to be recovered from the '
    'vendor over several days, created a reconciliation exception in the ledger for the '
    'month, and required manual correction by two other members of the finance team.')


@override_settings(MEDIA_ROOT=tempfile.mkdtemp())
class DisciplinaryTests(TestCase):
    def setUp(self):
        self.factory = APIRequestFactory()
        self.cfo = User.objects.create_superuser('discfo', 'discfo@alphadirect.co.bw', 'pw')
        self.hr = User.objects.create_user('dishr', UNAMI_EMAIL, 'pw')
        self.mgr = User.objects.create_user('dismgr', 'dismgr@alphadirect.co.bw', 'pw')
        self.mgr_emp = Employee.objects.create(employee_number='DIS-MGR', full_name='Mgr Boss',
                                               status='active', job_title='Operations Manager', user=self.mgr)
        # The subject has a login + a work email so the inquiry can be served and
        # they can reach their own response box.
        self.sub_user = User.objects.create_user('dissub', 'dissub@alphadirect.co.bw', 'pw')
        self.subject = Employee.objects.create(employee_number='DIS-SUB', full_name='Sub Ject',
                                               status='active', job_title='Junior Associate',
                                               email='dissub@alphadirect.co.bw', user=self.sub_user)

    def _raise(self, category='written', allegation=ALLEGATION, by=None):
        return svc.raise_case(raised_by=by or self.mgr, subject_employee=self.subject,
                              category=category, incident_date=date(2026, 7, 20),
                              allegation=allegation, proposed_action='Written warning on file.')

    def _served(self, category='written', by=None):
        """A case that has cleared natural justice: inquiry issued, employee
        answered. This is the normal precondition for HR review now."""
        c = self._raise(category, by=by)
        svc.issue_inquiry(c.id, self.hr)
        return svc.record_response(c.id, self.sub_user,
                                   response='I was on approved leave that day and did not '
                                            'process the payment. Here is my account of it.')

    def test_word_count_enforced(self):
        with self.assertRaises(ValidationError):
            self._raise(allegation='He paid the wrong supplier.')

    def test_raise_happy_path(self):
        c = self._raise()
        self.assertEqual(c.status, DisciplinaryCase.Status.PENDING_HR)
        self.assertEqual(c.raised_by_email, 'dismgr@alphadirect.co.bw')
        self.assertEqual(c.subject_name, 'Sub Ject')

    def test_post_with_string_date_returns_201_not_500(self):
        # Regression (Bharath, 2026-08-08): the API sends incident_date as a
        # string. The service assigned it straight to the DateField, so the
        # response serializer called .isoformat() on a str → HTTP 500 while the
        # row was already committed. Exercise the full view POST path.
        req = self.factory.post('/hris/api/disciplinary/', {
            'subject_employee_id': str(self.subject.id),
            'category': 'written',
            'incident_date': '2026-08-08',
            'allegation': ALLEGATION,
            'proposed_action': '',
        }, format='json')
        force_authenticate(req, user=self.mgr)
        resp = cases(req)
        self.assertEqual(resp.status_code, 201, getattr(resp, 'data', None))
        self.assertEqual(resp.data['incident_date'], '2026-08-08')

    def test_bad_date_string_rejected_400_not_500(self):
        req = self.factory.post('/hris/api/disciplinary/', {
            'subject_employee_id': str(self.subject.id),
            'category': 'written',
            'incident_date': 'not-a-date',
            'allegation': ALLEGATION,
            'proposed_action': '',
        }, format='json')
        force_authenticate(req, user=self.mgr)
        resp = cases(req)
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(DisciplinaryCase.objects.count(), 0)

    def test_non_manager_cannot_raise(self):
        ess = User.objects.create_user('disess', 'disess@alphadirect.co.bw', 'pw')
        Employee.objects.create(employee_number='DIS-ESS', full_name='Ess Ess', status='active',
                                job_title='Junior Analyst', user=ess)
        with self.assertRaises(ValidationError):
            self._raise(by=ess)

    def test_cannot_raise_about_self(self):
        with self.assertRaises(ValidationError):
            svc.raise_case(raised_by=self.mgr, subject_employee=self.mgr_emp, category='written',
                           incident_date=date(2026, 7, 20), allegation=ALLEGATION)

    def test_minor_issued_after_hr(self):
        c = self._served('written')
        c2 = svc.hr_review(c.id, self.hr)
        self.assertEqual(c2.status, DisciplinaryCase.Status.ISSUED)
        self.assertIsNotNone(c2.issued_at)
        self.assertEqual(c2.hr_reviewer_id, self.hr.id)

    def test_serious_requires_cfo(self):
        c = self._served('dismissal')
        c2 = svc.hr_review(c.id, self.hr)
        self.assertEqual(c2.status, DisciplinaryCase.Status.PENDING_CFO)
        c3 = svc.cfo_signoff(c.id, self.cfo)
        self.assertEqual(c3.status, DisciplinaryCase.Status.ISSUED)
        self.assertEqual(c3.cfo_approver_id, self.cfo.id)

    def test_sod_raiser_cannot_review(self):
        # An HR user who raised the case cannot also HR-review it.
        c = self._served('written', by=self.hr)
        with self.assertRaises(ValidationError):
            svc.hr_review(c.id, self.hr)

    def test_reject_at_hr(self):
        c = self._raise('written')
        c2 = svc.reject(c.id, self.hr, notes='Insufficient evidence.')
        self.assertEqual(c2.status, DisciplinaryCase.Status.REJECTED)
        self.assertEqual(c2.rejected_stage, 'hr')

    def test_visibility_predicates(self):
        self.assertTrue(svc.can_view_all(self.cfo))
        self.assertTrue(svc.can_view_all(self.hr))
        self.assertFalse(svc.can_view_all(self.mgr))
        self.assertTrue(svc.can_raise(self.mgr))

    # ── Evidence attachments (CFO directive 2026-07-22) ──────────────────────

    def _pdf(self, name='evidence.pdf'):
        return SimpleUploadedFile(name, b'%PDF-1.4 dummy evidence bytes',
                                  content_type='application/pdf')

    def _attach(self, case, user, upload):
        req = self.factory.post(f'/hris/api/disciplinary/{case.id}/attach/',
                                {'file': upload}, format='multipart')
        force_authenticate(req, user=user)
        return attach_evidence(req, case_id=str(case.id))

    def test_attach_creates_row_bound_to_case(self):
        c = self._raise('written')
        resp = self._attach(c, self.hr, self._pdf())
        self.assertEqual(resp.status_code, 201)
        atts = DisciplinaryAttachment.objects.filter(case=c)
        self.assertEqual(atts.count(), 1)
        att = atts.first()
        self.assertEqual(att.case_id, c.id)
        self.assertEqual(att.filename, 'evidence.pdf')
        self.assertEqual(att.uploaded_by_id, self.hr.id)
        self.assertGreater(att.size, 0)

    def test_non_viewer_cannot_attach(self):
        c = self._raise('written')
        ess = User.objects.create_user('disess2', 'disess2@alphadirect.co.bw', 'pw')
        Employee.objects.create(employee_number='DIS-ESS2', full_name='Ess Two',
                                status='active', job_title='Junior Analyst', user=ess)
        resp = self._attach(c, ess, self._pdf())
        self.assertEqual(resp.status_code, 403)
        self.assertEqual(DisciplinaryAttachment.objects.filter(case=c).count(), 0)

    def test_disallowed_file_type_rejected(self):
        c = self._raise('written')
        bad = SimpleUploadedFile('malware.exe', b'MZ dummy',
                                 content_type='application/octet-stream')
        resp = self._attach(c, self.hr, bad)
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(DisciplinaryAttachment.objects.filter(case=c).count(), 0)

    def test_raiser_can_delete_own_evidence(self):
        c = self._raise('written')
        self.assertEqual(self._attach(c, self.mgr, self._pdf()).status_code, 201)
        att = DisciplinaryAttachment.objects.get(case=c)
        req = self.factory.post(f'/hris/api/disciplinary/attachment/{att.id}/delete/')
        force_authenticate(req, user=self.mgr)
        resp = delete_evidence(req, attachment_id=str(att.id))
        self.assertEqual(resp.status_code, 204)
        self.assertEqual(DisciplinaryAttachment.objects.filter(case=c).count(), 0)


# ── Natural justice (CFO directive 2026-08-11) ───────────────────────────────
# The gap these close: a warning could be issued with no record that the employee
# was ever told the allegation or given a chance to answer.

@override_settings(MEDIA_ROOT=tempfile.mkdtemp())
class NaturalJusticeTests(DisciplinaryTests):
    """Inherits setUp + helpers from DisciplinaryTests."""

    # 1 — the issue gate ------------------------------------------------------

    def test_hr_cannot_issue_before_inquiry_served(self):
        """THE CORE FIX. Without the gate in hr_review this passes straight to
        ISSUED — which is the challengeable outcome the CFO found on 2026-08-11."""
        c = self._raise('written')
        with self.assertRaises(ValidationError) as ctx:
            svc.hr_review(c.id, self.hr)
        self.assertIn('Issue the inquiry', '; '.join(ctx.exception.messages))
        c.refresh_from_db()
        self.assertEqual(c.status, DisciplinaryCase.Status.PENDING_HR)
        self.assertIsNone(c.issued_at)

    def test_hr_cannot_issue_while_deadline_still_open(self):
        c = self._raise('written')
        svc.issue_inquiry(c.id, self.hr)
        with self.assertRaises(ValidationError) as ctx:
            svc.hr_review(c.id, self.hr)
        self.assertIn('to respond', '; '.join(ctx.exception.messages))
        c.refresh_from_db()
        self.assertEqual(c.status, DisciplinaryCase.Status.PENDING_RESPONSE)
        self.assertIsNone(c.issued_at)

    def test_hr_may_issue_once_deadline_has_passed_with_no_response(self):
        """Silence is an answer — but only after the window really closed."""
        c = self._raise('written')
        svc.issue_inquiry(c.id, self.hr)
        DisciplinaryCase.objects.filter(id=c.id).update(
            response_deadline=timezone.localdate() - timedelta(days=1))
        c.refresh_from_db()
        self.assertTrue(c.deadline_passed)
        c2 = svc.hr_review(c.id, self.hr)
        self.assertEqual(c2.status, DisciplinaryCase.Status.ISSUED)

    def test_missing_deadline_never_reads_as_window_closed(self):
        """A null deadline must not satisfy the gate by defaulting to 'passed'."""
        c = self._raise('written')
        self.assertFalse(c.deadline_passed)
        self.assertFalse(c.natural_justice_satisfied)

    def test_deadline_floor_enforced(self):
        c = self._raise('written')
        with self.assertRaises(ValidationError):
            svc.issue_inquiry(c.id, self.hr,
                              deadline=timezone.localdate().isoformat())   # same day

    def test_cannot_serve_inquiry_twice(self):
        c = self._raise('written')
        svc.issue_inquiry(c.id, self.hr)
        with self.assertRaises(ValidationError):
            svc.issue_inquiry(c.id, self.hr)

    def test_employee_with_no_email_cannot_be_served_silently(self):
        Employee.objects.filter(id=self.subject.id).update(email='')
        self.subject.refresh_from_db()
        c = self._raise('written')
        with self.assertRaises(ValidationError) as ctx:
            svc.issue_inquiry(c.id, self.hr)
        self.assertIn('no email address', '; '.join(ctx.exception.messages))
        c.refresh_from_db()
        self.assertIsNone(c.inquiry_issued_at)

    def test_rejecting_needs_no_inquiry(self):
        """Dropping a case favours the employee, so it needs no hearing."""
        c = self._raise('written')
        c2 = svc.reject(c.id, self.hr, notes='No case to answer.')
        self.assertEqual(c2.status, DisciplinaryCase.Status.REJECTED)
        self.assertEqual(c2.rejected_stage, 'hr')

    def test_reject_while_awaiting_response_is_an_hr_stage_rejection(self):
        """Regression guard: PENDING_RESPONSE must not fall through to the 'cfo'
        stage, which would demand CFO authority to drop an untried case."""
        c = self._raise('written')
        svc.issue_inquiry(c.id, self.hr)
        c2 = svc.reject(c.id, self.hr, notes='Manager withdrew it.')
        self.assertEqual(c2.rejected_stage, 'hr')

    # 2 — the letter ----------------------------------------------------------

    def test_inquiry_letter_reaches_the_employee_with_reply_allowed(self):
        c = self._raise('written')
        mail.outbox = []
        req = self.factory.post(f'/hris/api/disciplinary/{c.id}/issue-inquiry/', {}, format='json')
        force_authenticate(req, user=self.hr)
        resp = issue_inquiry_view(req, case_id=str(c.id))
        self.assertEqual(resp.status_code, 200, getattr(resp, 'data', None))
        self.assertEqual(resp.data['status'], 'pending_response')

        self.assertEqual(len(mail.outbox), 1)
        msg = mail.outbox[0]
        # The employee is the direct recipient — the whole gap being closed.
        self.assertIn('dissub@alphadirect.co.bw', msg.to)
        body = msg.body + ''.join(str(a[0]) for a in getattr(msg, 'alternatives', []))
        # The red "do not reply, log it in omni" banner must be OFF: the letter
        # asks the employee a question (standing rule). Assert both the marker
        # the injector stamps and the prose, so neither can come back unnoticed.
        self.assertNotIn('data-omni-noreply', body)
        self.assertNotIn('do not reply', body.lower())
        self.assertIn(UNAMI_EMAIL, msg.reply_to)
        # They must be able to read what they are answering.
        self.assertIn('supplier payment', body)

    def test_case_not_marked_served_when_the_letter_fails(self):
        """A failed send must roll the state back — never a case that claims it
        was served when nothing left the building."""
        c = self._raise('written')
        req = self.factory.post(f'/hris/api/disciplinary/{c.id}/issue-inquiry/', {}, format='json')
        force_authenticate(req, user=self.hr)
        with mock.patch('hris.disciplinary_notify.send_html_with_cfo_cc', return_value=0):
            resp = issue_inquiry_view(req, case_id=str(c.id))
        self.assertEqual(resp.status_code, 502)
        c.refresh_from_db()
        self.assertIsNone(c.inquiry_issued_at)
        self.assertEqual(c.status, DisciplinaryCase.Status.PENDING_HR)
        # And the block still bites, because nothing was served.
        with self.assertRaises(ValidationError):
            svc.hr_review(c.id, self.hr)

    # 3 — the subject's own response -----------------------------------------

    def test_subject_sees_and_answers_their_own_case(self):
        c = self._raise('written')
        svc.issue_inquiry(c.id, self.hr)

        req = self.factory.get('/hris/api/my-disciplinary/')
        force_authenticate(req, user=self.sub_user)
        resp = my_disciplinary(req)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data['count'], 1)
        row = resp.data['cases'][0]
        self.assertTrue(row['awaiting_my_response'])
        self.assertTrue(row['can_respond'])
        self.assertIn('supplier payment', row['allegation'])
        # Only the fields needed to answer — never who reported them, never HR's
        # notes, never the evidence files.
        for leaked in ('raised_by', 'decision_notes', 'attachments', 'hr_reviewer'):
            self.assertNotIn(leaked, row)

        req = self.factory.post(f'/hris/api/my-disciplinary/{c.id}/respond/',
                                {'response': 'I was on approved leave that day.'}, format='json')
        force_authenticate(req, user=self.sub_user)
        resp = my_disciplinary_respond(req, case_id=str(c.id))
        self.assertEqual(resp.status_code, 200, getattr(resp, 'data', None))
        c.refresh_from_db()
        self.assertEqual(c.employee_response, 'I was on approved leave that day.')
        self.assertIsNotNone(c.employee_responded_at)
        # Back on HR's desk, and now reviewable.
        self.assertEqual(c.status, DisciplinaryCase.Status.PENDING_HR)
        self.assertTrue(c.natural_justice_satisfied)
        self.assertEqual(svc.hr_review(c.id, self.hr).status, DisciplinaryCase.Status.ISSUED)

    def test_response_is_written_by_the_employee_not_hr(self):
        c = self._raise('written')
        svc.issue_inquiry(c.id, self.hr)
        for impostor in (self.hr, self.cfo, self.mgr):
            with self.assertRaises(ValidationError):
                svc.record_response(c.id, impostor, response='Speaking for them.')
        c.refresh_from_db()
        self.assertEqual(c.employee_response, '')

    def test_one_response_only(self):
        c = self._raise('written')
        svc.issue_inquiry(c.id, self.hr)
        svc.record_response(c.id, self.sub_user, response='First and final account.')
        with self.assertRaises(ValidationError):
            svc.record_response(c.id, self.sub_user, response='Rewriting history.')
        c.refresh_from_db()
        self.assertEqual(c.employee_response, 'First and final account.')

    def test_empty_response_rejected(self):
        c = self._raise('written')
        svc.issue_inquiry(c.id, self.hr)
        with self.assertRaises(ValidationError):
            svc.record_response(c.id, self.sub_user, response='   ')

    def test_late_response_still_accepted_while_case_open(self):
        c = self._raise('written')
        svc.issue_inquiry(c.id, self.hr)
        DisciplinaryCase.objects.filter(id=c.id).update(
            response_deadline=timezone.localdate() - timedelta(days=3))
        c2 = svc.record_response(c.id, self.sub_user, response='Late, but this is my account.')
        self.assertTrue(c2.has_response)

    # 3b — HR capturing a reply that arrived outside omni ---------------------

    def test_hr_can_record_a_reply_received_outside_omni(self):
        """The real-world case: the employee emails Unami or hands in a letter.
        Without this the reply stays in a mailbox — the original problem."""
        c = self._raise('written')
        svc.issue_inquiry(c.id, self.hr)
        c2 = svc.record_response_on_behalf(
            c.id, self.hr, response='Emailed reply: I was on leave.',
            received_on=(timezone.localdate() - timedelta(days=1)).isoformat())
        self.assertTrue(c2.has_response)
        self.assertEqual(c2.status, DisciplinaryCase.Status.PENDING_HR)
        # And it is marked as HR's transcription, NOT the employee's own words.
        self.assertFalse(c2.response_is_self_submitted)
        self.assertEqual(c2.response_recorded_by_id, self.hr.id)
        self.assertEqual(svc.hr_review(c.id, self.hr).status, DisciplinaryCase.Status.ISSUED)

    def test_self_submitted_response_is_not_flagged_as_transcribed(self):
        c = self._raise('written')
        svc.issue_inquiry(c.id, self.hr)
        c2 = svc.record_response(c.id, self.sub_user, response='My own words, my own login.')
        self.assertTrue(c2.response_is_self_submitted)
        self.assertIsNone(c2.response_recorded_by_id)

    def test_raiser_cannot_author_the_defence(self):
        c = self._raise('written')
        svc.issue_inquiry(c.id, self.hr)
        with self.assertRaises(ValidationError):
            svc.record_response_on_behalf(c.id, self.mgr, response='Speaking for them.')
        c.refresh_from_db()
        self.assertEqual(c.employee_response, '')

    def test_transcribed_response_cannot_overwrite_the_employees_own(self):
        c = self._raise('written')
        svc.issue_inquiry(c.id, self.hr)
        svc.record_response(c.id, self.sub_user, response='My own account.')
        with self.assertRaises(ValidationError):
            svc.record_response_on_behalf(c.id, self.hr, response='HR rewriting it.')
        c.refresh_from_db()
        self.assertEqual(c.employee_response, 'My own account.')

    def test_future_received_date_rejected(self):
        c = self._raise('written')
        svc.issue_inquiry(c.id, self.hr)
        with self.assertRaises(ValidationError):
            svc.record_response_on_behalf(
                c.id, self.hr, response='Received tomorrow, somehow.',
                received_on=(timezone.localdate() + timedelta(days=1)).isoformat())

    # 3c — a reply that arrives AFTER the case was decided --------------------
    # CFO decision 2026-08-11, from the live case: the warning was issued on
    # 11-Aug while the employee's deadline was 13-Aug. Her answer must land on the
    # file rather than in a mailbox — without reopening the decision.

    def _issued_case(self):
        c = self._served('written')
        return svc.hr_review(c.id, self.hr)

    def test_late_reply_can_be_filed_against_an_issued_case(self):
        c = self._raise('written')
        svc.issue_inquiry(c.id, self.hr)
        # Decided before the employee answered (deadline expired).
        DisciplinaryCase.objects.filter(id=c.id).update(
            response_deadline=timezone.localdate() - timedelta(days=1))
        issued = svc.hr_review(c.id, self.hr)
        self.assertEqual(issued.status, DisciplinaryCase.Status.ISSUED)

        after = svc.record_response_on_behalf(
            c.id, self.hr, response='My reply, sent after I got the letter.')
        self.assertEqual(after.employee_response, 'My reply, sent after I got the letter.')
        # The decision STANDS — filing a document must not un-issue a warning.
        self.assertEqual(after.status, DisciplinaryCase.Status.ISSUED)
        self.assertEqual(after.issued_at, issued.issued_at)
        # And the record tells the true sequence.
        self.assertTrue(after.response_arrived_after_decision)

    def test_late_reply_cannot_be_backdated_before_the_decision(self):
        """Otherwise a reply filed today could read as if it had been in hand
        before the outcome — the record would lie about the sequence."""
        c = self._raise('written')
        svc.issue_inquiry(c.id, self.hr)
        DisciplinaryCase.objects.filter(id=c.id).update(
            response_deadline=timezone.localdate() - timedelta(days=1))
        svc.hr_review(c.id, self.hr)
        with self.assertRaises(ValidationError) as ctx:
            svc.record_response_on_behalf(
                c.id, self.hr, response='Pretending this came in first.',
                received_on=(timezone.localdate() - timedelta(days=5)).isoformat())
        self.assertIn('already issued', '; '.join(ctx.exception.messages))
        c.refresh_from_db()
        self.assertEqual(c.employee_response, '')

    def test_reply_received_the_same_day_the_case_was_decided_is_accepted(self):
        """Issued 09:00, reply arrives 15:00 the same day. A midnight timestamp
        would read as 'before the decision' and be refused, so the guard compares
        dates. This was a real bug in the first cut of it."""
        c = self._raise('written')
        svc.issue_inquiry(c.id, self.hr)
        DisciplinaryCase.objects.filter(id=c.id).update(
            response_deadline=timezone.localdate() - timedelta(days=1))
        svc.hr_review(c.id, self.hr)
        after = svc.record_response_on_behalf(
            c.id, self.hr, response='Replied the same afternoon.',
            received_on=timezone.localdate().isoformat())
        self.assertEqual(after.employee_response, 'Replied the same afternoon.')
        self.assertEqual(after.status, DisciplinaryCase.Status.ISSUED)

    def test_response_before_the_decision_is_not_flagged_as_late(self):
        c = self._issued_case()
        self.assertTrue(c.has_response)
        self.assertFalse(c.response_arrived_after_decision)

    def test_rejected_case_takes_no_late_reply(self):
        c = self._raise('written')
        svc.issue_inquiry(c.id, self.hr)
        svc.reject(c.id, self.hr, notes='Withdrawn.')
        with self.assertRaises(ValidationError):
            svc.record_response_on_behalf(c.id, self.hr, response='Too late, case dropped.')

    # 4 — the carve-out must not leak anyone else's case ----------------------

    def _other_case(self):
        """A case about a DIFFERENT employee, raised by the same manager."""
        other_user = User.objects.create_user('disoth', 'disoth@alphadirect.co.bw', 'pw')
        other_emp = Employee.objects.create(employee_number='DIS-OTH', full_name='Oth Er',
                                            status='active', job_title='Clerk',
                                            email='disoth@alphadirect.co.bw', user=other_user)
        c = svc.raise_case(raised_by=self.mgr, subject_employee=other_emp, category='final',
                           incident_date=date(2026, 7, 21),
                           allegation=ALLEGATION.replace('employee', 'other employee'),
                           proposed_action='Final written warning.')
        svc.issue_inquiry(c.id, self.hr)
        return c, other_user

    def test_subject_self_view_returns_only_their_own_case(self):
        mine = self._raise('written')
        svc.issue_inquiry(mine.id, self.hr)
        theirs, _ = self._other_case()

        req = self.factory.get('/hris/api/my-disciplinary/')
        force_authenticate(req, user=self.sub_user)
        resp = my_disciplinary(req)
        ids = {row['id'] for row in resp.data['cases']}
        self.assertEqual(ids, {str(mine.id)})
        self.assertNotIn(str(theirs.id), ids)

    def test_subject_cannot_respond_on_another_employees_case(self):
        theirs, _ = self._other_case()
        req = self.factory.post(f'/hris/api/my-disciplinary/{theirs.id}/respond/',
                                {'response': 'Answering for someone else.'}, format='json')
        force_authenticate(req, user=self.sub_user)
        resp = my_disciplinary_respond(req, case_id=str(theirs.id))
        self.assertEqual(resp.status_code, 403)
        theirs.refresh_from_db()
        self.assertEqual(theirs.employee_response, '')
        self.assertIsNone(theirs.employee_responded_at)

    def test_unknown_case_id_and_someone_elses_look_identical(self):
        """403 either way, so the endpoint can't be used to discover whether a
        colleague has a disciplinary case."""
        import uuid as _uuid
        theirs, _ = self._other_case()
        missing = _uuid.uuid4()
        outs = []
        for cid in (theirs.id, missing):
            req = self.factory.post(f'/hris/api/my-disciplinary/{cid}/respond/',
                                    {'response': 'probe'}, format='json')
            force_authenticate(req, user=self.sub_user)
            r = my_disciplinary_respond(req, case_id=str(cid))
            outs.append((r.status_code, r.data.get('detail')))
        self.assertEqual(outs[0], outs[1])
        self.assertEqual(outs[0][0], 403)

    def test_subject_still_sees_nothing_on_the_hr_board(self):
        """The carve-out must NOT widen the HR-internal board. The subject must
        still get can_view_all=False and zero cases there — the exact prod
        behaviour verified for Kutlo on 2026-08-11, which must not regress."""
        mine = self._raise('written')
        svc.issue_inquiry(mine.id, self.hr)
        self._other_case()

        req = self.factory.get('/hris/api/disciplinary/')
        force_authenticate(req, user=self.sub_user)
        resp = cases(req)
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(resp.data['me']['can_view_all'])
        self.assertEqual(resp.data['cases'], [])
        self.assertEqual(resp.data['subjects'], [])

    def test_subject_cannot_reach_case_evidence(self):
        """The response carve-out grants NO access to evidence files."""
        mine = self._raise('written')
        self.assertEqual(self._attach(mine, self.mgr, self._pdf()).status_code, 201)
        svc.issue_inquiry(mine.id, self.hr)
        resp = self._attach(mine, self.sub_user, self._pdf('mine.pdf'))
        self.assertEqual(resp.status_code, 403)
        self.assertFalse(svc.can_view_all(self.sub_user))

    def test_subject_cannot_serve_an_inquiry_on_themselves(self):
        mine = self._raise('written')
        self.assertFalse(svc.can_issue_inquiry(mine, self.sub_user))
        req = self.factory.post(f'/hris/api/disciplinary/{mine.id}/issue-inquiry/', {}, format='json')
        force_authenticate(req, user=self.sub_user)
        self.assertEqual(issue_inquiry_view(req, case_id=str(mine.id)).status_code, 400)

    def test_login_with_no_employee_record_is_not_a_subject(self):
        """An identity check must be a positive match, never a fallback."""
        ghost = User.objects.create_user('disghost', 'disghost@alphadirect.co.bw', 'pw')
        mine = self._raise('written')
        self.assertFalse(svc.is_subject(mine, ghost))
        self.assertFalse(svc.is_subject(mine, None))

    # 5 — audit trail ---------------------------------------------------------

    def test_every_natural_justice_write_is_audit_logged(self):
        from core.models import AuditLog
        c = self._raise('written')
        svc.issue_inquiry(c.id, self.hr)
        svc.record_response(c.id, self.sub_user, response='My account of the day.')
        rows = AuditLog.objects.filter(record_id=str(c.id))
        self.assertGreaterEqual(rows.count(), 3)      # raise + inquiry + response
        actors = {r.user_id for r in rows}
        self.assertIn(self.sub_user.id, actors)       # the employee's own write
        self.assertIn(self.hr.id, actors)
@override_settings(MEDIA_ROOT=tempfile.mkdtemp())
class CfoOverrideOfARejectionTests(TestCase):
    """The CFO overturning an HR rejection (CFO directive 2026-08-12).

    Written warnings never reach the CFO — only suspensions and dismissals do —
    and a rejection was otherwise terminal, so there was no way to act on a case
    HR had turned down. Real case behind this: HR rejected a written warning on
    process ("issue must be raised through the manager before such escalation")
    rather than on the facts.

    The guards are the point. An override that anyone can apply, or that records
    no reason, or that quietly erases the rejection it overturned, would be worse
    than having no override at all.
    """

    def setUp(self):
        self.cfo = User.objects.create_superuser('ovcfo', 'ovcfo@alphadirect.co.bw', 'pw')
        self.hr = User.objects.create_user('ovhr', UNAMI_EMAIL, 'pw')
        self.mgr = User.objects.create_user('ovmgr', 'ovmgr@alphadirect.co.bw', 'pw')
        Employee.objects.create(employee_number='OV-MGR', full_name='Ov Boss', status='active',
                                job_title='Operations Manager', user=self.mgr)
        # The subject needs a work email and a login so the inquiry can be served
        # on them and they can answer it (natural justice, 2026-08-11).
        self.sub_user = User.objects.create_user('ovsub', 'ovsub@alphadirect.co.bw', 'pw')
        self.subject = Employee.objects.create(employee_number='OV-SUB', full_name='Ov Ject',
                                               status='active', job_title='Financial Controller',
                                               email='ovsub@alphadirect.co.bw', user=self.sub_user)
        self.reason = ('Repeated failure to settle supplier accounts on time is a direct '
                       'financial and reputational exposure to the company and warrants a '
                       'written warning despite the procedural objection raised.')

    def _rejected_case(self, category='written'):
        """A case HR rejected AFTER the employee was heard.

        The hearing steps were added on 2026-08-11: cfo_override also issues a
        case, so it now refuses when the employee was never asked. These tests are
        about the override mechanics — who may apply it, that the reason is kept,
        that the rejection is not erased — so the fixture runs the real path and
        leaves the natural-justice gate to be tested on its own, in
        OverrideRespectsNaturalJusticeTests below.
        """
        case = svc.raise_case(raised_by=self.mgr, subject_employee=self.subject,
                              category=category, incident_date=date(2026, 8, 8),
                              allegation=ALLEGATION, proposed_action='Written warning on file.')
        svc.issue_inquiry(case.id, self.hr)
        svc.record_response(case.id, self.sub_user,
                            response='I dispute the timeline and my account is set out here.')
        return svc.reject(case.id, self.hr,
                          notes='Issue must be raised through the manager before such esclation.')

    # ── what the CFO asked for ───────────────────────────────────────────────
    def test_the_cfo_can_overturn_a_rejection_and_the_case_is_issued(self):
        case = self._rejected_case()
        self.assertEqual(case.status, DisciplinaryCase.Status.REJECTED)
        out = svc.cfo_override(case.id, self.cfo, notes=self.reason)
        self.assertEqual(out.status, DisciplinaryCase.Status.ISSUED)
        self.assertIsNotNone(out.issued_at)
        self.assertEqual(out.cfo_approver_id, self.cfo.id)
        self.assertIsNotNone(out.cfo_approved_at)

    def test_the_override_reason_is_stored(self):
        case = self._rejected_case()
        out = svc.cfo_override(case.id, self.cfo, notes=self.reason)
        self.assertIn('reputational exposure', out.override_reason)

    # ── the guards ───────────────────────────────────────────────────────────
    def test_the_rejection_is_NOT_erased(self):
        """The file must keep reading honestly: HR rejected it, and why."""
        case = self._rejected_case()
        out = svc.cfo_override(case.id, self.cfo, notes=self.reason)
        self.assertEqual(out.rejected_by_id, self.hr.id)
        self.assertIsNotNone(out.rejected_at)
        self.assertEqual(out.rejected_stage, 'hr')
        self.assertIn('through the manager', out.decision_notes)
        self.assertNotEqual(out.decision_notes, out.override_reason)

    def test_HR_cannot_overturn_its_own_rejection(self):
        case = self._rejected_case()
        with self.assertRaises(ValidationError):
            svc.cfo_override(case.id, self.hr, notes=self.reason)

    def test_the_raising_manager_cannot_overturn_it(self):
        case = self._rejected_case()
        with self.assertRaises(ValidationError):
            svc.cfo_override(case.id, self.mgr, notes=self.reason)

    def test_a_substantive_reason_is_compulsory(self):
        case = self._rejected_case()
        for weak in ('', '   ', 'approved', 'I disagree with HR'):
            with self.assertRaises(ValidationError):
                svc.cfo_override(case.id, self.cfo, notes=weak)

    def test_an_open_case_cannot_be_overridden(self):
        """Override is only for rejections — it must not skip HR review."""
        case = svc.raise_case(raised_by=self.mgr, subject_employee=self.subject,
                              category='written', incident_date=date(2026, 8, 8),
                              allegation=ALLEGATION, proposed_action='Written warning.')
        self.assertEqual(case.status, DisciplinaryCase.Status.PENDING_HR)
        with self.assertRaises(ValidationError):
            svc.cfo_override(case.id, self.cfo, notes=self.reason)

    def test_it_cannot_be_applied_twice(self):
        case = self._rejected_case()
        svc.cfo_override(case.id, self.cfo, notes=self.reason)
        with self.assertRaises(ValidationError):
            svc.cfo_override(case.id, self.cfo, notes=self.reason)

    # ── the button ───────────────────────────────────────────────────────────
    def test_only_the_cfo_sees_the_override_button(self):
        case = self._rejected_case()
        self.assertTrue(svc.can_cfo_override(case, self.cfo))
        self.assertFalse(svc.can_cfo_override(case, self.hr))
        self.assertFalse(svc.can_cfo_override(case, self.mgr))

    def test_the_button_does_not_show_on_an_open_case(self):
        case = svc.raise_case(raised_by=self.mgr, subject_employee=self.subject,
                              category='written', incident_date=date(2026, 8, 8),
                              allegation=ALLEGATION, proposed_action='Written warning.')
        self.assertFalse(svc.can_cfo_override(case, self.cfo))

    def test_rejected_cases_stay_out_of_the_approvals_queue(self):
        """can_act_now drives the queue; a rejection must not resurface there."""
        case = self._rejected_case()
        self.assertFalse(svc.can_act_now(case, self.cfo))
        self.assertFalse(svc.can_act_now(case, self.hr))


# ── The CFO override must not be a way around natural justice ────────────────
# CFO decision 2026-08-12. cfo_override also ISSUES a case, so leaving it ungated
# would have made the gate on hr_review decorative — a control that can be walked
# around silently is worse than no control. Default closed; forcing is allowed but
# is deliberate and permanently stamped.

@override_settings(MEDIA_ROOT=tempfile.mkdtemp())
class OverrideRespectsNaturalJusticeTests(TestCase):
    def setUp(self):
        self.factory = APIRequestFactory()
        self.cfo = User.objects.create_superuser('njcfo', 'njcfo@alphadirect.co.bw', 'pw')
        self.hr = User.objects.create_user('njhr', UNAMI_EMAIL, 'pw')
        self.mgr = User.objects.create_user('njmgr', 'njmgr@alphadirect.co.bw', 'pw')
        Employee.objects.create(employee_number='NJ-MGR', full_name='Nj Mgr', status='active',
                                job_title='Operations Manager', user=self.mgr)
        self.sub_user = User.objects.create_user('njsub', 'njsub@alphadirect.co.bw', 'pw')
        self.subject = Employee.objects.create(employee_number='NJ-SUB', full_name='Nj Sub',
                                               status='active', job_title='Clerk',
                                               email='njsub@alphadirect.co.bw', user=self.sub_user)

    def _rejected(self):
        c = svc.raise_case(raised_by=self.mgr, subject_employee=self.subject, category='written',
                           incident_date=date(2026, 8, 8), allegation=ALLEGATION,
                           proposed_action='Written warning.')
        return svc.reject(c.id, self.hr, notes='Must go through the manager first.')

    REASON = ('The rejection was on process rather than on the facts and the underlying '
              'conduct still needs to be addressed on the record this month.')
    FORCED = ('The conduct is admitted in writing already and the matter cannot wait for '
              'the response window because the audit closes this week.')

    def test_override_refuses_when_the_employee_was_never_heard(self):
        """THE FIX. Without the gate the case walks to ISSUED with nobody asked."""
        c = self._rejected()
        with self.assertRaises(ValidationError) as ctx:
            svc.cfo_override(c.id, self.cfo, notes=self.REASON)
        msg = '; '.join(ctx.exception.messages)
        self.assertIn('not been asked for their side', msg)
        c.refresh_from_db()
        self.assertEqual(c.status, DisciplinaryCase.Status.REJECTED)
        self.assertIsNone(c.issued_at)

    def test_override_succeeds_once_the_employee_has_been_heard(self):
        c = self._rejected()
        svc.issue_inquiry(c.id, self.cfo)                    # revives the rejected case
        svc.record_response(c.id, self.sub_user, response='My side of it.')
        svc.reject(c.id, self.hr, notes='Still not persuaded.')
        out = svc.cfo_override(c.id, self.cfo, notes=self.REASON)
        self.assertEqual(out.status, DisciplinaryCase.Status.ISSUED)
        # No force needed, so no stamp.
        self.assertEqual(out.unheard_issue_reason, '')
        self.assertFalse(out.issued_without_being_heard)

    def test_cfo_can_force_past_it_with_a_reason_and_is_stamped(self):
        c = self._rejected()
        out = svc.cfo_override(c.id, self.cfo, notes=self.REASON,
                               issue_unheard_reason=self.FORCED)
        self.assertEqual(out.status, DisciplinaryCase.Status.ISSUED)
        self.assertEqual(out.unheard_issue_reason, self.FORCED)
        self.assertEqual(out.unheard_issued_by_id, self.cfo.id)
        # The stamp is derived from the facts, so it cannot be dressed up.
        self.assertTrue(out.issued_without_being_heard)
        # And the rejection it overturned still stands on the record.
        self.assertEqual(out.rejected_stage, 'hr')
        self.assertEqual(out.decision_notes, 'Must go through the manager first.')

    def test_a_token_force_reason_is_refused(self):
        c = self._rejected()
        with self.assertRaises(ValidationError):
            svc.cfo_override(c.id, self.cfo, notes=self.REASON, issue_unheard_reason='urgent')
        c.refresh_from_db()
        self.assertEqual(c.status, DisciplinaryCase.Status.REJECTED)

    def test_only_the_cfo_can_force(self):
        c = self._rejected()
        for who in (self.hr, self.mgr):
            with self.assertRaises(ValidationError):
                svc.cfo_override(c.id, who, notes=self.REASON,
                                 issue_unheard_reason=self.FORCED)

    def test_the_cfo_can_serve_an_inquiry_on_a_rejected_case(self):
        """The gate's error message tells the CFO to ask the employee first, so
        that path has to exist — otherwise forcing is the only option and the
        gate is theatre."""
        c = self._rejected()
        self.assertTrue(svc.can_issue_inquiry(c, self.cfo))
        self.assertFalse(svc.can_issue_inquiry(c, self.hr))   # CFO only, on a rejection
        out = svc.issue_inquiry(c.id, self.cfo)
        self.assertEqual(out.status, DisciplinaryCase.Status.PENDING_RESPONSE)
        self.assertIsNotNone(out.inquiry_issued_at)
        # Reviving it does not erase why HR rejected it.
        self.assertEqual(out.decision_notes, 'Must go through the manager first.')

    def test_forced_issue_is_visible_on_the_api(self):
        c = self._rejected()
        svc.cfo_override(c.id, self.cfo, notes=self.REASON, issue_unheard_reason=self.FORCED)
        req = self.factory.get('/hris/api/disciplinary/')
        force_authenticate(req, user=self.cfo)
        row = next(r for r in cases(req).data['cases'] if r['id'] == str(c.id))
        self.assertTrue(row['issued_without_being_heard'])
        self.assertEqual(row['unheard_issue_reason'], self.FORCED)


# ── Inviting a late response on an ALREADY-ISSUED case ───────────────────────
# CFO decision 2026-08-12, for the two live warnings decided before this stage
# existed (Kutlo Keitumele 11-Aug, Pako Kago 12-Aug). The employee gets to put
# their account on the file; the outcome already recorded does not change.

@override_settings(MEDIA_ROOT=tempfile.mkdtemp())
class LateInquiryOnIssuedCaseTests(TestCase):
    def setUp(self):
        self.factory = APIRequestFactory()
        self.cfo = User.objects.create_superuser('licfo', 'licfo@alphadirect.co.bw', 'pw')
        self.hr = User.objects.create_user('lihr', UNAMI_EMAIL, 'pw')
        self.mgr = User.objects.create_user('limgr', 'limgr@alphadirect.co.bw', 'pw')
        Employee.objects.create(employee_number='LI-MGR', full_name='Li Mgr', status='active',
                                job_title='Operations Manager', user=self.mgr)
        self.sub_user = User.objects.create_user('lisub', 'lisub@alphadirect.co.bw', 'pw')
        self.subject = Employee.objects.create(employee_number='LI-SUB', full_name='Li Sub',
                                               status='active', job_title='Accountant',
                                               email='lisub@alphadirect.co.bw', user=self.sub_user)

    def _issued_unheard(self):
        """A warning on the file with nobody having asked the employee — exactly
        the state the two live cases were in."""
        c = svc.raise_case(raised_by=self.mgr, subject_employee=self.subject, category='written',
                           incident_date=date(2026, 8, 5), allegation=ALLEGATION,
                           proposed_action='Written warning on file.')
        DisciplinaryCase.objects.filter(id=c.id).update(
            status=DisciplinaryCase.Status.ISSUED, issued_at=timezone.now())
        c.refresh_from_db()
        self.assertTrue(c.issued_without_being_heard)
        return c

    def test_hr_can_invite_a_late_response_without_un_issuing_the_warning(self):
        c = self._issued_unheard()
        issued_at = c.issued_at
        out = svc.issue_inquiry(c.id, self.hr)
        self.assertIsNotNone(out.inquiry_issued_at)
        self.assertIsNotNone(out.response_deadline)
        # THE POINT: the decision stands. Sending a letter must not reverse it.
        self.assertEqual(out.status, DisciplinaryCase.Status.ISSUED)
        self.assertEqual(out.issued_at, issued_at)

    def test_the_letter_does_not_claim_the_decision_is_still_open(self):
        """It would be untrue — the warning is already on their file."""
        c = self._issued_unheard()
        svc.issue_inquiry(c.id, self.hr)
        c.refresh_from_db()
        mail.outbox = []
        self.assertEqual(notify.notify_inquiry(c), 1)
        body = ''.join(str(a[0]) for a in mail.outbox[0].alternatives)
        # The pre-decision wording would be a lie on a case already decided.
        self.assertNotIn('No decision will be recorded until', body)
        self.assertNotIn('Category under consideration', body)
        # What it must say instead.
        self.assertIn('has been recorded on your file', body)
        self.assertIn('should not have happened', body)
        self.assertIn('does not by itself', body)
        self.assertIn('Outcome already recorded', body)
        self.assertNotIn('data-omni-noreply', body)          # banner still OFF
        self.assertIn('your file', mail.outbox[0].subject)

    def test_the_employee_can_answer_and_the_outcome_is_untouched(self):
        c = self._issued_unheard()
        svc.issue_inquiry(c.id, self.hr)
        issued_at = DisciplinaryCase.objects.get(id=c.id).issued_at

        req = self.factory.get('/hris/api/my-disciplinary/')
        force_authenticate(req, user=self.sub_user)
        row = my_disciplinary(req).data['cases'][0]
        self.assertTrue(row['can_respond'])
        self.assertTrue(row['response_invited_after_decision'])

        req2 = self.factory.post(f'/hris/api/my-disciplinary/{c.id}/respond/',
                                 {'response': 'This is my account, given after the fact.'},
                                 format='json')
        force_authenticate(req2, user=self.sub_user)
        self.assertEqual(my_disciplinary_respond(req2, case_id=str(c.id)).status_code, 200)

        c.refresh_from_db()
        self.assertEqual(c.employee_response, 'This is my account, given after the fact.')
        self.assertEqual(c.status, DisciplinaryCase.Status.ISSUED)   # still issued
        self.assertEqual(c.issued_at, issued_at)                    # same decision
        self.assertTrue(c.response_arrived_after_decision)
        self.assertTrue(c.response_is_self_submitted)

    def test_an_issued_case_with_no_inquiry_is_still_closed_to_responses(self):
        """Only an INVITED late response is accepted — not a free-for-all on any
        historical case."""
        c = self._issued_unheard()
        with self.assertRaises(ValidationError):
            svc.record_response(c.id, self.sub_user, response='Unsolicited.')

    def test_the_raising_manager_cannot_invite_on_a_decided_case(self):
        c = self._issued_unheard()
        self.assertFalse(svc.can_issue_inquiry(c, self.mgr))
        self.assertFalse(svc.can_issue_inquiry(c, self.sub_user))
        self.assertTrue(svc.can_issue_inquiry(c, self.hr))
        self.assertTrue(svc.can_issue_inquiry(c, self.cfo))

    def test_it_cannot_be_invited_twice(self):
        c = self._issued_unheard()
        svc.issue_inquiry(c.id, self.hr)
        with self.assertRaises(ValidationError):
            svc.issue_inquiry(c.id, self.hr)


class SalutationTests(TestCase):
    """Standing rule (CFO 2026-07-27, in capitals): never open a letter with the
    bare first name. No honorific is known here, so the documented fallback is the
    full name with no title — never a guessed 'Mr', which would misgender people."""

    def test_never_the_bare_first_name(self):
        self.assertEqual(notify._salutation('Pako Lisley Kago'), 'Pako Kago')
        self.assertEqual(notify._salutation('Kutlo Keitumele'), 'Kutlo Keitumele')

    def test_middle_names_are_dropped(self):
        self.assertEqual(notify._salutation('Tumiso Innocent Motseko'), 'Tumiso Motseko')

    def test_no_invented_honorific(self):
        for name in ('Pako Lisley Kago', 'Unami Butale', 'Modiri Fofo Katai'):
            s = notify._salutation(name)
            for title in ('Mr ', 'Ms ', 'Mrs ', 'Dr '):
                self.assertNotIn(title, s)

    def test_degenerate_names(self):
        self.assertEqual(notify._salutation(''), 'Colleague')
        self.assertEqual(notify._salutation(None), 'Colleague')
        self.assertEqual(notify._salutation('Madonna'), 'Madonna')

    def test_the_real_letter_uses_it(self):
        cfo = User.objects.create_superuser('salcfo', 'salcfo@alphadirect.co.bw', 'pw')
        hr = User.objects.create_user('salhr', UNAMI_EMAIL, 'pw')
        su = User.objects.create_user('salsub', 'salsub@alphadirect.co.bw', 'pw')
        emp = Employee.objects.create(employee_number='SAL-SUB', full_name='Pako Lisley Kago',
                                      status='active', job_title='Financial Controller',
                                      email='salsub@alphadirect.co.bw', user=su)
        c = svc.raise_case(raised_by=cfo, subject_employee=emp, category='written',
                           incident_date=date(2026, 8, 8), allegation=ALLEGATION)
        svc.issue_inquiry(c.id, hr)
        c.refresh_from_db()
        mail.outbox = []
        notify.notify_inquiry(c)
        body = ''.join(str(a[0]) for a in mail.outbox[0].alternatives)
        self.assertIn('Pako Kago,', body)
        self.assertNotIn('>Pako,', body)


@override_settings(MEDIA_ROOT=tempfile.mkdtemp())
class InquiryInOmniOnlyTests(TestCase):
    """Raising the invitation in omni with NO letter sent (CFO 2026-08-12).

    The guard being protected: the case must never imply a letter went out when
    none did. inquiry_sent_to records how the employee was served, not a fake
    address.
    """

    def setUp(self):
        self.factory = APIRequestFactory()
        self.hr = User.objects.create_user('oohr', UNAMI_EMAIL, 'pw')
        self.mgr = User.objects.create_user('oomgr', 'oomgr@alphadirect.co.bw', 'pw')
        Employee.objects.create(employee_number='OO-MGR', full_name='Oo Mgr', status='active',
                                job_title='Operations Manager', user=self.mgr)
        self.sub_user = User.objects.create_user('oosub', 'oosub@alphadirect.co.bw', 'pw')
        self.subject = Employee.objects.create(employee_number='OO-SUB', full_name='Oo Sub',
                                               status='active', job_title='Clerk',
                                               email='oosub@alphadirect.co.bw', user=self.sub_user)

    def _case(self):
        return svc.raise_case(raised_by=self.mgr, subject_employee=self.subject,
                              category='written', incident_date=date(2026, 8, 5),
                              allegation=ALLEGATION, proposed_action='Written warning.')

    def test_no_email_is_sent_and_the_file_says_so(self):
        c = self._case()
        mail.outbox = []
        req = self.factory.post(f'/hris/api/disciplinary/{c.id}/issue-inquiry/',
                                {'send_email': False}, format='json')
        force_authenticate(req, user=self.hr)
        resp = issue_inquiry_view(req, case_id=str(c.id))
        self.assertEqual(resp.status_code, 200, getattr(resp, 'data', None))
        self.assertEqual(len(mail.outbox), 0)                    # nothing sent
        c.refresh_from_db()
        self.assertEqual(c.status, DisciplinaryCase.Status.PENDING_RESPONSE)
        # The file must not carry an address, which would imply a letter went out.
        self.assertNotIn('@', c.inquiry_sent_to)
        self.assertEqual(c.inquiry_sent_to, svc.SERVED_IN_OMNI_ONLY)

    def test_the_employee_still_sees_it_and_can_answer(self):
        c = self._case()
        svc.issue_inquiry(c.id, self.hr, send_email=False)
        req = self.factory.get('/hris/api/my-disciplinary/')
        force_authenticate(req, user=self.sub_user)
        row = my_disciplinary(req).data['cases'][0]
        self.assertTrue(row['awaiting_my_response'])
        self.assertTrue(row['can_respond'])
        out = svc.record_response(c.id, self.sub_user, response='Answered in omni.')
        self.assertTrue(out.has_response)
        self.assertTrue(out.natural_justice_satisfied)

    def test_emailing_stays_the_default(self):
        """Omitting the flag must EMAIL — silence should not quietly downgrade
        service to an in-app note the employee may never look at."""
        c = self._case()
        mail.outbox = []
        req = self.factory.post(f'/hris/api/disciplinary/{c.id}/issue-inquiry/', {}, format='json')
        force_authenticate(req, user=self.hr)
        self.assertEqual(issue_inquiry_view(req, case_id=str(c.id)).status_code, 200)
        self.assertEqual(len(mail.outbox), 1)
        c.refresh_from_db()
        self.assertEqual(c.inquiry_sent_to, 'oosub@alphadirect.co.bw')

    def test_omni_only_works_without_an_email_address(self):
        Employee.objects.filter(id=self.subject.id).update(email='')
        c = self._case()
        out = svc.issue_inquiry(c.id, self.hr, send_email=False)
        self.assertEqual(out.inquiry_sent_to, svc.SERVED_IN_OMNI_ONLY)
        # But emailing still refuses, with a hint at the omni-only route.
        c2 = self._case()
        with self.assertRaises(ValidationError) as ctx:
            svc.issue_inquiry(c2.id, self.hr, send_email=True)
        self.assertIn('omni only', '; '.join(ctx.exception.messages))
