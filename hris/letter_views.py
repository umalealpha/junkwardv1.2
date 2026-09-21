"""hris/letter_views.py — staff letter requests + manager sign-off + branded PDF.

Endpoints (mounted under /hris/api/ so Caddy proxies them to Django):

    GET/POST /hris/api/letters/                     my letters + queue to sign / request one
    POST     /hris/api/letters/<uuid>/decide/       manager (or HR) signs off or declines
    GET      /hris/api/letters/<uuid>/pdf/          download the issued letter (branded PDF)
    GET      /hris/api/letters/letterhead/          download the blank branded letterhead (docx)
    GET      /hris/api/letters/verify/<uuid>/        PUBLIC — verify an issued letter (QR target)

Self-service tier: any authenticated employee may request a letter about
THEMSELVES and download their own issued copy. Signing off is restricted to the
subject's manager (HRISProfile.manager) or HR — never the subject (a staff
member cannot issue their own letter). Mirrors the leave apply→decide flow.
"""
from __future__ import annotations

import os

from django.conf import settings
from django.db import transaction
from django.http import FileResponse, Http404, HttpResponse
from django.utils import timezone
from django.utils.html import escape
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework import status

from hris.models import HRISProfile, LetterRequest
from hris.letter_models import LetterType, LETTER_REF_CODE
from hris.letter_pdf import render_letter_pdf
from hris.feature_views import _gate
from hris.amendment_service import _is_hr_admin, _is_manager_of

_HERE = os.path.dirname(os.path.abspath(__file__))
_BLANK_LETTERHEAD = os.path.join(_HERE, 'assets', 'letterhead_blank.docx')
_DEFAULT_COMPANY = 'Alpha Direct Insurance Company (Pty) Ltd'
_HR_MAILBOX = 'hr@alphadirect.co.bw'
NAVY, ORANGE = '#0D1B2A', '#F4A623'


# ── small helpers ───────────────────────────────────────────────────────────

def _base_url() -> str:
    return getattr(settings, 'PUBLIC_BASE_URL', 'https://omni.alphadirect.co.bw').rstrip('/')


def _emp_for(user):
    """The Employee linked to this login.

    Primary link is the OneToOne payroll.Employee.user (reverse
    ``employee_record``). Fall back to an email match so a staff member whose
    Employee row exists but whose login was never linked still gets self-service
    — otherwise they hit "No employee record is linked to your login" even
    though their record plainly exists. Mirrors performance_views._resolve_employee.
    """
    emp = getattr(user, 'employee_record', None)
    if emp is not None:
        return emp
    email = (getattr(user, 'email', '') or '').strip()
    if not email:
        return None
    try:
        from payroll.models import Employee
        return Employee.objects.filter(email__iexact=email).first()
    except Exception:  # noqa: BLE001
        return None


def _company_name(emp) -> str:
    c = getattr(emp, 'company', None)
    if c:
        return c.legal_name or c.name or _DEFAULT_COMPANY
    return _DEFAULT_COMPANY


def _manager_emp(emp):
    prof = (HRISProfile.objects.filter(employee=emp)
            .select_related('manager').first()) if emp else None
    return getattr(prof, 'manager', None) if prof else None


def _can_sign(user, emp) -> bool:
    """A manager of `emp` (or HR) may sign — but never the subject themselves."""
    if emp is None:
        return False
    signer = _emp_for(user)
    if signer and signer.pk == emp.pk:
        return False
    if _is_hr_admin(user):
        return True
    return _is_manager_of(user, emp)


def _pending_for_signer(user) -> list:
    qs = (LetterRequest.objects.filter(status=LetterRequest.Status.PENDING)
          .select_related('employee', 'requested_by').order_by('-created_at'))
    if _is_hr_admin(user):
        return list(qs[:200])
    mgr_emp = _emp_for(user)
    if mgr_emp is None:
        return []
    managed = (HRISProfile.objects.filter(manager=mgr_emp)
               .values_list('employee_id', flat=True))
    return list(qs.filter(employee_id__in=list(managed))[:200])


def _serialize(lr, request=None) -> dict:
    issued = lr.status == LetterRequest.Status.ISSUED
    pdf_url = ''
    if issued and request is not None:
        pdf_url = request.build_absolute_uri(f'/hris/api/letters/{lr.id}/pdf/')
    return {
        'id':                str(lr.id),
        'letter_type':       lr.letter_type,
        'letter_type_label': lr.get_letter_type_display(),
        'employee_name':     lr.employee.full_name if lr.employee_id else '',
        'employee_number':   lr.employee.employee_number if lr.employee_id else '',
        'job_title':         lr.employee.job_title if lr.employee_id else '',
        'addressee':         lr.addressee,
        'purpose':           lr.purpose,
        'status':            lr.status,
        'status_label':      lr.get_status_display(),
        'reference':         lr.reference,
        'signatory_name':    lr.signatory.full_name if lr.signatory_id else '',
        'decline_reason':    lr.decline_reason,
        'requested_by':      (lr.requested_by.get_full_name() or lr.requested_by.username)
                             if lr.requested_by_id else '',
        'decided_at':        lr.decided_at.isoformat() if lr.decided_at else None,
        'created_at':        lr.created_at.isoformat(),
        'can_download':      issued,
        'pdf_url':           pdf_url,
    }


def _make_reference(emp, letter_type) -> str:
    code = LETTER_REF_CODE.get(letter_type, 'LT')
    prefix = (getattr(getattr(emp, 'company', None), 'code', None) or 'ADIC')
    year = timezone.now().year
    seq = LetterRequest.objects.filter(
        letter_type=letter_type,
        status=LetterRequest.Status.ISSUED,
        decided_at__year=year,
    ).count() + 1
    return f'{prefix}/HR/{code}/{year}/{seq:04d}'


def _polish_additional_details(raw: str, emp) -> str:
    """Turn HR's rough note into one clean, formal sentence for the letter, via
    the omni AI helper (DeepSeek / 'Aria'). Never invents facts — only rewrites
    what HR typed. Falls back to the tidied raw note if the AI is unavailable, so
    the letter still issues immediately."""
    raw = (raw or '').strip()
    if not raw:
        return ''
    try:
        from core.ai_assist import deepseek_complete, DeepSeekUnavailable
        system = (
            'You write one sentence for a formal employment-confirmation letter from '
            'an insurance company. Rewrite the HR note into a single concise, '
            'professional sentence in British English. Do NOT invent any facts not '
            'in the note. Do not add greetings, names, signatures or a reference to '
            'the company already stated elsewhere. Return only the sentence.'
        )
        prompt = (f'Employee: {emp.full_name}, {emp.job_title or "staff member"}.\n'
                  f'HR note to include: "{raw}"')
        out = deepseek_complete(prompt, system_prompt=system, max_tokens=160).strip()
        # Strip stray wrapping quotes the model sometimes adds.
        out = out.strip().strip('"').strip()
        return out or raw
    except Exception:  # noqa: BLE001 — DeepSeekUnavailable or any AI error
        return raw


def _build_snapshot(lr, signer_emp, signer_user, extra_paragraph='') -> dict:
    emp = lr.employee
    sig_name = (signer_emp.full_name if signer_emp
                else (signer_user.get_full_name() or signer_user.username))
    sig_title = (getattr(signer_emp, 'job_title', '') or 'Manager') if signer_emp else 'Manager'
    return {
        'extra_paragraph':   extra_paragraph or '',
        'letter_type':       lr.letter_type,
        'full_name':         emp.full_name,
        'employee_number':   emp.employee_number or '',
        'job_title':         emp.job_title or '',
        'department':        emp.department or '',
        'employment_status': emp.status,
        'company_name':      _company_name(emp),
        'hire_date':         emp.hire_date.isoformat() if emp.hire_date else '',
        'issued_date':       timezone.localdate().isoformat(),
        'addressee':         lr.addressee,
        'purpose':           lr.purpose,
        'reference':         lr.reference,
        'signatory_name':    sig_name,
        'signatory_title':   sig_title,
    }


# ── email (best-effort — never blocks the request) ──────────────────────────

def _send(subject, html, to, *, attachments=None):
    try:
        from core.notifications import send_html_with_cfo_cc
        recips = [a for a in (to or []) if a]
        if not recips:
            return
        send_html_with_cfo_cc(subject, html, recips, attachments=attachments, cc_cfo=False)
    except Exception:  # noqa: BLE001 — notification must never break the flow
        pass


def _notify_manager(lr):
    mgr = _manager_emp(lr.employee)
    to = [getattr(mgr, 'email', '') or ''] if mgr else []
    if not to or not to[0]:
        to = [_HR_MAILBOX]
    link = f'{_base_url()}/hris/letters'
    who = escape(lr.employee.full_name)
    purpose = escape(lr.purpose) or '—'
    html = (
        f'<p>{who} has requested an <b>employment-confirmation letter</b> and needs your sign-off.</p>'
        f'<p><b>Purpose:</b> {purpose}</p>'
        f'<p>Open omni → HRIS → Letters to review and sign it off (or decline). '
        f'The letter is only issued once you sign it.</p>'
        f'<p><a href="{link}" style="color:{NAVY}">Open Letters in omni</a></p>'
    )
    _send('Letter to sign off — employment confirmation', html, to)


def _notify_issued(lr, pdf_bytes):
    emp = lr.employee
    to = [emp.email or '']
    fn = f"Employment-Confirmation-{(lr.reference or str(lr.id)).replace('/', '-')}.pdf"
    html = (
        f'<p>Dumela {escape(emp.full_name.split()[0] if emp.full_name else "")},</p>'
        f'<p>Your employment-confirmation letter has been signed off and is attached. '
        f'It is also available in omni under HRIS → Letters.</p>'
        f'<p><b>Reference:</b> {escape(lr.reference)}</p>'
        f'<p>Regards,<br/>Human Resources — Alpha Direct</p>'
    )
    _send('Your employment-confirmation letter', html, to,
          attachments=[(fn, pdf_bytes, 'application/pdf')])


def _notify_declined(lr):
    emp = lr.employee
    reason = escape(lr.decline_reason) or 'No reason given.'
    html = (
        f'<p>Dumela {escape(emp.full_name.split()[0] if emp.full_name else "")},</p>'
        f'<p>Your request for an employment-confirmation letter was not approved.</p>'
        f'<p><b>Reason:</b> {reason}</p>'
        f'<p>Please speak to your manager or HR if you have questions.</p>'
    )
    _send('Employment-letter request — not approved', html, [emp.email or ''])


# ── endpoints ────────────────────────────────────────────────────────────────

@api_view(['GET', 'POST'])
@permission_classes([IsAuthenticated])
def letters(request):
    """GET: my letters + (if I'm a manager/HR) the queue awaiting my sign-off.
    POST: request a letter about myself (auto-filled from my payroll record)."""
    denied = _gate(request, capability='view_self')
    if denied is not None:
        return denied
    emp = _emp_for(request.user)

    if request.method == 'POST':
        if emp is None:
            return Response(
                {'detail': 'No employee record is linked to your login. Contact HR.'},
                status=status.HTTP_400_BAD_REQUEST)
        if emp.status in ('suspended', 'terminated'):
            return Response(
                {'detail': 'Your employment status is not active, so this letter '
                           'cannot be issued. Please contact HR.'},
                status=status.HTTP_400_BAD_REQUEST)
        letter_type = (request.data.get('letter_type') or LetterType.EMPLOYMENT_CONFIRMATION)
        if letter_type not in LetterType.values:
            letter_type = LetterType.EMPLOYMENT_CONFIRMATION
        purpose = (request.data.get('purpose') or '').strip()[:200]
        addressee = (request.data.get('addressee') or 'To Whom It May Concern').strip()[:200] \
            or 'To Whom It May Concern'

        # Don't stack duplicate pending requests of the same type.
        existing = LetterRequest.objects.filter(
            employee=emp, letter_type=letter_type,
            status=LetterRequest.Status.PENDING).first()
        if existing is not None:
            return Response(_serialize(existing, request), status=status.HTTP_200_OK)

        lr = LetterRequest(
            employee=emp, letter_type=letter_type, purpose=purpose,
            addressee=addressee, requested_by=request.user,
            status=LetterRequest.Status.PENDING)
        lr.save(audit_user=request.user)
        _notify_manager(lr)
        return Response(_serialize(lr, request), status=status.HTTP_201_CREATED)

    # GET
    mine = ([_serialize(lr, request) for lr in
             LetterRequest.objects.filter(employee=emp).select_related('signatory')[:100]]
            if emp else [])
    to_sign = [_serialize(lr, request) for lr in _pending_for_signer(request.user)]
    return Response({
        'mine': mine,
        'to_sign': to_sign,
        'is_signer': bool(to_sign) or _is_hr_admin(request.user),
        'letter_types': [{'value': v, 'label': l} for v, l in LetterType.choices],
    })


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def decide_letter(request, letter_id):
    """POST — manager/HR signs off (decision=approve) or declines (decision=decline)."""
    denied = _gate(request, capability='view_self')
    if denied is not None:
        return denied
    lr = LetterRequest.objects.filter(pk=letter_id).select_related('employee').first()
    if lr is None:
        return Response({'detail': 'Letter request not found.'}, status=404)
    if lr.status != LetterRequest.Status.PENDING:
        return Response({'detail': f'This request is already {lr.get_status_display().lower()}.'},
                        status=409)
    if not _can_sign(request.user, lr.employee):
        return Response(
            {'detail': 'Only the employee’s manager or HR can sign off this letter.'},
            status=403)

    decision = (request.data.get('decision') or '').strip().lower()
    if decision not in ('approve', 'decline'):
        return Response({'detail': 'decision must be approve|decline.'}, status=400)

    signer_emp = _emp_for(request.user)
    additional = (request.data.get('additional_details') or '').strip()[:1000]

    if decision == 'decline':
        lr.status = LetterRequest.Status.DECLINED
        lr.decline_reason = (request.data.get('reason') or '').strip()[:300]
        lr.decided_by = request.user
        lr.signatory = signer_emp
        lr.decided_at = timezone.now()
        lr.save(audit_user=request.user)
        _notify_declined(lr)
        return Response(_serialize(lr, request))

    # Polish HR's optional note into a clean letter sentence via the omni AI
    # helper BEFORE opening the transaction (the AI call can take a few seconds;
    # we don't want it holding a row lock). Falls back to the raw note.
    extra_paragraph = _polish_additional_details(additional, lr.employee) if additional else ''

    # approve → stamp reference + frozen snapshot, issue, then email the PDF.
    with transaction.atomic():
        locked = LetterRequest.objects.select_for_update().get(pk=lr.pk)
        if locked.status != LetterRequest.Status.PENDING:
            return Response({'detail': 'This request was just decided by someone else.'},
                            status=409)
        locked.additional_details = additional
        locked.reference = _make_reference(locked.employee, locked.letter_type)
        locked.issued_snapshot = _build_snapshot(locked, signer_emp, request.user, extra_paragraph)
        locked.status = LetterRequest.Status.ISSUED
        locked.signatory = signer_emp
        locked.decided_by = request.user
        locked.decided_at = timezone.now()
        locked.save(audit_user=request.user)
        lr = locked

    try:
        verify_url = f'{_base_url()}/hris/api/letters/{lr.id}/verify/'
        pdf = render_letter_pdf(lr.issued_snapshot, verify_url=verify_url)
        _notify_issued(lr, pdf)
    except Exception:  # noqa: BLE001 — issuing succeeded even if the email didn't
        pass
    return Response(_serialize(lr, request))


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def letter_pdf_view(request, letter_id):
    """GET — the issued letter as a branded PDF (subject, signatory, or HR only)."""
    denied = _gate(request, capability='view_self')
    if denied is not None:
        return denied
    lr = LetterRequest.objects.filter(pk=letter_id).select_related('employee').first()
    if lr is None or lr.status != LetterRequest.Status.ISSUED or not lr.issued_snapshot:
        raise Http404('Letter not found.')
    emp = _emp_for(request.user)
    allowed = (_is_hr_admin(request.user)
               or (emp and lr.employee_id == emp.pk)
               or (emp and lr.signatory_id and lr.signatory_id == emp.pk))
    if not allowed:
        return Response({'detail': 'You are not permitted to download this letter.'}, status=403)
    verify_url = f'{_base_url()}/hris/api/letters/{lr.id}/verify/'
    pdf = render_letter_pdf(lr.issued_snapshot, verify_url=verify_url)
    fn = f"Employment-Confirmation-{(lr.reference or str(lr.id)).replace('/', '-')}.pdf"
    resp = HttpResponse(pdf, content_type='application/pdf')
    resp['Content-Disposition'] = f'inline; filename="{fn}"'
    return resp


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def letterhead_template(request):
    """GET — the blank branded Alpha Direct letterhead (Word .docx).

    Managers and HR only (CFO directive 2026-07-23, Unami's request): a plain
    staff member should not be able to pull the blank company letterhead. If they
    need a letter they use "Request letter", which their manager signs and which
    issues on the letterhead — the letterhead itself is a manager/HR document."""
    denied = _gate(request, capability='view_self')
    if denied is not None:
        return denied
    from core.hris_access import hris_role
    if hris_role(request.user) not in ('mgr', 'hr', 'hris', 'admin', 'ceo', 'superadmin'):
        return Response(
            {'detail': 'The blank letterhead is for managers and HR only. If you '
                       'need a letter, use “Request letter” above — your manager '
                       'signs it and it is issued on the letterhead.'},
            status=status.HTTP_403_FORBIDDEN)
    if not os.path.exists(_BLANK_LETTERHEAD):
        raise Http404('Letterhead template not found.')
    return FileResponse(open(_BLANK_LETTERHEAD, 'rb'), as_attachment=True,
                        filename='Alpha Direct Letterhead.docx')


@api_view(['GET'])
@permission_classes([AllowAny])
def letter_verify(request, letter_id):
    """PUBLIC — the QR target. A branded page a third party (bank, embassy) opens
    to confirm Alpha Direct really issued this letter. Shows only what is already
    printed on the letter itself; an unknown/unissued id returns a clear notice."""
    lr = LetterRequest.objects.filter(pk=letter_id).select_related('employee', 'signatory').first()
    valid = bool(lr and lr.status == LetterRequest.Status.ISSUED and lr.issued_snapshot)
    if valid:
        s = lr.issued_snapshot
        body = f"""
          <div class="ok">✓ Verified — issued by Alpha Direct Insurance</div>
          <table>
            <tr><td>Reference</td><td>{escape(s.get('reference',''))}</td></tr>
            <tr><td>Employee</td><td>{escape(s.get('full_name',''))}</td></tr>
            <tr><td>Position</td><td>{escape(s.get('job_title',''))}</td></tr>
            <tr><td>Letter</td><td>{escape(lr.get_letter_type_display())}</td></tr>
            <tr><td>Issued</td><td>{escape(s.get('issued_date',''))}</td></tr>
            <tr><td>Signed by</td><td>{escape(s.get('signatory_name',''))}, {escape(s.get('signatory_title',''))}</td></tr>
          </table>
          <p class="note">This page confirms the letter with this reference was issued through
          Alpha Direct's omni system. For anything further, contact hr@alphadirect.co.bw.</p>"""
        st = 200
    else:
        body = ('<div class="bad">This letter reference could not be verified.</div>'
                '<p class="note">If you were given a printed letter, please contact '
                'hr@alphadirect.co.bw to confirm it.</p>')
        st = 404
    html = f"""<!DOCTYPE html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Verify letter — Alpha Direct</title>
<style>
 body{{font-family:-apple-system,Segoe UI,Roboto,Arial,sans-serif;background:#F4F6FA;margin:0;color:#1F2937}}
 .card{{max-width:520px;margin:6vh auto;background:#fff;border-radius:12px;
        box-shadow:0 4px 24px rgba(13,27,42,.10);overflow:hidden}}
 .bar{{height:8px;background:linear-gradient(90deg,{NAVY},{ORANGE})}}
 .inner{{padding:28px}}
 h1{{color:{NAVY};font-size:20px;margin:0 0 4px}}
 .ok{{color:#0B7A3B;font-weight:700;margin:12px 0}}
 .bad{{color:#B91C1C;font-weight:700;margin:12px 0}}
 table{{width:100%;border-collapse:collapse;margin-top:8px}}
 td{{padding:8px 6px;border-bottom:1px solid #EEF1F5;font-size:14px;vertical-align:top}}
 td:first-child{{color:#6B7280;width:34%}}
 .note{{color:#6B7280;font-size:12.5px;margin-top:16px;line-height:1.5}}
</style></head><body>
 <div class="card"><div class="bar"></div><div class="inner">
   <h1>Alpha Direct Insurance</h1>
   <div>Letter verification</div>
   {body}
 </div></div></body></html>"""
    return HttpResponse(html, status=st)
