"""core/screen_view_models.py — screen-usage telemetry (CFO 2026-09-03).

"Who opens which screen, per minute, staff only, no content." One row per
(user, screen family, minute). The frontend beacon posts the path; the server
collapses record ids to ``:id`` so the table counts screens, never records.
Rows older than 180 days are pruned by ``prune_screen_views``.
"""
from django.conf import settings
from django.db import models


class ScreenView(models.Model):
    class Surface(models.TextChoices):
        APP     = 'app',     'Phone app'
        NEXUS   = 'm',       'Nexus'
        DESKTOP = 'desktop', 'Desktop'

    user    = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
                                related_name='screen_views')
    # Path family, e.g. /payment-requests/:id/edit — never a query string.
    screen  = models.CharField(max_length=200)
    surface = models.CharField(max_length=10, choices=Surface.choices)
    # Truncated to the minute; the unique key makes a double post a no-op.
    minute  = models.DateTimeField(db_index=True)

    class Meta:
        unique_together = ('user', 'screen', 'minute')
        verbose_name = 'Screen view'
        verbose_name_plural = 'Screen views'

    def __str__(self):
        return f'{self.user_id} {self.surface}:{self.screen} @ {self.minute:%Y-%m-%d %H:%M}'
