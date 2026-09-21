from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from django.utils.html import escape

from core.models import AuditLog
from core.notifications import send_html_with_cfo_cc
from hris.hr_settings import get_setting
from hris.joiner_pack import documents_status, house_email, start_monthly_reviews
from hris.models import EmployeeAcknowledgement, HRISProfile
from payroll.models import Employee


class Command(BaseCommand):
    help = 'Send new joiner reminders for overdue sign-offs and HR missing documents.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--commit',
            action='store_true',
            default=False,
            help='Actually send emails and write audit logs.',
        )

    def handle(self, *args, **options):
        commit = options['commit']
        today = timezone.localdate()

        if not commit:
            self.stdout.write('DRY RUN: no emails will be sent or records written.')
        else:
            self.stdout.write('Commit mode: emails will be sent and records written.')

        if True:  # idempotent per period — safe every run (a weekend 1st must not skip a month)
            if commit:
                created = start_monthly_reviews(today)
                self.stdout.write(f'Created {created} monthly review acknowledgement(s).')
            else:
                self.stdout.write(
                    'DRY RUN: would run start_monthly_reviews for monthly review acknowledgements.'
                )

        self._send_ack_reminders(today, commit)
        self._send_missing_documents(today, commit)

    def _recently_sent(self, email):
        cutoff = timezone.now() - timedelta(days=3)
        last = AuditLog.objects.filter(
            record_id=email,
            description__startswith='Joiner reminder',
        ).order_by('-pk').first()

        if not last:
            return False
        if hasattr(last, 'created_at') and last.created_at is not None:
            return last.created_at >= cutoff

        sent_at = (last.new_values or {}).get('sent_at')
        if sent_at:
            parsed = parse_datetime(sent_at)
            if parsed:
                return parsed >= cutoff
        return True

    def _send_ack_reminders(self, today, commit):
        acks = EmployeeAcknowledgement.objects.filter(
            due_date__isnull=False,
            due_date__lt=today,
        ).select_related('employee__hris_profile__manager')

        persons = {}
        for ack in acks:
            employee = ack.employee
            due = ack.due_date.strftime('%d %b %Y') if ack.due_date else ''

            if ack.employee_signed_at is None and employee.email:
                entry = persons.setdefault(employee.email, {
                    'name': employee.full_name,
                    'acks': [],
                })
                entry['acks'].append(f'{ack.title} (due {due})')

            if ack.needs_manager and ack.manager_signed_at is None:
                manager = None
                try:
                    manager = employee.hris_profile.manager
                except HRISProfile.DoesNotExist:
                    manager = None

                if manager and manager.email:
                    entry = persons.setdefault(manager.email, {
                        'name': manager.full_name,
                        'acks': [],
                    })
                    entry['acks'].append(
                        f'{ack.title} for {employee.full_name} (due {due})'
                    )

        sent = 0
        for email, data in persons.items():
            if self._recently_sent(email):
                if not commit:
                    self.stdout.write(
                        f'DRY RUN: would skip {email} (reminder sent in the last 3 days).'
                    )
                continue

            subject = 'Your overdue sign-offs in Omni'
            body_html = (
                f'<p>Dear {escape(data["name"])},</p>'
                '<p>The following sign-offs are overdue in Omni:</p>'
                '<ul>'
                + ''.join(f'<li>{escape(ack_text)}</li>' for ack_text in data['acks'])
                + '</ul>'
                '<p>Please sign them in Omni at your earliest convenience.</p>'
            )

            if commit:
                sent_count = send_html_with_cfo_cc(
                    subject=subject,
                    html=house_email('Overdue sign-offs', body_html),
                    to=[email],
                    cc_cfo=False,
                    allow_named_exec=True,
                )
                if sent_count:
                    AuditLog.objects.create(
                        table_name='hris_employeeacknowledgement',
                        record_id=email,
                        action='read',
                        old_values={},
                        new_values={'sent_at': timezone.now().isoformat()},
                        user=None,
                        description=f'Joiner reminder sent to {email}',
                    )
                    self.stdout.write(f'Sent reminder to {email}')
                    sent += 1
            else:
                self.stdout.write(
                    f'DRY RUN: would send reminder to {email} about '
                    f'{len(data["acks"])} overdue sign-off(s).'
                )

        if commit:
            self.stdout.write(f'Acknowledgement reminders: {sent} sent.')
        else:
            self.stdout.write('DRY RUN complete for acknowledgement reminders.')

    def _send_missing_documents(self, today, commit):
        cutoff = today - timedelta(days=60)
        joiners = Employee.objects.filter(
            status='active',
            hire_date__gte=cutoff,
            hire_date__lte=today,
        ).select_related('company')

        rows = []
        for employee in joiners:
            missing = [doc['label'] for doc in documents_status(employee) if not doc['present']]
            if missing:
                rows.append((employee.full_name, employee.department or '', ', '.join(missing)))

        if not rows:
            self.stdout.write('No missing documents for recent joiners.')
            return

        recipients = get_setting('contract_reminder_recipients', [])
        if isinstance(recipients, str):
            recipients = [recipients]
        recipients = [r for r in (recipients or []) if r]

        if not recipients:
            self.stdout.write('No HR recipients configured; skipping missing documents email.')
            return

        subject = 'Missing new joiner documents'
        body_html = (
            '<p>The following recent joiners have missing documents:</p><ul>'
            + ''.join(
                f'<li>{escape(name)} ({escape(dept or "no department")}) — {escape(labels)}</li>'
                for name, dept, labels in rows
            )
            + '</ul>'
        )

        if commit:
            send_html_with_cfo_cc(
                subject=subject,
                html=house_email('Missing documents', body_html),
                to=recipients,
            )
            self.stdout.write(
                f'Sent missing documents email to {len(recipients)} HR recipient(s) for {len(rows)} joiners.'
            )
        else:
            self.stdout.write(
                f'DRY RUN: would send missing documents email to {len(recipients)} HR '
                f'recipient(s) for {len(rows)} joiners.'
            )
