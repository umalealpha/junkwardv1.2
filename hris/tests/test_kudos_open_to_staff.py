"""The kudos feed is on every staff member's home page, so every staff
member must be able to read it (Motlatsi, 2026-09-10).

/hris/api/kudos/ was gated with `_gate(request)` and no capability, which puts
it on the PRIVILEGED tier — the five-person HRIS whitelist. But the feed and
its send form sit on /my-omni (everyone's home screen) and on /hris/rewards,
so every ordinary employee got 403 on their own home page and the tile read as
broken. The live access log showed the 403 firing on each /my-omni load.

Recognition is peer-to-peer by design: the read is already filtered to public
kudos plus ones addressed to the caller and entity-scoped, and the write forces
the sender to the caller's own profile. Self-service tier is the correct tier.
"""
from django.contrib.auth.models import User
from django.test import TestCase
from rest_framework.authtoken.models import Token

from core.models import Company, Currency
from hris.models import HRISProfile, Recognition
from payroll.models import Employee


class KudosOpenToStaffTest(TestCase):

    @classmethod
    def setUpTestData(cls):
        Currency.objects.get_or_create(
            code='BWP', defaults={'name': 'Botswana Pula', 'symbol': 'P'})
        cls.co = Company.objects.create(code='ADKUD', name='Alpha Direct')

        # An ordinary employee: NOT a superuser, NOT an administrator, and a
        # local-part that is not on the HRIS whitelist — i.e. role 'ess'.
        cls.user = User.objects.create_user(
            'mmolefe_kudos', 'mmolefe.kudostest@alphadirect.co.bw', 'x')
        cls.emp = Employee.objects.create(
            employee_number='K1', full_name='Ordinary Staffer', company=cls.co,
            user=cls.user)
        cls.profile = HRISProfile.objects.create(employee=cls.emp)
        cls.token = Token.objects.create(user=cls.user)

        # Somebody else's public kudos, so the feed has a row to return.
        other_user = Employee.objects.create(
            employee_number='K2', full_name='Colleague Two', company=cls.co)
        cls.other = HRISProfile.objects.create(employee=other_user)
        Recognition.objects.create(
            sender=cls.other, receiver=cls.profile,
            value_demonstrated='teamwork', message='Carried the close.',
            points=3, is_public=True)

    def _get(self):
        return self.client.get(
            '/hris/api/kudos/',
            HTTP_AUTHORIZATION=f'Token {self.token.key}')

    def test_ordinary_employee_can_read_the_feed(self):
        r = self._get()
        self.assertEqual(r.status_code, 200, r.content[:300])
        self.assertIn('kudos', r.json())

    def test_the_feed_actually_returns_rows(self):
        # A 200 with an empty list would look identical to the broken tile.
        body = self._get().json()
        self.assertEqual(body['count'], 1)
        self.assertEqual(body['kudos'][0]['message'], 'Carried the close.')

    def test_anonymous_is_still_refused(self):
        # The gate must fail in BOTH directions — opening it to staff must not
        # open it to the public.
        r = self.client.get('/hris/api/kudos/')
        self.assertIn(r.status_code, (401, 403))
