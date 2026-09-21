"""L-DEPT: fold Employee.department onto the CFO-approved list (18-Sep-2026)."""
from io import StringIO

from django.core.management import call_command
from django.test import TestCase

from core.models import AuditLog
from payroll.models import Employee


def _run(*args):
    out = StringIO()
    call_command('fold_employee_departments', *args, stdout=out)
    return out.getvalue()


class FoldEmployeeDepartmentsTests(TestCase):
    def setUp(self):
        self.fin = Employee.objects.create(employee_number='FOLD-0', full_name='Fold Finance', department='Finance')
        self.uc = Employee.objects.create(employee_number='FOLD-1', full_name='Fold UniCoin', department='Uni Coin')
        self.ex = Employee.objects.create(employee_number='FOLD-2', full_name='Fold Exco', department='C-Suite')
        self.ok = Employee.objects.create(employee_number='FOLD-3', full_name='Fold Claims', department='Claims')
        self.blank = Employee.objects.create(employee_number='FOLD-4', full_name='Fold Blank', department='')
        self.odd = Employee.objects.create(employee_number='FOLD-5', full_name='Fold Odd', department='Mystery Unit')

    def _depts(self):
        return {e.full_name: e.department for e in Employee.objects.filter(full_name__startswith='Fold ')}

    def test_dry_run_changes_nothing(self):
        before = self._depts()
        out = _run()
        self.assertEqual(self._depts(), before)
        self.assertIn("WOULD", out)
        self.assertIn('Would move 3', out)
        self.assertFalse(AuditLog.objects.filter(description__contains='L-DEPT').exists())

    def test_commit_folds_known_spellings_only(self):
        _run('--commit')
        d = self._depts()
        self.assertEqual(d['Fold Finance'], 'Finance & Planning')
        self.assertEqual(d['Fold UniCoin'], 'UniCoin')
        self.assertEqual(d['Fold Exco'], 'Executive')
        self.assertEqual(d['Fold Claims'], 'Claims')
        self.assertEqual(d['Fold Blank'], '')             # never guessed
        self.assertEqual(d['Fold Odd'], 'Mystery Unit')   # unknown left alone

    def test_unknown_value_is_reported(self):
        self.assertIn("'Mystery Unit' x1", _run())

    def test_commit_is_idempotent(self):
        _run('--commit')
        self.assertIn('Moved 0', _run('--commit'))

    def test_every_move_is_audited_with_the_old_value(self):
        _run('--commit')
        rows = AuditLog.objects.filter(description__contains='L-DEPT')
        self.assertEqual(rows.count(), 3)
        fin = rows.get(record_id=str(self.fin.pk))
        self.assertEqual(fin.old_values, {'department': 'Finance'})
        self.assertEqual(fin.new_values, {'department': 'Finance & Planning'})


class SeedWritesApprovedSpellingTests(TestCase):
    def test_setup_employees_writes_the_approved_spelling(self):
        from unittest import mock
        legacy = [('Seed Legacy Person', 'Information Technology')]
        with mock.patch('payroll.management.commands.setup_employees.EMPLOYEES', legacy):
            call_command('setup_employees', stdout=StringIO())
        self.assertEqual(Employee.objects.get(full_name='Seed Legacy Person').department, 'Admin & IT')


class ExecutivesStayExecutivesAfterTheFoldTests(TestCase):
    """Opus judge 18-Sep: after the fold executives read 'Executive'; the board
    pin/routing and login-role defaults must still treat them as executives."""

    def test_board_pin_includes_the_approved_name(self):
        from hris.exceptions_report import _leaderboard_always_show
        self.assertIn('Executive', _leaderboard_always_show())

    def test_login_default_role_for_executive(self):
        from payroll.management.commands.setup_employee_users import _defaults_for
        from core.models import UserProfile
        emp = Employee(full_name='Some Executive', department='Executive')
        title, role, _ = _defaults_for(emp)
        self.assertEqual(role, UserProfile.Role.EXECUTIVE)

    def test_commit_skips_a_row_changed_after_the_plan(self):
        from hris.management.commands import fold_employee_departments as cmd
        e = Employee.objects.create(employee_number='FOLD-X', full_name='Fold Race', department='Finance')
        real_plan = cmd.plan_folds

        def plan_then_edit():
            moves, unknown = real_plan()
            Employee.objects.filter(pk=e.pk).update(department='Claims')
            return moves, unknown
        from unittest import mock
        with mock.patch.object(cmd, 'plan_folds', plan_then_edit):
            _run('--commit')
        e.refresh_from_db()
        self.assertEqual(e.department, 'Claims')
