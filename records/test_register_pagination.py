"""records/test_register_pagination.py — the register shows the WHOLE list.

Tlotlo Maswabi 2026-08-13: records past the first page of 25 were invisible
(default DRF pagination + no next-page control on the screen). The register must
return all rows.
"""
from django.contrib.auth.models import User
from django.test import TestCase
from rest_framework.test import APIClient

from records.models import RecordCategory, RecordItem


class RegisterPaginationTests(TestCase):
    def test_register_returns_all_records_not_just_first_25(self):
        cat = RecordCategory.objects.create(name='HR files')
        for i in range(30):
            RecordItem.objects.create(reference=f'REG-{i:03d}', title=f'File {i}',
                                      category=cat, confidentiality='internal')
        su = User.objects.create_superuser('regboss', 'regboss@example.com', 'x')
        c = APIClient(); c.force_authenticate(su)
        r = c.get('/api/v1/records/')
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.data['count'], 30)
        self.assertEqual(len(r.data['results']), 30)   # all 30, not capped at 25
