"""Create/normalise the ONE app-store reviewer identity (CFO 2026-09-06).

Apple and Google reviewers sign in with this account to browse the Omni staff
phone app. It is deliberately powerless: active so it can sign in, NOT staff,
NOT superuser, and named in core.screenshot_bot.READ_ONLY_USERNAMES so the auth
layer refuses it any write. What it actually sees is invented demo data, served
by core.review_demo.ReviewDemoMiddleware.

Email and password come from the environment (OMNI_REVIEW_EMAIL /
OMNI_REVIEW_PASSWORD) — never from code. Run it after every deploy that changes
those values; it is idempotent.
"""
from __future__ import annotations

import os

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError

from core.review_demo import REVIEW_USERNAME


class Command(BaseCommand):
    help = 'Create/normalise the locked app-store reviewer account from the environment.'

    def handle(self, *args, **opts):
        email = str(getattr(settings, 'OMNI_REVIEW_EMAIL', '') or '').strip().lower()
        password = os.environ.get('OMNI_REVIEW_PASSWORD', '')
        if not email:
            raise CommandError('OMNI_REVIEW_EMAIL is not set — nothing to create.')
        if not password:
            raise CommandError('OMNI_REVIEW_PASSWORD is not set — refusing to create '
                               'an account without a password.')

        U = get_user_model()
        user, created = U.objects.get_or_create(
            username=REVIEW_USERNAME,
            defaults={'email': email, 'is_staff': False,
                      'is_superuser': False, 'is_active': True},
        )
        changed = []
        if user.email != email:
            user.email = email; changed.append('email')
        if not user.is_active:
            user.is_active = True; changed.append('is_active')
        if user.is_staff:
            user.is_staff = False; changed.append('is_staff')
        if user.is_superuser:
            user.is_superuser = False; changed.append('is_superuser')
        # Always reset the password: this is how the CFO rotates it for a
        # resubmission, and it is the only way in.
        user.set_password(password)
        changed.append('password')
        user.save()

        self.stdout.write(
            f"{'created' if created else 'updated'} {REVIEW_USERNAME} <{email}> "
            f"({', '.join(changed)})")
        if not getattr(settings, 'OMNI_REVIEW_MODE', False):
            self.stdout.write(self.style.WARNING(
                'OMNI_REVIEW_MODE is OFF — the account exists but cannot sign in '
                'and would see no demo data. Set it to True to arm review mode.'))
