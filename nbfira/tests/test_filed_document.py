"""Attaching the return actually FILED with NBFIRA (CFO directive 2026-08-17).

Omni GENERATES its schedules from the GL; these endpoints hold the real filed
workbook against the same period so the two can be reconciled.

Covers: happy-path upload, the file-type / size / empty guards, the statement
whitelist, duplicate detection by hash, upload being allowed at LOCKED and
SUBMITTED status (you only have the filed copy after filing), download, the
delete permission rule, and the audit trail.
"""
import datetime as dt
import hashlib

from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings
from rest_framework.test import APIClient, APITestCase

from core.models import Company, UserProfile
from nbfira.models import NBFIRAAuditLog, NBFIRAFiledDocument, NBFIRAReturn

URL = '/api/v1/nbfira/returns'

# A tiny valid-enough payload. These tests never parse the file — the endpoint
# stores and hashes it — so the bytes only need to be stable, not real Excel.
XLSX_BYTES = b'PK\x03\x04fake-xlsx-body-for-tests'


def xlsx(name='ADIC_2026Q3_Return.xlsx', body=XLSX_BYTES):
    return SimpleUploadedFile(
        name, body,
        content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')


@override_settings(MEDIA_ROOT='/tmp/nbfira-test-media')
class FiledDocumentTest(APITestCase):

    @classmethod
    def setUpTestData(cls):
        cls.company = Company.objects.create(code='NBFT', name='NBFIRA Test Co.')
        # CFO 2026-08-17: these endpoints are restricted to managers + FC + FM,
        # so both ordinary actors carry one of those titles. A Senior Accountant
        # is deliberately NOT enough any more.
        cls.user = User.objects.create_user('filer', 'filer@test.example', 'x')
        UserProfile.objects.update_or_create(
            user=cls.user,
            defaults={'title': UserProfile.Title.FINANCIAL_CONTROLLER, 'is_active': True})
        cls.other = User.objects.create_user('other', 'other@test.example', 'x')
        UserProfile.objects.update_or_create(
            user=cls.other,
            defaults={'title': UserProfile.Title.FINANCE_MANAGER, 'is_active': True})
        cls.admin = User.objects.create_superuser('root', 'root@test.example', 'x')
        # An ordinary operational employee — authenticated, but not management.
        cls.operational = User.objects.create_user('ops', 'ops@test.example', 'x')
        UserProfile.objects.update_or_create(
            user=cls.operational,
            defaults={'title': UserProfile.Title.JUNIOR_CLAIMS_ASSOCIATE, 'is_active': True})

    def setUp(self):
        self.ret = NBFIRAReturn.objects.create(
            type=NBFIRAReturn.Type.QUARTERLY, period_label='2026Q3',
            period_start=dt.date(2026, 1, 1), period_end=dt.date(2026, 3, 31),
            company=self.company, initiated_by=self.user,
        )

    def _client(self, user=None):
        c = APIClient()
        c.force_authenticate(user or self.user)
        return c

    def _upload(self, user=None, ret=None, **extra):
        f = extra.pop('file', None) or xlsx()
        data = {'file': f, **extra}
        target = ret or self.ret
        return self._client(user).post(
            f'{URL}/{target.id}/upload-filed/', data, format='multipart')

    # ── happy path ──────────────────────────────────────────────────────────
    def test_upload_attaches_the_file_to_the_period(self):
        r = self._upload(statement='A.1', notes='as filed 15 Aug')
        self.assertEqual(r.status_code, 201, r.content)
        b = r.json()
        self.assertEqual(b['original_name'], 'ADIC_2026Q3_Return.xlsx')
        self.assertEqual(b['statement'], 'A.1')
        self.assertEqual(b['notes'], 'as filed 15 Aug')
        self.assertEqual(b['uploaded_by'], 'filer')
        self.assertEqual(b['size_bytes'], len(XLSX_BYTES))
        # The hash must be the real SHA-256 of the bytes, not a placeholder.
        self.assertEqual(b['file_hash_sha256'],
                         hashlib.sha256(XLSX_BYTES).hexdigest())
        # Stored against THIS return, and nothing has been parsed yet.
        doc = NBFIRAFiledDocument.objects.get(pk=b['id'])
        self.assertEqual(doc.return_obj_id, self.ret.id)
        self.assertFalse(doc.parsed)

    def test_blank_statement_means_whole_workbook(self):
        b = self._upload().json()
        self.assertEqual(b['statement'], '')

    def test_it_shows_up_on_the_return_detail_and_the_filed_list(self):
        self._upload(statement='A')
        detail = self._client().get(f'{URL}/{self.ret.id}/').json()
        self.assertEqual(len(detail['filed_documents']), 1)
        listing = self._client().get(f'{URL}/{self.ret.id}/filed/').json()
        self.assertEqual(len(listing['results']), 1)
        self.assertEqual(listing['results'][0]['statement'], 'A')

    def test_upload_is_recorded_in_the_audit_log(self):
        self._upload(statement='B')
        entry = (NBFIRAAuditLog.objects
                 .filter(return_obj=self.ret,
                         action=NBFIRAAuditLog.Action.UPLOAD).first())
        self.assertIsNotNone(entry, 'attaching a filed return must be audited')
        self.assertIn('ADIC_2026Q3_Return.xlsx', entry.comment)
        self.assertEqual(entry.after_json.get('statement'), 'B')

    # ── the guards ──────────────────────────────────────────────────────────
    def test_no_file_is_a_plain_400(self):
        r = self._client().post(f'{URL}/{self.ret.id}/upload-filed/', {},
                                format='multipart')
        self.assertEqual(r.status_code, 400)
        self.assertIn('choose a file', r.json()['detail'].lower())

    def test_wrong_file_type_is_refused_with_the_allowed_list(self):
        r = self._upload(file=SimpleUploadedFile('return.exe', b'MZbinary',
                                                 content_type='application/octet-stream'))
        self.assertEqual(r.status_code, 400)
        self.assertIn('.xlsx', r.json()['detail'])

    def test_empty_file_is_refused(self):
        r = self._upload(file=SimpleUploadedFile('empty.xlsx', b'',
                                                 content_type='application/vnd.ms-excel'))
        self.assertEqual(r.status_code, 400)
        self.assertIn('empty', r.json()['detail'].lower())

    def test_oversize_file_is_refused_and_says_the_limit(self):
        big = SimpleUploadedFile('huge.xlsx', b'x' * (26 * 1024 * 1024),
                                 content_type='application/vnd.ms-excel')
        r = self._upload(file=big)
        self.assertEqual(r.status_code, 400)
        self.assertIn('25 MB', r.json()['detail'])

    def test_unknown_statement_is_refused(self):
        r = self._upload(statement='Z9')
        self.assertEqual(r.status_code, 400)
        self.assertIn('A.1', r.json()['detail'])

    def test_old_xls_and_pdf_are_accepted_because_this_is_evidence_not_parsing(self):
        """A scanned PDF or a pre-2007 .xls is still the return that was filed.
        Refusing it would lose the evidence; parsing constraints come later."""
        for name, ct in (('filed.pdf', 'application/pdf'),
                         ('filed.xls', 'application/vnd.ms-excel')):
            with self.subTest(name=name):
                r = self._upload(file=SimpleUploadedFile(name, b'body-' + name.encode(),
                                                         content_type=ct))
                self.assertEqual(r.status_code, 201, r.content)

    def test_the_same_file_twice_is_refused_as_a_duplicate(self):
        self.assertEqual(self._upload().status_code, 201)
        again = self._upload()
        self.assertEqual(again.status_code, 409)
        self.assertIn('already attached', again.json()['detail'])
        self.assertEqual(NBFIRAFiledDocument.objects.count(), 1)

    def test_a_different_file_with_the_same_name_is_still_accepted(self):
        """Dedup is on CONTENT, not the filename — a corrected re-file usually
        keeps the same name."""
        self.assertEqual(self._upload().status_code, 201)
        r = self._upload(file=xlsx(body=b'PK\x03\x04corrected-version'))
        self.assertEqual(r.status_code, 201, r.content)
        self.assertEqual(NBFIRAFiledDocument.objects.count(), 2)

    # ── status must never block attaching ───────────────────────────────────
    def test_upload_allowed_even_when_locked_or_submitted(self):
        """You normally only HAVE the filed copy after filing, so a locked or
        submitted return must still accept it."""
        for status_val in (NBFIRAReturn.Status.LOCKED,
                           NBFIRAReturn.Status.SUBMITTED):
            with self.subTest(status=status_val):
                ret = NBFIRAReturn.objects.create(
                    type=NBFIRAReturn.Type.QUARTERLY,
                    period_label=f'2026Q3-{status_val}',
                    period_start=dt.date(2026, 1, 1), period_end=dt.date(2026, 3, 31),
                    company=self.company, status=status_val,
                )
                r = self._upload(ret=ret,
                                 file=xlsx(body=b'PK\x03\x04' + status_val.encode()))
                self.assertEqual(r.status_code, 201, r.content)

    # ── download + delete ───────────────────────────────────────────────────
    def test_download_returns_the_bytes_under_the_original_name(self):
        doc_id = self._upload().json()['id']
        r = self._client().get(f'{URL}/{self.ret.id}/filed/{doc_id}/download/')
        self.assertEqual(r.status_code, 200)
        self.assertIn('ADIC_2026Q3_Return.xlsx', r['Content-Disposition'])
        self.assertEqual(b''.join(r.streaming_content), XLSX_BYTES)

    def test_only_the_uploader_or_an_admin_can_remove_it(self):
        doc_id = self._upload().json()['id']
        path = f'{URL}/{self.ret.id}/filed/{doc_id}/'

        blocked = self._client(self.other).delete(path)
        self.assertEqual(blocked.status_code, 403)
        self.assertEqual(NBFIRAFiledDocument.objects.count(), 1)

        allowed = self._client(self.user).delete(path)
        self.assertEqual(allowed.status_code, 204)
        self.assertEqual(NBFIRAFiledDocument.objects.count(), 0)

    def test_an_admin_can_remove_someone_elses_attachment(self):
        doc_id = self._upload().json()['id']
        r = self._client(self.admin).delete(f'{URL}/{self.ret.id}/filed/{doc_id}/')
        self.assertEqual(r.status_code, 204)

    def test_removal_is_audited_before_the_row_goes(self):
        doc_id = self._upload().json()['id']
        self._client(self.user).delete(f'{URL}/{self.ret.id}/filed/{doc_id}/')
        entry = (NBFIRAAuditLog.objects
                 .filter(return_obj=self.ret,
                         action=NBFIRAAuditLog.Action.UNATTACH).first())
        self.assertIsNotNone(entry)
        self.assertIn('ADIC_2026Q3_Return.xlsx', entry.comment)

    def test_a_document_on_another_return_is_not_reachable_through_this_one(self):
        """The doc_id is scoped by the return in the URL — no cross-period read."""
        mine = self._upload().json()['id']
        other_ret = NBFIRAReturn.objects.create(
            type=NBFIRAReturn.Type.QUARTERLY, period_label='2026Q4',
            period_start=dt.date(2026, 4, 1), period_end=dt.date(2026, 6, 30),
            company=self.company,
        )
        r = self._client().get(f'{URL}/{other_ret.id}/filed/{mine}/download/')
        self.assertEqual(r.status_code, 404)

    def test_signed_out_cannot_upload(self):
        r = APIClient().post(f'{URL}/{self.ret.id}/upload-filed/',
                             {'file': xlsx()}, format='multipart')
        self.assertIn(r.status_code, (401, 403))

    # ── the audience gate (Fable 5 + Gemini + OpenAI, H5, 2026-08-17) ───────
    def test_an_ordinary_staff_login_cannot_read_or_write_filed_returns(self):
        """Being signed in is NOT enough. These are the company's actual filed
        NBFIRA returns; before the gate any staff login could download them."""
        doc_id = self._upload().json()['id']
        ops = self._client(self.operational)

        for label, resp in (
            ('upload',   self._upload(user=self.operational,
                                      file=xlsx(body=b'PKops-attempt'))),
            ('list',     ops.get(f'{URL}/{self.ret.id}/filed/')),
            ('detail',   ops.get(f'{URL}/{self.ret.id}/')),
            ('download', ops.get(f'{URL}/{self.ret.id}/filed/{doc_id}/download/')),
            ('delete',   ops.delete(f'{URL}/{self.ret.id}/filed/{doc_id}/')),
        ):
            with self.subTest(action=label):
                self.assertEqual(resp.status_code, 403,
                                 f'{label} must be refused for non-finance staff')

        # and nothing of theirs was stored
        self.assertEqual(NBFIRAFiledDocument.objects.count(), 1)

    def test_capital_factors_cannot_be_edited_by_ordinary_staff(self):
        """These rows drive the Prescribed Capital Target and this viewset WRITES
        them — an ungated upsert let any staff login move the capital target."""
        ops = self._client(self.operational)
        self.assertEqual(ops.get('/api/v1/nbfira/capital-factors/').status_code, 403)
        self.assertEqual(
            ops.post('/api/v1/nbfira/capital-factors/',
                     {'kind': 'irc', 'code': 'X', 'label': 'X', 'value': '0.5'},
                     format='json').status_code, 403)

    def test_exactly_who_the_CFO_allowed_and_nobody_else(self):
        """CFO 2026-08-17: "managers and fc and fm only". Pins BOTH sides of the
        line, so widening it later has to be deliberate.

        Note this is TIGHTER than CanViewFinancials — an accountant, bookkeeper,
        finance analyst, auditor or read-only executive is refused, even though
        they can see other financial pages.
        """
        T = UserProfile.Title
        allowed = [T.CEO, T.COO, T.CFO, T.CLAIMS_MANAGER, T.OPERATIONS_MANAGER,
                   T.HR_MANAGER, T.FINANCIAL_CONTROLLER, T.FINANCE_MANAGER]
        refused = [T.ACCOUNTANT, T.SENIOR_ACCOUNTANT, T.BOOKKEEPER,
                   T.FINANCE_ANALYST, T.AUDITOR, T.EXECUTIVE, T.OPERATIONS,
                   T.CLAIMS_TEAM_LEADER, T.SENIOR_CLAIMS_ASSOCIATE,
                   T.JUNIOR_CLAIMS_ASSOCIATE, T.CLAIMS_INTERN]

        for title in allowed:
            with self.subTest(allowed=title):
                u = User.objects.create_user(f'ok-{title}', f'{title}@t.example', 'x')
                UserProfile.objects.update_or_create(
                    user=u, defaults={'title': title, 'is_active': True})
                self.assertEqual(
                    self._client(u).get(f'{URL}/{self.ret.id}/').status_code, 200,
                    f'{title} must be allowed')

        for title in refused:
            with self.subTest(refused=title):
                u = User.objects.create_user(f'no-{title}', f'no{title}@t.example', 'x')
                UserProfile.objects.update_or_create(
                    user=u, defaults={'title': title, 'is_active': True})
                self.assertEqual(
                    self._client(u).get(f'{URL}/{self.ret.id}/').status_code, 403,
                    f'{title} must be refused')

    def test_an_inactive_profile_is_refused_even_with_the_right_title(self):
        u = User.objects.create_user('exfc', 'exfc@t.example', 'x')
        UserProfile.objects.update_or_create(
            user=u, defaults={'title': UserProfile.Title.FINANCIAL_CONTROLLER,
                              'is_active': False})
        self.assertEqual(
            self._client(u).get(f'{URL}/{self.ret.id}/').status_code, 403)

    def test_a_user_with_no_profile_at_all_is_refused(self):
        u = User.objects.create_user('noprof', 'noprof@t.example', 'x')
        self.assertEqual(
            self._client(u).get(f'{URL}/{self.ret.id}/').status_code, 403)
