"""Chase the signatories on Authorities to Recruit that are still unsigned.

CFO directive 2026-08-11. Raising an authority now emails the signatories, but a
raise-time email is a single event — the two records live on prod when this was
written had been pending for days with nobody chased. This is the recurring half.

    python manage.py authority_signature_reminders            # send
    python manage.py authority_signature_reminders --dry-run   # list, send nothing

Runs daily from cron. Only PENDING authorities are chased, only those at least
`--min-age-days` old (default 2, so it never doubles up on the raise email), and
only the signatories who still owe a decision.
"""
from __future__ import annotations

from django.core.management.base import BaseCommand
from django.utils import timezone

from recruitment import authority_notify
from recruitment.models import AuthorityToRecruit


class Command(BaseCommand):
    help = 'Email the outstanding signatories on unsigned Authorities to Recruit.'

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true',
                            help='List what would be chased and send nothing.')
        parser.add_argument('--min-age-days', type=int, default=2,
                            help='Only chase authorities older than this (default 2).')

    def handle(self, *args, **opts):
        dry = opts['dry_run']
        min_age = max(0, int(opts['min_age_days']))
        cutoff = timezone.now() - timezone.timedelta(days=min_age)

        qs = (AuthorityToRecruit.objects
              .filter(status=AuthorityToRecruit.Status.PENDING,
                      created_at__lte=cutoff)
              .order_by('created_at'))

        chased = skipped = sent_total = 0
        for a in qs:
            to = authority_notify.outstanding_recipients(a)
            if not to:
                # PENDING with nothing outstanding would be a status bug; say so
                # rather than silently counting it as done.
                self.stdout.write(self.style.WARNING(
                    f'  {a.reference}: pending but no outstanding signatory — check status'))
                skipped += 1
                continue
            age = (timezone.now() - a.created_at).days
            self.stdout.write(f'  {a.reference}  {a.person_name[:28]:28}  '
                              f'{age:3}d  {len(to)} to chase')
            if dry:
                skipped += 1
                continue
            sent_total += authority_notify.notify_outstanding(
                a, lead=(f'This authority has been waiting {age} day(s) for signatures. '
                         'An offer cannot go out until all five signatories are in.'))
            chased += 1

        verb = 'would chase' if dry else 'chased'
        self.stdout.write(self.style.SUCCESS(
            f'{verb} {chased if not dry else skipped} authority(ies); '
            f'emails sent={sent_total}'))
