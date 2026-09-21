"""
hris/disciplinary_views.py

Function-based DRF views for the disciplinary chain (mirrors
leave_encash_views). Routes are registered in hris/urls.py under
api/disciplinary/ so they land under /hris/api/* (Caddy-proxied to Django).

Visibility: HR + CFO see every case; a manager sees only the cases they raised;
everyone else sees none. Sensitive HR data — the list is filtered server-side.
"""
from __future__ import annotations

from django.core.exceptions import ValidationError
from django.db import transaction
from django.http import FileResponse
from rest_framework.decorators import api_view, parser_classes, permission_classes
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from payroll.models import Employee
from . import disciplinary_notify as notify
from . import disciplinary_service as svc
from .disciplinary_models import DisciplinaryAttachment, DisciplinaryCase

# Evidence upload limits (CFO directive 2026-07-22). Evidence may contain PII —
# it is stored server-side and only ever streamed through the gated download
# view (same viewer gate as the case). No external processing.
MAX_EVIDENCE_BYTES = 25 * 1024 * 1024   # 25 MB
ALLOWED_EVIDENCE_EXTS = frozenset({
    'pdf', 'doc', 'docx', 'png', 'jpg', 'jpeg', 'gif', 'webp', 'eml', 'msg', 'txt',
})


class _InquiryNotServed(Exception):
    """Raised inside issue_inquiry's transaction when the letter fails to send,
    so the 'served' state change is rolled back rather than committed on a lie."""


def _can_view_case(user, case) -> bool:
    """The case visibility gate, reused for evidence attach/download/delete:
    HR + CFO see every case; a manager only the cases they raised."""
    if svc.can_view_all(user):
        return True
    return (svc.can_raise(user)
            and (getattr(user, 'email', '') or '').lower() == (case.raised_by_email or '').lower())


def _serialize_attachment(a: DisciplinaryAttachment) -> dict:
    return {
        'id': str(a.id),
        'filename': a.filename or (a.file.name.split('/')[-1] if a.file else ''),
        'content_type': a.content_type,
        'size': a.size,
        'uploaded_by': ((a.uploaded_by.email or a.uploaded_by.username)
                        if a.uploaded_by_id else None),
        'uploaded_at': a.created_at.isoformat() if a.created_at else None,
        # Root-relative path to the gated stream (NOT FileField.url — media is
        # not served directly and evidence is access-controlled). The frontend
        # fetches this WITH the auth header and downloads the blob.
        'url': f'/hris/api/disciplinary/attachment/{a.id}/download/',
    }


def _serialize(c: DisciplinaryCase, viewer) -> dict:
    return {
        'id': str(c.id),
        'subject': c.subject_name or getattr(c.subject_employee, 'full_name', ''),
        'category': c.category,
        'category_label': c.get_category_display(),
        'incident_date': c.incident_date.isoformat() if c.incident_date else None,
        'allegation': c.allegation,
        'proposed_action': c.proposed_action,
        'status': c.status,
        # Neutral, outcome-agnostic wording (CFO directive 2026-07-23): the UI
        # must not imply the CFO decides a disciplinary outcome. The internal
        # routing is unchanged; only the displayed label for the final-review
        # stage is reworded away from "Awaiting CFO sign-off".
        'status_label': ('Awaiting final review'
                         if c.status == 'pending_cfo'
                         else c.get_status_display()),
        'needs_cfo': c.needs_cfo,
        'raised_by': c.raised_by_email,
        # Natural justice (CFO directive 2026-08-11) — the invitation to respond
        # and the employee's answer, so the board shows why issuing is blocked.
        'inquiry_issued_at': c.inquiry_issued_at.isoformat() if c.inquiry_issued_at else None,
        'inquiry_sent_to': c.inquiry_sent_to,
        'response_deadline': c.response_deadline.isoformat() if c.response_deadline else None,
        'employee_response': c.employee_response,
        'employee_responded_at': (c.employee_responded_at.isoformat()
                                  if c.employee_responded_at else None),
        # Visible on the record: did the employee write it, or did HR transcribe
        # a reply that came in outside omni?
        'response_self_submitted': c.response_is_self_submitted,
        'response_after_decision': c.response_arrived_after_decision,
        # Was this outcome recorded without the employee's side? Derived from the
        # facts, so the board cannot show a friendlier answer than the file holds.
        'issued_without_being_heard': c.issued_without_being_heard,
        'unheard_issue_reason': c.unheard_issue_reason,
        'unheard_issued_by': ((c.unheard_issued_by.email or c.unheard_issued_by.username)
                              if c.unheard_issued_by_id else None),
        'response_recorded_by': ((c.response_recorded_by.email or c.response_recorded_by.username)
                                 if c.response_recorded_by_id else None),
        'deadline_passed': c.deadline_passed,
        'natural_justice_satisfied': c.natural_justice_satisfied,
        'can_issue_inquiry': svc.can_issue_inquiry(c, viewer),
        'subject_email': (getattr(c.subject_employee, 'email', '') or ''),
        'hr_reviewed_at': c.hr_reviewed_at.isoformat() if c.hr_reviewed_at else None,
        'cfo_approved_at': c.cfo_approved_at.isoformat() if c.cfo_approved_at else None,
        'issued_at': c.issued_at.isoformat() if c.issued_at else None,
        'rejected_stage': c.rejected_stage,
        'decision_notes': c.decision_notes,
        # A rejection the CFO overturned keeps BOTH sides on the record.
        'override_reason': c.override_reason,
        'was_overridden': bool(c.override_reason and c.status == 'issued'),
        'created_at': c.created_at.isoformat(),
        'can_act': svc.can_act_now(c, viewer),
        'can_cfo_override': svc.can_cfo_override(c, viewer),
        # Evidence — anyone who can see the case may attach / delete evidence.
        'can_attach': _can_view_case(viewer, c),
        'attachments': [_serialize_attachment(a) for a in c.attachments.all()],
    }


def _me(user) -> dict:
    return {
        'can_raise': svc.can_raise(user),
        'can_view_all': svc.can_view_all(user),
        'is_hr': svc.is_hr(user),
        'is_cfo': svc.is_cfo(user),
    }


@api_view(['GET', 'POST'])
@permission_classes([IsAuthenticated])
def cases(request):
    user = request.user
    if request.method == 'POST':
        try:
            emp = Employee.objects.filter(id=request.data.get('subject_employee_id')).first()
            case = svc.raise_case(
                raised_by=user,
                subject_employee=emp,
                category=request.data.get('category') or '',
                incident_date=request.data.get('incident_date') or None,
                allegation=request.data.get('allegation') or '',
                proposed_action=request.data.get('proposed_action') or '',
            )
        except ValidationError as exc:
            return Response({'detail': '; '.join(exc.messages)}, status=400)
        try:
            notify.notify_raised(case)
        except Exception:    # noqa: BLE001
            pass
        return Response(_serialize(case, user), status=201)

    # GET — filtered by what the viewer may see.
    if svc.can_view_all(user):
        qs = DisciplinaryCase.objects.all()
    elif svc.can_raise(user):
        qs = DisciplinaryCase.objects.filter(raised_by_email=(user.email or '').lower())
    else:
        qs = DisciplinaryCase.objects.none()
    qs = (qs.select_related('subject_employee')
            .prefetch_related('attachments__uploaded_by')
            .order_by('-created_at')[:200])

    subjects = []
    if svc.can_raise(user):
        subjects = [{'id': str(e.id), 'name': e.full_name, 'department': e.department or ''}
                    for e in Employee.objects.filter(status='active').order_by('full_name')]
    return Response({'me': _me(user), 'cases': [_serialize(c, user) for c in qs], 'subjects': subjects})


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def issue_inquiry(request, case_id):
    """POST api/disciplinary/<uuid:case_id>/issue-inquiry/ — serve the invitation
    to respond on the employee (CFO directive 2026-08-11).

    Unlike the other transitions, the email is NOT best-effort here: the whole
    point of the stage is provable service, so if the letter does not go out the
    state change is rolled back and the caller is told.

    Body fields, both optional:
      `deadline`   — YYYY-MM-DD; defaults to 5 days out.
      `send_email` — pass false to raise the invitation IN OMNI ONLY (CFO
                     instruction 2026-08-12). No letter is sent and none is
                     claimed: the case records that it was served in omni only.
    """
    send_email = request.data.get('send_email')
    send_email = True if send_email is None else bool(send_email)
    try:
        with transaction.atomic():
            case = svc.issue_inquiry(case_id, request.user,
                                     deadline=request.data.get('deadline') or None,
                                     send_email=send_email)
            if send_email:
                try:
                    sent = notify.notify_inquiry(case)
                except Exception as exc:    # noqa: BLE001
                    raise _InquiryNotServed(str(exc)) from exc
                if not sent:
                    raise _InquiryNotServed('the mail server accepted no recipients')
    except DisciplinaryCase.DoesNotExist:
        return Response({'detail': 'Case not found.'}, status=404)
    except _InquiryNotServed as exc:
        return Response(
            {'detail': f'The inquiry letter could not be emailed to the employee '
                       f'({exc}), so the case has NOT been marked as served. '
                       f'Try again, or hand-deliver the letter and record the response here.'},
            status=502)
    except ValidationError as exc:
        return Response({'detail': '; '.join(exc.messages)}, status=400)
    return Response(_serialize(case, request.user))


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def record_response_on_behalf(request, case_id):
    """POST api/disciplinary/<uuid:case_id>/record-response/ — HR captures a reply
    that arrived outside omni (emailed, or a signed letter). Body: `response`,
    optional `received_on` (YYYY-MM-DD). HR / final reviewer only."""
    try:
        case = svc.record_response_on_behalf(
            case_id, request.user,
            response=request.data.get('response') or '',
            received_on=request.data.get('received_on') or None)
    except DisciplinaryCase.DoesNotExist:
        return Response({'detail': 'Case not found.'}, status=404)
    except ValidationError as exc:
        return Response({'detail': '; '.join(exc.messages)}, status=400)
    try:
        notify.notify_response_recorded(case)
    except Exception:    # noqa: BLE001
        pass
    return Response(_serialize(case, request.user))


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def hr_review(request, case_id):
    try:
        case = svc.hr_review(case_id, request.user, notes=request.data.get('notes') or '')
    except DisciplinaryCase.DoesNotExist:
        return Response({'detail': 'Case not found.'}, status=404)
    except ValidationError as exc:
        return Response({'detail': '; '.join(exc.messages)}, status=400)
    try:
        notify.notify_advanced(case)
    except Exception:    # noqa: BLE001
        pass
    return Response(_serialize(case, request.user))


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def cfo_signoff(request, case_id):
    try:
        case = svc.cfo_signoff(case_id, request.user)
    except DisciplinaryCase.DoesNotExist:
        return Response({'detail': 'Case not found.'}, status=404)
    except ValidationError as exc:
        return Response({'detail': '; '.join(exc.messages)}, status=400)
    try:
        notify.notify_advanced(case)
    except Exception:    # noqa: BLE001
        pass
    return Response(_serialize(case, request.user))


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def reject(request, case_id):
    try:
        case = svc.reject(case_id, request.user, notes=request.data.get('notes') or '')
    except DisciplinaryCase.DoesNotExist:
        return Response({'detail': 'Case not found.'}, status=404)
    except ValidationError as exc:
        return Response({'detail': '; '.join(exc.messages)}, status=400)
    try:
        notify.notify_rejected(case)
    except Exception:    # noqa: BLE001
        pass
    return Response(_serialize(case, request.user))


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def cfo_override(request, case_id):
    """POST api/disciplinary/<uuid:case_id>/cfo-override/ — the CFO overturns a
    rejection and issues the case. Body: {"notes": "<reason, 10+ words>"}.

    If the employee was never heard, the call is REFUSED unless it also carries
    `issue_unheard_reason` (10+ words), which is stamped permanently on the file
    (CFO decision 2026-08-12). The default is to refuse, so forcing is always a
    deliberate act rather than something that happens by omission."""
    try:
        case = svc.cfo_override(
            case_id, request.user,
            notes=request.data.get('notes') or '',
            issue_unheard_reason=request.data.get('issue_unheard_reason') or '')
    except DisciplinaryCase.DoesNotExist:
        return Response({'detail': 'Case not found.'}, status=404)
    except ValidationError as exc:
        return Response({'detail': '; '.join(exc.messages)}, status=400)
    try:
        notify.notify_overridden(case)
    except Exception:    # noqa: BLE001
        pass
    return Response(_serialize(case, request.user))


# ── Evidence attachments (CFO directive 2026-07-22) ──────────────────────────

@api_view(['POST'])
@permission_classes([IsAuthenticated])
@parser_classes([MultiPartParser, FormParser])
def attach_evidence(request, case_id):
    """POST api/disciplinary/<uuid:case_id>/attach/ — multipart upload of one
    evidence file (field name 'file'). Only someone who can see the case
    (raiser / HR / CFO) may attach. Rejects oversized or disallowed file types."""
    case = DisciplinaryCase.objects.filter(id=case_id).first()
    if case is None:
        return Response({'detail': 'Case not found.'}, status=404)
    if not _can_view_case(request.user, case):
        return Response({'detail': 'You do not have access to this case.'}, status=403)

    f = request.FILES.get('file')
    if f is None:
        return Response({'detail': 'Attach a file in the "file" field.'}, status=400)

    name = getattr(f, 'name', '') or ''
    ext = (name.rsplit('.', 1)[-1] if '.' in name else '').lower()
    if ext not in ALLOWED_EVIDENCE_EXTS:
        return Response(
            {'detail': f'File type ".{ext}" is not allowed. Allowed: '
                       f'{", ".join(sorted(ALLOWED_EVIDENCE_EXTS))}.'},
            status=400)
    if (f.size or 0) > MAX_EVIDENCE_BYTES:
        return Response(
            {'detail': f'File is too large ({f.size} bytes). Maximum is 25 MB.'},
            status=400)

    att = DisciplinaryAttachment(
        case=case,
        file=f,
        filename=name[:255],
        content_type=(getattr(f, 'content_type', '') or '')[:120],
        size=(f.size or 0),
        uploaded_by=request.user if request.user.is_authenticated else None,
    )
    att.save(audit_user=request.user)
    return Response(_serialize_attachment(att), status=201)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def delete_evidence(request, attachment_id):
    """POST api/disciplinary/attachment/<uuid:attachment_id>/delete/ — remove an
    evidence file. The raiser (own case) or HR / CFO only. Audit-logged."""
    att = (DisciplinaryAttachment.objects
           .select_related('case').filter(id=attachment_id).first())
    if att is None:
        return Response({'detail': 'Attachment not found.'}, status=404)
    if not _can_view_case(request.user, att.case):
        return Response({'detail': 'You cannot delete this attachment.'}, status=403)
    att.delete(audit_user=request.user)   # AuditableMixin.delete writes a DELETE row
    return Response(status=204)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def download_evidence(request, attachment_id):
    """GET api/disciplinary/attachment/<uuid:attachment_id>/download/ — stream
    the file behind the case's visibility gate. Media is never served directly."""
    att = (DisciplinaryAttachment.objects
           .select_related('case').filter(id=attachment_id).first())
    if att is None or not att.file:
        return Response({'detail': 'Attachment not found.'}, status=404)
    if not _can_view_case(request.user, att.case):
        return Response({'detail': 'You do not have access to this attachment.'}, status=403)
    return FileResponse(
        att.file.open('rb'), as_attachment=True,
        filename=(att.filename or att.file.name.split('/')[-1] or 'evidence'))
