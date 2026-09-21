"""StaffDeviceSession — the Omni staff PHONE app's sign-in (CFO 2026-09-03).

Why a separate table: the desktop DRF token dies 15h after issue
(core/token_auth.py) and that is right for a shared PC. A phone is personal,
so the app gets a 30-day session PER DEVICE that the owner can
switch off one device at a time. Only the sha256 of the token is stored — the
raw token is shown to the client exactly once at issue.

Fixed 30-day expiry (not sliding): a stolen phone is dead in 30 days even if
the thief keeps using it. Revoke = set revoked_at; the auth class
(core/device_auth.py) refuses the row from then on.
"""
from __future__ import annotations

import hashlib
import secrets
from datetime import timedelta

from django.conf import settings
from django.db import models
from django.utils import timezone

DEVICE_SESSION_DAYS = 30


class StaffDeviceSession(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
                             related_name='device_sessions')
    token_hash = models.CharField(max_length=64, unique=True, db_index=True)
    device_label = models.CharField(max_length=120, blank=True, default='')
    user_agent = models.CharField(max_length=300, blank=True, default='')
    created_at = models.DateTimeField(auto_now_add=True)
    last_seen_at = models.DateTimeField(null=True, blank=True)
    expires_at = models.DateTimeField(db_index=True)
    revoked_at = models.DateTimeField(null=True, blank=True)
    revoked_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                                   on_delete=models.SET_NULL, related_name='+')

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f'device:{self.user_id}:{self.device_label or self.token_hash[:8]}'

    @staticmethod
    def hash_token(raw: str) -> str:
        return hashlib.sha256(raw.encode('utf-8')).hexdigest()

    @classmethod
    def issue(cls, user, *, device_label: str = '', user_agent: str = ''):
        """Create a session; return (raw_token, row). Raw token is never stored."""
        raw = secrets.token_hex(32)               # 64 hex chars, no dots (never a JWT)
        row = cls.objects.create(
            user=user,
            token_hash=cls.hash_token(raw),
            device_label=(device_label or '')[:120],
            user_agent=(user_agent or '')[:300],
            expires_at=timezone.now() + timedelta(days=DEVICE_SESSION_DAYS),
        )
        return raw, row

    @property
    def is_live(self) -> bool:
        return self.revoked_at is None and self.expires_at > timezone.now()

    def revoke(self, by=None):
        if self.revoked_at is None:
            self.revoked_at = timezone.now()
            self.revoked_by = by
            self.save(update_fields=['revoked_at', 'revoked_by'])
