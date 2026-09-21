"""
email_morning_report — email a morning-check Markdown report.

Pairs with .github/workflows/morning-check.yml. Once IT lands
EMAIL_HOST_PASSWORD on /etc/alpha-finance/.env (omni mailbox), the
Action runs this command via SSH; the report drops into the CFO inbox
at 05:05 SAST instead of just sitting in the repo.

Falls back to console backend (Django default) when no SMTP creds —
safe to call from anywhere, never throws on missing config.

Usage:
    python manage.py email_morning_report \
        --to excoboard@alphadirect.co.bw \
        --report-path /tmp/morning-check.md
"""

from __future__ import annotations

import os
from pathlib import Path

from django.conf import settings
from django.core.mail import EmailMessage
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = 'Email a morning-check Markdown report.'

    def add_arguments(self, parser):
        parser.add_argument('--to', required=True,
                            help='Recipient(s). Comma-separated for multiple.')
        parser.add_argument('--report-path', required=True,
                            help='Path to the Markdown report (e.g. ops/morning-checks/2026-05-22.md).')
        parser.add_argument('--subject', default='',
                            help='Subject. Defaults to "Omni morning check — <filename>".')
        parser.add_argument('--cc', default='',
                            help='Optional CC addresses, comma-separated.')

    def handle(self, *args, **opts):
        path = Path(opts['report_path'])
        if not path.exists():
            raise SystemExit(f'Report not found: {path}')

        body = path.read_text()
        to_list = [a.strip() for a in opts['to'].split(',') if a.strip()]
        cc_list = [a.strip() for a in (opts['cc'] or '').split(',') if a.strip()]
        subject = opts['subject'] or f'Omni morning check — {path.stem}'

        # Surface what's about to happen — useful in CI logs.
        self.stdout.write(self.style.MIGRATE_HEADING(
            f'Sending {path.name} via {settings.EMAIL_BACKEND}'
        ))
        self.stdout.write(f'  to:      {to_list}')
        if cc_list:
            self.stdout.write(f'  cc:      {cc_list}')
        self.stdout.write(f'  subject: {subject}')
        self.stdout.write(f'  bytes:   {len(body)}')

        # Attach the raw markdown so the recipient can re-open it as a file.
        msg = EmailMessage(
            subject=subject,
            body=_strip_to_plain(body),
            from_email=settings.DEFAULT_FROM_EMAIL,
            to=to_list,
            cc=cc_list or None,
        )
        msg.attach(path.name, body, 'text/markdown')
        sent = msg.send(fail_silently=False)
        if sent:
            self.stdout.write(self.style.SUCCESS(f'OK — {sent} message sent.'))
        else:
            raise SystemExit('send() returned 0 — investigate SMTP creds / network')


def _strip_to_plain(md: str) -> str:
    """Cheap markdown → plaintext: keep headings + table layout legible in mail
    clients that don't render markdown. Pure-text fallback; the .md attachment
    carries the full source."""
    out = []
    for line in md.splitlines():
        if line.startswith('### '):
            out.append('\n' + line[4:] + '\n' + '-' * len(line[4:]))
        elif line.startswith('## '):
            out.append('\n' + line[3:].upper() + '\n')
        elif line.startswith('# '):
            out.append(line[2:].upper())
        elif line.startswith('```'):
            continue  # drop fences, keep contents
        else:
            out.append(line)
    return '\n'.join(out)
