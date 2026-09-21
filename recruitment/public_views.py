"""
recruitment/public_views.py — PUBLIC (no-login) job apply endpoints.

Candidates reach these from a link posted on LinkedIn / Facebook. They see one
OPEN vacancy and submit name / email / phone + CV. We create the Candidate +
Application exactly like the HR upload path (CV text extracted LOCALLY, a
deterministic skills score, then the optional identity-stripped, PII-firewalled
AI screening), so a public application lands in HR's ranked queue automatically.

No candidate PII is ever returned to the browser. AllowAny, but hardened:
only OPEN requisitions accept, required fields, CV type + size limits, a hidden
honeypot, a duplicate guard, and a per-IP hourly rate limit.
"""
from __future__ import annotations

import logging

from django.core.cache import cache
from django.db import IntegrityError, transaction
from rest_framework.decorators import api_view, parser_classes, permission_classes
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.permissions import AllowAny
from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from rest_framework.response import Response

from core.upload_safety import validate_document_upload
from .matching import extract_skills, score_candidate
from .models import Application, Candidate, JobRequisition

log = logging.getLogger("recruitment.public")

MAX_CV_MB = 8
ALLOWED_EXT = (".pdf", ".doc", ".docx")
RATE_LIMIT_PER_HOUR = 12
_THANK_YOU = "Thank you — your application has been received."


def _client_ip(request) -> str:
    xff = request.META.get("HTTP_X_FORWARDED_FOR", "")
    if xff:
        return xff.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR", "") or "unknown"


def _public_job_json(r) -> dict:
    """Safe, public-only fields of a vacancy — never HR/internal data."""
    return {
        "id": str(r.id),
        "title": r.title,
        "department": r.department,
        "employment_type": r.get_employment_type_display(),
        "jd_text": r.jd_text,
        "required_skills": r.required_skills or [],
        "is_open": r.status == "open",
    }


@api_view(["GET"])
@permission_classes([AllowAny])
def public_job(request, rid):
    """Public view of ONE vacancy (used to render the apply page)."""
    r = JobRequisition.objects.filter(id=rid).first()
    if not r or r.status == "draft":
        return Response({"detail": "This vacancy was not found."}, status=404)
    return Response(_public_job_json(r))


@api_view(["POST"])
@permission_classes([AllowAny])
@parser_classes([MultiPartParser, FormParser])
def public_apply(request, rid):
    """A candidate submits their application from the public link."""
    # Honeypot: bots fill hidden fields. Pretend success, create nothing.
    if (request.data.get("company_website") or "").strip():
        return Response({"ok": True, "message": _THANK_YOU})

    ip = _client_ip(request)
    rate_key = f"recruit_apply_rate:{ip}"
    hits = cache.get(rate_key, 0)
    if hits >= RATE_LIMIT_PER_HOUR:
        return Response(
            {"detail": "Too many applications from this connection. Please try again later."},
            status=429,
        )

    r = JobRequisition.objects.filter(id=rid).first()
    if not r:
        return Response({"detail": "This vacancy was not found."}, status=404)
    if r.status != "open":
        return Response({"detail": "This role is no longer accepting applications."}, status=409)

    name = (request.data.get("full_name") or "").strip()
    email = (request.data.get("email") or "").strip()
    phone = (request.data.get("phone") or "").strip()
    if not name:
        return Response({"detail": "Please enter your full name."}, status=400)
    if not email:
        return Response({"detail": "Please enter your email address."}, status=400)
    try:
        validate_email(email)
    except ValidationError:
        return Response({"detail": "Please enter a valid email address."}, status=400)
    # Normalise so one person = one Candidate however they type their address
    # (Manus QC 2026-08-27): "Ann@X.com" and "ann@x.com" are the same applicant.
    email = email.lower()

    cv = request.FILES.get("cv")
    if cv is None:
        return Response({"detail": "Please attach your CV."}, status=400)
    # Validate by CONTENT, not by filename (Manus QC 2026-08-26, P0): the old
    # check was `cv.name.endswith(ALLOWED_EXT)`, so an .exe renamed cv.pdf was
    # accepted and stored. The filename is attacker-controlled.
    err = validate_document_upload(cv, allowed=("pdf", "doc", "docx"), max_mb=MAX_CV_MB)
    if err:
        return Response({"detail": err}, status=400)

    # Already applied for this exact role with this email? Don't create a duplicate.
    if Application.objects.filter(requisition=r, candidate__email__iexact=email).exists():
        return Response({"ok": True, "message": "You have already applied for this role — we have your application."})

    # Extract CV text LOCALLY (never leaves Omni).
    cv_text = ""
    try:
        from core.doc_parse import parse
        raw = cv.read()
        cv.seek(0)  # rewind so the FileField save re-reads the whole file
        cv_text = (parse(raw, filename=cv.name).text or "")[:20000]
    except Exception:  # noqa: BLE001 — degrade: store the CV, score on what we have
        cv_text = ""

    linkedin = (request.data.get("linkedin") or "").strip()[:200]
    heard = (request.data.get("heard") or "").strip()[:60]
    source = " / ".join(["public"] + ([heard] if heard else []))[:40]

    skills = extract_skills(cv_text)
    scored = score_candidate(r.required_skills or [], cv_text, skills)
    # One identity per email, race-safe: get_or_create honours the Candidate
    # email uniqueness constraint (a concurrent duplicate raises IntegrityError,
    # which get_or_create turns back into the existing row). A second vacancy
    # therefore reuses the same Candidate instead of spawning a new identity
    # (Manus QC 2026-08-27). Wrapped in atomic so a failed create can't leave a
    # half-written row inside the request's transaction.
    try:
        with transaction.atomic():
            candidate, _c_created = Candidate.objects.get_or_create(
                email=email[:254],
                defaults={"full_name": name[:120], "phone": phone[:40],
                          "source": source, "cv": cv, "cv_text": cv_text,
                          "skills": skills},
            )
    except IntegrityError:      # expected concurrent-apply race — handled below
        # A concurrent apply created the same-email candidate a moment ago; reuse
        # it. Log the id, never the raw email — PII stays out of the logs, same
        # as the stage audit next door (Fable review 2026-08-27).
        candidate = Candidate.objects.filter(email__iexact=email).first()
        if candidate is None:
            log.warning("candidate email race but no row found on retry")
            return Response({"detail": "Could not save your application. Please try again."},
                            status=409)
        log.info("candidate email race — reusing candidate %s", candidate.pk)

    try:
        with transaction.atomic():
            app, created = Application.objects.get_or_create(
                requisition=r, candidate=candidate,
                defaults={
                    "match_score": scored["score"],
                    "matched_skills": scored["matched"],
                    "missing_skills": scored["missing"],
                    "notes": (f"LinkedIn: {linkedin}" if linkedin else ""),
                },
            )
    except IntegrityError:      # expected duplicate-application race — handled below
        log.info("duplicate application race for candidate %s on requisition %s",
                 candidate.pk, r.pk)
        # Expected race: this candidate already has an application for this role
        # (uniq_req_candidate). Treat it as the friendly "already applied" case.
        created = False
        app = Application.objects.filter(requisition=r, candidate=candidate).first()
    if not created:
        return Response({"ok": True, "message": "You have already applied for this role — we have your application."})

    # Best-effort AI screening (identity-stripped, PII-firewalled). A failure
    # leaves the deterministic score intact.
    try:
        from .api_views import _run_ai
        _run_ai(app, r, candidate)
    except Exception:  # noqa: BLE001
        log.exception("public apply: AI screen failed for application %s", app.id)

    cache.set(rate_key, hits + 1, 3600)
    log.info("public application %s created for requisition %s", app.id, r.id)

    # Instant acknowledgement to the applicant + their status link (best-effort).
    try:
        from .candidate_emails import send_ack
        send_ack(candidate, r, application=app)
    except Exception:  # noqa: BLE001
        log.exception("public apply: ack email failed for application %s", app.id)

    return Response({"ok": True, "message": _THANK_YOU}, status=201)
