from rest_framework import serializers

from .models import TrainingModule, TrainingQuestion, TrainingAttempt


class TrainingQuestionTraineeSerializer(serializers.ModelSerializer):
    """No `correct_letter` — never shown to the trainee."""
    options = serializers.SerializerMethodField()

    class Meta:
        model  = TrainingQuestion
        fields = ("id", "order", "prompt", "options")

    def get_options(self, obj):
        return obj.options_visible_to_trainee()


class TrainingModuleTraineeSerializer(serializers.ModelSerializer):
    questions       = TrainingQuestionTraineeSerializer(many=True, read_only=True)
    pdf_url         = serializers.SerializerMethodField()
    my_best_attempt = serializers.SerializerMethodField()

    class Meta:
        model  = TrainingModule
        fields = ("id", "slug", "title", "subtitle", "body_html",
                  "video_url", "pdf_url",
                  "pass_mark_pct", "open_from", "open_until",
                  "is_published", "questions",
                  "my_best_attempt")

    def get_pdf_url(self, obj):
        if not obj.pdf:
            return None
        req = self.context.get("request")
        return req.build_absolute_uri(obj.pdf.url) if req else obj.pdf.url

    def get_my_best_attempt(self, obj):
        req = self.context.get("request")
        if not req or not req.user.is_authenticated:
            return None
        att = (TrainingAttempt.objects
               .filter(module=obj, user=req.user)
               .order_by("-passed", "-score_pct", "-completed_at")
               .first())
        if not att:
            return None
        return {
            "score_pct":         att.score_pct,
            "passed":            att.passed,
            "certificate_number": att.certificate_number or None,
            "completed_at":      att.completed_at.isoformat(),
        }


class TrainingAttemptRowSerializer(serializers.ModelSerializer):
    """Compliance dashboard row."""
    user_username = serializers.CharField(source="user.username", read_only=True)
    user_email    = serializers.EmailField(source="user.email", read_only=True)
    user_name     = serializers.SerializerMethodField()

    class Meta:
        model  = TrainingAttempt
        fields = ("id", "user_username", "user_email", "user_name",
                  "score_pct", "passed", "certificate_number", "completed_at")

    def get_user_name(self, obj):
        u = obj.user
        return (f"{u.first_name} {u.last_name}".strip() or u.username)
