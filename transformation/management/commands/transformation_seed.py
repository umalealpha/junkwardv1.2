"""Seed the transformation path.

This command exists because a `seed()` function with no caller ships an empty
feature: every test passes, the deploy is green, and the CFO opens the board to
a blank screen. The entrypoint runs this on every start, and re-running is
safe — it refreshes wording, money and dates and never resets progress.
"""
from __future__ import annotations

from django.core.management.base import BaseCommand

from transformation.seed import seed


class Command(BaseCommand):
    help = 'Create or refresh the four-month transformation path.'

    def handle(self, *args, **opts):
        result = seed()
        self.stdout.write(
            f"transformation: {result['initiatives_created']} step(s) created, "
            f"{result['initiatives_updated']} refreshed; "
            f"{result['plans_created']} department plan(s) created, "
            f"{result['plans_updated']} refreshed."
        )
