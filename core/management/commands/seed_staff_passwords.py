"""Seed the default staff email-login password (Omni123) for every active
Alpha Direct M365 user, creating a Django user where one is missing.

NEVER overwrites a password a user has already changed — only sets the
default for users who have no usable password or are still on the default.
CFO directive 2026-07-07.

    python manage.py seed_staff_passwords --dry-run
    python manage.py seed_staff_passwords
"""
from __future__ import annotations

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.db import IntegrityError

from core.staff_login_views import _is_human_ad_email
from licensing.models import M365ActiveUser

User = get_user_model()
DEFAULT_PASSWORD = 'Omni123'


class Command(BaseCommand):
    help = 'Seed Omni123 default password for all active Alpha Direct M365 users.'

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true')

    def handle(self, *args, **opts):
        dry = opts['dry_run']
        created = seeded = skipped = bad = nonhuman = 0
        for m in M365ActiveUser.objects.all():
            email = (m.email or m.user_principal_name or '').strip().lower()
            if not email or '@' not in email:
                bad += 1
                continue
            # Individual humans only — never shared/forwarding/service boxes.
            if not _is_human_ad_email(email):
                nonhuman += 1
                continue
            user = User.objects.filter(email__iexact=email).order_by('id').first()
            if user is None:
                if dry:
                    created += 1
                    continue
                username = email[:150]
                if User.objects.filter(username=username).exists():
                    username = f'{email.split("@")[0]}_{m.object_id[:6]}'[:150]
                try:
                    user = User.objects.create(
                        username=username, email=email, is_active=True,
                        first_name=(m.display_name or '').split(' ')[0][:150])
                    user.set_password(DEFAULT_PASSWORD)
                    user.save()
                    created += 1
                except IntegrityError:
                    bad += 1
                continue
            # Existing user: only (re)set the default if they have NO usable
            # password or are STILL on the default — never clobber a changed one.
            if user.has_usable_password() and not user.check_password(DEFAULT_PASSWORD):
                skipped += 1
                continue
            if dry:
                seeded += 1
                continue
            user.set_password(DEFAULT_PASSWORD)
            user.save(update_fields=['password'])
            seeded += 1

        # Second pass: active group-domain humans who already have a Django
        # account but are NOT in the AD M365 tenant (e.g. some theriskco.com /
        # motorliquidators.co.bw staff) — give them a working default too.
        extra = 0
        for u in User.objects.filter(is_active=True).exclude(email=''):
            if not _is_human_ad_email(u.email):
                continue
            if u.has_usable_password() and not u.check_password(DEFAULT_PASSWORD):
                continue
            if dry:
                extra += 1
                continue
            u.set_password(DEFAULT_PASSWORD)
            u.save(update_fields=['password'])
            extra += 1

        self.stdout.write(self.style.SUCCESS(
            f'created={created} seeded_default={seeded} extra_group={extra} '
            f'skipped_already_changed={skipped} non_human={nonhuman} '
            f'bad_email={bad} dry_run={dry}'))
