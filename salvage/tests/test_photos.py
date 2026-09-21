"""Salvage photo attachment — Bharath Balasubramanian, 16-Sep-2026.

"We must be able to attach photos to each salvage on our salvage management
in OMNI."

The SalvageImage model, the detail-page gallery and primary_image all existed
already; the write path did not, and prod proved it — 52 salvage items and 0
photos, because nothing could put one there. These tests cover that write path
and only that.

Each test covers something that would actually hurt if it broke:
  * a photo can be attached at all, and several in one go;
  * the FIRST photo attached stays the primary one, so the inventory
    thumbnail does not shuffle every time somebody adds another angle;
  * a non-image, an oversized file and the 21st photo are refused BEFORE
    anything is written, so a bad third file cannot leave the first two
    half-attached;
  * deleting closes the ordering gap, so "primary" keeps meaning ordering 0
    after any number of deletes;
  * another entity's item is invisible, and a non-salvage user is refused —
    the photo route must not be a way round the company scope the rest of the
    module enforces.
"""
from __future__ import annotations

import io
import tempfile

from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings
from rest_framework.test import APITestCase

from core.models import Company
from salvage.models import SalvageImage, SalvageItem


def png_bytes(size_px: int = 4) -> bytes:
    """A real, decodable PNG. ImageField runs Pillow over the upload, so a
    handful of fake bytes with an image/png content-type is rejected by the
    field itself and the test would pass for the wrong reason."""
    from PIL import Image
    buf = io.BytesIO()
    Image.new('RGB', (size_px, size_px), (13, 27, 42)).save(buf, format='PNG')
    return buf.getvalue()


def photo(name: str = 'front.png', content: bytes | None = None,
          content_type: str = 'image/png') -> SimpleUploadedFile:
    return SimpleUploadedFile(name, content if content is not None else png_bytes(),
                              content_type=content_type)


@override_settings(MEDIA_ROOT=tempfile.mkdtemp())
class SalvagePhotoTests(APITestCase):
    """Photos on a salvage item. MEDIA_ROOT is redirected to a temp dir so a
    test run never writes into the repo's media/ folder."""

    @classmethod
    def setUpTestData(cls):
        cls.company = Company.objects.create(code='ADIC', name='Alpha Direct Insurance')
        cls.other   = Company.objects.create(code='RSA', name='Alpha Direct RSA')
        # A superuser clears both IsSalvageUser and the company scope without
        # needing an Employee row; the scope itself is proven separately below
        # with a user who is NOT unrestricted.
        cls.user = User.objects.create_superuser(
            'yardmanager', 'yard@alphadirect.co.bw', 'x')

    def setUp(self):
        self.client.force_authenticate(user=self.user)
        self.item = SalvageItem.objects.create(
            item_code='SLV-TEST-0001', part_name='2018 Toyota Hilux 2.4D',
            company=self.company,
        )

    def url(self, item=None):
        return f'/api/v1/salvage-items/{(item or self.item).pk}/images/'

    # ── attaching ─────────────────────────────────────────────────────────
    def test_one_photo_attaches_and_comes_back(self):
        r = self.client.post(self.url(), {'images': photo()}, format='multipart')
        self.assertEqual(r.status_code, 201, r.data)
        self.assertEqual(r.data['count'], 1)
        self.assertEqual(SalvageImage.objects.filter(item=self.item).count(), 1)

    def test_three_photos_in_one_go_keep_their_order(self):
        r = self.client.post(
            self.url(),
            {'images': [photo('a.png'), photo('b.png'), photo('c.png')]},
            format='multipart')
        self.assertEqual(r.status_code, 201, r.data)
        self.assertEqual(r.data['count'], 3)
        self.assertEqual([i['ordering'] for i in r.data['images']], [0, 1, 2])

    def test_a_later_batch_appends_and_does_not_steal_primary(self):
        """The first photo ever uploaded stays the inventory thumbnail."""
        first = self.client.post(self.url(), {'images': photo('first.png')},
                                 format='multipart').data['images'][0]
        self.client.post(self.url(), {'images': photo('second.png')}, format='multipart')
        imgs = list(SalvageImage.objects.filter(item=self.item).order_by('ordering'))
        self.assertEqual(str(imgs[0].id), first['id'])
        self.assertEqual([i.ordering for i in imgs], [0, 1])

    def test_caption_is_applied_to_the_batch(self):
        r = self.client.post(self.url(),
                             {'images': [photo('a.png'), photo('b.png')],
                              'caption': 'Front-end collision'},
                             format='multipart')
        self.assertEqual(
            {i['caption'] for i in r.data['images']}, {'Front-end collision'})

    # ── guards ────────────────────────────────────────────────────────────
    def test_no_file_is_a_clear_400(self):
        r = self.client.post(self.url(), {}, format='multipart')
        self.assertEqual(r.status_code, 400)
        self.assertIn('at least one photo', str(r.data).lower())

    def test_a_pdf_is_refused(self):
        r = self.client.post(
            self.url(),
            {'images': SimpleUploadedFile('slip.pdf', b'%PDF-1.4 not a photo',
                                          content_type='application/pdf')},
            format='multipart')
        self.assertEqual(r.status_code, 400)
        self.assertEqual(SalvageImage.objects.filter(item=self.item).count(), 0)

    def test_an_iphone_heic_is_refused_at_the_door(self):
        """Chrome, Edge and Firefox cannot render HEIC, so accepting one would
        mean a clean 201 and then a broken tile in the gallery, the inventory
        list AND the public portal, with nothing anywhere explaining why.
        Refused at the door, the person gets a sentence they can act on."""
        r = self.client.post(
            self.url(),
            {'images': SimpleUploadedFile('IMG_4021.HEIC', b'\x00\x00\x00 ftypheic',
                                          content_type='image/heic')},
            format='multipart')
        self.assertEqual(r.status_code, 400)
        self.assertIn('JPG, PNG or WEBP', str(r.data))
        self.assertEqual(SalvageImage.objects.filter(item=self.item).count(), 0)

    def test_a_file_over_ten_megabytes_is_refused(self):
        big = SimpleUploadedFile('huge.png', b'\x89PNG' + b'0' * (10 * 1024 * 1024 + 1),
                                 content_type='image/png')
        r = self.client.post(self.url(), {'images': big}, format='multipart')
        self.assertEqual(r.status_code, 400)
        self.assertIn('10 mb', str(r.data).lower())

    def test_a_bad_file_in_the_batch_writes_none_of_them(self):
        """Validate every file before writing any — otherwise a bad third file
        leaves the first two half-attached and the yard has to guess."""
        r = self.client.post(
            self.url(),
            {'images': [photo('ok1.png'), photo('ok2.png'),
                        SimpleUploadedFile('notes.txt', b'hello',
                                           content_type='text/plain')]},
            format='multipart')
        self.assertEqual(r.status_code, 400)
        self.assertEqual(SalvageImage.objects.filter(item=self.item).count(), 0)

    def test_the_twenty_first_photo_is_refused(self):
        for n in range(20):
            SalvageImage.objects.create(item=self.item, image=f'salvage/x{n}.png',
                                        ordering=n)
        r = self.client.post(self.url(), {'images': photo()}, format='multipart')
        self.assertEqual(r.status_code, 400)
        self.assertIn('20 photos', str(r.data))
        self.assertEqual(SalvageImage.objects.filter(item=self.item).count(), 20)

    # ── delete and primary ────────────────────────────────────────────────
    def test_delete_removes_one_and_closes_the_ordering_gap(self):
        r = self.client.post(
            self.url(),
            {'images': [photo('a.png'), photo('b.png'), photo('c.png')]},
            format='multipart')
        middle = r.data['images'][1]['id']
        d = self.client.delete(f'{self.url()}{middle}/')
        self.assertEqual(d.status_code, 200, d.data)
        self.assertEqual(d.data['count'], 2)
        self.assertEqual([i['ordering'] for i in d.data['images']], [0, 1])

    def test_set_primary_moves_the_chosen_photo_to_ordering_zero(self):
        r = self.client.post(
            self.url(),
            {'images': [photo('a.png'), photo('b.png'), photo('c.png')]},
            format='multipart')
        third = r.data['images'][2]['id']
        p = self.client.post(f'{self.url()}{third}/primary/')
        self.assertEqual(p.status_code, 200, p.data)
        self.assertEqual(p.data['images'][0]['id'], third)
        self.assertEqual([i['ordering'] for i in p.data['images']], [0, 1, 2])
        # And the list payload the inventory table reads follows it.
        listing = self.client.get(f'/api/v1/salvage-items/{self.item.pk}/')
        self.assertEqual(listing.data['images'][0]['id'], third)

    def test_a_photo_on_another_item_cannot_be_deleted_through_this_one(self):
        mine  = SalvageItem.objects.create(item_code='SLV-TEST-0002',
                                           part_name='Door', company=self.company)
        theirs = SalvageImage.objects.create(item=mine, image='salvage/z.png')
        r = self.client.delete(f'{self.url()}{theirs.pk}/')
        self.assertEqual(r.status_code, 404)
        self.assertTrue(SalvageImage.objects.filter(pk=theirs.pk).exists())

    # ── access ────────────────────────────────────────────────────────────
    def test_a_user_with_no_salvage_access_is_refused(self):
        outsider = User.objects.create_user('outsider', 'out@example.com', 'x')
        self.client.force_authenticate(user=outsider)
        r = self.client.post(self.url(), {'images': photo()}, format='multipart')
        self.assertIn(r.status_code, (403, 404))
        self.assertEqual(SalvageImage.objects.filter(item=self.item).count(), 0)

    def test_signed_out_is_refused(self):
        self.client.force_authenticate(user=None)
        r = self.client.post(self.url(), {'images': photo()}, format='multipart')
        self.assertIn(r.status_code, (401, 403))

    # ── the photo has to be VISIBLE, not merely stored ────────────────────
    # /media/ is not served in this deployment (Django's static() helper does
    # nothing with DEBUG=False, and prod runs DEBUG=False). Before these, the
    # upload would have returned a clean 201 and every gallery tile would have
    # been a broken image: stored, deployed, invisible.
    def test_the_photo_url_is_never_a_media_path(self):
        r = self.client.post(self.url(), {'images': photo()}, format='multipart')
        url = r.data['images'][0]['image']
        self.assertNotIn('/media/', url)
        self.assertIn('/api/v1/salvage/photo/', url)

    def test_the_photo_url_actually_returns_the_image(self):
        r = self.client.post(self.url(), {'images': photo()}, format='multipart')
        img_id = r.data['images'][0]['id']
        got = self.client.get(f'/api/v1/salvage/photo/{img_id}/')
        self.assertEqual(got.status_code, 200)
        self.assertEqual(got['Content-Type'], 'image/png')
        self.assertEqual(b''.join(got.streaming_content)[:4], b'\x89PNG')

    def test_the_photo_url_works_without_signing_in(self):
        """Deliberate: an <img src> carries no bearer token, and the public
        buy-salvage portal renders these with no credentials at all."""
        r = self.client.post(self.url(), {'images': photo()}, format='multipart')
        img_id = r.data['images'][0]['id']
        self.client.force_authenticate(user=None)
        self.assertEqual(
            self.client.get(f'/api/v1/salvage/photo/{img_id}/').status_code, 200)

    def test_the_photo_url_never_publishes_the_uploaders_own_filename(self):
        """Django 5 otherwise writes the stored file's basename into
        Content-Disposition, and that name is whatever the uploader called it.
        A yard assessor's "CLM-2026-0123 B123ABC front.jpg" would publish a
        claim number and a registration plate on an open url."""
        r = self.client.post(
            self.url(),
            {'images': photo('CLM-2026-0123 B123ABC front.png')},
            format='multipart')
        img_id = r.data['images'][0]['id']
        got = self.client.get(f'/api/v1/salvage/photo/{img_id}/')
        disposition = got.get('Content-Disposition', '')
        self.assertNotIn('CLM-2026-0123', disposition)
        self.assertNotIn('B123ABC', disposition)
        self.assertIn(str(img_id), disposition)

    def test_a_junk_photo_id_in_the_url_is_not_a_500(self):
        """The first regex was [0-9a-fA-F-]{36}, which also matched 36 dashes;
        pk='------...' makes get_object_or_404 raise Django's ValidationError,
        which DRF does not translate — it answered 500."""
        r = self.client.delete(f'{self.url()}{"-" * 36}/')
        self.assertNotEqual(r.status_code, 500)
        self.assertEqual(r.status_code, 404)

    def test_an_unknown_photo_id_is_a_404_not_a_500(self):
        self.assertEqual(
            self.client.get(
                '/api/v1/salvage/photo/00000000-0000-4000-8000-000000000000/'
            ).status_code, 404)

    def test_a_row_whose_file_has_gone_is_a_404_not_a_500(self):
        """A database restored against a fresh media volume. A 404 is honest;
        a 500 reads as the whole site being down."""
        orphan = SalvageImage.objects.create(
            item=self.item, image='salvage/2026/09/never-written.png')
        self.assertEqual(
            self.client.get(f'/api/v1/salvage/photo/{orphan.pk}/').status_code, 404)

    def test_the_inventory_list_thumbnail_is_the_streaming_url(self):
        self.client.post(self.url(), {'images': photo()}, format='multipart')
        listing = self.client.get('/api/v1/salvage-items/')
        rows = listing.data['results'] if 'results' in listing.data else listing.data
        row = next(r for r in rows if r['item_code'] == 'SLV-TEST-0001')
        self.assertIsNotNone(row['primary_image'])
        self.assertIn('/api/v1/salvage/photo/', row['primary_image'])
        self.assertNotIn('/media/', row['primary_image'])
