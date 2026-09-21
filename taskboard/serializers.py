from rest_framework import serializers

from core.models import OmniTask, OmniTaskComment, TaskFeedback

from .models import Notification


class OmniTaskBriefSerializer(serializers.ModelSerializer):
    assignee_name = serializers.CharField(source="assignee.get_full_name", read_only=True)
    assignee_username = serializers.CharField(source="assignee.username", read_only=True)
    assigner_name = serializers.CharField(source="assigner.get_full_name", read_only=True)
    is_overdue = serializers.SerializerMethodField()
    # The linked payment authorisation, when this task is one (CFO 2026-08-03:
    # the phone card showed a total and nothing else, so the approver could not
    # see WHICH suppliers and amounts were inside it). The id lets the app pull
    # the full pack from /payment-requests/<id>/; the ref labels the card.
    payment_request_id = serializers.SerializerMethodField()
    payment_ref = serializers.SerializerMethodField()

    class Meta:
        model = OmniTask
        fields = [
            "id", "title", "body", "due_at", "due_time", "priority", "status",
            "completion_pct",
            "assignee", "assignee_name", "assignee_username", "assigner", "assigner_name",
            "completed_at", "is_overdue", "week_of", "source", "created_at",
            "payment_request_id", "payment_ref",
        ]

    def get_is_overdue(self, obj):
        from .services import is_overdue
        return is_overdue(obj)

    def _payment_request(self, obj):
        # `payment_request` is the reverse FK from PaymentRequest.task, so it is
        # a manager, not an object. Callers prefetch it; memoised so the id and
        # the ref cost ONE lookup even where a caller forgets to.
        if obj.source != "payment_request":
            return None
        if not hasattr(obj, "_pr_cache"):
            obj._pr_cache = next(iter(obj.payment_request.all()), None)
        return obj._pr_cache

    def get_payment_request_id(self, obj):
        pr = self._payment_request(obj)
        return str(pr.id) if pr else None

    def get_payment_ref(self, obj):
        pr = self._payment_request(obj)
        return pr.ref if pr else ''


class NotificationSerializer(serializers.ModelSerializer):
    task_title = serializers.CharField(source="task.title", read_only=True)
    task_due_at = serializers.DateField(source="task.due_at", read_only=True)

    class Meta:
        model = Notification
        fields = ["id", "task", "task_title", "task_due_at", "type", "acknowledged", "seen_at", "created_at"]


class CompleteTaskSerializer(serializers.Serializer):
    """Payload for the completion gate. Real enforcement is in services.complete_task
    (server-side); these are just shape checks."""
    # Note is OPTIONAL — the service gate is MIN_NOTE_CHARS=0 (CFO 2026-07-24),
    # so one-tap Done sends an empty body. This shape check must not re-impose the
    # note the service made optional (Fable review 2026-08-03).
    body = serializers.CharField(allow_blank=True, required=False, default='', trim_whitespace=True)
    interaction_seconds = serializers.IntegerField(min_value=0)


class TaskCommentSerializer(serializers.ModelSerializer):
    author_name = serializers.CharField(source="author.get_full_name", read_only=True)
    evidence_url = serializers.SerializerMethodField()

    class Meta:
        model = OmniTaskComment
        fields = ["id", "author", "author_name", "body", "new_status",
                  "evidence_url", "created_at"]

    def get_evidence_url(self, obj):
        # Authenticated endpoint — raw /media/ URLs are not served in production
        # (DEBUG=False), so .url would 404. FE fetches this with the auth token.
        return (f"/api/v1/taskboard/comments/{obj.pk}/evidence/"
                if obj.evidence else None)


class TaskFeedbackSerializer(serializers.ModelSerializer):
    from_name = serializers.CharField(source="from_user.get_full_name", read_only=True)
    task_title = serializers.CharField(source="task.title", read_only=True)

    class Meta:
        model = TaskFeedback
        fields = ["id", "task", "task_title", "from_user", "from_name",
                  "to_user", "body", "acknowledged_at", "created_at"]


class TaskDetailSerializer(OmniTaskBriefSerializer):
    comments = TaskCommentSerializer(many=True, read_only=True)
    feedback = TaskFeedbackSerializer(many=True, read_only=True)

    class Meta(OmniTaskBriefSerializer.Meta):
        fields = OmniTaskBriefSerializer.Meta.fields + ["comments", "feedback"]


class TaskStatusUpdateSerializer(serializers.Serializer):
    """Move a task to in_progress / partial / blocked with a REQUIRED justification
    (CFO 2026-07-13). Evidence is required for blocked/partial. 'done' keeps its own
    interaction-gated endpoint."""
    status = serializers.ChoiceField(choices=[
        OmniTask.Status.IN_PROGRESS, OmniTask.Status.PARTIAL, OmniTask.Status.BLOCKED,
    ])
    body = serializers.CharField(allow_blank=False, trim_whitespace=True,
                                 help_text="Why — the justification / blocker reason.")
    evidence = serializers.FileField(required=False)


class TaskFeedbackCreateSerializer(serializers.Serializer):
    """Assigner feedback on a task, optionally recording a status decision +
    percent complete from the dashboard control (CFO 2026-07-13):
      status = done | partial | not_done
      completion_pct in {0, 25, 50, 75, 100}
    'not_done' requires a >= 30-word note — enforced in the view."""
    body = serializers.CharField(allow_blank=False, trim_whitespace=True)
    status = serializers.ChoiceField(
        required=False, choices=["done", "partial", "not_done"])
    completion_pct = serializers.IntegerField(
        required=False, min_value=0, max_value=100)
