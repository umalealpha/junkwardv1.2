"""Auto-generated salvage item codes — Bharath Balasubramanian, 17-Sep-2026.

"Code must be auto generated for each salvage/part. Manually doing this might
end up in getting duplicates."

Before this change `item_code` was a required free-text box on the intake form.
Two people booking stock in at the same time both typed the next number they
could see on screen, and the second one hit the unique index — a 500, not a
message. Now the server draws it.

Each test covers something that would actually hurt:
  * a blank code is filled, and it continues the yard's own ML-#### run
    rather than starting a second numbering nobody recognises;
  * a code the CALLER supplied is left alone — the spreadsheet import and
    `import_motor_liquidators` depend on that to stay idempotent;
  * the run is ranked on the number, not the string, so it does not go
    backwards the day the yard passes ML-9999;
  * the REST intake works with no item_code in the body at all, which is what
    the form now posts.
"""
from __future__ import annotations

from django.contrib.auth.models import User
from django.db import transaction
from django.test import TestCase
from rest_framework.test import APITestCase

from core.models import Company
from salvage.models import SalvageItem, next_item_code


class ItemCodeGenerationTests(TestCase):

    @classmethod
    def setUpTestData(cls):
        cls.company = Company.objects.create(code='VCM', name='Veritas Capital')

    def make(self, **kw):
        kw.setdefault('part_name', 'Nissan Magnite battery')
        kw.setdefault('company', self.company)
        return SalvageItem.objects.create(**kw)

    def test_blank_code_is_generated(self):
        """The whole point: save with nothing in the box and get a code."""
        item = self.make()
        self.assertEqual(item.item_code, 'ML-0001')
        item.refresh_from_db()
        self.assertEqual(item.item_code, 'ML-0001')

    def test_generation_continues_the_existing_yard_run(self):
        """The yard is on ML-0051. The next one must be ML-0052, not ML-0001."""
        self.make(item_code='ML-0051')
        self.assertEqual(self.make().item_code, 'ML-0052')

    def test_a_supplied_code_is_never_overwritten(self):
        """The imports carry their own legacy codes and must keep them."""
        item = self.make(item_code='LEG-G2026005534')
        self.assertEqual(item.item_code, 'LEG-G2026005534')

    def test_two_saves_in_a_row_do_not_collide(self):
        """The duplicate Bharath was worried about."""
        codes = {self.make().item_code for _ in range(5)}
        self.assertEqual(len(codes), 5)
        self.assertEqual(
            sorted(codes),
            ['ML-0001', 'ML-0002', 'ML-0003', 'ML-0004', 'ML-0005'],
        )

    def test_run_is_ranked_on_the_number_not_the_string(self):
        """Past ML-9999 the padding gives out and ordering by text would rank
        'ML-9999' above 'ML-10000' and hand out a code already taken."""
        self.make(item_code='ML-9999')
        self.make(item_code='ML-10000')
        with transaction.atomic():
            self.assertEqual(next_item_code(), 'ML-10001')

    def test_an_existing_item_is_never_renumbered(self):
        """Blanking the code of a saved item must refuse, not quietly hand it
        the next number — the label on the physical part would stop matching."""
        item = self.make(item_code='ML-0007')
        item.item_code = ''
        with self.assertRaises(ValueError):
            item.save()
        item.refresh_from_db()
        self.assertEqual(item.item_code, 'ML-0007')

    def test_foreign_codes_do_not_disturb_the_run(self):
        """LEG-/SLV- rows sit in the same table and must be ignored."""
        self.make(item_code='LEG-whatever')
        self.make(item_code='SLV-2026-0001')
        self.make(item_code='ML-0007')
        self.assertEqual(self.make().item_code, 'ML-0008')


class ItemCodeApiTests(APITestCase):
    """The intake form no longer posts item_code at all."""

    @classmethod
    def setUpTestData(cls):
        cls.company = Company.objects.create(code='VCM', name='Veritas Capital')
        cls.user = User.objects.create_superuser(
            'yardmanager', 'yard@alphadirect.co.bw', 'x')

    def setUp(self):
        self.client.force_authenticate(user=self.user)

    def test_create_without_item_code_succeeds_and_returns_the_code(self):
        r = self.client.post('/api/v1/salvage-items/', {
            'part_name': 'Hyundai Venue fuel pump',
            'condition': 'fair',
            'status':    'available',
            'company':   str(self.company.id),
        }, format='json')
        self.assertEqual(r.status_code, 201, r.content)
        self.assertEqual(r.data['item_code'], 'ML-0001')

    def test_two_creates_get_different_codes(self):
        body = {
            'part_name': 'Toyota Prado differential',
            'condition': 'good',
            'status':    'available',
            'company':   str(self.company.id),
        }
        first  = self.client.post('/api/v1/salvage-items/', body, format='json')
        second = self.client.post('/api/v1/salvage-items/', body, format='json')
        self.assertEqual(first.status_code, 201, first.content)
        self.assertEqual(second.status_code, 201, second.content)
        self.assertNotEqual(first.data['item_code'], second.data['item_code'])
