"""Seed the three frozen components Internal Audit named (idempotent)."""
from django.core.management.base import BaseCommand

from core.models import FrozenComponent
from core.frozen_controls import FROZEN_COMPONENTS


class Command(BaseCommand):
    help = "Seed/refresh the frozen-component registry (ADIC MA P&L layout, revenue mapping, GWP figure)."

    def handle(self, *args, **opts):
        made = 0
        for spec in FROZEN_COMPONENTS:
            obj, created = FrozenComponent.objects.update_or_create(
                key=spec['key'],
                defaults={'label': spec['label'], 'description': spec['description'],
                          'is_frozen': True},
            )
            made += 1 if created else 0
            self.stdout.write(f"  {'created' if created else 'exists '} · {obj.label}")
        self.stdout.write(self.style.SUCCESS(
            f"Frozen components seeded ({FrozenComponent.objects.count()} total, {made} new)."))
