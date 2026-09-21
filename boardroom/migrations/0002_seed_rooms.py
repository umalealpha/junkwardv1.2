"""Seed the real Alpha Direct meeting rooms.

Done as a migration (not loaddata) so a deploy can never land with an empty
room list — which would leave the board with nothing to book. Idempotent:
existing rooms keep whatever facilities have since edited (floor, seats, kit,
active); only missing rooms are added.

Seats / floor / kit are left blank for facilities to fill in — only the names
and the two prominent rooms (Exco + Staff board rooms) are confirmed.
"""

from django.db import migrations

# (name, is_prominent, sort_order)
ROOMS = [
    ('Exco Board Room', True, 10),
    ('Staff Board Room', True, 20),
    ('Chrch', False, 30),
    ('Exco Balcony', False, 40),
    ('Client Meeting Room 1', False, 50),
    ('Client Meeting Room 2', False, 60),
]


def seed(apps, schema_editor):
    Room = apps.get_model('boardroom', 'Room')
    for name, prominent, order in ROOMS:
        Room.objects.get_or_create(
            name=name,
            defaults={'is_prominent': prominent, 'sort_order': order, 'is_active': True},
        )


def unseed(apps, schema_editor):
    """Remove only seeded rooms that were never booked."""
    Room = apps.get_model('boardroom', 'Room')
    (Room.objects
     .filter(name__in=[n for n, *_ in ROOMS], bookings__isnull=True)
     .delete())


class Migration(migrations.Migration):

    dependencies = [
        ('boardroom', '0001_initial'),
    ]

    operations = [
        migrations.RunPython(seed, unseed),
    ]
