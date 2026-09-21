"""
devlog_add — record an ask, or move one along, from the command line.

This is how /goal, /code, /lane-b and /fabe write to the build log. It runs
server-side through the existing SSM path, so there is no new API token to
create, nothing to keep in a keychain, and nothing that could end up pasted
into a chat.

  manage.py devlog_add --key goal-2026-09-09-1 --text "his words" \
      --source goal --area devlog --who pganesharajah@alphadirect.co.bw
  manage.py devlog_add --key goal-2026-09-09-1 --status building

Idempotent on --key: the same ask logged twice is one row. That matters because
a session may run /goal and then /fabe over the same request, and two rows would
show as two outstanding jobs.
"""
from __future__ import annotations

from django.core.management.base import BaseCommand
from django.utils import timezone


class Command(BaseCommand):
    help = 'Record or update one CFO build-log item.'

    def add_arguments(self, p):
        p.add_argument('--key', required=True, help='Stable id for this ask.')
        p.add_argument('--text', default='', help="The request, in HIS words.")
        p.add_argument('--title', default='')
        p.add_argument('--status', default='')
        p.add_argument('--source', default='', help='goal / code / lane-b / fabe / deploy')
        p.add_argument('--area', default='')
        p.add_argument('--machine', default='')
        p.add_argument('--who', default='', help='Email of whoever asked.')
        p.add_argument('--who-name', default='')
        p.add_argument('--criteria', default='')
        p.add_argument('--notes', default='')

    def handle(self, *a, **o):
        from devlog.models import DevItem

        key = o['key'].strip()[:120]
        item = DevItem.objects.filter(client_key=key).first()

        if item is None:
            if not o['text'].strip():
                self.stderr.write('--text is required the first time (his words).')
                return
            item = DevItem(client_key=key, asked_at=timezone.now(),
                           asked_text=o['text'].strip()[:4000])

        for field, val in (('title', o['title'][:140]),
                           ('source', o['source'][:24]),
                           ('area', o['area'][:64]),
                           ('machine', o['machine'][:16]),
                           ('requested_by_name', o['who_name'][:120]),
                           ('success_criteria', o['criteria'][:4000]),
                           ('notes', o['notes'][:4000])):
            if val:
                setattr(item, field, val)

        if o['who'].strip():
            from django.contrib.auth.models import User
            # Match on the address only. Never on a name — crediting a request
            # to the wrong person is worse than leaving it blank.
            u = User.objects.filter(email__iexact=o['who'].strip()).first()
            if u is not None:
                item.requested_by = u
            elif not item.requested_by_name:
                item.requested_by_name = o['who'].strip()[:120]

        if o['status']:
            valid = {c for c, _ in DevItem.Status.choices}
            if o['status'] not in valid:
                self.stderr.write(f'--status must be one of {sorted(valid)}')
                return
            # LIVE is the deploy record's to give. A skill saying "live" without
            # a release behind it is the exact claim this log exists to stop.
            if o['status'] == DevItem.Status.LIVE and not item.deploy_id:
                self.stdout.write(self.style.WARNING(
                    'refusing to mark live — only a recorded deploy does that; '
                    'set waiting instead'))
                item.status = DevItem.Status.WAITING
            else:
                item.status = o['status']

        item.save()
        self.stdout.write(self.style.SUCCESS(
            f'{key} · {item.get_status_display()} · {item.title or item.asked_text[:50]}'))
