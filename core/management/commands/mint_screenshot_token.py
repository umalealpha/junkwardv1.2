"""Mint a FRESH read-only screenshot-bot token and print ONLY the key on stdout.

Old tokens for the bot are deleted first, so the minted key is always well
within the 15h expiry window. Used by ops/omni_screenshot.py just before a
capture; the orchestration deletes it again afterwards.
"""
from django.core.management.base import BaseCommand
from rest_framework.authtoken.models import Token

from core.screenshot_bot import get_or_create_bot


class Command(BaseCommand):
    help = 'Mint a fresh read-only screenshot-bot DRF token (prints the key).'

    def add_arguments(self, parser):
        parser.add_argument('--revoke', action='store_true',
                            help='Delete all screenshot-bot tokens and print nothing.')

    def handle(self, *args, **opts):
        bot, _ = get_or_create_bot()
        Token.objects.filter(user=bot).delete()
        if opts.get('revoke'):
            self.stdout.write('revoked')
            return
        token = Token.objects.create(user=bot)
        # Print ONLY the key so the caller can capture it cleanly.
        self.stdout.write(token.key)
