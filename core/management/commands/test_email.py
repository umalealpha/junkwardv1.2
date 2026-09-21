"""
test_email — fire a real send_mail() via configured backend.

Verifies SMTP credentials once IT lands EMAIL_HOST_PASSWORD in
/etc/alpha-finance/.env. Prints the backend + host so the console-vs-SMTP
fall-through is visible.

Usage:
    python manage.py test_email --to pganesharajah@alphadirect.co.bw
    python manage.py test_email --to pganesharajah@alphadirect.co.bw --subject "smtp probe"
"""

from __future__ import annotations

from django.conf import settings
from django.core.mail import EmailMessage
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = 'Send a test email through the configured outbound backend.'

    def add_arguments(self, parser):
        parser.add_argument('--to', required=True, help='Recipient address.')
        parser.add_argument('--subject', default='Omni SMTP probe',
                            help='Subject line (default: "Omni SMTP probe").')
        parser.add_argument('--body', default='',
                            help='Body text (default: backend + host fingerprint).')

    def handle(self, *args, **opts):
        to       = opts['to']
        subject  = opts['subject']
        body     = opts['body'] or (
            f"Omni ERP outbound test\n\n"
            f"Backend  = {settings.EMAIL_BACKEND}\n"
            f"Host     = {settings.EMAIL_HOST}:{settings.EMAIL_PORT}\n"
            f"TLS      = {settings.EMAIL_USE_TLS}\n"
            f"From     = {settings.DEFAULT_FROM_EMAIL}\n"
            f"User     = {settings.EMAIL_HOST_USER}\n"
        )

        self.stdout.write(self.style.MIGRATE_HEADING(
            f"Sending via {settings.EMAIL_BACKEND}"
        ))
        msg = EmailMessage(
            subject=subject,
            body=body,
            from_email=settings.DEFAULT_FROM_EMAIL,
            to=[to],
        )
        sent = msg.send(fail_silently=False)
        if sent:
            self.stdout.write(self.style.SUCCESS(f'OK — {sent} message sent to {to}'))
        else:
            raise SystemExit('send() returned 0 — investigate SMTP creds / network')
