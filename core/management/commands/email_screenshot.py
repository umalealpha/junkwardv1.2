"""Email a screenshot file to recipient(s) as an attachment, via omni's own
configured email backend (same path the daily briefs use)."""
import mimetypes
import os

from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = 'Email a screenshot file as an attachment.'

    def add_arguments(self, parser):
        parser.add_argument('--file', required=True, help='Path to the image file.')
        parser.add_argument('--to', required=True, help='Recipient(s), comma-separated.')
        parser.add_argument('--subject', default='omni — feature screenshot')
        parser.add_argument('--body', default='Automated omni screenshot attached.')

    def handle(self, *args, **opts):
        path = opts['file']
        if not os.path.exists(path):
            raise CommandError(f'file not found: {path}')
        to = [a.strip() for a in opts['to'].split(',') if a.strip()]
        frm = (getattr(settings, 'OMNI_FROM_EMAIL', '')
               or getattr(settings, 'DEFAULT_FROM_EMAIL', 'Omni ERP <omni@alphadirect.co.bw>'))
        msg = EmailMultiAlternatives(subject=opts['subject'], body=opts['body'],
                                     from_email=frm, to=to)
        with open(path, 'rb') as f:
            data = f.read()
        ctype = mimetypes.guess_type(path)[0] or 'image/png'
        msg.attach(os.path.basename(path), data, ctype)
        sent = msg.send()
        self.stdout.write(self.style.SUCCESS(
            f'emailed {os.path.basename(path)} ({len(data)} bytes) to {", ".join(to)}: send()={sent}'))
