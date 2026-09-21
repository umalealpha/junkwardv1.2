"""
training/api_views.py

Trainee endpoints:
  GET  /api/v1/training/modules/<slug>/          — module + questions (no answers)
  POST /api/v1/training/modules/<slug>/submit/   — submit answers, get score
  GET  /api/v1/training/attempts/<id>/certificate.pdf — PDF for a passing attempt

Compliance dashboard:
  GET  /api/v1/training/modules/<slug>/roster/   — every user + their status
"""
from __future__ import annotations

from django.contrib.auth import get_user_model
from django.db.models import Prefetch
from django.http import HttpResponse
from django.shortcuts import get_object_or_404
from django.utils import timezone

from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .certificate import render_certificate_pdf
from .models import TrainingAttempt, TrainingModule, TrainingQuestion
from .serializers import (
    TrainingAttemptRowSerializer,
    TrainingModuleTraineeSerializer,
)


User = get_user_model()


def _is_compliance_or_admin(user) -> bool:
    if not user.is_authenticated:
        return False
    if user.is_superuser or user.is_staff:
        return True
    grp_names = set(user.groups.values_list("name", flat=True))
    return bool(grp_names & {"Compliance", "compliance", "hris", "HRIS"})


class ModuleDetailView(APIView):
    """GET the module + its questions (never the correct letters)."""
    permission_classes = [IsAuthenticated]

    def get(self, request, slug):
        module = get_object_or_404(
            TrainingModule.objects.prefetch_related(
                Prefetch("questions",
                         queryset=TrainingQuestion.objects.order_by("order")),
            ),
            slug=slug, is_published=True,
        )
        data = TrainingModuleTraineeSerializer(
            module, context={"request": request},
        ).data
        return Response(data)


class SubmitAttemptView(APIView):
    """POST answers → score, pass/fail, mint certificate number on pass."""
    permission_classes = [IsAuthenticated]

    def post(self, request, slug):
        module = get_object_or_404(TrainingModule, slug=slug, is_published=True)
        if not module.is_open_today():
            return Response(
                {"detail":
                 f"This module is open {module.open_from} to {module.open_until}."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        submitted = request.data.get("answers") or {}
        if not isinstance(submitted, dict):
            return Response({"detail": "answers must be an object."},
                            status=status.HTTP_400_BAD_REQUEST)

        questions = list(module.questions.all())
        if not questions:
            return Response({"detail": "This module has no questions yet."},
                            status=status.HTTP_400_BAD_REQUEST)

        # Score. A missing / unknown answer scores 0 for that question.
        correct = 0
        for q in questions:
            picked = (submitted.get(str(q.id)) or "").strip().upper()
            if picked == q.correct_letter:
                correct += 1
        score_pct = round(correct * 100 / len(questions))
        passed = score_pct >= module.pass_mark_pct

        attempt = TrainingAttempt.objects.create(
            module       = module,
            user         = request.user,
            answers      = {str(k): (v or "").upper()[:1] for k, v in submitted.items()},
            score_pct    = score_pct,
            passed       = passed,
            completed_at = timezone.now(),
        )
        if passed:
            attempt.mint_certificate_number()
            attempt.save(update_fields=["certificate_number", "updated_at"])

        return Response({
            "attempt_id":        str(attempt.id),
            "score_pct":         score_pct,
            "pass_mark_pct":     module.pass_mark_pct,
            "passed":            passed,
            "questions_total":   len(questions),
            "questions_correct": correct,
            "certificate_number": attempt.certificate_number or None,
        })


class CertificatePdfView(APIView):
    """GET the PDF for a passing attempt.
    Owner reads their own; Compliance/staff read any."""
    permission_classes = [IsAuthenticated]

    def get(self, request, pk):
        attempt = get_object_or_404(
            TrainingAttempt.objects.select_related("module", "user"), pk=pk,
        )
        if attempt.user_id != request.user.id and not _is_compliance_or_admin(request.user):
            return Response({"detail": "Not allowed."},
                            status=status.HTTP_403_FORBIDDEN)
        if not attempt.passed or not attempt.certificate_number:
            return Response({"detail": "No certificate — this attempt did not pass."},
                            status=status.HTTP_404_NOT_FOUND)
        pdf = render_certificate_pdf(attempt)
        resp = HttpResponse(pdf, content_type="application/pdf")
        resp["Content-Disposition"] = (
            f'inline; filename="{attempt.certificate_number}.pdf"'
        )
        return resp


class RosterView(APIView):
    """Compliance dashboard — every active user, done/not-done for a module."""
    permission_classes = [IsAuthenticated]

    def get(self, request, slug):
        if not _is_compliance_or_admin(request.user):
            return Response({"detail": "Compliance-only."},
                            status=status.HTTP_403_FORBIDDEN)
        module = get_object_or_404(TrainingModule, slug=slug)

        # Latest attempt per user for this module.
        attempts = (TrainingAttempt.objects
                    .filter(module=module)
                    .select_related("user")
                    .order_by("user_id", "-passed", "-score_pct", "-completed_at"))
        best_by_user: dict[int, TrainingAttempt] = {}
        for a in attempts:
            best_by_user.setdefault(a.user_id, a)

        active_users = (User.objects
                        .filter(is_active=True)
                        .exclude(email="")
                        .order_by("first_name", "last_name", "username"))

        rows = []
        done = 0
        for u in active_users:
            att = best_by_user.get(u.id)
            if att and att.passed:
                done += 1
            rows.append({
                "user_id":          u.id,
                "username":         u.username,
                "email":            u.email,
                "name":             (f"{u.first_name} {u.last_name}".strip() or u.username),
                "status":           "passed" if (att and att.passed)
                                    else "attempted" if att
                                    else "not_started",
                "best_score":       att.score_pct if att else None,
                "attempts":         TrainingAttempt.objects.filter(module=module, user=u).count(),
                "certificate_number": att.certificate_number if (att and att.passed) else None,
                "completed_at":     att.completed_at.isoformat() if (att and att.passed) else None,
            })

        return Response({
            "module": {
                "slug":          module.slug,
                "title":         module.title,
                "open_from":     module.open_from,
                "open_until":    module.open_until,
                "pass_mark_pct": module.pass_mark_pct,
                "is_published":  module.is_published,
            },
            "total":       len(rows),
            "done":        done,
            "not_started": sum(1 for r in rows if r["status"] == "not_started"),
            "attempted":   sum(1 for r in rows if r["status"] == "attempted"),
            "rows":        rows,
        })
