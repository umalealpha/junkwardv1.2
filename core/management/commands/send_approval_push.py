"""Push each approver a nudge for what's waiting on them (CFO 2026-07-23
wow-feature). Run from cron alongside the existing approvals chase. Fail-soft:
if push isn't configured the whole thing is a no-op.

  python manage.py send_approval_push
"""
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Send a Web Push nudge to each approver with pending approvals."

    def handle(self, *args, **opts):
        from core.webpush import push_enabled, send_push_to_user
        if not push_enabled():
            self.stdout.write("Push disabled (no VAPID key) — nothing sent.")
            return

        from django.contrib.auth.models import User
        from core.models import PushSubscription
        from core.approvals_views import (pending_approvals_for,
                                           pending_approval_items_for)

        user_ids = (PushSubscription.objects.values_list("user_id", flat=True)
                    .distinct())
        people = pushed = 0
        for u in User.objects.filter(id__in=list(user_ids), is_active=True):
            try:
                streams = pending_approvals_for(u)
            except Exception:  # noqa: BLE001
                continue
            count = sum(s.get("count", 0) for s in streams)
            if not count:
                continue
            # BWP total across the sign-only itemised streams (best-effort).
            total = 0.0
            try:
                for s in pending_approval_items_for(u):
                    for it in s["items"]:
                        if it.get("amount"):
                            total += float(it["amount"])
            except Exception:  # noqa: BLE001
                total = 0.0
            money = f" — BWP {total:,.0f}" if total else ""
            body = f"{count} approval{'s' if count != 1 else ''} waiting{money}. Tap to sign."
            people += 1
            pushed += send_push_to_user(u, "Approvals waiting on you", body,
                                        url="/app/approve")
        self.stdout.write(f"Nudged {people} approver(s); {pushed} device(s).")
