"""boardroom/models.py — Alpha Rooms meeting-room booking.

The shared successor to the standalone Alpha Rooms PWA (React/Vite, localStorage
only). Rooms and bookings live in Omni so every tablet, laptop and phone sees the
same board, and the booker is the signed-in Omni user — no more free-text names.

Entity posture: meeting rooms are PHYSICAL and shared across the whole business,
so this module is deliberately company-wide. Room/Booking carry no `company` FK
and the viewsets do NOT inherit CompanyScopedViewSetMixin (mirrors the
`assets.AssetCategoryViewSet` precedent). This does not weaken the entity walls
elsewhere — isolation is per-viewset.
"""

from __future__ import annotations

from django.contrib.auth.models import User
from django.db import models

from core.models import BaseModel

# Booking day window — mirrors the standalone app's lib/time.ts (07:00–19:00).
DAY_START_MIN = 7 * 60
DAY_END_MIN = 19 * 60


class Room(BaseModel):
    """A physical meeting room. Company-wide (one building), not entity-scoped."""

    name = models.CharField(max_length=120, unique=True)
    floor = models.CharField(max_length=120, blank=True, default='')
    seats = models.PositiveIntegerField(
        null=True, blank=True,
        help_text='Number of seats. Blank until facilities confirm it.',
    )
    kit = models.CharField(
        max_length=255, blank=True, default='',
        help_text='Comma-separated equipment, e.g. "Screen, Speakerphone, Camera".',
    )
    is_prominent = models.BooleanField(
        default=False,
        help_text='Show first on the board — the Exco and Staff board rooms.',
    )
    is_active = models.BooleanField(default=True)
    sort_order = models.PositiveIntegerField(
        default=100, help_text='Lower shows first (after the prominent rooms).',
    )

    class Meta:
        ordering = ['-is_prominent', 'sort_order', 'name']

    def __str__(self):
        return self.name


class Booking(BaseModel):
    """One meeting in one room on one day.

    start_min / end_min are minutes since midnight (540 = 09:00), matching the
    standalone app's shape so the front-end grid maths is unchanged.
    """

    room = models.ForeignKey(
        Room, on_delete=models.PROTECT, related_name='bookings',
    )
    day = models.DateField()
    start_min = models.PositiveIntegerField(
        help_text='Minutes since midnight, e.g. 540 = 09:00.',
    )
    end_min = models.PositiveIntegerField()
    title = models.CharField(max_length=200)
    attendees = models.PositiveIntegerField(default=1)
    # The signed-in Omni user who made the booking. Kept nullable so a booking
    # survives if the user is later removed; booked_by_name preserves the label.
    booked_by = models.ForeignKey(
        User, null=True, blank=True, on_delete=models.SET_NULL,
        related_name='room_bookings',
    )
    booked_by_name = models.CharField(
        max_length=150, blank=True, default='',
        help_text="Booker's display name captured at booking time.",
    )

    class Meta:
        ordering = ['day', 'start_min']
        indexes = [
            models.Index(fields=['room', 'day']),
            models.Index(fields=['day']),
        ]

    def __str__(self):
        return f'{self.title} — {self.room.name} {self.day} {self.start_min}'

    @classmethod
    def find_clash(cls, *, room, day, start_min, end_min, exclude_id=None):
        """First booking that overlaps [start_min, end_min) in the same room/day,
        or None. Half-open overlap: start < other.end AND other.start < end."""
        qs = cls.objects.filter(
            room=room, day=day, start_min__lt=end_min, end_min__gt=start_min,
        )
        if exclude_id is not None:
            qs = qs.exclude(pk=exclude_id)
        return qs.select_related('room').first()
