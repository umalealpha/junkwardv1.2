"""Who can open the reinsurer KYC / evidence register.

The CFO, 17-Sep-2026, on being told only a superuser could open it:
"i dont understand these kyc documents should be able to be accessed by any
manager" — and, asked specifically who should see a director's ID or passport
copy: "Underwriting, AML officers, Complaince managers, exco and csuit".

`re.view` is the gate on every reinsurance endpoint, including the document
register (reinsurance.document_views.VIEW_PERM). Before this it was held only
by the CEO and the two reinsurance officer roles, so the people who actually
do the AML work could not open the file they are accountable for.

These tests run the real seeder and read the real permission check, so they
fail if someone edits the catalogue without meaning to.
"""
from django.core.management import call_command
from django.test import TestCase

from core.models import Permission, Role

#: The roles he named. Reinsurance itself already held it.
MUST_SEE = [
    # exco / c-suite
    'CEO', 'CFO', 'COO', 'CRO', 'CTO',
    # underwriting
    'HEAD_UNDERWRITING', 'UNDERWRITING_MANAGER', 'SENIOR_UNDERWRITER', 'UNDERWRITER',
    # AML / compliance
    'HEAD_COMPLIANCE', 'COMPLIANCE_MANAGER', 'RISK_OFFICER', 'INTERNAL_AUDITOR',
    # the department that owns the register
    'HEAD_REINSURANCE', 'REINSURANCE_MANAGER', 'SENIOR_RI_OFFICER', 'RI_OFFICER',
]

#: Roles he did NOT name. A KYC file holds directors' ID documents, so a role
#: picking this up by accident is a privacy leak, not a harmless extra.
#: IT_MANAGER is here for a concrete reason: it shares a permission line with
#: the CTO in the catalogue, and a careless edit gives IT the whole register.
MUST_NOT_SEE = ['IT_MANAGER', 'IT_HELPDESK', 'HR_MANAGER', 'HR_OFFICER',
                'PAYROLL_OFFICER', 'AP_CLERK', 'AR_CLERK', 'CLAIMS_ADJUSTER',
                'EXTERNAL_AUDITOR', 'NBFIRA_INSPECTOR']


class KycRegisterAccessTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command('seed_roles', '--skip-bootstrap', verbosity=0)

    def _codes(self, role_code):
        role = Role.objects.filter(code=role_code).first()
        self.assertIsNotNone(role, f'{role_code} is not in the catalogue')
        return set(role.permissions.values_list('code', flat=True))

    def test_the_roles_he_named_can_open_the_register(self):
        for code in MUST_SEE:
            self.assertIn('re.view', self._codes(code),
                          f'{code} cannot open the reinsurer KYC register')

    def test_nobody_else_picked_it_up(self):
        for code in MUST_NOT_SEE:
            self.assertNotIn('re.view', self._codes(code),
                             f'{code} was never named but can read counterparty ID documents')

    def test_looking_is_not_signing_off(self):
        """Widening the read must not widen the compliance sign-off with it."""
        from reinsurance.document_views import VERIFY_PERM

        self.assertTrue(Permission.objects.filter(code=VERIFY_PERM).exists())
        for code in ('UNDERWRITER', 'SENIOR_UNDERWRITER', 'UNDERWRITING_MANAGER',
                     'CTO', 'INTERNAL_AUDITOR', 'RISK_OFFICER'):
            perms = self._codes(code)
            self.assertIn('re.view', perms)
            self.assertNotIn(VERIFY_PERM, perms,
                             f'{code} can mark a KYC document verified')
