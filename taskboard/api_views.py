"""
taskboard/api_views.py — DRF endpoints for the task reminder + completion layer.

  GET  taskboard/my-tasks/                      my open tasks (soonest due first)
  POST taskboard/tasks/<uuid>/complete/         the server-side completion gate
  GET  taskboard/notifications/                  my unacknowledged reminders (drives modal)
  POST taskboard/notifications/<uuid>/ack/       dismiss an assign-day toast
  GET  taskboard/completion-rate/                self; ?user_id=<id> for managers (DPA-gated)

The completion gate's real enforcement lives in services.complete_task (never the DOM).
"""
from datetime import date

from django.contrib.auth.models import User
from django.core.exceptions import ValidationError as DjangoValidationError
from django.utils import timezone
from rest_framework import status as http
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from core.models import OmniTask
from taskboard.payment_bulk_views import _is_cfo

from . import services
from .models import Notification
from .serializers import (
    CompleteTaskSerializer,
    NotificationSerializer,
    OmniTaskBriefSerializer,
)


class MyTasksView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        qs = (
            OmniTask.objects.filter(assignee=request.user)
            .exclude(status__in=[OmniTask.Status.DONE, OmniTask.Status.CANCELLED])
            # payment_request is the reverse FK the brief serializer reads to
            # label a payment task with its pack — prefetch or it is one query
            # per card.
            .prefetch_related("payment_request")
            .order_by("due_at", "-priority")
        )
        # Payment authorisation is the CFO's job on the dedicated Payments screen,
        # not a task (CFO 2026-08-29). Hide payment tasks for the CFO only — the
        # finance first-approvers (Pako / Kago / Legakwa) KEEP their payment
        # sign-off tasks, exactly as the CFO asked.
        if _is_cfo(request.user):
            qs = qs.exclude(payment_request__isnull=False)
        return Response(OmniTaskBriefSerializer(qs, many=True).data)


class CompleteTaskView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, task_id):
        task = OmniTask.objects.filter(pk=task_id).first()
        if not task:
            return Response({"detail": "Task not found."}, status=http.HTTP_404_NOT_FOUND)
        if task.assignee_id != request.user.id and not request.user.is_staff:
            return Response(
                {"detail": "Only the assignee can complete this task."},
                status=http.HTTP_403_FORBIDDEN,
            )
        ser = CompleteTaskSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        try:
            services.complete_task(
                task,
                request.user,
                ser.validated_data["body"],
                ser.validated_data["interaction_seconds"],
            )
        except DjangoValidationError as e:
            msg = e.messages[0] if getattr(e, "messages", None) else str(e)
            # A payment-control refusal (PAY-DUP-01 and any later PAY-* control)
            # is a multi-line finding, not a one-line error. Pass the control code
            # through so the front end renders it whole; the global toast shows
            # only 240 characters and cut the CFO's block off mid-word on 3 Aug 2026.
            payload = {"detail": msg}
            code = getattr(e, "code", None)
            if isinstance(code, str) and code.startswith("PAY-"):
                payload["control"] = code
            return Response(payload, status=http.HTTP_400_BAD_REQUEST)
        return Response(
            {"detail": "Task completed.", "completed_at": task.completed_at},
            status=http.HTTP_200_OK,
        )


class NotificationsView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        # Exclude reminders whose task is already finished (CFO 2026-07-26:
        # "even after we clear the task they do not appear again").
        #
        # sweep_due_and_overdue() has self-healed orphaned reminders since
        # 2026-07-15, but only when the CRON runs — so a task completed by any
        # path that doesn't call complete_task() (the dashboard status control,
        # the CFO decision modal, admin, a workflow's bulk status update) kept
        # re-raising the force-modal until the next sweep, up to a day later.
        # The read side now filters too, so clearing a task clears its popup on
        # the very next poll, whatever cleared it.
        qs = (
            Notification.objects.filter(recipient=request.user, acknowledged=False)
            .exclude(task__status__in=[OmniTask.Status.DONE,
                                       OmniTask.Status.CANCELLED])
            .select_related("task")
        )
        return Response(NotificationSerializer(qs, many=True).data)


class AckNotificationView(APIView):
    """Dismiss an assign-day toast only. due_day / overdue reminders are NOT
    dismissible here — they clear automatically when the task is completed."""

    permission_classes = [IsAuthenticated]

    def post(self, request, notif_id):
        n = Notification.objects.filter(pk=notif_id, recipient=request.user).first()
        if not n:
            return Response({"detail": "Not found."}, status=http.HTTP_404_NOT_FOUND)
        if n.type != Notification.Type.ASSIGN_DAY:
            return Response(
                {"detail": "Due/overdue reminders clear when the task is completed."},
                status=http.HTTP_400_BAD_REQUEST,
            )
        n.acknowledged = True
        n.seen_at = timezone.now()
        n.save(update_fields=["acknowledged", "seen_at", "updated_at"])
        return Response({"detail": "ok"})


class CompletionRateView(APIView):
    """Self by default. ?user_id=<id> is manager-only (is_staff) and is EMPLOYEE
    MONITORING under the Botswana DPA 2024 — do not expose in the UI before the
    DPIA + staff privacy notice are signed off."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        target = request.user
        uid = request.query_params.get("user_id")
        if uid and str(uid) != str(request.user.id):
            if not request.user.is_staff:
                return Response({"detail": "Not permitted."}, status=http.HTTP_403_FORBIDDEN)
            target = User.objects.filter(pk=uid).first()
            if not target:
                return Response({"detail": "User not found."}, status=http.HTTP_404_NOT_FOUND)

        today = timezone.localdate()
        start = self._parse(request.query_params.get("start")) or today.replace(day=1)
        end = self._parse(request.query_params.get("end")) or today
        data = services.completion_rate(target, start, end)
        data.update({"user_id": target.id, "start": start, "end": end})
        return Response(data)

    @staticmethod
    def _parse(s: str | None) -> date | None:
        if not s:
            return None
        try:
            return date.fromisoformat(s)
        except ValueError:
            return None
