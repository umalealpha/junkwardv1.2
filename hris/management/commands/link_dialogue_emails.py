"""Attach each Development Dialogue to its owner's work email so the person
can open ONLY their own review (self-service). Idempotent; only touches the
`email` column — never the review payload/edits.

    python manage.py link_dialogue_emails
"""
from __future__ import annotations

from django.core.management.base import BaseCommand

from hris.models import DevelopmentDialogue

# name (as stored on the dialogue) -> work email
NAME_EMAIL = {
    'Prathap Ganesharajah': 'pganesharajah@alphadirect.co.bw',
    'Kago Tshutlhedi':       'ktshutlhedi@alphadirect.co.bw',
    'Pako Kago':             'pkago@alphadirect.co.bw',
    'Legakwa Ntabeni':       'lntabeni@alphadirect.co.bw',
    'Keetile Mokhendo':      'kmokhendo@alphadirect.co.bw',
    'Bokani Makosha':        'bmakosha@alphadirect.co.bw',
    'Laone Moteane':         'lthebe@alphadirect.co.bw',  # Laone Angela Thebe
}


class Command(BaseCommand):
    help = "Set each DevelopmentDialogue.email from its owner name (self-service linking)."

    def handle(self, *args, **opts):
        done = missing = 0
        for row in DevelopmentDialogue.objects.all():
            email = NAME_EMAIL.get((row.name or '').strip())
            if email and row.email != email:
                row.email = email
                row.save(update_fields=['email', 'updated_at'])
                done += 1
            elif not email:
                missing += 1
        self.stdout.write(self.style.SUCCESS(
            f'Linked {done} dialogue(s) to an email; {missing} had no email mapping.'))
