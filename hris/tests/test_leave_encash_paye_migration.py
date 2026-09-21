"""Migration test for hris/0068 — the PAYE backfill.

Prod carries LIVE leave-encashment rows (checked 2026-08-17: 1 pending CFO,
3 pending HR, 1 paid, 1 rejected). Adding `net_amount` with a 0.00 default and
no backfill would make every one of them render, and EMAIL, as
"net payable P0.00" — and Finance could load zero against an approved payout.

This test drives the real migration graph backwards to 0067, writes rows the way
they existed before PAYE, migrates forward, and asserts the backfill put
net = gross on all of them. It fails if anyone removes the RunPython step.

⚠️ LOCAL `--keepdb` CAVEAT (does not affect CI, which builds a fresh database).
`TransactionTestCase` FLUSHES every table when it finishes, so a database kept
with `--keepdb` is left without the reference rows the migrations seeded (e.g.
core_currency's BWP row). `serialized_rollback = True` below restores them DURING
a run — so ordering inside the suite is safe — but the final flush still leaves a
KEPT database bare for the NEXT run. If a later `--keepdb` run suddenly errors
with `core_company_base_currency_id_... is not present in table "core_currency"`,
drop the test database and re-run; nothing is wrong with the code.
"""
from decimal import Decimal

from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase

APP = 'hris'
BEFORE = '0067_merge_disciplinary_and_corating'
AFTER = '0068_leaveencashment_paye'


def _required_blanks(model) -> dict:
    """Historical models are frozen at the migration they were captured in, so a
    NOT NULL text column whose default arrived in a LATER migration has no
    default here and the insert fails. Fill every such column with ''."""
    from django.db.models import CharField, TextField
    out = {}
    for f in model._meta.get_fields():
        if (isinstance(f, (CharField, TextField)) and not f.null
                and not f.has_default() and not f.primary_key):
            out[f.name] = ''
    return out


class LeaveEncashmentPayeBackfillTest(TransactionTestCase):
    # The migration graph is rewound, so this cannot share the fast TestCase
    # transaction wrapper; and every app's tables must exist to rewind safely.
    available_apps = None

    # MUST stay True. TransactionTestCase FLUSHES every table when it finishes,
    # which wipes reference data the migrations seeded — notably core_currency's
    # BWP row that Company.base_currency defaults to. Without this, every later
    # test in the run that creates a Company dies with a foreign-key error (52
    # errors when this was first written), and with --keepdb the damage persists
    # into the next run. serialized_rollback restores the migration-created rows.
    serialized_rollback = True

    @classmethod
    def setUpClass(cls):
        # An EARLIER TransactionTestCase in the run (healthcare's invoice-number
        # concurrency test, reset_sequences=True) flushes and lets post_migrate
        # re-create every django_content_type row — on DIFFERENT pks once any
        # new model has shifted the registry order. Our serialized_rollback
        # snapshot then re-inserts content types at their ORIGINAL pks →
        # IntegrityError: duplicate (app_label, model). Broke main 17-Aug-2026
        # when the deploy batch added subrogation/nbfira models. Clearing the
        # re-seeded rows first lets the snapshot restore cleanly, whatever other
        # TransactionTestCases ran before us.
        from django.contrib.contenttypes.models import ContentType
        ContentType.objects.all().delete()
        super().setUpClass()

    def _migrate_to(self, target):
        executor = MigrationExecutor(connection)
        executor.loader.build_graph()
        executor.migrate([(APP, target)])
        executor.loader.build_graph()
        return executor

    def tearDown(self):
        # Always leave the database on the LATEST hris migration for other
        # tests — the live leaf, not the AFTER constant, which froze at 0068
        # and would strand the run one migration behind once 0069+ exist.
        executor = MigrationExecutor(connection)
        leaf = executor.loader.graph.leaf_nodes(APP)
        executor.migrate(leaf)
        executor.loader.build_graph()

    def test_backfill_sets_net_equal_to_gross_on_pre_existing_rows(self):
        executor = self._migrate_to(BEFORE)
        old_state = executor.loader.project_state((APP, BEFORE)).apps

        # Only `hris` is rewound — core/payroll tables stay at the LATEST schema,
        # so their historical models are missing columns the DB still requires
        # (e.g. payroll_employee.archive_reason NOT NULL). Use the real models for
        # those; the historical model is needed ONLY for LeaveEncashment, whose
        # three new columns must not exist yet.
        from core.models import Company, Currency
        from payroll.models import Employee
        Encashment = old_state.get_model(APP, 'LeaveEncashment')

        # The new columns must not exist yet — otherwise this test proves nothing.
        self.assertNotIn('net_amount', [f.name for f in Encashment._meta.get_fields()])

        # Company defaults base_currency to 'BWP', which must exist as a row.
        Currency.objects.get_or_create(code='BWP',
                                       defaults={'name': 'Botswana Pula'})
        company = Company.objects.create(code='BFT', name='Backfill Test Co')
        employee = Employee.objects.create(
            employee_number='BF1', full_name='Backfill Person',
            status='active', company=company)

        # Mirror the real prod mix: in flight, already paid, and rejected.
        fixtures = [
            ('pending_cfo', Decimal('20'), Decimal('58333.40'), False),
            ('pending_hr',  Decimal('8'),  Decimal('2666.64'),  False),
            ('paid',        Decimal('11'), Decimal('21725.00'), True),
            ('rejected',    Decimal('10'), Decimal('2500.00'),  False),
        ]
        for status, days, amount, paid in fixtures:
            kwargs = _required_blanks(Encashment)
            kwargs.update(  # explicit values win over the blank filler
                # FKs by id — the historical model rejects a real-model instance.
                employee_id=employee.id, company_id=company.id,
                leave_type_code='annual',
                days=days, basic_salary=Decimal('70000'),
                daily_rate=Decimal('2916.67'), amount=amount,
                balance_at_request=Decimal('44'), basic_source='2026-07',
                status=status, payroll_processed=paid,
                applicant_email='x@y.example', reason='r')
            Encashment.objects.create(**kwargs)

        # ── the thing under test ────────────────────────────────────────────
        executor = self._migrate_to(AFTER)
        new_state = executor.loader.project_state((APP, AFTER)).apps
        NewEncashment = new_state.get_model(APP, 'LeaveEncashment')

        rows = list(NewEncashment.objects.order_by('amount'))
        self.assertEqual(len(rows), len(fixtures))
        for row in rows:
            with self.subTest(status=row.status):
                # No PAYE was withheld on these — say so, and never show a zero net.
                self.assertEqual(row.tax_amount, Decimal('0.00'))
                self.assertEqual(row.tax_base, Decimal('0.00'))
                self.assertEqual(row.net_amount, row.amount)
                self.assertGreater(row.net_amount, Decimal('0.00'))
