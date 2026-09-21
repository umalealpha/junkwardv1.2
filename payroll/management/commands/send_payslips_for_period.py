"""
python manage.py send_payslips_for_period --period 2026-05

Email every employee their personal payslip for the named period.  Uses
the existing Microsoft Graph email backend.  Skips drafts.  Per Unami's
wishlist (Fw: Omni, 2026-06-02): "Send Batch Payslips to employees."

Options:
  --period 2026-05      period_name to send (required)
  --status approved     payslip status filter (default 'approved')
  --dry-run             do not send; log who would receive
  --limit N             cap on number of emails (testing)
"""
from __future__ import annotations

from django.core.management.base import BaseCommand
from django.core.mail import EmailMultiAlternatives

from payroll.models import PayrollPeriod, Payslip
from payroll.pdf import generate_payslip_pdf


SUBJECT_TMPL = 'Your payslip — {period}'
FROM_EMAIL   = 'Omni ERP <omni@alphadirect.co.bw>'


class Command(BaseCommand):
    help = 'Email batch payslips to employees for a payroll period.'

    def add_arguments(self, parser):
        parser.add_argument('--period', required=True,
                            help='period_name e.g. 2026-05')
        parser.add_argument('--status', default='approved',
                            help='Payslip status filter (default: approved)')
        parser.add_argument('--dry-run', action='store_true')
        parser.add_argument('--limit', type=int, default=0,
                            help='Max number of emails to send (0=all)')

    def handle(self, *args, **opts):
        try:
            period = PayrollPeriod.objects.get(period_name=opts['period'])
        except PayrollPeriod.DoesNotExist:
            self.stderr.write(self.style.ERROR(
                f'Period {opts["period"]!r} not found'))
            return

        qs = (Payslip.objects
              .filter(period=period, status=opts['status'])
              .select_related('employee'))
        if opts['limit']:
            qs = qs[:opts['limit']]

        sent, skipped, failed = 0, 0, 0
        for ps in qs:
            emp = ps.employee
            if not emp.email:
                skipped += 1
                self.stdout.write(f'skip no_email {emp.full_name}')
                continue

            if opts['dry_run']:
                sent += 1
                self.stdout.write(f'dry-run → {emp.email} ({emp.full_name})')
                continue

            try:
                pdf = generate_payslip_pdf(ps)
            except Exception as e:
                failed += 1
                self.stderr.write(f'pdf_error {emp.email}: {e!r}')
                continue

            subject = SUBJECT_TMPL.format(period=period.period_name)
            text = (
                f'Hi {emp.full_name.split()[0]},\n\n'
                f'Your payslip for the {period.period_name} payroll period is '
                f'attached as a PDF.\n\n'
                f'For any payroll queries, reply to this email or contact the '
                f'Human Capital team.\n\n'
                f'— Alpha Direct Payroll (via Omni)\n'
            )
            html = text.replace('\n', '<br/>')

            try:
                msg = EmailMultiAlternatives(subject, text, FROM_EMAIL, [emp.email])
                msg.attach_alternative(html, 'text/html')
                msg.attach(
                    f'payslip-{emp.employee_number}-{period.period_name}.pdf',
                    pdf, 'application/pdf',
                )
                msg.send()
                sent += 1
            except Exception as e:
                failed += 1
                self.stderr.write(f'send_error {emp.email}: {e!r}')

        self.stdout.write(self.style.SUCCESS(
            f'send_payslips period={period.period_name} sent={sent} '
            f'skipped={skipped} failed={failed}'
        ))
