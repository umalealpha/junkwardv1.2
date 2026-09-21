"""
Management command: poll_fnb_notifications

Pulls FNB's new-notifications feed once and persists every item to
FNBWebhookEvent. Designed to be run every minute by cron.

Usage:
    python manage.py poll_fnb_notifications
    python manage.py poll_fnb_notifications --since-minutes 15  # back-fill
"""
from __future__ import annotations

from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from fnb.notifications import poll_new_notifications, poll_filtered


class Command(BaseCommand):
    help = 'Poll FNB Notification Execution API and persist new events.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--since-minutes', type=int, default=0,
            help='If > 0, pull notifications from N minutes ago instead of '
                 'using the "new" feed (useful for back-filling after a '
                 'cron outage).',
        )

    def handle(self, *args, **opts):
        minutes = opts.get('since_minutes') or 0
        if minutes:
            to_dt = timezone.now()
            from_dt = to_dt - timedelta(minutes=minutes)
            count = poll_filtered(from_dt=from_dt, to_dt=to_dt)
            self.stdout.write(self.style.SUCCESS(
                f'FNB filtered poll {from_dt:%H:%M}..{to_dt:%H:%M}: {count} new'
            ))
        else:
            count = poll_new_notifications()
            self.stdout.write(self.style.SUCCESS(
                f'FNB new-notifications poll: {count} new'
            ))
