"""A read-only QC key must see the whole payment register.

Manus tested the key on production on 2026-08-09 and got an empty list alongside
`submission_window.is_open: false`, which reads like a closed window. It was not
the window: the key belongs to no approver, so the queryset fell through to
"requests I raised myself" — always none for a service account. The key could not
do the one job it was issued for.
"""
from django.contrib.auth import get_user_model
from django.contrib.auth.hashers import make_password
from django.test import TestCase
from rest_framework.test import APIClient

from core.models import ApiKey
from taskboard.models import PaymentRequest

User = get_user_model()

PLAINTEXT = 'qcreadonlykey_testonly_0123456789'


class QcReaderSeesTheWholeRegisterTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.raiser = User.objects.create_user('araiser', 'ar@example.invalid', 'x')
        cls.svc = User.objects.create_user('bqcsvc', 'qc@example.invalid', 'x')
        for i, (ent, st) in enumerate((
                ('ADIC', PaymentRequest.Status.PENDING_FINANCE),
                ('AIZ', PaymentRequest.Status.PENDING_CFO),
                ('ADRG', PaymentRequest.Status.PENDING_FINANCE))):
            PaymentRequest.objects.create(
                ref=f'PR-QC-{i}', entity=ent, status=st, payee=f'Payee {i}',
                currency='BWP', total='100.00', created_by=cls.raiser)
        ApiKey.objects.create(
            label='QC reader', key_prefix=PLAINTEXT[:12],   # _PREFIX_LEN is 12; the model docstring says 8
            key_hash=make_password(PLAINTEXT), service_user=cls.svc,
            allowed_scopes=['read-only'], is_active=True)

    def _key_client(self):
        c = APIClient()
        c.credentials(HTTP_AUTHORIZATION=f'ApiKey {PLAINTEXT}')
        return c

    def test_read_only_key_sees_every_entity_and_status(self):
        res = self._key_client().get('/api/v1/payment-requests/?all=1')
        self.assertEqual(res.status_code, 200, res.content[:300])
        refs = {r['ref'] for r in res.json()['requests']}
        self.assertEqual(refs, {'PR-QC-0', 'PR-QC-1', 'PR-QC-2'},
                         'the QC reader must see all three entities, not an empty list')

    def test_the_key_still_cannot_change_anything(self):
        res = self._key_client().post('/api/v1/payment-requests/', {}, format='json')
        self.assertIn(res.status_code, (401, 403), 'a read-only key must never write')

    def test_widening_the_qc_reader_did_not_widen_ordinary_staff(self):
        c = APIClient()
        c.force_authenticate(self.svc)
        res = c.get('/api/v1/payment-requests/?all=1')
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json()['requests'], [],
                         'the same service user, signed in normally, sees only its own')
