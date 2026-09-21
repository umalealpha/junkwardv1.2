"""Auto-attach new joiners in an entity to a default manager, filling BLANKS only."""
from io import StringIO

from django.core.management import call_command
from django.test import TestCase, override_settings

from core.models import Company
from hris.models import HRISProfile
from payroll.models import Employee


@override_settings(ENTITY_DEFAULT_MANAGERS=[{'company': 'Veritas', 'head_email': 'head@x.co'}])
class DefaultEntityManagerTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.co = Company.objects.create(code='VER', name='Veritas Capital')
        cls.other = Company.objects.create(code='ADI', name='Alpha Direct Insurance')
        mk = lambda n, fn, co, **k: Employee.objects.create(
            employee_number=n, full_name=fn, company=co, email=f'{n}@x.co', **k)
        cls.head = mk('head', 'The Head', cls.co)   # email -> head@x.co
        cls.joiner = mk('j1', 'New Joiner', cls.co)          # blank manager -> should fill
        cls.has_mgr = mk('j2', 'Already Managed', cls.co)    # already has a manager -> untouched
        cls.leaver = mk('j3', 'A Leaver', cls.co, status=Employee.Status.TERMINATED)
        cls.outsider = mk('o1', 'Other Entity', cls.other)   # different company -> untouched

        HRISProfile.objects.create(employee=cls.head)
        cls.p_joiner = HRISProfile.objects.create(employee=cls.joiner)                      # manager NULL
        HRISProfile.objects.create(employee=cls.has_mgr, manager=cls.head)                  # manager set (by hand)
        HRISProfile.objects.create(employee=cls.leaver)                                     # terminated, NULL
        cls.p_outsider = HRISProfile.objects.create(employee=cls.outsider)                  # other company, NULL

    def test_dry_run_writes_nothing(self):
        call_command('default_entity_manager', stdout=StringIO())
        self.p_joiner.refresh_from_db()
        self.assertIsNone(self.p_joiner.manager_id)

    def test_commit_fills_only_blank_managers_in_the_entity(self):
        call_command('default_entity_manager', commit=True, stdout=StringIO())
        self.p_joiner.refresh_from_db()
        self.assertEqual(self.p_joiner.manager_id, self.head.id)          # filled
        # Terminated joiner is skipped.
        self.assertIsNone(HRISProfile.objects.get(employee=self.leaver).manager_id)
        # A manager set by hand is never overwritten.
        self.assertEqual(HRISProfile.objects.get(employee=self.has_mgr).manager_id, self.head.id)
        # Another entity is untouched.
        self.p_outsider.refresh_from_db()
        self.assertIsNone(self.p_outsider.manager_id)

    def test_idempotent(self):
        call_command('default_entity_manager', commit=True, stdout=StringIO())
        call_command('default_entity_manager', commit=True, stdout=StringIO())
        self.p_joiner.refresh_from_db()
        self.assertEqual(self.p_joiner.manager_id, self.head.id)
