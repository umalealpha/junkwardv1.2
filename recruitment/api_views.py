"""
recruitment/api_views.py — ATS endpoints (HR-gated; CVs are PII).

  GET  /api/v1/recruitment/requisitions/                 list vacancies
  POST /api/v1/recruitment/requisitions/                 create a vacancy
  GET  /api/v1/recruitment/requisitions/<id>/applications/  ranked candidates
  POST /api/v1/recruitment/requisitions/<id>/candidates/    upload a CV (multipart)
  POST /api/v1/recruitment/applications/<id>/stage/         move stage / mark reviewed

CV text is extracted LOCALLY (core.doc_parse) and scored LOCALLY
(recruitment.matching). The optional AI assessment (recruitment.ai_analysis) sends
an ANONYMISED, PII-scrubbed CV to the reasoning failover chain — the candidate's
identity is stripped first and every external-LLM call is PII-firewalled
(core.pii_firewall). Raw candidate PII never leaves Omni in the clear.
"""
from __future__ import annotations

from rest_framework.decorators import api_view, parser_classes, permission_classes
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

import logging

from django.db.models import Q
from django.utils import timezone

from core.hris_access import user_can_access_hris
from . import candidate_status
from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from core.upload_safety import validate_document_upload
from .matching import extract_skills, score_candidate
from .models import (Application, AuthorityToRecruit, Candidate,
                     InterviewScorecard, JobRequisition)

log = logging.getLogger("recruitment.api")
MAX_CV_MB = 8


def _hr_gate(request):
    """Recruitment holds candidate PII — restrict to HR/recruiters (or admin)."""
    u = request.user
    if u.is_superuser or user_can_access_hris(u):
        return None
    return Response({"detail": "Recruitment is restricted to HR / recruiters."}, status=403)


def _req_json(r):
    return {
        "id": str(r.id), "title": r.title, "department": r.department,
        "employment_type": r.employment_type, "headcount": r.headcount,
        "status": r.status, "required_skills": r.required_skills or [],
        "jd_text": r.jd_text, "applications": r.applications.count(),
        "created_at": r.created_at.isoformat(),
        "days_open": max(0, (timezone.now() - r.created_at).days),
    }


def _app_json(a):
    c = a.candidate
    return {
        "id": str(a.id), "candidate_id": str(c.id),
        "name": c.full_name, "email": c.email, "phone": c.phone,
        "stage": a.stage, "match_score": a.match_score,
        "matched_skills": a.matched_skills or [], "missing_skills": a.missing_skills or [],
        "ai_analysed": a.ai_analysed, "ai_score": a.ai_score,
        "ai_summary": a.ai_summary, "ai_strengths": a.ai_strengths or [],
        "ai_gaps": a.ai_gaps or [], "ai_questions": a.ai_questions or [],
        "human_reviewed": a.human_reviewed, "notes": a.notes,
        "has_cv": bool(c.cv), "created_at": a.created_at.isoformat(),
        # The no-login link HR can share so the candidate can check their own
        # coarse status (received / under review / not successful).
        "status_url": candidate_status.status_url(a),
        "scorecard_count": a.scorecards.count(),
    }


def _run_ai(app, req, candidate):
    """Run the AI CV assessment and persist it. Best-effort — a failure leaves the
    deterministic score intact."""
    from .ai_analysis import analyse_cv
    res = analyse_cv(
        candidate.cv_text, job_title=req.title, jd_text=req.jd_text,
        required_skills=req.required_skills or [],
        name=candidate.full_name, email=candidate.email, phone=candidate.phone)
    if not res:
        return False
    app.ai_analysed = True
    app.ai_score = res.get("fit_score")
    app.ai_summary = res.get("summary", "")
    app.ai_strengths = res.get("strengths", [])
    app.ai_gaps = res.get("gaps", [])
    app.ai_questions = res.get("interview_questions", [])
    app.save(update_fields=["ai_analysed", "ai_score", "ai_summary",
                            "ai_strengths", "ai_gaps", "ai_questions", "updated_at"])
    return True


@api_view(["GET", "POST"])
@permission_classes([IsAuthenticated])
def requisitions(request):
    denied = _hr_gate(request)
    if denied:
        return denied
    if request.method == "GET":
        qs = JobRequisition.objects.all()
        status_f = (request.query_params.get("status") or "").strip()
        if status_f:
            qs = qs.filter(status=status_f)
        return Response({"count": qs.count(), "requisitions": [_req_json(r) for r in qs[:200]]})

    d = request.data
    title = (d.get("title") or "").strip()
    if not title:
        return Response({"detail": "title is required."}, status=400)
    skills = d.get("required_skills") or []
    if isinstance(skills, str):
        skills = [s.strip() for s in skills.split(",") if s.strip()]
    r = JobRequisition.objects.create(
        title=title,
        department=(d.get("department") or "").strip(),
        employment_type=(d.get("employment_type") or "permanent"),
        headcount=int(d.get("headcount") or 1),
        jd_text=(d.get("jd_text") or ""),
        required_skills=[str(s).strip() for s in skills if str(s).strip()],
        status=(d.get("status") or "open"),
        created_by=request.user if request.user.is_authenticated else None,
    )
    return Response(_req_json(r), status=201)


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def requisition_applications(request, rid):
    denied = _hr_gate(request)
    if denied:
        return denied
    r = JobRequisition.objects.filter(id=rid).first()
    if not r:
        return Response({"detail": "Requisition not found."}, status=404)
    apps = list(r.applications.select_related("candidate").all())   # ordered by -match_score
    data = []
    for a in apps:
        d = _app_json(a)
        c = a.candidate
        # Repeat applicant: same person (email OR phone) applied elsewhere before.
        q = Q()
        if c.email:
            q |= Q(candidate__email__iexact=c.email)
        if c.phone:
            q |= Q(candidate__phone=c.phone)
        prior = Application.objects.filter(q).exclude(id=a.id).count() if q else 0
        d["prior_applications"] = prior
        d["repeat_applicant"] = prior > 0
        data.append(d)
    return Response({"requisition": _req_json(r), "count": len(apps), "applications": data})


@api_view(["POST"])
@permission_classes([IsAuthenticated])
@parser_classes([MultiPartParser, FormParser])
def upload_candidate(request, rid):
    """Upload a CV against a requisition: store the person + CV, extract the CV
    text locally, detect skills, and score against the required skills."""
    denied = _hr_gate(request)
    if denied:
        return denied
    r = JobRequisition.objects.filter(id=rid).first()
    if not r:
        return Response({"detail": "Requisition not found."}, status=404)

    name = (request.data.get("full_name") or request.data.get("name") or "").strip()
    if not name:
        return Response({"detail": "full_name is required."}, status=400)
    cv = request.FILES.get("cv")
    if cv is not None:
        # Manus QC 2026-08-26 (P0): this path checked SIZE ONLY — an .exe walked
        # straight in. Same content-based gate as the public form.
        err = validate_document_upload(cv, allowed=("pdf", "doc", "docx"),
                                       max_mb=MAX_CV_MB)
        if err:
            return Response({"detail": err}, status=400)

    # Extract CV text LOCALLY (never leaves Omni).
    cv_text = ""
    if cv is not None:
        try:
            from core.doc_parse import parse
            raw = cv.read()
            cv.seek(0)   # rewind so the FileField save re-reads the whole file
            cv_text = (parse(raw, filename=cv.name).text or "")[:20000]
        except Exception:   # noqa: BLE001 — degrade: store the CV, score on what we have
            cv_text = ""

    skills = extract_skills(cv_text)
    email = (request.data.get("email") or "").strip()
    if email:
        try:
            validate_email(email)
        except ValidationError:
            return Response({"detail": "Enter a valid email address."}, status=400)

    # Manus QC 2026-08-26 (P1): this always created a NEW Candidate, so uploading
    # the same person twice produced duplicate candidates and duplicate
    # applications on the same vacancy. Reuse the existing person when we can
    # identify them (email is the identifier; a blank email cannot dedupe).
    candidate = Candidate.objects.filter(email__iexact=email).first() if email else None
    if candidate is not None and name and \
            candidate.full_name.strip().casefold() != name.strip().casefold():
        # The email already belongs to SOMEONE ELSE. Before dedupe this made a
        # harmless duplicate; now it would rename that person, replace their CV
        # and re-score their applications — silent destruction of another
        # candidate's record on a single typo. Refuse and let HR check.
        return Response(
            {"detail": f"That email address is already on file for "
                       f"{candidate.full_name}. Check the address, or update "
                       f"that candidate directly."},
            status=409)
    if candidate is None:
        candidate = Candidate.objects.create(
            full_name=name, email=email,
            phone=(request.data.get("phone") or "").strip(),
            source=(request.data.get("source") or "").strip()[:40],
            cv=cv, cv_text=cv_text, skills=skills,
        )
    elif cv is not None:
        # Same person, newer CV — refresh it rather than spawning a second record.
        candidate.full_name = name or candidate.full_name
        candidate.cv, candidate.cv_text, candidate.skills = cv, cv_text, skills
        candidate.save(update_fields=["full_name", "cv", "cv_text", "skills"])
        # A Candidate is the PERSON, so their CV is shared by every application
        # they have. Replacing it would leave their other vacancies showing a
        # match score computed against the CV that is no longer stored — the
        # reviewer would open one CV and see another CV's score. Re-score them
        # against their own requisition so the number always matches the file.
        for other in (candidate.applications
                      .exclude(requisition_id=r.id)
                      .exclude(stage__in=("hired", "rejected"))
                      .select_related("requisition")):
            re_scored = score_candidate(
                other.requisition.required_skills or [], cv_text, skills)
            other.match_score = re_scored["score"]
            other.matched_skills = re_scored["matched"]
            other.missing_skills = re_scored["missing"]
            other.save(update_fields=["match_score", "matched_skills", "missing_skills"])

    # When HR adds an existing candidate to a second vacancy without re-attaching
    # their CV, cv_text/skills here are empty — scoring on them would show 0% for
    # someone whose CV is already on file. Fall back to what we stored.
    score_text = cv_text or (candidate.cv_text or "")
    score_skills = skills or (candidate.skills or [])
    scored = score_candidate(r.required_skills or [], score_text, score_skills)
    app, created = Application.objects.get_or_create(
        requisition=r, candidate=candidate,
        defaults={"match_score": scored["score"], "matched_skills": scored["matched"],
                  "missing_skills": scored["missing"]})
    ai_ok = _run_ai(app, r, candidate)   # best-effort AI assessment
    # Acknowledge the candidate + give them their status link (best-effort; a
    # mail failure must never fail the upload). Only on a new application.
    if created:
        try:
            from .candidate_emails import send_ack
            send_ack(candidate, r, application=app)
        except Exception:   # noqa: BLE001
            log.exception("ack email failed for application %s", app.id)
    return Response({"application": _app_json(app), "ai_analysed": ai_ok,
                     "parsed_chars": len(cv_text)}, status=201)


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def analyse_application(request, aid):
    """(Re)run the AI CV assessment for one application on demand."""
    denied = _hr_gate(request)
    if denied:
        return denied
    a = Application.objects.filter(id=aid).select_related("candidate", "requisition").first()
    if not a:
        return Response({"detail": "Application not found."}, status=404)
    ok = _run_ai(a, a.requisition, a.candidate)
    return Response({"application": _app_json(a), "ai_analysed": ok},
                    status=200 if ok else 422)


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def application_stage(request, aid):
    denied = _hr_gate(request)
    if denied:
        return denied
    a = Application.objects.filter(id=aid).select_related("candidate", "requisition").first()
    if not a:
        return Response({"detail": "Application not found."}, status=404)
    old_stage = a.stage
    stage = (request.data.get("stage") or "").strip()
    valid = {s for s, _ in Application.STAGE}
    if stage and stage not in valid:
        return Response({"detail": f"Invalid stage. One of {sorted(valid)}."}, status=400)
    if stage and stage != a.stage:
        # Manus QC 2026-08-26 (P1): any stage was accepted, so Applied -> Hired
        # in one call skipped screening, interviews, the offer AND the Authority
        # to Recruit. Enforce the transition graph server-side.
        allowed = Application.ALLOWED_STAGE_MOVES.get(a.stage, set())
        if stage not in allowed:
            return Response(
                {"detail": f"Cannot move from {a.get_stage_display()} to "
                           f"{dict(Application.STAGE).get(stage, stage)}. "
                           f"Allowed from here: "
                           f"{sorted(dict(Application.STAGE)[x] for x in allowed) or 'none'}."},
                status=409)
        if stage == "hired":
            # Nobody is hired without the five-signature Authority to Recruit.
            approved = a.authorities.filter(
                status=AuthorityToRecruit.Status.APPROVED).exists()
            if not approved:
                return Response(
                    {"detail": "This candidate cannot be marked Hired until an "
                               "Authority to Recruit for them is fully approved."},
                    status=409)
    if stage:
        # Blueprint: never auto-reject without a human — mark reviewed on any move.
        a.stage = stage
        a.human_reviewed = True
    if "notes" in request.data:
        a.notes = str(request.data.get("notes") or "")[:2000]
    if request.data.get("human_reviewed") in (True, "true", "1"):
        a.human_reviewed = True
    a.save()
    if stage and stage != old_stage:
        # Immutable audit of the stage transition (Manus QC 2026-08-27, HIGH):
        # a recruitment stage move used to leave no trail. Record actor + before/
        # after; IDs only (no candidate PII in the log).
        try:
            from core.models import AuditLog
            AuditLog.objects.create(
                table_name="recruitment.Application",
                record_id=str(a.id),
                action=AuditLog.Action.UPDATE,
                old_values={"stage": old_stage},
                new_values={"stage": a.stage, "candidate_id": str(a.candidate_id),
                            "requisition_id": str(a.requisition_id)},
                user=request.user if getattr(request.user, "is_authenticated", False) else None,
                ip_address=request.META.get("REMOTE_ADDR"),
                description=f"Recruitment stage {old_stage} → {a.stage} (application {a.id})",
            )
        except Exception:  # noqa: BLE001 — auditing must never block the move
            log.exception("stage-change audit failed for application %s", a.id)
    if stage == "rejected":
        # Courteous decline to the candidate (best-effort).
        try:
            from .candidate_emails import send_decline
            send_decline(a.candidate, a.requisition)
        except Exception:  # noqa: BLE001
            log.exception("decline email failed for application %s", a.id)
    return Response(_app_json(a))


def _scorecard_json(s):
    return {
        "id": str(s.id),
        "interviewer": s.interviewer_name or (s.interviewer.get_full_name()
                       if s.interviewer_id else ""),
        "round": s.round,
        "score": s.score,
        "recommendation": s.recommendation,
        "strengths": s.strengths,
        "concerns": s.concerns,
        "created_at": s.created_at.isoformat(),
    }


@api_view(["GET", "POST"])
@permission_classes([IsAuthenticated])
def application_scorecards(request, aid):
    """Interview scorecards for one application (HR-gated — internal only).

      GET   list every interviewer's scorecard for the application
      POST  {round, score, recommendation, strengths, concerns}
            file (or update) the caller's own scorecard for that round
    """
    denied = _hr_gate(request)
    if denied:
        return denied
    a = Application.objects.filter(id=aid).first()
    if not a:
        return Response({"detail": "Application not found."}, status=404)

    if request.method == "GET":
        cards = a.scorecards.select_related("interviewer").all()
        return Response({"count": cards.count(),
                         "scorecards": [_scorecard_json(s) for s in cards]})

    d = request.data or {}
    rnd = (d.get("round") or "interview_1").strip()
    if rnd not in {r for r, _ in InterviewScorecard.ROUND}:
        return Response({"detail": f"Invalid round. One of "
                         f"{sorted(r for r, _ in InterviewScorecard.ROUND)}."}, status=400)
    rec = (d.get("recommendation") or "maybe").strip()
    if rec not in {r for r, _ in InterviewScorecard.RECOMMENDATION}:
        return Response({"detail": "Invalid recommendation."}, status=400)
    # Manus QC 2026-08-26 (P0): this was max(0, min(100, int(... or 0))) — a BLANK
    # score saved silently as 0 and 999 saved silently as 100. An interview score
    # is evidence in a hiring decision; never invent or clamp it. Reject instead.
    raw_score = d.get("score")
    if raw_score is None or (isinstance(raw_score, str) and not raw_score.strip()):
        return Response({"detail": "Please enter a score between 0 and 100."}, status=400)
    try:
        score = int(raw_score)
    except (TypeError, ValueError):
        return Response({"detail": "score must be a whole number between 0 and 100."},
                        status=400)
    if not 0 <= score <= 100:
        return Response({"detail": f"score must be between 0 and 100 (got {score})."},
                        status=400)

    # One scorecard per (application, interviewer, round) — a re-submit updates.
    card, _ = InterviewScorecard.objects.get_or_create(
        application=a, interviewer=request.user, round=rnd)
    card.interviewer_name = request.user.get_full_name() or request.user.username
    card.score = score
    card.recommendation = rec
    card.strengths = str(d.get("strengths") or "")[:4000]
    card.concerns = str(d.get("concerns") or "")[:4000]
    card.save()
    return Response(_scorecard_json(card), status=201)
