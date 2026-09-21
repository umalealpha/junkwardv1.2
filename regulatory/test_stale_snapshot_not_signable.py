"""A snapshot computed on a withdrawn method must not be signable.

Suppressing the LIVE ratio on 2026-08-10 left the stored one untouched. The only
snapshot on prod (2026-07-24) still carried CAR 612.02%, a green COMPLIANT badge
and an enabled Approve button, sitting directly under a banner saying the figure
is not for regulatory or board use.

Approval is what makes a snapshot the official record behind a statutory return.
The only gate on it was segregation of duties, which stops the preparer and not a
second approver — so a second approver could have signed 612% as the audit trail
for a quarter whose lawful cover is between 0.83 and 1.54.
"""
from datetime import date
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from regulatory.models import CapitalRequirementParameter, RegulatoryCapitalSnapshot
from regulatory.services import snapshot_method_state

# Every parameter the live reg 4 formula reads. Adopting the method means putting
# an effective date on all of them, so the tests below have to do the same.
_FORMULA = (('minimum_capital_floor', '5000000'),
            ('opex_factor', '0.25'),
            ('compliant_threshold', '1.25'),
            ('margin_threshold', '1.00'),
            ('intangibles_account_codes', ''))


def _adopt_the_method(effective=date(2026, 8, 15)):
    for code, value in _FORMULA:
        CapitalRequirementParameter.objects.update_or_create(
            code=code,
            defaults={'label': code, 'value': value, 'is_active': True,
                      'effective_from': effective,
                      'notes': 'Adopted per S.I. 68 of 2019. Signed off by the CFO.'})


class StaleSnapshotIsNotSignableTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.preparer = User.objects.create_superuser(
            'prep', 'prep@example.invalid', 'x')
        self.approver = User.objects.create_superuser(
            'appr', 'appr@example.invalid', 'x')
        CapitalRequirementParameter.objects.create(
            code='opex_factor', label='Opex factor', value='0.25',
            is_active=True,
            notes='Reg 4(b). VERIFY against current rule.')
        # The prod snapshot, reproduced: 30,601,185.06 / 5,000,000 = 612.02%.
        self.snap = RegulatoryCapitalSnapshot.objects.create(
            as_of_date=date(2026, 7, 24),
            available_capital=Decimal('30601185.06'),
            required_capital=Decimal('5000000.00'),
            capital_adequacy_ratio=Decimal('6.1202'),
            status=RegulatoryCapitalSnapshot.Status.COMPLIANT,
            components={'parameters': {'premium_factor': '0.18',
                                       'claims_factor': '0.26'}},
            prepared_by=self.preparer,
        )

    def test_it_is_flagged_as_not_on_the_current_method(self):
        state = snapshot_method_state(self.snap)
        self.assertFalse(state['method_current'])
        self.assertIn('never been adopted', state['method_note'])

    def test_a_second_approver_cannot_sign_it(self):
        client = APIClient()
        client.force_authenticate(self.approver)
        r = client.post(f'/api/v1/capital-snapshots/{self.snap.id}/approve/')
        self.assertEqual(r.status_code, 400, r.content)
        self.snap.refresh_from_db()
        self.assertIsNone(self.snap.approved_by_id,
                          '612% must not become the signed record')

    def test_the_list_tells_the_screen_it_is_withdrawn(self):
        client = APIClient()
        client.force_authenticate(self.approver)
        r = client.get('/api/v1/capital-snapshots/')
        self.assertEqual(r.status_code, 200, r.content)
        row = r.json()['results'][0]
        self.assertFalse(row['method_current'])
        self.assertTrue(row['method_note'])

    def test_a_snapshot_on_the_adopted_method_stays_signable(self):
        """The gate must not simply block everything — that would be a broken
        button dressed up as a control."""
        _adopt_the_method()
        # The corrected reg 4 position: own funds of P17.01m (equity, with the
        # reinsurers' share excluded) against a target of P7.06m — 25% of ADIC's
        # actual P28.24m opex.
        current = RegulatoryCapitalSnapshot.objects.create(
            as_of_date=date(2026, 8, 15),
            available_capital=Decimal('17010997.59'),
            required_capital=Decimal('7061241.94'),
            capital_adequacy_ratio=Decimal('2.4090'),
            status=RegulatoryCapitalSnapshot.Status.COMPLIANT,
            components={'parameters': dict(_FORMULA)},
            prepared_by=self.preparer,
        )
        self.assertTrue(snapshot_method_state(current)['method_current'])
        client = APIClient()
        client.force_authenticate(self.approver)
        r = client.post(f'/api/v1/capital-snapshots/{current.id}/approve/')
        self.assertEqual(r.status_code, 200, r.content)

    def test_a_parameter_moving_supersedes_an_earlier_snapshot(self):
        _adopt_the_method()
        snap = RegulatoryCapitalSnapshot.objects.create(
            as_of_date=date(2026, 8, 15),
            available_capital=Decimal('17010997.59'),
            required_capital=Decimal('7061241.94'),
            capital_adequacy_ratio=Decimal('2.4090'),
            status=RegulatoryCapitalSnapshot.Status.COMPLIANT,
            components={'parameters': dict(_FORMULA)},
            prepared_by=self.preparer,
        )
        self.assertTrue(snapshot_method_state(snap)['method_current'])
        p = CapitalRequirementParameter.objects.get(code='opex_factor')
        p.value = '0.30'
        p.save()
        state = snapshot_method_state(snap)
        self.assertFalse(state['method_current'])
        self.assertIn('Superseded', state['method_note'])
        self.assertIn('opex_factor', state['drifted_parameters'])
