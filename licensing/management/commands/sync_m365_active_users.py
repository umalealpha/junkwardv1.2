"""Refresh the M365ActiveUser table from Microsoft Graph and email HR.

Scheduled weekly by the host cron entry under `infra/cron/` (Fri 06:00
SAST). Can also be run on demand:

    python manage.py sync_m365_active_users
    python manage.py sync_m365_active_users --no-email
    python manage.py sync_m365_active_users --cutoff-months 6 --dry-run
"""
from __future__ import annotations

import csv
import io
import logging
from datetime import timedelta
from typing import Iterable

from django.conf import settings
from django.core.mail import EmailMessage
from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from licensing.models import M365ActiveUser, M365LicenseSyncRun
from licensing.services.graph import GraphError, fetch_active_licensed_humans

logger = logging.getLogger(__name__)


def _csv_for(rows: Iterable[dict]) -> bytes:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow([
        "#", "DisplayName", "Email", "JobTitle", "Department",
        "LicenseCount", "LastInteractiveSignIn",
    ])
    for i, r in enumerate(sorted(rows, key=lambda x: (x.get("display_name") or "").lower()), 1):
        w.writerow([
            i,
            r.get("display_name", ""),
            r.get("email", "") or r.get("user_principal_name", ""),
            r.get("job_title", ""),
            r.get("department", ""),
            r.get("license_count", 0),
            (r.get("last_interactive_signin_at").isoformat()
             if r.get("last_interactive_signin_at") else ""),
        ])
    return buf.getvalue().encode("utf-8")


class Command(BaseCommand):
    help = "Refresh the M365 active-users table from Graph and email HR + Unami."

    def add_arguments(self, parser):
        parser.add_argument("--cutoff-months", type=int, default=None,
                            help="Override M365_ACTIVE_CUTOFF_MONTHS (default 6).")
        parser.add_argument("--dry-run", action="store_true",
                            help="Fetch + print counts but do not write or email.")
        parser.add_argument("--no-email", action="store_true",
                            help="Write to DB but skip the HR email.")
        parser.add_argument("--triggered-by", default="cron")

    def handle(self, *args, **opts):
        cutoff_months = opts["cutoff_months"] or getattr(
            settings, "M365_ACTIVE_CUTOFF_MONTHS", 6
        )
        cutoff = timezone.now() - timedelta(days=cutoff_months * 30)
        run = M365LicenseSyncRun.objects.create(
            cutoff_at=cutoff, triggered_by=opts["triggered_by"],
        )
        self.stdout.write(self.style.MIGRATE_HEADING(
            f"sync_m365_active_users cutoff={cutoff:%Y-%m-%d} months={cutoff_months} "
            f"dry={opts['dry_run']} email={not opts['no_email']}"
        ))
        try:
            rows, total_seen = fetch_active_licensed_humans(cutoff)
        except GraphError as e:
            run.finished_at = timezone.now()
            run.success = False
            run.error = str(e)
            run.save()
            self.stderr.write(self.style.ERROR(f"Graph fetch failed: {e}"))
            raise SystemExit(2)

        run.total_seen = total_seen
        run.active_count = len(rows)
        self.stdout.write(f"  scanned={total_seen}  active_humans={len(rows)}")

        if opts["dry_run"]:
            run.success = True
            run.finished_at = timezone.now()
            run.save()
            self.stdout.write(self.style.WARNING("DRY RUN — table not changed."))
            return

        inserted = updated = removed = 0
        with transaction.atomic():
            seen_oids = set()
            for r in rows:
                obj, created = M365ActiveUser.objects.update_or_create(
                    object_id=r["object_id"], defaults=r,
                )
                seen_oids.add(r["object_id"])
                if created:
                    inserted += 1
                else:
                    updated += 1
            # Remove rows that are no longer active.
            stale_qs = M365ActiveUser.objects.exclude(object_id__in=seen_oids)
            removed = stale_qs.count()
            stale_qs.delete()

        run.inserted = inserted
        run.updated = updated
        run.removed = removed
        run.success = True
        run.finished_at = timezone.now()
        run.save()
        self.stdout.write(self.style.SUCCESS(
            f"  inserted={inserted}  updated={updated}  removed={removed}"
        ))

        if opts["no_email"]:
            self.stdout.write(self.style.WARNING("--no-email: skipping HR notification."))
            return

        recipients = getattr(settings, "M365_NOTIFY_EMAILS", []) or []
        if not recipients:
            self.stdout.write(self.style.WARNING(
                "M365_NOTIFY_EMAILS empty — no email sent. "
                "Set it in /etc/alpha-finance/.env (comma-separated)."
            ))
            return

        body = (
            f"Weekly M365 active-user refresh\n"
            f"================================\n\n"
            f"Cutoff (interactive sign-in on/after): {cutoff:%Y-%m-%d %H:%M %Z}\n"
            f"Tenant scanned (enabled members): {total_seen}\n"
            f"Active licensed humans            : {len(rows)}\n"
            f"  new this week  : {inserted}\n"
            f"  updated        : {updated}\n"
            f"  dropped (gone) : {removed}\n\n"
            f"The current list is attached as CSV and is also visible inside omni "
            f"under Settings > M365 Active Users.\n"
        )
        msg = EmailMessage(
            subject=f"M365 active licensed users — weekly refresh ({timezone.now():%Y-%m-%d})",
            body=body,
            from_email=getattr(settings, "DEFAULT_FROM_EMAIL", None),
            to=list(recipients),
        )
        msg.attach("m365-active-users.csv", _csv_for(rows), "text/csv")
        msg.send(fail_silently=False)
        self.stdout.write(self.style.SUCCESS(
            f"Email sent to {', '.join(recipients)}."
        ))
