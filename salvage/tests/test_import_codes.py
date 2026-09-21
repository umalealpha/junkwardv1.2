"""Bulk salvage import with generated item codes — 17-Sep-2026.

Bharath asked for the code to be generated "for each salvage/part". Fable's
review caught that the first cut only covered the intake form: the spreadsheet
import still refused a file without an item_code column, while the save()
docstring claimed the import was covered. It is covered now, and these tests
are what stops that claim drifting back into a lie.

Every row of a file with NO codes must get its own number — the earlier bug
class here is a duplicate check running against the blank string, which would
have rejected every row after the first.
"""
from __future__ import annotations

import io

from django.contrib.auth.models import User
from rest_framework.test import APITestCase

from core.models import Company
from salvage.models import SalvageItem

URL = '/api/v1/salvage-items/import/'


def csv_file(text: str, name: str = 'stock.csv'):
    from django.core.files.uploadedfile import SimpleUploadedFile
    return SimpleUploadedFile(name, text.encode('utf-8'), content_type='text/csv')


class ImportItemCodeTests(APITestCase):

    @classmethod
    def setUpTestData(cls):
        cls.company = Company.objects.create(code='VCM', name='Veritas Capital')
        cls.user = User.objects.create_superuser(
            'yardmanager', 'yard@alphadirect.co.bw', 'x')

    def setUp(self):
        self.client.force_authenticate(user=self.user)

    def post(self, text):
        return self.client.post(
            URL, {'file': csv_file(text), 'company': str(self.company.id)},
            format='multipart')

    def test_a_file_with_no_item_code_column_is_accepted(self):
        """This is the whole ask — the operator should not have to invent
        codes in a spreadsheet either."""
        r = self.post(
            'part_name,condition\n'
            'Toyota Hilux bonnet,good\n'
            'Nissan Magnite battery,fair\n'
        )
        self.assertEqual(r.status_code, 201, r.content)
        codes = sorted(SalvageItem.objects.values_list('item_code', flat=True))
        self.assertEqual(codes, ['ML-0001', 'ML-0002'])

    def test_every_row_gets_its_OWN_number(self):
        """A duplicate check run against the blank string would reject every
        row after the first, or hand them all one code."""
        rows = '\n'.join(f'Part {i},fair' for i in range(1, 6))
        r = self.post(f'part_name,condition\n{rows}\n')
        self.assertEqual(r.status_code, 201, r.content)
        codes = set(SalvageItem.objects.values_list('item_code', flat=True))
        self.assertEqual(len(codes), 5)

    def test_a_supplied_code_is_still_honoured(self):
        """Existing workbooks must keep working exactly as before."""
        r = self.post(
            'item_code,part_name\n'
            'LEG-0001,Hyundai Venue fuel pump\n'
        )
        self.assertEqual(r.status_code, 201, r.content)
        self.assertEqual(
            list(SalvageItem.objects.values_list('item_code', flat=True)),
            ['LEG-0001'],
        )

    def test_a_duplicate_supplied_code_is_still_refused(self):
        """Relaxing the blank case must not switch the duplicate guard off."""
        self.post('item_code,part_name\nML-0099,First\n')
        r = self.post('item_code,part_name\nML-0099,Second\n')
        body = str(r.content)
        self.assertIn('ML-0099', body)
        self.assertEqual(SalvageItem.objects.filter(item_code='ML-0099').count(), 1)

    def test_a_mixed_file_fills_only_the_blanks(self):
        r = self.post(
            'item_code,part_name\n'
            'LEG-0002,Supplied one\n'
            ',Blank one\n'
        )
        self.assertEqual(r.status_code, 201, r.content)
        codes = set(SalvageItem.objects.values_list('item_code', flat=True))
        self.assertEqual(codes, {'LEG-0002', 'ML-0001'})

    def test_part_name_is_still_required(self):
        r = self.post('item_code,condition\nML-0500,fair\n')
        self.assertEqual(r.status_code, 400, r.content)
        self.assertIn('part_name', str(r.content))
