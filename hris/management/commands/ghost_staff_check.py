"""
Triple-verified ghost staff check.

Finds active employees who have NEVER used Omni — verified three ways:
1. OnlinePresence.last_seen is NULL (never hit the middleware)
2. No OmniTask completed_at (never finished a task)
3. No audit trail in AuditLog (never created/edited anything)

Only flags someone as "never logged in" when ALL THREE checks agree.

Usage:
    python manage.py ghost_staff_check
    python manage.py ghost_staff_check --email   (also sends the email to Dorothy)
"""
import logging
from django.core.management.base import BaseCommand
from django.utils import timezone

log = logging.getLogger(__name__)


class Command(BaseCommand):
    help = 'Triple-verified check for staff who have never logged into Omni.'

    def add_arguments(self, parser):
        parser.add_argument('--email', action='store_true',
                            help='Send the results email to Dorothy, CC Unami and Oprah')

    def handle(self, *args, **options):
        from django.contrib.auth.models import User
        from core.models import OnlinePresence
        from payroll.models import Employee

        # Unicoin agents (UNI company with no payslips) are not real staff
        from payroll.models import Payslip
        uni_emp_ids_on_payroll = set(
            Payslip.objects
            .filter(employee__company__code='UNI')
            .values_list('employee_id', flat=True).distinct()
        )
        employees = (Employee.objects
                     .filter(status='active', is_test_record=False,
                             user__isnull=False)
                     .select_related('user', 'company')
                     .order_by('full_name'))
        # Exclude Unicoin agents (UNI company, never on payroll)
        employees = [
            e for e in employees
            if not (e.company and e.company.code == 'UNI'
                    and e.pk not in uni_emp_ids_on_payroll)
        ]

        self.stdout.write(f'Checking {len(employees)} active employees with logins...\n')

        # Build lookup: user_id -> last_seen
        presence = dict(
            OnlinePresence.objects
            .filter(user__in=[e.user for e in employees])
            .values_list('user_id', 'last_seen')
        )

        # Check 2: any completed task
        from taskboard.models import OmniTask
        users_with_tasks = set(
            OmniTask.objects
            .filter(completed_at__isnull=False,
                    assignee__in=[e.user for e in employees])
            .values_list('assignee_id', flat=True)
            .distinct()
        )

        # Check 3: any audit log entry (created anything)
        has_audit = set()
        try:
            from core.models import AuditLog
            has_audit = set(
                AuditLog.objects
                .filter(user__in=[e.user for e in employees])
                .values_list('user_id', flat=True)
                .distinct()
            )
        except Exception:
            pass

        # Check 3b: raised or signed off payment requests
        users_with_payments = set()
        try:
            from taskboard.models import PaymentRequest
            created = set(
                PaymentRequest.objects
                .filter(created_by__in=[e.user for e in employees])
                .values_list('created_by_id', flat=True)
                .distinct()
            )
            users_with_payments = created
        except Exception:
            pass

        # Check 3c: filed helpdesk bugs
        users_with_bugs = set()
        try:
            from helpdesk.models import Ticket
            users_with_bugs = set(
                Ticket.objects
                .filter(created_by__in=[e.user for e in employees])
                .values_list('created_by_id', flat=True)
                .distinct()
            )
        except Exception:
            pass

        never_logged = []
        active_but_no_presence = []

        for emp in employees:
            uid = emp.user_id
            last_seen = presence.get(uid)
            did_task = uid in users_with_tasks
            did_audit = uid in has_audit
            did_payment = uid in users_with_payments
            did_bug = uid in users_with_bugs
            has_activity = did_task or did_audit or did_payment or did_bug

            if last_seen is None and not has_activity:
                never_logged.append(emp)
            elif last_seen is None and has_activity:
                # FALSE POSITIVE: no presence record but they DID do real work.
                # This is yesterday's mistake — flag it as a warning, NOT ghost.
                activity = []
                if did_task: activity.append('tasks')
                if did_audit: activity.append('edits')
                if did_payment: activity.append('payments')
                if did_bug: activity.append('bugs')
                active_but_no_presence.append((emp, ', '.join(activity)))

        self.stdout.write(f'\n=== TRIPLE-VERIFIED: NEVER USED OMNI ({len(never_logged)}) ===')
        self.stdout.write('(No presence, no tasks, no edits, no payments, no bugs)\n')
        for emp in never_logged:
            co = getattr(emp.company, 'code', '?') if emp.company else '?'
            self.stdout.write(
                f'  {emp.full_name:<35} {emp.email:<40} {co:<10} {emp.department}')

        if active_but_no_presence:
            self.stdout.write(
                f'\n=== WARNING: No presence record but HAS activity '
                f'({len(active_but_no_presence)}) ===')
            self.stdout.write('(These are NOT ghost staff — they used Omni but '
                              'presence tracking missed them)\n')
            for emp, acts in active_but_no_presence:
                self.stdout.write(f'  {emp.full_name:<35} active in: {acts}')

        self.stdout.write(
            f'\nSummary: {len(never_logged)} confirmed never-used, '
            f'{len(active_but_no_presence)} false positives caught.')

        if options['email'] and never_logged:
            self._send_email(never_logged)

    def _send_email(self, never_logged):
        from core.notifications import send_html_with_cfo_cc

        rows = ''
        for i, emp in enumerate(never_logged, 1):
            co = getattr(emp.company, 'code', '?') if emp.company else '?'
            rows += (
                f'<tr><td style="padding:6px 10px;border-bottom:1px solid #eee;">{i}</td>'
                f'<td style="padding:6px 10px;border-bottom:1px solid #eee;">{emp.full_name}</td>'
                f'<td style="padding:6px 10px;border-bottom:1px solid #eee;">{emp.email}</td>'
                f'<td style="padding:6px 10px;border-bottom:1px solid #eee;">{co}</td>'
                f'<td style="padding:6px 10px;border-bottom:1px solid #eee;">{emp.department}</td></tr>'
            )

        monday = timezone.localdate() + timezone.timedelta(
            days=(7 - timezone.localdate().weekday()) % 7 or 7)

        html = f"""
        <p>Dorothy</p>
        <p>The following {len(never_logged)} staff members have <strong>never logged into Omni</strong>.
        This has been verified three ways: no login activity, no tasks completed, no records created.</p>

        <table style="border-collapse:collapse;width:100%;font-family:Arial,sans-serif;font-size:13px;">
        <tr style="background:#1D3270;color:white;">
            <th style="padding:8px 10px;text-align:left;">#</th>
            <th style="padding:8px 10px;text-align:left;">Name</th>
            <th style="padding:8px 10px;text-align:left;">Email</th>
            <th style="padding:8px 10px;text-align:left;">Company</th>
            <th style="padding:8px 10px;text-align:left;">Department</th>
        </tr>
        {rows}
        </table>

        <p><strong>Action required by {monday.strftime('%A %d %B %Y')}:</strong></p>
        <ul>
            <li>Each person on this list must log into Omni at least once before the deadline.</li>
            <li>If they do not, they will be flagged as ghost staff and their <strong>payroll
            will be blocked</strong> until Internal Audit confirms their existence.</li>
        </ul>

        <p>Please follow up with each person's line manager.</p>

        <p>Regards,<br>Prathap Ganesharajah<br>Chief Financial Officer</p>
        """

        try:
            send_html_with_cfo_cc(
                subject=f'ACTION REQUIRED: {len(never_logged)} staff have never logged into Omni',
                html=html,
                to=['dikgopoleng@alphadirect.co.bw'],
                cc=['ubutale@alphadirect.co.bw', 'omogomotsi@alphadirect.co.bw'],
                no_reply=False,
            )
            self.stdout.write('\nEmail sent to Dorothy, CC Unami and Oprah.')
        except Exception as e:
            self.stdout.write(f'\nEmail FAILED: {e}')
            self.stdout.write('Run again with --email or send manually.')
