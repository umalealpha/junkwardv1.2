"""hris/training_views.py — API for the Induction & Training Academy.

Two audiences, two gates:

  * **Every employee** (self-service): see every PUBLISHED course — company
    training is open to all staff, and an assignment only says which ones you
    must do — read the slides, sit the exam, and download their OWN certificate.
    No whitelist; results and certificates are self-scoped to the caller.
  * **Human Capital** (privileged): the "Upload Here" button, publishing a
    course, assigning it, and seeing who has passed. Gated by the standard HRIS
    policy, same as the rest of the vault.

    GET  /hris/api/training/courses/                  list (self or HR view)
    GET  /hris/api/training/courses/<slug>/           slides + my progress
    POST /hris/api/training/courses/<slug>/progress/  record slides read + dwell
    POST /hris/api/training/courses/<slug>/start/     issue a paper
    GET  /hris/api/training/attempts/<id>/            the paper, as I must see it
    POST /hris/api/training/attempts/<id>/submit/     mark it
    GET  /hris/api/training/certificates/<id>/pdf/    stream the certificate
    GET  /hris/api/training/verify/?code=ABCD1234     public-ish verification

    POST /hris/api/training/upload/                   HR: document -> draft course
    POST /hris/api/training/courses/<slug>/publish/   HR: build papers, go live
    POST /hris/api/training/courses/<slug>/assign/    HR: assign to people
    GET  /hris/api/training/courses/<slug>/results/   HR: who has passed
"""
from __future__ import annotations

import logging

from django.db import transaction
from django.db.models import Q
from django.http import FileResponse, Http404
from django.utils import timezone
from rest_framework import status
from rest_framework.decorators import (api_view, parser_classes, permission_classes)
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response

from hris.feature_views import _gate
from hris.models import HRISProfile
from hris.training_ai import ExtractionFailed, GenerationFailed, generate_course
from hris.training_models import (TrainingAssignment, TrainingAttempt,
                                  TrainingCertificate, TrainingCourse)
from hris.training_service import (NotEnoughQuestions, attempts_left,
                                   build_versions, exam_unlocked, issue_attempt,
                                   paper_for, score_attempt)

log = logging.getLogger(__name__)

MAX_UPLOAD_BYTES = 25 * 1024 * 1024

# Slide-reading is credited from heartbeats, and a heartbeat is worth at most
# this many seconds. A script spamming the endpoint therefore still needs a real
# number of real requests to clear the 15-minute gate.
MAX_HEARTBEAT_SECONDS = 20


def _as_int(value, default: int = 0) -> int:
    """Read a number the browser sent. A missing or malformed value is a normal
    thing for a form post to contain, not an error worth raising — but it is
    worth saying so out loud rather than hiding it in a bare except."""
    try:
        return int(value)
    except (TypeError, ValueError):
        if value not in (None, ''):
            log.info('training: ignoring malformed number %r, using %s',
                     value, default)
        return default


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _my_employee(user):
    """The caller's payroll.Employee, by the user link then by email."""
    emp = getattr(user, 'employee_record', None)
    if emp is not None:
        return emp
    prof = None
    email = (getattr(user, 'email', '') or '').strip().lower()
    if email:
        prof = (HRISProfile.objects
                .filter(employee__email__iexact=email)
                .select_related('employee').first())
    return getattr(prof, 'employee', None) if prof else None


def _is_hr(request) -> bool:
    """True when the caller passes the privileged HRIS gate."""
    return _gate(request) is None


def _course_card(c, *, me_attempt=None, assignment=None) -> dict:
    return {
        'slug': c.slug,
        'title': c.title,
        'summary': c.summary,
        'status': c.status,
        'source': c.source,
        'is_induction': c.is_induction,
        'duration_minutes': c.duration_minutes,
        'questions_per_paper': c.questions_per_paper,
        'version_count': c.version_count,
        'pass_mark': c.pass_mark,
        'slide_count': c.slides.count(),
        'bank_size': c.questions.filter(is_active=True).count(),
        'papers_built': c.versions.count(),
        'papers_issued': c.papers_issued,
        'ai_generated': c.ai_generated,
        'ai_notes': c.ai_notes,
        'source_filename': c.source_filename,
        'my_status': (assignment.status if assignment else None),
        'my_best_percent': (me_attempt.percent if me_attempt else None),
        'my_passed': bool(me_attempt and me_attempt.passed),
        'certificate_id': (
            str(me_attempt.certificate.id)
            if me_attempt and getattr(me_attempt, 'certificate', None) else None),
    }


def _my_best(course, user, employee):
    qs = TrainingAttempt.objects.filter(course=course, submitted_at__isnull=False)
    qs = qs.filter(Q(user=user) | Q(employee=employee)) if employee else qs.filter(user=user)
    return qs.order_by('-passed', '-percent', '-submitted_at').first()


# ---------------------------------------------------------------------------
# employee-facing
# ---------------------------------------------------------------------------

@api_view(['GET'])
@permission_classes([IsAuthenticated])
def courses(request):
    """Every published course, plus drafts when the caller is HR.

    Deliberately not filtered to the caller's assignments: a published course is
    company training anyone may take, and `my_status` tells them which ones they
    have actually been assigned.
    """
    hr = _is_hr(request)
    qs = TrainingCourse.objects.all() if hr else TrainingCourse.objects.filter(
        status=TrainingCourse.Status.PUBLISHED)
    emp = _my_employee(request.user)
    assignments = {}
    if emp:
        assignments = {a.course_id: a for a in
                       TrainingAssignment.objects.filter(employee=emp)}
    rows = []
    for c in qs.prefetch_related('slides', 'questions', 'versions'):
        rows.append(_course_card(
            c, me_attempt=_my_best(c, request.user, emp),
            assignment=assignments.get(c.id)))
    return Response({'courses': rows, 'can_manage': hr})


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def course_detail(request, slug):
    """The slide deck plus where I got to. Never any correct answers."""
    course = TrainingCourse.objects.filter(slug=slug).first()
    if not course:
        raise Http404
    hr = _is_hr(request)
    if course.status != TrainingCourse.Status.PUBLISHED and not hr:
        return Response({'detail': 'That course is not published yet.'},
                        status=status.HTTP_403_FORBIDDEN)

    emp = _my_employee(request.user)
    open_attempt = (TrainingAttempt.objects
                    .filter(course=course, user=request.user,
                            submitted_at__isnull=True, version__isnull=False)
                    .order_by('-started_at').first())
    best = _my_best(course, request.user, emp)

    # Carry the reader's progress forward across page reloads.
    progress = (TrainingAttempt.objects
                .filter(course=course, user=request.user)
                .order_by('-started_at').first())
    slides_seen = list(progress.slides_seen) if progress else []
    dwell = progress.dwell_seconds if progress else 0

    return Response({
        'course': _course_card(course, me_attempt=best),
        'slides': [{
            'order': s.order, 'title': s.title, 'body_md': s.body_md,
            'section_ref': s.section_ref, 'est_seconds': s.est_seconds,
        } for s in course.slides.all()],
        'source_document_id': (str(course.source_document_id)
                               if course.source_document_id else None),
        'my_progress': {'slides_seen': slides_seen, 'dwell_seconds': dwell},
        'open_attempt_id': (str(open_attempt.id) if open_attempt else None),
        'can_manage': hr,
    })


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def start_exam(request, slug):
    """Issue the next paper — if the slides have actually been read."""
    course = TrainingCourse.objects.filter(
        slug=slug, status=TrainingCourse.Status.PUBLISHED).first()
    if not course:
        raise Http404

    # The gate is judged on what the SERVER credited through save_progress —
    # never on numbers the browser sends here, which a candidate could simply
    # make up.
    progress = (TrainingAttempt.objects
                .filter(course=course, user=request.user)
                .order_by('-started_at').first())
    slides_seen = list(progress.slides_seen) if progress else []
    dwell = progress.dwell_seconds if progress else 0

    ok, why = exam_unlocked(course, slides_seen=slides_seen, dwell_seconds=dwell)
    if not ok:
        return Response({'detail': why}, status=status.HTTP_409_CONFLICT)

    emp = _my_employee(request.user)
    if attempts_left(course, user=request.user, employee=emp) <= 0:
        return Response(
            {'detail': 'You have used all your attempts at this exam. Please '
                       'speak to the Human Capital department, who can give you '
                       'another sitting.'},
            status=status.HTTP_409_CONFLICT)

    # Never hand out two open papers at once — resume the one already running.
    # version__isnull=False is load-bearing: save_progress parks a row with NO
    # version to carry slides_seen and dwell, and without this filter that row
    # matches here and is returned as a "resumed" paper with no questions in it.
    live = (TrainingAttempt.objects
            .filter(course=course, user=request.user, submitted_at__isnull=True,
                    version__isnull=False)
            .order_by('-started_at').first())
    if live:
        return Response({'attempt_id': str(live.id), 'resumed': True,
                         'paper': paper_for(live),
                         'time_limit_minutes': course.time_limit_minutes})

    try:
        attempt = issue_attempt(course, user=request.user, employee=emp,
                                dwell_seconds=dwell, slides_seen=slides_seen)
    except NotEnoughQuestions as exc:
        log.warning('training: cannot issue a paper for %s — %s', course.slug, exc)
        return Response({'detail': str(exc)}, status=status.HTTP_409_CONFLICT)

    return Response({
        'attempt_id': str(attempt.id),
        'paper_number': attempt.version.number if attempt.version else None,
        'attempt_no': attempt.attempt_no,
        'paper': paper_for(attempt),
        'time_limit_minutes': course.time_limit_minutes,
        'questions': course.questions_per_paper,
        'pass_mark': course.pass_mark,
    })


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def save_progress(request, slug):
    """Remember slides read and time spent, so a reload does not lose it."""
    course = TrainingCourse.objects.filter(slug=slug).first()
    if not course:
        raise Http404
    # The page sends the seconds spent since its last heartbeat, and only while
    # the tab is actually visible. The server credits at most
    # MAX_HEARTBEAT_SECONDS of it, so the 15-minute gate cannot be cleared by
    # posting one enormous number.
    tick = max(0, min(MAX_HEARTBEAT_SECONDS, _as_int(request.data.get('seconds'))))
    seen = [int(s) for s in (request.data.get('slides_seen') or [])
            if str(s).isdigit()]

    row = (TrainingAttempt.objects
           .filter(course=course, user=request.user, submitted_at__isnull=True,
                   version__isnull=True)
           .order_by('-started_at').first())
    if row is None:
        row = TrainingAttempt.objects.create(
            course=course, user=request.user, employee=_my_employee(request.user),
            version=None, attempt_no=0)
    # Bound the credit by REAL elapsed time since this row was last touched.
    # Capping each beat is not enough on its own — nothing stops a script
    # posting fifty capped beats in a second, which cleared the whole 15-minute
    # gate instantly (proved at the /fabe gate, 20-Sep-2026).
    elapsed = int((timezone.now() - row.updated_at).total_seconds())
    tick = max(0, min(tick, elapsed))
    row.dwell_seconds = row.dwell_seconds + tick
    row.slides_seen = sorted(set(list(row.slides_seen) + seen))
    row.save(update_fields=['dwell_seconds', 'slides_seen', 'updated_at'])

    ok, why = exam_unlocked(course, slides_seen=row.slides_seen,
                            dwell_seconds=row.dwell_seconds)
    return Response({'saved': True, 'exam_unlocked': ok, 'reason': why,
                     'dwell_seconds': row.dwell_seconds,
                     'slides_seen': row.slides_seen})


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def attempt_paper(request, pk):
    attempt = TrainingAttempt.objects.filter(pk=pk, user=request.user).first()
    if not attempt:
        raise Http404
    return Response({
        'attempt_id': str(attempt.id),
        'paper_number': attempt.version.number if attempt.version else None,
        'submitted': not attempt.is_open,
        'paper': paper_for(attempt),
        'time_limit_minutes': attempt.course.time_limit_minutes,
        'pass_mark': attempt.course.pass_mark,
    })


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def submit_exam(request, pk):
    """Mark the paper and, on a pass, mint the certificate."""
    # Lock the row and re-check under the lock: two submits arriving together
    # both passed the old check and both scored, and the loser could flip a
    # passed attempt to failed while its certificate survived.
    with transaction.atomic():
        attempt = (TrainingAttempt.objects.select_for_update()
                   .filter(pk=pk, user=request.user).first())
        if not attempt:
            raise Http404
        if not attempt.is_open:
            return Response({'detail': 'That paper has already been submitted.'},
                            status=status.HTTP_409_CONFLICT)
        attempt = score_attempt(attempt, request.data.get('answers') or {})
    cert = getattr(attempt, 'certificate', None)

    # The review shown afterwards is deliberately thin on a FAIL: it names the
    # topics to go back over, never the answer to a specific question, so a
    # failed sitting cannot be used to harvest the bank.
    wrong_topics = []
    if not attempt.passed and attempt.version:
        from hris.training_models import TrainingQuestion
        qs = {str(q.id): q for q in TrainingQuestion.objects.filter(
            id__in=attempt.version.question_ids)}
        for qid, shown in attempt.answers.items():
            q = qs.get(qid)
            if not q:
                continue
            positions = attempt.option_order.get(qid, [0, 1, 2, 3])
            if positions[shown] != q.correct_index:
                label = q.topic or (f'clause {q.section_ref}' if q.section_ref else '')
                if label and label not in wrong_topics:
                    wrong_topics.append(label)

    return Response({
        'passed': attempt.passed,
        'score': attempt.score,
        'percent': attempt.percent,
        'pass_mark': attempt.course.pass_mark,
        'certificate_id': str(cert.id) if cert else None,
        'certificate_serial': cert.serial if cert else None,
        'review_topics': wrong_topics[:8],
    })


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def certificate_pdf(request, pk):
    """Stream the certificate. /media/ is not served in production, so the PDF
    is rendered on demand and streamed through this view."""
    cert = TrainingCertificate.objects.filter(pk=pk).select_related(
        'attempt__course').first()
    if not cert:
        raise Http404
    mine = cert.attempt.user_id == request.user.id
    if not mine and not _is_hr(request):
        return Response({'detail': 'That certificate belongs to someone else.'},
                        status=status.HTTP_403_FORBIDDEN)

    from hris.training_certificate import render_certificate_pdf
    import io
    pdf = render_certificate_pdf(cert)
    safe = cert.holder_name.replace(' ', '_') or 'certificate'
    return FileResponse(io.BytesIO(pdf), as_attachment=False,
                        filename=f'Alpha-Direct-Induction-{safe}.pdf',
                        content_type='application/pdf')


@api_view(['GET'])
@permission_classes([AllowAny])
def verify_certificate(request):
    """Confirm a certificate code is real. Deliberately says very little."""
    code = (request.query_params.get('code') or '').strip().upper()
    cert = TrainingCertificate.objects.filter(verify_code=code).first() if code else None
    if not cert:
        return Response({'valid': False})
    return Response({
        'valid': True,
        'holder_name': cert.holder_name,
        'course_title': cert.course_title,
        'serial': cert.serial,
        'issued_on': cert.issued_at.date().isoformat(),
    })


# ---------------------------------------------------------------------------
# HR-facing
# ---------------------------------------------------------------------------

@api_view(['POST'])
@permission_classes([IsAuthenticated])
@parser_classes([MultiPartParser, FormParser, JSONParser])
def upload_course(request):
    """"Upload Here" — a PDF or Word file in, a DRAFT course out.

    The course lands in DRAFT and is invisible to staff until HR publishes it.
    That, not any machine check, is what keeps an AI mistake away from a
    candidate — HR must read the draft.
    """
    denied = _gate(request)
    if denied:
        return denied

    f = request.FILES.get('file')
    if not f:
        return Response({'detail': 'Please choose a PDF or Word file to upload.'},
                        status=status.HTTP_400_BAD_REQUEST)
    if f.size > MAX_UPLOAD_BYTES:
        return Response({'detail': 'That file is larger than 25 MB. Please split it.'},
                        status=status.HTTP_400_BAD_REQUEST)

    try:
        course, report = generate_course(
            data=f.read(), filename=f.name,
            title=(request.data.get('title') or '').strip(),
            created_by=request.user,
        )
    except ExtractionFailed as exc:
        return Response({'detail': str(exc)}, status=status.HTTP_400_BAD_REQUEST)
    except GenerationFailed as exc:
        return Response({'detail': str(exc)}, status=status.HTTP_502_BAD_GATEWAY)

    return Response({'course': _course_card(course), 'report': report},
                    status=status.HTTP_201_CREATED)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def publish_course(request, slug):
    """Build the papers and put the course live."""
    denied = _gate(request)
    if denied:
        return denied
    course = TrainingCourse.objects.filter(slug=slug).first()
    if not course:
        raise Http404

    sitting = TrainingAttempt.objects.filter(
        course=course, submitted_at__isnull=True, version__isnull=False).count()
    if sitting and not str(request.data.get('even_though_people_are_sitting')).lower() == 'true':
        # Rebuilding deletes the ExamVersion rows, and TrainingAttempt.version is
        # SET_NULL — so a candidate mid-exam loses their paper and is marked 0.
        return Response(
            {'detail': f'{sitting} person(s) are sitting this exam right now. '
                       f'Publishing again would wipe their paper and score them '
                       f'zero. Please wait until they finish.'},
            status=status.HTTP_409_CONFLICT)

    for field, cap in (('version_count', 15), ('questions_per_paper', 60),
                       ('pass_mark', 100), ('min_dwell_seconds', 7200),
                       ('duration_minutes', 240), ('time_limit_minutes', 240)):
        if field in request.data:
            val = _as_int(request.data[field], 0)
            if val > 0:
                setattr(course, field, min(cap, val))
    course.save()

    try:
        versions = build_versions(course)
    except NotEnoughQuestions as exc:
        log.warning('training: refusing to publish %s — %s', course.slug, exc)
        return Response({'detail': str(exc)}, status=status.HTTP_409_CONFLICT)

    course.status = TrainingCourse.Status.PUBLISHED
    course.published_at = timezone.now()
    course.save(update_fields=['status', 'published_at', 'updated_at'])
    return Response({'course': _course_card(course), 'papers_built': len(versions)})


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def assign_course(request, slug):
    """Assign the course to named people, or to everyone on payroll."""
    denied = _gate(request)
    if denied:
        return denied
    course = TrainingCourse.objects.filter(slug=slug).first()
    if not course:
        raise Http404

    from payroll.models import Employee
    ids = request.data.get('employee_ids') or []
    everyone = bool(request.data.get('everyone'))
    qs = Employee.objects.filter(status='active') if everyone else \
        Employee.objects.filter(id__in=ids)

    due = request.data.get('due_date') or None
    made = 0
    for emp in qs:
        _, created = TrainingAssignment.objects.get_or_create(
            course=course, employee=emp,
            defaults={'assigned_by': request.user, 'due_date': due})
        made += int(created)
    return Response({'assigned': made, 'total': qs.count()})


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def course_results(request, slug):
    """Who has passed, who has not. HR only — it names people."""
    denied = _gate(request)
    if denied:
        return denied
    course = TrainingCourse.objects.filter(slug=slug).first()
    if not course:
        raise Http404

    # One pass over the attempts instead of two queries per assignee — this
    # table is opened for the whole company.
    by_employee: dict = {}
    counts: dict = {}
    for at in (TrainingAttempt.objects
               .filter(course=course, submitted_at__isnull=False,
                       employee__isnull=False)
               .select_related('certificate')
               .order_by('-passed', '-percent')):
        counts[at.employee_id] = counts.get(at.employee_id, 0) + 1
        by_employee.setdefault(at.employee_id, at)

    rows = []
    for a in (TrainingAssignment.objects.filter(course=course)
              .select_related('employee')):
        best = by_employee.get(a.employee_id)
        cert = getattr(best, 'certificate', None) if best else None
        rows.append({
            'employee_id': str(a.employee_id),
            'name': a.employee.full_name,
            'status': a.status,
            'due_date': a.due_date.isoformat() if a.due_date else None,
            'attempts': counts.get(a.employee_id, 0),
            'percent': best.percent if best else None,
            'passed': bool(best and best.passed),
            'certificate_id': str(cert.id) if cert else None,
        })
    rows.sort(key=lambda r: (r['passed'], r['name']))
    passed = sum(1 for r in rows if r['passed'])
    return Response({'course': _course_card(course), 'rows': rows,
                     'passed': passed, 'assigned': len(rows)})
