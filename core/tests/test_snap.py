"""POST /api/v1/snap/classify/ (CFO 2026-09-03) — one photo in, "what is it and
where does it go" out. vision_complete is mocked: these tests pin the contract
(routing per kind, the id_document DPA refusal, defensive parsing, the upload
gates), not the model.

Run: python manage.py test core.tests.test_snap
"""
from unittest.mock import patch

from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from rest_framework.test import APIClient, APITestCase

URL = '/api/v1/snap/classify/'
JPEG = b'\xff\xd8\xff\xe0' + b'\x00' * 64          # a JPEG by its magic bytes
PNG = b'\x89PNG\r\n\x1a\n' + b'\x00' * 64
VISION = 'core.ai_assist.vision_complete'


def _img(data=JPEG, name='snap.jpg', ctype='image/jpeg'):
    return SimpleUploadedFile(name, data, content_type=ctype)


class SnapClassifyTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user('snapper', password='x')
        self.c = APIClient()
        self.c.force_authenticate(user=self.user)

    def _post(self, reply, **kw):
        with patch(VISION, return_value=reply) as vc:
            r = self.c.post(URL, {'image': _img(**kw)}, format='multipart')
        return r, vc

    def test_invoice_routes_to_raise_payment_with_fields(self):
        r, vc = self._post('{"kind":"invoice","confidence":0.93,"vendor":"Grand Traders",'
                           '"total":"1250.00","currency":"BWP","date":"2026-09-01",'
                           '"reference":"INV-77"}')
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(r.data['kind'], 'invoice')
        self.assertEqual(r.data['suggested']['route'], 'raise-payment')
        self.assertEqual(r.data['suggested']['fields']['vendor'], 'Grand Traders')
        self.assertEqual(r.data['suggested']['fields']['total'], '1250.00')
        self.assertEqual(r.data['suggested']['fields']['reference'], 'INV-77')
        self.assertIn('Grand Traders', r.data['hint'])
        self.assertIn('1,250.00 BWP', r.data['hint'])
        vc.assert_called_once()
        # The photo went to the vision engine as a data URL of the right type.
        self.assertTrue(vc.call_args.args[1].startswith('data:image/jpeg;base64,'))

    def test_receipt_routes_to_receipt_and_quote_to_raise_po(self):
        r, _ = self._post('{"kind":"receipt","confidence":0.8,"vendor":"Shell","total":"420.50","currency":"BWP"}')
        self.assertEqual(r.data['suggested']['route'], 'receipt')
        self.assertIn('receipt', r.data['hint'])
        r, _ = self._post('```json\n{"kind": "quote", "confidence": 0.7, "vendor": "Motovac"}\n```')
        self.assertEqual(r.data['kind'], 'quote')
        self.assertEqual(r.data['suggested']['route'], 'raise-po')

    def test_id_document_is_refused_with_no_fields(self):
        # Even if the model disobeys and reads the card, nothing of it leaves.
        r, _ = self._post('{"kind":"id_document","confidence":0.99,"vendor":"Republic of Botswana",'
                          '"reference":"123456789","date":"1990-01-01"}')
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.data['kind'], 'id_document')
        self.assertIsNone(r.data['suggested']['route'])
        self.assertNotIn('fields', r.data['suggested'])
        self.assertIn('identity document', r.data['hint'])
        self.assertNotIn('123456789', str(r.data))
        self.assertNotIn('Botswana', str(r.data))

    def test_garbage_model_output_is_kind_other(self):
        r, _ = self._post('Sorry, I cannot help with that.')
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.data['kind'], 'other')
        self.assertEqual(r.data['confidence'], 0.0)
        self.assertIsNone(r.data['suggested']['route'])
        r, _ = self._post('{"kind":"spaceship","confidence":"high"}')
        self.assertEqual(r.data['kind'], 'other')

    def test_oversized_and_wrong_type_are_400_and_never_reach_the_model(self):
        with patch(VISION) as vc:
            r = self.c.post(URL, {'image': _img(JPEG + b'\x00' * (6 * 1024 * 1024))}, format='multipart')
            self.assertEqual(r.status_code, 400)
            r = self.c.post(URL, {'image': _img(b'%PDF-1.4 not a photo', name='x.pdf', ctype='application/pdf')}, format='multipart')
            self.assertEqual(r.status_code, 400)
            r = self.c.post(URL, {}, format='multipart')
            self.assertEqual(r.status_code, 400)
        vc.assert_not_called()

    def test_png_is_accepted(self):
        r, vc = self._post('{"kind":"other","confidence":0.2}', data=PNG, name='p.png', ctype='image/png')
        self.assertEqual(r.status_code, 200)
        self.assertTrue(vc.call_args.args[1].startswith('data:image/png;base64,'))

    def test_anonymous_is_401(self):
        with patch(VISION) as vc:
            r = APIClient().post(URL, {'image': _img()}, format='multipart')
        self.assertEqual(r.status_code, 401)
        vc.assert_not_called()

    def test_vision_outage_is_503_not_a_crash(self):
        from core.ai_assist import VisionUnavailable
        with patch(VISION, side_effect=VisionUnavailable('all engines down')):
            r = self.c.post(URL, {'image': _img()}, format='multipart')
        self.assertEqual(r.status_code, 503)
        self.assertEqual(r.data['kind'], 'other')


class SnapIdentityScrubTests(APITestCase):
    """DPA guard #2: identity-looking content is dropped whatever the model called it."""

    def setUp(self):
        self.user = User.objects.create_user('scrubber', password='x')
        self.c = APIClient()
        self.c.force_authenticate(user=self.user)

    def _post(self, reply, **kw):
        with patch(VISION, return_value=reply) as vc:
            r = self.c.post(URL, {'image': _img(**kw)}, format='multipart')
        return r, vc

    def test_misclassified_id_card_returns_no_fields(self):
        # Model says "other" but the fields carry an Omang-shaped number and a name.
        r, _ = self._post('{"kind":"other","confidence":0.4,"vendor":"Thabo Example",'
                          '"reference":"123456789","date":"1990-01-01"}')
        self.assertEqual(r.status_code, 200, r.content[:300])
        body = r.json()
        self.assertEqual(body['kind'], 'id_document')
        self.assertEqual(body['suggested']['route'], None)
        self.assertNotIn('123456789', r.content.decode())
        self.assertNotIn('Thabo', r.content.decode())

    def test_single_passport_like_reference_is_dropped_but_invoice_kept(self):
        r, _ = self._post('{"kind":"invoice","confidence":0.9,"vendor":"Grand Traders",'
                          '"total":"1250.00","currency":"BWP","reference":"BW1234567"}')
        self.assertEqual(r.status_code, 200, r.content[:300])
        body = r.json()
        self.assertEqual(body['kind'], 'invoice')
        self.assertNotIn('BW1234567', r.content.decode())
        self.assertEqual(body['suggested']['fields'].get('vendor'), 'Grand Traders')

    def test_oversized_content_length_is_refused_before_parsing(self):
        # The declared body size is checked BEFORE the multipart parser runs, so an
        # oversized upload never reaches the model (vision is NOT mocked here on purpose).
        from rest_framework.test import APIRequestFactory, force_authenticate
        from core.snap_views import SnapClassifyView
        req = APIRequestFactory().post(URL, {'image': _img()}, format='multipart')
        req.META['CONTENT_LENGTH'] = str(50 * 1024 * 1024)
        force_authenticate(req, user=self.user)
        r = SnapClassifyView.as_view()(req)
        self.assertEqual(r.status_code, 400, getattr(r, 'data', None))
        self.assertIn('too big', str(r.data.get('detail', '')))
