"""Tests for the SOP Bank (CFO directive 2026-06-10, BOBS audit gap)."""
import io
import json
import os
import re
import shutil
import tempfile
import zipfile

from django.contrib.auth.models import Group, User
from django.core.files.base import ContentFile
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.test import override_settings
from rest_framework.test import APIClient, APITestCase

from iso_compliance.models import SOPAcknowledgement, SOPDocument

_MEDIA = tempfile.mkdtemp(prefix='sopbank-media-')


def _docx_bytes(text: str) -> bytes:
    """Minimal valid .docx with one paragraph of `text`."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w') as z:
        z.writestr('[Content_Types].xml',
                   '<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
                   '<Default Extension="xml" ContentType="application/xml"/>'
                   '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/></Types>')
        z.writestr('word/document.xml',
                   '<?xml version="1.0"?><w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
                   f'<w:body><w:p><w:r><w:t>{text}</w:t></w:r></w:p></w:body></w:document>')
    return buf.getvalue()


@override_settings(MEDIA_ROOT=_MEDIA)
class SopBankApiTest(APITestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user('staff', email='s@alphadirect.co.bw', password='x')
        cls.sop_fin = SOPDocument.objects.create(
            department='Finance', title='Payment Run SOP', sop_number='40',
            owner='Kago Tshutlhedi', file_type='docx', size_bytes=10,
            content_text='Process supplier payments through the omni payables module.',
            source_path='Finance/Payment Run SOP.docx')
        cls.sop_fin.file.save('payment.docx', ContentFile(_docx_bytes('payments')), save=True)
        cls.sop_obs = SOPDocument.objects.create(
            department='Obsolete', title='Old Fax SOP', status=SOPDocument.STATUS_OBSOLETE,
            file_type='docx', size_bytes=5, source_path='Obsolete/Old Fax SOP.docx')
        cls.sop_obs.file.save('fax.docx', ContentFile(_docx_bytes('fax')), save=True)

    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(_MEDIA, ignore_errors=True)

    def _client(self, user=None):
        c = APIClient()
        c.force_authenticate(user=user or self.user)
        return c

    def test_anonymous_is_refused(self):
        r = APIClient().get('/api/v1/iso/sops/')
        self.assertIn(r.status_code, (401, 403))

    def test_list_defaults_to_active_only(self):
        r = self._client().get('/api/v1/iso/sops/')
        self.assertEqual(r.status_code, 200)
        titles = [x['title'] for x in r.json()['rows']]
        self.assertIn('Payment Run SOP', titles)
        self.assertNotIn('Old Fax SOP', titles)

    def test_content_search(self):
        r = self._client().get('/api/v1/iso/sops/', {'q': 'payables module'})
        self.assertEqual(r.json()['count'], 1)

    def test_acknowledge_is_idempotent_and_counts(self):
        c = self._client()
        r1 = c.post(f'/api/v1/iso/sops/{self.sop_fin.id}/acknowledge/')
        self.assertEqual(r1.status_code, 200)
        self.assertFalse(r1.json()['already'])
        r2 = c.post(f'/api/v1/iso/sops/{self.sop_fin.id}/acknowledge/')
        self.assertTrue(r2.json()['already'])
        self.assertEqual(SOPAcknowledgement.objects.filter(sop=self.sop_fin).count(), 1)
        r3 = c.get('/api/v1/iso/sops/')
        row = [x for x in r3.json()['rows'] if x['id'] == str(self.sop_fin.id)][0]
        self.assertTrue(row['my_acknowledged'])
        self.assertEqual(row['ack_count'], 1)

    def test_download_streams_file(self):
        r = self._client().get(f'/api/v1/iso/sops/{self.sop_fin.id}/download/')
        self.assertEqual(r.status_code, 200)
        body = b''.join(r.streaming_content)
        self.assertTrue(body.startswith(b'PK'))   # docx = zip container

    def test_coverage_shape(self):
        self._client().post(f'/api/v1/iso/sops/{self.sop_fin.id}/acknowledge/')
        r = self._client().get('/api/v1/iso/sops/coverage/')
        self.assertEqual(r.status_code, 200)
        body = r.json()
        fin = [d for d in body['departments'] if d['department'] == 'Finance'][0]
        self.assertEqual(fin['sops'], 1)
        self.assertEqual(fin['distinct_readers'], 1)


@override_settings(MEDIA_ROOT=_MEDIA)
class SopBankUploadTest(APITestCase):
    """Self-service upload + DeepSeek suggest → confirm (CFO 2026-07-27).

    DeepSeek is unavailable in tests, so classify_document returns its
    deterministic fallback (department='', doc_type='sop') — the draft is
    created and the uploader confirms explicitly, which is exactly the path
    we want to guarantee works with no AI.
    """
    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user('reader', password='x')
        cls.uploader = User.objects.create_user('unami', password='x')
        cls.uploader.groups.add(Group.objects.create(name='sop_uploader'))

    def _c(self, user):
        c = APIClient(); c.force_authenticate(user=user); return c

    def test_can_upload_flag_reflects_group(self):
        self.assertFalse(self._c(self.staff).get('/api/v1/iso/sops/').json()['can_upload'])
        self.assertTrue(self._c(self.uploader).get('/api/v1/iso/sops/').json()['can_upload'])

    def test_non_uploader_is_forbidden(self):
        f = SimpleUploadedFile('leave.docx', _docx_bytes('Annual leave policy.'))
        r = self._c(self.staff).post('/api/v1/iso/sops/upload/', {'file': f}, format='multipart')
        self.assertEqual(r.status_code, 403)
        self.assertEqual(SOPDocument.objects.count(), 0)

    def test_bad_extension_rejected(self):
        f = SimpleUploadedFile('notes.txt', b'hello')
        r = self._c(self.uploader).post('/api/v1/iso/sops/upload/', {'file': f}, format='multipart')
        self.assertEqual(r.status_code, 400)

    def test_upload_creates_draft_then_confirm_publishes_policy(self):
        c = self._c(self.uploader)
        f = SimpleUploadedFile('leave.docx', _docx_bytes('This leave policy governs annual leave.'))
        up = c.post('/api/v1/iso/sops/upload/', {'file': f}, format='multipart')
        self.assertEqual(up.status_code, 201)
        did = up.json()['id']
        draft = SOPDocument.objects.get(pk=did)
        self.assertEqual(draft.status, SOPDocument.STATUS_DRAFT)
        self.assertEqual(draft.uploaded_by, 'unami')
        # Draft must NOT appear in the default (active) list for anyone.
        titles = [x['id'] for x in self._c(self.staff).get('/api/v1/iso/sops/').json()['rows']]
        self.assertNotIn(did, titles)
        # Confirm as a Policy in Human Resources.
        cf = c.post(f'/api/v1/iso/sops/{did}/confirm/',
                    {'department': 'Human Resources', 'doc_type': 'policy', 'title': 'Leave Policy'},
                    format='json')
        self.assertEqual(cf.status_code, 200)
        draft.refresh_from_db()
        self.assertEqual(draft.status, SOPDocument.STATUS_ACTIVE)
        self.assertEqual(draft.doc_type, 'policy')
        self.assertEqual(draft.department, 'Human Resources')
        # Now visible + filterable by type for ordinary staff.
        pol = self._c(self.staff).get('/api/v1/iso/sops/', {'doc_type': 'policy'}).json()
        self.assertIn(did, [x['id'] for x in pol['rows']])
        self.assertEqual(pol['doc_type_counts']['policy'], 1)

    def test_confirm_rejects_bad_doc_type(self):
        c = self._c(self.uploader)
        f = SimpleUploadedFile('x.docx', _docx_bytes('x'))
        did = c.post('/api/v1/iso/sops/upload/', {'file': f}, format='multipart').json()['id']
        r = c.post(f'/api/v1/iso/sops/{did}/confirm/',
                   {'department': 'Finance', 'doc_type': 'nonsense', 'title': 'X'}, format='json')
        self.assertEqual(r.status_code, 400)

    def test_other_uploader_cannot_touch_my_draft(self):
        other = User.objects.create_user('dikgopoleng', password='x')
        other.groups.add(Group.objects.get(name='sop_uploader'))
        f = SimpleUploadedFile('x.docx', _docx_bytes('x'))
        did = self._c(self.uploader).post('/api/v1/iso/sops/upload/',
                                          {'file': f}, format='multipart').json()['id']
        oc = self._c(other)
        r1 = oc.post(f'/api/v1/iso/sops/{did}/confirm/',
                     {'department': 'Finance', 'doc_type': 'sop', 'title': 'X'}, format='json')
        self.assertEqual(r1.status_code, 403)
        r2 = oc.post(f'/api/v1/iso/sops/{did}/discard/')
        self.assertEqual(r2.status_code, 403)
        self.assertTrue(SOPDocument.objects.filter(pk=did).exists())

    def test_draft_is_hidden_from_ordinary_staff_list(self):
        f = SimpleUploadedFile('x.docx', _docx_bytes('secret draft'))
        did = self._c(self.uploader).post('/api/v1/iso/sops/upload/',
                                          {'file': f}, format='multipart').json()['id']
        # Even explicitly asking for drafts, a non-uploader sees none.
        for status in ('draft', 'all'):
            ids = [x['id'] for x in self._c(self.staff)
                   .get('/api/v1/iso/sops/', {'status': status}).json()['rows']]
            self.assertNotIn(did, ids, f'draft leaked via status={status}')
        # The uploader CAN see their own draft.
        ids = [x['id'] for x in self._c(self.uploader)
               .get('/api/v1/iso/sops/', {'status': 'draft'}).json()['rows']]
        self.assertIn(did, ids)

    def test_draft_file_not_downloadable_by_ordinary_staff(self):
        f = SimpleUploadedFile('x.docx', _docx_bytes('secret draft body'))
        did = self._c(self.uploader).post('/api/v1/iso/sops/upload/',
                                          {'file': f}, format='multipart').json()['id']
        r = self._c(self.staff).get(f'/api/v1/iso/sops/{did}/download/')
        self.assertEqual(r.status_code, 404)
        # Uploader (owner) can download their own draft.
        r2 = self._c(self.uploader).get(f'/api/v1/iso/sops/{did}/download/')
        self.assertEqual(r2.status_code, 200)

    def test_discard_removes_draft(self):
        c = self._c(self.uploader)
        f = SimpleUploadedFile('x.docx', _docx_bytes('x'))
        did = c.post('/api/v1/iso/sops/upload/', {'file': f}, format='multipart').json()['id']
        r = c.post(f'/api/v1/iso/sops/{did}/discard/')
        self.assertEqual(r.status_code, 200)
        self.assertFalse(SOPDocument.objects.filter(pk=did).exists())

    def test_grant_command_is_idempotent(self):
        # ubutale already exists as self.uploader.username == 'unami'; create
        # one of the real usernames to prove the command adds membership.
        User.objects.create_user('pkago', password='x')
        call_command('grant_sop_uploaders')
        call_command('grant_sop_uploaders')   # second run must not error/duplicate
        g = Group.objects.get(name='sop_uploader')
        self.assertTrue(g.user_set.filter(username='pkago').exists())


@override_settings(MEDIA_ROOT=_MEDIA)
class SopBankIngestTest(APITestCase):
    def test_ingest_walks_tree_and_applies_manifest(self):
        root = tempfile.mkdtemp(prefix='sopbank-src-')
        try:
            os.makedirs(os.path.join(root, 'Finance'))
            os.makedirs(os.path.join(root, 'Obsolete'))
            with open(os.path.join(root, 'Finance', 'Refunds SOP.docx'), 'wb') as f:
                f.write(_docx_bytes('Refund the client through omni journal entries.'))
            with open(os.path.join(root, 'Obsolete', 'Telex SOP.docx'), 'wb') as f:
                f.write(_docx_bytes('Send a telex.'))
            with open(os.path.join(root, 'Finance', 'notes.txt'), 'w') as f:
                f.write('skip me')
            manifest = {
                'Finance/Refunds SOP.docx': {
                    'sop_number': '57', 'owner': 'Pako Kago',
                    'revision_date': '2025-09-10',
                    'omni_fit': 'gap',
                    'omni_fit_notes': 'References manual refund register replaced by omni.',
                },
            }
            mpath = os.path.join(root, 'manifest.json')
            with open(mpath, 'w') as f:
                json.dump(manifest, f)

            call_command('ingest_sop_bank', root=root, manifest=mpath)

            self.assertEqual(SOPDocument.objects.count(), 2)
            ref = SOPDocument.objects.get(source_path='Finance/Refunds SOP.docx')
            self.assertEqual(ref.owner, 'Pako Kago')
            self.assertEqual(ref.sop_number, '57')
            self.assertEqual(ref.omni_fit, 'gap')
            self.assertIn('omni journal entries', ref.content_text)
            self.assertEqual(str(ref.revision_date), '2025-09-10')
            tlx = SOPDocument.objects.get(source_path='Obsolete/Telex SOP.docx')
            self.assertEqual(tlx.status, SOPDocument.STATUS_OBSOLETE)

            # Idempotent: re-run does not duplicate.
            call_command('ingest_sop_bank', root=root, manifest=mpath)
            self.assertEqual(SOPDocument.objects.count(), 2)
        finally:
            shutil.rmtree(root, ignore_errors=True)
