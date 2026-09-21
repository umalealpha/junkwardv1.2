"""python manage.py encrypt_employee_pii [--commit]

Back-fill: encrypt existing plaintext Employee national_id + bank_account_no at rest
(DPA S-5). Read-tolerant field, so this is safe + idempotent. --commit does it (with
per-record round-trip verification); default is a dry-run count. Authorised payroll/HR
views keep reading the values transparently. CFO directive 2026-07-20.
"""
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Encrypt existing Employee national_id + bank_account_no at rest."

    def add_arguments(self, parser):
        parser.add_argument('--commit', action='store_true', help='Actually encrypt (default: dry-run).')

    def handle(self, *args, **opts):
        from payroll.models import Employee
        from django.db import connection

        def _plain(col):
            with connection.cursor() as c:
                c.execute(f"SELECT count(*) FROM payroll_employee "
                          f"WHERE {col} != '' AND {col} NOT LIKE 'enc::%%'")
                return c.fetchone()[0]

        total = Employee.objects.count()
        if not opts['commit']:
            self.stdout.write(f"[dry-run] {total} employees; plaintext national_id={_plain('national_id')} "
                              f"bank_account_no={_plain('bank_account_no')}. Re-run with --commit.")
            return

        done = fail = 0
        for emp in Employee.objects.all().iterator():
            nid, ban = emp.national_id, emp.bank_account_no          # read-tolerant -> plaintext
            Employee.objects.filter(pk=emp.pk).update(national_id=nid, bank_account_no=ban)  # -> encrypts
            fresh = Employee.objects.get(pk=emp.pk)                  # round-trip verify
            if fresh.national_id == nid and fresh.bank_account_no == ban:
                done += 1
            else:
                fail += 1
                self.stderr.write(f"VERIFY-FAIL emp {emp.pk} — left as-is")
        self.stdout.write(self.style.SUCCESS(
            f"encrypted+verified {done}, verify-fail {fail}; plaintext left: "
            f"national_id={_plain('national_id')} bank_account_no={_plain('bank_account_no')}"))
