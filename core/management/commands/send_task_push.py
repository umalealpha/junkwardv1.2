"""Push each staff member a morning nudge for their tasks due today or overdue
(CFO 2026-09-03, Omni staff app §5.4). Runs once a day from cron — that IS the
dedupe. Fail-soft: if push isn't configured the whole thing is a no-op.

  python manage.py send_task_push
"""
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Send a Web Push nudge to each user with tasks due today or overdue."

    def handle(self, *args, **opts):
        from core.webpush import push_enabled, send_push_to_user
        if not push_enabled():
            self.stdout.write("Push disabled (no VAPID key) — nothing sent.")
            return

        from django.contrib.auth.models import User
        from django.utils import timezone
        from core.models import OmniTask, PushSubscription
        from taskboard.payment_bulk_views import _is_cfo

        today = timezone.localdate()
        user_ids = (PushSubscription.objects.values_list("user_id", flat=True)
                    .distinct())
        people = pushed = 0
        for u in User.objects.filter(id__in=list(user_ids), is_active=True):
            # Same filters as taskboard MyTasksView: open tasks only, and the
            # CFO's payment tasks live on the Payments screen, not the board.
            qs = (OmniTask.objects.filter(assignee=u, due_at__lte=today)
                  .exclude(status__in=[OmniTask.Status.DONE,
                                       OmniTask.Status.CANCELLED]))
            if _is_cfo(u):
                qs = qs.exclude(payment_request__isnull=False)
            count = qs.count()
            if not count:
                continue
            body = f"{count} task{'s' if count != 1 else ''} due today or overdue — waiting on you."
            people += 1
            pushed += send_push_to_user(u, "Tasks due today", body, url="/app/tasks")
        self.stdout.write(f"Nudged {people} person(s); {pushed} device(s).")
